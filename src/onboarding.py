# src/onboarding.py
from __future__ import annotations
from pathlib import Path
import json, time, os, re
from typing import Iterable, Tuple, List, Dict, Optional, Callable

import pyotp
from playwright.sync_api import sync_playwright, Page, Locator

LOGIN_URL = "https://www.twitch.tv/login?no-reload=true"

# ───────── helpers ─────────

def _launch_browser(p):
    args = [
        "--disable-blink-features=AutomationControlled",
        "--disable-extensions",
        "--no-first-run",
        "--no-default-browser-check",
        "--start-maximized",
    ]
    if os.environ.get("TW_ONB_DISABLE_GPU", "0") == "1":
        args += ["--disable-gpu", "--disable-software-rasterizer"]

    for channel in ("chrome", "msedge", None):
        try:
            if channel:
                return p.chromium.launch(channel=channel, headless=False, args=args)
            else:
                return p.chromium.launch(headless=False, args=args)
        except Exception:
            continue
    return p.chromium.launch(headless=False)

def _cookies_map(context) -> Dict[str, str]:
    try:
        cookies = context.cookies()
    except Exception:
        return {}
    return {c.get("name",""): c.get("value","") for c in cookies if c.get("name")}

def _goto(page: Page, url: str) -> None:
    for _ in range(4):
        try:
            page.goto(url, wait_until="domcontentloaded")
            return
        except Exception:
            time.sleep(0.6)

def _click_if_exists(page: Page, selector: str, timeout_ms: int = 1500) -> bool:
    try:
        page.locator(selector).first.click(timeout=timeout_ms)
        return True
    except Exception:
        return False

def _dismiss_consent(page: Page) -> None:
    for sel in (
        "#onetrust-accept-btn-handler",
        "button#onetrust-accept-btn-handler",
        'button:has-text("Принять")',
        'button:has-text("Accept All")',
        'button[aria-label="Accept"]',
        'button[aria-label="Согласиться"]',
    ):
        if _click_if_exists(page, sel, 800):
            break

def _wait_visible(page: Page, css: str, timeout: int = 30000) -> Locator:
    loc = page.locator(css).first
    loc.wait_for(state="visible", timeout=timeout)
    return loc

def _fill_js(page: Page, selector: str, value: str) -> None:
    try:
        page.evaluate(
            """(sel, val) => {
                const el = document.querySelector(sel);
                if (!el) return;
                el.focus();
                try { el.value = ''; } catch(e) {}
                const proto = el.constructor && el.constructor.prototype;
                const desc = proto && Object.getOwnPropertyDescriptor(proto, 'value');
                if (desc && desc.set) { desc.set.call(el, val); } else { el.value = val; }
                el.dispatchEvent(new Event('input', {bubbles:true}));
                el.dispatchEvent(new Event('change', {bubbles:true}));
            }""",
            selector, value
        )
    except Exception:
        pass

def _fill_strong(page: Page, loc: Locator, selector_for_js: str, text: str) -> None:
    try: loc.scroll_into_view_if_needed(timeout=1500)
    except Exception: pass
    for op in ("click", "fill", "type"):
        try:
            if op == "click":
                loc.click(timeout=1200)
                loc.fill("", timeout=800)
            elif op == "fill":
                loc.fill(text, timeout=2000)
                if loc.input_value(timeout=600):
                    return
            else:
                loc.type(text, delay=20, timeout=2500)
                if loc.input_value(timeout=600):
                    return
        except Exception:
            pass
    _fill_js(page, selector_for_js, text)

def _autofill_and_submit(page: Page, login: str, password: str) -> None:
    user_css = ':is(input#login-username, input[name="login"], input[autocomplete="username"], input[type="text"])'
    pass_css = ':is(input[type="password"], input#password-input, input[name="password"], input[autocomplete="current-password"])'
    user = _wait_visible(page, user_css)
    pwd  = _wait_visible(page, pass_css)
    _fill_strong(page, user, user_css, login)
    _fill_strong(page, pwd,  pass_css, password)
    if not _click_if_exists(page, 'button[data-a-target="passport-login-button"]', 2500):
        try: page.keyboard.press("Enter")
        except Exception: pass

def _maybe_enter_totp(page: Page, totp_secret: str) -> None:
    if not totp_secret:
        return
    try:
        code = pyotp.TOTP(totp_secret).now()
        field_css = 'input[data-a-target="two-factor-input"]'
        field = _wait_visible(page, field_css, 8000)
        _fill_strong(page, field, field_css, code)
        _click_if_exists(page, 'button[data-a-target="two-factor-submit"]', 2000)
    except Exception:
        pass

def _text_any(page: Page, patterns: List[str], timeout: int = 350) -> bool:
    """Проверка наличия текста: regex через text=/.../i и get_by_text (на всякий случай)."""
    for pat in patterns:
        try:
            if page.locator(f"text=/{pat}/i").first.is_visible(timeout=timeout):
                return True
        except Exception:
            pass
        try:
            if page.get_by_text(re.compile(pat, re.I)).first.is_visible(timeout=timeout):
                return True
        except Exception:
            pass
    return False

