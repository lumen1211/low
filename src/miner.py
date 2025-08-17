# src/miner.py
from __future__ import annotations

import asyncio
import random
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
        # молча — вернём то, что получилось (или пусто)
        pass
    return out


async def _initial_channels(api: TwitchAPI, campaign_id: str) -> List[Dict[str, Any]]:
    """
    Получаем живые каналы по кампании через DropsCampaignDetails.
    Возвращаем список словарей: {name, viewers, live}.
    """
    try:
        chans = await api.get_live_channels(campaign_id)  # List[tuple[id_or_login, viewers, live]]
        # сортируем по зрителям (desc)
        chans.sort(key=lambda x: x[1], reverse=True)
        items: List[Dict[str, Any]] = []
        for cid_or_login, viewers, live in chans:
            items.append(
                {"name": str(cid_or_login) or "unknown", "viewers": int(viewers or 0), "live": bool(live)}
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

    api = TwitchAPI(token, proxy=proxy or "")
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

            # 2) попробовать получить живые каналы детальнее
            ch_items = await _initial_channels(api, selected_campaign["id"])
            if not ch_items and selected_campaign.get("channels"):
                # fallback — если хотя бы логины есть в dashboard
                ch_items = [{"name": n, "viewers": 0, "live": False} for n in selected_campaign["channels"]]
            await _safe_put(queue, (login, "channels", {"channels": ch_items}))
        await _safe_put(queue, (login, "status", {"status": "Ready", "note": "Campaigns discovered"}))

        # 3) Периодические задачи: инкремент (если есть numeric channel id) и инвентори
        loop = asyncio.get_event_loop()
        next_inc = loop.time() + 60.0
        next_inv = loop.time() + random.uniform(120.0, 180.0)

        # выберем канал для increment: берём первый с числовым id из DropsCampaignDetails
        increment_channel_id: Optional[str] = None
        try:
            if selected_campaign:
                live = await api.get_live_channels(selected_campaign["id"])
                # live: list[(cid_or_login, viewers, is_live)]
                for cid, _v, _live in live:
                    if isinstance(cid, str) and cid.isdigit():
                        increment_channel_id = cid
                        break
        except Exception:
            increment_channel_id = None  # просто не будем вызывать increment

        # 4) Главный цикл
        while not stop_evt.is_set():
            now = loop.time()

            # обработка команд GUI (не блокируя)
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
                                increment_channel_id = None
                                try:
                                    live = await api.get_live_channels(selected_campaign["id"])
                                    for cid, _v, _live in live:
                                        if isinstance(cid, str) and cid.isdigit():
                                            increment_channel_id = cid
                                            break
                                except Exception:
                                    pass
                    elif cmd == "switch":
                        await _safe_put(queue, (login, "switch", {"channel": str(arg or "")}))
                except asyncio.QueueEmpty:
                    pass
                except Exception as e:
                    await _safe_put(queue, (login, "error", {"msg": f"cmd_q error: {e}"}))

            # increment прогресса раз в ~60 сек, если удалось найти numeric channel id
            if increment_channel_id and now >= next_inc:
                try:
                    await api.increment(increment_channel_id)
                except Exception as e:
                    await _safe_put(queue, (login, "error", {"msg": f"increment error: {e}"}))
                    # не выходим — просто попробуем позже
                next_inc = now + 60.0

            # inventory поллинг с джиттером
            if now >= next_inv:
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
                    next_inv = now + random.uniform(120.0, 180.0)

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
