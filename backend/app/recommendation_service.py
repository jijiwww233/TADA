"""Day6 application boundary: brief -> one Day5 search -> bulk context -> pure core.

Read-only. Exactly one SearchApplication.search call (one query embedding); the
core only chooses among that Top20 and never calls a model. Purposes, evidence and
unmet requirements are returned to the caller only: nothing is written to analyses
or pins, and nothing is pinned automatically.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Callable

from . import config
from .embedding.search_application import SearchApplication, SearchUnavailable, readonly_connection
from .recommendation import recommend
from .recommendation_query import resolve_brief


class RecommendationUnavailable(RuntimeError):
    """Fixed public message: never includes provider response, Key or raw exception."""


def _json(value):
    if not value:
        return None
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return None  # malformed stored JSON is not evidence


def load_candidate_context(conn: sqlite3.Connection, ids: list[int]) -> dict[int, dict]:
    """Three bulk SELECTs regardless of len(ids). Only done analyses count."""
    if not ids:
        return {}
    marks = ",".join("?" for _ in ids)
    context = {row["id"]: {"sha256": row["sha256"], "position": row["position"], "analyses": {}, "colors": []}
               for row in conn.execute(f"SELECT id,sha256,position FROM images WHERE id IN ({marks})", ids)}
    for row in conn.execute(
            f"""SELECT image_id,visible_text,subject,layout_json,style_json,description
                FROM analyses WHERE status='done' AND image_id IN ({marks})""", ids):
        if row["image_id"] in context:
            context[row["image_id"]]["analyses"] = {
                "subject": _json(row["subject"]), "layout": _json(row["layout_json"]),
                "style": _json(row["style_json"]), "description": row["description"],
                "visible_text": row["visible_text"]}
    for row in conn.execute(
            f"SELECT image_id,hex,ratio FROM colors WHERE image_id IN ({marks}) ORDER BY image_id,rank", ids):
        if row["image_id"] in context:
            context[row["image_id"]]["colors"].append({"hex": row["hex"], "ratio": row["ratio"]})
    return context


def build_candidates(hits: list[dict], context: dict[int, dict]) -> list[dict]:
    """Search hit (score, sources, is_video) + DB context. Vanished images are skipped."""
    candidates = []
    for hit in hits:
        ctx = context.get(hit["image_id"])
        if ctx is None:
            continue
        candidates.append({"image_id": hit["image_id"], "semantic_score": hit["score"],
                           "sha256": ctx["sha256"], "position": ctx["position"],
                           "post_images": [{"post_id": s["post_id"], "position": s["position"]} for s in hit["sources"]],
                           "is_video": hit["is_video"], "analyses": ctx["analyses"], "colors": ctx["colors"]})
    return candidates


class RecommendationApplication:
    def __init__(self, searcher: SearchApplication, database: Callable[[], Path] = config.db_path):
        self.searcher = searcher
        self.database = database

    def recommend(self, brief: str, previous: str | None = None) -> dict:
        """ValueError (incl. EmptyEffectiveQuery) for unusable input, before any request."""
        final, effective = resolve_brief(brief, previous)
        try:
            found = self.searcher.search(effective)
            if found["status"] != "ok":
                return {"status": found["status"], "effective_query": effective,
                        "items": [], "images": [], "notices": []}
            hits = found["items"]
            with readonly_connection(self.database()) as conn:
                context = load_candidate_context(conn, [h["image_id"] for h in hits])
            result = recommend(final, build_candidates(hits, context), limit=6)
        except (SearchUnavailable, sqlite3.Error):
            raise RecommendationUnavailable("推荐服务暂不可用") from None
        by_id = {h["image_id"]: h for h in hits}
        items = [{"image_id": r["image_id"], "semantic_score": r["semantic_score"],
                  "semantic_rank": r["semantic_rank"],
                  "media_scope": "cover_and_description" if by_id[r["image_id"]]["is_video"] else "image",
                  "post_ids": r["post_ids"], "sources": r["sources"], "purposes": r["purposes"],
                  "unmet_requirements": r["unmet_requirements"]} for r in result["recommendations"]]
        chosen = {i["image_id"] for i in items}
        return {"status": "ok", "effective_query": effective, "items": items,
                "images": [h for h in hits if h["image_id"] in chosen], "notices": result["notices"]}
