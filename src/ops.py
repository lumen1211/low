from __future__ import annotations
import json
import logging
from typing import Dict
from pathlib import Path

OPS_PATH = Path("ops/ops.json")
REQUIRED = [
    "ViewerDropsDashboard",
    "Inventory",
    "IncrementDropCurrentSessionProgress",
    "ClaimDropReward",
    "DropsCampaignDetails",
]

logger = logging.getLogger(__name__)


def load_ops() -> Dict[str, str]:
    try:
        return json.loads(OPS_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        logger.error("OPS file not found at %s", OPS_PATH)
        return {}
    except json.JSONDecodeError as exc:
        logger.error("Failed to decode OPS file %s: %s", OPS_PATH, exc)
        return {}

def missing_ops(ops: dict) -> list[str]:
    miss = []
    for k in REQUIRED:
        v = ops.get(k, "")
        if not v or v.startswith("actual_hash"):
            miss.append(k)
    return miss
