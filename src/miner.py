# src/miner.py
# -*- coding: utf-8 -*-
from __future__ import annotations
import asyncio
import json
from pathlib import Path
from typing import Optional, Dict, Any

import aiohttp

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
    """Минимальный дашборд дропсов для текущего пользователя.
    Возвращает сырой ответ Twitch GQL."""
    return await _gql(session, "ViewerDropsDashboard", {"isLoggedIn": True})

async def run_account(login: str, proxy: Optional[str], queue, cmd_q: asyncio.Queue, stop_evt: asyncio.Event):
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

            campaigns_info: list[Dict[str, Any]] = []
            camp_name = ""
            game_name = ""
            try:
                d = (data or {}).get("data") or {}
                vd = d.get("viewer") or d.get("currentUser") or {}
                drops = vd.get("dropsDashboard") or vd.get("drops") or vd

                if isinstance(drops, dict):
                    raw_camps = []
                    if isinstance(drops.get("currentCampaigns"), list):
                        raw_camps = drops["currentCampaigns"]
                    elif isinstance(drops.get("availableCampaigns"), list):
                        raw_camps = drops["availableCampaigns"]
                    elif isinstance(drops.get("campaigns"), list):
                        raw_camps = drops["campaigns"]

                    for c in raw_camps:
                        cid = c.get("id", "")
                        cname = c.get("name") or c.get("displayName") or cid
                        game = c.get("game") or c.get("gameTitle") or {}
                        gname = (game.get("name") or game.get("displayName") or "")

                        channels: list[str] = []
                        for ck in [
                            "allowlistedChannels",
                            "allowList",
                            "allowedChannels",
                            "channels",
                        ]:
                            if isinstance(c.get(ck), list):
                                for ch in c[ck]:
                                    nm = (
                                        ch.get("name")
                                        or ch.get("displayName")
                                        or ch.get("login")
                                        or ch.get("channelLogin")
                                        or ""
                                    )
                                    if nm:
                                        channels.append(nm)
                                if channels:
                                    break
                        campaigns_info.append(
                            {"id": cid, "name": cname, "game": gname, "channels": channels}
                        )

                    if campaigns_info:
                        camp_name = campaigns_info[0]["name"]
                        game_name = campaigns_info[0]["game"]

                if not camp_name:
                    import re, json as _json

                    txt = _json.dumps(drops)
                    m = re.search(r'"displayName"\s*:\s*"([^"]+)"', txt) or re.search(
                        r'"name"\s*:\s*"([^"]+)"', txt
                    )
                    if m:
                        camp_name = m.group(1)
                    m2 = re.search(
                        r'"gameTitle"\s*:\s*{[^}]*"displayName"\s*:\s*"([^"]+)"',
                        txt,
                    ) or re.search(r'"game"\s*:\s*{[^}]*"name"\s*:\s*"([^"]+)"', txt)
                    if m2:
                        game_name = m2.group(1)
            except Exception:
                pass

            await queue.put((login, "campaigns", {"campaigns": campaigns_info}))
            await queue.put(
                (
                    login,
                    "campaign",
                    {"camp": camp_name or "—", "game": game_name or "—"},
                )
            )
            if campaigns_info:
                await queue.put(
                    (login, "channels", {"channels": campaigns_info[0].get("channels", [])})
                )
            await queue.put((login, "status", {"status": "Ready", "note": "Campaigns discovered"}))

            # цикл ожидания команд/останова
            active_ids = [campaigns_info[0]["id"]] if campaigns_info else []
            while not stop_evt.is_set():
                try:
                    cmd, arg = await asyncio.wait_for(cmd_q.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue
                if cmd == "select_campaigns":
                    active_ids = [
                        cid for cid in arg if any(c["id"] == cid for c in campaigns_info)
                    ]
                    info = (
                        next((c for c in campaigns_info if c["id"] == active_ids[0]), None)
                        if active_ids
                        else None
                    )
                    if info:
                        await queue.put(
                            (login, "campaign", {"camp": info["name"], "game": info["game"]})
                        )
                        await queue.put(
                            (login, "channels", {"channels": info.get("channels", [])})
                        )

            await queue.put((login, "status", {"status": "Stopped"}))

        except Exception as e:
            await queue.put((login, "error", {"msg": f"GQL error: {e}"}))
