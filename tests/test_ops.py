import json
import pytest
import src.ops as ops


@pytest.mark.parametrize("missing", ops.REQUIRED)
def test_missing_ops_detects_missing_hash(tmp_path, monkeypatch, missing):
    data = {k: "hash" for k in ops.REQUIRED if k != missing}
    path = tmp_path / "ops.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(ops, "OPS_PATH", path)

    loaded = ops.load_ops()
    assert ops.missing_ops(loaded) == [missing]


def test_get_hash_accepts_alias(tmp_path, monkeypatch):
    data = {
        "ViewerDropsDashboard": "h1",
        "DropCampaignDetails": "h2",
        "Inventory": "h3",
        "DropCurrentSessionContext": "h4",
        "DropsPage_ClaimDropRewards": "h5",
    }
    path = tmp_path / "ops.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(ops, "OPS_PATH", path)

    loaded = ops.load_ops()
    assert ops.missing_ops(loaded) == []
    assert ops.get_hash(loaded, "DropsCampaignDetails") == (
        "DropCampaignDetails",
        "h2",
    )
    assert ops.get_hash(loaded, "IncrementDropCurrentSessionProgress") == (
        "DropCurrentSessionContext",
        "h4",
    )
    assert ops.get_hash(loaded, "ClaimDropReward") == (
        "DropsPage_ClaimDropRewards",
        "h5",
    )
