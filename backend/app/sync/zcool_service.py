"""ZCOOL product adapter: Python starts the existing collect.mjs Node process.

No bypass logic is added here. The Node script owns its own headed Chrome profile and
handles captcha/login normally; this adapter only supplies arguments, timeout, exit
checking, and converts its works/singles JSON into sync-reader/1 items for engine/store.

Two collection modes are supported end to end:
- head (default): only each folder's newest SSR page; the cheap "anything new?" check
  the current auto/manual sync already used.
- backfill: walk every folder page (resumable through the Node out/state.json) so
  favorites past item 30 are reachable, and ingest single-image favorites (folder type
  29, out/data/singles/<id>.json) alongside works.

The reader exposes account_key (from the Node state.json account.idStr/id) so the
account-scoped sync engine/store keys cursors/runs/failures per ZCOOL account.
"""
from __future__ import annotations

import inspect
import json
import logging
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable

from .. import config
from . import engine, store

log = logging.getLogger(__name__)
PLATFORM = "zcool"
COLLECTION = "fav"
NODE = "node"
TIMEOUT_S = 1800
MODES = ("head", "backfill")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def vendor_dir() -> Path:
    return config.PROJECT_ROOT / "vendor" / "zcool"


def out_dir() -> Path:
    return vendor_dir() / "out"


def profile_dir() -> Path:
    return vendor_dir() / "profile"


def _run_node(args: list[str], timeout: int) -> subprocess.CompletedProcess:
    return subprocess.run([NODE, *args], cwd=str(vendor_dir()), capture_output=True,
                          text=True, timeout=timeout, encoding="utf-8", errors="replace")


def _node_args(mode: str, limit: int, max_pages: int | None = None,
               folders: int | None = None) -> list[str]:
    if mode == "backfill":
        # limit 0 = no per-folder item cap; --max-pages bounds one run (0 = all).
        args = ["scripts/collect.mjs", "--mode", "backfill", "--limit", "0", "--login-timeout", "0"]
        if max_pages is not None:
            args += ["--max-pages", str(int(max_pages))]
        if folders is not None:
            args += ["--folders", str(int(folders))]
        return args
    return ["scripts/collect.mjs", "--mode", "head", "--limit", str(int(limit)), "--login-timeout", "0"]


def _fresh_done(started: float) -> tuple[dict, bool]:
    """Read done.json only when this run wrote it; an old file must never report success."""
    path = out_dir() / "done.json"
    try:
        fresh = path.stat().st_mtime >= started - 1.0
    except OSError:
        return {}, False
    return (_read_done() if fresh else {}), fresh


def _read_done() -> dict:
    try:
        return json.loads((out_dir() / "done.json").read_text(encoding="utf-8"))
    except Exception:
        return {}


def _account_key() -> str:
    """Stable ZCOOL account id from the Node collector state, for account-scoped sync.

    The engine/store key cursors, runs, failures and favorite relations by account_key
    (schema v7); without it the product would fall back to the legacy '' cursor and
    bootstrap again. collect.mjs writes state.account={id,idStr,...} during a run.
    """
    try:
        state = json.loads((out_dir() / "state.json").read_text(encoding="utf-8"))
    except Exception:
        return ""
    account = state.get("account") if isinstance(state, dict) else None
    if not isinstance(account, dict):
        return ""
    key = account.get("idStr") or account.get("id")
    return str(key) if key not in (None, "") else ""


def login(timeout_s: int = 600) -> dict:
    profile_dir().mkdir(parents=True, exist_ok=True)
    started = time.time()
    try:
        p = _run_node(["scripts/collect.mjs", "--discover", "--login-timeout",
                       str(timeout_s)], timeout_s + 60)
    except FileNotFoundError:
        return {"ok": False, "message": "本机没有 Node.js"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "message": "站酷登录超时"}
    done, fresh = _fresh_done(started)
    if not fresh:
        return {"ok": False, "message": "站酷登录进程没有产出本次结果（未检测到登录）"}
    if done.get("auth_state") == "logged_in":
        return {"ok": True, "message": "站酷已登录"}
    return {"ok": False, "message": done.get("error_message") or p.stderr[-300:] or "登录未完成"}


def _http_url(value) -> str | None:
    return value if isinstance(value, str) and value.startswith(("http://", "https://")) else None


def _local_file(out: Path, rel) -> Path | None:
    if not isinstance(rel, str) or not rel:
        return None
    candidate = out / rel
    return candidate if candidate.exists() else None


def _valid_sha(value) -> str | None:
    return value if isinstance(value, str) and _SHA256.match(value) else None


