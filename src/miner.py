# src/miner.py
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from .accounts import auth_token_from_cookies
from .twitch_api import TwitchAPI


async def _safe_put(queue: asyncio.Queue, payload: Tuple[str, str, Dict[str, Any]]):
    """Кладём сообщение в GUI-очередь, не роняя воркер из-за случайной ошибки."""
    try:
        await queue.put(payload)
    except Exception:
        pass


def _parse_campaigns_from_dashboard(data: Any) -> List[Dict[str, Any]]:
    """
    Выдёргиваем список кампаний из ответа ViewerDropsDashboard.
    Возвращаем список словарей: {id, name, game, channels(str[])}.
    """
    out: List[Dict[str, Any]] = []
    try:
        d = (data or {}).get("data") or {}
        vd = d.get("viewer") or d.get("currentUser") or {}
        drops = vd.get("dropsDashboard") or vd.get("drops") or vd

        raw_camps = []
        if isinstance(drops, dict):
            if isinstance(drops.get("currentCampaigns"), list):
                raw_camps = drops["currentCampaigns"]
            elif isinstance(drops.get("availableCampaigns"), list):
                raw_camps = drops["availableCampaigns"]
            elif isinstance(drops.get("campaigns"), list):
                raw_camps = drops["campaigns"]

        for c in raw_camps or []:
            cid = c.get("id") or c.get("campaignID") or c.get("campaignId") or ""
            cname = c.get("name") or c.get("displayName") or cid
            game = c.get("game") or c.get("gameTitle") or {}
            gname = (game.get("name") or game.get("displayName") or "") or "—"

            # Попытка достать список разрешённых каналов из кампании (если Twitch их отдаёт)
            channels: List[str] = []
            for ck in ("allowlistedChannels", "allowList", "allowedChannels", "channels"):
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

            out.append({"id": cid, "name": cname, "game": gname, "channels": channels})
    except Exception:
        pass
    return out


async def _initial_channels(api: TwitchAPI, campaign_id: str) -> List[Dict[str, Any]]:
    """
    Получаем живые каналы по кампании через DropCampaignDetails
    (или DropsCampaignDetails).
    Возвращаем список словарей: {name, viewers, live}.
    """
    try:
        chans = await api.get_live_channels(campaign_id)  # List[tuple[login, id, viewers, live]]
        chans.sort(key=lambda x: x[2], reverse=True)
        items: List[Dict[str, Any]] = []
        for clogin, _cid, viewers, live in chans:
            items.append(
                {"name": str(clogin) or "unknown", "viewers": int(viewers or 0), "live": bool(live)}
            )
        return items
    except Exception:
        return []


def _extract_time_based_drop(inv: Any) -> Optional[Dict[str, Any]]:
    """
    Ищем тайм-бейзд дроп в ответе Inventory:
    ожидаем поля requiredMinutesWatched/currentMinutesWatched/dropInstanceID (+ name).
    Возвращаем узел drop или None.
    """
    def walk(obj: Any) -> Optional[Dict[str, Any]]:
        if isinstance(obj, dict):
            keys = set(obj.keys())
            if {"requiredMinutesWatched", "currentMinutesWatched", "dropInstanceID"} <= keys:
                return obj
            for v in obj.values():
                r = walk(v)
                if r:
                    return r
        elif isinstance(obj, list):
            for v in obj:
                r = walk(v)
                if r:
                    return r
        return None

    return walk(inv)


