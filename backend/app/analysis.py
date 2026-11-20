"""AI analysis bookkeeping and the background queue. One analyses row per image
(image_id is UNIQUE).

Idempotency: ensure_analysis never touches an existing row; only status='pending' rows are
claimed; retry only moves failed -> pending. Nothing resets a 'done' row, so re-importing
or re-syncing an image never re-analyzes it.

Atomic claim (schema v2): before calling the model, an executor (background worker or CLI,
any process) stamps the row with claimed_by / claimed_at inside BEGIN IMMEDIATE. SQLite's
write lock makes the claim exclusive across threads and processes; only the claimer may
call the model, and only the claimer can write the result (WHERE claimed_by = token).
status stays 'pending' while claimed. A claim older than ANALYSIS_CLAIM_TTL_SECONDS means
its executor died mid-call: the row becomes failed (result unknown, maybe billed) and
waits for a manual retry instead of being resent automatically.

After a row is written as done, process_image calls on_done(image_id) if one is given.
The app and the CLI pass EmbeddingAdapter.enqueue (returns at once, no HTTP in this
thread); the default None does nothing. A failing on_done never changes the analysis.

Usage from backend/: python -m app.analysis --ids 1,2,3   (analyze just these, then exit)
                     python -m app.analysis --pending [--limit N]
"""
import argparse
import json
import logging
import os
import sqlite3
import threading
import time
import uuid
from typing import Callable

import httpx

from . import colors, config, db, vision

log = logging.getLogger(__name__)
IDLE_POLL = 3.0
# Longest a claim may be held. One image makes at most vision.MAX_REQUESTS requests of
# vision.TIMEOUT seconds plus retry delays (~6.5 min), well inside this.
ANALYSIS_CLAIM_TTL_SECONDS = 900
INTERRUPTED = "上次处理被中断，结果未知，可能已经产生费用，请手动重试"


def ensure_analysis(conn: sqlite3.Connection, image_id: int) -> bool:
    """Queue an image for analysis. Idempotent: an existing row (pending / done / failed)
    is left untouched, so re-importing or re-syncing never re-analyzes an image.
    Returns True if a new pending row was created."""
    cur = conn.execute(
        "INSERT OR IGNORE INTO analyses (image_id) VALUES (?)", (image_id,)
    )
    return cur.rowcount == 1


# ---- claims ----

def new_token(kind: str) -> str:
    return f"{kind}:{os.getpid()}:{uuid.uuid4().hex[:12]}"


def _now() -> int:
    return int(time.time())  # Unix seconds, independent of local time zone


def _expire(conn: sqlite3.Connection, now: int) -> int:
    return conn.execute(
        """UPDATE analyses SET status = 'failed', error = ?, claimed_by = NULL,
                               claimed_at = NULL, updated_at = datetime('now')
           WHERE status = 'pending' AND claimed_by IS NOT NULL AND claimed_at <= ?""",
        (INTERRUPTED, now - ANALYSIS_CLAIM_TTL_SECONDS),
    ).rowcount


def expire_stale_claims(now: int | None = None) -> int:
    conn = db.connect()
    try:
        return _expire(conn, _now() if now is None else now)
    finally:
        conn.close()


def claim(token: str, image_id: int | None = None, *, now: int | None = None,
          prepare: Callable[[sqlite3.Connection], int | None] | None = None) -> int | None:
    """Atomically claim one pending, unclaimed row (a specific image, or the next in grid
    order). Returns the image id, or None if there is nothing claimable."""
    now = _now() if now is None else now
    conn = db.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            _expire(conn, now)
            if prepare is not None:
                image_id = prepare(conn)
                if image_id is None:
                    conn.execute("COMMIT")
                    return None
            sql = """SELECT a.image_id FROM analyses a JOIN images i ON i.id = a.image_id
                     WHERE a.status = 'pending' AND a.claimed_by IS NULL
                       AND i.deleted_at IS NULL"""
            if image_id is None:
                row = conn.execute(sql + " ORDER BY i.post_id DESC, i.position ASC LIMIT 1").fetchone()
            else:
                row = conn.execute(sql + " AND a.image_id = ?", (image_id,)).fetchone()
            if row is None:
                conn.execute("COMMIT")
                return None
            conn.execute(
                "UPDATE analyses SET claimed_by = ?, claimed_at = ? WHERE image_id = ?",
                (token, now, row["image_id"]),
            )
            conn.execute("COMMIT")
            return row["image_id"]
        except BaseException:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()


def _release(conn, image_id: int, token: str) -> None:
    """Give a claim back without a model call having been made (row stays pending)."""
    conn.execute(
        "UPDATE analyses SET claimed_by = NULL, claimed_at = NULL WHERE image_id = ? AND claimed_by = ?",
        (image_id, token),
    )


