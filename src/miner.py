# src/miner.py
from __future__ import annotations

import asyncio
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
      4) ждёт команды из cmd_q: 'select_campaigns', 'switch'
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
        # 1) дашборд дропсов
        dashboard = await api.viewer_dashboard()
        campaigns = _parse_campaigns_from_dashboard(dashboard)
        await _safe_put(queue, (login, "campaigns", {"campaigns": campaigns}))

        # выберем первую как активную по умолчанию
        active_ids: List[str] = [campaigns[0]["id"]] if campaigns else []
        if campaigns:
            first = campaigns[0]
            await _safe_put(
                queue,
                (login, "campaign", {"camp": first["name"], "game": first["game"]}),
            )

            # 2) попробовать получить живые каналы детальнее
            ch_items = await _initial_channels(api, first["id"])
            if not ch_items and first.get("channels"):
                # fallback — если хотя бы логины есть в dashboard
                ch_items = [{"name": n, "viewers": 0, "live": False} for n in first["channels"]]
            await _safe_put(queue, (login, "channels", {"channels": ch_items}))

        await _safe_put(queue, (login, "status", {"status": "Ready", "note": "Campaigns discovered"}))

        # 3) цикл ожидания команд/останова
        while not stop_evt.is_set():
            try:
                if cmd_q is None:
                    await asyncio.sleep(0.8)
                    continue
                cmd, arg = await asyncio.wait_for(cmd_q.get(), timeout=0.8)
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                await _safe_put(queue, (login, "error", {"msg": f"cmd_q error: {e}"}))
                continue

            if cmd == "select_campaigns":
                # пользователь выбрал кампании в GUI-диалоге
                if isinstance(arg, list):
                    # оставляем только реально доступные
                    ids = [cid for cid in arg if any(c["id"] == cid for c in campaigns)]
                    if ids:
                        active_ids = ids
                        # отобразим первую выбранную
                        info = next((c for c in campaigns if c["id"] == active_ids[0]), None)
                        if info:
                            await _safe_put(
                                queue,
                                (login, "campaign", {"camp": info["name"], "game": info["game"]}),
                            )
                            ch_items = await _initial_channels(api, info["id"])
                            if not ch_items and info.get("channels"):
                                ch_items = [{"name": n, "viewers": 0, "live": False} for n in info["channels"]]
                            await _safe_put(queue, (login, "channels", {"channels": ch_items}))

            elif cmd == "switch":
                # ручная смена канала: без видеособиратора просто подсветим в GUI
                await _safe_put(queue, (login, "switch", {"channel": str(arg or "")}))

        await _safe_put(queue, (login, "status", {"status": "Stopped"}))

    except Exception as e:
        await _safe_put(queue, (login, "error", {"msg": f"GQL error: {e}"}))
        await _safe_put(queue, (login, "status", {"status": "Stopped"}))
    finally:
        try:
            await api.close()
        except Exception:
            pass

