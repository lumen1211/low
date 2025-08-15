# src/gui.py
from __future__ import annotations
import asyncio, json
from pathlib import Path

import requests
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableView, QHeaderView, QTextEdit, QLineEdit
)
from PySide6.QtCore import (
    QTimer, QAbstractTableModel, Qt, QModelIndex, QSortFilterProxyModel
)

# локальные модули
from .types import Account
from .accounts import load_accounts, COOKIES_DIR
from .onboarding import bulk_onboarding
from .onboarding_webview import WebOnboarding, Account as WVAccount
from .miner import run_account  # <- асинхронный воркер (aiohttp)
from .ops import load_ops, missing_ops


class AccountsTableModel(QAbstractTableModel):
    headers = [
        "Label",
        "Login",
        "Status",
        "Campaign",
        "Game",
        "Progress",
        "Remain (min)",
        "Last claim",
    ]

    def __init__(self, accounts: list[Account]):
        super().__init__()
        self.accounts = accounts

    # базовая структура модели -------------------------------------------------
    def rowCount(self, parent: QModelIndex | None = None):  # type: ignore[override]
        return len(self.accounts)

    def columnCount(self, parent: QModelIndex | None = None):  # type: ignore[override]
        return len(self.headers)

    def data(self, index: QModelIndex, role=Qt.DisplayRole):  # type: ignore[override]
        if not index.isValid():
            return None
        acc = self.accounts[index.row()]
        col = index.column()

        if role == Qt.DisplayRole:
            return [
                acc.label,
                acc.login,
                acc.status,
                acc.active_campaign,
                acc.game,
                f"{acc.progress_pct:.0f}%",
                str(acc.remaining_minutes),
                acc.last_claim_at or "",
            ][col]
        if role == Qt.UserRole:
            return [
                acc.label,
                acc.login,
                acc.status,
                acc.active_campaign,
                acc.game,
                acc.progress_pct,
                acc.remaining_minutes,
                acc.last_claim_at or "",
            ][col]
        return None

    def headerData(self, section: int, orientation: Qt.Orientation, role=Qt.DisplayRole):  # type: ignore[override]
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return self.headers[section]
        return super().headerData(section, orientation, role)


class AccountsFilterModel(QSortFilterProxyModel):
    def __init__(self):
        super().__init__()
        self._text = ""

    def setFilterText(self, text: str):
        self._text = text.lower()
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex):  # type: ignore[override]
        if not self._text:
            return True
        model = self.sourceModel()
        label = model.index(source_row, 0, source_parent).data(Qt.DisplayRole)
        login = model.index(source_row, 1, source_parent).data(Qt.DisplayRole)
        return (
            self._text in str(label).lower() or
            self._text in str(login).lower()
        )


