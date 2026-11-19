"""Application service/repository for post-level collection classification.

Automatic classification and manual overrides live in separate tables:
- post_classifications: last successful automatic result; reanalysis may replace it
- post_classification_manual: user override; automatic code never writes/deletes it
Effective category = manual if present, else automatic, else uncertain.

Images inherit classification through their source posts (post_images). The same image
row can be linked to multiple posts and therefore appear with different effective source
categories; an image is returned once, with only visible sources for the requested surface.

No model calls happen here beyond an explicitly supplied backend. There is no startup
hook or automatic classification of existing analyses.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from .classification_backend import classify_many_cached
from .classification_visibility import visible_image_sources
from .collection_classification import (
    CATEGORIES,
    ClassificationState,
    _category,
    effective_category,
    should_include,
)


class ClassificationServiceError(RuntimeError):
    pass


def _json(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, (list, dict)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _post_ids(ids: Iterable[int]) -> list[int]:
    out: list[int] = []
    for value in ids:
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError("post_id must be a positive integer")
        if value not in out:
            out.append(value)
    return out


def _surface(surface: str) -> str:
    if surface not in {"library", "design_workspace", "search", "recommendation", "life", "uncertain"}:
        raise ValueError("unknown collection surface")
    return surface


def build_post_document(conn: sqlite3.Connection, post_id: int) -> dict[str, Any] | None:
    """Read existing post text + analyses only; no image bytes/OCR/model call."""
    post = conn.execute(
        "SELECT id, title, description, platform, is_video FROM posts WHERE id = ?", (post_id,)
    ).fetchone()
    if post is None:
        return None
    rows = conn.execute(
        """SELECT pi.position, a.status, a.subject, a.description, a.layout_json,
                  a.style_json, a.visible_text
           FROM post_images pi
           LEFT JOIN analyses a ON a.image_id = pi.image_id
           WHERE pi.post_id = ?
           ORDER BY pi.position""",
        (post_id,),
    ).fetchall()
    images = []
    for row in rows:
        if row["status"] != "done":
            images.append({})
            continue
        images.append({
            "subject": _json(row["subject"], []),
            "description": row["description"],
            "layout": _json(row["layout_json"], {}),
            "style": _json(row["style_json"], {}),
            "visible_text": row["visible_text"],
        })
    return {
        "post": {
            "title": post["title"] or "",
            "description": post["description"] or "",
            "platform": post["platform"] or "",
            "is_video": bool(post["is_video"]),
        },
        "images": images,
    }


def load_automatic(conn: sqlite3.Connection, post_ids: Sequence[int]) -> dict[int, dict[str, Any]]:
    ids = _post_ids(post_ids)
    if not ids:
        return {}
    placeholders = ",".join("?" for _ in ids)
    rows = conn.execute(
        f"""SELECT post_id, category, confidence, status, evidence_json,
                   ignored_evidence_json, reasons_json, classifier_version, input_hash,
                   scope, computed_at, updated_at
            FROM post_classifications WHERE post_id IN ({placeholders})""",
        ids,
    ).fetchall()
    result = {}
    for row in rows:
        result[int(row["post_id"])] = {
            "category": row["category"],
            "confidence": row["confidence"],
            "status": row["status"],
            "evidence": _json(row["evidence_json"], []),
            "ignored_evidence": _json(row["ignored_evidence_json"], []),
            "reasons": _json(row["reasons_json"], []),
            "classifier_version": row["classifier_version"],
            "input_hash": row["input_hash"],
            "scope": row["scope"],
            "computed_at": row["computed_at"],
            "updated_at": row["updated_at"],
        }
    return result


def load_manual(conn: sqlite3.Connection, post_ids: Sequence[int]) -> dict[int, dict[str, Any]]:
    ids = _post_ids(post_ids)
    if not ids:
        return {}
    placeholders = ",".join("?" for _ in ids)
    rows = conn.execute(
        f"""SELECT post_id, category, note, updated_at
            FROM post_classification_manual WHERE post_id IN ({placeholders})""",
        ids,
    ).fetchall()
    return {
        int(row["post_id"]): {"category": row["category"], "note": row["note"],
                              "updated_at": row["updated_at"]}
        for row in rows
    }


def _same_automatic(previous: Mapping[str, Any] | None, result: Mapping[str, Any]) -> bool:
    if not previous:
        return False
    return all(previous.get(key) == result.get(key) for key in
               ("category", "confidence", "input_hash", "classifier_version", "status"))


def _write_automatic(conn: sqlite3.Connection, post_id: int, result: Mapping[str, Any]) -> None:
    conn.execute(
        """INSERT INTO post_classifications
               (post_id, category, confidence, status, evidence_json, ignored_evidence_json,
                reasons_json, classifier_version, input_hash, scope, computed_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
           ON CONFLICT(post_id) DO UPDATE SET
               category = excluded.category,
               confidence = excluded.confidence,
               status = excluded.status,
               evidence_json = excluded.evidence_json,
               ignored_evidence_json = excluded.ignored_evidence_json,
               reasons_json = excluded.reasons_json,
               classifier_version = excluded.classifier_version,
               input_hash = excluded.input_hash,
               scope = excluded.scope,
               updated_at = datetime('now')""",
        (
            post_id,
            result["category"],
            float(result.get("confidence") or 0.0),
            "ok",
            json.dumps(result.get("evidence", []), ensure_ascii=False),
            json.dumps(result.get("ignored_evidence", []), ensure_ascii=False),
            json.dumps(result.get("reasons", []), ensure_ascii=False),
            str(result.get("classifier_version") or ""),
            str(result.get("input_hash") or ""),
            result.get("scope"),
        ),
    )


@dataclass
class ClassificationRun:
    requested: int
    ok: int
    backend_error: int
    updated: int
    errors: int
    error: str | None = None


def classify_posts(conn: sqlite3.Connection, post_ids: Sequence[int], backend: Any) -> ClassificationRun:
    """Classify docs through the existing cached backend API, then persist ONLY ok rows.

    Manual overrides are never read for this write path and never modified. Backend
    failures leave the previous automatic row untouched (and do not create a new one).
    """
    ids = _post_ids(post_ids)
    if not ids:
        return ClassificationRun(0, 0, 0, 0, 0)
    documents = []
    for post_id in ids:
        document = build_post_document(conn, post_id)
        if document is None:
            raise ClassificationServiceError(f"post {post_id} does not exist")
        documents.append(document)
    automatic = load_automatic(conn, ids)
    previous = [automatic.get(post_id) for post_id in ids]
    try:
        results = classify_many_cached(documents, backend, previous=previous)
    except Exception:
        # Fixed local error: never leak backend/provider messages into persistence/logs.
        return ClassificationRun(len(ids), 0, len(ids), 0, len(ids), "backend_failed")
    ok = 0
    backend_error = 0
    updated = 0
    try:
        conn.execute("BEGIN IMMEDIATE")
        for post_id, result in zip(ids, results):
            if result.get("status") != "ok":
                backend_error += 1
                continue
            ok += 1
            if _same_automatic(automatic.get(post_id), result):
                continue
            _write_automatic(conn, post_id, result)
            updated += 1
        conn.execute("COMMIT")
    except Exception:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    return ClassificationRun(len(ids), ok, backend_error, updated, backend_error)


def set_manual(conn: sqlite3.Connection, post_id: int, category: str, note: str | None = None) -> None:
    _category(category)
    if conn.execute("SELECT 1 FROM posts WHERE id = ?", (post_id,)).fetchone() is None:
        raise ClassificationServiceError(f"post {post_id} does not exist")
    conn.execute(
        """INSERT INTO post_classification_manual (post_id, category, note, updated_at)
           VALUES (?, ?, ?, datetime('now'))
           ON CONFLICT(post_id) DO UPDATE SET
               category = excluded.category,
               note = excluded.note,
               updated_at = datetime('now')""",
        (post_id, category, note),
    )


def clear_manual(conn: sqlite3.Connection, post_id: int) -> None:
    conn.execute("DELETE FROM post_classification_manual WHERE post_id = ?", (post_id,))


def effective_post_states(conn: sqlite3.Connection, post_ids: Sequence[int],
                          ) -> dict[int, ClassificationState]:
    ids = _post_ids(post_ids)
    automatic = load_automatic(conn, ids)
    manual = load_manual(conn, ids)
    return {
        post_id: ClassificationState(
            automatic=automatic.get(post_id),
            manual=manual.get(post_id, {}).get("category"),
        )
        for post_id in ids
    }


def effective_post_categories(conn: sqlite3.Connection, post_ids: Sequence[int],
                              ) -> dict[int, dict[str, Any]]:
    states = effective_post_states(conn, post_ids)
    return {post_id: effective_category(state) for post_id, state in states.items()}


def _image_rows(conn: sqlite3.Connection, image_ids: Sequence[int] | None,
                limit: int | None, offset: int) -> list[sqlite3.Row]:
    sql = ("SELECT id, sha256, file_path, width, height, mime, original_name "
           "FROM images")
    args: list[Any] = []
    if image_ids is not None:
        ids = _post_ids(image_ids)
        if not ids:
            return []
        sql += " WHERE id IN (" + ",".join("?" for _ in ids) + ")"
        args.extend(ids)
    sql += " ORDER BY id DESC"
    if limit is not None:
        sql += " LIMIT ? OFFSET ?"
        args.extend([int(limit), int(offset)])
    return conn.execute(sql, args).fetchall()


def _sources_for(conn: sqlite3.Connection, image_ids: Sequence[int]) -> dict[int, list[dict[str, Any]]]:
    if not image_ids:
        return {}
    placeholders = ",".join("?" for _ in image_ids)
    rows = conn.execute(
        f"""SELECT pi.image_id, pi.post_id, pi.position, p.platform
            FROM post_images pi JOIN posts p ON p.id = pi.post_id
            WHERE pi.image_id IN ({placeholders})
            ORDER BY pi.image_id, pi.position""",
        list(image_ids),
    ).fetchall()
    result: dict[int, list[dict[str, Any]]] = {image_id: [] for image_id in image_ids}
    for row in rows:
        result[int(row["image_id"])].append({
            "post_id": int(row["post_id"]), "position": int(row["position"]),
            "platform": row["platform"],
        })
    return result


def visible_images(conn: sqlite3.Connection, surface: str, *, limit: int | None = 50,
                   offset: int = 0, image_ids: Sequence[int] | None = None) -> list[dict[str, Any]]:
    """Source-scoped library rows; filtering never deletes or rewrites classification."""
    _surface(surface)
    rows = _image_rows(conn, image_ids, limit, offset)
    ids = [int(row["id"]) for row in rows]
    sources = _sources_for(conn, ids)
    post_ids = sorted({source["post_id"] for values in sources.values() for source in values})
    states = effective_post_states(conn, post_ids)
    images = []
    for row in rows:
        image = {key: row[key] for key in ("id", "sha256", "file_path", "width", "height",
                                           "mime", "original_name")}
        image["image_id"] = int(row["id"])
        image["sources"] = sources.get(int(row["id"]), [])
        images.append(image)
    visible = visible_image_sources(images, states, surface)
    for image in visible:
        image.pop("id", None)
    return visible


def visible_image_ids(conn: sqlite3.Connection, surface: str,
                      image_ids: Sequence[int] | None = None) -> list[int]:
    return [int(item["image_id"]) for item in
            visible_images(conn, surface, limit=None, image_ids=image_ids)]


def filter_image_ids(conn: sqlite3.Connection, surface: str,
                     image_ids: Sequence[int]) -> set[int]:
    return set(visible_image_ids(conn, surface, image_ids=image_ids))


class ClassificationService:
    """Small application facade; use one connection per request/worker unit."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def classify(self, post_ids: Sequence[int], backend: Any) -> ClassificationRun:
        return classify_posts(self.conn, post_ids, backend)

    def set_manual(self, post_id: int, category: str, note: str | None = None) -> None:
        set_manual(self.conn, post_id, category, note)

    def clear_manual(self, post_id: int) -> None:
        clear_manual(self.conn, post_id)

    def effective(self, post_ids: Sequence[int]) -> dict[int, dict[str, Any]]:
        return effective_post_categories(self.conn, post_ids)

    def visible_images(self, surface: str, *, limit: int | None = 50, offset: int = 0,
                       image_ids: Sequence[int] | None = None) -> list[dict[str, Any]]:
        return visible_images(self.conn, surface, limit=limit, offset=offset,
                              image_ids=image_ids)

    def filter_image_ids(self, surface: str, image_ids: Sequence[int]) -> set[int]:
        return filter_image_ids(self.conn, surface, image_ids)
