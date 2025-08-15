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


def _load_channels() -> list[str]:
    """Возвращает список каналов из channels.txt (по одному в строке)."""
    fp = Path("channels.txt")
    if not fp.exists():
        return []
    lines = fp.read_text(encoding="utf-8").splitlines()
    return [ln.strip() for ln in lines if ln.strip() and not ln.startswith("#")]


async def get_live_channels(session: aiohttp.ClientSession, channels: list[str]) -> list[str]:
    """Проверяем live-статус заданных каналов через Helix /streams."""
    if not channels:
        return []
    params = [("user_login", ch) for ch in channels]
    async with session.get("https://api.twitch.tv/helix/streams", params=params) as r:
        if r.status != 200:
            return []
        data = await r.json()
        items = data.get("data") or []
        return [itm.get("user_login", "").lower() for itm in items]

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

            channels = _load_channels()
            if not channels:
                await queue.put((login, "status", {"status": "NoChannels", "note": "channels.txt not found"}))
                return

            idx = 0
            current = channels[idx]
            await queue.put((login, "status", {"status": "Watching", "note": f"{current}"}))
            progress = 0

            while not stop_evt.is_set():
                live = await get_live_channels(session, [current])
                if current.lower() not in live:
                    idx = (idx + 1) % len(channels)
                    current = channels[idx]
                    progress = 0
                    msg = f"switching to {current}"  # channel offline
                    print(f"[{login}] {msg}")
                    await queue.put((login, "status", {"status": "Switch", "note": msg}))
                    continue
                progress = (progress + 5) % 100
                await queue.put((login, "progress", {"pct": progress, "remain": 100 - progress}))
                await asyncio.sleep(15.0)

            await queue.put((login, "status", {"status": "Stopped"}))

        except Exception as e:
            await queue.put((login, "error", {"msg": f"GQL error: {e}"}))
