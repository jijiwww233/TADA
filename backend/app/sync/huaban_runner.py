"""Multi-board orchestration for Huaban.

One board = collection_key 'board:<id>'. Each board is synced by the EXISTING
sync.engine.run(); this module only:

- asks the reader for the board list (one login check, no extra per-board browser),
- runs boards independently so one board's failure does not abort the others,
- returns total + per-board statistics.

It does not implement a second engine, a new schema, full missing/unfavorite detection,
or any API/frontend. A full run is rejected: 'full' would touch apply_missing and mark
posts possibly_removed; this runner only supports bootstrap/incremental.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from . import engine


@dataclass(frozen=True)
class BoardResult:
    collection_key: str
    board_id: str | None
    title: str | None
    status: str
    run_id: int | None = None
    items_new: int = 0
    items_failed: int = 0
    pages_done: int = 0
    images_ok: int = 0
    images_failed: int = 0
    pause_reason: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class MultiBoardReport:
    platform: str
    boards: tuple[BoardResult, ...]
    totals: dict[str, Any]
    max_boards: int | None = None


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _board_result(collection_key: str, board: dict, run: dict) -> BoardResult:
    return BoardResult(
        collection_key=collection_key,
        board_id=str(board.get("board_id")) if board.get("board_id") is not None else None,
        title=board.get("title"),
        status=str(run.get("status") or "unknown"),
        run_id=run.get("id"),
        items_new=_int(run.get("items_new")),
        items_failed=_int(run.get("items_failed")),
        pages_done=_int(run.get("pages_done")),
        images_ok=_int(run.get("images_ok")),
        images_failed=_int(run.get("images_failed")),
        pause_reason=run.get("pause_reason"),
        error=run.get("error"),
    )


def _totals(results: Iterable[BoardResult]) -> dict[str, Any]:
    boards = list(results)
    statuses = {name: 0 for name in ("completed", "partial", "paused", "failed", "other")}
    for result in boards:
        if result.status in statuses:
            statuses[result.status] += 1
        else:
            statuses["other"] += 1
    total = {
        "boards": len(boards),
        **statuses,
        "items_new": sum(r.items_new for r in boards),
        "items_failed": sum(r.items_failed for r in boards),
        "pages_done": sum(r.pages_done for r in boards),
        "images_ok": sum(r.images_ok for r in boards),
        "images_failed": sum(r.images_failed for r in boards),
    }
    return total


# A pause caused by the platform refusing access must end the whole sync: requesting
# another board would just repeat the same risk response.
STOP_PAUSE_REASONS = {"blocked", "rate_limited", "needs_login"}


def run_boards(reader: Any, *, mode: str = "incremental",
               max_boards: int | None = None,
               run_one: Callable[..., dict] | None = None,
               sleep: Callable[[float], None] = time.sleep) -> MultiBoardReport:
    """Sync every board returned by reader.list_boards(), each via engine.run().

    mode='incremental' is safe for both first and later runs: engine auto-converts to
    bootstrap when that collection has no completed bootstrap, then later runs start
    from the top for new pins. mode='full' is intentionally rejected.
    """
    if mode not in ("incremental", "bootstrap"):
        raise ValueError("multi-board runner 只支持 bootstrap / incremental，不做 full/missing 判定")
    if max_boards is not None and max_boards < 0:
        raise ValueError("max_boards 不能为负数")

    boards = list(reader.list_boards())
    if max_boards is not None:
        boards = boards[:max_boards]
    runner = run_one or engine.run
    results: list[BoardResult] = []
    for board in boards:
        key = board.get("collection_key") if isinstance(board, dict) else None
        if not key:
            results.append(BoardResult(
                collection_key="", board_id=None, title=None, status="failed",
                error="画板缺少 collection_key",
            ))
            continue
        try:
            run = runner(reader, key, mode=mode, sleep=sleep)
        except Exception as exc:  # a board-specific crash must not abort later boards
            results.append(BoardResult(
                collection_key=key, board_id=str(board.get("board_id")),
                title=board.get("title"), status="failed",
                error=f"{type(exc).__name__}: {exc}"[:500],
            ))
            continue
        result = _board_result(key, board, run or {})
        results.append(result)
        if result.status == "paused" and result.pause_reason in STOP_PAUSE_REASONS:
            # Stop requesting later boards after a captcha / access block. The paused
            # run row already records the reason; later boards simply are not touched.
            break
    return MultiBoardReport(
        platform=getattr(reader, "platform", "huaban"),
        boards=tuple(results),
        totals=_totals(results),
        max_boards=max_boards,
    )
