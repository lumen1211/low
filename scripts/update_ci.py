#!/usr/bin/env python3
"""Fetch Client-Version and Client-Integrity for accounts."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from playwright.sync_api import sync_playwright

from src.accounts import load_accounts, COOKIES_DIR, CI_DIR


DROPS_URL = "https://www.twitch.tv/drops"
GQL_URL = "https://gql.twitch.tv/gql"


def fetch_ci(login: str, proxy: str = "") -> tuple[str, str]:
    """Open Drops page and capture Client-Version and Client-Integrity headers."""
    cookies_file = COOKIES_DIR / f"{login}.json"
    if not cookies_file.exists():
        print(f"No cookies for {login}, skip")
        return "", ""

    cookies = json.loads(cookies_file.read_text(encoding="utf-8"))

    with sync_playwright() as pw:
        launch_kwargs = {"headless": True}
        if proxy:
            launch_kwargs["proxy"] = {"server": proxy}
        browser = pw.chromium.launch(**launch_kwargs)
        context = browser.new_context()
        context.add_cookies(cookies)
        page = context.new_page()
        with page.expect_request(GQL_URL) as req_info:
            page.goto(DROPS_URL)
        req = req_info.value
        headers = req.headers
        browser.close()

    cv = headers.get("client-version", "")
    ci = headers.get("client-integrity", "")
    return cv, ci


def save_ci(login: str, cv: str, ci: str) -> None:
    CI_DIR.mkdir(parents=True, exist_ok=True)
    path = CI_DIR / f"{login}.json"
    data = {"client_version": cv, "client_integrity": ci}
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(description="Update Client-Version and Client-Integrity")
    ap.add_argument("--accounts", required=True, help="Path to CSV or TXT accounts file")
    args = ap.parse_args()

    accounts = load_accounts(Path(args.accounts))
    for acc in accounts:
        cv, ci = fetch_ci(acc.login, acc.proxy)
        if cv and ci:
            save_ci(acc.login, cv, ci)
            print(f"{acc.login}: Client-Version={cv} Client-Integrity={ci}")
        else:
            print(f"{acc.login}: failed to capture headers")


if __name__ == "__main__":
    main()
