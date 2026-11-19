"""Automatic incremental sync checks around the existing SyncRunner services.

The watcher does not implement a second sync/engine. It triggers the same
SyncRunner.start_sync used by the manual button; the runner's mutex serialises manual,
login and automatic actions. The existing cursor decides whether the next run is a
bootstrap continuation or a fresh incremental top-of-list check.

The historical "paused / needs_login" run is only skipped while the saved account is
older than that run. A successful re-login refreshes the account artifact, so auto sync
retries instead of staying disabled by a stale failure record.

No model or analysis call is made here.
"""
from __future__ import annotations

import datetime
import os
import threading
import time
from pathlib import Path
from typing import Callable
from . import safety


def file_mtime(path) -> float | None:
    try:
        return Path(path).stat().st_mtime
    except OSError:
        return None


def newest_mtime(root) -> float | None:
    """Newest mtime under a Chrome profile (cookies live in nested files)."""
    root = Path(root)
    if not root.exists():
        return None
    newest = None
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            try:
                mtime = os.stat(os.path.join(dirpath, name)).st_mtime
            except OSError:
                continue
            if newest is None or mtime > newest:
                newest = mtime
    return newest


def _run_finished_ts(run: dict) -> float | None:
    value = run.get("finished_at")
    if not value:
        return None
    try:
        return datetime.datetime.strptime(str(value), "%Y-%m-%d %H:%M:%S").timestamp()
    except ValueError:
        return None


class AutoSync:
    def __init__(self, name: str, trigger: Callable[[], bool], state_provider: Callable[[], dict],
                 *, interval_s: float = 180.0, startup_delay_s: float = 15.0,
                 enabled: bool = True, sleep: Callable[[float], None] = time.sleep,
                 account_mtime: Callable[[], float | None] | None = None):
        self.name = name
        self.trigger = trigger
        self.state_provider = state_provider
        self.interval_s = interval_s
        self.startup_delay_s = startup_delay_s
        self.enabled = enabled
        self.sleep = sleep
        self.account_mtime = account_mtime
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.last_trigger_at: float | None = None
        self.next_check_at: float | None = None
        self.last_error: str | None = None
        self.needs_login = False

    def start(self) -> bool:
        if not self.enabled:
            return False
        if self._thread is not None and self._thread.is_alive():
            return False
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name=f"auto-sync-{self.name}", daemon=True)
        self._thread.start()
        return True

    def set_enabled(self, enabled: bool) -> None:
        self.enabled = bool(enabled)
        if self.enabled:
            self.start()
        else:
            self.stop()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3)
            self._thread = None

    def _account_refreshed_since(self, run: dict) -> bool:
        """True when the local account artifact was written after the failed run."""
        if self.account_mtime is None:
            return False
        current = self.account_mtime()
        if current is None:
            return False
        finished = _run_finished_ts(run)
        return finished is None or current > finished

    def _loop(self) -> None:
        delay = self.startup_delay_s
        while not self._stop.wait(delay):
            try:
                if self.name == 'xhs' and safety.state()['auto_blocked']:
                    self.last_error = safety.MESSAGE
                    delay = self.interval_s
                    continue
                state = self.state_provider() or {}
                if state.get("busy"):
                    delay = 10.0
                    continue
                run = state.get("last_run") or {}
                if run.get('status')=='paused' and run.get('pause_reason') in {'blocked','captcha','verification','rate_limited'}:
                    self.last_error='等待用户手动验证，不自动重试'
                    delay=self.interval_s
                    continue
                if (run.get("status") == "paused" and run.get("pause_reason") == "needs_login"
                        and not self._account_refreshed_since(run)):
                    self.needs_login = True
                    delay = self.interval_s
                    continue
                self.needs_login = False
                if not state.get("account_known"):
                    delay = self.interval_s
                    continue
                started = self.trigger()
                self.last_trigger_at = time.time()
                if not started:
                    delay = 10.0
                else:
                    delay = self.interval_s
            except Exception as exc:
                self.last_error = f"{type(exc).__name__}: {exc}"
                delay = self.interval_s
            finally:
                self.next_check_at = time.time() + delay
        self.next_check_at = None

    def status(self) -> dict:
        return {
            "auto": {
                "enabled": self.enabled,
                "running": bool(self._thread and self._thread.is_alive()),
                "next_check_at": self.next_check_at,
                "last_trigger_at": self.last_trigger_at,
                "needs_login": self.needs_login,
                "last_error": self.last_error,
            }
        }
