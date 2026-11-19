"""Offline retrieval evaluation: query JSON -> Top1/Top3/Top5 and failures.

This tool is for re-acceptance on real collections. It does not reuse the old
spike's 22/24 figure; every report is computed from the supplied queries and the
current index.
"""
from __future__ import annotations

import json
import pathlib
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Sequence

SearchFn = Callable[[str, int], Sequence[Any]]


@dataclass(frozen=True)
class EvalQuery:
    id: str
    query: str
    expected: tuple[int, ...]
    category: str = ""


def load_queries(path: str | pathlib.Path) -> list[EvalQuery]:
    data = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    if isinstance(data, dict):
        data = data.get("queries") or []
    if not isinstance(data, list):
        raise ValueError("查询集必须是 JSON 数组，或 {\"queries\": [...]}")
    queries: list[EvalQuery] = []
    for index, raw in enumerate(data, 1):
        if not isinstance(raw, dict):
            raise ValueError(f"第 {index} 条查询不是对象")
        query = str(raw.get("query") or "").strip()
        if not query:
            raise ValueError(f"第 {index} 条查询缺少 query")
        expected_raw = raw.get("expected")
        if expected_raw is None:
            expected: tuple[int, ...] = ()
        elif isinstance(expected_raw, (int, str)):
            expected = (int(expected_raw),)
        else:
            expected = tuple(int(value) for value in expected_raw)
        queries.append(EvalQuery(
            id=str(raw.get("id") or f"q{index:02d}"),
            query=query,
            expected=expected,
            category=str(raw.get("category") or ""),
        ))
    return queries


def evaluate(search_fn: SearchFn, queries: Iterable[EvalQuery], top_n: int = 5,
             rank_depth: int = 20) -> dict:
    """Run each query and report Top1/Top3/Top5 + rank of the first expected image.

    search_fn(query, limit) must return objects with .image_id and .score (SearchHit).
    Queries with empty expected are diagnostic only and excluded from metrics.
    """
    per_query: list[dict] = []
    scored: list[dict] = []
    for query in queries:
        hits = list(search_fn(query.query, max(rank_depth, top_n)))
        ids = [int(hit.image_id) for hit in hits]
        expected = set(query.expected)
        rank = next((index for index, image_id in enumerate(ids, 1) if image_id in expected), None)
        top = hits[:top_n]
        row = {
            "id": query.id,
            "category": query.category,
            "query": query.query,
            "expected": list(query.expected),
            "rank": rank,
            "hit1": rank == 1,
            "hit3": bool(rank and rank <= 3),
            "hit5": bool(rank and rank <= 5),
            "top": [
                {"image_id": int(hit.image_id), "score": round(float(hit.score), 6)}
                for hit in top
            ],
        }
        per_query.append(row)
        if expected:
            scored.append(row)

    n = len(scored)
    metrics = {
        "queries": n,
        "top1": sum(row["hit1"] for row in scored),
        "top3": sum(row["hit3"] for row in scored),
        "top5": sum(row["hit5"] for row in scored),
        "top1_rate": round(sum(row["hit1"] for row in scored) / n, 4) if n else 0.0,
        "top3_rate": round(sum(row["hit3"] for row in scored) / n, 4) if n else 0.0,
        "top5_rate": round(sum(row["hit5"] for row in scored) / n, 4) if n else 0.0,
        "mrr": round(sum((1.0 / row["rank"]) for row in scored if row["rank"]) / n, 4) if n else 0.0,
    }
    failures = [
        {
            "id": row["id"],
            "category": row["category"],
            "query": row["query"],
            "expected": row["expected"],
            "rank": row["rank"],
            "top": row["top"],
        }
        for row in scored
        if not row["hit5"]
    ]
    return {"metrics": metrics, "per_query": per_query, "failures": failures}
