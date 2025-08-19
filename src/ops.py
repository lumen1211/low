from __future__ import annotations
import json
import logging
from typing import Dict
from pathlib import Path

OPS_PATH = Path("ops/ops.json")

# Алиасы для операций GQL: ключ и его взаимозаменяемый вариант.
ALIASES = {
    "DropCampaignDetails": "DropsCampaignDetails",
    "DropsCampaignDetails": "DropCampaignDetails",
}

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

def get_hash(ops: dict, op: str) -> tuple[str, str]:
    """Возвращает имя операции и её hash, учитывая алиасы."""
    candidates = [op]
    alias = ALIASES.get(op)
    if alias:
        candidates.append(alias)

    for name in candidates:
        h = ops.get(name, "")
        if h and not str(h).startswith("actual_hash"):
            return name, h
    raise RuntimeError(f"Persisted hash for {op} not set in ops/ops.json")


def missing_ops(ops: dict) -> list[str]:
    miss = []
    for k in REQUIRED:
        try:
            get_hash(ops, k)
        except RuntimeError:
            miss.append(k)
    return miss
