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
