# src/miner.py
# -*- coding: utf-8 -*-
from __future__ import annotations
import asyncio
import json
from pathlib import Path
from typing import Optional

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
    Шаг 2a: без видео. Только API-дискавери кампаний для аккаунта.
    - читает cookies/<login>.json -> auth-token
    - вызывает метод viewer_dashboard
    - обновляет GUI: Status/Campaign/Game
    """
    cookies_dir = Path("cookies")
    token = await _read_auth_token(cookies_dir, login)
    if not token:
        await queue.put((login, "error", {"msg": "no cookies/auth-token"}))
        return

    api = TwitchAPI(auth_token=token, proxy=proxy or "")
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

        # Пока просто ждём Stop (заготовка под 2b: heartbeats/прогресс)
        while not stop_evt.is_set():
            await asyncio.sleep(1.0)

        await queue.put((login, "status", {"status": "Stopped"}))

    except Exception as e:
        await queue.put((login, "error", {"msg": f"API error: {e}"}))
    finally:
        await api.close()
