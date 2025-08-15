# src/gui.py
from __future__ import annotations
import asyncio, json
from pathlib import Path

import requests
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QTextEdit
)
from PySide6.QtCore import QTimer

# локальные модули
from .types import Account
from .accounts import load_accounts, COOKIES_DIR
from .onboarding import bulk_onboarding
from .onboarding_webview import WebOnboarding, Account as WVAccount
from .miner import run_account  # <- асинхронный воркер (aiohttp)
from .ops import load_ops, missing_ops


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

        self.tbl = QTableWidget(0, 8)
        self.tbl.setHorizontalHeaderLabels(["Label","Login","Status","Campaign","Game","Progress","Remain (min)","Last claim"])
        self.tbl.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        v.addWidget(self.tbl)

        self.log = QTextEdit(); self.log.setReadOnly(True); v.addWidget(self.log)

        self.populate(); self.refresh_totals()

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
    def populate(self):
        self.tbl.setRowCount(0)
        for a in self.accounts:
            r = self.tbl.rowCount(); self.tbl.insertRow(r)
            for i, val in enumerate([a.label, a.login, a.status, "", "", "0%", "0", ""]):
                self.tbl.setItem(r, i, QTableWidgetItem(str(val)))

    def row_of(self, login: str) -> int:
        for r in range(self.tbl.rowCount()):
            if self.tbl.item(r,1).text() == login: return r
        return -1

    def log_line(self, s: str): self.log.append(s)

    def refresh_totals(self):
        active = sum(1 for a in self.accounts if a.status=="Running")
        self.lbl.setText(f"Аккаунтов: {len(self.accounts)} • Активных: {active} • Клеймов: {self.metrics['claimed']} • Ошибок: {self.metrics['errors']}")

    def _remove_account_from_ui(self, login: str):
        r = self.row_of(login)
        if r >= 0: self.tbl.removeRow(r)
        self.accounts = [a for a in self.accounts if a.login != login]
        self.refresh_totals()

    # ── actions ────────────────────────────────────────────────────────────────
    def check_gql(self):
        """Валидируем auth-token в cookies через https://id.twitch.tv/oauth2/validate."""
        ok = exp = miss = other = 0
        for a in self.accounts:
            login = a.login
            r = self.row_of(login)
            if r < 0: continue

            cookie_file = Path(COOKIES_DIR) / f"{login}.json"
            if not cookie_file.exists():
                self.tbl.item(r,2).setText("NO COOKIES")
                self.log_line(f"[{login}] NO COOKIES — {cookie_file} not found")
                miss += 1; continue

            token = ""
            try:
                data = json.loads(cookie_file.read_text(encoding="utf-8"))
                for c in data:
                    if c.get("name") == "auth-token":
                        token = c.get("value") or ""
                        break
            except Exception as e:
                self.tbl.item(r,2).setText("BAD COOKIES")
                self.log_line(f"[{login}] BAD COOKIES — {e}")
                other += 1; continue

            if not token:
                self.tbl.item(r,2).setText("NO TOKEN")
                self.log_line(f"[{login}] NO TOKEN in cookies")
                miss += 1; continue

            try:
                resp = requests.get(
                    "https://id.twitch.tv/oauth2/validate",
                    headers={"Authorization": f"OAuth {token}"},
                    timeout=6,
                )
                if resp.status_code == 200:
                    self.tbl.item(r,2).setText("OK")
                    j = resp.json()
                    login_resp = j.get("login","?")
                    scopes = ",".join(j.get("scopes",[]))
                    self.log_line(f"[{login}] OK — login={login_resp} scopes=[{scopes}]")
                    ok += 1
                elif resp.status_code in (401, 403):
                    self.tbl.item(r,2).setText("EXPIRED")
                    self.log_line(f"[{login}] EXPIRED — token invalid")
                    exp += 1
                else:
                    self.tbl.item(r,2).setText(f"HTTP {resp.status_code}")
                    self.log_line(f"[{login}] HTTP {resp.status_code}: {resp.text[:120]}")
                    other += 1
            except Exception as e:
                self.tbl.item(r,2).setText("ERROR")
                self.log_line(f"[{login}] ERROR — {e}")
                other += 1

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
        for a in self.accounts:
            if a.login in self.tasks: continue
            stop = asyncio.Event()
            self.stops[a.login] = stop
            t = self.loop.create_task(run_account(a.login, a.proxy, self.queue, stop))
            self.tasks[a.login] = t
            a.status = "Running"
        self.refresh_totals()

    def stop_all(self):
        for s in self.stops.values():
            s.set()
        self.stops.clear()
        self.tasks.clear()
        for a in self.accounts:
            a.status = "Stopped"
        self.refresh_totals()

    # ── корутина-приёмник сообщений от miner.py ───────────────────────────────
    async def feeder(self):
        while True:
            login, kind, p = await self.queue.get()
            r = self.row_of(login)
            if r < 0:
                continue
            if kind == "status":
                self.tbl.item(r,2).setText(p.get("status",""))
                note = p.get("note")
                if note:
                    self.log_line(f"[{login}] {note}")
            elif kind == "campaign":
                self.tbl.item(r,3).setText(p.get("camp","") or "—")
                self.tbl.item(r,4).setText(p.get("game","") or "—")
            elif kind == "progress":
                self.tbl.item(r,5).setText(f"{p.get('pct',0):.0f}%")
                self.tbl.item(r,6).setText(str(p.get("remain",0)))
            elif kind == "claimed":
                self.metrics["claimed"] += 1
                self.tbl.item(r,7).setText(p.get("at",""))
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

    # ── корректное завершение приложения ─────────────────────────────────────
    def closeEvent(self, event):
        # запрашиваем остановку всех воркеров
        running_tasks = list(self.tasks.values())
        # feeder также работает в нашем loop
        running_tasks.append(self._feeder_task)
        self.stop_all()
        for t in running_tasks:
            t.cancel()
        if running_tasks:
            self.loop.run_until_complete(
                asyncio.gather(*running_tasks, return_exceptions=True)
            )
        self.loop.stop()
        self.loop.close()
        super().closeEvent(event)
