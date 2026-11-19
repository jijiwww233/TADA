"""Read-only Day5 application boundary; usable by HTTP and offline evaluation.

SearchService remains the ranking interface. No queue, backfill, migration, visual
analysis or embedding writes here. Connections close before the paid query call.
"""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import sqlite3
from typing import Callable

from .. import config
from .client import EmbeddingClient, EmbeddingSettings
from .repository import load_search_rows
from .search import SearchHit
from .search_service import SearchService
from .service import EmbeddingSpec

# Text search can also be answered by the local Chinese-CLIP text tower against the
# clip_image_v1 image vectors. The two routes are min-max normalised and fused at the
# score level; 0.2 is the combined-vector weight chosen on the dev find queries only
# (docs/eval_log.md). Missing local weights simply fall back to the text route.
CLIP_FUSION_ALPHA = 0.2
FUSION_TOP = 20


def _minmax(scores: dict) -> dict:
    if not scores:
        return {}
    low, high = min(scores.values()), max(scores.values())
    span = high - low
    return {image_id: ((value - low) / span if span > 1e-9 else 0.0)
            for image_id, value in scores.items()}


def fuse_hits(combined, clip_scores: dict, *, alpha: float = CLIP_FUSION_ALPHA,
              top_n: int = FUSION_TOP) -> list:
    """Rank by alpha*normalised(text cosine) + (1-alpha)*normalised(CLIP cosine)."""
    normalised_text = _minmax({hit.image_id: hit.score for hit in combined})
    normalised_clip = _minmax(clip_scores)
    scores = {}
    for image_id in set(normalised_text) | set(normalised_clip):
        scores[image_id] = (alpha * normalised_text.get(image_id, 0.0)
                            + (1.0 - alpha) * normalised_clip.get(image_id, 0.0))
    fused = sorted(scores.items(), key=lambda pair: (-pair[1], pair[0]))[:top_n]
    return [SearchHit(image_id, score) for image_id, score in fused]


class SearchUnavailable(RuntimeError):
    """Fixed public message: never includes provider response, Key or raw exception."""


@contextmanager
def readonly_connection(path: Path):
    conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA query_only=ON")
        conn.execute("BEGIN")
        yield conn
    finally:
        conn.close()


def hydrate_hits(conn: sqlite3.Connection, hits) -> list[dict]:
    """Two bulk SELECTs, independent of result count. Preserve the ranked order."""
    if not hits:
        return []
    ids = [hit.image_id for hit in hits]
    marks = ",".join("?" for _ in ids)
    images = {row["id"]: dict(row) for row in conn.execute(
        f"""SELECT id,file_path,width,height,original_name,created_at
            FROM images WHERE id IN ({marks})""", ids)}
    sources: dict[int, list[dict]] = {image_id: [] for image_id in ids}
    for row in conn.execute(
        f"""SELECT pi.image_id,pi.position,p.id AS post_id,p.platform,p.source_id,
                   p.title,p.url AS canonical_url,p.is_video,
                   (SELECT COUNT(*) FROM post_images allpi WHERE allpi.post_id=p.id)AS image_count
            FROM post_images pi JOIN posts p ON p.id=pi.post_id
            WHERE pi.image_id IN ({marks}) ORDER BY pi.image_id,p.id,pi.position""", ids):
        source = dict(row)
        image_id = source.pop("image_id")
        source["is_video"] = bool(source["is_video"])
        sources[image_id].append(source)
    return [{"image_id": hit.image_id, "score": hit.score,
             "url": "/" + images[hit.image_id]["file_path"],
             "width": images[hit.image_id]["width"], "height": images[hit.image_id]["height"],
             "original_name": images[hit.image_id]["original_name"],
             "created_at": images[hit.image_id]["created_at"],
             "is_video": any(source["is_video"] for source in sources[hit.image_id]),
             "sources": sources[hit.image_id]}
            for hit in hits if hit.image_id in images]


