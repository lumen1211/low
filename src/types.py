from __future__ import annotations
from dataclasses import dataclass
from typing import Optional

@dataclass
class Account:
    label: str
    login: str
    password: str = ""
    proxy: str = ""
    totp_secret: str = ""
    status: str = "Idle"
    note: str = ""
    active_campaign: str = ""
    game: str = ""
    progress_pct: float = 0.0
    remaining_minutes: int = 0
    last_claim_at: Optional[str] = None
