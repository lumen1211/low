#!/usr/bin/env python3
"""Fetch Client-Version and Client-Integrity for accounts."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import asyncio

from src.accounts import load_accounts
from src.client_integrity import fetch_ci, save_ci


async def _process(accounts):
    for acc in accounts:
        cv, ci = await fetch_ci(acc.login, acc.proxy)
        if cv and ci:
            save_ci(acc.login, cv, ci)
            print(f"{acc.login}: Client-Version={cv} Client-Integrity={ci}")
        else:
            print(f"{acc.login}: failed to capture headers")


def main() -> None:
    ap = argparse.ArgumentParser(description="Update Client-Version and Client-Integrity")
    ap.add_argument("--accounts", required=True, help="Path to CSV or TXT accounts file")
    args = ap.parse_args()

    accounts = load_accounts(Path(args.accounts))
    asyncio.run(_process(accounts))


if __name__ == "__main__":
    main()
