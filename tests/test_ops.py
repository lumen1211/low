import json
import src.ops as ops


def test_missing_ops_detects_missing_hash(tmp_path, monkeypatch):
    # create ops.json without DropsCampaignDetails
    data = {
        "ViewerDropsDashboard": "hash",
        "Inventory": "hash",
        "DropCurrentSessionContext": "hash",
        "DropsPage_ClaimDropRewards": "hash",
    }
    path = tmp_path / "ops.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(ops, "OPS_PATH", path)

    loaded = ops.load_ops()
    assert ops.missing_ops(loaded) == ["DropsCampaignDetails"]


def test_missing_ops_accepts_alias(tmp_path, monkeypatch):
    data = {
        "ViewerDropsDashboard": "hash",
        "Inventory": "hash",
        "DropCurrentSessionContext": "hash",
        "DropsPage_ClaimDropRewards": "hash",
        "DropCampaignDetails": "hash",
    }
    path = tmp_path / "ops.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(ops, "OPS_PATH", path)

    loaded = ops.load_ops()
    assert ops.missing_ops(loaded) == []
