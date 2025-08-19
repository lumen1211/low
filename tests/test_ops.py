import json
import src.ops as ops


def test_missing_ops_detects_missing_hash(tmp_path, monkeypatch):
    # create ops.json without DropsCampaignDetails
    data = {
        "ViewerDropsDashboard": "hash",
        "Inventory": "hash",
        "IncrementDropCurrentSessionProgress": "hash",
        "ClaimDropReward": "hash",
    }
    path = tmp_path / "ops.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(ops, "OPS_PATH", path)

    loaded = ops.load_ops()
    assert ops.missing_ops(loaded) == ["DropsCampaignDetails"]
