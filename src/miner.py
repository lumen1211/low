# src/miner.py
# -*- coding: utf-8 -*-
from __future__ import annotations
import asyncio
import json
from pathlib import Path
from typing import Optional, Dict, Any, List

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

def _extract_campaigns(drops: Dict[str, Any]) -> List[Dict[str, str]]:
    """Извлекаем список кампаний из ответа Twitch."""
    campaigns: List[Dict[str, str]] = []
    lst: List[Dict[str, Any]] = []
    if isinstance(drops.get("currentCampaigns"), list):
        lst = drops["currentCampaigns"]
    elif isinstance(drops.get("availableCampaigns"), list):
        lst = drops["availableCampaigns"]
    elif isinstance(drops.get("campaigns"), list):
        lst = drops["campaigns"]

    for c in lst:
        game = c.get("game") or c.get("gameTitle") or {}
        campaigns.append({
            "id": c.get("id", ""),
            "name": c.get("name") or c.get("displayName") or c.get("id", ""),
            "game": game.get("name") or game.get("displayName") or "",
        })
    return campaigns

def _find_campaign(drops: Dict[str, Any], cid: str) -> Optional[Dict[str, Any]]:
    """Возвращает объект кампании по ID."""
    for key in ("currentCampaigns", "availableCampaigns", "campaigns"):
        lst = drops.get(key)
        if isinstance(lst, list):
            for c in lst:
                if c.get("id") == cid:
                    return c
    return None

def _campaign_progress(camp: Dict[str, Any]) -> tuple[float, int]:
    """Пытаемся вычислить прогресс кампании в минутах."""
    drops = camp.get("timeBasedDrops") or camp.get("timebasedDrops") or camp.get("drops") or []
    for d in drops:
        cur = (
            (d.get("self") or {}).get("currentMinutesWatched")
            or d.get("currentMinutesWatched")
            or 0
        )
        req = (
            (d.get("self") or {}).get("requiredMinutesWatched")
            or d.get("requiredMinutesWatched")
            or 0
        )
        if req:
            pct = min(cur / req, 1.0) * 100.0
            remain = max(req - cur, 0)
            return pct, int(remain)
    return 0.0, 0

async def run_account(
    login: str,
    proxy: Optional[str],
    queue,
    stop_evt: asyncio.Event,
    campaign_id: Optional[str] = None,
):
    """
    Шаг 2a: без видео. GQL-дискавери кампаний для аккаунта и трекинг прогресса.
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

            # Вытаскиваем список кампаний
            camp_list: List[Dict[str, str]] = []
            try:
                d = (data or {}).get("data") or {}
                vd = d.get("viewer") or d.get("currentUser") or {}
                drops = vd.get("dropsDashboard") or vd.get("drops") or vd
                if isinstance(drops, dict):
                    camp_list = _extract_campaigns(drops)
                await queue.put(
                    (login, "campaigns", {"list": [(c["id"], c["name"]) for c in camp_list]})
                )
            except Exception:
                pass

            if campaign_id:
                # найдём выбранную кампанию
                selected = None
                for c in camp_list:
                    if c["id"] == campaign_id:
                        selected = c
                        break
                if not selected and camp_list:
                    selected = camp_list[0]
                    campaign_id = selected["id"]
                if selected:
                    await queue.put(
                        (
                            login,
                            "campaign",
                            {
                                "id": campaign_id,
                                "camp": selected["name"],
                                "game": selected["game"],
                            },
                        )
                    )
                await queue.put(
                    (login, "status", {"status": "Running", "note": "Tracking campaign"})
                )
                # цикл обновления прогресса
                while not stop_evt.is_set():
                    try:
                        data = await _discover_campaign(session)
                        d = (data or {}).get("data") or {}
                        vd = d.get("viewer") or d.get("currentUser") or {}
                        drops = vd.get("dropsDashboard") or vd.get("drops") or vd
                        camp_obj = _find_campaign(drops, campaign_id)
                        if camp_obj:
                            pct, remain = _campaign_progress(camp_obj)
                            await queue.put(
                                (login, "progress", {"pct": pct, "remain": remain})
                            )
                    except Exception:
                        pass
                    await asyncio.sleep(30.0)

                await queue.put((login, "status", {"status": "Stopped"}))
            else:
                await queue.put(
                    (login, "status", {"status": "Ready", "note": "Campaigns discovered"})
                )

        except Exception as e:
            await queue.put((login, "error", {"msg": f"GQL error: {e}"}))