async def run_account(
    login: str,
    proxy: Optional[str],
    queue: asyncio.Queue,
    stop_evt: asyncio.Event,
    cmd_q: Optional[asyncio.Queue] = None,
    client_version: str = "",
    client_integrity: str = "",
    tick_interval: float = 60.0,
):
    """
    Воркер для одного аккаунта:
      1) читает cookies/<login>.json -> auth-token
      2) запрашивает ViewerDropsDashboard и публикует список кампаний
      3) публикует активную кампанию и список каналов по ней (если удаётся)
      4) периодически опрашивает Inventory и отправляет прогресс/клеймы
      5) ждёт команды из cmd_q: 'select_campaigns', 'switch'
    """
    await _safe_put(queue, (login, "status", {"status": "Starting", "note": "Init worker"}))

    token = auth_token_from_cookies(login)
    if not token:
        await _safe_put(queue, (login, "error", {"msg": "no cookies/auth-token"}))
        await _safe_put(queue, (login, "status", {"status": "Stopped"}))
        return

    api = TwitchAPI(
        token,
        proxy=proxy or "",
        client_version=client_version or "",
        client_integrity=client_integrity or "",
        login=login,
    )
    await api.start()
    await _safe_put(queue, (login, "status", {"status": "Querying", "note": "Fetching campaigns"}))

    try:
        # 1) Дашборд дропсов
        dashboard = await api.viewer_dashboard()
        campaigns = _parse_campaigns_from_dashboard(dashboard)
        await _safe_put(queue, (login, "campaigns", {"campaigns": campaigns}))

        # выберем первую как активную по умолчанию
        active_ids: List[str] = [campaigns[0]["id"]] if campaigns else []
        selected_campaign = campaigns[0] if campaigns else None
        if selected_campaign:
            await _safe_put(
                queue,
                (login, "campaign", {"camp": selected_campaign["name"], "game": selected_campaign["game"]}),
            )

            # 2) список живых каналов
            ch_items = await _initial_channels(api, selected_campaign["id"])
            if not ch_items and selected_campaign.get("channels"):
                ch_items = [{"name": n, "viewers": 0, "live": False} for n in selected_campaign["channels"]]
            await _safe_put(queue, (login, "channels", {"channels": ch_items}))

        await _safe_put(queue, (login, "status", {"status": "Ready", "note": "Campaigns discovered"}))

        # 3) периодика: increment + inventory
        loop = asyncio.get_event_loop()
        next_tick = loop.time() + tick_interval

        # данные канала (login, id) для DropCurrentSessionContext
        increment_channel: Optional[tuple[str, str]] = None
        spade_url = ""
        hls_url = ""
        try:
            if selected_campaign:
                live = await api.get_live_channels(selected_campaign["id"])
                for clogin, cid, _v, _live in live:
                    if isinstance(cid, str) and cid.isdigit():
                        increment_channel = (clogin, cid)
                        break
        except Exception:
            increment_channel = None

        if increment_channel:
            try:
                spade_url, hls_url = await api.get_spade_and_hls(increment_channel[0])
            except Exception:
                spade_url = ""
                hls_url = ""
        # 4) цикл
        while not stop_evt.is_set():
            now = loop.time()

            # команды из GUI
            if cmd_q is not None:
                try:
                    cmd, arg = cmd_q.get_nowait()
                    if cmd == "select_campaigns" and isinstance(arg, list):
                        ids = [cid for cid in arg if any(c["id"] == cid for c in campaigns)]
                        if ids:
                            active_ids = ids
                            selected_campaign = next((c for c in campaigns if c["id"] == active_ids[0]), selected_campaign)
                            if selected_campaign:
                                await _safe_put(queue, (login, "campaign", {
                                    "camp": selected_campaign["name"], "game": selected_campaign["game"]
                                }))
                                ch_items = await _initial_channels(api, selected_campaign["id"])
                                if not ch_items and selected_campaign.get("channels"):
                                    ch_items = [{"name": n, "viewers": 0, "live": False} for n in selected_campaign["channels"]]
                                await _safe_put(queue, (login, "channels", {"channels": ch_items}))
                                # переопределим increment канал
                                increment_channel = None
                                try:
                                    live = await api.get_live_channels(selected_campaign["id"])
                                    for clogin, cid, _v, _live in live:
                                        if isinstance(cid, str) and cid.isdigit():
                                            increment_channel = (clogin, cid)
                                            break
                                except Exception:
                                    pass
                    elif cmd == "switch":
                        await _safe_put(queue, (login, "switch", {"channel": str(arg or "")}))
                except asyncio.QueueEmpty:
                    pass
                except Exception as e:
                    await _safe_put(queue, (login, "error", {"msg": f"cmd_q error: {e}"}))

            if now >= next_tick:
                if increment_channel:
                    try:
                        clogin, cid = increment_channel
                        await api.drop_current_session_context(clogin, cid)
                    except Exception as e:
                        await _safe_put(queue, (login, "error", {"msg": f"increment error: {e}"}))

                if spade_url:
                    try:
                        await api.spade_minute_watched(spade_url)
                    except Exception as e:
                        await _safe_put(queue, (login, "error", {"msg": f"spade error: {e}"}))

                if hls_url:
                    try:
                        await api.head_hls(hls_url)
                    except Exception as e:
                        await _safe_put(queue, (login, "error", {"msg": f"hls error: {e}"}))

                try:
                    inv = await api.inventory()
                    drop = _extract_time_based_drop(inv)
                    if drop:
                        req = int(drop.get("requiredMinutesWatched") or 0)
                        cur = int(drop.get("currentMinutesWatched") or 0)
                        did = str(drop.get("dropInstanceID") or "")
                        drop_name = (
                            drop.get("name")
                            or (drop.get("benefit") or {}).get("name")
                            or drop.get("id")
                            or ""
                        )
                        pct = (cur / req * 100) if req else 0.0
                        remain = max(0, req - cur)
                        await _safe_put(queue, (login, "progress", {"pct": pct, "remain": remain, "drop": drop_name}))

                        if req and cur >= req and did:
                            try:
                                await api.claim(did)
                                ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
                                await _safe_put(queue, (login, "claimed", {"drop": drop_name, "at": ts, "pct": 100, "remain": 0}))
                            except Exception as e:
                                await _safe_put(queue, (login, "error", {"msg": f"claim error: {e}"}))
                except Exception as e:
                    await _safe_put(queue, (login, "error", {"msg": f"inventory error: {e}"}))
                finally:
                    next_tick = now + tick_interval

            await asyncio.sleep(0.5)

        await _safe_put(queue, (login, "status", {"status": "Stopped"}))

    except Exception as e:
        await _safe_put(queue, (login, "error", {"msg": f"GQL error: {e}"}))
        await _safe_put(queue, (login, "status", {"status": "Stopped"}))
    finally:
        try:
            await api.close()
        except Exception:
            pass
