from __future__ import annotations
from typing import Dict, Optional

OP: Dict[str, Dict[str, Optional[str]]] = {
    "ViewerDropsDashboard": {
        "sha256": "30ae6031cdfe0ea3f96a26caf96095a5336b7ccd4e0e7fe9bb2ff1b4cc7efabc",
    },
    "Inventory": {
        "sha256": "62e14a6105e1efc9c35e5ad9d211fba163f92f2189e50e54f9b89e0ee1cdeacb",
    },
    "IncrementDropCurrentSessionProgress": {
        "sha256": "464f66e4a79e2dfe01fb38bebf37cc37828c4a21c2e59954e07de3f1cdd192d1",
    },
    # sha256 hash for ClaimDropReward changes frequently; set None if unknown
    "ClaimDropReward": {
        "sha256": None,
    },
}

REQUIRED = [
    "ViewerDropsDashboard",
    "Inventory",
    "IncrementDropCurrentSessionProgress",
    "ClaimDropReward",
]


def get_op(name: str) -> Dict[str, Optional[str]]:
    """Return persisted query data for the given operation.

    Raises ``RuntimeError`` if the operation is missing or its ``sha256`` is not set.
    """
    op = OP.get(name)
    if not op:
        raise KeyError(f"Unknown operation: {name}")
    if not op.get("sha256"):
        raise RuntimeError(f"Persisted hash for {name} not set; update src/ops.py")
    return op


def missing_ops() -> list[str]:
    """Return a list of required operations without sha256 hashes."""
    miss = []
    for k in REQUIRED:
        v = OP.get(k, {}).get("sha256")
        if not v:
            miss.append(k)
    return miss
