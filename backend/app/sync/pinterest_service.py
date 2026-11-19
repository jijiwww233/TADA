"""Pinterest product adapter over the existing spike scripts.

The heavy lifting stays in vendor/pinterest:
  - pinterest_cdp.mjs: visible login, profile -> out/cookies.json + out/auth.json
  - pinterest_reader.py: emits sync-reader/1 page files (bootstrap/incremental)
Python only starts those processes, loads the page files and feeds them through the
existing sync engine/store. Credentials/profile/output live inside this refactor copy.
"""
from __future__ import annotations

import json
import logging
import shutil
import subprocess
import threading
import time
import sys
from pathlib import Path
from typing import Callable

from .. import config, db
from . import engine, store

log = logging.getLogger(__name__)
PLATFORM = "pinterest"
COLLECTION = "fav"
NODE = "node"
TIMEOUT_S = 1800


def vendor_dir() -> Path:
    return config.PROJECT_ROOT / "vendor" / "pinterest"


def out_dir() -> Path:
    return vendor_dir() / "out"


def cookies_path() -> Path:
    return out_dir() / "cookies.json"


def auth_path() -> Path:
    return out_dir() / "auth.json"


def checkpoint_path() -> Path:
    return config.sync_profile_dir("pinterest") / "reader-checkpoint.json"


def _node(*args: str, timeout: int = TIMEOUT_S) -> subprocess.CompletedProcess:
    return subprocess.run([NODE, *args], cwd=str(vendor_dir()), capture_output=True,
                          text=True, timeout=timeout, encoding="utf-8", errors="replace")


def login(timeout_s: int = 600) -> dict:
    out_dir().mkdir(parents=True, exist_ok=True)
    try:
        p = _node("scripts/pinterest_cdp.mjs", "login", "--timeout", str(timeout_s),
                  timeout=timeout_s + 30)
    except FileNotFoundError:
        return {"ok": False, "message": "本机没有 Node.js"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "message": "Pinterest 登录超时"}
    if not auth_path().exists() or not cookies_path().exists():
        return {"ok": False, "message": "未检测到 Pinterest 登录；请在弹出的 Chrome 完成登录"}
    auth = json.loads(auth_path().read_text(encoding="utf-8"))
    return {"ok": True, "message": f"Pinterest 已登录：{auth.get('username') or '(已保存)'}"}


def _auth_username() -> str | None:
    try:
        data = json.loads(auth_path().read_text(encoding="utf-8"))
        return data.get("username")
    except Exception:
        return None


class PinterestExternalReader:
    """Run pinterest_reader.py once, then expose its fav page files to engine.run."""

    platform = PLATFORM
    restarted = False

    def __init__(self, mode: str, out: Path, *, timeout: int = TIMEOUT_S):
        self.mode = mode
        self.out = out
        self.timeout = timeout
        self.pages: dict[str, list[dict]] = {}
        self.done: dict = {}
        self.account_key = ""

    def __enter__(self):
        cookies, username = cookies_path(), _auth_username()
        if not cookies.exists() or not username:
            raise engine.ReaderError("needs_login", "Pinterest 未登录")
        self.account_key = username or ""
        self.out.mkdir(parents=True, exist_ok=True)
        # The database is authoritative; vendor checkpoint.json is never trusted to
        # advance past a page that has not committed in store.apply_page.
        snapshot = {}
        conn=db.connect()
        try:
            rows=conn.execute("SELECT collection_key,cursor_json,bootstrap_complete FROM sync_cursors_v2 WHERE platform=? AND account_key=?",(PLATFORM,self.account_key)).fetchall()
            for row in rows:
                cur=json.loads(row['cursor_json'])
                ids=[x[0]for x in conn.execute("""SELECT p.source_id FROM posts p JOIN post_collections pc ON pc.post_id=p.id
                    WHERE pc.platform=? AND pc.account_key=? AND pc.collection_key=?""",(PLATFORM,self.account_key,row['collection_key']))]
                snapshot[row['collection_key']]={'cursor':cur.get('next_cursor'),'page_index':cur.get('page_index',0),'has_more':cur.get('has_more',False),'bootstrap_complete':bool(row['bootstrap_complete']),'seen_ids':ids}
        finally:conn.close()
        snapshot['__complete__'] = []
        conn=db.connect()
        try:
            for row in conn.execute("SELECT source_id,extra_json FROM posts WHERE platform=?",(PLATFORM,)):
                if (json.loads(row['extra_json'] or '{}').get('pinterest_resource_schema')==2
                        and store.is_complete(PLATFORM,row['source_id'])):
                    snapshot['__complete__'].append(row['source_id'])
        finally:conn.close()
        snapshot['__failures__']=[x['work_key']for x in store.unresolved_failures(PLATFORM,self.account_key)]
        snapshot_file=self.out/'input-checkpoint.json'
        snapshot_file.write_text(json.dumps(snapshot),encoding='utf8')
        cmd = [sys.executable,
               str(vendor_dir() / "scripts" / "pinterest_reader.py"),
               "--out", str(self.out), "--mode", self.mode,
               "--checkpoint", str(snapshot_file),
               "--cookies", str(cookies), "--username", username, "--delay", "1.0"]
        checkpoint_path().parent.mkdir(parents=True, exist_ok=True)
        p = subprocess.run(cmd, cwd=str(vendor_dir()), capture_output=True, text=True,
                           timeout=self.timeout, encoding="utf-8", errors="replace")
        self.done = json.loads((self.out / "done.json").read_text(encoding="utf-8")) if (
                self.out / "done.json").exists() else {"status": "error"}
        if self.done.get("status") == "needs_login":
            raise engine.ReaderError("needs_login", "Pinterest 登录失效")
        if self.done.get("status") == "blocked":
            raise engine.ReaderError("rate_limited", "Pinterest 验证码/访问限制")
        if self.done.get("status") != "ok":
            raise engine.ReaderError("network", "Pinterest 采集异常，本地数据保留")
        for f in sorted(self.out.glob("page-*.json")):
            try:
                env = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            key = (env.get("collection") or {}).get("collection_key")
            if key:
                self.pages.setdefault(key, []).append(env)
        for pages in self.pages.values():
            pages.sort(key=lambda e: int((e.get("page") or {}).get("index") or 0))
        return self

    def check_auth(self) -> str:
        return "ok"

    def fetch_page(self, collection_key: str, cursor, page_index: int) -> dict:
        pages = self.pages.get(collection_key, [])
        for page in pages:
            if page['page']['index']==page_index:
                return json.loads(json.dumps(page))
        if pages and pages[-1]['page']['has_more']:
            raise engine.ReaderError('limit','Pinterest 本次分页上限，下次从已提交游标继续')
        return {
            "contract_version": "sync-reader/1", "platform": PLATFORM,
            "collection": {"collection_key": collection_key},
            "page": {"index": page_index, "cursor_in": cursor, "cursor_out": None,
                     "has_more": False},
            "items": [], "failures": [],
        }

    def __exit__(self, *exc):
        return False


