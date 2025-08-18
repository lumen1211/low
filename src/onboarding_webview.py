# src/onboarding_webview.py
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import List, Dict, Any
import json
from urllib.parse import urlparse

from PySide6.QtCore import QUrl, QTimer, QObject, Signal
from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWebEngineCore import QWebEngineProfile, QWebEngineScript, QWebEnginePage
from PySide6.QtNetwork import QNetworkProxy

LOGIN_URL = "https://www.twitch.tv/login"
UA_CHROME = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

@dataclass
class Account:
    label: str
    login: str
    password: str
    proxy: str = ""
    totp_secret: str = ""

class CookieSniffer(QObject):
    cookieAdded = Signal(dict)

    def __init__(self, profile: QWebEngineProfile):
        super().__init__()
        store = profile.cookieStore()
        store.cookieAdded.connect(self._on_added)

    def _on_added(self, cookie):
        try:
            d = {
                "name": bytes(cookie.name()).decode("utf-8", "ignore"),
                "value": bytes(cookie.value()).decode("utf-8", "ignore"),
                "domain": cookie.domain(),
                "path": cookie.path(),
                "secure": cookie.isSecure(),
                "httpOnly": cookie.isHttpOnly(),
            }
            self.cookieAdded.emit(d)
        except Exception:
            pass

