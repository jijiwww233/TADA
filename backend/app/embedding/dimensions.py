"""Per-dimension embeddings (original plan): subject / layout / style / description.

One text per image per dimension, built deterministically from the existing analyses
row (no new vision calls). Rows live in the existing embeddings table, told apart by
`dimension` = scheme name; combined_d_v1 rows are untouched and remain the search index.

  subject_v1      主题标签 + visible_text 摘录（与 combined_d_v1 同一摘录规则）
  layout_v1       结构化排版字段（中文）+ layout_note；unknown 不写
  style_v1        style tags + medium + style_note
  description_v1  整体 description

Colors are not embedded: the palette column uses Pillow hex/ratio directly.
"""
from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from . import text as T
from .service import EmbeddingSpec
from .store import Pending, stale_reason, vector_problem


def _subject(a: Mapping[str, Any]) -> str:
    lines = []
    subject = "、".join(T._items(a.get("subject")))
    if subject:
        lines.append("主题：" + subject)
    visible = T.excerpt_visible_text(a.get("visible_text"))
    if visible:
        lines.append("图内文字：" + visible)
    return "\n".join(lines)


def _layout(a: Mapping[str, Any]) -> str:
    line = T._layout_line(T._mapping(a.get("layout") or a.get("layout_json")))
    return "排版：" + line if line else ""


def _style(a: Mapping[str, Any]) -> str:
    line = T._style_line(T._mapping(a.get("style") or a.get("style_json")))
    return "风格：" + line if line else ""


def _description(a: Mapping[str, Any]) -> str:
    description = T._labeled(a.get("description") or "")
    return "描述：" + description if description else ""


BUILDERS: dict[str, Callable[[Mapping[str, Any]], str]] = {
    "subject_v1": _subject,
    "layout_v1": _layout,
    "style_v1": _style,
    "description_v1": _description,
}
SCHEMES = tuple(BUILDERS)


def build_text(scheme: str, analysis: Mapping[str, Any]) -> str:
    return BUILDERS[scheme](T.mapping_or_empty(analysis))


def text_hash(scheme: str, text: str) -> str:
    return hashlib.sha256((scheme + "\n" + (text or "")).encode("utf-8")).hexdigest()


def spec_for(settings: Any, scheme: str) -> EmbeddingSpec:
    """Same model / dimensions as the configured embedding; only the scheme differs."""
    if scheme not in BUILDERS:
        raise ValueError(f"unknown scheme {scheme!r}")
    return EmbeddingSpec(model=settings.model, dimensions=settings.dimensions, scheme=scheme, text_version=scheme)


_DONE = """SELECT image_id, visible_text, subject, layout_json, style_json, description
           FROM analyses WHERE status = 'done' ORDER BY image_id"""


def load_pending(conn: sqlite3.Connection, spec: EmbeddingSpec,
                 image_ids: list[int] | None = None) -> tuple[list[Pending], int]:
    """Done analyses whose row for this scheme is missing or stale. (pending, skipped)."""
    existing = {r["image_id"]: r for r in conn.execute(
        "SELECT image_id, model, dimension, dimensions, text_hash, vector FROM embeddings WHERE dimension = ?",
        (spec.scheme,))}
    pending, skipped = [], 0
    rows = conn.execute(_DONE)
    if image_ids is not None:
        allowed = set(image_ids)
        rows = [a for a in rows if a["image_id"] in allowed]
    for a in rows:
        text = build_text(spec.scheme, dict(a))
        if not text:
            skipped += 1
            continue
        current = text_hash(spec.scheme, text)
        reason = stale_reason(existing.get(a["image_id"]), spec, current)
        if reason is None:
            skipped += 1
        else:
            pending.append(Pending(a["image_id"], text, current, reason))
    return pending, skipped


@dataclass(frozen=True)
class DimensionRow:
    image_id: int
    vector: bytes


def load_rows(conn: sqlite3.Connection, spec: EmbeddingSpec) -> list[DimensionRow]:
    """Valid rows only: same model/dimensions/scheme, text hash of the CURRENT analysis,
    usable float32 vector. A changed analysis without a rebuilt row is left out."""
    texts = {a["image_id"]: text_hash(spec.scheme, build_text(spec.scheme, dict(a)))
             for a in conn.execute(_DONE)}
    out = []
    for r in conn.execute(
            """SELECT image_id, model, dimension, dimensions, text_hash, vector FROM embeddings
               WHERE dimension = ? AND model = ? AND dimensions = ? ORDER BY image_id""",
            (spec.scheme, spec.model, spec.dimensions)):
        if r["image_id"] in texts and stale_reason(r, spec, texts[r["image_id"]]) is None:
            out.append(DimensionRow(r["image_id"], r["vector"]))
    return out


def counts(conn: sqlite3.Connection, spec: EmbeddingSpec) -> dict:
    pending, skipped = load_pending(conn, spec)
    return {"scheme": spec.scheme, "stale": len(pending), "valid_or_no_text": skipped,
            "chars": sum(len(p.text) for p in pending), "valid_rows": len(load_rows(conn, spec)),
            "pending": pending}


__all__ = ["SCHEMES", "BUILDERS", "build_text", "text_hash", "spec_for", "load_pending", "load_rows",
           "counts", "DimensionRow", "vector_problem"]