# ---- one image ----

def _record(conn, image_id: int, token: str, status: str, calls: vision.Calls, model: str,
            result: dict | None = None, error: str | None = None) -> bool:
    """Write the outcome and drop the claim. Tokens and attempts accumulate across
    retries (real spend); tokens stay NULL if no response ever reported usage.
    Only the current claimer of a still-pending row can write."""
    r = result or {}
    cur = conn.execute(
        """UPDATE analyses SET
               status = ?, model = ?, error = ?, raw_json = ?,
               visible_text = ?, subject = ?, layout_json = ?, style_json = ?, description = ?, title = ?,
               prompt_tokens = CASE WHEN ? IS NULL THEN prompt_tokens
                                    ELSE COALESCE(prompt_tokens, 0) + ? END,
               completion_tokens = CASE WHEN ? IS NULL THEN completion_tokens
                                        ELSE COALESCE(completion_tokens, 0) + ? END,
               attempts = attempts + ?,
               claimed_by = NULL, claimed_at = NULL,
               updated_at = datetime('now')
           WHERE image_id = ? AND status = 'pending' AND claimed_by = ?""",
        (status, model, vision.redact(error), vision.redact(calls.raw, max_len=None) if status == "failed" else calls.raw,
         r.get("visible_text"),
         json.dumps(r["subject"], ensure_ascii=False) if r else None,
         json.dumps(r["layout"], ensure_ascii=False) if r else None,
         json.dumps(r["style"], ensure_ascii=False) if r else None,
         r.get("description"),
         r.get("title"),
         calls.prompt_tokens, calls.prompt_tokens,
         calls.completion_tokens, calls.completion_tokens,
         calls.count, image_id, token),
    )
    if cur.rowcount != 1:
        log.warning("analysis result for image %s not recorded: claim lost", image_id)
    return cur.rowcount == 1


def _notify_done(on_done: Callable[[int], object] | None, image_id: int) -> None:
    if on_done is None:
        return
    try:
        on_done(image_id)
    except Exception as exc:  # embedding problems never touch the analysis
        log.error("on_done for image %s failed: %s", image_id, vision.redact(f"{type(exc).__name__}: {exc}"))


