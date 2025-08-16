from __future__ import annotations
import aiohttp, asyncio
from yarl import URL
from typing import Any, Dict, Optional
from .ops import load_ops

GQL = URL("https://gql.twitch.tv/gql")

class TwitchAPI:
    def __init__(self, auth_token: str, client_id: str = "kimne78kx3ncx6brgo4mv6wki5h1ko", proxy: str = ""):
        self.auth = auth_token
        self.client_id = client_id
        self.proxy = proxy or None
        self.session: Optional[aiohttp.ClientSession] = None
        self.ops = load_ops()
        self.ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"

    async def start(self):
        if not self.session or self.session.closed:
            self.session = aiohttp.ClientSession(headers={"User-Agent": self.ua})

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()

    async def gql(self, operation: str, variables: Dict[str, Any]) -> Any:
        assert self.session is not None
        h = self.ops.get(operation, "")
        if not h or h.startswith("actual_hash"):
            raise RuntimeError(f"Persisted hash for {operation} not set in ops/ops.json")
        payload = {"operationName": operation, "variables": variables, "extensions": {"persistedQuery": {"version": 1, "sha256Hash": h}}}
        attempt = 0
        while True:
            try:
                async with self.session.post(GQL, json=payload, proxy=self.proxy, headers={
                    "Client-ID": self.client_id,
                    "Authorization": f"OAuth {self.auth}",
                    "Content-Type": "application/json",
                }) as r:
                    if r.status == 429:
                        await asyncio.sleep(min(60, 2**attempt))
                        attempt += 1
                        continue
                    if 200 <= r.status < 300:
                        data = await r.json()
                        if isinstance(data, list): data = data[0]
                        if isinstance(data, dict) and data.get("errors"):
                            raise RuntimeError(str(data["errors"]))
                        return data
                    text = await r.text()
                    raise RuntimeError(f"GQL {r.status}: {text}")
            except aiohttp.ClientError:
                await asyncio.sleep(min(60, 2**attempt))
                attempt += 1
                if attempt > 5:
                    raise

    async def viewer_dashboard(self) -> Any: return await self.gql("ViewerDropsDashboard", {})
    async def inventory(self) -> Any: return await self.gql("Inventory", {})
    async def increment(self, channel_id: str) -> Any: return await self.gql("IncrementDropCurrentSessionProgress", {"channelID": channel_id})
    async def claim(self, drop_instance_id: str) -> Any: return await self.gql("ClaimDropReward", {"dropInstanceID": drop_instance_id})
    async def campaign_details(self, campaign_id: str) -> Any: return await self.gql("DropsCampaignDetails", {"campaignID": campaign_id})

    async def get_live_channels(self, campaign_id: str) -> list[tuple[str,int,bool]]:
        data = await self.campaign_details(campaign_id)
        channels: list[tuple[str,int,bool]] = []
        try:
            d = (data or {}).get("data") or {}
            camp = d.get("campaign") or d.get("dropsCampaign") or {}
            avail = camp.get("availableChannels") or camp.get("channels") or []
            for ch in avail:
                chan = ch.get("channel") if isinstance(ch.get("channel"), dict) else ch
                cid = (chan or {}).get("id") or ch.get("id") or ""
                stream = (chan or {}).get("stream") or ch.get("stream") or {}
                live = bool(stream)
                viewers = stream.get("viewersCount") or 0
                channels.append((cid, viewers, live))
        except Exception:
            pass
        return channels
