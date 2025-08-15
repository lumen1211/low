# src/miner.py
# -*- coding: utf-8 -*-
from __future__ import annotations
import asyncio
import json
from pathlib import Path
from typing import Optional, Dict, Any

import aiohttp
import random
from datetime import datetime

from .twitch_api import TwitchAPI

GQL_URL = "https://gql.twitch.tv/gql"
CLIENT_ID = "kimne78kx3ncx6brgo4mv6wki5h1ko"

# PersistedQuery хэши (стабильные для веб-клиента Twitch; время от времени меняются, тогда обновим)
PQ = {
    "ViewerDropsDashboard": "30ae6031cdfe0ea3f96a26caf96095a5336b7ccd4e0e7fe9bb2ff1b4cc7efabc",
}

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

async def _gql(session: aiohttp.ClientSession, op_name: str, variables: Dict[str, Any]) -> Any:
    body = {
        "operationName": op_name,
        "variables": variables,
        "extensions": {
            "persistedQuery": {"version": 1, "sha256Hash": PQ[op_name]}
        },
    }
    async with session.post(GQL_URL, json=body) as r:
        if r.status != 200:
            raise RuntimeError(f"GQL HTTP {r.status}")
        return await r.json()

async def _discover_campaign(session: aiohttp.ClientSession) -> Dict[str, Any]:
    # минимальный дашборд дропсов для текущего пользователя
    return await _gql(session, "ViewerDropsDashboard", {"isLoggedIn": True})

async def run_account(login: str, proxy: Optional[str], queue, stop_evt: asyncio.Event):
    """
    Шаг 2a: без видео. Только GQL-дискавери кампаний для аккаунта.
    - читает cookies/<login>.json -> auth-token
    - вызывает ViewerDropsDashboard PQ
    - обновляет GUI: Status/Campaign/Game
    """
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

        # Вытаскиваем “как получится” имя кампании и игры (структура у Twitch часто меняется)
        camp_name = ""
        game_name = ""
        channel_id = ""
        try:
            d = (data or {}).get("data") or {}
            vd = d.get("viewer") or d.get("currentUser") or {}
            drops = vd.get("dropsDashboard") or vd.get("drops") or vd

            if isinstance(drops, dict):
                # сначала пробуем текущую/активные кампании
                campaigns = []
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
                    game_name = (game.get("name") or game.get("displayName") or "")
                    ch = c0.get("self") or c0.get("channel") or {}
                    channel_id = ch.get("channelId") or ch.get("id") or ""
            # fallback — по тексту
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
        next_inv = loop.time()
        next_inc = loop.time()

        def find_drop(obj):
            if isinstance(obj, dict):
                if all(k in obj for k in ("requiredMinutesWatched", "currentMinutesWatched", "dropInstanceID")):
                    return obj
                for v in obj.values():
                    r = find_drop(v)
                    if r:
                        return r
            elif isinstance(obj, list):
                for item in obj:
                    r = find_drop(item)
                    if r:
                        return r
            return None

        while not stop_evt.is_set():
            now = loop.time()

            if channel_id and now >= next_inc:
                try:
                    await api.increment(channel_id)
                except (aiohttp.ClientError, RuntimeError) as e:
                    await queue.put((login, "error", {"msg": str(e)}))
                next_inc = now + 60

            if now >= next_inv:
                try:
                    inv = await api.inventory()
                    drop = find_drop(inv)
                    if drop:
                        req = drop.get("requiredMinutesWatched") or 0
                        cur = drop.get("currentMinutesWatched") or 0
                        did = drop.get("dropInstanceID") or ""
                        drop_name = drop.get("name") or drop.get("benefit", {}).get("name", "")
                        pct = (cur / req * 100) if req else 0
                        remain = max(0, req - cur)
                        await queue.put((login, "progress", {"pct": pct, "remain": remain}))
                        if pct >= 100 and did:
                            try:
                                await api.claim(did)
                                ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                                await queue.put((login, "claimed", {"drop": drop_name, "at": ts}))
                            except (aiohttp.ClientError, RuntimeError) as e:
                                await queue.put((login, "error", {"msg": str(e)}))
                except (aiohttp.ClientError, RuntimeError) as e:
                    await queue.put((login, "error", {"msg": str(e)}))
                next_inv = now + random.uniform(120, 180)

            await asyncio.sleep(1.0)

        await queue.put((login, "status", {"status": "Stopped"}))

    except Exception as e:
        await queue.put((login, "error", {"msg": f"GQL error: {e}"}))
    finally:
        await api.close()