class MainWindow(QMainWindow):
    def __init__(self, accounts_file: Path):
        super().__init__()
        self.setWindowTitle("Twitch Drops — API Miner (TXT/CSV)")
        self.resize(1100, 700)

        self.accounts_file = Path(accounts_file)
        self.accounts: list[Account] = load_accounts(self.accounts_file)

        # состояния/метрики
        self.tasks: dict[str, asyncio.Task] = {}
        self.stops: dict[str, asyncio.Event] = {}
        self.metrics = {"claimed": 0, "errors": 0}

        # ── UI ──────────────────────────────────────────────────────────────────
        root = QWidget(); self.setCentralWidget(root)
        v = QVBoxLayout(root)

        top = QHBoxLayout()
        self.lbl = QLabel("—"); top.addWidget(self.lbl); top.addStretch(1)
        self.btn_check = QPushButton("Проверить GQL"); self.btn_check.clicked.connect(self.check_gql); top.addWidget(self.btn_check)
        self.btn_onb = QPushButton("Onboarding (Playwright)"); self.btn_onb.clicked.connect(self.onboarding); top.addWidget(self.btn_onb)
        self.btn_onb2 = QPushButton("Onboarding (WebView)"); self.btn_onb2.clicked.connect(self.onboarding_webview); top.addWidget(self.btn_onb2)
        self.btn_start = QPushButton("Start All"); self.btn_start.clicked.connect(self.start_all); top.addWidget(self.btn_start)
        self.btn_stop = QPushButton("Stop All"); self.btn_stop.clicked.connect(self.stop_all); top.addWidget(self.btn_stop)
        v.addLayout(top)

        self.search = QLineEdit(); self.search.setPlaceholderText("Поиск по логину/метке")
        v.addWidget(self.search)

        self.model = AccountsTableModel(self.accounts)
        self.proxy = AccountsFilterModel()
        self.proxy.setSourceModel(self.model)
        self.proxy.setSortRole(Qt.UserRole)
        self.tbl = QTableView(); self.tbl.setModel(self.proxy)
        self.tbl.setSortingEnabled(True)
        self.tbl.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.search.textChanged.connect(self.proxy.setFilterText)
        v.addWidget(self.tbl)

        self.log = QTextEdit(); self.log.setReadOnly(True); v.addWidget(self.log)

        self.refresh_totals()

        # ── встроенный asyncio-loop ─────────────────────────────────────────────
        # ВАЖНО: loop живёт в главном потоке; QTimer "тикает" его, чтобы шли задачи.
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.queue: asyncio.Queue = asyncio.Queue()  # канал miner -> GUI
        # feeder читает из очереди (в этом же loop) и дергается в тиках pump()
        self._feeder_task = self.loop.create_task(self.feeder())

        # таймер вызывает короткий прогон цикла, чтобы не блокировать Qt
        self.timer = QTimer(self); self.timer.setInterval(50)  # 20 FPS малой кровью
        self.timer.timeout.connect(self.pump); self.timer.start()

    # ── helpers ────────────────────────────────────────────────────────────────
    def row_of(self, login: str) -> int:
        for i, a in enumerate(self.accounts):
            if a.login == login:
                return i
        return -1

    def log_line(self, s: str): self.log.append(s)

    def refresh_totals(self):
        active = sum(1 for a in self.accounts if a.status=="Running")
        self.lbl.setText(f"Аккаунтов: {len(self.accounts)} • Активных: {active} • Клеймов: {self.metrics['claimed']} • Ошибок: {self.metrics['errors']}")

    def _remove_account_from_ui(self, login: str):
        r = self.row_of(login)
        if r >= 0:
            self.model.beginRemoveRows(QModelIndex(), r, r)
            self.accounts.pop(r)
            self.model.endRemoveRows()
        self.refresh_totals()

    # ── actions ────────────────────────────────────────────────────────────────
    def check_gql(self):
        """Валидируем auth-token в cookies через https://id.twitch.tv/oauth2/validate."""
        ok = exp = miss = other = 0
        for i, a in enumerate(self.accounts):
            login = a.login

            cookie_file = Path(COOKIES_DIR) / f"{login}.json"
            if not cookie_file.exists():
                a.status = "NO COOKIES"
                self.model.dataChanged.emit(self.model.index(i,2), self.model.index(i,2))
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
                a.status = "BAD COOKIES"
                self.model.dataChanged.emit(self.model.index(i,2), self.model.index(i,2))
                self.log_line(f"[{login}] BAD COOKIES — {e}")
                other += 1
                continue

            if not token:
                a.status = "NO TOKEN"
                self.model.dataChanged.emit(self.model.index(i,2), self.model.index(i,2))
                self.log_line(f"[{login}] NO TOKEN in cookies")
                miss += 1
                continue

            try:
                resp = requests.get(
                    "https://id.twitch.tv/oauth2/validate",
                    headers={"Authorization": f"OAuth {token}"},
                    timeout=6,
                )
                if resp.status_code == 200:
                    a.status = "OK"
                    j = resp.json()
                    login_resp = j.get("login","?")
                    scopes = ",".join(j.get("scopes",[]))
                    self.log_line(f"[{login}] OK — login={login_resp} scopes=[{scopes}]")
                    ok += 1
                elif resp.status_code in (401, 403):
                    a.status = "EXPIRED"
                    self.log_line(f"[{login}] EXPIRED — token invalid")
                    exp += 1
                else:
                    a.status = f"HTTP {resp.status_code}"
                    self.log_line(f"[{login}] HTTP {resp.status_code}: {resp.text[:120]}")
                    other += 1
            except Exception as e:
                a.status = "ERROR"
                self.log_line(f"[{login}] ERROR — {e}")
                other += 1

            self.model.dataChanged.emit(self.model.index(i,2), self.model.index(i,2))

        self.log_line(f"Итог: OK={ok} EXPIRED={exp} NO_COOKIES/NO_TOKEN={miss} OTHER={other}")

    def _on_onboarding_progress(self, res: dict):
        login = res.get("login", "?")
        result = res.get("result", "")
        note = res.get("note", "")
        if result == "STEP":
            self.log_line(f"[{login}] {note}")
        else:
            self.log_line(f"[{login}] {result} — {note}")
        if result == "DELETE":
            self._remove_account_from_ui(login)

    def onboarding(self):
        rows = [(a.login, a.password or "", a.totp_secret or "") for a in self.accounts]
        self.log_line(f"Onboarding: запускаю, всего аккаунтов: {len(rows)}")
        bulk_onboarding(
            rows,
            out_dir=COOKIES_DIR,
            timeout_s=180,
            progress_cb=self._on_onboarding_progress,
            accounts_file=self.accounts_file,
        )

    def onboarding_webview(self):
        accs = [WVAccount(label=a.label, login=a.login, password=a.password or "") for a in self.accounts]
        dlg = WebOnboarding(cookies_dir=Path("cookies"), accounts=accs, per_acc_timeout_sec=120, parent=self)
        dlg.exec()
        self.log_line("Onboarding (WebView) завершён; cookies сохранены.")

    def start_all(self):
        # создаём/пересоздаём задачи в нашем asyncio-цикле
        for i, a in enumerate(self.accounts):
            if a.login in self.tasks:
                continue
            stop = asyncio.Event()
            self.stops[a.login] = stop
            t = self.loop.create_task(run_account(a.login, a.proxy, self.queue, stop))
            self.tasks[a.login] = t
            a.status = "Running"
            self.model.dataChanged.emit(self.model.index(i,2), self.model.index(i,2))
        self.refresh_totals()

    def stop_all(self):
        for s in self.stops.values():
            s.set()
        self.stops.clear()
        self.tasks.clear()
        for i, a in enumerate(self.accounts):
            a.status = "Stopped"
            self.model.dataChanged.emit(self.model.index(i,2), self.model.index(i,2))
        self.refresh_totals()

    # ── корутина-приёмник сообщений от miner.py ───────────────────────────────
    async def feeder(self):
        while True:
            login, kind, p = await self.queue.get()
            r = self.row_of(login)
            if r < 0:
                continue
            acc = self.accounts[r]
            if kind == "status":
                acc.status = p.get("status", "")
                note = p.get("note")
                if note:
                    self.log_line(f"[{login}] {note}")
                self.model.dataChanged.emit(self.model.index(r,2), self.model.index(r,2))
            elif kind == "campaign":
                acc.active_campaign = p.get("camp", "") or "—"
                acc.game = p.get("game", "") or "—"
                self.model.dataChanged.emit(self.model.index(r,3), self.model.index(r,4))
            elif kind == "progress":
                acc.progress_pct = p.get("pct", 0)
                acc.remaining_minutes = p.get("remain", 0)
                self.model.dataChanged.emit(self.model.index(r,5), self.model.index(r,6))
            elif kind == "claimed":
                self.metrics["claimed"] += 1
                acc.last_claim_at = p.get("at", "")
                self.model.dataChanged.emit(self.model.index(r,7), self.model.index(r,7))
                self.log_line(f"[{login}] Claimed {p.get('drop','')}")
            elif kind == "error":
                self.metrics["errors"] += 1
                self.log_line(f"[{login}] ERROR: {p.get('msg','')}")
            self.refresh_totals()

    # ── короткий «тик» asyncio-цикла, чтобы задачи выполнялись ────────────────
    def pump(self):
        try:
            # даём циклу чуть-чуть времени; не блокирует UI
            self.loop.run_until_complete(asyncio.sleep(0))
        except Exception:
            pass
