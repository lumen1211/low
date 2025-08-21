import asyncio
import pytest
import sys, types

# provide minimal aiohttp stub so miner imports without dependency
aiohttp = types.ModuleType("aiohttp")
class ClientSession:
    pass
class ClientError(Exception):
    pass
aiohttp.ClientSession = ClientSession
aiohttp.ClientError = ClientError
sys.modules.setdefault("aiohttp", aiohttp)

from src import miner

class StubAPI:
    last = None
    def __init__(self, *a, **kw):
        StubAPI.last = self
        self.spade_calls = 0
        self.hls_calls = 0
        self.cur = 0
    async def start(self):
        pass
    async def close(self):
        pass
    async def viewer_dashboard(self):
        return {
            "data": {
                "viewer": {
                    "dropsDashboard": {
                        "currentCampaigns": [
                            {
                                "id": "camp1",
                                "name": "Camp",
                                "game": {"name": "Game"},
                                "allowlistedChannels": [{"name": "chan"}],
                            }
                        ]
                    }
                }
            }
        }
    async def get_live_channels(self, cid):
        return [("chan", "123", 100, True)]
    async def get_spade_and_hls(self, login):
        return ("http://spade.test", "http://usher.test/playlist.m3u8")
    async def drop_current_session_context(self, *a):
        pass
    async def spade_minute_watched(self, url):
        self.spade_calls += 1
    async def head_hls(self, url):
        self.hls_calls += 1
    async def inventory(self):
        self.cur += 1
        return {
            "requiredMinutesWatched": 2,
            "currentMinutesWatched": self.cur,
            "dropInstanceID": "d1",
            "name": "Drop",
        }
    async def claim(self, did):
        pass


def test_spade_hls_synced(monkeypatch):
    async def _run():
        monkeypatch.setattr(miner, "TwitchAPI", StubAPI)
        monkeypatch.setattr(miner, "auth_token_from_cookies", lambda login: "token")
        q = asyncio.Queue()
        stop = asyncio.Event()
        task = asyncio.create_task(miner.run_account("user", None, q, stop, tick_interval=0.01))
        await asyncio.sleep(0.6)
        stop.set()
        await task
        msgs = []
        while not q.empty():
            msgs.append(await q.get())
        progress = [m for m in msgs if m[1] == "progress"]
        assert progress
        api = StubAPI.last
        assert api.spade_calls == len(progress)
        assert api.hls_calls == len(progress)

    asyncio.run(_run())
