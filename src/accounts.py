from __future__ import annotations
from pathlib import Path
from typing import List, Optional
import csv, json, logging
from .types import Account

COOKIES_DIR = Path("cookies"); COOKIES_DIR.mkdir(exist_ok=True)
logger = logging.getLogger(__name__)

def _parse_txt(path: Path) -> list[Account]:
    res: list[Account] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"): continue
        parts = line.split(":")
        if len(parts) < 2: continue
        login = parts[0].strip()
        password = parts[1]
        totp = parts[2] if len(parts) >= 3 else ""
        proxy = parts[3] if len(parts) >= 4 else ""
        res.append(Account(label=login, login=login, password=password, proxy=proxy, totp_secret=totp))
    return res

def _parse_csv(path: Path) -> list[Account]:
    res: list[Account] = []
    with path.open("r", encoding="utf-8") as f:
        r = csv.DictReader(f)
        for row in r:
            res.append(Account(
                label=(row.get("label") or row.get("login") or "").strip(),
                login=(row.get("login") or "").strip(),
                password=(row.get("password") or "").strip(),
                proxy=(row.get("proxy") or "").strip(),
                totp_secret=(row.get("totp_secret") or "").strip(),
            ))
    return [a for a in res if a.login]

def load_accounts(path: Path) -> list[Account]:
    ext = path.suffix.lower()
    if ext == ".txt":
        return _parse_txt(path)
    return _parse_csv(path)

def auth_token_from_cookies(login: str) -> Optional[str]:
    p = COOKIES_DIR / f"{login}.json"
    if not p.exists(): return None
    try:
        cookies = json.loads(p.read_text(encoding="utf-8"))
        for c in cookies:
            if c.get("name") == "auth-token":
                return c.get("value")
    except (FileNotFoundError, json.JSONDecodeError) as e:
        logger.error("Failed to load auth token for %s: %s", login, e)
        return None
    return None
