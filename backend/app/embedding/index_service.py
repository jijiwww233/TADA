"""Incremental embedding index service.

- At most 10 texts per batch request.
- Only missing/stale images are sent; stale now includes corrupt/NaN/zero vectors.
- Error policy (see EmbeddingError.scope):
    global  (auth/permission/rate limit/connect): whole batch fails, 1 request.
    unknown (timeout/protocol/malformed result): whole batch fails, 1 request, no retry.
    input   (definitely one bad input): isolate per image, budget = 1 + len(batch).
- Persistence boundary: the in-memory "already valid" cache is updated only after
  persist() succeeded (or via mark_persisted() after the caller committed its SQL).
  If persist fails, the row stays stale and the next call requests it again.
- Duplicate image_id in one call is collapsed to the first task.
- A process-wide per-image lock is shared by every EmbeddingIndexService instance, so an
  analysis callback and a backfill entry in the same process cannot process the same
  image concurrently. Cross-process safety still relies on the DB unique row.
"""
from __future__ import annotations

import math
import threading
from contextlib import ExitStack
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping, Sequence

from .client import (EmbeddingClient, EmbeddingError, SCOPE_INPUT, SCOPE_UNKNOWN)
from .service import (EmbeddingSpec, blob_to_vector, needs_rebuild)
from .text import build_combined_d_v1, text_hash

MAX_BATCH = 10
BATCH_REQUEST_BUDGET = 1 + MAX_BATCH  # one batch + at most one request per item

_LOCKS_GUARD = threading.Lock()
_IMAGE_LOCKS: dict[int, threading.RLock] = {}


def image_lock(image_id: int) -> threading.RLock:
    """Process-wide lock shared by analysis callback + backfill entry points."""
    with _LOCKS_GUARD:
        lock = _IMAGE_LOCKS.get(int(image_id))
        if lock is None:
            lock = threading.RLock()
            _IMAGE_LOCKS[int(image_id)] = lock
        return lock


@dataclass(frozen=True)
class IndexTask:
    image_id: int
    analysis: Mapping[str, Any]
    colors: Iterable[Any] = field(default_factory=tuple)


@dataclass(frozen=True)
class IndexedEmbedding:
    image_id: int
    vector: tuple[float, ...]
    model: str
    dimension: str          # DB embeddings.dimension == spec.scheme
    dimensions: int         # DB embeddings.dimensions == vector size
    text_hash: str

    @classmethod
    def from_mapping(cls, row: Mapping[str, Any]) -> "IndexedEmbedding":
        """The ONLY dict -> row adapter (DB dimension stays the scheme field)."""
        if not isinstance(row, Mapping):
            raise TypeError("existing 只接受 IndexedEmbedding 或 Mapping（dict）")
        dimensions = int(row.get("dimensions") or 0)
        raw = row.get("vector")
        if isinstance(raw, (bytes, bytearray, memoryview)):
            try:
                vector: tuple[float, ...] = tuple(blob_to_vector(bytes(raw), dimensions))
            except (TypeError, ValueError):
                vector = ()  # corrupt: needs_rebuild() sees an empty/invalid vector
        elif raw is None:
            vector = ()
        else:
            try:
                vector = tuple(float(value) for value in raw)
            except (TypeError, ValueError):
                vector = ()
        return cls(
            image_id=int(row["image_id"]),
            vector=vector,
            model=str(row.get("model") or ""),
            dimension=str(row.get("dimension") or ""),
            dimensions=dimensions,
            text_hash=str(row.get("text_hash") or ""),
        )


@dataclass(frozen=True)
class IndexFailure:
    image_id: int
    kind: str
    error: str
    scope: str = SCOPE_UNKNOWN


@dataclass(frozen=True)
class IndexReport:
    indexed: int
    skipped: int
    failed: int
    failures: tuple[IndexFailure, ...]
    rows: tuple[IndexedEmbedding, ...]
    requests: int = 0
    duplicates: int = 0


