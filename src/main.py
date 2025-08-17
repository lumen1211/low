#!/usr/bin/env python3
from __future__ import annotations

import sys
import argparse
import csv
from pathlib import Path

from PySide6.QtWidgets import QApplication
from .gui import MainWindow
from .ops import load_ops, missing_ops


def create_sample_txt(path: Path):
    if path.exists():
        print(f"{path} уже существует")
        return
    path.write_text("user1:pass1\nuser2:pass2\n", encoding="utf-8")
    print(f"Создан пример TXT: {path}")


def create_sample_csv(path: Path):
    if path.exists():
        print(f"{path} уже существует")
        return
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(
            f, fieldnames=["label", "login", "password", "proxy", "totp_secret"]
        )
        w.writeheader()
        w.writerow(
            {
                "label": "acc001",
                "login": "user1",
                "password": "pass1",
                "proxy": "",
                "totp_secret": "",
            }
        )
    print(f"Создан пример CSV: {path}")


def main():
    p = argparse.ArgumentParser(description="Twitch Drops — API Miner (TXT/CSV)")
    p.add_argument("--accounts", type=str, help="Путь к CSV или TXT (login:password)")
    p.add_argument("--create-sample-txt", action="store_true")
    p.add_argument("--create-sample-csv", action="store_true")
    p.add_argument(
        "--onboarding",
        action="store_true",
        help="Открыть логин-окна и сохранить cookies/<login>.json",
    )
    args = p.parse_args()

    if args.create_sample_txt:
        create_sample_txt(Path("accounts.txt"))
        return
    if args.create_sample_csv:
        create_sample_csv(Path("accounts.csv"))
        return
    if not args.accounts:
        print("Укажите --accounts путь (CSV или TXT)")
        sys.exit(2)

    miss = missing_ops(load_ops())
    if miss:
        print(f"Отсутствуют хэши GQL для: {', '.join(miss)}")
        sys.exit(1)

    app = QApplication(sys.argv)
    win = MainWindow(Path(args.accounts))
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
