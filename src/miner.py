# src/miner.py
from __future__ import annotations

import asyncio, json, time
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional, Dict, Any

from .twitch_api import TwitchAPI

CLIENT_ID = "kimne78kx3ncx6brgo4mv6wki5h1ko"

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
    """Фоновый воркер для одного аккаунта."""
    cookies_dir = Path("cookies")
    token = await _read_auth_token(cookies_dir, login)
    if not token:
        await queue.put((login, "error", {"msg": "no cookies/auth-token"}))
        return

    api = TwitchAPI(token, client_id=CLIENT_ID, proxy=proxy or "")
    await api.start()
    await queue.put((login, "status", {"status": "Querying", "note": "Fetching campaigns"}))

    try:
        dash = await api.viewer_dashboard()
        # Try to extract campaign name, game and channel id
        camp_name = game_name = channel_id = ""
        try:
            d = (dash or {}).get("data") or {}
            viewer = d.get("viewer") or d.get("currentUser") or {}
            drops = viewer.get("dropsDashboard") or viewer.get("drops") or viewer
            campaigns = []
            if isinstance(drops, dict):
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
                game_name = game.get("name") or game.get("displayName") or ""
                chan = c0.get("channel") or c0.get("broadcaster") or c0.get("self", {}).get("channel")
                if isinstance(chan, dict):
                    channel_id = chan.get("id") or chan.get("channelID") or ""
        except Exception:
            pass

        await queue.put((login, "campaign", {"camp": camp_name or "—", "game": game_name or "—"}))
        await queue.put((login, "status", {"status": "Running", "note": "Campaigns discovered"}))

        claimed: set[str] = set()
        next_inv = time.monotonic()
        while not stop_evt.is_set():
            try:
                if channel_id:
                    await api.increment(channel_id)
            except Exception as e:
                await queue.put((login, "error", {"msg": f"increment: {e}"}))
                break

            now = time.monotonic()
            if now >= next_inv:
                try:
                    inv = await api.inventory()
                    drop = None
                    d = (inv or {}).get("data") or {}
                    viewer = d.get("viewer") or d.get("currentUser") or {}
                    invd = viewer.get("inventory") or viewer
                    camps = invd.get("dropCampaigns") if isinstance(invd, dict) else []
                    for camp in camps or []:
                        for tb in camp.get("timeBasedDrops", []):
                            drop = tb; break
                        if drop:
                            break
                    if drop:
                        required = drop.get("requiredMinutesWatched") or 0
                        current = drop.get("currentMinutesWatched") or drop.get("self", {}).get("currentMinutesWatched", 0)
                        drop_instance_id = drop.get("dropInstanceID") or drop.get("self", {}).get("dropInstanceID")
                        name = drop.get("name") or drop.get("benefit", {}).get("name", "")
                        pct = (current / required * 100) if required else 0
                        remain = max(int(required - current), 0)
                        await queue.put((login, "progress", {"pct": pct, "remain": remain}))
                        if drop_instance_id and pct >= 100 and drop_instance_id not in claimed:
                            try:
                                await api.claim(drop_instance_id)
                                claimed.add(drop_instance_id)
                                ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
                                await queue.put((login, "claimed", {"drop": name, "at": ts}))
                            except Exception as e:
                                await queue.put((login, "error", {"msg": f"claim: {e}"}))
                    next_inv = now + 180
                except Exception as e:
                    await queue.put((login, "error", {"msg": f"inventory: {e}"}))
                    next_inv = now + 180
            await asyncio.sleep(60)

        await queue.put((login, "status", {"status": "Stopped"}))
    except Exception as e:
        await queue.put((login, "error", {"msg": f"GQL error: {e}"}))
    finally:
        await api.close()