def process_image(image_id: int, *, token: str | None = None, client: httpx.Client | None = None,
                  sleep: Callable[[float], None] = time.sleep,
                  on_done: Callable[[int], object] | None = None) -> dict:
    """Colors (if missing), then the vision call if this executor holds the claim.
    token=None: claim this image first (CLI path); otherwise the caller already claimed it.
    Returns {'status', 'calls', 'error'}; raises VisionError for vision.STOP_KINDS, problems
    that would fail every image (recorded as failed, except 'config': no call was made)."""
    conn = db.connect()
    try:
        row = conn.execute(
            """SELECT i.file_path, a.status, a.claimed_by FROM images i
               LEFT JOIN analyses a ON a.image_id = i.id WHERE i.id = ?""", (image_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"图片 {image_id} 不存在")
        path = config.data_dir() / row["file_path"]
        try:
            colors.ensure_colors(conn, image_id, path)
        except Exception as exc:  # colors are independent of the AI result
            log.warning("colors failed for image %s: %s", image_id, exc)

        if token is None:
            token = new_token("cli")
            if claim(token, image_id) is None:
                status = row["status"] or "missing"
                if status == "pending":
                    status = "claimed_elsewhere"
                return {"status": status, "calls": 0, "error": None}

        settings = config.ai_settings()
        calls = vision.Calls()
        try:
            result = vision.analyze(path, settings, calls=calls, client=client, sleep=sleep)
        except vision.VisionError as exc:
            if exc.kind == "config":
                _release(conn, image_id, token)
                raise
            error = f"{exc.kind}: {exc}"
            if calls.notes:
                error += "（" + "；".join(calls.notes) + "）"
            _record(conn, image_id, token, "failed", calls, settings["vision_model"], error=error)
            if exc.kind in vision.STOP_KINDS:
                raise
            return {"status": "failed", "calls": calls.count, "error": vision.redact(error)}
        except Exception as exc:  # unreadable file etc.
            error = f"local: {type(exc).__name__}: {exc}"
            _record(conn, image_id, token, "failed", calls, settings["vision_model"], error=error)
            return {"status": "failed", "calls": calls.count, "error": vision.redact(error)}
        if _record(conn, image_id, token, "done", calls, settings["vision_model"], result=result):
            _notify_done(on_done, image_id)
        return {"status": "done", "calls": calls.count, "error": None}
    finally:
        conn.close()


def retry(image_id: int | None = None) -> int:
    """failed -> pending (one image, or all failed). Never touches done rows."""
    conn = db.connect()
    try:
        sql = """UPDATE analyses SET status = 'pending', claimed_by = NULL, claimed_at = NULL,
                                     updated_at = datetime('now') WHERE status = 'failed'"""
        if image_id is None:
            return conn.execute(sql).rowcount
        return conn.execute(sql + " AND image_id = ?", (image_id,)).rowcount
    finally:
        conn.close()


# ---- read side ----

def get_analysis(image_id: int) -> dict:
    conn = db.connect()
    try:
        a = conn.execute("SELECT * FROM analyses WHERE image_id = ?", (image_id,)).fetchone()
        cols = conn.execute(
            "SELECT rank, hex, ratio FROM colors WHERE image_id = ? ORDER BY rank", (image_id,)
        ).fetchall()
    finally:
        conn.close()
    analysis = None
    if a is not None:
        analysis = {
            "status": a["status"],
            "processing": a["claimed_by"] is not None,
            "model": a["model"],
            "visible_text": a["visible_text"],
            "subject": json.loads(a["subject"]) if a["subject"] else None,
            "layout": json.loads(a["layout_json"]) if a["layout_json"] else None,
            "style": json.loads(a["style_json"]) if a["style_json"] else None,
            "description": a["description"],
            "title": a["title"],
            "prompt_tokens": a["prompt_tokens"],
            "completion_tokens": a["completion_tokens"],
            "attempts": a["attempts"],
            "error": vision.redact(a["error"]),
            "updated_at": a["updated_at"],
        }
    return {"analysis": analysis, "colors": [dict(c) for c in cols]}


def summary() -> dict:
    conn = db.connect()
    try:
        # Soft-deleted images leave every count, so the progress bar cannot hang on
        # rows the grid no longer shows.
        counts = {r["status"]: r["n"] for r in conn.execute(
            """SELECT a.status, COUNT(*) AS n FROM analyses a
               JOIN images i ON i.id=a.image_id AND i.deleted_at IS NULL
               GROUP BY a.status""")}
        processing = conn.execute(
            """SELECT COUNT(*) FROM analyses a JOIN images i ON i.id=a.image_id
               WHERE i.deleted_at IS NULL AND a.status = 'pending' AND a.claimed_by IS NOT NULL"""
        ).fetchone()[0]
        tok = conn.execute(
            """SELECT COALESCE(SUM(a.prompt_tokens), 0) AS p, COALESCE(SUM(a.completion_tokens), 0) AS c
               FROM analyses a JOIN images i ON i.id=a.image_id AND i.deleted_at IS NULL""").fetchone()
        total = conn.execute("SELECT COUNT(*) FROM images WHERE deleted_at IS NULL").fetchone()[0]
    finally:
        conn.close()
    return {
        "total": total,
        "pending": counts.get("pending", 0) - processing,
        "processing": processing,
        "done": counts.get("done", 0),
        "failed": counts.get("failed", 0),
        "prompt_tokens": tok["p"],
        "completion_tokens": tok["c"],
    }


# ---- background queue ----

class Worker:
    """Bounded daemon pool sharing a pause switch. Active claims finish after pause."""

    def __init__(self, client_factory: Callable[[], httpx.Client] = httpx.Client,
                 sleep: Callable[[float], None] = time.sleep,
                 on_done: Callable[[int], object] | None = None,
                 next_eligible: Callable[[], int | None] | None = None,
                 prepare_claim: Callable[[sqlite3.Connection], int | None] | None = None,
                 concurrency: int = 1):
        self._client_factory = client_factory
        self._sleep = sleep
        self._on_done = on_done
        self._next_eligible = next_eligible
        self._prepare_claim = prepare_claim
        self._lock = threading.Lock()
        self._running = threading.Event()
        self._stop = threading.Event()
        self._concurrency = concurrency
        self._threads: list[threading.Thread] = []
        self._current: dict[str, int] = {}
        self.token = new_token("worker")
        self.last_error: str | None = None

    @property
    def running(self) -> bool:
        return (self._running.is_set() and not self._stop.is_set()
                and any(thread.is_alive() for thread in self._threads))

    def start(self) -> None:
        with self._lock:
            if self._stop.is_set():
                return
            self.last_error = None
            self._running.set()
            self._threads = [thread for thread in self._threads if thread.is_alive()]
            for _ in range(self._concurrency - len(self._threads)):
                thread = threading.Thread(target=self._loop, name="analysis-worker", daemon=True)
                self._threads.append(thread)
                thread.start()

    def pause(self) -> None:
        with self._lock:
            self._running.clear()

    def shutdown(self) -> None:
        with self._lock:
            self._stop.set()
            self._running.set()  # wake paused threads so they can exit
            threads = tuple(self._threads)
        deadline = time.monotonic() + 5
        for thread in threads:
            thread.join(timeout=max(0, deadline - time.monotonic()))

    def run_once(self, client: httpx.Client, token: str | None = None) -> bool:
        """Claim and process the next image. False when the queue is empty, we are paused,
        or a STOP_KINDS error paused us. Single-image failures are recorded and return True.
        Errors in claiming propagate (the loop ends and clears running)."""
        token = token or self.token
        with self._lock:
            if not self._running.is_set() or self._stop.is_set():
                return False
            if self._next_eligible is not None:
                candidate = self._next_eligible()
                if candidate is None:
                    return False
                image_id = claim(token, candidate)
            else:
                image_id = claim(token, prepare=self._prepare_claim)
            if image_id is None:
                return False
            self._current[token] = image_id
        try:
            process_image(image_id, token=token, client=client, sleep=self._sleep,
                          on_done=self._on_done)
        except vision.VisionError as exc:  # STOP_KINDS: every image would fail
            self.last_error = vision.redact(f"{exc.kind}: {exc}")
            self.pause()
            return False
        except Exception as exc:
            self.last_error = vision.redact(f"{type(exc).__name__}: {exc}")
            log.error("analysis worker: image %s: %s", image_id, self.last_error)
            self.pause()
            return False
        finally:
            with self._lock:
                self._current.pop(token, None)
        return True

    def _loop(self) -> None:
        token = new_token("worker")
        try:
            with self._client_factory() as client:
                while not self._stop.is_set():
                    self._running.wait()
                    if self._stop.is_set():
                        break
                    if not self.run_once(client, token):
                        self._stop.wait(IDLE_POLL)
        except BaseException as exc:
            self.last_error = vision.redact(f"后台任务异常退出：{type(exc).__name__}: {exc}")
            log.error("analysis worker stopped: %s", self.last_error)
            self.pause()

    def state(self) -> dict:
        settings = config.ai_settings()
        with self._lock:
            current = list(self._current.values())
        return {
            "running": self.running,
            "current_image_id": current[0] if current else None,
            "current_image_ids": current,
            "concurrency": self._concurrency,
            "last_error": vision.redact(self.last_error),
            "configured": bool(settings["base_url"] and settings["api_key"] and settings["vision_model"]),
            "model": settings["vision_model"] or None,
        }


def run_batch(*, ids: list[int] | None = None, limit: int | None = None,
              client: httpx.Client, on_done: Callable[[int], object] | None = None,
              sleep: Callable[[float], None] = time.sleep,
              out: Callable[[str], None] = lambda line: print(line, flush=True)) -> int:
    """CLI body. ids: exactly these images; ids=None: claim pending rows in grid order
    until none is left (or `limit` processed). Stops at vision.STOP_KINDS.
    Returns how many images this executor processed."""
    token = new_token("cli")
    processed = 0
    while limit is None or processed < limit:
        if ids is not None:
            if processed >= len(ids):
                break
            image_id, held = ids[processed], None
        else:
            image_id = held = claim(token)
            if image_id is None:
                break
        started = time.monotonic()
        try:
            # --ids: process_image claims the image itself; --pending: we already hold it
            res = process_image(image_id, token=token if held is not None else None,
                                client=client, sleep=sleep,
                                on_done=on_done)
        except vision.VisionError as exc:
            out(f"image {image_id}: stopped, {exc.kind}: {vision.redact(str(exc))}")
            break
        processed += 1
        out(f"image {image_id}: {res['status']} calls={res['calls']} "
            f"{time.monotonic() - started:.1f}s {res['error'] or ''}")
    return processed


def _cli() -> None:
    from .embedding import dimensions
    from .embedding.adapter import EmbeddingAdapter

    parser = argparse.ArgumentParser(description="Analyze images, then exit.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--ids", help="comma-separated image ids")
    group.add_argument("--pending", action="store_true", help="claim pending images in grid order")
    parser.add_argument("--limit", type=int, default=None, help="process at most N images")
    args = parser.parse_args()
    db.init_db()
    ids = [int(x) for x in args.ids.split(",") if x.strip()] if args.ids else None
    embedder = EmbeddingAdapter(dimension_schemes=dimensions.SCHEMES)  # combined + per-dimension rows
    try:
        with httpx.Client() as client:
            run_batch(ids=ids, limit=args.limit, client=client, on_done=embedder.enqueue)
    finally:
        print("embedding: " + json.dumps(embedder.shutdown(timeout=300), ensure_ascii=False), flush=True)
    print(json.dumps(summary(), ensure_ascii=False))


if __name__ == "__main__":
    _cli()
