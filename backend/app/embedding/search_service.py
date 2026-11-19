"""SearchService: natural-language query -> TopN over current combined embeddings.

Reads only rows that belong to the configured identity:
  model == configured model
  dimension (DB scheme) == combined_d_v1
  dimensions == configured vector size
  text_hash == current text hash for that image   (stale rows are ignored)
  vector length == dimensions, finite, non-zero

Uses in-memory cosine (normalized dot); stable order: score desc, then NUMERIC image_id
asc (never string sorting). This is also the upstream ordering contract recommendation
uses: equal semantic scores keep the numeric image_id / stable upstream order.
No threshold, no percentage. No BM25/rerank/layout weighting.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from .client import EmbeddingClient
from .search import EmbeddingItem, SearchHit, rank
from .service import EmbeddingSpec

_HEX64 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class SearchRow:
    """One candidate vector as stored by the corrected migration.

    field mapping: embeddings.dimension -> dimension (scheme),
                   embeddings.dimensions -> dimensions (vector size).
    current_text_hash is computed from the current analysis+colors; when it does not
    match text_hash, the row is stale and must not be searched.
    """

    image_id: int
    vector: Sequence[float]
    model: str
    dimension: str
    dimensions: int
    text_hash: str | None
    current_text_hash: str | None = None

    @classmethod
    def from_mapping(cls, row: Mapping[str, Any]) -> "SearchRow":
        if not isinstance(row, Mapping):
            raise TypeError("SearchRow 只接受 Mapping（dict）")
        return cls(
            image_id=int(row["image_id"]),
            vector=row["vector"],
            model=str(row.get("model") or ""),
            dimension=str(row.get("dimension") or ""),
            dimensions=int(row.get("dimensions") or 0),
            text_hash=row.get("text_hash"),
            current_text_hash=row.get("current_text_hash"),
        )


class SearchService:
    def __init__(self, client: EmbeddingClient, spec: EmbeddingSpec | None = None):
        self.client = client
        settings = getattr(client, "settings", None)
        if spec is None:
            if settings is None:
                raise ValueError("client 没有 settings 时必须显式传入 spec")
            spec = EmbeddingSpec.from_settings(settings)
        elif settings is not None:
            _assert_same_config(settings, spec)
        self.spec = spec

    @classmethod
    def from_settings(cls, settings: Any) -> "SearchService":
        from .client import EmbeddingClient

        return cls(EmbeddingClient(settings), EmbeddingSpec.from_settings(settings))

    def search(self, query: str, rows: Iterable[SearchRow], top_n: int = 20) -> list[SearchHit]:
        """Embed the raw query and rank valid rows. Empty valid set -> [] (no API call)."""
        if not isinstance(query, str) or not query.strip():
            raise ValueError("查询不能为空")
        valid = [row for row in rows if self.is_current(row)]
        if not valid:
            return []
        query_vector = self.client.embed_one(query)
        items = [EmbeddingItem(image_id=row.image_id, vector=row.vector, payload=row) for row in valid]
        return rank(query_vector, items, top_n=top_n)

    def is_current(self, row: SearchRow) -> bool:
        """Metadata + vector + text_hash freshness check. Never raises on bad rows."""
        if row.model != self.spec.model:
            return False
        if row.dimension != self.spec.scheme:
            return False
        if row.dimensions != self.spec.dimensions:
            return False
        if not isinstance(row.text_hash, str) or not _HEX64.match(row.text_hash):
            return False
        if not isinstance(row.current_text_hash, str) or not _HEX64.match(row.current_text_hash):
            return False
        if row.text_hash != row.current_text_hash:
            return False
        try:
            if len(row.vector) != self.spec.dimensions:
                return False
            values = [float(value) for value in row.vector]
        except (TypeError, ValueError):
            return False
        if any(not math.isfinite(value) for value in values):
            return False
        if math.sqrt(sum(value * value for value in values)) <= 1e-12:
            return False
        return True


def _assert_same_config(settings: Any, spec: EmbeddingSpec) -> None:
    pairs = (("model", settings.model, spec.model),
             ("dimensions", settings.dimensions, spec.dimensions),
             ("scheme", settings.scheme, spec.scheme),
             ("text_version", settings.text_version, spec.text_version))
    for name, left, right in pairs:
        if left != right:
            raise ValueError(f"client/spec 配置不一致: {name} {left!r} != {right!r}")