def _email_challenge_present(page: Page) -> bool:
    if _text_any(page, [
        r"Введите код из электронной почты",
        r"Enter the code from your email",
    ]): return True
    try:
        if page.locator('input[type="text"][maxlength="1"]').count() >= 6:
            return True
    except Exception:
        pass
    try:
        if page.locator('input[autocomplete="one-time-code"]').count() >= 1:
            return True
    except Exception:
        pass
    return False

def _username_not_exist(page: Page) -> bool:
    # максимально «широкие» матчеры RU/EN
    return _text_any(page, [
        r"Такого\s+имени\s+пользователя\s+не\s+существует",
        r"Имя\s+пользователя\s+не\s+существует",
        r"(that|this)\s+username\s+does(?:n['’]t| not)\s+exist",
    ], timeout=450)

def _remove_from_accounts_file(accounts_file: Path, login: str) -> bool:
    try:
        lines = accounts_file.read_text(encoding="utf-8", errors="ignore").splitlines()
        out: List[str] = []
        removed = False
        for line in lines:
            raw = line.rstrip("\r\n")
            s = raw.strip()
            if not s:
                out.append(raw); continue
            # "login:pass" (твоя схема) — самое надёжное
            if s.startswith(f"{login}:"):
                removed = True
                continue
            # CSV-поддержка на всякий
            if f",{login}," in s or s == login:
                removed = True
                continue
            out.append(raw)
        if removed:
            accounts_file.write_text("\n".join(out) + ("\n" if out else ""), encoding="utf-8")
        return removed
    except Exception:
        return False

# ───────── public API ─────────

def login_and_save_cookies(login: str, password: str, out_path: Path, totp_secret: str = "", timeout_s: int = 180) -> dict:
    res = bulk_onboarding([(login, password, totp_secret)], out_dir=out_path.parent, timeout_s=timeout_s)
    return res[0] if res else {"result": "FAILED", "note": "unknown error"}

def bulk_onboarding(
    accounts: Iterable[Tuple[str, str, str]],
    out_dir: Path,
    timeout_s: int = 180,
    progress_cb: Optional[Callable[[Dict[str, str]], None]] = None,
    accounts_file: Optional[Path] = None,
) -> List[Dict[str, str]]:
    """
    Один браузер Playwright, один таб. Для аккаунтов:
      - OK → сохраняем cookies/<login>.json
      - EMAIL_2FA_REQUIRED → SKIP
      - USERNAME_NOT_FOUND → DELETE и удаляем из accounts.txt (если указан путь)
    """
    out_dir = Path(out_dir); out_dir.mkdir(parents=True, exist_ok=True)
    results: List[Dict[str, str]] = []

    with sync_playwright() as p:
        browser = _launch_browser(p)
        context = browser.new_context(viewport={"width": 1280, "height": 900})
        page = context.new_page(); page.bring_to_front()

        for login, password, totp in accounts:
            progress_cb and progress_cb({"login": login, "result": "STEP", "note": "Открываю форму логина"})
            try: context.clear_cookies()
            except Exception: pass

            _goto(page, LOGIN_URL)
            _dismiss_consent(page)

            last_err = ""
            for _ in range(3):
                try:
                    progress_cb and progress_cb({"login": login, "result": "STEP", "note": "Ввожу логин/пароль"})
                    _autofill_and_submit(page, login, password)
                    _maybe_enter_totp(page, totp)
                    break
                except Exception as e:
                    last_err = str(e)
                    time.sleep(0.7)
                    _goto(page, LOGIN_URL)

            saved = False
            t0 = time.time()
            while time.time() - t0 < timeout_s:
                # успех
                try:
                    page.wait_for_selector('[data-a-target="user-menu-toggle"]', timeout=900)
                except Exception:
                    pass
                kv = _cookies_map(context)
                if kv.get("auth-token") or kv.get("twilight-user"):
                    out_path = out_dir / f"{login}.json"
                    out_path.write_text(json.dumps(context.cookies(), indent=2, ensure_ascii=False), encoding="utf-8")
                    res = {"login": login, "result": "OK", "note": f"cookies → {out_path}"}
                    results.append(res); progress_cb and progress_cb(res)
                    saved = True
                    break

                # особые кейсы — чтобы не виснуть
                if _email_challenge_present(page):
                    res = {"login": login, "result": "SKIP", "note": "EMAIL_2FA_REQUIRED"}
                    results.append(res); progress_cb and progress_cb(res)
                    saved = True
                    break

                if _username_not_exist(page):
                    note = "USERNAME_NOT_FOUND"
                    removed = False
                    if accounts_file and accounts_file.exists():
                        removed = _remove_from_accounts_file(accounts_file, login)
                        if removed:
                            note += " — removed from accounts.txt"
                    res = {"login": login, "result": "DELETE", "note": note}
                    results.append(res); progress_cb and progress_cb(res)
                    saved = True
                    break

                time.sleep(0.35)

            if not saved:
                res = {"login": login, "result": "FAILED", "note": last_err or "Пер-аккаунт таймаут"}
                results.append(res); progress_cb and progress_cb(res)

        try: context.close()
        except Exception: pass
        try: browser.close()
        except Exception: pass

    return results