def run_sync(max_pages: int | None = None) -> dict:
    saved = store.load_cursor(PLATFORM, COLLECTION)
    mode = "incremental" if saved and saved["bootstrap_complete"] else "bootstrap"
    out = config.sync_staging_dir(PLATFORM) / f"reader-{time.time_ns()}"
    base = store.max_image_id()
    aggregate = []
    try:
        with PinterestExternalReader(mode, out) as reader:
            runs=[]
            for collection in reader.pages:
                runs.append(engine.run(reader, collection, 'incremental', max_pages=max_pages))
            aggregate = runs
    except Exception as exc:
        msg = f"{type(exc).__name__}: {exc}"[:300]
        if not store.fail_open_runs(PLATFORM, msg):
            rid = store.start_run(PLATFORM, COLLECTION, mode)
            kind=getattr(exc,'kind','')
            if kind in engine.PAUSE_KINDS: store.finish_run(rid,'paused',pause_reason=kind)
            else: store.finish_run(rid, "failed", error="Pinterest 采集失败，本地数据保留")
    if aggregate:
        rid=store.start_run(PLATFORM,'all','incremental',reader.account_key)
        bad=[x for x in aggregate if x['status']not in ('completed',)]
        status='partial'if bad else'completed'
        store.finish_run(rid,status,error='部分集合未完成，请查看分集合记录'if bad else None)
        conn=db.connect()
        try:
            names=('pages_done','items_found','items_new','items_failed','images_ok','images_failed')
            conn.execute('UPDATE sync_runs SET '+','.join(k+'=?'for k in names)+' WHERE id=?',tuple(sum(x.get(k,0)or 0 for x in aggregate)for k in names)+(rid,))
        finally:conn.close()
    run = store.latest_run(PLATFORM)
    return {**run, "images_new": store.images_created_since(PLATFORM, base), 'collections':aggregate}


def account_known() -> bool:
    return auth_path().exists() and cookies_path().exists() and bool(_auth_username())


class PinterestSyncRunner:
    def __init__(self, sync: Callable = run_sync, login_fn: Callable = login):
        import threading
        self._sync, self._login = sync, login_fn
        self._lock = threading.Lock()
        self._thread = None
        self.action = None
        self.message = None

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

    def start_sync(self):
        return self._start("sync", lambda: self._sync().get("error"))

    def start_login(self):
        return self._start("login", lambda: login()["message"])

    def state(self):
        run = store.latest_run(PLATFORM)
        return {"busy": self.busy, "action": self.action, "message": self.message,
                "account_known": account_known(), "last_run": run,
                "failures": store.run_failures(run["id"]) if run else []}
