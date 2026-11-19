"""Sync run state machine. Platform-agnostic: talks to a Reader, writes via store.

Modes:
- bootstrap:   page 1 .. has_more=false, resumable from the saved cursor.
- incremental: from page 1. Stops at a page with no new post only once every work_key
               that had an unresolved failure before the run has been met (and retried)
               in this run; otherwise keeps going to the end of the list. Never touches
               the cursor; becomes bootstrap until bootstrap has completed once.
- full:        complete pass, resumable. Only a full pass that reaches the end with no
               unresolved item failures (across resumed runs) may advance miss_streak.

Reader errors: needs_login / blocked / limit -> paused, never retried. network / rate_limited ->
retried with backoff, then paused. Anything else -> failed. No error deletes data.
"""
import time
from typing import Callable, Protocol

from . import store, safety
from .metrics import measure
from .contract import ContractError, parse_page

RETRY_DELAYS = (2, 8, 30)
PAUSE_KINDS = {"needs_login", "blocked", "network", "rate_limited", "limit"}
RETRYABLE_KINDS = {"network", "rate_limited"}


class ReaderError(Exception):
    def __init__(self, kind: str, message: str = ""):
        super().__init__(message or kind)
        self.kind = kind


class Reader(Protocol):
    platform: str

    def check_auth(self) -> str:
        """'ok' | 'needs_login' | 'blocked'"""

    def fetch_page(self, collection_key: str, cursor: str | None, page_index: int) -> dict:
        """Return one sync-reader/1 page, or raise ReaderError."""


def _fetch(reader: Reader, collection_key, cursor, page_index, sleep) -> dict:
    for delay in (*RETRY_DELAYS, None):
        try:
            return reader.fetch_page(collection_key, cursor, page_index)
        except ReaderError as exc:
            if reader.platform == "xhs" and exc.kind in {"network", "rate_limited", "blocked"}:
                safety.trip("同步异常响应，已停止自动重试")
                raise
            if exc.kind not in RETRYABLE_KINDS or delay is None:
                raise
            sleep(delay)
    raise AssertionError("unreachable")


def run(reader: Reader, collection_key: str, mode: str = "incremental", *,
        max_pages: int | None = None, sleep: Callable[[float], None] = time.sleep) -> dict:
    if mode not in ("bootstrap", "incremental", "full"):
        raise ValueError(f"unknown mode {mode}")
    platform = reader.platform
    run_id = store.start_run(platform, collection_key, mode)
    if platform == 'xhs' and safety.state()['blocked']:
        store.finish_run(run_id, 'paused', pause_reason='blocked', error=safety.MESSAGE)
        return store.get_run(run_id)

    try:
        auth = reader.check_auth()
    except ReaderError as exc:
        auth = exc.kind
    if auth != "ok":
        if platform == "xhs" and auth in {"blocked", "network", "rate_limited"}:
            safety.trip("认证阶段异常，停止同步")
        if auth in PAUSE_KINDS:
            store.finish_run(run_id, "paused", pause_reason=auth)
        else:
            store.finish_run(run_id, "failed", error=f"check_auth: {auth}")
        return store.get_run(run_id)

    account = getattr(reader, "account_key", "") or ""
    store.set_run_account(run_id, account)
    if mode == "full" and store.other_collections(platform, collection_key, account):
        # posts carry no collection membership yet, so missing-marking is per account.
        store.finish_run(run_id, "failed", error="full 扫描暂只支持每个平台一个收藏夹")
        return store.get_run(run_id)

    saved = store.load_cursor(platform, collection_key, account)
    bootstrap_done = bool(saved and saved["bootstrap_complete"])
    if mode == "incremental" and not bootstrap_done:
        mode = "bootstrap"
    elif mode == "bootstrap" and bootstrap_done:
        mode = "incremental"
    store.set_run_mode(run_id, mode)

    # Where to start, and the cursor fields carried across resumed runs.
    cursor, page_index, cursor_base = None, 1, None
    if mode in ("bootstrap", "full"):
        prev = saved["cursor"] if saved and saved["phase"] == mode else None
        if prev and prev.get("has_more"):
            cursor, page_index = prev.get("next_cursor"), prev["page_index"] + 1
            cursor_base = {k: prev[k] for k in ("scan_start_run_id", "scan_clean") if k in prev}
        else:
            cursor_base = {"scan_start_run_id": run_id, "scan_clean": True} if mode == "full" else {}

    # Earlier failures (possibly deep in the list) that incremental must reach before stopping.
    outstanding = ({f["work_key"] for f in store.unresolved_failures(platform, account)}
                   if mode == "incremental" else set())

    status, pause_reason, error = None, None, None
    reached_end, any_failed, pages = False, False, 0
    while True:
        if max_pages is not None and pages >= max_pages:
            status, pause_reason = "paused", "page_limit"
            break
        try:
            raw = _fetch(reader, collection_key, cursor, page_index, sleep)
        except ReaderError as exc:
            if exc.kind in PAUSE_KINDS:
                status, pause_reason = "paused", exc.kind
            else:
                status, error = "failed", f"{exc.kind}: {exc}"
            break
        except Exception as exc:  # reader bug: never leave the run stuck in 'running'
            status, error = "failed", f"reader: {type(exc).__name__}: {exc}"
            break
        try:
            page = parse_page(raw)
            if (page.platform, page.collection_key, page.index) != (platform, collection_key, page_index):
                raise ContractError(
                    f"页面不匹配：期望 {platform}/{collection_key}/第 {page_index} 页，"
                    f"收到 {page.platform}/{page.collection_key}/第 {page.index} 页")
        except ContractError as exc:
            if platform == "xhs": safety.trip("收藏响应格式异常")
            status, error = "failed", f"contract: {exc}"
            break
        try:
            with measure(platform, "store", len(page.items) + len(page.failures)):
                result = store.apply_page(run_id, page, mode, cursor_base, account)
        except Exception as exc:
            status, error = "failed", f"store: {type(exc).__name__}: {exc}"
            break
        pages += 1
        any_failed = any_failed or result.items_failed > 0
        if cursor_base is not None:
            cursor_base["scan_clean"] = cursor_base.get("scan_clean", True) and result.items_failed == 0
        outstanding -= {i.work_key for i in page.items} | {f.work_key for f in page.failures}
        if mode == "incremental" and result.items_new == 0 and not outstanding:
            break  # caught up, and every earlier failure got its retry
        if not page.has_more:
            reached_end = True
            break
        cursor, page_index = page.cursor_out, page_index + 1

    if status is None:
        status = "partial" if any_failed else "completed"
    full_scan = mode == "full" and reached_end and bool(cursor_base.get("scan_clean"))
    if full_scan:
        store.apply_missing(platform, cursor_base["scan_start_run_id"])
    store.finish_run(run_id, status, pause_reason=pause_reason, error=error, full_scan=full_scan)
    return store.get_run(run_id)
