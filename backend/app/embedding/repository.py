"""Read/write helpers for the corrected embeddings table (no schema changes here).

Expected columns after applying backend/migrations/2026-11-04-embedding-core.sql:
  embeddings(image_id, dimension='combined_d_v1', model, vector,
             dimensions INTEGER, text_hash TEXT)

The module deliberately has no import of db.py and never migrates; it only runs SQL.
"""
from __future__ import annotations

import sqlite3
from typing import Any, Iterable, Mapping

from .index_service import IndexedEmbedding
from .search_service import SearchRow
from .service import EmbeddingSpec, vector_to_blob
from .text import build_combined_d_v1, text_hash


class RepositoryError(RuntimeError):
    pass


def _require_columns(conn: sqlite3.Connection) -> None:
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(embeddings)")}
    except sqlite3.DatabaseError as exc:
        raise RepositoryError(f"读取 embeddings 表失败: {exc}") from exc
    missing = {"dimensions", "text_hash"} - columns
    if missing:
        raise RepositoryError(
            "embeddings 表缺少 " + ", ".join(sorted(missing)) +
            "；请先应用 backend/migrations/2026-11-04-embedding-core.sql 的迁移"
        )


def indexed_from_row(row: Mapping[str, Any], spec: EmbeddingSpec) -> IndexedEmbedding:
    """The single DB-row -> IndexedEmbedding adapter.

    embeddings.dimension is the scheme; it is passed through as IndexedEmbedding.dimension.
    Do not hand a raw sqlite row to EmbeddingIndexService: this adapter also decodes the
    BLOB and preserves corrupt rows as an empty vector (needs_rebuild=True).
    """
    if not isinstance(row, Mapping):
        row = dict(row)
    return IndexedEmbedding.from_mapping(row)


def load_embeddings(conn: sqlite3.Connection, spec: EmbeddingSpec) -> list[IndexedEmbedding]:
    _require_columns(conn)
    rows = conn.execute(
        """SELECT image_id, model, dimension, dimensions, text_hash, vector
           FROM embeddings
           WHERE dimension = ? AND model = ? AND dimensions = ?""",
        (spec.scheme, spec.model, spec.dimensions),
    ).fetchall()
    result: list[IndexedEmbedding] = []
    for row in rows:
        indexed = indexed_from_row(row, spec)
        if indexed.vector:
            result.append(indexed)
        # corrupt/empty BLOB is deliberately omitted here: the index service then sees
        # no row and rebuilds it instead of leaving it permanently excluded from search.
    return result


def _analysis_mapping(row: Any) -> dict[str, Any]:
    return {
        "visible_text": row["visible_text"],
        "subject": row["subject"],
        "layout_json": row["layout_json"],
        "style_json": row["style_json"],
        "description": row["description"],
    }


def _colors_for(conn: sqlite3.Connection, image_ids: list[int]) -> dict[int, list[dict]]:
    result: dict[int, list[dict]] = {image_id: [] for image_id in image_ids}
    for start in range(0, len(image_ids), 500):
        chunk = image_ids[start:start + 500]
        placeholders = ",".join("?" for _ in chunk)
        rows = conn.execute(
            f"""SELECT image_id, hex, ratio, rank FROM colors
                WHERE image_id IN ({placeholders}) ORDER BY image_id, rank""",
            chunk,
        ).fetchall()
        for row in rows:
            result.setdefault(int(row["image_id"]), []).append(
                {"hex": row["hex"], "ratio": row["ratio"], "rank": row["rank"]}
            )
    return result


def load_current_text_hashes(conn: sqlite3.Connection, image_ids: Iterable[int],
                             spec: EmbeddingSpec) -> dict[int, str]:
    ids = [int(image_id) for image_id in image_ids]
    hashes: dict[int, str] = {}
    colors = _colors_for(conn, ids)
    for start in range(0, len(ids), 500):
        chunk = ids[start:start + 500]
        placeholders = ",".join("?" for _ in chunk)
        rows = conn.execute(
            f"""SELECT image_id, visible_text, subject, layout_json, style_json, description
                FROM analyses WHERE status = 'done' AND image_id IN ({placeholders})""",
            chunk,
        ).fetchall()
        for row in rows:
            image_id = int(row["image_id"])
            text = build_combined_d_v1(_analysis_mapping(row), colors.get(image_id, []))
            if text:
                hashes[image_id] = text_hash(text)
    return hashes


def _visible_image_ids(conn: sqlite3.Connection, ids: list[int]) -> set[int]:
    """Image ids that are not soft-deleted. Pre-v15 databases (no deleted_at column,
    e.g. a raw fixture) keep every row visible."""
    if not ids:
        return set()
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(images)")}
    except sqlite3.DatabaseError:
        return set(ids)
    if "deleted_at" not in columns:
        return set(ids)
    visible: set[int] = set()
    for start in range(0, len(ids), 500):
        chunk = ids[start:start + 500]
        placeholders = ",".join("?" for _ in chunk)
        visible.update(row[0] for row in conn.execute(
            f"SELECT id FROM images WHERE id IN ({placeholders}) AND deleted_at IS NULL", chunk))
    return visible


def load_search_rows(conn: sqlite3.Connection, spec: EmbeddingSpec) -> list[SearchRow]:
    embeddings = load_embeddings(conn, spec)
    if embeddings:
        visible = _visible_image_ids(conn, [row.image_id for row in embeddings])
        embeddings = [row for row in embeddings if row.image_id in visible]
    hashes = load_current_text_hashes(conn, [row.image_id for row in embeddings], spec)
    rows: list[SearchRow] = []
    for embedding in embeddings:
        current_hash = hashes.get(embedding.image_id)
        if not current_hash:
            continue
        rows.append(SearchRow(
            image_id=embedding.image_id,
            vector=list(embedding.vector),
            model=embedding.model,
            dimension=embedding.dimension,
            dimensions=embedding.dimensions,
            text_hash=embedding.text_hash,
            current_text_hash=current_hash,
        ))
    return rows


def upsert_embedding(conn: sqlite3.Connection, row: IndexedEmbedding) -> None:
    _require_columns(conn)
    conn.execute(
        """INSERT INTO embeddings (image_id, dimension, model, vector, dimensions, text_hash)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(image_id, dimension) DO UPDATE SET
               model = excluded.model,
               vector = excluded.vector,
               dimensions = excluded.dimensions,
               text_hash = excluded.text_hash""",
        (row.image_id, row.dimension, row.model, vector_to_blob(list(row.vector)),
         row.dimensions, row.text_hash),
    )
