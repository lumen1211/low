# src/miner.py
# -*- coding: utf-8 -*-
from __future__ import annotations
import asyncio
import json
from pathlib import Path
from typing import Optional, Dict, Any
from datetime import datetime

import aiohttp

from .twitch_api import TwitchAPI

async def _read_auth_token(cookies_dir: Path, login: str) -> Optional[str]:
    fp = cookies_dir / f"{login}.json"
    if not fp.exists():
        return None
    try:
        data = json.loads(fp.read_text(encoding="utf-8"))
        for c in data:
            if c.get("name") == "auth-token":
                return c.get("value") or ""
    except Exception:
        return None
    return None

async def run_account(login: str, proxy: Optional[str], queue, stop_evt: asyncio.Event):
    """
    Основной рабочий цикл аккаунта.
    - читает cookies/<login>.json -> auth-token
    - через TwitchAPI получает кампании, отслеживает прогресс и автоматически клеймит дропы
    - обновляет GUI: статус, кампания/игра, прогресс
    """
    cookies_dir = Path("cookies")
    token = await _read_auth_token(cookies_dir, login)
    if not token:
        await queue.put((login, "error", {"msg": "no cookies/auth-token"}))
        return

    api = TwitchAPI(token, proxy=proxy or "")
    await api.start()

    def find_time_based_drop(obj: Any) -> Optional[Dict[str, Any]]:
        if isinstance(obj, dict):
            if {"requiredMinutesWatched", "currentMinutesWatched", "dropInstanceID"} <= obj.keys():
                return obj
            for v in obj.values():
                res = find_time_based_drop(v)
                if res:
                    return res
        elif isinstance(obj, list):
            for v in obj:
                res = find_time_based_drop(v)
                if res:
                    return res
        return None

    await queue.put((login, "status", {"status": "Querying", "note": "Fetching campaigns"}))
    try:
        data = await api.viewer_dashboard()

        camp_name = ""
        game_name = ""
        channel_id = ""
        try:
            import re, json as _json
            d = (data or {}).get("data") or {}
            vd = d.get("viewer") or d.get("currentUser") or {}
            drops = vd.get("dropsDashboard") or vd.get("drops") or vd

            if isinstance(drops, dict):
                campaigns = []
                if isinstance(drops.get("currentCampaigns"), list):
                    campaigns = drops["currentCampaigns"]
                elif isinstance(drops.get("availableCampaigns"), list):
                    campaigns = drops["availableCampaigns"]
                elif isinstance(drops.get("campaigns"), list):
                    campaigns = drops["campaigns"]
                elif isinstance(drops.get("currentDropCampaigns"), list):
                    campaigns = drops["currentDropCampaigns"]

                if campaigns:
                    c0 = campaigns[0]
                    camp_name = c0.get("name") or c0.get("displayName") or c0.get("id", "")
                    game = c0.get("game") or c0.get("gameTitle") or {}
                    game_name = game.get("name") or game.get("displayName") or ""
                    ch = c0.get("broadcaster") or c0.get("channel") or {}
                    if isinstance(ch, dict):
                        channel_id = ch.get("id") or ch.get("_id") or ch.get("channelID") or ""
            if not channel_id or not camp_name:
                txt = _json.dumps(drops)
                if not channel_id:
                    m = re.search(r'"channelID"\s*:\s*"(\d+)"', txt) or re.search(r'"id"\s*:\s*"(\d+)"', txt)
                    if m:
                        channel_id = m.group(1)
                if not camp_name:
                    m = re.search(r'"displayName"\s*:\s*"([^"]+)"', txt) or re.search(r'"name"\s*:\s*"([^"]+)"', txt)
                    if m:
                        camp_name = m.group(1)
                    m2 = re.search(r'"gameTitle"\s*:\s*{[^}]*"displayName"\s*:\s*"([^"]+)"', txt) or re.search(r'"game"\s*:\s*{[^}]*"name"\s*:\s*"([^"]+)"', txt)
                    if m2:
                        game_name = m2.group(1)
        except Exception:
            pass

        await queue.put((login, "campaign", {"camp": camp_name or "—", "game": game_name or "—"}))
        await queue.put((login, "status", {"status": "Ready", "note": "Campaigns discovered"}))

        if not channel_id:
            raise RuntimeError("active channel not found")

        loop = asyncio.get_event_loop()
        last_inv = last_inc = 0.0
        while not stop_evt.is_set():
            now = loop.time()
            if now - last_inc >= 60:
                try:
                    await api.increment(channel_id)
                except (aiohttp.ClientError, RuntimeError) as e:
                    await queue.put((login, "error", {"msg": f"increment: {e}"}))
                    break
                last_inc = now
            if now - last_inv >= 150:
                try:
                    inv = await api.inventory()
                except (aiohttp.ClientError, RuntimeError) as e:
                    await queue.put((login, "error", {"msg": f"inventory: {e}"}))
                    break
                drop = find_time_based_drop(inv)
                if drop:
                    required = drop.get("requiredMinutesWatched") or 0
                    current = drop.get("currentMinutesWatched") or 0
                    drop_id = drop.get("dropInstanceID") or ""
                    drop_name = drop.get("name") or drop.get("dropName") or drop_id
                    pct = (current / required * 100) if required else 0
                    remain = max(0, required - current)
                    await queue.put((login, "progress", {"pct": pct, "remain": remain}))
                    if pct >= 100 and drop_id:
                        try:
                            await api.claim(drop_id)
                            ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
                            await queue.put((login, "claimed", {"drop": drop_name, "at": ts}))
                            await queue.put((login, "last_claim_at", {"at": ts}))
                        except (aiohttp.ClientError, RuntimeError) as e:
                            await queue.put((login, "error", {"msg": f"claim: {e}"}))
                last_inv = now
            await asyncio.sleep(1.0)

    except (aiohttp.ClientError, RuntimeError) as e:
        await queue.put((login, "error", {"msg": f"GQL error: {e}"}))
    finally:
        await api.close()
        await queue.put((login, "status", {"status": "Stopped"}))