class ZcoolFileReader:
    """Run collect.mjs once, then expose out/ records as one sync-reader/1 page."""

    platform = PLATFORM
    restarted = False

    def __init__(self, limit: int = 30, timeout: int = TIMEOUT_S, mode: str = "head",
                 max_pages: int | None = None, folders: int | None = None, confirmation: Path | None = None):
        if mode not in MODES:
            raise ValueError(f"未知站酷采集模式 {mode!r}（只支持 head/backfill）")
        self.limit = int(limit)
        self.timeout = timeout
        self.mode = mode
        self.max_pages = max_pages
        self.folders = folders
        self.account_key = _account_key()
        self.confirmation = confirmation
        self._page = None

    def __enter__(self):
        out = out_dir()
        started = time.time()
        args=_node_args(self.mode,self.limit,self.max_pages,self.folders)
        if self.confirmation:args += ['--manual-verify','--confirm-file',str(self.confirmation)]
        p = _run_node(args,self.timeout)
        # Refresh before reading done.json: this run's state.json carries the account id
        # that the sync engine/store use to scope cursors, runs and failures.
        self.account_key = _account_key()
        done, fresh = _fresh_done(started)
        if not fresh:
            # Old done.json from a previous run must not be read as this run's success.
            raise engine.ReaderError(
                "network", f"站酷采集未产出本次 done.json（exit={p.returncode}）：{(p.stderr or '')[-200:]}")
        if done.get("status") == "blocked" or done.get("error_kind") in ("captcha", "blocked"):
            raise engine.ReaderError("blocked", "站酷要求安全验证，本次跳过")
        if done.get("auth_state") != "logged_in":
            raise engine.ReaderError("needs_login", done.get("error_message") or "站酷未登录")
        if done.get("status") not in ("done", "partial"):
            raise engine.ReaderError("network", done.get("error_message") or (p.stderr or "")[-300:])
        produced_mode = done.get("mode")
        if produced_mode not in (None, self.mode):
            # A head run ingested as backfill (or vice versa) would silently hide history.
            raise engine.ReaderError(
                "network", f"站酷采集模式不符：请求 {self.mode}，本次产出 {produced_mode}")
        items = self._work_items(out) + self._single_items(out)
        self._page = {"contract_version": "sync-reader/1", "platform": PLATFORM,
                      "collection": {"collection_key": COLLECTION, "title": "站酷收藏"},
                      "page": {"index": 1, "cursor_in": None, "cursor_out": None,
                               "has_more": False, "mode": self.mode},
                      "items": items, "failures": []}
        log.info("zcool %s reader: %d items (works+singles)", self.mode, len(items))
        return self

    def _image_refs(self, raw_images, out: Path) -> list[dict]:
        images = []
        for im in raw_images or []:
            if not isinstance(im, dict):
                continue
            local = _local_file(out, im.get("file"))
            url = _http_url(im.get("url"))
            sha = _valid_sha(im.get("sha256"))
            if not (url or local or sha):
                continue
            images.append({"idx": im.get("idx") or len(images) + 1, "url": url,
                           "local_path": str(local) if local else None,
                           "sha256": sha, "width": im.get("width"),
                           "height": im.get("height"),
                           "download_state": "ok" if local else "failed"})
        return images

    def _work_items(self, out: Path) -> list[dict]:
        items = []
        for f in sorted((out / "data" / "works").glob("*.json")):
            try:
                w = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            items.append({"work_key": w.get("encodeId") or f.stem, "title": w.get("title"),
                          "description": w.get("title"), "author_name": w.get("author"),
                          "canonical_url": _http_url(w.get("url")),
                          "source_url": _http_url(w.get("url")),
                          "media_type": "image",
                          "images": self._image_refs(w.get("images"), out)})
        return items

    def _single_items(self, out: Path) -> list[dict]:
        """Single-image favorites (folder type 29) from out/data/singles/<id>.json."""
        items = []
        singles = out / "data" / "singles"
        if not singles.is_dir():
            return items
        for f in sorted(singles.glob("*.json")):
            try:
                s = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            sid = str(s.get("id") or f.stem)
            page_url = _http_url(s.get("pageUrl")) or _http_url(s.get("url"))
            image = {"idx": 1, "url": _http_url(s.get("url")) or _http_url(s.get("urlBig")),
                     "local_path": None, "sha256": _valid_sha(s.get("sha256")),
                     "width": s.get("width"), "height": s.get("height"),
                     "download_state": "failed"}
            local = _local_file(out, s.get("file"))
            if local:
                image["local_path"] = str(local)
                image["download_state"] = "ok"
            if not (image["url"] or image["local_path"] or image["sha256"]):
                continue
            items.append({
                "work_key": "img29|" + sid,
                "title": s.get("title") or sid,
                "description": s.get("title") or sid,
                "author_name": None,
                "canonical_url": page_url,
                "source_url": page_url,
                "media_type": "image",
                "images": [image],
                # order 0: the single image is position 1 of a one-image post (idx 1).
                "extra": {"kind": "img29", "order": 0, "folder": s.get("folder")},
            })
        return items

    def check_auth(self) -> str:
        return "ok"

    def fetch_page(self, collection_key: str, cursor, page_index: int) -> dict:
        if page_index != 1:
            return {"contract_version": "sync-reader/1", "platform": PLATFORM,
                    "collection": {"collection_key": collection_key},
                    "page": {"index": page_index, "cursor_in": cursor, "cursor_out": None,
                             "has_more": False}, "items": [], "failures": []}
        env = json.loads(json.dumps(self._page))
        env["page"]["index"] = 1
        env["page"]["cursor_in"] = cursor
        return env

    def __exit__(self, *exc):
        return False


