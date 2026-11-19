"""Huaban sync/login service for the HTTP API.

Reader/runner are reused from the existing Huaban branch (MIT-compatible internal
implementation); this module only adds the same one-action-at-a-time thread wrapper
as XHS, plus a visible-login wait. No model calls and no automatic analysis.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable

from .. import config, db
from . import engine, store
from .huaban_runner import run_boards
from .readers import huaban

log = logging.getLogger(__name__)
PROFILE_SUBDIR = "huaban"


def profile_dir():
    return config.sync_profile_dir(PROFILE_SUBDIR)


def make_reader() -> huaban.HuabanReader:
    return huaban.HuabanReader(
        huaban.HuabanBrowser(profile_dir(), visible=False, log=log.info),
        config.sync_staging_dir(huaban.PLATFORM),
        is_complete=lambda key: store.is_complete(huaban.PLATFORM, key),
    )


def login_browser() -> huaban.HuabanBrowser:
    return huaban.HuabanBrowser(profile_dir(), visible=True, log=log.info)


def run_sync(reader_factory: Callable = make_reader, max_boards: int | None = None) -> dict:
    try:
        with reader_factory() as reader:
            report = run_boards(reader, mode="incremental", max_boards=max_boards)
        latest = store.latest_run(huaban.PLATFORM)
        return {**(latest or {}), "boards": [r.__dict__ for r in report.boards],
                "totals": report.totals}
    except Exception as exc:
        # Rows already committed by engine/store stay; never delete on failure.
        msg = huaban.clean(f"{type(exc).__name__}: {exc}")
        rid = store.start_run(huaban.PLATFORM, "boards", "incremental")
        kind = getattr(exc, 'kind', '')
        if kind in engine.PAUSE_KINDS:
            store.finish_run(rid, 'paused', pause_reason=kind)
        else:
            store.finish_run(rid, 'failed', error=msg)
        latest = store.get_run(rid)
        return {**latest, "boards": [], "totals": {}}


LOGIN_TIMEOUT_S = 480
LOGIN_POLL_S = 2.0


def run_login(browser_factory: Callable = login_browser, timeout_s: float = LOGIN_TIMEOUT_S,
              poll_s: float = LOGIN_POLL_S, sleep: Callable[[float], None] = time.sleep) -> dict:
    browser = browser_factory()
    try:
        browser.open()
    except Exception as exc:
        return {"ok": False, "message": huaban.clean(f"无法打开登录窗口：{type(exc).__name__}: {exc}")}
    blocked = ("blocked", "rate_limited")
    last_state = None
    try:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if browser.is_closed():
                return {"ok": False, "message": "登录窗口已关闭"}
            try:
                state = browser.check_login()
            except engine.ReaderError as exc:
                if exc.kind not in blocked:
                    return {"ok": False, "message": huaban.clean(f"登录检查失败：{exc.kind}: {exc}")}
                state = {"state": exc.kind}
            except Exception as exc:
                return {"ok": False, "message": huaban.clean(f"登录检查失败：{type(exc).__name__}: {exc}")}
            current = state.get("state")
            if current == "ok":
                return {"ok": True, "message": "花瓣登录成功"}
            if current in blocked:
                # Keep the visible window open: a human may need to pass a captcha or a
                # temporary access block. Do not close the only window on the first poll.
                last_state = current
                log.info("huaban login blocked (%s); window stays open", current)
            else:
                last_state = current
            sleep(poll_s)
        detail = f"（最后状态：{last_state}）" if last_state else "（未检测到登录）"
        return {"ok": False, "message": f"登录超时（8 分钟）{detail}"}
    finally:
        try:
            browser.close()
        except Exception:
            pass


def verify_and_sync(confirmed, browser_factory=login_browser, timeout_s=480):
    browser=browser_factory()
    try:
        browser.open()
        browser.page.goto(huaban.FOLLOW_URL,wait_until='domcontentloaded',timeout=60000)
        deadline=time.monotonic()+timeout_s
        while time.monotonic()<deadline and not browser.is_closed():
            if not confirmed.is_set():
                browser.page.wait_for_timeout(250)
                continue
            confirmed.clear()
            try:auth=browser.check_login()
            except engine.ReaderError:continue  # window remains for the user, no automatic retry
            if auth.get('state')!='ok':continue
            reader=huaban.HuabanReader(browser,config.sync_staging_dir('huaban'),
                is_complete=lambda key:store.is_complete('huaban',key))
            reader.urlname=auth['urlname']
            report=run_boards(reader,mode='incremental')
            return {'ok':True,'message':'验证完成，已继续同步','totals':report.totals}
        return {'ok':False,'message':'验证窗口已关闭或等待超时，未继续同步'}
    finally:browser.close()


class HuabanSyncRunner:
    def __init__(self, reader_factory: Callable = make_reader,
                 browser_factory: Callable = login_browser):
        self._reader_factory = reader_factory
        self._browser_factory = browser_factory
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self.action: str | None = None
        self.message: str | None = None
        self._verified=threading.Event()

    @property
    def busy(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _start(self, action: str, target) -> bool:
        with self._lock:
            if self.busy:
                return False
            self.action, self.message = action, None
            self._thread = threading.Thread(target=self._wrap(target), name=f"huaban-{action}", daemon=True)
            self._thread.start()
            return True

    def _wrap(self, target):
        def go():
            try:
                target()
            except Exception as exc:
                self.message = huaban.clean(f"{type(exc).__name__}: {exc}")
                log.error("huaban %s: %s", self.action, self.message)
            finally:
                self.action = None
        return go

    def start_sync(self, max_boards: int | None = None) -> bool:
        def target():
            run = run_sync(self._reader_factory, max_boards=max_boards)
            self.message = run.get("error")
        return self._start("sync", target)

    def start_login(self) -> bool:
        def target():
            self.message = run_login(self._browser_factory)["message"]
        return self._start("login", target)

    def start_verification(self):
        def target():
            self.message=verify_and_sync(self._verified,self._browser_factory)['message']
        self._verified.clear()
        return self._start('verify',target)

    def confirm_verification(self):
        if self.action!='verify' or not self.busy:return False
        self._verified.set();return True

    def join(self, timeout: float | None = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    def state(self) -> dict:
        run = store.latest_run(huaban.PLATFORM)
        return {
            "busy": self.busy,
            "action": self.action,
            "message": self.message,
            "account_known": profile_dir().exists() and any(profile_dir().iterdir()),
            "last_run": run,
            "failures": store.run_failures(run["id"]) if run else [],
        }
