from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Tuple

# Directories for cookies and client integrity tokens
COOKIES_DIR = Path("cookies")
COOKIES_DIR.mkdir(parents=True, exist_ok=True)
CI_DIR = Path("ci")
CI_DIR.mkdir(parents=True, exist_ok=True)

DROPS_URL = "https://www.twitch.tv/drops"
GQL_URL = "https://gql.twitch.tv/gql"
# Default time-to-live for stored tokens (24h)
CI_TTL = 60 * 60 * 24

async def fetch_ci(login: str, proxy: str = "") -> Tuple[str, str]:
    """Open Drops page in headless browser and capture CI headers.

    Returns tuple (Client-Version, Client-Integrity). If cookies for login are
    missing or headers cannot be captured, returns empty strings.
    """
    cookies_file = COOKIES_DIR / f"{login}.json"
    if not cookies_file.exists():
        return "", ""
    try:
        cookies = json.loads(cookies_file.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return "", ""

    # Import playwright lazily so tests without the dependency still work
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        launch_kwargs = {"headless": True}
        if proxy:
            launch_kwargs["proxy"] = {"server": proxy}
        browser = await pw.chromium.launch(**launch_kwargs)
        context = await browser.new_context()
        await context.add_cookies(cookies)
        page = await context.new_page()

        fut: asyncio.Future = asyncio.get_event_loop().create_future()

        def handle_request(req):
            if req.url == GQL_URL and not fut.done():
                fut.set_result(req.headers)

        page.on("request", handle_request)
        await page.goto(DROPS_URL)
        headers = await fut
        await browser.close()

    cv = headers.get("client-version", "")
    ci = headers.get("client-integrity", "")
    return cv, ci


def save_ci(login: str, cv: str, ci: str, ttl: int = CI_TTL) -> None:
    """Persist tokens for account with expiration timestamp."""
    data = {
        "client_version": cv,
        "client_integrity": ci,
        "expires_at": time.time() + ttl,
    }
    path = CI_DIR / f"{login}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_ci(login: str) -> Tuple[str, str]:
    """Load tokens for account if not expired."""
    path = CI_DIR / f"{login}.json"
    if not path.exists():
        return "", ""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return "", ""
    expires = float(data.get("expires_at") or 0)
    if expires and expires < time.time():
        return "", ""
    cv = (
        data.get("client_version")
        or data.get("Client-Version")
        or data.get("clientVersion")
        or data.get("client-version")
        or ""
    )
    ci = (
        data.get("client_integrity")
        or data.get("Client-Integrity")
        or data.get("clientIntegrity")
        or data.get("client-integrity")
        or ""
    )
    return str(cv), str(ci)
