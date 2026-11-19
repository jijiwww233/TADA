"""In-memory cosine search over one combined embedding per image.

No vector database, no score threshold, no percentage display. Ties are stable:
higher cosine first, then smaller image_id.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from .client import EmbeddingClient


@dataclass(frozen=True)
class EmbeddingItem:
    image_id: int
    vector: Sequence[float]
    payload: Any = None


@dataclass(frozen=True)
class SearchHit:
    image_id: int
    score: float
    payload: Any = None


def _validated(vector: Sequence[float], label: str) -> list[float]:
    values = [float(value) for value in vector]
    if not values:
        raise ValueError(f"{label} 向量为空")
    if any(not math.isfinite(value) for value in values):
        raise ValueError(f"{label} 向量含 NaN/Inf")
    if math.sqrt(sum(value * value for value in values)) <= 1e-12:
        raise ValueError(f"{label} 向量是零向量")
    return values


def cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    """Cosine via normalized dot product. Raises on invalid vectors."""
    a = _validated(left, "query")
    b = _validated(right, "item")
    if len(a) != len(b):
        raise ValueError(f"向量维度不一致: {len(a)} != {len(b)}")
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    return dot / (norm_a * norm_b)


def rank(query_vector: Sequence[float], items: Iterable[EmbeddingItem],
         top_n: int = 10) -> list[SearchHit]:
    """TopN by cosine. No rejection: callers decide what to do with low scores."""
    query = _validated(query_vector, "query")
    query_norm = math.sqrt(sum(value * value for value in query))
    hits: list[SearchHit] = []
    for item in items:
        vector = _validated(item.vector, "item")
        if len(vector) != len(query):
            raise ValueError(f"向量维度不一致: {len(query)} != {len(vector)}")
        item_norm = math.sqrt(sum(value * value for value in vector))
        score = sum(x * y for x, y in zip(query, vector)) / (query_norm * item_norm)
        hits.append(SearchHit(image_id=int(item.image_id), score=score, payload=item.payload))
    hits.sort(key=lambda hit: (-hit.score, hit.image_id))
    if top_n is None:
        return hits
    if top_n <= 0:
        return []
    return hits[:top_n]


def search_text(query: str, items: Iterable[EmbeddingItem], client: EmbeddingClient,
                top_n: int = 10) -> list[SearchHit]:
    if not isinstance(query, str) or not query.strip():
        raise ValueError("查询不能为空")
    return rank(client.embed_one(query), items, top_n=top_n)
