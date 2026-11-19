"""Runs the xhs sync (or the QR login) in one background thread for the API, plus a CLI.

The reader only reads the platform; engine/store write the library. One action at a time:
sync and login share the same browser profile.

CLI (from backend/):  python -m app.sync.runner login [--uid <24 hex>]
                      python -m app.sync.runner sync [--max-pages N]
                      python -m app.sync.runner status
"""
import argparse
import json
import logging
import threading
import time
from typing import Callable

from .. import config, db
from . import engine, store, safety
from .readers import xhs

log = logging.getLogger(__name__)
PROFILE_SUBDIR = "xiaohongshu"


def profile_dir():
    return config.sync_profile_dir(PROFILE_SUBDIR)


def make_reader() -> xhs.XhsReader:
    return xhs.XhsReader(
        xhs.XhsBrowser(profile_dir(), visible=False, log=log.info),
        profile_dir(), config.sync_staging_dir(xhs.PLATFORM),
        is_complete=lambda key: store.is_complete(xhs.PLATFORM, key),
    )


def run_sync(reader_factory: Callable[[], xhs.XhsReader] = make_reader,
             max_pages: int | None = None, on_start: Callable[[int], None] | None = None,
             sleep: Callable[[float], None] = time.sleep) -> dict:
    """One sync. Bootstrap (until it completed once) is limited to SYNC_MAX_PAGES per run;
    incremental is not page-limited (only by the reader's note cap and the list end) so
    it can reach earlier failures deep in the list. max_pages overrides both (CLI tests)."""
    saved = store.load_cursor(xhs.PLATFORM, xhs.COLLECTION_KEY)
    if saved and saved["bootstrap_complete"]:
        mode, pages = "incremental", max_pages
    else:
        mode, pages = "bootstrap", max_pages or config.sync_max_pages()
    if safety.state()['blocked']:
        rid=store.start_run('xhs','fav',mode)
        store.finish_run(rid,'paused',pause_reason='blocked',error=safety.MESSAGE)
        return {**store.get_run(rid),'images_new':0}
    base_image_id = store.max_image_id()
    if on_start:
        on_start(base_image_id)
    try:
        with reader_factory() as reader:
            run = engine.run(reader, xhs.COLLECTION_KEY, mode, max_pages=pages, sleep=sleep)
            if reader.restarted:
                store.note_run(run["id"], "断点笔记已不在收藏列表，本次从头扫描（非断点续跑）")
    except Exception as exc:  # browser failed to start, profile locked, ...
        msg = xhs.clean(f"{type(exc).__name__}: {exc}")
        if not store.fail_open_runs(xhs.PLATFORM, msg):
            run_id = store.start_run(xhs.PLATFORM, xhs.COLLECTION_KEY, mode)
            store.finish_run(run_id, "failed", error=f"browser: {msg}")
    run = store.latest_run(xhs.PLATFORM)
    return {**run, "images_new": store.images_created_since(xhs.PLATFORM, base_image_id)}


LOGIN_TIMEOUT_S = 480
LOGIN_POLL_S = 2.0
WINDOW_CLOSED = "登录窗口已关闭"


def _diag(line: str) -> None:
    """Login diagnostics in data/sync_logs/xhs_login.log (flags and id prefixes only)."""
    path = config.data_dir() / "sync_logs" / "xhs_login.log"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {line}\n")
    except OSError:
        pass


def login_browser() -> xhs.XhsBrowser:
    return xhs.XhsBrowser(profile_dir(), visible=True, log=log.info)