def run_sync(limit: int = 30, mode: str = "head", confirmation: Path | None = None) -> dict:
    if mode not in MODES:
        raise ValueError(f"未知站酷采集模式 {mode!r}（只支持 head/backfill）")
    base = store.max_image_id()
    engine_mode = "bootstrap"  # one page with has_more=false in both modes
    try:
        with ZcoolFileReader(limit=limit, mode=mode, **({"confirmation":confirmation}if confirmation else {})) as reader:
            engine.run(reader, COLLECTION, engine_mode)
    except Exception as exc:
        msg = f"{type(exc).__name__}: {exc}"[:300]
        rid = store.start_run(PLATFORM, COLLECTION, engine_mode, _account_key())
        kind = getattr(exc, 'kind', '')
        if kind in engine.PAUSE_KINDS:
            store.finish_run(rid, 'paused', pause_reason=kind)
        else:
            store.finish_run(rid, "failed", error=msg)
    run = store.latest_run(PLATFORM)
    return {**run, "images_new": store.images_created_since(PLATFORM, base)}


def run_backfill() -> dict:
    return run_sync(limit=0, mode="backfill")


def account_known() -> bool:
    return profile_dir().exists() and any(profile_dir().iterdir())


class ZcoolSyncRunner:
    def __init__(self, sync: Callable = run_sync, login_fn: Callable = login):
        self._sync, self._login = sync, login_fn
        self._lock = threading.Lock()
        self._thread = None
        self.action = None
        self.message = None
        self._confirmation = None

    @property
    def busy(self):
        return self._thread is not None and self._thread.is_alive()

    def _start(self, action, target):
        with self._lock:
            if self.busy:
                return False
            self.action, self.message = action, None
            self._thread = threading.Thread(target=self._wrap(target), daemon=True)
            self._thread.start()
            return True

    def _wrap(self, target):
        def go():
            try:
                self.message = target()
            except Exception as exc:
                self.message = f"{type(exc).__name__}: {exc}"[:300]
            finally:
                self.action = None
        return go

    def _invoke_sync(self, mode: str) -> dict:
        # run_sync owns the mode parameter; injected test doubles may take none.
        try:
            parameters = inspect.signature(self._sync).parameters
        except (TypeError, ValueError):
            parameters = {}
        return self._sync(mode=mode) if "mode" in parameters else self._sync()

    def start_sync(self, mode: str = "head"):
        if mode not in MODES:
            raise ValueError(f"未知站酷采集模式 {mode!r}（只支持 head/backfill）")
        return self._start("sync", lambda: self._invoke_sync(mode).get("error"))

    def start_backfill(self):
        return self.start_sync("backfill")

    def start_login(self):
        return self._start("login", lambda: self._login()["message"])

    def start_verification(self):
        if self.busy:return False
        signal=config.sync_staging_dir('zcool')/f'verify-{time.time_ns()}.signal'
        signal.parent.mkdir(parents=True,exist_ok=True)
        self._confirmation=signal
        return self._start('verify',lambda:run_sync(confirmation=signal).get('error') or '验证完成，已继续同步')

    def confirm_verification(self):
        if self.action!='verify' or not self.busy or self._confirmation is None:return False
        self._confirmation.write_text(str(time.time_ns()),encoding='utf8');return True

    def state(self):
        run = store.latest_run(PLATFORM)
        return {"busy": self.busy, "action": self.action, "message": self.message,
                "account_known": account_known(), "last_run": run,
                "failures": store.run_failures(run["id"]) if run else []}
