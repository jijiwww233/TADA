"""DB side of the combined embedding: what is stale, and how a batch is stored.

No network here. The adapter (adapter.py) is the only caller that embeds; backfill
and the analyses.done notification both go through it, so they share is_valid().

A row counts as valid only if model / scheme / dimensions / text_hash all match the
current config and text, AND the BLOB decodes to `dimensions` float32 values that are
all finite with a non-zero norm. Anything else (missing row, legacy NULL columns,
NaN / Inf / zero vector, truncated BLOB) is stale and gets rebuilt.

Only analyses.status = 'done' rows are embedded. Nothing here writes to analyses.
"""
from __future__ import annotations

import math
import sqlite3
import struct
from dataclasses import dataclass
from typing import Iterable

from . import text as text_builder
from .service import EmbeddingSpec, vector_to_blob


@dataclass(frozen=True)
class Pending:
    """One image whose embedding must be (re)built."""
    image_id: int
    text: str
    text_hash: str
    reason: str  # missing / model / scheme / dimensions / text_hash / vector


def vector_problem(blob: bytes | None, dimensions: int | None) -> str | None:
    """None if the BLOB is a usable float32 vector of `dimensions` values."""
    if not isinstance(blob, (bytes, bytearray)) or not dimensions or len(blob) != 4 * dimensions:
        return "length"
    values = struct.unpack("<" + "f" * dimensions, blob)
    if not all(math.isfinite(v) for v in values):
        return "nonfinite"
    if math.sqrt(sum(v * v for v in values)) <= 1e-12:
        return "zero_norm"
    return None


def stale_reason(row: sqlite3.Row | dict | None, spec: EmbeddingSpec, current_hash: str) -> str | None:
    """None if the stored row is valid for this spec and text; otherwise why it is stale."""
    if row is None:
        return "missing"
    if row["model"] != spec.model:
        return "model"
    if row["dimension"] != spec.scheme:
        return "scheme"
    if row["dimensions"] != spec.dimensions:
        return "dimensions"
    if row["text_hash"] != current_hash:
        return "text_hash"
    if vector_problem(row["vector"], spec.dimensions):
        return "vector"
    return None


def is_valid(row, spec: EmbeddingSpec, current_hash: str) -> bool:
    return stale_reason(row, spec, current_hash) is None


def _done_rows(conn: sqlite3.Connection, image_ids: Iterable[int] | None):
    sql = """SELECT image_id, visible_text, subject, layout_json, style_json, description
             FROM analyses WHERE status = 'done'"""
    if image_ids is None:
        return conn.execute(sql + " ORDER BY image_id").fetchall()
    ids = sorted({int(i) for i in image_ids})
    if not ids:
        return []
    marks = ",".join("?" * len(ids))
    return conn.execute(sql + f" AND image_id IN ({marks}) ORDER BY image_id", ids).fetchall()


def load_pending(conn: sqlite3.Connection, spec: EmbeddingSpec,
                 image_ids: Iterable[int] | None = None) -> tuple[list[Pending], int]:
    """Build the combined_d_v1 text for done analyses (all of them, or just image_ids)
    and keep the ones whose stored embedding is stale.
    Returns (pending, skipped); skipped = done but already valid, or no text at all.
    Ids that are not done are silently ignored."""
    pending: list[Pending] = []
    skipped = 0
    for a in _done_rows(conn, image_ids):
        image_id = a["image_id"]
        colors = [dict(c) for c in conn.execute(
            "SELECT rank, hex, ratio FROM colors WHERE image_id = ? ORDER BY rank", (image_id,))]
        text = text_builder.build_combined_d_v1(dict(a), colors)
        if not text:
            skipped += 1
            continue
        current_hash = text_builder.text_hash(text)
        row = conn.execute(
            """SELECT model, dimension, dimensions, text_hash, vector FROM embeddings
               WHERE image_id = ? AND dimension = ?""", (image_id, spec.scheme)).fetchone()
        reason = stale_reason(row, spec, current_hash)
        if reason is None:
            skipped += 1
        else:
            pending.append(Pending(image_id, text, current_hash, reason))
    return pending, skipped


def upsert_batch(conn: sqlite3.Connection, spec: EmbeddingSpec,
                 items: list[Pending], vectors: list[list[float]]) -> None:
    """Store one embedded batch in a single transaction. Returning means committed;
    any error rolls back and leaves every previous row as it was."""
    if len(items) != len(vectors):
        raise ValueError(f"向量数量不符: {len(vectors)} != {len(items)}")
    conn.execute("BEGIN IMMEDIATE")
    try:
        for item, vector in zip(items, vectors):
            conn.execute(
                """INSERT INTO embeddings (image_id, dimension, model, vector, dimensions, text_hash)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT (image_id, dimension) DO UPDATE SET
                       model = excluded.model, vector = excluded.vector,
                       dimensions = excluded.dimensions, text_hash = excluded.text_hash""",
                (item.image_id, spec.scheme, spec.model, vector_to_blob(vector),
                 spec.dimensions, item.text_hash),
            )
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise


def counts(conn: sqlite3.Connection, spec: EmbeddingSpec) -> dict:
    """done / valid / stale (by reason) under the current spec. Read-only, no network."""
    pending, skipped = load_pending(conn, spec)
    reasons: dict[str, int] = {}
    for p in pending:
        reasons[p.reason] = reasons.get(p.reason, 0) + 1
    lengths = [len(p.text) for p in pending]
    done = conn.execute("SELECT COUNT(*) FROM analyses WHERE status = 'done'").fetchone()[0]
    return {
        "done": done,
        "valid_or_no_text": skipped,
        "stale": len(pending),
        "stale_reasons": reasons,
        "text_chars_total": sum(lengths),
        "text_chars_max": max(lengths, default=0),
        "pending": pending,
    }