class EmbeddingIndexService:
    def __init__(self, client: EmbeddingClient, spec: EmbeddingSpec | None = None,
                 existing: Iterable[IndexedEmbedding | Mapping[str, Any]] = (),
                 existing_provider: Callable[[int], IndexedEmbedding | Mapping[str, Any] | None] | None = None):
        self.client = client
        self.existing_provider = existing_provider
        settings = getattr(client, "settings", None)
        if spec is None:
            if settings is None:
                raise ValueError("client 没有 settings 时必须显式传入 spec")
            spec = EmbeddingSpec.from_settings(settings)
        elif settings is not None:
            _assert_same_config(settings, spec)
        self.spec = spec
        self._rows: dict[int, IndexedEmbedding] = {}
        for row in existing:
            coerced = self._coerce(row)
            self._rows[coerced.image_id] = coerced
        self._requests = 0

    @classmethod
    def from_settings(cls, settings: Any,
                      existing: Iterable[IndexedEmbedding | Mapping[str, Any]] = ()) -> "EmbeddingIndexService":
        from .client import EmbeddingClient

        client = EmbeddingClient(settings)
        return cls(client, EmbeddingSpec.from_settings(settings), existing=existing)

    @property
    def rows(self) -> dict[int, IndexedEmbedding]:
        return dict(self._rows)

    def mark_persisted(self, rows: Iterable[IndexedEmbedding]) -> None:
        """Call ONLY after the caller's SQL transaction committed."""
        for row in rows:
            self._rows[row.image_id] = row

    def index(self, tasks: Sequence[IndexTask],
              persist: Callable[[IndexedEmbedding], None] | None = None) -> IndexReport:
        """Index tasks. persist(row) must write+commit one row; cache updates after it.

        Without persist, rows are returned but not cached; call mark_persisted() after
        committing them, otherwise the next call requests the same images again.
        """
        unique: dict[int, IndexTask] = {}
        for task in tasks:
            unique.setdefault(int(task.image_id), task)
        duplicates = len(tasks) - len(unique)
        start_requests = self._requests

        rows: list[IndexedEmbedding] = []
        failures: list[IndexFailure] = []
        skipped = 0
        with ExitStack() as stack:
            for image_id in sorted(unique):
                stack.enter_context(image_lock(image_id))
            prepared: list[tuple[IndexTask, str, str]] = []
            for task in unique.values():
                image_id = int(task.image_id)
                if self.existing_provider is not None:
                    # Re-read the committed row while holding the image lock: a second
                    # entry point (backfill vs analysis callback) sees the first commit.
                    fresh = self.existing_provider(image_id)
                    if fresh is None:
                        self._rows.pop(image_id, None)
                    else:
                        self._rows[image_id] = self._coerce(fresh)
                text = build_combined_d_v1(task.analysis, task.colors)
                if not text:
                    skipped += 1
                    continue
                current_hash = text_hash(text)
                existing = self._rows.get(int(task.image_id))
                if existing and not needs_rebuild(_as_existing(existing, self.spec), self.spec, current_hash):
                    skipped += 1
                    continue
                prepared.append((task, text, current_hash))

            for start in range(0, len(prepared), MAX_BATCH):
                batch = prepared[start:start + MAX_BATCH]
                vectors = self._embed_batch(
                    [(int(task.image_id), text) for task, text, _ in batch], failures)
                for (task, text, current_hash), vector in zip(batch, vectors):
                    if vector is None:
                        continue
                    item = IndexedEmbedding(
                        image_id=int(task.image_id),
                        vector=tuple(vector),
                        model=self.spec.model,
                        dimension=self.spec.scheme,
                        dimensions=self.spec.dimensions,
                        text_hash=current_hash,
                    )
                    if persist is not None:
                        try:
                            persist(item)
                        except Exception as exc:
                            failures.append(IndexFailure(
                                image_id=item.image_id, kind="store",
                                error=f"{type(exc).__name__}: {exc}", scope="store"))
                            continue
                    rows.append(item)
                    if persist is not None:
                        self._rows[item.image_id] = item
        return IndexReport(
            indexed=len(rows), skipped=skipped, failed=len(failures),
            failures=tuple(failures), rows=tuple(rows),
            requests=self._requests - start_requests, duplicates=duplicates,
        )

    def _coerce(self, row: IndexedEmbedding | Mapping[str, Any]) -> IndexedEmbedding:
        if isinstance(row, IndexedEmbedding):
            return row
        return IndexedEmbedding.from_mapping(row)

    def _embed_batch(self, batch: list[tuple[int, str]],
                     failures: list[IndexFailure]) -> list[list[float] | None]:
        try:
            self._requests += 1
            vectors = self.client.embed([text for _, text in batch])
        except EmbeddingError as exc:
            if exc.scope != SCOPE_INPUT:
                failures.extend(IndexFailure(image_id=image_id, kind=exc.kind,
                                             error=exc.message, scope=exc.scope)
                                for image_id, _ in batch)
                return [None] * len(batch)
            return self._isolate(batch, failures)

        if len(vectors) != len(batch):
            failures.extend(IndexFailure(image_id=image_id, kind="count",
                                         error=f"返回数量不符: {len(vectors)} != {len(batch)}",
                                         scope=SCOPE_UNKNOWN)
                            for image_id, _ in batch)
            return [None] * len(batch)
        out: list[list[float] | None] = []
        try:
            for vector in vectors:
                out.append(self._validate_vector(vector))
            return out
        except EmbeddingError as exc:
            failures.extend(IndexFailure(image_id=image_id, kind=exc.kind,
                                         error=exc.message, scope=SCOPE_UNKNOWN)
                            for image_id, _ in batch)
            return [None] * len(batch)

    def _isolate(self, batch: list[tuple[int, str]],
                 failures: list[IndexFailure]) -> list[list[float] | None]:
        """Only for scope=input: at most 1 batch + len(batch) single requests."""
        out: list[list[float] | None] = [None] * len(batch)
        attempted = 0
        for index, (image_id, text) in enumerate(batch):
            if attempted >= BATCH_REQUEST_BUDGET - 1:
                for rest_id, _ in batch[index:]:
                    failures.append(IndexFailure(
                        image_id=rest_id, kind="budget",
                        error=f"单批请求预算 {BATCH_REQUEST_BUDGET} 已用完", scope=SCOPE_UNKNOWN))
                break
            attempted += 1
            try:
                self._requests += 1
                vector = self.client.embed([text])[0]
            except EmbeddingError as exc:
                failures.append(IndexFailure(image_id=image_id, kind=exc.kind,
                                             error=exc.message, scope=exc.scope))
                if exc.scope != SCOPE_INPUT:
                    for rest_id, _ in batch[index + 1:]:
                        failures.append(IndexFailure(
                            image_id=rest_id, kind=exc.kind, error=exc.message,
                            scope=exc.scope))
                    break
                continue
            try:
                out[index] = self._validate_vector(vector)
            except EmbeddingError as exc:
                failures.append(IndexFailure(image_id=image_id, kind=exc.kind,
                                             error=exc.message, scope=SCOPE_UNKNOWN))
        return out

    def _validate_vector(self, vector: Sequence[float]) -> list[float]:
        try:
            values = [float(value) for value in vector]
        except (TypeError, ValueError) as exc:
            raise EmbeddingError("value", "向量含非数值", scope=SCOPE_UNKNOWN) from exc
        if len(values) != self.spec.dimensions:
            raise EmbeddingError("dimension", f"向量维度不符: {len(values)} != {self.spec.dimensions}")
        if any(not math.isfinite(value) for value in values):
            raise EmbeddingError("nonfinite", "向量含 NaN/Inf")
        if math.sqrt(sum(value * value for value in values)) <= 1e-12:
            raise EmbeddingError("zero_norm", "向量是零向量")
        return values


def _as_existing(row: IndexedEmbedding, spec: EmbeddingSpec) -> dict[str, Any]:
    # DB has no text_version column: scheme IS the text version, so take it from spec.
    return {
        "model": row.model,
        "dimensions": row.dimensions,
        "scheme": row.dimension,
        "text_version": spec.text_version,
        "text_hash": row.text_hash,
        "vector": row.vector,
    }


def _assert_same_config(settings: Any, spec: EmbeddingSpec) -> None:
    pairs = (("model", settings.model, spec.model),
             ("dimensions", settings.dimensions, spec.dimensions),
             ("scheme", settings.scheme, spec.scheme),
             ("text_version", settings.text_version, spec.text_version))
    for name, left, right in pairs:
        if left != right:
            raise ValueError(f"client/spec 配置不一致: {name} {left!r} != {right!r}")
