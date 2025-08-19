import asyncio
from src.twitch_api import TwitchAPI


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
