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

    headers = {
        "Client-Id": CLIENT_ID,
        "Authorization": f"OAuth {token}",
        "Content-Type": "application/json",
        "Origin": "https://www.twitch.tv",
        "Referer": "https://www.twitch.tv/",
    }

    async with aiohttp.ClientSession(headers=headers) as session:
        await queue.put((login, "status", {"status": "Querying", "note": "Fetching campaigns"}))
        try:
            data = await _discover_campaign(session)

            # Вытаскиваем “как получится” имя кампании и игры (структура у Twitch часто меняется)
            camp_name = ""
            game_name = ""
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
                        camp_name = c0.get("name") or c0.get("displayName") or c0.get("id","")
                        game = c0.get("game") or c0.get("gameTitle") or {}
                        game_name = (game.get("name") or game.get("displayName") or "")
                # fallback — по тексту
                if not camp_name:
                    import re, json as _json
                    txt = _json.dumps(drops)
                    m = re.search(r'"displayName"\s*:\s*"([^"]+)"', txt) or re.search(r'"name"\s*:\s*"([^"]+)"', txt)
                    if m: camp_name = m.group(1)
                    m2 = re.search(r'"gameTitle"\s*:\s*{[^}]*"displayName"\s*:\s*"([^"]+)"', txt) or re.search(r'"game"\s*:\s*{[^}]*"name"\s*:\s*"([^"]+)"', txt)
                    if m2: game_name = m2.group(1)
            except Exception:
                pass

            await queue.put((login, "campaign", {"camp": camp_name or "—", "game": game_name or "—"}))
            await queue.put((login, "status", {"status": "Ready", "note": "Campaigns discovered"}))

        except Exception as e:
            await queue.put((login, "error", {"msg": f"GQL error: {e}"}))

    # дальнейшая работа через TwitchAPI — мониторинг дропсов
    api = TwitchAPI(token, proxy or "")
    await api.start()
    try:
        while not stop_evt.is_set():
            try:
                inv = await api.inventory()
                drops = []
                d = (inv or {}).get("data") or {}
                vd = d.get("viewer") or d.get("currentUser") or {}
                inv2 = vd.get("inventory") or vd
                tbd = inv2.get("timeBasedDrops") or {}
                if isinstance(tbd, dict):
                    if isinstance(tbd.get("edges"), list):
                        drops = [e.get("node") or e for e in tbd["edges"]]
                    elif isinstance(tbd.get("items"), list):
                        drops = tbd["items"]
                elif isinstance(tbd, list):
                    drops = tbd

                if drops:
                    drop = drops[0]
                    req = drop.get("requiredMinutesWatched") or drop.get("requiredMinutes") or 0
                    cur = (
                        (drop.get("self") or {}).get("currentMinutesWatched")
                        or drop.get("currentMinutesWatched")
                        or 0
                    )
                    pct = float(cur) / req * 100 if req else 0.0
                    remain = max(int(req - cur), 0)
                    await queue.put((login, "progress", {"pct": pct, "remain": remain}))

                    if req and cur >= req:
                        drop_id = (
                            (drop.get("self") or {}).get("dropInstanceID")
                            or drop.get("dropInstanceID")
                            or ""
                        )
                        try:
                            await api.claim(drop_id)
                            ts = datetime.utcnow().isoformat(timespec="seconds")
                            name = drop.get("name") or drop.get("id", "")
                            await queue.put((login, "claimed", {"drop": name, "at": ts}))
                        except Exception as e:
                            await queue.put((login, "error", {"msg": f"claim error: {e}"}))
                await asyncio.sleep(30.0)
            except Exception as e:
                await queue.put((login, "error", {"msg": f"inventory error: {e}"}))
                await asyncio.sleep(30.0)
        await queue.put((login, "status", {"status": "Stopped"}))
    finally:
        await api.close()
