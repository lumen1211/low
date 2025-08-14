from __future__ import annotations
import json
from typing import Dict
from pathlib import Path

OPS_PATH = Path("ops/ops.json")
REQUIRED = ["ViewerDropsDashboard","Inventory","IncrementDropCurrentSessionProgress","ClaimDropReward"]

def load_ops() -> Dict[str,str]:
    return json.loads(OPS_PATH.read_text(encoding="utf-8"))

def missing_ops(ops: dict) -> list[str]:
    miss = []
    for k in REQUIRED:
        v = ops.get(k, "")
        if not v or v.startswith("actual_hash"):
            miss.append(k)
    return miss
