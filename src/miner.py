# src/miner.py
# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import json
import random
from datetime import datetime
from pathlib import Path
from typing import Optional, Any, Dict

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


def _find_time_based_drop(obj: Any) -> Optional[Dict[str, Any]]:
    """Recursively search for a time-based drop in the inventory response."""
    if isinstance(obj, dict):
        if {"requiredMinutesWatched", "currentMinutesWatched", "dropInstanceID"} <= obj.keys():
            return obj
        for v in obj.values():
            found = _find_time_based_drop(v)
            if found:
                return found
    elif isinstance(obj, list):
        for item in obj:
            found = _find_time_based_drop(item)
            if found:
                return found
    return None


async def run_account(login: str, proxy: Optional[str], queue, stop_evt: asyncio.Event):
    """Run miner for a single account."""
    cookies_dir = Path("cookies")
    token = await _read_auth_token(cookies_dir, login)
    if not token:
        await queue.put((login, "error", {"msg": "no cookies/auth-token"}))
        return

    api = TwitchAPI(token, proxy=proxy or "")
    await api.start()

    await queue.put((login, "status", {"status": "Querying", "note": "Fetching campaigns"}))
    try:
        data = await api.viewer_dashboard()

        camp_name = ""
        game_name = ""
        channel_id = ""

        try:
            d = (data or {}).get("data") or {}
            vd = d.get("viewer") or d.get("currentUser") or {}
            drops = vd.get("dropsDashboard") or vd.get("drops") or vd

            campaigns: list = []
            if isinstance(drops, dict):
                if isinstance(drops.get("currentCampaigns"), list):
                    campaigns = drops["currentCampaigns"]
                elif isinstance(drops.get("availableCampaigns"), list):
                    campaigns = drops["availableCampaigns"]
                elif isinstance(drops.get("campaigns"), list):
                    campaigns = drops["campaigns"]

                if campaigns:
                    c0 = campaigns[0]
                    camp_name = c0.get("name") or c0.get("displayName") or c0.get("id", "")
                    game = c0.get("game") or c0.get("gameTitle") or {}
                    game_name = game.get("name") or game.get("displayName") or ""
                    channel = c0.get("channel") or {}
                    channel_id = channel.get("id") or ""

            if not channel_id and isinstance(drops, dict):
                cs = drops.get("currentSession") or drops.get("dropCurrentSession") or {}
                channel_id = cs.get("channelId") or cs.get("channelID") or ""
                if not channel_id:
                    ch = cs.get("channel") or {}
                    channel_id = ch.get("id") or ""

            if not camp_name:
                import re, json as _json
                txt = _json.dumps(drops)
                m = re.search(r'"displayName"\s*:\s*"([^"]+)"', txt) or re.search(
                    r'"name"\s*:\s*"([^"]+)"', txt
                )
                if m:
                    camp_name = m.group(1)
                m2 = re.search(
                    r'"gameTitle"\s*:\s*{[^}]*"displayName"\s*:\s*"([^"]+)"', txt
                ) or re.search(r'"game"\s*:\s*{[^}]*"name"\s*:\s*"([^"]+)"', txt)
                if m2:
                    game_name = m2.group(1)
        except Exception:
            pass

        await queue.put((login, "campaign", {"camp": camp_name or "—", "game": game_name or "—"}))
        await queue.put((login, "status", {"status": "Ready", "note": "Campaigns discovered"}))

        last_inv = 0.0
        inv_delay = 0.0

        while not stop_evt.is_set():
            try:
                if channel_id:
                    await api.increment(channel_id)
            except (aiohttp.ClientError, RuntimeError) as e:
                await queue.put((login, "error", {"msg": f"increment: {e}"}))

            now = asyncio.get_event_loop().time()
            if now - last_inv >= inv_delay:
                try:
                    inv = await api.inventory()
                    drop = _find_time_based_drop(inv)
                    if drop:
                        required = drop.get("requiredMinutesWatched") or 0
                        current = drop.get("currentMinutesWatched") or 0
                        drop_id = drop.get("dropInstanceID") or drop.get("id")
                        drop_name = drop.get("benefit", {}).get("name") or drop.get("name") or ""
                        pct = (current / required * 100) if required else 0
                        remain = int(required - current)
                        await queue.put((login, "progress", {"pct": pct, "remain": remain}))
                        if pct >= 100 and drop_id:
                            try:
                                await api.claim(drop_id)
                                await queue.put(
                                    (
                                        login,
                                        "claimed",
                                        {"drop": drop_name, "at": datetime.utcnow().isoformat()},
                                    )
                                )
                            except (aiohttp.ClientError, RuntimeError) as e:
                                await queue.put((login, "error", {"msg": f"claim: {e}"}))
                except (aiohttp.ClientError, RuntimeError) as e:
                    await queue.put((login, "error", {"msg": f"inventory: {e}"}))
                last_inv = now
                inv_delay = 120 + random.randint(0, 60)

            try:
                await asyncio.wait_for(stop_evt.wait(), timeout=60)
            except asyncio.TimeoutError:
                pass

        await queue.put((login, "status", {"status": "Stopped"}))

    except (aiohttp.ClientError, RuntimeError) as e:
        await queue.put((login, "error", {"msg": f"GQL error: {e}"}))
    finally:
        await api.close()

