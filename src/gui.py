# src/gui.py
from __future__ import annotations
import asyncio
import json
from datetime import datetime
from pathlib import Path

import aiohttp
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QTextEdit, QProgressBar,
    QLineEdit, QComboBox, QInputDialog, QMessageBox
)
from PySide6.QtCore import QTimer

# локальные модули
from .types import Account
from .accounts import load_accounts, COOKIES_DIR
from .onboarding import bulk_onboarding
from .onboarding_webview import WebOnboarding, Account as WVAccount
from .miner import run_account  # асинхронный воркер
from .ops import load_ops, missing_ops
from .campaign_dialog import CampaignSettingsDialog


class MainWindow(QMainWindow):
    def __init__(self, accounts_file: Path):
        super().__init__()
        self.setWindowTitle("Twitch Drops — API Miner (TXT/CSV)")
        self.resize(1200, 720)

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
        self.lbl = QLabel("—")
        top.addWidget(self.lbl)
        top.addStretch(1)
        self.btn_check = QPushButton("Проверить GQL")
        self.btn_check.clicked.connect(self.check_gql)
        top.addWidget(self.btn_check)
        self.btn_onb = QPushButton("Onboarding (Playwright)")
        self.btn_onb.clicked.connect(self.onboarding)
        top.addWidget(self.btn_onb)
        self.btn_onb2 = QPushButton("Onboarding (WebView)")
        self.btn_onb2.clicked.connect(self.onboarding_webview)
        top.addWidget(self.btn_onb2)
        self.btn_campaigns = QPushButton("Campaigns…")
        self.btn_campaigns.clicked.connect(self.campaign_settings)
        top.addWidget(self.btn_campaigns)
        self.btn_start = QPushButton("Start All")
        self.btn_start.clicked.connect(self.start_all)
        top.addWidget(self.btn_start)
        self.btn_stop = QPushButton("Stop All")
        self.btn_stop.clicked.connect(self.stop_all)
        top.addWidget(self.btn_stop)
        v.addLayout(top)

        # 10 колонок: последняя — Action (кнопка Start/Stop)
        self.tbl = QTableWidget(0, 10)
        self.tbl.setHorizontalHeaderLabels([
            "Label","Login","Status","Campaign","Game","Channels",
            "Progress","Remain (min)","Last claim","Action"
        ])
        self.tbl.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.tbl.cellDoubleClicked.connect(self.cell_dbl_clicked)
        v.addWidget(self.tbl)

        # фильтры и лог
        flt = QHBoxLayout()
        flt.addWidget(QLabel("Login:"))
        self.filter_login = QLineEdit()
        self.filter_login.setPlaceholderText("фильтр по логину/метке")
        self.filter_login.textChanged.connect(self.refresh_log_display)
        flt.addWidget(self.filter_login)
        flt.addWidget(QLabel("Level:"))
        self.filter_level = QComboBox()
        self.filter_level.addItems(["ALL", "INFO", "ERROR"])
        self.filter_level.currentTextChanged.connect(self.refresh_log_display)
        flt.addStretch(1)
        v.addLayout(flt)

        self.log = QTextEdit()
        self.log.setReadOnly(True)
        v.addWidget(self.log)
        self.log_entries: list[dict] = []
        self.max_log_entries = 500

        self.populate()
        self.refresh_totals()

        # ── встроенный asyncio-loop ────────────────────────────────────────────
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.queue: asyncio.Queue = asyncio.Queue()  # канал miner -> GUI
        self._feeder_task = self.loop.create_task(self.feeder())

        # таймер: даём циклу «тикать», не блокируя Qt
        self.timer = QTimer(self)
        self.timer.setInterval(50)  # ~20 FPS
        self.timer.timeout.connect(self.pump)
        self.timer.start()

    # ── helpers ────────────────────────────────────────────────────────────────
    def populate(self):
        self.tbl.setRowCount(0)
        for a in self.accounts:
            r = self.tbl.rowCount()
            self.tbl.insertRow(r)

            # Колонки: Label, Login, Status, Campaign(cmb), Game, Channels, Progress(QProgressBar), Remain, Last, Action
            values = [a.label, a.login, a.status, None, "", "—", "", "0", ""]
            for i in range(9):
                if i == 3:  # Campaign — выпадающий список
                    cmb = QComboBox()
                    cmb.currentIndexChanged.connect(
                        lambda _idx, login=a.login: self._on_campaign_changed(login)
                    )
                    self.tbl.setCellWidget(r, i, cmb)
                    self.cmb_campaigns[a.login] = cmb
                elif i == 6:  # Progress — QProgressBar
                    pb = QProgressBar()
                    pb.setRange(0, 100)
                    pb.setValue(0)
                    pb.setFormat("%p%")
                    self.tbl.setCellWidget(r, i, pb)
                else:
                    self.tbl.setItem(r, i, QTableWidgetItem(str(values[i])))

            # Action button (колонка 9)
            btn = QPushButton("Start")
            btn.clicked.connect(lambda _, l=a.login: self.start_stop_account(l))
            self.tbl.setCellWidget(r, 9, btn)

    def row_of(self, login: str) -> int:
        for r in range(self.tbl.rowCount()):
            item = self.tbl.item(r, 1)
            if item and item.text() == login:
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
        # прокинем выбор в майнер
        if cid:
            q = self.cmds.get(login)
            if q:
                q.put_nowait(("select_campaigns", [cid]))

    def _fmt_seconds(self, secs: int) -> str:
        try:
            m, s = divmod(int(secs), 60)
            h, m = divmod(m, 60)
            if h:
                return f"{h:02d}:{m:02d}:{s:02d}"
            return f"{m:02d}:{s:02d}"
        except Exception:
            return "—"

    def log_line(self, msg: str, login: str = "", level: str = "INFO"):
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_entries.append({"ts": ts, "login": login, "level": level, "msg": msg})
        if len(self.log_entries) > self.max_log_entries:
            self.log_entries = self.log_entries[-self.max_log_entries:]
        self.refresh_log_display()

    def refresh_log_display(self):
        login_f = self.filter_login.text().strip().lower()
        level_f = self.filter_level.currentText()
        lines = []
        for e in self.log_entries:
            if login_f and login_f not in e["login"].lower():
                continue
            if level_f != "ALL" and e["level"] != level_f:
                continue
            color = "red" if e["level"] == "ERROR" else "black"
            login_part = f"[{e['login']}]" if e["login"] else ""
            lines.append(
                f"<span style='color:{color}'>[{e['ts']}] [{e['level']}] {login_part} {e['msg']}</span>"
            )
        self.log.setHtml("<br>".join(lines))

    def refresh_totals(self):
        active = len(self.tasks)
        self.lbl.setText(
            f"Аккаунтов: {len(self.accounts)} • Активных: {active} • "
            f"Клеймов: {self.metrics['claimed']} • Ошибок: {self.metrics['errors']}"
        )

    def _remove_account_from_ui(self, login: str):
        self.stop_account(login)
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
        # запускаем асинхронную проверку, чтобы не блокировать GUI
        self.loop.create_task(self._check_gql_async())

    async def _check_gql_async(self):
        ok = exp = miss = other = 0

        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=6)) as session:
            async def _check_one(a: Account) -> str:
                login = a.login
                r = self.row_of(login)
                if r < 0:
                    return "other"

                cookie_file = Path(COOKIES_DIR) / f"{login}.json"
                if not cookie_file.exists():
                    await self.queue.put((login, "status", {
                        "status": "NO COOKIES",
                        "note": f"{cookie_file} not found",
                    }))
                    return "miss"

                token = ""
                try:
                    data = json.loads(cookie_file.read_text(encoding="utf-8"))
                    for c in data:
                        if c.get("name") == "auth-token":
                            token = c.get("value") or ""
                            break
                except Exception as e:
                    await self.queue.put((login, "status", {
                        "status": "BAD COOKIES",
                        "note": str(e),
                    }))
                    return "other"

                if not token:
                    await self.queue.put((login, "status", {
                        "status": "NO TOKEN",
                        "note": "NO TOKEN in cookies",
                    }))
                    return "miss"

                try:
                    async with session.get(
                        "https://id.twitch.tv/oauth2/validate",
                        headers={"Authorization": f"OAuth {token}"},
                    ) as resp:
                        if resp.status == 200:
                            j = await resp.json()
                            login_resp = j.get("login", "?")
                            scopes = ",".join(j.get("scopes", []))
                            await self.queue.put((login, "status", {
                                "status": "OK",
                                "note": f"login={login_resp} scopes=[{scopes}]",
                            }))
                            return "ok"
                        elif resp.status in (401, 403):
                            await self.queue.put((login, "status", {
                                "status": "EXPIRED",
                                "note": "token invalid",
                            }))
                            return "exp"
                        else:
                            text = (await resp.text())[:120]
                            await self.queue.put((login, "status", {
                                "status": f"HTTP {resp.status}",
                                "note": text,
                            }))
                            return "other"
                except Exception as e:
                    await self.queue.put((login, "status", {
                        "status": "ERROR",
                        "note": str(e),
                    }))
                    return "other"

            results = await asyncio.gather(*(_check_one(a) for a in self.accounts))

        for res in results:
            if res == "ok":
                ok += 1
            elif res == "exp":
                exp += 1
            elif res == "miss":
                miss += 1
            else:
                other += 1

        self.log_line(f"Итог: OK={ok} EXPIRED={exp} NO_COOKIES/NO_TOKEN={miss} OTHER={other}")

    def _on_onboarding_progress(self, res: dict):
        login = res.get("login", "?")
        result = res.get("result", "")
        note = res.get("note", "")
        if result == "STEP":
            self.log_line(note, login=login)
        else:
            self.log_line(f"{result} — {note}", login=login)
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

    def campaign_settings(self):
        """Открыть диалог выбора кампаний для текущего аккаунта."""
        r = self.tbl.currentRow()
        if r < 0:
            return
        login = self.tbl.item(r, 1).text()
        campaigns = self.available_campaigns.get(login, [])
        if not campaigns:
            self.log_line("Нет данных о кампаниях", login=login)
            return
        selected = self.selected_campaigns.get(login, [c.get("id") for c in campaigns])
        dlg = CampaignSettingsDialog(campaigns, selected, self)
        if dlg.exec():
            ids = dlg.selected()
            self.selected_campaigns[login] = ids
            q = self.cmds.get(login)
            if q:
                q.put_nowait(("select_campaigns", ids))

    # ── пер-аккаунтный запуск/остановка ────────────────────────────────────────
    def start_account(self, login: str):
        if login in self.tasks:
            return
        acc = next((a for a in self.accounts if a.login == login), None)
        if not acc:
            return
        stop = asyncio.Event()
        self.stops[login] = stop
        cmd_q: asyncio.Queue = asyncio.Queue()
        self.cmds[login] = cmd_q
        t = self.loop.create_task(run_account(login, acc.proxy, self.queue, stop, cmd_q))
        self.tasks[login] = t
        acc.status = "Running"
        r = self.row_of(login)
        if r >= 0:
            btn = self.tbl.cellWidget(r, 9)
            if isinstance(btn, QPushButton):
                btn.setText("Stop")
            self.tbl.item(r, 2).setText("Starting")
        self.refresh_totals()

    def stop_account(self, login: str):
        evt = self.stops.pop(login, None)
        if evt:
            evt.set()
        self.tasks.pop(login, None)
        self.cmds.pop(login, None)
        acc = next((a for a in self.accounts if a.login == login), None)
        if acc:
            acc.status = "Stopped"
        r = self.row_of(login)
        if r >= 0:
            btn = self.tbl.cellWidget(r, 9)
            if isinstance(btn, QPushButton):
                btn.setText("Start")
            self.tbl.item(r, 2).setText("Stopped")
        self.refresh_totals()

    def start_stop_account(self, login: str):
        if login in self.tasks:
            self.stop_account(login)
        else:
            self.start_account(login)

    def start_all(self):
        for a in self.accounts:
            self.start_account(a.login)

    def stop_all(self):
        for login in list(self.tasks.keys()):
            self.stop_account(login)

    # ── корутина-приёмник сообщений от miner.py ────────────────────────────────
    async def feeder(self):
        while True:
            login, kind, p = await self.queue.get()
            r = self.row_of(login)
            if r < 0:
                continue
            if kind == "status":
                self.tbl.item(r, 2).setText(p.get("status", ""))
                note = p.get("note")
                if note:
                    self.log_line(note, login=login)
            elif kind == "campaign":
                self.tbl.item(r, 3).setText(p.get("camp", "") or "—")
                self.tbl.item(r, 4).setText(p.get("game", "") or "—")
            elif kind == "campaigns":
                # список доступных кампаний (для диалога + выпадающий список)
                camps = p.get("campaigns", [])
                self.available_campaigns[login] = camps
                if login not in self.selected_campaigns:
                    self.selected_campaigns[login] = [c.get("id") for c in camps]
                # заполним выпадающий список в таблице
                cmb = self.cmb_campaigns.get(login)
                if cmb is not None:
                    cmb.blockSignals(True)
                    cmb.clear()
                    for c in camps:
                        cmb.addItem(c.get("name", c.get("id", "—")), c.get("id"))
                    cmb.blockSignals(False)
                self.log_line(f"Доступно кампаний: {len(camps)}", login=login)
            elif kind == "channels":
                items = p.get("channels", [])
                self.channels[login] = items
                txt = "\n".join(f"{c.get('name','')} ({c.get('viewers',0)})" for c in items) or "—"
                self.tbl.setItem(r, 5, QTableWidgetItem(txt))
            elif kind == "switch":
                chan = p.get("channel", "")
                items = self.channels.get(login, [])
                if chan:
                    items = sorted(items, key=lambda c: c.get('name') != chan)
                    self.channels[login] = items
                # исправлено: было .инjoin, должно быть .join
                txt = "\n".join(f"{c.get('name','')} ({c.get('viewers',0)})" for c in items) or "—"
                self.tbl.setItem(r, 5, QTableWidgetItem(txt))
                if chan:
                    self.log_line(f"switched to {chan}", login=login)
            elif kind == "progress":
                # поддерживаем и старый remain, и новый next (+ опциональный drop)
                pb = self.tbl.cellWidget(r, 6)
                if isinstance(pb, QProgressBar):
                    pb.setValue(int(p.get("pct", 0)))
                    drop = p.get("drop")
                    pb.setFormat(f"{drop} %p%" if drop else "%p%")
                remain_secs = p.get("remain")
                if remain_secs is None:
                    remain_secs = p.get("next", 0)
                self.tbl.setItem(r, 7, QTableWidgetItem(self._fmt_seconds(remain_secs)))
            elif kind == "claimed":
                self.metrics["claimed"] += 1
                pb = self.tbl.cellWidget(r, 6)
                if isinstance(pb, QProgressBar):
                    pb.setValue(int(p.get("pct", 0)))
                    drop = p.get("drop")
                    pb.setFormat(f"{drop} %p%" if drop else "%p%")
                remain_secs = p.get("remain")
                if remain_secs is None:
                    remain_secs = p.get("next", 0)
                self.tbl.setItem(r, 7, QTableWidgetItem(self._fmt_seconds(remain_secs)))
                self.tbl.item(r, 8).setText(p.get("at", ""))
                self.log_line(f"Claimed {p.get('drop','')}", login=login)
            elif kind == "error":
                self.metrics["errors"] += 1
                self.log_line(f"ERROR: {p.get('msg','')}", login=login, level="ERROR")
            self.refresh_totals()

    # ── короткий «тик» asyncio-цикла, чтобы задачи выполнялись ────────────────
    def pump(self):
        try:
            self.loop.run_until_complete(asyncio.sleep(0))
        except Exception:
            pass

    # ── корректное завершение приложения ──────────────────────────────────────
    def closeEvent(self, event):
        # просим остановиться все воркеры
        running_tasks = list(self.tasks.values())
        running_tasks.append(self._feeder_task)
        self.stop_all()
        for t in running_tasks:
            t.cancel()
        if running_tasks:
            try:
                self.loop.run_until_complete(
                    asyncio.gather(*running_tasks, return_exceptions=True)
                )
            except Exception:
                pass
        self.loop.stop()
        self.loop.close()
        super().closeEvent(event)
