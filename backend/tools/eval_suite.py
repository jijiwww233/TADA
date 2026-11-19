#!/usr/bin/env python
"""Formal evaluation infrastructure for search and recommendation.

No algorithm implementation here. Search/recommendation are injected adapters:
- real SearchService binding: SearchServiceAdapter(service, rows)
- offline/fixed results: FixtureSearchAdapter(mapping)
- recommendation: any callable/object returning recommendation output (Day6 later)

Task file: eval/tasks.json, schema version 1. Expected lists may be empty; empty means
unlabeled/manual and is never counted as PASS.

Usage:
  python tools/eval_suite.py --tasks ../eval/tasks.json \
      --search-fixture ../eval/fixtures/search.json \
      --recommendation-fixture ../eval/fixtures/recommendation.json \
      --output-md ../docs/eval_report.md --output-json ../docs/eval_report.json
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Protocol, Sequence

BACKEND = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND.parent
sys.path.insert(0, str(BACKEND))

TASK_FORMAT_VERSION = 1
REPORT_SCHEMA_VERSION = 1
MAX_TOP = 20
VIDEO_SCOPES = {"cover_only", "cover_and_description", "video"}
TASK_FIELDS = {
    "id", "type", "query", "brief", "previous_brief", "expected_image_ids",
    "expected_purposes", "cross_theme_image_ids", "tags", "note", "metadata", "group",
}


class EvalError(ValueError):
    pass


@dataclass(frozen=True)
class Task:
    id: str
    type: str
    query: str | None = None
    brief: str | None = None
    previous_brief: str | None = None
    expected_image_ids: tuple[int, ...] = ()
    expected_purposes: tuple[str, ...] = ()
    cross_theme_image_ids: tuple[int, ...] = ()
    tags: tuple[str, ...] = ()
    note: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    group: str = "dev"

    @property
    def labeled(self) -> bool:
        return bool(self.expected_image_ids or self.expected_purposes)

    @property
    def visual_only(self) -> bool:
        return "visual-only" in self.tags or "visual_only" in self.tags

    @property
    def cross_theme(self) -> bool:
        return "cross-theme" in self.tags or "cross_theme" in self.tags


@dataclass(frozen=True)
class TaskSet:
    version: int
    tasks: tuple[Task, ...]
    sha256: str
    path: str


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _int_list(raw: Any, name: str, *, required: bool = True) -> tuple[int, ...]:
    if raw is None:
        if required:
            raise EvalError(f"{name} 必须是整数数组")
        return ()
    if not isinstance(raw, list):
        raise EvalError(f"{name} 必须是整数数组")
    out: list[int] = []
    for value in raw:
        if not _is_int(value) or value <= 0:
            raise EvalError(f"{name} 只能包含正整数 image_id")
        if value not in out:
            out.append(value)
    return tuple(out)


def _str_list(raw: Any, name: str, *, required: bool = True) -> tuple[str, ...]:
    if raw is None:
        if required:
            raise EvalError(f"{name} 必须是字符串数组")
        return ()
    if not isinstance(raw, list):
        raise EvalError(f"{name} 必须是字符串数组")
    out: list[str] = []
    for value in raw:
        if not isinstance(value, str):
            raise EvalError(f"{name} 只能包含字符串")
        value = value.strip()
        if value and value not in out:
            out.append(value)
    return tuple(out)


def _task_payload(task: Task) -> dict[str, Any]:
    return {
        "id": task.id, "type": task.type, "query": task.query, "brief": task.brief,
        "previous_brief": task.previous_brief, "expected_image_ids": list(task.expected_image_ids),
        "expected_purposes": list(task.expected_purposes),
        "cross_theme_image_ids": list(task.cross_theme_image_ids),
        "tags": list(task.tags), "note": task.note, "metadata": task.metadata,
        "group": task.group,
    }


def test_set_hash(tasks: Iterable[Task]) -> str:
    selected = sorted((_task_payload(task) for task in tasks if task.group == "test"),
                      key=lambda item: item["id"])
    return hashlib.sha256(json.dumps(selected, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def seal_test_set(tasks: Iterable[Task], path: Path) -> dict[str, Any]:
    selected = [task for task in tasks if task.group == "test"]
    if not selected:
        raise EvalError("没有 group=test 的题，不能封存空 test 集")
    payload = {"version": 1, "sha256": test_set_hash(selected),
               "task_ids": [task.id for task in selected],
               "sealed_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


def verify_test_seal(tasks: Iterable[Task], path: Path) -> dict[str, Any]:
    if not path.exists():
        raise EvalError("--final 前必须先用 --seal-test 封存 test 题")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("sha256") != test_set_hash(tasks):
        raise EvalError("test 题内容已变化，拒绝 --final；需重新封存")
    return payload


def load_tasks(path: str | Path) -> TaskSet:
    file_path = Path(path)
    raw_bytes = file_path.read_bytes()
    try:
        raw = json.loads(raw_bytes.decode("utf-8"))
    except Exception as exc:
        raise EvalError(f"tasks JSON 无法解析: {exc}") from exc
    version = TASK_FORMAT_VERSION
    if isinstance(raw, dict):
        version = raw.get("version", TASK_FORMAT_VERSION)
        if not _is_int(version) or version != TASK_FORMAT_VERSION:
            raise EvalError(f"不支持的 tasks version: {version!r}")
        raw_tasks = raw.get("tasks")
    elif isinstance(raw, list):
        raw_tasks = raw
    else:
        raise EvalError("tasks 文件必须是数组或 {version,tasks}")
    if not isinstance(raw_tasks, list):
        raise EvalError("tasks 必须是数组")
    tasks: list[Task] = []
    seen: set[str] = set()
    for index, item in enumerate(raw_tasks, 1):
        if not isinstance(item, dict):
            raise EvalError(f"tasks[{index}] 必须是对象")
        unknown = set(item) - TASK_FIELDS
        if unknown:
            raise EvalError(f"tasks[{index}] 有不认识字段: {sorted(unknown)}")
        task_id = item.get("id")
        if not isinstance(task_id, str) or not task_id.strip():
            raise EvalError(f"tasks[{index}].id 必须是非空字符串")
        if task_id in seen:
            raise EvalError(f"task id 重复: {task_id}")
        seen.add(task_id)
        task_type = item.get("type")
        if task_type not in ("find", "create"):
            raise EvalError(f"tasks[{index}].type 必须是 find 或 create")
        tags = _str_list(item.get("tags", []), f"tasks[{index}].tags", required=False)
        note = item.get("note", "")
        if not isinstance(note, str):
            raise EvalError(f"tasks[{index}].note 必须是字符串")
        metadata = item.get("metadata", {})
        if not isinstance(metadata, dict):
            raise EvalError(f"tasks[{index}].metadata 必须是对象")
        group = item.get("group", "dev")
        if group not in ("dev", "test"):
            raise EvalError(f"tasks[{index}].group 只能是 dev 或 test")
        expected = _int_list(item.get("expected_image_ids", []), f"tasks[{index}].expected_image_ids", required=False)
        if task_type == "find":
            query = item.get("query")
            if not isinstance(query, str) or not query.strip():
                raise EvalError(f"tasks[{index}].query 不能为空")
            if item.get("expected_purposes"):
                raise EvalError(f"find task 不应有 expected_purposes: {task_id}")
            cross = _int_list(item.get("cross_theme_image_ids", []),
                              f"tasks[{index}].cross_theme_image_ids", required=False)
            tasks.append(Task(id=task_id, type="find", query=query.strip(), expected_image_ids=expected,
                              cross_theme_image_ids=cross, tags=tags, note=note, metadata=metadata,
                              group=group))
        else:
            brief = item.get("brief")
            if not isinstance(brief, str) or not brief.strip():
                raise EvalError(f"tasks[{index}].brief 不能为空")
            previous = item.get("previous_brief") or None
            if previous is not None and not isinstance(previous, str):
                raise EvalError(f"tasks[{index}].previous_brief 必须是字符串")
            purposes = _str_list(item.get("expected_purposes", []),
                                 f"tasks[{index}].expected_purposes", required=False)
            tasks.append(Task(id=task_id, type="create", brief=brief.strip(),
                              previous_brief=previous.strip() if previous else None,
                              expected_image_ids=expected, expected_purposes=purposes,
                              tags=tags, note=note, metadata=metadata, group=group))
    return TaskSet(version=version, tasks=tuple(tasks),
                   sha256=hashlib.sha256(raw_bytes).hexdigest(), path=str(file_path))


# ---------------------------------------------------------------- adapters

class SearchAdapter(Protocol):
    def search(self, task_id: str, query: str, top_n: int) -> Sequence[Any]: ...


class RecommendationAdapter(Protocol):
    def recommend(self, task_id: str, brief: str, previous_brief: str | None,
                  top_n: int) -> Mapping[str, Any] | Sequence[Any]: ...


class FixtureSearchAdapter:
    def __init__(self, mapping: Mapping[str, Any]):
        self.mapping = mapping

    def search(self, task_id: str, query: str, top_n: int):
        if task_id not in self.mapping:
            raise KeyError(f"search fixture 缺少 task {task_id}")
        return self.mapping[task_id]


class FixtureRecommendationAdapter:
    def __init__(self, mapping: Mapping[str, Any]):
        self.mapping = mapping

    def recommend(self, task_id: str, brief: str, previous_brief: str | None, top_n: int):
        if task_id not in self.mapping:
            raise KeyError(f"recommendation fixture 缺少 task {task_id}")
        return self.mapping[task_id]


class RecommendationCoreAdapter:
    """Bind Day6 recommend_fn(brief, candidates, limit=...) later.

    candidates_by_task maps task_id to the enriched candidate list; contexts maps image_id
    to the raw candidate payload used for evidence path validation.
    """

    def __init__(self, recommend_fn: Any, candidates_by_task: Mapping[str, Sequence[Any]],
                 contexts_by_task: Mapping[str, Mapping[int, Any]] | None = None,
                 limit: int = 6):
        self.recommend_fn = recommend_fn
        self.candidates_by_task = candidates_by_task
        self.contexts_by_task = contexts_by_task or {}
        self.limit = limit

    def recommend(self, task_id: str, brief: str, previous_brief: str | None, top_n: int):
        candidates = list(self.candidates_by_task.get(task_id, ()))
        result = self.recommend_fn(brief, candidates, limit=min(top_n, self.limit))
        contexts = self.contexts_by_task.get(task_id, {})
        if contexts:
            result = dict(result)
            result["contexts"] = {str(key): value for key, value in contexts.items()}
        return result


class SearchApplicationAdapter:
    """Bind current SearchApplication.search(query) response (status/items)."""

    def __init__(self, application: Any):
        self.application = application

    def search(self, task_id: str, query: str, top_n: int):
        result = self.application.search(query)
        if not isinstance(result, Mapping) or result.get("status") != "ok":
            return []
        return result.get("items") or []


class RecommendationApplicationAdapter:
    """Bind current RecommendationApplication.recommend(brief, previous).

    The application response does not carry raw candidate context, so evidence paths
    are MANUAL/unvalidated unless a context_provider is supplied. The provider should
    use the same read-only candidate context loader as production.
    """

    def __init__(self, application: Any, context_provider=None):
        self.application = application
        self.context_provider = context_provider

    def recommend(self, task_id: str, brief: str, previous_brief: str | None, top_n: int):
        result = self.application.recommend(brief, previous_brief)
        if not isinstance(result, Mapping) or result.get("status") != "ok":
            return {"recommendations": [], "notices": [str((result or {}).get("status"))]}
        items = list(result.get("items") or [])
        if not items and result.get("columns"):
            for column in result.get("columns") or []:
                if not isinstance(column, Mapping):
                    continue
                purpose = column.get("purpose")
                for item in column.get("items") or []:
                    if not isinstance(item, Mapping):
                        continue
                    row = dict(item)
                    row.setdefault("purpose", purpose)
                    if not row.get("purposes") and purpose:
                        row["purposes"] = [{"purpose": purpose,
                                            "reason": row.get("reference_point") or "",
                                            "evidence": []}]
                    items.append(row)
        contexts = {}
        if self.context_provider is not None and items:
            contexts = self.context_provider([int(item["image_id"]) for item in items]) or {}
        recommendations = []
        for item in items:
            candidate = dict(item)
            if candidate.get("media_scope") == "cover_and_description":
                candidate["video_scope"] = "cover_and_description"
                candidate["is_video"] = True
            context = contexts.get(candidate.get("image_id"))
            if isinstance(context, Mapping) and context.get("sha256"):
                candidate.setdefault("sha256", context["sha256"])
            recommendations.append(candidate)
        return {"recommendations": recommendations,
                "columns": result.get("columns"),
                "notices": list(result.get("notices") or []),
                "contexts": {str(key): value for key, value in contexts.items()}}


def recommendation_context_provider(database: Any):
    """Build a read-only context loader for current RecommendationApplication."""
    from app.embedding.search_application import readonly_connection
    from app.recommendation_service import load_candidate_context
    from pathlib import Path

    def provider(image_ids):
        with readonly_connection(Path(database())) as conn:
            return load_candidate_context(conn, list(image_ids))
    return provider


class SearchServiceAdapter:
    """Bind a real app.embedding SearchService + rows (no models if embedder faked)."""

    def __init__(self, service: Any, rows: Sequence[Any], top_n: int = MAX_TOP):
        self.service = service
        self.rows = rows
        self.top_n = top_n

    def search(self, task_id: str, query: str, top_n: int):
        return self.service.search(query, self.rows, top_n=min(top_n or self.top_n, self.top_n))


@dataclass(frozen=True)
class Hit:
    image_id: int
    score: float | None = None


def normalize_hits(raw: Any, top_n: int = MAX_TOP) -> list[Hit]:
    if isinstance(raw, Mapping):
        if raw.get("status") == "ok" and "items" in raw:
            raw = raw.get("items") or []
        else:
            raw = raw.get("hits") or raw.get("results") or raw.get("items") or []
    if not isinstance(raw, (list, tuple)):
        raise EvalError("search adapter 返回值必须是数组或 {hits|results: []}")
    hits: list[Hit] = []
    seen: set[int] = set()
    for item in raw:
        if _is_int(item):
            image_id, score = item, None
        elif isinstance(item, Mapping):
            image_id = item.get("image_id")
            score = item.get("score")
        else:
            image_id = getattr(item, "image_id", None)
            score = getattr(item, "score", None)
        if not _is_int(image_id) or image_id <= 0:
            raise EvalError("search hit 缺少合法 image_id")
        if image_id in seen:
            continue
        seen.add(image_id)
        if score is not None and not isinstance(score, bool) and isinstance(score, (int, float)):
            score = float(score)
        else:
            score = None
        hits.append(Hit(image_id=image_id, score=score))
        if len(hits) >= top_n:
            break
    return hits


def _rank_map(hits: Sequence[Hit], expected: Sequence[int]) -> dict[int, int | None]:
    ranks = {image_id: None for image_id in expected}
    for index, hit in enumerate(hits, 1):
        if hit.image_id in ranks and ranks[hit.image_id] is None:
            ranks[hit.image_id] = index
    return ranks


# ---------------------------------------------------------------- search evaluation

def task_column(task: Task) -> str:
    """Label column for reporting; tags come from the manual label page."""
    if task.type == "create":
        return "recommendation"
    for name in ("layout", "color", "style"):
        if name in task.tags:
            return name
    return "find"


def _usage_tokens(raw: Any) -> int | None:
    if not isinstance(raw, Mapping):
        return None
    usage = raw.get("usage")
    if isinstance(usage, Mapping):
        if usage.get("total_tokens") is not None:
            return int(usage["total_tokens"])
        if usage.get("prompt_tokens") is not None or usage.get("completion_tokens") is not None:
            return int(usage.get("prompt_tokens") or 0) + int(usage.get("completion_tokens") or 0)
    if raw.get("prompt_tokens") is not None or raw.get("completion_tokens") is not None:
        return int(raw.get("prompt_tokens") or 0) + int(raw.get("completion_tokens") or 0)
    return None


def eval_search(tasks: Iterable[Task], adapter: SearchAdapter, *, top_n: int = MAX_TOP) -> dict[str, Any]:
    top_n = max(1, min(int(top_n), MAX_TOP))
    per_task: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    labeled = unlabeled = scored = 0
    hit_counts = {k: 0 for k in (1, 3, 5, 10, 20)}
    mrr_sum = 0.0
    cross_rows: list[dict[str, Any]] = []
    visual_cross_rows: list[dict[str, Any]] = []

    for task in tasks:
        if task.type != "find":
            continue
        if not task.labeled:
            unlabeled += 1
        else:
            labeled += 1
        failed = False
        started = time.perf_counter()
        raw: Any = None
        try:
            raw = adapter.search(task.id, task.query or "", top_n)
            hits = normalize_hits(raw, top_n)
        except Exception as exc:
            failed = True
            errors.append({"task_id": task.id, "error": f"{type(exc).__name__}: {exc}"})
            hits = []
        elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
        requests = 0 if failed or (isinstance(raw, Mapping) and raw.get("status") not in (None, "ok")) else 1
        tokens = _usage_tokens(raw)
        ranks = _rank_map(hits, task.expected_image_ids)
        first_rank = next((r for r in ranks.values() if r is not None), None)
        hit_flags = {str(k): bool(first_rank and first_rank <= k) for k in (1, 3, 5, 10, 20)}
        if task.labeled and not failed:
            scored += 1
            if first_rank is not None:
                mrr_sum += 1.0 / first_rank
                for k in hit_counts:
                    if first_rank <= k:
                        hit_counts[k] += 1
        cross = None
        if task.cross_theme_image_ids:
            cross_ids = set(task.cross_theme_image_ids)
            cross_hits = [(index, hit.image_id) for index, hit in enumerate(hits[:20], 1)
                          if hit.image_id in cross_ids]
            cross = {
                "annotated": True,
                "top20_candidates": len(cross_hits),
                "first_rank": cross_hits[0][0] if cross_hits else None,
                "ids": [image_id for _, image_id in cross_hits],
            }
            cross_rows.append({"task_id": task.id, **cross})
            if task.visual_only:
                visual_cross_rows.append({"task_id": task.id, **cross})
        else:
            relevant = task.cross_theme or task.visual_only
            cross = {"annotated": False, "manual_pending": relevant,
                     "not_applicable": not relevant,
                     "reason": ("缺少 cross_theme_image_ids 人工元数据" if relevant else
                                "该 task 未标记 cross-theme/visual-only，不适用")}
        per_task.append({
            "id": task.id,
            "type": "find",
            "query": task.query,
            "labeled": task.labeled,
            "tags": list(task.tags),
            "expected_image_ids": list(task.expected_image_ids),
            "ranks": {str(image_id): rank for image_id, rank in ranks.items()},
            "first_expected_rank": first_rank,
            "hit": hit_flags,
            "mrr": (1.0 / first_rank) if first_rank else 0.0,
            "top": [{"image_id": hit.image_id, "score": hit.score} for hit in hits],
            "cross_theme": cross,
            "manual_pending": not task.labeled,
            "error": failed,
            "note": task.note,
            "column": task_column(task),
            "elapsed_ms": elapsed_ms,
            "requests": requests,
            "tokens": tokens,
        })

    def rate(value: int) -> float | None:
        return round(value / scored, 4) if scored else None

    find_tasks = [task for task in tasks if task.type == "find"]
    misses = [row["id"] for row, task in zip(per_task, find_tasks)
              if task.labeled and not row.get("error") and row["first_expected_rank"] is None]
    worst = sorted(
        [row for row, task in zip(per_task, find_tasks)
         if task.labeled and not row.get("error")],
        key=lambda row: (row["hit"]["5"], row["mrr"],
                         -1 if row["first_expected_rank"] is None else -row["first_expected_rank"]),
    )[:5]
    cross_annotated = [row for row in cross_rows if row["top20_candidates"] is not None]
    columns: dict[str, dict[str, Any]] = {}
    for row, task in zip(per_task, find_tasks):
        key = row["column"]
        bucket = columns.setdefault(key, {"column": key, "tasks_total": 0, "labeled": 0,
            "top1_hits": 0, "top5_hits": 0, "misses": 0, "requests": 0,
            "tokens": None, "elapsed_ms": 0.0})
        bucket["tasks_total"] += 1
        bucket["requests"] += row["requests"]
        bucket["elapsed_ms"] = round(bucket["elapsed_ms"] + row["elapsed_ms"], 1)
        if row["tokens"] is not None:
            bucket["tokens"] = (bucket["tokens"] or 0) + row["tokens"]
        if task.labeled and not row["error"]:
            bucket["labeled"] += 1
            if row["first_expected_rank"] is not None and row["first_expected_rank"] <= 1:
                bucket["top1_hits"] += 1
            if row["first_expected_rank"] is not None and row["first_expected_rank"] <= 5:
                bucket["top5_hits"] += 1
            else:
                bucket["misses"] += 1
    return {
        "tasks_total": sum(1 for task in tasks if task.type == "find"),
        "tasks_labeled": labeled,
        "tasks_unlabeled": unlabeled,
        "tasks_scored": scored,
        "errors": errors,
        "metrics": {
            "top1": hit_counts[1],
            "top3": hit_counts[3],
            "top5": hit_counts[5],
            "top10": hit_counts[10],
            "top20": hit_counts[20],
            "top1_rate": rate(hit_counts[1]),
            "top3_rate": rate(hit_counts[3]),
            "top5_rate": rate(hit_counts[5]),
            "top10_rate": rate(hit_counts[10]),
            "top20_rate": rate(hit_counts[20]),
            "mrr": round(mrr_sum / scored, 4) if scored else None,
        },
        "per_task": per_task,
        "per_column": columns,
        "misses": misses,
        "worst_queries": [row["id"] for row in worst],
        "cross_theme": {
            "tasks_tagged": sum(1 for task in tasks
                                if task.type == "find" and task.cross_theme),
            "visual_only_tasks": sum(1 for task in tasks
                                     if task.type == "find" and task.visual_only),
            "tasks_annotated": len(cross_annotated),
            "candidates_top20": sum(row["top20_candidates"] for row in cross_annotated),
            "with_first_rank": sum(1 for row in cross_annotated if row["first_rank"] is not None),
            "rows": cross_annotated,
            "manual_pending": [row["id"] for row in per_task
                               if row["cross_theme"].get("manual_pending")],
        },
        "visual_only_cross_theme": {
            "tasks_annotated": len(visual_cross_rows),
            "candidates_top20": sum(row["top20_candidates"] for row in visual_cross_rows),
            "with_first_rank": sum(1 for row in visual_cross_rows if row["first_rank"] is not None),
            "rows": visual_cross_rows,
        },
        "manual_pending": [row["id"] for row in per_task if row["manual_pending"]],
    }


# ---------------------------------------------------------------- recommendation evaluation

def _candidate_list(raw: Any) -> tuple[list[dict[str, Any]], dict[str, Any], dict[int, Any]]:
    contexts: dict[int, Any] = {}
    if isinstance(raw, Mapping):
        recommendations = raw.get("recommendations") or raw.get("items") or []
        if not recommendations and raw.get("columns"):
            recommendations = []
            for column in raw.get("columns") or []:
                if not isinstance(column, Mapping):
                    continue
                purpose = column.get("purpose")
                for item in column.get("items") or []:
                    if not isinstance(item, Mapping):
                        continue
                    row = dict(item)
                    row.setdefault("purpose", purpose)
                    if not row.get("purposes") and purpose:
                        row["purposes"] = [{"purpose": purpose, "reason": row.get("reference_point") or "",
                                            "evidence": []}]
                    recommendations.append(row)
        raw_contexts = raw.get("contexts") or raw.get("evidence_contexts") or {}
        if isinstance(raw_contexts, Mapping):
            for key, value in raw_contexts.items():
                try:
                    contexts[int(key)] = value
                except (TypeError, ValueError):
                    continue
    else:
        recommendations = raw
    if not isinstance(recommendations, (list, tuple)):
        raise EvalError("recommendation adapter 返回值必须是数组或 {recommendations: []}")
    out = []
    for item in recommendations:
        if not isinstance(item, Mapping):
            raise EvalError("recommendation 条目必须是对象")
        image_id = item.get("image_id")
        if not _is_int(image_id) or image_id <= 0:
            raise EvalError("recommendation 条目缺少合法 image_id")
        candidate = dict(item)
        candidate["image_id"] = image_id
        candidate["purposes"] = _normalize_purposes(item.get("purposes"))
        candidate["sha256"] = item.get("sha256") or item.get("sha")
        candidate["post_ids"] = _post_ids(item)
        if isinstance(raw, Mapping) and item.get("context") is not None:
            contexts.setdefault(image_id, item["context"])
        out.append(candidate)
    return out, (raw if isinstance(raw, Mapping) else {}), contexts


def _normalize_purposes(raw: Any) -> list[dict[str, Any]]:
    if raw is None:
        return []
    if not isinstance(raw, (list, tuple)):
        raise EvalError("purposes 必须是数组")
    out = []
    for item in raw:
        if isinstance(item, str):
            out.append({"purpose": item, "reason": "", "evidence": []})
        elif isinstance(item, Mapping):
            purpose = item.get("purpose") or item.get("kind")
            if not isinstance(purpose, str) or not purpose:
                raise EvalError("purpose 缺少字符串名称")
            evidence = item.get("evidence") or []
            if not isinstance(evidence, (list, tuple)):
                raise EvalError("purpose evidence 必须是数组")
            out.append({"purpose": purpose, "reason": str(item.get("reason") or ""),
                        "evidence": list(evidence)})
        else:
            raise EvalError("purpose 条目必须是字符串或对象")
    return out


def _post_ids(candidate: Mapping[str, Any]) -> list[Any]:
    values = []
    for key in ("post_ids", "post_id"):
        raw = candidate.get(key)
        if isinstance(raw, (list, tuple)):
            values.extend(raw)
        elif raw is not None:
            values.append(raw)
    for source in candidate.get("sources") or []:
        if isinstance(source, Mapping):
            values.extend(source.get("post_ids") or [])
            if source.get("post_id") is not None:
                values.append(source["post_id"])
    unique = []
    for value in values:
        if value is not None and value not in unique:
            unique.append(value)
    return unique


def resolve_path(obj: Any, path: str) -> Any:
    if not isinstance(path, str) or not path:
        raise KeyError("empty evidence path")
    current = obj
    for part in re.findall(r"[^.\[\]]+", path):
        if isinstance(current, (list, tuple)):
            current = current[int(part)]
        elif isinstance(current, Mapping):
            current = current[part]
        else:
            raise KeyError(path)
    return current


def evaluate_evidence(candidate: Mapping[str, Any], context: Any) -> tuple[int, int, int]:
    valid = invalid = unvalidated = 0
    for purpose in candidate.get("purposes", []):
        for evidence in purpose.get("evidence", []):
            if not isinstance(evidence, Mapping) or "field" not in evidence:
                invalid += 1
                continue
            if context is None:
                unvalidated += 1
                continue
            try:
                actual = resolve_path(context, evidence["field"])
            except Exception:
                invalid += 1
                continue
            if "value" in evidence and actual != evidence["value"]:
                invalid += 1
            else:
                valid += 1
    return valid, invalid, unvalidated


def eval_recommendation(tasks: Iterable[Task], adapter: RecommendationAdapter, *,
                        top_n: int = MAX_TOP) -> dict[str, Any]:
    per_task: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    labeled = unlabeled = scored = 0
    image_scored = purpose_scored = 0
    recall_sum = 0.0
    purpose_sum = 0.0
    evidence_valid = evidence_invalid = evidence_unvalidated = 0
    sha_violations = 0
    post_violations = 0
    video_missing = video_invalid = 0
    auto_pin = 0
    manual_pending = 0

    for task in tasks:
        if task.type != "create":
            continue
        if task.labeled:
            labeled += 1
        else:
            unlabeled += 1
        failed = False
        started = time.perf_counter()
        raw: Any = None
        try:
            raw = adapter.recommend(task.id, task.brief or "", task.previous_brief, top_n)
            candidates, envelope, contexts = _candidate_list(raw)
        except Exception as exc:
            failed = True
            errors.append({"task_id": task.id, "error": f"{type(exc).__name__}: {exc}"})
            candidates, envelope, contexts = [], {}, {}
        elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
        requests = 0 if failed else int((raw or {}).get("requests") or (1 if raw else 0)) if isinstance(raw, Mapping) else 1
        tokens = _usage_tokens(raw)
        if isinstance(raw, Mapping):
            for column in raw.get("columns") or []:
                if isinstance(column, Mapping) and isinstance(column.get("usage"), Mapping):
                    usage = column["usage"]
                    requests += int(usage.get("requests") or 0)
                    if usage.get("prompt_tokens") is not None or usage.get("completion_tokens") is not None:
                        tokens = (tokens or 0) + int(usage.get("prompt_tokens") or 0) + int(usage.get("completion_tokens") or 0)
            needs_usage = raw.get("needs_usage")
            if isinstance(needs_usage, Mapping):
                requests += int(needs_usage.get("requests") or 0)
                if needs_usage.get("prompt_tokens") is not None or needs_usage.get("completion_tokens") is not None:
                    tokens = (tokens or 0) + int(needs_usage.get("prompt_tokens") or 0) + int(needs_usage.get("completion_tokens") or 0)
        expected = set(task.expected_image_ids)
        found = {c["image_id"] for c in candidates} & expected
        recall = len(found) / len(expected) if expected else None
        if task.labeled and not failed:
            scored += 1
        if recall is not None and not failed:
            recall_sum += recall
            image_scored += 1
        purposes = {p["purpose"] for c in candidates for p in c["purposes"]}
        expected_purposes = set(task.expected_purposes)
        covered = purposes & expected_purposes
        coverage = len(covered) / len(expected_purposes) if expected_purposes else None
        if coverage is not None and not failed:
            purpose_sum += coverage
            purpose_scored += 1

        sha_seen: dict[str, int] = {}
        task_sha_violations = 0
        post_seen: dict[Any, int] = {}
        task_post_violations = 0
        task_video_missing = task_video_invalid = 0
        task_auto_pin = 0
        task_evidence = [0, 0, 0]
        unmet_candidates = 0
        for candidate in candidates:
            sha = candidate.get("sha256")
            if sha:
                sha_seen[sha] = sha_seen.get(sha, 0) + 1
            for post_id in candidate["post_ids"]:
                post_seen[post_id] = post_seen.get(post_id, 0) + 1
            video = bool(candidate.get("is_video") or candidate.get("media_type") == "video")
            scope = candidate.get("video_scope") or candidate.get("scope")
            if video:
                if not scope:
                    task_video_missing += 1
                elif scope not in VIDEO_SCOPES:
                    task_video_invalid += 1
            if any(candidate.get(key) not in (None, "", False) for key in
                   ("pin_id", "auto_created_pin", "created_pin_id", "created_pin", "auto_pin")):
                task_auto_pin += 1
            if candidate.get("unmet_requirements"):
                unmet_candidates += 1
            context = contexts.get(candidate["image_id"], candidate.get("context"))
            ev = evaluate_evidence(candidate, context)
            task_evidence = [task_evidence[i] + ev[i] for i in range(3)]
        task_sha_violations = sum(count - 1 for count in sha_seen.values() if count > 1)
        task_post_violations = sum(count - 1 for count in post_seen.values() if count > 1)
        sha_violations += task_sha_violations
        post_violations += task_post_violations
        video_missing += task_video_missing
        video_invalid += task_video_invalid
        auto_pin += task_auto_pin
        evidence_valid += task_evidence[0]
        evidence_invalid += task_evidence[1]
        evidence_unvalidated += task_evidence[2]
        # Subjective quality (usefulness, reason quality, honest unmet) is always MANUAL;
        # unlabeled tasks are also MANUAL and never enter recall/coverage denominators.
        manual_pending += 1

        per_task.append({
            "id": task.id,
            "brief": task.brief,
            "previous_brief": task.previous_brief,
            "labeled": task.labeled,
            "expected_image_ids": list(task.expected_image_ids),
            "expected_purposes": list(task.expected_purposes),
            "candidate_images": [c["image_id"] for c in candidates],
            "found_expected_images": sorted(found),
            "image_recall": recall,
            "purposes": sorted(purposes),
            "covered_purposes": sorted(covered),
            "missing_purposes": sorted(expected_purposes - covered),
            "purpose_coverage": coverage,
            "evidence_valid": task_evidence[0],
            "evidence_invalid": task_evidence[1],
            "evidence_unvalidated": task_evidence[2],
            "sha_violations": task_sha_violations,
            "post_diversity_violations": task_post_violations,
            "video_scope_missing": task_video_missing,
            "video_scope_invalid": task_video_invalid,
            "auto_pin_violations": task_auto_pin,
            "unmet_disclosed_candidates": unmet_candidates,
            "manual_pending": True,
            "error": failed,
            "elapsed_ms": elapsed_ms,
            "requests": requests,
            "tokens": tokens,
            "columns": envelope.get("columns") if isinstance(envelope, Mapping) else None,
            "candidates": [
                {"image_id": c["image_id"], "purposes": [p["purpose"] for p in c["purposes"]],
                 "reasons": [p["reason"] for p in c["purposes"] if p.get("reason")],
                 "sha256": c.get("sha256"), "post_ids": c["post_ids"]}
                for c in candidates
            ],
            "notices": list(envelope.get("notices") or []),
            "note": task.note,
        })

    has_evidence = evidence_valid + evidence_invalid
    rec_columns: dict[str, dict[str, Any]] = {}
    for row in per_task:
        for column in row.get("columns") or []:
            if not isinstance(column, Mapping):
                continue
            key = str(column.get("purpose") or "unknown")
            bucket = rec_columns.setdefault(key, {
                "column": key, "label": column.get("label"), "tasks_total": 0,
                "top5_hits": 0, "misses": 0, "requests": 0, "tokens": None,
                "elapsed_ms": 0.0, "review_status": {}})
            bucket["tasks_total"] += 1
            status = str(column.get("status") or "unknown")
            bucket["review_status"][status] = bucket["review_status"].get(status, 0) + 1
            usage = column.get("usage") if isinstance(column.get("usage"), Mapping) else {}
            bucket["requests"] += int(usage.get("requests") or 0)
            bucket["elapsed_ms"] = round(bucket["elapsed_ms"] + float(usage.get("latency_ms") or 0), 1)
            if usage.get("prompt_tokens") is not None or usage.get("completion_tokens") is not None:
                bucket["tokens"] = (bucket["tokens"] or 0) + int(usage.get("prompt_tokens") or 0) + int(usage.get("completion_tokens") or 0)
            expected = set(row.get("expected_image_ids") or [])
            if expected:
                picked = [item.get("image_id") for item in (column.get("items") or [])[:5]
                          if isinstance(item, Mapping)]
                if expected & set(picked):
                    bucket["top5_hits"] += 1
                else:
                    bucket["misses"] += 1
    return {
        "tasks_total": sum(1 for task in tasks if task.type == "create"),
        "tasks_labeled": labeled,
        "tasks_unlabeled": unlabeled,
        "tasks_scored": scored,
        "errors": errors,
        "metrics": {
            "image_recall": round(recall_sum / image_scored, 4) if image_scored else None,
            "purpose_coverage": round(purpose_sum / purpose_scored, 4) if purpose_scored else None,
            "evidence_valid": evidence_valid,
            "evidence_invalid": evidence_invalid,
            "evidence_unvalidated": evidence_unvalidated,
            "evidence_validity": round(evidence_valid / has_evidence, 4) if has_evidence else None,
            "sha_violations": sha_violations,
            "post_diversity_violations": post_violations,
            "video_scope_missing": video_missing,
            "video_scope_invalid": video_invalid,
            "auto_pin_violations": auto_pin,
            "auto_pin_ok": auto_pin == 0,
            "manual_pending": manual_pending,
        },
        "per_task": per_task,
        "per_column": rec_columns,
        "manual_pending": [row["id"] for row in per_task if row["manual_pending"]],
        "errors": errors,
    }


# ---------------------------------------------------------------- regression / meta / report

def compare_reports(baseline: Mapping[str, Any], current: Mapping[str, Any], *,
                    rank_delta: int = 5) -> dict[str, Any]:
    old_tasks = {row["id"]: row for row in (baseline.get("search") or {}).get("per_task", [])}
    new_tasks = {row["id"]: row for row in (current.get("search") or {}).get("per_task", [])}
    changes = []
    severe = []
    task_changes = []
    for task_id, old in old_tasks.items():
        new = new_tasks.get(task_id)
        if new is None:
            changes.append({"task_id": task_id, "kind": "removed_task"})
            continue
        if old.get("first_expected_rank") != new.get("first_expected_rank") or old.get("mrr") != new.get("mrr"):
            task_changes.append({
                "task_id": task_id, "old_first_rank": old.get("first_expected_rank"),
                "new_first_rank": new.get("first_expected_rank"),
                "old_mrr": old.get("mrr"), "new_mrr": new.get("mrr"),
            })
        old_ranks = old.get("ranks") or {}
        new_ranks = new.get("ranks") or {}
        for image_key, old_rank in old_ranks.items():
            new_rank = new_ranks.get(image_key)
            if old_rank == new_rank:
                continue
            kind = "improved" if (old_rank is None or (new_rank is not None and new_rank < old_rank)) else "declined"
            change = {
                "task_id": task_id, "image_id": int(image_key), "old_rank": old_rank,
                "new_rank": new_rank, "kind": kind,
            }
            changes.append(change)
            if old_rank is not None and old_rank <= 20 and new_rank is None:
                severe.append({**change, "reason": "expected image 从 Top20 掉出"})
            elif old_rank is not None and new_rank is not None and new_rank - old_rank >= rank_delta:
                severe.append({**change, "reason": f"rank 下降 >= {rank_delta}"})
    for task_id in new_tasks.keys() - old_tasks.keys():
        changes.append({"task_id": task_id, "kind": "new_task"})

    old_rec = {row["id"]: row for row in (baseline.get("recommendation") or {}).get("per_task", [])}
    new_rec = {row["id"]: row for row in (current.get("recommendation") or {}).get("per_task", [])}
    rec_changes = []
    for task_id, old in old_rec.items():
        new = new_rec.get(task_id)
        if new is None:
            rec_changes.append({"task_id": task_id, "kind": "removed_task"})
            continue
        old_purposes = set(old.get("purposes") or [])
        new_purposes = set(new.get("purposes") or [])
        old_images = set(old.get("candidate_images") or [])
        new_images = set(new.get("candidate_images") or [])
        if old_purposes != new_purposes:
            rec_changes.append({"task_id": task_id, "kind": "purpose_changed",
                                "added": sorted(new_purposes - old_purposes),
                                "removed": sorted(old_purposes - new_purposes)})
        if old_images != new_images:
            rec_changes.append({"task_id": task_id, "kind": "image_set_changed",
                                "added": sorted(new_images - old_images),
                                "removed": sorted(old_images - new_images)})
    return {
        "baseline_available": True,
        "rank_changes": changes,
        "query_changes": task_changes,
        "severe_regressions": severe,
        "recommendation_changes": rec_changes,
        "summary": {
            "improved": sum(1 for c in changes if c.get("kind") == "improved"),
            "declined": sum(1 for c in changes if c.get("kind") == "declined"),
            "severe": len(severe),
            "new_tasks": sum(1 for c in changes if c.get("kind") == "new_task"),
        },
    }


def _git_commit() -> str | None:
    try:
        result = subprocess.run(["git", "-C", str(PROJECT_ROOT), "rev-parse", "HEAD"],
                                capture_output=True, text=True, timeout=5, check=False)
        return result.stdout.strip() or None
    except Exception:
        return None


def _default_embedding() -> dict[str, Any]:
    try:
        from app.embedding.service import EmbeddingSpec
        spec = EmbeddingSpec()
        return {"model": spec.model, "dimensions": spec.dimensions,
                "scheme": spec.scheme, "text_version": spec.text_version}
    except Exception:
        return {"model": None, "dimensions": None, "scheme": None, "text_version": None}


def _default_search_version() -> str:
    if importlib.util.find_spec("app.embedding.search_application") is not None:
        return "search-application-v1"
    return "search-service-v1"


def _default_recommendation_version() -> str:
    if importlib.util.find_spec("app.recommendation_service") is not None:
        return "day6-application-v1"
    if importlib.util.find_spec("app.recommendation") is not None:
        return "day6-core"
    return "not_bound"


def collect_meta(tasks: TaskSet, *, embedding: Mapping[str, Any] | None = None,
                 search_version: str | None = None,
                 recommendation_version: str | None = None) -> dict[str, Any]:
    emb = dict(_default_embedding())
    if embedding:
        emb.update({k: v for k, v in embedding.items() if v is not None})
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "git_commit": _git_commit(),
        "embedding_model": emb.get("model"),
        "dimensions": emb.get("dimensions"),
        "scheme": emb.get("scheme"),
        "text_version": emb.get("text_version"),
        "search_algorithm_version": search_version or _default_search_version(),
        "recommendation_version": recommendation_version or _default_recommendation_version(),
        "task_file": tasks.path,
        "task_file_sha256": tasks.sha256,
    }


def build_report(tasks: TaskSet, *, search: Mapping[str, Any] | None = None,
                 recommendation: Mapping[str, Any] | None = None,
                 meta: Mapping[str, Any] | None = None,
                 baseline: Mapping[str, Any] | None = None) -> dict[str, Any]:
    search_default = {
        "tasks_total": 0, "tasks_labeled": 0, "tasks_unlabeled": 0,
        "metrics": {"top1": 0, "top3": 0, "top5": 0, "top10": 0, "top20": 0,
                    "top1_rate": None, "top3_rate": None, "top5_rate": None,
                    "top10_rate": None, "top20_rate": None, "mrr": None},
        "per_task": [], "per_column": {}, "misses": [], "worst_queries": [],
        "cross_theme": {"tasks_tagged": 0, "visual_only_tasks": 0,
                        "tasks_annotated": 0, "candidates_top20": 0,
                        "with_first_rank": 0, "rows": [], "manual_pending": []},
        "visual_only_cross_theme": {"tasks_annotated": 0, "candidates_top20": 0,
                                    "with_first_rank": 0, "rows": []},
        "manual_pending": [], "errors": [],
    }
    rec_default = {
        "tasks_total": 0, "tasks_labeled": 0, "tasks_unlabeled": 0,
        "metrics": {"image_recall": None, "purpose_coverage": None,
                    "evidence_valid": 0, "evidence_invalid": 0,
                    "evidence_unvalidated": 0, "evidence_validity": None,
                    "sha_violations": 0, "post_diversity_violations": 0,
                    "video_scope_missing": 0, "video_scope_invalid": 0,
                    "auto_pin_violations": 0, "auto_pin_ok": True,
                    "manual_pending": 0},
        "per_task": [], "per_column": {}, "manual_pending": [], "errors": [],
    }
    report = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "meta": dict(meta or {}),
        "search": dict(search_default, **(search or {})),
        "recommendation": dict(rec_default, **(recommendation or {})),
        "regression": None,
    }
    if baseline is not None:
        report["regression"] = compare_reports(baseline, report)
    return report


def render_markdown(report: Mapping[str, Any]) -> str:
    meta = report.get("meta") or {}
    search = report.get("search") or {}
    rec = report.get("recommendation") or {}
    lines = [
        "# Evaluation report",
        "",
        f"- generated_at: {meta.get('generated_at')}",
        f"- git_commit: {meta.get('git_commit')}",
        f"- embedding: {meta.get('embedding_model')} / {meta.get('dimensions')} dims / "
        f"{meta.get('scheme')} / {meta.get('text_version')}",
        f"- search_algorithm_version: {meta.get('search_algorithm_version')}",
        f"- recommendation_version: {meta.get('recommendation_version')}",
        f"- task_group: {meta.get('task_group')}",
        f"- task_file_sha256: {meta.get('task_file_sha256')}",
        "",
        "## Search",
        "",
        f"- tasks: {search.get('tasks_total', 0)} total / "
        f"{search.get('tasks_labeled', 0)} labeled / {search.get('tasks_unlabeled', 0)} unlabeled",
    ]
    metrics = search.get("metrics") or {}
    if metrics:
        lines += [
            "",
            "| top1 | top3 | top5 | top10 | top20 | MRR |",
            "|---:|---:|---:|---:|---:|---:|",
            f"| {metrics.get('top1')} | {metrics.get('top3')} | {metrics.get('top5')} | "
            f"{metrics.get('top10')} | {metrics.get('top20')} | {metrics.get('mrr')} |",
            "",
            "未标注 expected 的 task 不计入准确率，单独列为 MANUAL。",
        ]
    if search.get("per_column"):
        lines += ["", "| 栏 | 已标 | Top5 命中 | 遗漏 | 请求数 | tokens | 耗时ms |",
                  "|---|---:|---:|---:|---:|---:|---:|"]
        for key, bucket in (search["per_column"] or {}).items():
            lines.append(f"| {key} | {bucket.get('labeled')} | {bucket.get('top5_hits')} | "
                         f"{bucket.get('misses')} | {bucket.get('requests')} | "
                         f"{bucket.get('tokens')} | {bucket.get('elapsed_ms')} |")
    cross = search.get("cross_theme") or {}
    if cross:
        lines += [
            "",
            f"cross-theme: tagged={cross.get('tasks_tagged')}, "
            f"annotated tasks={cross.get('tasks_annotated')}, "
            f"Top20 candidates={cross.get('candidates_top20')}, "
            f"first-rank hits={cross.get('with_first_rank')}；缺少 cross_theme_image_ids 的 task 为 MANUAL，"
            "不由程序猜语义。",
            f"visual-only tasks: {cross.get('visual_only_tasks')}; "
            f"manual pending: {', '.join(cross.get('manual_pending') or []) or '-'}",
        ]
    if search.get("misses"):
        lines += ["", f"misses: {', '.join(search['misses'])}"]
    if search.get("worst_queries"):
        lines += [f"worst queries: {', '.join(search['worst_queries'])}"]

    lines += ["", "## Recommendation", ""]
    rmetrics = rec.get("metrics") or {}
    lines += [
        f"- tasks: {rec.get('tasks_total', 0)} total / {rec.get('tasks_labeled', 0)} labeled / "
        f"{rec.get('tasks_unlabeled', 0)} unlabeled",
        f"- image recall: {rmetrics.get('image_recall')}",
        f"- purpose coverage: {rmetrics.get('purpose_coverage')}",
        f"- evidence validity: {rmetrics.get('evidence_validity')} "
        f"(valid={rmetrics.get('evidence_valid')}, invalid={rmetrics.get('evidence_invalid')}, "
        f"unvalidated={rmetrics.get('evidence_unvalidated')})",
        f"- SHA violations: {rmetrics.get('sha_violations')}",
        f"- same-post diversity violations: {rmetrics.get('post_diversity_violations')}",
        f"- video scope missing/invalid: {rmetrics.get('video_scope_missing')} / {rmetrics.get('video_scope_invalid')}",
        f"- auto pin violations: {rmetrics.get('auto_pin_violations')} "
        f"(expected 0; ok={rmetrics.get('auto_pin_ok')})",
        f"- manual pending: {rmetrics.get('manual_pending')}",
        "",
        "主观质量（是否好用、理由是否充分、unmet 是否诚实）一律为 MANUAL；未自动打分。",
    ]
    if rec.get("per_column"):
        lines += ["", "| 栏 | Top5 命中 | 遗漏 | 请求数 | tokens | 耗时ms |",
                  "|---|---:|---:|---:|---:|---:|"]
        for key, bucket in (rec["per_column"] or {}).items():
            lines.append(f"| {key} | {bucket.get('top5_hits')} | {bucket.get('misses')} | "
                         f"{bucket.get('requests')} | {bucket.get('tokens')} | {bucket.get('elapsed_ms')} |")

    regression = report.get("regression")
    if regression:
        summary = regression.get("summary") or {}
        lines += [
            "", "## Regression", "",
            f"- improved={summary.get('improved')} declined={summary.get('declined')} "
            f"severe={summary.get('severe')} new_tasks={summary.get('new_tasks')}",
        ]
        for change in regression.get("severe_regressions") or []:
            lines.append(f"- SEVERE {change}")
        for change in regression.get("rank_changes") or []:
            if change.get("kind") in ("improved", "declined"):
                lines.append(f"- {change.get('kind')} {change}")
        for change in regression.get("recommendation_changes") or []:
            lines.append(f"- rec {change}")
    else:
        lines += ["", "## Regression", "", "无 baseline，未比较。"]

    lines += ["", "## 人工标注草稿", ""]
    find_rows = list(search.get("per_task", []))
    if not find_rows:
        lines.append("暂无 find task。")
    for row in find_rows:
        lines += [f"### {row['id']}", f"Query: {row.get('query', '')}"]
        for index, item in enumerate(row.get("top", [])[:5], 1):
            lines.append(f"{index}. image {item['image_id']} [相关 / 不相关]")
        lines.append("")
    create_rows = list(rec.get("per_task", []))
    if not create_rows:
        lines.append("暂无 recommendation task。")
    for row in create_rows:
        lines += [f"### {row['id']}", f"Brief: {row.get('brief', '')}"]
        for candidate in row.get("candidates", [])[:5]:
            lines += [f"image {candidate['image_id']}",
                      f"用途: {', '.join(candidate.get('purposes') or [])}",
                      f"理由: {'; '.join(candidate.get('reasons') or [])}",
                      "[有用 / 一般 / 无用]", ""]
    return "\n".join(lines) + "\n"


def _load_fixture(path: str | Path | None) -> dict[str, Any]:
    if not path:
        return {}
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise EvalError("fixture 必须是对象")
    return raw


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Formal search/recommendation evaluation")
    parser.add_argument("--tasks", default=str(PROJECT_ROOT / "eval" / "tasks.json"))
    parser.add_argument("--search-fixture", default=None)
    parser.add_argument("--recommendation-fixture", default=None)
    parser.add_argument("--baseline", default=None)
    parser.add_argument("--output-md", default=str(PROJECT_ROOT / "docs" / "eval_report.md"))
    parser.add_argument("--output-json", default=str(PROJECT_ROOT / "docs" / "eval_report.json"))
    parser.add_argument("--top-n", type=int, default=20)
    parser.add_argument("--embedding-model", default=None)
    parser.add_argument("--dimensions", type=int, default=None)
    parser.add_argument("--scheme", default=None)
    parser.add_argument("--text-version", default=None)
    parser.add_argument("--search-version", default=None)
    parser.add_argument("--recommendation-version", default=None)
    parser.add_argument("--final", action="store_true",
                        help="include group=test tasks; default evaluates group=dev only")
    parser.add_argument("--seal-test", action="store_true",
                        help="freeze group=test annotations before any --final run")
    parser.add_argument("--test-lock", default=str(PROJECT_ROOT / "eval" / "test.lock.json"))
    parser.add_argument("--real-search", action="store_true",
                        help="bind current SearchApplication (paid query embeddings; no fixture)")
    parser.add_argument("--real-recommendation", action="store_true",
                        help="bind current CreativeApplication / columns (paid text/vision/embedding)")
    parser.add_argument("--database", default=None,
                        help="read-only DB path for real recommendation evidence contexts")
    args = parser.parse_args(argv)

    try:
        loaded = load_tasks(args.tasks)
        if args.seal_test:
            seal_test_set(loaded.tasks, Path(args.test_lock))
            print(f"sealed {args.test_lock}")
            return 0
        if args.final:
            verify_test_seal(loaded.tasks, Path(args.test_lock))
        selected = tuple(task for task in loaded.tasks if args.final or task.group == "dev")
        tasks = TaskSet(version=loaded.version, tasks=selected,
                        sha256=loaded.sha256, path=loaded.path)
        search_tasks = [task for task in tasks.tasks if task.type == "find"]
        create_tasks = [task for task in tasks.tasks if task.type == "create"]
        search_adapter = None
        if search_tasks and args.real_search:
            from app.embedding.search_application import SearchApplication
            search_adapter = SearchApplicationAdapter(SearchApplication())
        elif search_tasks and args.search_fixture:
            search_adapter = FixtureSearchAdapter(_load_fixture(args.search_fixture))
        recommendation_adapter = None
        if create_tasks and args.real_recommendation:
            from app.creative import CreativeApplication
            from app import config
            db_path = Path(args.database) if args.database else config.db_path()
            recommendation_adapter = RecommendationApplicationAdapter(
                CreativeApplication(), context_provider=recommendation_context_provider(db_path))
        elif create_tasks and args.recommendation_fixture:
            recommendation_adapter = FixtureRecommendationAdapter(_load_fixture(args.recommendation_fixture))
        search = eval_search(tasks.tasks, search_adapter, top_n=args.top_n) if search_adapter else None
        recommendation = eval_recommendation(tasks.tasks, recommendation_adapter,
                                             top_n=args.top_n) if recommendation_adapter else None
        if search is None and search_tasks:
            search = {"tasks_total": len(search_tasks), "tasks_labeled": 0, "tasks_unlabeled": 0,
                      "errors": [{"task_id": "*", "error": "缺少 --search-fixture / --real-search"}]}
        if recommendation is None and create_tasks:
            recommendation = {"tasks_total": len(create_tasks), "tasks_labeled": 0,
                              "tasks_unlabeled": 0,
                              "errors": [{"task_id": "*", "error": "缺少 --recommendation-fixture / --real-recommendation"}]}
        baseline = json.loads(Path(args.baseline).read_text(encoding="utf-8")) if args.baseline else None
        embedding = {}
        if args.embedding_model is not None:
            embedding["model"] = args.embedding_model
        if args.dimensions is not None:
            embedding["dimensions"] = args.dimensions
        if args.scheme is not None:
            embedding["scheme"] = args.scheme
        if args.text_version is not None:
            embedding["text_version"] = args.text_version
        meta = collect_meta(tasks, embedding=embedding, search_version=args.search_version,
                            recommendation_version=args.recommendation_version)
        meta["task_group"] = "dev+test(final)" if args.final else "dev"
        report = build_report(tasks, search=search, recommendation=recommendation,
                              meta=meta, baseline=baseline)
        Path(args.output_json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output_json).write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                          encoding="utf-8")
        Path(args.output_md).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output_md).write_text(render_markdown(report), encoding="utf-8")
        print(f"wrote {args.output_md}")
        print(f"wrote {args.output_json}")
        return 0
    except (EvalError, OSError, json.JSONDecodeError) as exc:
        print(f"FAIL {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