class SearchApplication:
    def __init__(self, settings_provider: Callable[[], EmbeddingSettings] = EmbeddingSettings.from_env,
                 client_factory: Callable = EmbeddingClient,
                 database: Callable[[], Path] = config.db_path,
                 clip_search=None):
        self.settings_provider = settings_provider
        self.client_factory = client_factory
        self.database = database
        # Local Chinese-CLIP text route (ImageSimilarity); None or a failure = text route only.
        self.clip_search = clip_search

    def search(self, query: str, *, visual_scores: dict[int, float] | None = None) -> dict:
        """Exactly one raw query embedding for a nonempty valid index, Top20.

        Recheck after the network call: changed/deleted/stale rows are omitted,
        without another query embedding or hidden backfill. Thus a concurrent
        change can yield fewer than 20 hits. No read lock is held across HTTP.
        Supplied visual scores restrict the candidates to those already matched locally.
        """
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query must not be blank")
        try:
            settings = self.settings_provider()
            spec = EmbeddingSpec.from_settings(settings)
            # Filtering does not need an HTTP client, including index_empty.
            class IdentityOnly:
                def __init__(self): self.settings = settings
            validator = SearchService(IdentityOnly(), spec)
            with readonly_connection(self.database()) as conn:
                rows = [r for r in load_search_rows(conn, spec) if validator.is_current(r)]
            if not rows:
                return {"status": "index_empty", "items": []}
            with self.client_factory(settings) as client:
                service = SearchService(client, spec)
                combined = service.search(query, rows, top_n=None)
            clip_scores = visual_scores
            if clip_scores is not None:
                combined = [hit for hit in combined if hit.image_id in clip_scores]
            elif self.clip_search is not None and not getattr(self.clip_search, "warming", False):
                try:
                    clip_scores = dict(self.clip_search.search_text(query, top_n=None))
                except Exception:
                    clip_scores = {}  # no local weights/vectors: stay on the text route
            hits = fuse_hits(combined, clip_scores) if clip_scores else combined[:FUSION_TOP]
            original = {r.image_id: r for r in rows}
            with readonly_connection(self.database()) as conn:
                current = {r.image_id: r for r in load_search_rows(conn, spec) if validator.is_current(r)}
                clip_valid = None
                if clip_scores:
                    try:
                        clip_valid = set(self.clip_search.valid_ids())
                    except Exception:
                        clip_valid = set(clip_scores)
                kept = []
                for hit in hits:
                    if hit.image_id in original:
                        if current.get(hit.image_id) == original[hit.image_id]:
                            kept.append(hit)
                    elif clip_valid is not None and hit.image_id in clip_valid:
                        kept.append(hit)  # image bytes unchanged since the clip vector was written
                items = hydrate_hits(conn, kept)
            mode = "文字检索"
            if clip_scores:
                mode = "图文融合" if combined else "本地检索"
            return {"status": "ok", "items": items, "search_mode": mode}
        except Exception:
            # Includes config/client/DB/malformed-vector errors. Never log raw upstream data.
            raise SearchUnavailable("搜索服务暂不可用") from None

    def search_local(self, query: str) -> dict:
        """Free fallback when the paid text route is unreachable (no Key, offline, quota).

        Only channels that cost nothing are used: the HTTP layer merges the local
        user-annotation hits, and this method adds the local Chinese-CLIP text tower.
        A missing weight vector contributes 0, so ranking falls back to the channels
        that do exist. Never raises: a local failure is an empty (degraded) result, so
        the UI can say "没找到" instead of "搜索服务暂不可用".
        """
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query must not be blank")
        try:
            clip_scores: dict[int, float] = {}
            if self.clip_search is not None and not getattr(self.clip_search, "warming", False):
                try:
                    clip_scores = dict(self.clip_search.search_text(query, top_n=None))
                except Exception:
                    clip_scores = {}
            hits: list[SearchHit] = []
            if clip_scores:
                try:
                    valid = set(self.clip_search.valid_ids())
                except Exception:
                    valid = set(clip_scores)
                hits = fuse_hits([], {image_id: score for image_id, score in clip_scores.items()
                                      if image_id in valid})
            if not hits:
                return {"status": "ok", "items": [], "degraded": True}
            with readonly_connection(self.database()) as conn:
                items = hydrate_hits(conn, hits)
            return {"status": "ok", "items": items, "degraded": True}
        except Exception:
            return {"status": "ok", "items": [], "degraded": True}
