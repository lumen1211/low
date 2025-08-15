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
    # минимальный дашборд дропсов для текущего пользователя
    return await _gql(session, "ViewerDropsDashboard", {"isLoggedIn": True})


async def get_active_campaigns(login: str, proxy: Optional[str]) -> list[dict[str, str]]:
    """Вернуть список активных кампаний для аккаунта.

    Возвращает список словарей вида ``{"id": str, "name": str, "game": str}``.
    Если авторизация не удалась или кампании не найдены – возвращает пустой список.
    """
    cookies_dir = Path("cookies")
    token = await _read_auth_token(cookies_dir, login)
    if not token:
        return []

    headers = {
        "Client-Id": CLIENT_ID,
        "Authorization": f"OAuth {token}",
        "Content-Type": "application/json",
        "Origin": "https://www.twitch.tv",
        "Referer": "https://www.twitch.tv/",
    }

    connector = None
    if proxy:
        connector = aiohttp.TCPConnector()

    async with aiohttp.ClientSession(headers=headers, connector=connector) as session:
        try:
            data = await _discover_campaign(session)
        except Exception:
            return []

        res: list[dict[str, str]] = []
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

                for c in campaigns:
                    name = c.get("name") or c.get("displayName") or c.get("id", "")
                    game_obj = c.get("game") or c.get("gameTitle") or {}
                    game = game_obj.get("name") or game_obj.get("displayName") or ""
                    cid = c.get("id", "")
                    res.append({"id": cid, "name": name or cid, "game": game})
        except Exception:
            pass

        return res

async def run_account(login: str, proxy: Optional[str], queue, stop_evt: asyncio.Event, campaign: Optional[str] = None):
    """
    Шаг 2a: без видео. Только GQL-дискавери кампаний для аккаунта.
    - читает cookies/<login>.json -> auth-token
    - вызывает ViewerDropsDashboard PQ
    - выбирает кампанию ``campaign`` (если задана) и обновляет GUI: Status/Campaign/Game
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

            # получаем список кампаний и подбираем нужную
            campaigns = []
            drops: dict[str, Any] = {}
            try:
                d = (data or {}).get("data") or {}
                vd = d.get("viewer") or d.get("currentUser") or {}
                drops = vd.get("dropsDashboard") or vd.get("drops") or vd

                if isinstance(drops, dict):
                    if isinstance(drops.get("currentCampaigns"), list):
                        campaigns = drops["currentCampaigns"]
                    elif isinstance(drops.get("availableCampaigns"), list):
                        campaigns = drops["availableCampaigns"]
                    elif isinstance(drops.get("campaigns"), list):
                        campaigns = drops["campaigns"]
            except Exception:
                campaigns = []

            sel = None
            for c in campaigns:
                name = c.get("name") or c.get("displayName") or c.get("id", "")
                if not campaign or campaign == name:
                    sel = c
                    break
            if sel is None and campaigns:
                sel = campaigns[0]

            camp_name = ""; game_name = ""
            if sel:
                camp_name = sel.get("name") or sel.get("displayName") or sel.get("id", "")
                game = sel.get("game") or sel.get("gameTitle") or {}
                game_name = game.get("name") or game.get("displayName") or ""
            else:
                # fallback — по тексту
                try:
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

            # Пока просто ждём Stop (заготовка под 2b: heartbeats/прогресс)
            while not stop_evt.is_set():
                await asyncio.sleep(1.0)

            await queue.put((login, "status", {"status": "Stopped"}))

        except Exception as e:
            await queue.put((login, "error", {"msg": f"GQL error: {e}"}))
