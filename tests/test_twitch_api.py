import asyncio
import sys
import types

sys.modules["aiohttp"] = types.SimpleNamespace(
    ClientSession=object, ClientError=Exception
)

import src.twitch_api as twitch_api

twitch_api.aiohttp = sys.modules["aiohttp"]
TwitchAPI = twitch_api.TwitchAPI


def test_viewer_dashboard_returns_campaigns(monkeypatch):
    api = TwitchAPI("token")

    async def fake_start():
        pass

    async def fake_gql(operation, variables):
        assert operation == "ViewerDropsDashboard"
        assert variables == {"fetchRewardCampaigns": True}
        return {"data": {"currentUser": {"dropCampaigns": ["c1"]}}}

    monkeypatch.setattr(api, "start", fake_start)
    monkeypatch.setattr(api, "gql", fake_gql)

    data = asyncio.run(api.viewer_dashboard())
    assert data["data"]["currentUser"]["dropCampaigns"] == ["c1"]


def test_headers_include_ci_tokens(monkeypatch):
    captured = {}
    api = TwitchAPI("token", client_version="cv", client_integrity="ci")

    class DummyResp:
        status = 200

        async def json(self):
            return {}

        async def text(self):
            return ""

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            pass

    async def fake_start():
        class DummySession:
            closed = False

            def post(self, url, json=None, proxy=None, headers=None):
                captured.update(headers)
                return DummyResp()

        api.session = DummySession()

    monkeypatch.setattr(api, "start", fake_start)

    asyncio.run(api.viewer_dashboard())
    assert captured.get("Client-Version") == "cv"
    assert captured.get("Client-Integrity") == "ci"
    assert captured.get("X-Device-Id") == api.x_device_id
    assert captured.get("Client-Session-Id") == api.client_session_id
    assert captured.get("Playback-Session-Id") == api.playback_session_id


def test_spade_includes_session_ids(monkeypatch):
    captured = {}
    api = TwitchAPI("token")

    class DummyResp:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            pass

    async def fake_start():
        class DummySession:
            closed = False

            def get(self, url, proxy=None):
                captured["url"] = str(url)
                return DummyResp()

        api.session = DummySession()

    monkeypatch.setattr(api, "start", fake_start)

    asyncio.run(api.spade_minute_watched("http://spade.test/event"))
    u = twitch_api.URL(captured["url"])
    q = dict(u.query)
    assert q.get("X-Device-Id") == api.x_device_id
    assert q.get("Client-Session-Id") == api.client_session_id
    assert q.get("Playback-Session-Id") == api.playback_session_id


def test_challenge_refreshes_tokens(monkeypatch):
    calls = []
    api = TwitchAPI("token", login="user", client_version="old", client_integrity="bad")

    class DummyResp:
        def __init__(self, status, text=""):
            self.status = status
            self._text = text

        async def json(self):
            return {}

        async def text(self):
            return self._text

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            pass

    def post(url, json=None, proxy=None, headers=None):
        calls.append(headers)
        if len(calls) == 1:
            return DummyResp(400, "integrity challenge")
        return DummyResp(200, "{}")

    class DummySession:
        closed = False

        def post(self, *a, **kw):
            return post(*a, **kw)

    async def fake_start():
        api.session = DummySession()

    async def fake_fetch_ci(login, proxy=""):
        return "cv2", "ci2"

    monkeypatch.setattr(api, "start", fake_start)
    monkeypatch.setattr(twitch_api, "fetch_ci", fake_fetch_ci)
    monkeypatch.setattr(twitch_api, "save_ci", lambda *a, **kw: None)

    asyncio.run(api.viewer_dashboard())

    assert calls[1]["Client-Version"] == "cv2"
    assert calls[1]["Client-Integrity"] == "ci2"
