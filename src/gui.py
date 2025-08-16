# src/gui.py
from __future__ import annotations
import asyncio, json
from pathlib import Path

import requests
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QTextEdit,
    QInputDialog, QMessageBox, QComboBox
)
from PySide6.QtCore import QTimer

# локальные модули
from .types import Account
from .accounts import load_accounts, COOKIES_DIR
from .onboarding import bulk_onboarding
from .onboarding_webview import WebOnboarding, Account as WVAccount
from .miner import run_account  # <- асинхронный воркер
from .ops import load_ops, missing_ops
from .campaign_dialog import CampaignSettingsDialog


class MainWindow(QMainWindow):
    def __init__(self, accounts_file: Path):
        super().__init__()
        self.setWindowTitle("Twitch Drops — API Miner (TXT/CSV)")
        self.resize(1100, 700)

        self.accounts_file = Path(accounts_file)
        self.accounts: list[Account] = load_accounts(self.accounts_file)

        # проверяем наличие PQ-хэшей
        try:
            ops = load_ops()
            miss = missing_ops(ops)
            if miss:
                QMessageBox.warning(
                    self,
                    "Missing PQ hashes",
                    "\n".join(["Отсутствуют PQ-хэши для операций:", *miss]),
                )
        except Exception as e:
            QMessageBox.warning(self, "OPS load error", f"Не удалось загрузить ops.json: {e}")

        # состояния/метрики
        self.tasks: dict[str, asyncio.Task] = {}
        self.stops: dict[str, asyncio.Event] = {}
        self.cmds: dict[str, asyncio.Queue] = {}              # команды в miner
        self.channels: dict[str, list[dict]] = {}             # каналы по аккаунту (для dbl-click switch)
        self.available_campaigns: dict[str, list] = {}        # доступные кампании с воркера
        self.selected_campaigns: dict[str, list] = {}         # выбранные кампании пользователем
        self.metrics = {"claimed": 0, "errors": 0}

        # выпадающие списки кампаний по логину (в таблице)
        self.cmb_campaigns: dict[str, QComboBox] = {}

        # ── UI ──────────────────────────────────────────────────────────────────
        root = QWidget()
        self.setCentralWidget(root)
        v = QVBoxLayout(root)

        top = QHBoxLayout()
        self.lbl = QLabel("—"); top.addWidget(self.lbl); top.addStretch(1)
        self.btn_check = QPushButton("Проверить GQL"); self.btn_check.clicked.connect(self.check_gql); top.addWidget(self.btn_check)
        self.btn_onb = QPushButton("Onboarding (Playwright)"); self.btn_onb.clicked.connect(self.onboarding); top.addWidget(self.btn_onb)
        self.btn_onb2 = QPushButton("Onboarding (WebView)"); self.btn_onb2.clicked.connect(self.onboarding_webview); top.addWidget(self.btn_onb2)
        self.btn_campaigns = QPushButton("Campaigns…"); self.btn_campaigns.clicked.connect(self.campaign_settings); top.addWidget(self.btn_campaigns)
        self.btn_start = QPushButton("Start All"); self.btn_start.clicked.connect(self.start_all); top.addWidget(self.btn_start)
        self.btn_stop = QPushButton("Stop All"); self.btn_stop.clicked.connect(self.stop_all); top.addWidget(self.btn_stop)
        v.addLayout(top)

        self.tbl = QTableWidget(0, 9)
        self.tbl.setHorizontalHeaderLabels(
            ["Label","Login","Status","Campaign","Game","Channels","Progress","Remain (min)","Last claim"]
        )
        self.tbl.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.tbl.cellDoubleClicked.connect(self.cell_dbl_clicked)
        v.addWidget(self.tbl)

        self.log = QTextEdit()
        self.log.setReadOnly(True)
        v.addWidget(self.log)

        self.populate()
        self.refresh_totals()

        # ── встроенный asyncio-loop ─────────────────────────────────────────────
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.queue: asyncio.Queue = asyncio.Queue()  # канал miner -> GUI
        self._feeder_task = self.loop.create_task(self.feeder())

        # таймер: даём циклу «тикать», не блокируя Qt
        self.timer = QTimer(self); self.timer.setInterval(50)  # ~20 FPS
        self.timer.timeout.connect(self.pump); self.timer.start()

    # ── helpers ────────────────────────────────────────────────────────────────
    def populate(self):
        self.tbl.setRowCount(0)
        for a in self.accounts:
            r = self.tbl.rowCount()
            self.tbl.insertRow(r)

            # Колонки: Label, Login, Status, Campaign(cmb), Game, Channels, Progress, Remain, Last claim
            values = [a.label, a.login, a.status, None, "", "—", "0%", "0", ""]
            for i in range(9):
                if i == 3:  # Campaign — выпадающий список
                    cmb = QComboBox()
                    cmb.currentIndexChanged.connect(
                        lambda _idx, login=a.login: self._on_campaign_changed(login)
                    )
                    self.tbl.setCellWidget(r, i, cmb)
                    self.cmb_campaigns[a.login] = cmb
                else:
                    self.tbl.setItem(r, i, QTableWidgetItem(str(values[i])))

    def row_of(self, login: str) -> int:
        for r in range(self.tbl.rowCount()):
            if self.tbl.item(r, 1).text() == login:
                return r
        return -1

    def _on_campaign_changed(self, login: str):
        cmb = self.cmb_campaigns.get(login)
        if not cmb:
            return
        idx = cmb.currentIndex()
        cid = cmb.itemData(idx)
        name = cmb.currentText()
        for a in self.accounts:
            if a.login == login:
                a.campaign_id = cid or ""
                a.active_campaign = name or ""
                break

    def log_line(self, s: str): self.log.append(s)

    def refresh_totals(self):
        active = sum(1 for a in self.accounts if a.status == "Running")
        self.lbl.setText(
            f"Аккаунтов: {len(self.accounts)} • Активных: {active} • "
            f"Клеймов: {self.metrics['claimed']} • Ошибок: {self.metrics['errors']}"
        )

    def _remove_account_from_ui(self, login: str):
        r = self.row_of(login)
        if r >= 0:
            self.tbl.removeRow(r)
        self.accounts = [a for a in self.accounts if a.login != login]
        self.cmb_campaigns.pop(login, None)
        self.refresh_totals()

    def cell_dbl_clicked(self, row: int, col: int):
        # двойной клик по колонке Channels — ручное переключение
        if col != 5:
            return
        login = self.tbl.item(row, 1).text()
        items = self.channels.get(login, [])
        if not items:
            return
        labels = [f"{c.get('name','')} ({c.get('viewers',0)})" for c in items]
        names = [c.get('name','') for c in items]
        sel, ok = QInputDialog.getItem(self, "Switch Channel", "Select channel:", labels, 0, False)
        if ok and sel in labels:
            idx = labels.index(sel)
            chan = names[idx]
            cmd_q = self.cmds.get(login)
            if cmd_q:
                cmd_q.put_nowait(("switch", chan))

    # ── actions ────────────────────────────────────────────────────────────────
    def check_gql(self):
        """Валидируем auth-token в cookies через https://id.twitch.tv/oauth2/validate."""
        ok = exp = miss = other = 0
        for a in self.accounts:
            login = a.login
            r = self.row_of(login)
            if r < 0:
                continue

            cookie_file = Path(COOKIES_DIR) / f"{login}.json"
            if not cookie_file.exists():
                self.tbl.item(r, 2).setText("NO COOKIES")
                self.log_line(f"[{login}] NO COOKIES — {cookie_file} not found")
                miss += 1
                continue

            token = ""
            try:
                data = json.loads(cookie_file.read_text(encoding="utf-8"))
                for c in data:
                    if c.get("name") == "auth-token":
                        token = c.get("value") or ""
                        break
            except Exception as e:
                self.tbl.item(r, 2).setText("BAD COOKIES")
                self.log_line(f"[{login}] BAD COOKIES — {e}")

