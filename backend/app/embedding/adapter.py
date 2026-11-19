"""EmbeddingAdapter: the one place that turns done analyses into stored embeddings.

One adapter per app instance (or per CLI process), one daemon thread. enqueue() only
records the image id and returns at once, so the vision worker never waits on
embedding HTTP. The thread sends a batch (at most MAX_BATCH texts, one request) when:
  - MAX_BATCH ids are queued, or
  - the oldest queued id has waited flush_seconds, or
  - drain() / shutdown() asks for everything now.
Repeated ids are merged. Before each request the batch is re-checked with
store.load_pending(), so ids that are not done or already valid cost nothing.

An embedding counts as done only after upsert_batch() committed. A failed batch is
logged and counted; its ids are not retried automatically: they stay stale and the
next backfill picks them up. STOP_KINDS (config / auth / model) stop the adapter,
because every later batch would fail the same way. Nothing here touches analyses.

Usage from backend/: python -m app.embedding.backfill [--dry-run] [--limit N]
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable

from .. import config, db, vision
from . import dimensions, store
from .client import MAX_BATCH, STOP_KINDS, EmbeddingClient, EmbeddingError, EmbeddingSettings
from .service import EmbeddingSpec

log = logging.getLogger(__name__)


class EmbeddingAdapter:
    def __init__(self, settings: EmbeddingSettings | None = None,
                 client_factory: Callable[[EmbeddingSettings], EmbeddingClient] | None = None,
                 flush_seconds: float | None = None,
                 clock: Callable[[], float] = time.monotonic,
                 dimension_schemes: tuple[str, ...] = ()):
        self.last_error: str | None = None
        try:
            self.settings = settings or EmbeddingSettings.from_env()
        except EmbeddingError as exc:  # e.g. EMBEDDING_DIMENSIONS is not a number
            self.settings = None
            self.last_error = exc.message
        self.spec = (EmbeddingSpec.from_settings(self.settings)
                     if self.settings else None)
        self._client_factory = client_factory or (lambda s: EmbeddingClient(s))
        self.flush_seconds = (config.ai_settings()["embedding_flush_seconds"]
                              if flush_seconds is None else flush_seconds)
        self._clock = clock
        self.dimension_schemes = tuple(dimension_schemes)
        self._cond = threading.Condition()
        self._queue: dict[int, float] = {}  # image_id -> first enqueue time (insertion ordered)
        self._busy = False
        self._flush = False
        self._closing = False
        self._stopped = False
        self._thread: threading.Thread | None = None
        self.stats = {"queued_total": 0, "embedded": 0, "skipped": 0, "failed": 0,
                      "not_configured": 0, "dropped": 0, "requests": 0, "batches": 0,
                      "dimension_embedded": 0, "dimension_skipped": 0, "dimension_failed": 0,
                      "dimension_requests": 0}

    @property
    def configured(self) -> bool:
        return self.settings is not None and self.settings.configured

    # ---- producer side ----

    def enqueue(self, image_id: int) -> str:
        """Record one image for embedding and return at once. Never raises, never HTTP.
        Returns 'queued' / 'not_configured' / 'stopped' / 'closed'."""
        with self._cond:
            if not self.configured:
                self.stats["not_configured"] += 1
                return "not_configured"
            if self._stopped or self._closing:
                self.stats["dropped"] += 1
                return "stopped" if self._stopped else "closed"
            if image_id not in self._queue:
                self._queue[int(image_id)] = self._clock()
                self.stats["queued_total"] += 1
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._loop, name="embedding-adapter", daemon=True)
                self._thread.start()
            self._cond.notify_all()
            return "queued"

    def drain(self, timeout: float | None = None) -> dict:
        """Send everything queued now and wait until it is committed or failed."""
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._cond:
            self._flush = True
            self._cond.notify_all()
            while (self._queue or self._busy) and self._thread is not None and self._thread.is_alive():
                left = None if deadline is None else deadline - time.monotonic()
                if left is not None and left <= 0:
                    break
                self._cond.wait(0.2 if left is None else min(0.2, left))
            self._flush = False
        return self.state()

    def shutdown(self, timeout: float | None = 30) -> dict:
        state = self.drain(timeout)
        with self._cond:
            self._closing = True
            self._cond.notify_all()
        if self._thread is not None:
            self._thread.join(timeout=5)
        return state

    def state(self) -> dict:
        with self._cond:
            return {
                "configured": self.configured,
                "model": self.spec.model if self.spec else None,
                "dimensions": self.spec.dimensions if self.spec else None,
                "scheme": self.spec.scheme if self.spec else None,
                "dimension_schemes": list(self.dimension_schemes),
                "queued": len(self._queue),
                "busy": self._busy,
                "stopped": self._stopped,
                "last_error": self.last_error,
                **self.stats,
            }

    # ---- consumer thread ----

    def _ready(self) -> bool:
        if not self._queue:
            return False
        if self._flush or self._closing or len(self._queue) >= MAX_BATCH:
            return True
        oldest = next(iter(self._queue.values()))
        return self._clock() - oldest >= self.flush_seconds

    def _take(self) -> list[int] | None:
        """Wait for a ready batch; None means exit."""
        with self._cond:
            while not self._ready():
                if self._closing and not self._queue:
                    return None
                if self._queue:
                    oldest = next(iter(self._queue.values()))
                    wait = max(0.05, self.flush_seconds - (self._clock() - oldest))
                    self._cond.wait(min(wait, 1.0))
                else:
                    self._cond.wait(1.0)
            ids = list(self._queue)[:MAX_BATCH]
            for image_id in ids:
                del self._queue[image_id]
            self._busy = True
            return ids

    def _loop(self) -> None:
        client = None
        try:
            client = self._client_factory(self.settings)
            while True:
                ids = self._take()
                if ids is None:
                    break
                try:
                    self._process(client, ids)
                    if self.dimension_schemes:
                        self._process_dimensions(client, ids)
                finally:
                    with self._cond:
                        self._busy = False
                        self._cond.notify_all()
        except BaseException as exc:
            self._error(f"embedding 线程异常退出：{type(exc).__name__}: {exc}")
        finally:
            if client is not None:
                client.close()
            with self._cond:
                self._busy = False
                self._cond.notify_all()

    def _process_dimensions(self, client: EmbeddingClient, ids: list[int]) -> None:
        """Fill layout/style/description/subject rows for the same done images.

        Idempotent by construction: dimensions.load_pending() skips valid rows, so a
        repeated analysis notification costs no request. A failed scheme is left stale
        for the explicit backfill tool; it never affects the combined search vector.
        """
        if self.settings is None:
            return
        conn = db.connect()
        try:
            for scheme in self.dimension_schemes:
                spec = dimensions.spec_for(self.settings, scheme)
                pending, skipped = dimensions.load_pending(conn, spec, ids)
                with self._cond:
                    self.stats["dimension_skipped"] += skipped
                if not pending:
                    continue
                before = client.requests
                try:
                    vectors = client.embed([p.text for p in pending])
                except EmbeddingError as exc:
                    with self._cond:
                        self.stats["dimension_failed"] += len(pending)
                        self.stats["dimension_requests"] += client.requests - before
                        if exc.kind in STOP_KINDS:
                            self._stopped = True
                            self.stats["dropped"] += len(self._queue)
                            self._queue.clear()
                    self._error(f"dimension {scheme}: {exc.kind}: {exc.message}")
                    return
                try:
                    store.upsert_batch(conn, spec, pending, vectors)
                except Exception as exc:
                    with self._cond:
                        self.stats["dimension_failed"] += len(pending)
                    self._error(f"dimension write {scheme}: {type(exc).__name__}: {exc}")
                    return
                with self._cond:
                    self.stats["dimension_embedded"] += len(pending)
                    self.stats["dimension_requests"] += client.requests - before
        except Exception as exc:
            with self._cond:
                self.stats["dimension_failed"] += len(ids)
            self._error(f"dimension {type(exc).__name__}: {exc}")
        finally:
            conn.close()

    def _error(self, message: str) -> None:
        self.last_error = vision.redact(message)
        log.error("embedding: %s", self.last_error)

    def _process(self, client: EmbeddingClient, ids: list[int]) -> None:
        conn = db.connect()
        try:
            pending, _ = store.load_pending(conn, self.spec, ids)
            skipped = len(ids) - len(pending)
            with self._cond:
                self.stats["skipped"] += skipped
            if not pending:
                return
            before = client.requests
            try:
                vectors = client.embed([p.text for p in pending])
            except EmbeddingError as exc:
                with self._cond:
                    self.stats["failed"] += len(pending)
                    self.stats["requests"] += client.requests - before
                    if exc.kind in STOP_KINDS:
                        self._stopped = True
                        self.stats["dropped"] += len(self._queue)
                        self._queue.clear()
                self._error(f"{exc.kind}: {exc.message}（图片 {[p.image_id for p in pending]}，留待 backfill）")
                return
            with self._cond:
                self.stats["requests"] += client.requests - before
                self.stats["batches"] += 1
            try:
                store.upsert_batch(conn, self.spec, pending, vectors)
            except Exception as exc:
                with self._cond:
                    self.stats["failed"] += len(pending)
                self._error(f"写入失败，已回滚: {type(exc).__name__}: {exc}")
                return
            with self._cond:
                self.stats["embedded"] += len(pending)
        except Exception as exc:  # e.g. DB unreadable: count the batch, keep the thread
            with self._cond:
                self.stats["failed"] += len(ids)
            self._error(f"{type(exc).__name__}: {exc}")
        finally:
            conn.close()
