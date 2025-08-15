# src/miner.py
# -*- coding: utf-8 -*-
from __future__ import annotations
import asyncio
import json
from pathlib import Path
from typing import Optional, Dict, Any
import random

from .twitch_api import TwitchAPI

CLIENT_ID = "kimne78kx3ncx6brgo4mv6wki5h1ko"

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

def _parse_inventory(inv: Dict[str, Any]) -> tuple[float, int, str, str]:
    """Best-effort parsing of progress information from Inventory result."""
    pct = 0.0
    remain = 0
    channel_id = ""
    drop_id = ""
    try:
        d = (inv or {}).get("data") or {}
        cu = d.get("currentUser") or d.get("user") or {}
        invd = cu.get("inventory") or {}
        camps = invd.get("dropCampaignsInProgress") or invd.get("dropCampaigns") or []
        if camps:
            camp = camps[0]
            drops = camp.get("timeBasedDrops") or camp.get("dropInstances") or camp.get("drops") or []
            if drops:
                drop = drops[0]
                required = (
                    drop.get("requiredMinutesWatched")
                    or drop.get("drop", {}).get("requiredMinutesWatched")
                    or 0
                )
                watched = (
                    drop.get("currentMinutesWatched")
                    or drop.get("self", {}).get("currentMinutesWatched")
                    or 0
                )
                channel_id = (
                    drop.get("channel", {}).get("id")
                    or drop.get("self", {}).get("channelID")
                    or camp.get("channelID")
                    or ""
                )
                drop_id = (
                    drop.get("dropInstanceID")
                    or drop.get("self", {}).get("dropInstanceID")
                    or drop.get("id")
                    or ""
                )
                if required:
                    pct = watched / required * 100.0
                    remain = max(int(required - watched), 0)
    except Exception:
        pass
    return pct, remain, channel_id, drop_id

async def run_account(login: str, proxy: Optional[str], queue, stop_evt: asyncio.Event):
    """
    Mining loop for a single account.

    - reads cookies/<login>.json -> auth-token
    - creates TwitchAPI with optional proxy and starts it
    - periodically sends progress updates and claims rewards
    """
    cookies_dir = Path("cookies")
    token = await _read_auth_token(cookies_dir, login)
    if not token:
        await queue.put((login, "error", {"msg": "no cookies/auth-token"}))
        return

    api = TwitchAPI(token, client_id=CLIENT_ID, proxy=proxy or "")
    await api.start()

    await queue.put((login, "status", {"status": "Querying", "note": "Fetching campaigns"}))
    try:
        data = await api.viewer_dashboard()

        # Вытаскиваем “как получится” имя кампании и игры (структура у Twitch часто меняется)
        camp_name = ""
        game_name = ""
        try:
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

                if campaigns:
                    c0 = campaigns[0]
                    camp_name = c0.get("name") or c0.get("displayName") or c0.get("id","")
                    game = c0.get("game") or c0.get("gameTitle") or {}
                    game_name = (game.get("name") or game.get("displayName") or "")
            if not camp_name:
                import re, json as _json
                txt = _json.dumps(drops)
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

        loop = asyncio.get_event_loop()
        next_inc = 0.0
        next_inv = 0.0
        channel_id = ""
        drop_id = ""
        while not stop_evt.is_set():
            now = loop.time()
            if channel_id and now >= next_inc:
                try:
                    await api.increment(channel_id)
                except Exception as e:
                    await queue.put((login, "error", {"msg": f"increment error: {e}"}))
                next_inc = now + 60
            if now >= next_inv:
                try:
                    inv = await api.inventory()
                    pct, remain, channel_id, drop_id = _parse_inventory(inv)
                    await queue.put((login, "progress", {"pct": pct, "remain": remain}))
                    if pct >= 100 and drop_id:
                        try:
                            await api.claim(drop_id)
                            await queue.put((login, "claimed", {"dropInstanceID": drop_id}))
                        except Exception as e:
                            await queue.put((login, "error", {"msg": f"claim error: {e}"}))
                except Exception as e:
                    await queue.put((login, "error", {"msg": f"inventory error: {e}"}))
                next_inv = now + 120 + random.uniform(0, 60)
            await asyncio.sleep(1.0)

        await queue.put((login, "status", {"status": "Stopped"}))

    except Exception as e:
        await queue.put((login, "error", {"msg": f"GQL error: {e}"}))
    finally:
        await api.close()