class WebOnboarding(QDialog):
    def __init__(self, cookies_dir: Path, accounts: List[Account], per_acc_timeout_sec: int = 120, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Onboarding (WebView) — Twitch")
        self.resize(1200, 820)

        self.cookies_dir = Path(cookies_dir)
        self.cookies_dir.mkdir(parents=True, exist_ok=True)
        self.accounts = accounts
        self.timeout_ms = max(15000, per_acc_timeout_sec * 1000)
        self._idx = -1

        # Профиль и страница
        self.profile = QWebEngineProfile(self)
        self.profile.setHttpUserAgent(UA_CHROME)
        self.page = QWebEnginePage(self.profile, self)
        self.view = QWebEngineView(self)
        self.view.setPage(self.page)

        # Сбор всех добавленных кук
        self._seen: Dict[str, str] = {}
        self.sniff = CookieSniffer(self.profile)
        self.sniff.cookieAdded.connect(self._on_cookie_added)

        # UI
        lay = QVBoxLayout(self)
        top = QHBoxLayout()
        self.lbl = QLabel("—", self)
        top.addWidget(self.lbl)
        top.addStretch(1)
        self.btn_next = QPushButton("Пропустить/Дальше", self)
        self.btn_next.clicked.connect(self._next_force)
        top.addWidget(self.btn_next)
        lay.addLayout(top)
        lay.addWidget(self.view)

        # Таймер на аккаунт
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self._on_timeout)

        # Навигация
        self.view.loadFinished.connect(self._on_load_finished)

        self._next()

    def _apply_proxy(self, proxy: str) -> None:
        """Установка HTTP/SOCKS прокси для профиля, поддержка логина/пароля."""
        try:
            if proxy:
                u = urlparse(proxy if "://" in proxy else f"http://{proxy}")
                # Определяем тип прокси
                scheme = (u.scheme or "http").lower()
                if scheme.startswith("socks"):
                    ptype = QNetworkProxy.Socks5Proxy
                else:
                    ptype = QNetworkProxy.HttpProxy
                qp = QNetworkProxy(
                    ptype,
                    u.hostname or "",
                    u.port or 0,
                    u.username or "",
                    u.password or "",
                )
            else:
                qp = QNetworkProxy(QNetworkProxy.NoProxy)
            self.profile.setHttpProxy(qp)
        except Exception:
            pass

    # ---------- scripts ----------
    def _install_scripts(self, login: str, password: str) -> None:
        # Полностью очищаем коллекцию и вставляем два скрипта (scaffold + autofill)
        coll = self.page.scripts()
        try:
            coll.clear()
        except Exception:
            pass

        scaffold = QWebEngineScript()
        scaffold.setName("tw_scaffold")
        scaffold.setInjectionPoint(QWebEngineScript.DocumentReady)
        scaffold.setWorldId(QWebEngineScript.MainWorld)
        scaffold.setRunsOnSubFrames(True)
        scaffold.setSourceCode(r"""
(function(){
  if (window.__tw_autofill_scaffold__) return;
  window.__tw_autofill_scaffold__ = true;
})();
""")
        coll.insert(scaffold)

        js = r"""
(function(){
  if (window.__tw_fill_installed__) return;
  window.__tw_fill_installed__ = true;

  const loginValue = %LOGIN%;
  const passValue  = %PASS%;

  function pick(root){
    const u = root.querySelector('input[name="login"]') ||
              root.querySelector('input#login-username') ||
              root.querySelector('input[autocomplete="username"]') ||
              root.querySelector('input[type="text"]');
    const p = root.querySelector('input[name="password"]') ||
              root.querySelector('input#password-input') ||
              root.querySelector('input[autocomplete="current-password"]') ||
              root.querySelector('input[type="password"]');
    const btn = root.querySelector('[data-a-target="passport-login-button"]') ||
                root.querySelector('[data-a-target="login-button"]') ||
                root.querySelector('button[type="submit"]') ||
                root.querySelector('button');
    return {u,p,btn};
  }

  function fire(el){ el.dispatchEvent(new Event('input',{bubbles:true})); el.dispatchEvent(new Event('change',{bubbles:true})); }

  function fillOnce(){
    try{
      const {u,p,btn} = pick(document);
      if(!u||!p||!btn) return false;
      if(!u.value){ u.focus(); u.value = loginValue; fire(u); }
      if(!p.value){ p.focus(); p.value = passValue;  fire(p); }
      setTimeout(()=>btn.click(), 200);
      return true;
    }catch(e){ return false; }
  }

  let tries = 0;
  const iv = setInterval(()=>{
    if(fillOnce()){ clearInterval(iv); return; }
    tries += 1;
    if(tries > 40) clearInterval(iv);
  }, 200);
})();
"""
        js = js.replace("%LOGIN%", json.dumps(login)).replace("%PASS%", json.dumps(password))
        fill = QWebEngineScript()
        fill.setName("tw_fill")
        fill.setInjectionPoint(QWebEngineScript.DocumentReady)
        fill.setWorldId(QWebEngineScript.MainWorld)
        fill.setRunsOnSubFrames(True)
        fill.setSourceCode(js)
        coll.insert(fill)

    # ---------- flow ----------
    def _on_cookie_added(self, c: Dict[str, Any]) -> None:
        name = c.get("name") or ""
        val = c.get("value") or ""
        if name:
            self._seen[name] = val

    def _next_force(self) -> None:
        self.timer.stop()
        self._save_and_next()

    def _next(self) -> None:
        self._idx += 1
        if self._idx >= len(self.accounts):
            self.accept()
            return

        acc = self.accounts[self._idx]
        self.lbl.setText(f"{self._idx+1}/{len(self.accounts)} — {acc.label} ({acc.login})")
        self._seen.clear()
        self.profile.cookieStore().deleteAllCookies()
        try:
            self.profile.clearHttpCache()
        except Exception:
            pass

        self._apply_proxy(acc.proxy)
        self._install_scripts(acc.login, acc.password)
        self.timer.start(self.timeout_ms)
        self.view.setUrl(QUrl(LOGIN_URL))

    def _on_load_finished(self, ok: bool) -> None:
        # Подождём, потом проверим, появились ли нужные куки.
        QTimer.singleShot(1500, self._maybe_save)

    def _maybe_save(self) -> None:
        if "auth-token" in self._seen or "twilight-user" in self._seen:
            self._save_and_next()
        else:
            if self.timer.isActive():
                QTimer.singleShot(800, self._maybe_save)

    def _save_and_next(self) -> None:
        acc = self.accounts[self._idx]
        cookies = [
            {"name": k, "value": v, "domain": ".twitch.tv", "path": "/", "secure": True, "httpOnly": False}
            for k, v in self._seen.items() if k
        ]
        p = self.cookies_dir / f"{acc.login}.json"
        p.write_text(json.dumps(cookies, ensure_ascii=False, indent=2), encoding="utf-8")
        self.lbl.setText(f"{acc.label}: cookies → {p.name}")
        self.timer.stop()
        QTimer.singleShot(250, self._next)

    def _on_timeout(self) -> None:
        self._save_and_next()

    def closeEvent(self, e) -> None:
        try:
            self.view.deleteLater()
        except Exception:
            pass
        try:
            self.page.deleteLater()
        except Exception:
            pass
        super().closeEvent(e)
