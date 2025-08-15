# src/miner.py
# -*- coding: utf-8 -*-
from __future__ import annotations
import asyncio
import json
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple

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
    """Минимальный дашборд дропсов для текущего пользователя."""
    return await _gql(session, "ViewerDropsDashboard", {"isLoggedIn": True})


def _extract_campaigns(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Вытащить список кампаний (id, name, game, raw)."""
    d = (data or {}).get("data") or {}
    vd = d.get("viewer") or d.get("currentUser") or {}
    drops = vd.get("dropsDashboard") or vd.get("drops") or vd
    campaigns: List[Dict[str, Any]] = []
    if isinstance(drops, dict):
        for key in ("currentCampaigns", "availableCampaigns", "campaigns"):
            if isinstance(drops.get(key), list):
                campaigns = drops[key]
                break

    result: List[Dict[str, Any]] = []
    for c in campaigns:
        cid = c.get("id") or ""
        name = c.get("name") or c.get("displayName") or cid
        game_obj = c.get("game") or c.get("gameTitle") or {}
        game_name = game_obj.get("name") or game_obj.get("displayName") or ""
        result.append({"id": cid, "name": name, "game": game_name, "raw": c})
    return result


def _calc_progress(camp: Optional[Dict[str, Any]]) -> Tuple[float, int]:
    """Грубая оценка прогресса кампании (pct, remain_min)."""
    if not isinstance(camp, dict):
        return 0.0, 0
    drops = camp.get("timeBasedDrops") or camp.get("drops") or []
    for drop in drops:
        self_data = drop.get("self") or drop.get("currentSession") or {}
        cur = (
            self_data.get("currentMinutesWatched")
            or self_data.get("currentProgressMin")
            or drop.get("currentProgressMin")
            or 0
        )
        req = (
            drop.get("requiredMinutesWatched")
            or drop.get("requiredProgressMin")
            or self_data.get("requiredMinutesWatched")
            or self_data.get("requiredProgressMin")
            or 0
        )
        if req and cur < req:
            pct = cur / req * 100
            remain = int(req - cur)
            return pct, remain
    return 100.0, 0


async def fetch_campaigns(login: str, proxy: Optional[str] = None) -> List[Dict[str, Any]]:
    """Запросить активные кампании для аккаунта."""
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

    async with aiohttp.ClientSession(headers=headers) as session:
        try:
            data = await _discover_campaign(session)
            return _extract_campaigns(data)
        except Exception:
            return []

async def run_account(
    login: str,
    proxy: Optional[str],
    queue,
    stop_evt: asyncio.Event,
    campaign_id: Optional[str] = None,
):
    """
    Мониторинг выбранной кампании:
    - читает cookies/<login>.json -> auth-token
    - вызывает ViewerDropsDashboard PQ
    - обновляет GUI: Status/Campaign/Game/Progress
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
            campaigns = _extract_campaigns(data)
            camp = None
            if campaign_id:
                for c in campaigns:
                    if c["id"] == campaign_id:
                        camp = c
                        break
            if camp is None and campaigns:
                camp = campaigns[0]

            camp_name = camp.get("name") if camp else ""
            game_name = camp.get("game") if camp else ""

            await queue.put((login, "campaign", {"camp": camp_name or "—", "game": game_name or "—"}))
            pct, remain = _calc_progress(camp)
            await queue.put((login, "progress", {"pct": pct, "remain": remain}))
            await queue.put((login, "status", {"status": "Ready", "note": "Campaign monitoring"}))

            # Периодически обновляем прогресс кампании
            while not stop_evt.is_set():
                await asyncio.sleep(60.0)
                try:
                    data = await _discover_campaign(session)
                    campaigns = _extract_campaigns(data)
                    if campaign_id:
                        for c in campaigns:
                            if c["id"] == campaign_id:
                                camp = c
                                break
                    else:
                        camp = campaigns[0] if campaigns else None
                    pct, remain = _calc_progress(camp)
                    await queue.put((login, "progress", {"pct": pct, "remain": remain}))
                except Exception:
                    pass

            await queue.put((login, "status", {"status": "Stopped"}))

        except Exception as e:
            await queue.put((login, "error", {"msg": f"GQL error: {e}"}))
