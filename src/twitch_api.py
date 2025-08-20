# src/twitch_api.py
from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional

import aiohttp
from yarl import URL

from .ops import load_ops, get_hash

GQL = URL("https://gql.twitch.tv/gql")
MAX_RETRIES = 5


class TwitchAPI:
    def __init__(
        self,
        auth_token: str,
        client_id: str = "kimne78kx3ncx6brgo4mv6wki5h1ko",
        proxy: str = "",
        client_version: str = "",
        client_integrity: str = "",
    ):
        self.auth = auth_token
        self.client_id = client_id
        self.proxy = proxy or None  # прокси указываем на уровне запроса
        self.client_version = client_version
        self.client_integrity = client_integrity
        self.session: Optional[aiohttp.ClientSession] = None
        self.ops = load_ops()
        self.ua = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        )

    async def start(self) -> None:
        if not self.session or self.session.closed:
            # В aiohttp нет глобального параметра proxy у ClientSession.
            # Используем proxy=... в каждом запросе (см. self.gql()).
            self.session = aiohttp.ClientSession(headers={"User-Agent": self.ua})

    async def close(self) -> None:
        if self.session and not self.session.closed:
            await self.session.close()

    async def gql(self, operation: str, variables: Dict[str, Any]) -> Any:
        """Вызов Twitch GQL с persistedQuery hash из ops.json, с ретраями на 429/сетевых ошибках."""
        if not self.session or self.session.closed:
            raise RuntimeError("Session not started; call start() first")

        operation, h = get_hash(self.ops, operation)

        payload = {
            "operationName": operation,
            "variables": variables,
            "extensions": {"persistedQuery": {"version": 1, "sha256Hash": h}},
        }

        attempt = 0
        while True:
            try:
                headers = {
                    "Client-ID": self.client_id,
                    "Authorization": f"OAuth {self.auth}",
                    "Content-Type": "application/json",
                }
                if self.client_version:
                    headers["Client-Version"] = self.client_version
                if self.client_integrity:
                    headers["Client-Integrity"] = self.client_integrity
                async with self.session.post(
                    GQL,
                    json=payload,
                    proxy=self.proxy,  # прокси на уровне запроса
                    headers=headers,
                ) as r:
                    if r.status == 429:
                        attempt += 1
                        if attempt > MAX_RETRIES:
                            raise RuntimeError(
                                "GQL 429: Too Many Requests; retry limit exceeded"
                            )
                        await asyncio.sleep(min(60, 2 ** (attempt - 1)))
                        continue

                    if 500 <= r.status < 600:
                        attempt += 1
                        if attempt > MAX_RETRIES:
                            raise RuntimeError(
                                f"GQL {r.status}: Server error; retry limit exceeded"
                            )
                        await asyncio.sleep(min(60, 2 ** (attempt - 1)))
                        continue

                    if 200 <= r.status < 300:
                        data = await r.json()
                        # иногда приходит список с единственным объектом
                        if isinstance(data, list):
                            data = data[0]
                        if isinstance(data, dict) and data.get("errors"):
                            raise RuntimeError(str(data["errors"]))
                        return data

                    text = await r.text()
                    raise RuntimeError(f"GQL {r.status}: {text}")

            except aiohttp.ClientError:
                attempt += 1
                if attempt > MAX_RETRIES:
                    raise
                await asyncio.sleep(min(60, 2 ** (attempt - 1)))

    # ----------------- Удобные обёртки -----------------

    async def viewer_dashboard(self) -> Any:
        await self.start()
        return await self.gql("ViewerDropsDashboard", {"fetchRewardCampaigns": True})

    async def inventory(self) -> Any:
        await self.start()
        return await self.gql("Inventory", {})

    async def drop_current_session_context(self, channel_login: str, channel_id: str) -> Any:
        await self.start()
        return await self.gql(
            "DropCurrentSessionContext",
            {"channelLogin": channel_login, "channelID": channel_id},
        )

    async def claim(self, drop_instance_id: str) -> Any:
        await self.start()
        return await self.gql(
            "DropsPage_ClaimDropRewards",
            {"input": {"dropInstanceID": drop_instance_id}},
        )

    async def campaign_details(self, campaign_id: str) -> Any:
        await self.start()
        return await self.gql("DropCampaignDetails", {"campaignID": campaign_id})

    async def get_live_channels(self, campaign_id: str) -> list[tuple[str, str, int, bool]]:
        """
        Возвращает список каналов для кампании:
        [(channel_login, channel_id, viewers, is_live), ...]
        """
        await self.start()
        data = await self.campaign_details(campaign_id)
        channels: list[tuple[str, str, int, bool]] = []
        try:
            d = (data or {}).get("data") or {}
            camp = d.get("campaign") or d.get("dropsCampaign") or {}
            avail = camp.get("availableChannels") or camp.get("channels") or []
            for ch in avail:
                # Twitch может слать либо объект {channel:{...}}, либо плоский объект канала
                chan = ch.get("channel") if isinstance(ch, dict) else None
                chan_obj = chan if isinstance(chan, dict) else (ch if isinstance(ch, dict) else {})
                login = chan_obj.get("login") or chan_obj.get("name") or ""
                cid = chan_obj.get("id") or ""
                stream = chan_obj.get("stream") or {}
                live = bool(stream)
                viewers = stream.get("viewersCount") or 0
                channels.append((login or cid, cid, int(viewers), live))
        except Exception:
            pass
        return channels