def run_login(browser_factory: Callable[[], xhs.XhsBrowser] = login_browser,
              uid: str | None = None, timeout_s: float = LOGIN_TIMEOUT_S,
              poll_s: float = LOGIN_POLL_S, clock: Callable[[], float] = time.monotonic) -> dict:
    """Visible Chrome on the formal profile, kept open while the user scans the QR code.
    "Not logged in yet" is the normal state here, never a reason to stop. Ends only on:
    login detected (account.json written), timeout, the user closing the window, or a
    fatal error. The browser is closed only after one of those."""
    if safety.state()["blocked"]:
        return {"ok":False,"message":safety.MESSAGE}
    browser = browser_factory()
    try:
        browser.open()
    except Exception as exc:
        return {"ok": False, "message": xhs.clean(f"无法打开登录窗口：{type(exc).__name__}: {exc}")}
    errors = 0
    verified: dict[str, float] = {}       # candidate -> last verification time
    _diag("login start")
    try:
        try:
            browser.show_login()
        except Exception:
            if browser.is_closed():
                return {"ok": False, "message": WINDOW_CLOSED}
        deadline = clock() + timeout_s
        while True:
            if browser.is_closed():
                return {"ok": False, "message": WINDOW_CLOSED}
            try:
                probe = browser.login_probe()
                cands = [c for c in xhs.login_candidates(probe) if uid is None or c == uid]
                _diag(f"poll {xhs.describe_probe(probe)} candidates={[c[:8] for c in cands]}")
                for cand in cands:
                    # A candidate is re-verified at most every 30 s (navigating the window
                    # away and back on every poll would disturb a QR scan in progress).
                    last = verified.get(cand)
                    if last is not None and clock() - last < 30:
                        continue
                    verified[cand] = clock()
                    result = browser.verify_account(cand)
                    _diag(f"verify {cand[:8]} -> {result}")
                    if result["ok"]:
                        xhs.write_account(profile_dir(), cand)
                        return {"ok": True, "message": "登录成功"}
                errors = 0
            except Exception as exc:
                if browser.is_closed() or "closed" in str(exc).lower():
                    return {"ok": False, "message": WINDOW_CLOSED}
                errors += 1  # e.g. evaluate during a navigation: transient
                _diag(f"error {xhs.clean(exc, 160)}")
                if errors >= 5:
                    return {"ok": False, "message": xhs.clean(f"登录窗口异常：{type(exc).__name__}: {exc}")}
            if clock() >= deadline:
                return {"ok": False, "message": f"登录超时（{int(timeout_s // 60)} 分钟内未检测到扫码登录）"}
            try:
                browser.pause(poll_s)
            except Exception:
                if browser.is_closed():
                    return {"ok": False, "message": WINDOW_CLOSED}
    finally:
        try:
            browser.close()
        except Exception:
            pass


class SyncRunner:
    def __init__(self, reader_factory: Callable[[], xhs.XhsReader] = make_reader,
                 browser_factory: Callable[[], xhs.XhsBrowser] = login_browser):
        self._reader_factory = reader_factory
        self._browser_factory = browser_factory
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self.action: str | None = None        # 'sync' / 'login' while busy
        self.message: str | None = None       # last login result / crash
        self._base_image_id: int | None = None
        self._images_new: dict[int, int] = {}  # run id -> new images (this process only)

    @property
    def busy(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _start(self, action: str, target) -> bool:
        with self._lock:
            if self.busy:
                return False
            self.action, self.message = action, None
            self._thread = threading.Thread(target=self._wrap(target), name=f"sync-{action}", daemon=True)
            self._thread.start()
            return True

    def _wrap(self, target):
        def go():
            try:
                target()
            except Exception as exc:
                self.message = xhs.clean(f"{type(exc).__name__}: {exc}")
                log.error("sync %s: %s", self.action, self.message)
            finally:
                self.action = None
        return go

    def start_sync(self) -> bool:
        if safety.state()["blocked"]: return False
        def target():
            run = run_sync(self._reader_factory, on_start=self._set_base)
            self._images_new[run["id"]] = run["images_new"]
            self._base_image_id = None
        return self._start("sync", target)

    def _set_base(self, base: int) -> None:
        self._base_image_id = base

    def start_login(self) -> bool:
        if safety.state()["blocked"]: return False
        def target():
            self.message = run_login(self._browser_factory)["message"]
        return self._start("login", target)

    def join(self, timeout: float | None = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    def state(self) -> dict:
        run = store.latest_run(xhs.PLATFORM)
        images_new = None
        if run:
            if self.action == "sync" and run["status"] == "running" and self._base_image_id is not None:
                images_new = store.images_created_since(xhs.PLATFORM, self._base_image_id)
            else:
                images_new = self._images_new.get(run["id"])
        return {
            "cooldown": safety.state(),
            "busy": self.busy,
            "action": self.action,
            "message": self.message,
            "account_known": bool(xhs.read_account(profile_dir())),
            "last_run": {**run, "images_new": images_new} if run else None,
            "failures": store.run_failures(run["id"]) if run else [],
        }


def _cli() -> None:
    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(message)s", datefmt="%H:%M:%S")
    parser = argparse.ArgumentParser(description="xhs favorites sync (formal profile under data/sync_profiles/xiaohongshu)")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_login = sub.add_parser("login", help="open a visible Chrome and wait for the QR scan")
    p_login.add_argument("--uid", default=None, help="expected own 24-hex user id; login must still be detected in the browser")
    p_sync = sub.add_parser("sync")
    p_sync.add_argument("--max-pages", type=int, default=None)
    sub.add_parser("status")
    args = parser.parse_args()
    db.init_db()
    if args.cmd == "login":
        out = run_login(uid=args.uid)
    elif args.cmd == "sync":
        out = run_sync(max_pages=args.max_pages)
        out["failures"] = store.run_failures(out["id"], limit=50)
    else:
        out = SyncRunner().state()
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    _cli()
