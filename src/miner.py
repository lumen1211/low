# src/miner.py
# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Optional, Dict, Any

from .twitch_api import TwitchAPI

CLIENT_ID = "kimne78kx3ncx6brgo4mv6wki5h1ko"


# --------------------------- утилиты ---------------------------------
async def _read_auth_token(cookies_dir: Path, login: str) -> Optional[str]:
    """Читает из cookies/<login>.json значение 'auth-token'."""
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


# ------------------------ основной шаг аккаунта -----------------------
async def run_account(
    login: str,
    proxy: Optional[str],
    queue,
    stop_evt: asyncio.Event,
    cmd_q: Optional[asyncio.Queue] = None,
):
    """
    Без видео: discovery дроп-кампаний для аккаунта и публикация статусов.
    Работает через TwitchAPI (инкапсулирует GQL/Helix и прокси).

    Порядок:
      - читает cookies/<login>.json -> auth-token
      - вызывает viewer_dashboard()
      - извлекает первую кампанию (имя и игра)
      - по campaign_id запрашивает живые каналы и публикует их в GUI
      - ждёт Stop; (при необходимости можно обрабатывать команды из cmd_q)
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

        # --- парсим название кампании/игры (структура у Twitch меняется; берём «как получится»)
        camp_name = ""
        game_name = ""
        c0: Dict[str, Any] = {}

        try:
            d = (data or {}).get("data") or {}
            vd = d.get("viewer") or d.get("currentUser") or {}
            drops = vd.get("dropsDashboard") or vd.get("drops") or vd

            if isinstance(drops, dict):
                # список кампаний в разных полях
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
            # fallback — попытаться выдернуть текстом
            if not camp_name:
                import re, json as _json
                txt = _json.dumps(drops)
                m = re.search(r'"displayName"\s*:\s*"([^"]+)"', txt) or re.search(r'"name"\s*:\s*"([^"]+)"', txt)
                if m:
                    camp_name = m.group(1)
                m2 = re.search(r'"gameTitle"\s*:\s*{[^}]*"displayName"\s*:\s*"([^"]+)"', txt) or \
                     re.search(r'"game"\s*:\s*{[^}]*"name"\s*:\s*"([^"]+)"', txt)
                if m2:
                    game_name = m2.group(1)
        except Exception:
            pass

        # --- получаем каналы по кампании через API (если есть campaign_id)
        campaign_id = c0.get("id") or c0.get("campaignID") or c0.get("campaignId") or ""
        streams = []
        try:
            if campaign_id:
                # ожидается список кортежей (login, viewers, is_live)
                chans = await api.get_live_channels(campaign_id)
                # сортируем по зрителям (убывание)
                chans.sort(key=lambda x: x[1], reverse=True)
                # приводим к формату для GUI
                streams = [{"name": ch[0], "viewers": ch[1], "live": bool(ch[2])} for ch in chans]
        except Exception:
            streams = []

        # --- публикация в GUI
        await queue.put((login, "campaign", {"camp": camp_name or "—", "game": game_name or "—"}))
        await queue.put((login, "channels", {"channels": streams}))
        await queue.put((login, "status", {"status": "Ready", "note": "Campaigns discovered"}))

        # Пока просто ждём Stop (заготовка под heartbeats/прогресс/ручные команды)
        while not stop_evt.is_set():
            if cmd_q is not None:
                try:
                    cmd, val = cmd_q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                else:
                    if cmd == "switch" and val:
                        await queue.put((login, "switch", {"channel": str(val)}))
            await asyncio.sleep(1.0)

        await queue.put((login, "status", {"status": "Stopped"}))

    except Exception as e:
        await queue.put((login, "error", {"msg": f"GQL error: {e}"}))
    finally:
        await api.close()


