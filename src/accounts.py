from __future__ import annotations
from pathlib import Path
from typing import Optional
import csv
import json
import logging

from .types import Account
from .client_integrity import COOKIES_DIR, CI_DIR, load_ci

logger = logging.getLogger(__name__)


def _parse_txt(path: Path) -> list[Account]:
    res: list[Account] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(":", 3)
        if len(parts) < 2:
            continue
        login = parts[0].strip()
        password = parts[1]
        totp = parts[2] if len(parts) >= 3 else ""
        rest = parts[3] if len(parts) >= 4 else ""
        proxy = rest
        client_version = ""
        client_integrity = ""
        if rest:
            rparts = rest.rsplit(":", 2)
            if len(rparts) == 3:
                maybe_proxy, maybe_cv, maybe_ci = rparts
                if "/" not in maybe_cv and "/" not in maybe_ci and "@" not in maybe_cv and "@" not in maybe_ci:
                    proxy, client_version, client_integrity = maybe_proxy, maybe_cv, maybe_ci
            elif len(rparts) == 2:
                maybe_proxy, maybe_ci = rparts
                if "/" not in maybe_ci and "@" not in maybe_ci:
                    proxy, client_integrity = maybe_proxy, maybe_ci
        res.append(
            Account(
                label=login,
                login=login,
                password=password,
                proxy=proxy,
                totp_secret=totp,
                client_version=client_version,
                client_integrity=client_integrity,
            )
        )
    return res


def _parse_csv(path: Path) -> list[Account]:
    res: list[Account] = []
    with path.open("r", encoding="utf-8", newline="") as f:
        r = csv.DictReader(f)
        for row in r:
            res.append(
                Account(
                    label=(row.get("label") or row.get("login") or "").strip(),
                    login=(row.get("login") or "").strip(),
                    password=(row.get("password") or "").strip(),
                    proxy=(row.get("proxy") or "").strip(),
                    totp_secret=(row.get("totp_secret") or "").strip(),
                    client_version=(
                        row.get("client_version")
                        or row.get("Client-Version")
                        or ""
                    ).strip(),
                    client_integrity=(
                        row.get("client_integrity")
                        or row.get("Client-Integrity")
                        or ""
                    ).strip(),
                )
            )
    return [a for a in res if a.login]
def load_accounts(path: Path) -> list[Account]:
    ext = path.suffix.lower()
    if ext == ".txt":
        accs = _parse_txt(path)
    else:
        accs = _parse_csv(path)
    for a in accs:
        cv, ci = load_ci(a.login)
        if not a.client_version:
            a.client_version = cv
        if not a.client_integrity:
            a.client_integrity = ci
    return accs


def auth_token_from_cookies(login: str) -> Optional[str]:
    p = COOKIES_DIR / f"{login}.json"
    if not p.exists():
        return None
    try:
        cookies = json.loads(p.read_text(encoding="utf-8"))
        for c in cookies:
            if c.get("name") == "auth-token":
                return c.get("value") or None
    except (FileNotFoundError, json.JSONDecodeError) as e:
        logger.error("Failed to load auth token for %s: %s", login, e)
        return None
    return None
