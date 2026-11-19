"""Fixed-fixture tests for eval infrastructure (no models, no real library)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.eval_suite import (
    EvalError,
    FixtureRecommendationAdapter,
    FixtureSearchAdapter,
    RecommendationApplicationAdapter,
    SearchApplicationAdapter,
    build_report,
    collect_meta,
    compare_reports,
    eval_recommendation,
    eval_search,
    load_tasks,
    render_markdown,
    resolve_path,
)


def write_tasks(tmp_path, tasks, version=1):
    path = tmp_path / "tasks.json"
    path.write_text(json.dumps({"version": version, "tasks": tasks}, ensure_ascii=False),
                    encoding="utf-8")
    return path


def find(task_id, expected, *, cross=None, tags=None):
    return {"id": task_id, "type": "find", "query": f"query {task_id}",
            "expected_image_ids": expected, "cross_theme_image_ids": cross or [],
            "tags": tags or [], "note": ""}


def create(task_id, expected, purposes, *, previous=None):
    return {"id": task_id, "type": "create", "brief": f"brief {task_id}",
            "previous_brief": previous, "expected_image_ids": expected,
            "expected_purposes": purposes, "tags": ["recommendation"], "note": ""}


def search_tasks(tmp_path):
    path = write_tasks(tmp_path, [
        find("find-1", [42]),
        find("find-2", [5, 6]),
        find("find-3", [9]),
        find("find-unlabeled", []),
        find("find-cross", [1], cross=[3, 4], tags=["cross-theme", "visual-only"]),
        find("find-cross-no-meta", [1], tags=["cross-theme", "visual-only"]),
    ])
    return load_tasks(path)


def search_adapter():
    return FixtureSearchAdapter({
        "find-1": [1, 42, 3],
        "find-2": [5, 7],
        "find-3": [2, 4],
        "find-unlabeled": [10, 11],
        "find-cross": [1, 3, 4, 8],
        "find-cross-no-meta": [1, 2],
    })


def test_find_metrics_and_multiple_expected(tmp_path):
    result = eval_search(search_tasks(tmp_path).tasks, search_adapter())
    assert result["tasks_total"] == 6
    assert result["tasks_labeled"] == 5
    assert result["tasks_unlabeled"] == 1
    assert result["metrics"]["top1"] == 3  # find-2 (5), find-cross (1), find-cross-no-meta (1)
    assert result["metrics"]["top3"] == 4  # find-1 also
    assert result["metrics"]["top5"] == 4
    assert result["metrics"]["top20"] == 4
    assert result["metrics"]["mrr"] == pytest.approx((1 / 2 + 1 + 0 + 1 + 1) / 5)
    task = next(row for row in result["per_task"] if row["id"] == "find-2")
    assert task["ranks"] == {"5": 1, "6": None}
    assert task["hit"]["1"] is True
    assert "find-3" in result["misses"]
    assert "find-unlabeled" not in result["misses"]


def test_unlabeled_not_counted_and_manual(tmp_path):
    result = eval_search(search_tasks(tmp_path).tasks, search_adapter())
    assert "find-unlabeled" in result["manual_pending"]
    assert result["tasks_unlabeled"] == 1
    assert result["metrics"]["top20_rate"] == pytest.approx(4 / 5)


def test_cross_theme_uses_manual_metadata_only(tmp_path):
    result = eval_search(search_tasks(tmp_path).tasks, search_adapter())
    cross = result["cross_theme"]
    assert cross["tasks_annotated"] == 1
    assert cross["candidates_top20"] == 2
    assert cross["with_first_rank"] == 1
    assert cross["rows"][0]["first_rank"] == 2
    visual = result["visual_only_cross_theme"]
    assert visual["tasks_annotated"] == 1
    assert visual["candidates_top20"] == 2
    no_meta = next(row for row in result["per_task"] if row["id"] == "find-cross-no-meta")
    assert no_meta["cross_theme"]["manual_pending"] is True
    assert no_meta["cross_theme"]["annotated"] is False
    normal = next(row for row in result["per_task"] if row["id"] == "find-1")
    assert normal["cross_theme"]["manual_pending"] is False
    assert normal["cross_theme"]["not_applicable"] is True


def test_search_adapter_error_is_reported_not_fake_pass(tmp_path):
    adapter = FixtureSearchAdapter({"find-1": [42]})
    result = eval_search(search_tasks(tmp_path).tasks, adapter)
    assert result["errors"]
    assert result["tasks_labeled"] == 5
    assert result["metrics"]["top1"] == 1  # only find-1 succeeded


def test_resolve_evidence_paths():
    context = {"analyses": {"layout": {"columns": ["multi"]}},
               "colors": [{"hex": "#111111", "ratio": 0.6}]}
    assert resolve_path(context, "analyses.layout.columns") == ["multi"]
    assert resolve_path(context, "colors[0].ratio") == 0.6
    with pytest.raises(KeyError):
        resolve_path(context, "analyses.missing")


def recommendation_contexts():
    return {
        "2": {"analyses": {"layout": {"columns": ["multi"]},
                           "style": {"tags": ["极简"]}}},
        "3": {"analyses": {"layout": {"columns": ["single"]}}},
    }


def test_recommendation_metrics_evidence_and_manual(tmp_path):
    path = write_tasks(tmp_path, [
        create("create-1", [2], ["layout", "style"]),
        create("create-unlabeled", [], []),
    ])
    fixture = {
        "create-1": {
            "recommendations": [
                {"image_id": 2, "sha256": "sha-2", "post_ids": [10], "purposes": [
                    {"purpose": "layout", "reason": "多栏", "evidence": [
                        {"field": "analyses.layout.columns", "value": ["multi"]}]},
                    {"purpose": "style", "reason": "极简", "evidence": [
                        {"field": "analyses.style.tags", "value": ["极简"]}]},
                ], "unmet_requirements": []},
                {"image_id": 3, "sha256": "sha-3", "post_ids": [11], "purposes": [
                    {"purpose": "layout", "reason": "单栏", "evidence": [
                        {"field": "analyses.layout.columns", "value": ["single"]}]},
                ]},
            ],
            "contexts": recommendation_contexts(),
        },
        "create-unlabeled": {"recommendations": []},
    }
    result = eval_recommendation(load_tasks(path).tasks,
                                 FixtureRecommendationAdapter(fixture))
    assert result["tasks_total"] == 2
    assert result["tasks_labeled"] == 1
    assert result["metrics"]["image_recall"] == 1.0
    assert result["metrics"]["purpose_coverage"] == 1.0
    assert result["metrics"]["evidence_valid"] == 3
    assert result["metrics"]["evidence_invalid"] == 0
    assert result["metrics"]["evidence_unvalidated"] == 0
    assert result["metrics"]["auto_pin_violations"] == 0
    assert result["metrics"]["auto_pin_ok"] is True
    assert result["metrics"]["manual_pending"] == 2  # subjective quality + unlabeled are MANUAL
    assert "create-unlabeled" in result["manual_pending"]


def test_recommendation_violations_and_manual(tmp_path):
    path = write_tasks(tmp_path, [create("create-1", [6], ["layout"])])
    fixture = {"create-1": {"recommendations": [
        {"image_id": 6, "sha256": "same", "post_ids": [10], "is_video": True,
         "pin_id": 99, "purposes": [{"purpose": "layout", "reason": "r",
                                     "evidence": [{"field": "missing.path", "value": 1}]}],
         "unmet_requirements": [{"kind": "style", "value": "复古"}]},
        {"image_id": 7, "sha256": "same", "post_ids": [10], "is_video": True,
         "video_scope": "bad_scope", "purposes": [{"purpose": "layout", "reason": "r",
                                                   "evidence": [{"field": "analyses.layout.columns",
                                                                 "value": ["multi"]}]}]},
    ], "contexts": {"6": {}, "7": recommendation_contexts()["2"]}}}
    result = eval_recommendation(load_tasks(path).tasks,
                                 FixtureRecommendationAdapter(fixture))
    metrics = result["metrics"]
    assert metrics["sha_violations"] == 1
    assert metrics["post_diversity_violations"] == 1
    assert metrics["video_scope_missing"] == 1
    assert metrics["video_scope_invalid"] == 1
    assert metrics["auto_pin_violations"] == 1
    assert metrics["auto_pin_ok"] is False
    assert metrics["evidence_invalid"] == 1
    assert metrics["evidence_valid"] == 1


def test_unmet_is_manual_not_auto_scored(tmp_path):
    path = write_tasks(tmp_path, [create("create-1", [], ["layout"])])
    fixture = {"create-1": {"recommendations": [
        {"image_id": 1, "purposes": [{"purpose": "layout", "reason": "r",
                                     "evidence": []}],
         "unmet_requirements": [{"kind": "style", "value": "复古"}]},
    ], "contexts": {"1": {}}}}
    result = eval_recommendation(load_tasks(path).tasks,
                                 FixtureRecommendationAdapter(fixture))
    assert result["per_task"][0]["unmet_disclosed_candidates"] == 1
    # A purpose-only expected task is labeled; subjective quality remains MANUAL.
    assert result["metrics"]["manual_pending"] == 1
    assert "unmet" not in json.dumps(result["metrics"])


def test_regression_reports_severe_drop_even_if_average_improves():
    baseline = {
        "search": {"per_task": [
            {"id": "t1", "ranks": {"5": 2}, "first_expected_rank": 2, "mrr": 0.5},
            {"id": "t2", "ranks": {"7": 10}, "first_expected_rank": 10, "mrr": 0.1},
        ]},
        "recommendation": {"per_task": [
            {"id": "c1", "purposes": ["layout"], "candidate_images": [1, 2]},
        ]},
    }
    current = {
        "search": {"per_task": [
            {"id": "t1", "ranks": {"5": 1}, "first_expected_rank": 1, "mrr": 1.0},
            {"id": "t2", "ranks": {"7": 12}, "first_expected_rank": 12, "mrr": 1 / 12},
        ]},
        "recommendation": {"per_task": [
            {"id": "c1", "purposes": ["layout", "style"], "candidate_images": [1, 3]},
        ]},
    }
    result = compare_reports(baseline, current, rank_delta=5)
    assert result["summary"]["improved"] == 1
    assert result["summary"]["declined"] == 1
    assert result["summary"]["severe"] == 0
    current["search"]["per_task"][1]["ranks"]["7"] = None
    result = compare_reports(baseline, current, rank_delta=5)
    assert result["summary"]["severe"] == 1
    assert result["severe_regressions"][0]["task_id"] == "t2"
    assert result["recommendation_changes"][0]["kind"] == "purpose_changed"
    assert result["query_changes"][0]["task_id"] == "t1"


def test_meta_and_task_hash(tmp_path):
    path = write_tasks(tmp_path, [find("f", [])])
    tasks = load_tasks(path)
    meta = collect_meta(tasks, search_version="s-v1", recommendation_version="r-v1")
    assert meta["task_file_sha256"] == tasks.sha256
    assert len(meta["task_file_sha256"]) == 64
    assert meta["search_algorithm_version"] == "s-v1"
    assert meta["recommendation_version"] == "r-v1"
    assert "embedding_model" in meta
    report = build_report(tasks, meta=meta)
    assert report["meta"]["task_file_sha256"] == tasks.sha256
    path.write_text(json.dumps({"version": 1, "tasks": [find("f2", [])]}), encoding="utf-8")
    assert load_tasks(path).sha256 != tasks.sha256


def test_malformed_tasks_are_rejected(tmp_path):
    cases = [
        [{"id": "x", "type": "bad"}],
        [{"id": "x", "type": "find", "query": ""}],
        [{"id": "x", "type": "find", "query": "q", "expected_image_ids": ["1"]}],
        [{"id": "x", "type": "find", "query": "q", "unknown": 1}],
        [{"id": "x", "type": "find", "query": "q"},
         {"id": "x", "type": "find", "query": "q2"}],
        [{"id": "x", "type": "create", "brief": ""}],
    ]
    for tasks in cases:
        path = write_tasks(tmp_path, tasks)
        with pytest.raises(EvalError):
            load_tasks(path)


def test_malformed_recommendation_result_is_rejected(tmp_path):
    path = write_tasks(tmp_path, [create("c", [1], ["layout"])])
    adapter = FixtureRecommendationAdapter({"c": {"recommendations": [{"no_id": 1}]}})
    result = eval_recommendation(load_tasks(path).tasks, adapter)
    assert result["errors"]


def test_markdown_contains_manual_sheet(tmp_path):
    tasks = load_tasks(write_tasks(tmp_path, [find("f", []), create("c", [], [])]))
    search = eval_search(tasks.tasks, FixtureSearchAdapter({"f": [1, 2]}))
    rec = eval_recommendation(tasks.tasks, FixtureRecommendationAdapter({
        "c": {"recommendations": [{"image_id": 1, "purposes": [
            {"purpose": "layout", "reason": "多栏", "evidence": []}]}]},
    }))
    report = build_report(tasks, search=search, recommendation=rec, meta=collect_meta(tasks))
    md = render_markdown(report)
    assert "Query: query f" in md
    assert "[相关 / 不相关]" in md
    assert "Brief: brief c" in md
    assert "用途: layout" in md
    assert "[有用 / 一般 / 无用]" in md
    assert "MANUAL" in md or "人工" in md


def test_evidence_without_context_is_unvalidated_not_false(tmp_path):
    path = write_tasks(tmp_path, [create("c", [1], ["layout"])])
    fixture = {"c": {"recommendations": [
        {"image_id": 1, "purposes": [{"purpose": "layout", "reason": "r",
                                     "evidence": [{"field": "analyses.layout.columns", "value": ["multi"]}]}]},
    ]}}
    result = eval_recommendation(load_tasks(path).tasks, FixtureRecommendationAdapter(fixture))
    assert result["metrics"]["evidence_unvalidated"] == 1
    assert result["metrics"]["evidence_valid"] == 0
    assert result["metrics"]["evidence_invalid"] == 0
    assert result["metrics"]["evidence_validity"] is None


def test_partial_purpose_coverage_is_reported(tmp_path):
    path = write_tasks(tmp_path, [create("c", [], ["layout", "style"])])
    fixture = {"c": {"recommendations": [
        {"image_id": 1, "purposes": [{"purpose": "layout", "reason": "r", "evidence": []}]},
    ]}}
    result = eval_recommendation(load_tasks(path).tasks, FixtureRecommendationAdapter(fixture))
    task = result["per_task"][0]
    assert task["purpose_coverage"] == 0.5
    assert task["missing_purposes"] == ["style"]


class FakeSearchApplication:
    def __init__(self, status="ok", items=None):
        self.status = status
        self.items = items or []
        self.queries = []

    def search(self, query):
        self.queries.append(query)
        if self.status != "ok":
            return {"status": self.status, "items": []}
        return {"status": "ok", "items": self.items}


class FakeRecommendationApplication:
    def __init__(self, *, status="ok"):
        self.status = status
        self.calls = []

    def recommend(self, brief, previous):
        self.calls.append((brief, previous))
        if self.status != "ok":
            return {"status": self.status, "items": [], "notices": []}
        return {
            "status": "ok", "effective_query": brief,
            "items": [{
                "image_id": 2, "semantic_score": 0.9, "semantic_rank": 1,
                "media_scope": "cover_and_description", "post_ids": [10],
                "sources": [{"post_id": 10}],
                "purposes": [{"purpose": "layout", "reason": "多栏",
                              "evidence": [{"field": "analyses.layout.columns",
                                            "value": ["multi"]}]}],
                "unmet_requirements": [],
            }],
            "notices": [],
        }


def test_search_application_adapter_status_and_items(tmp_path):
    tasks = load_tasks(write_tasks(tmp_path, [find("f", [2])]))
    adapter = SearchApplicationAdapter(FakeSearchApplication(
        items=[{"image_id": 1, "score": 0.9}, {"image_id": 2, "score": 0.8}]))
    result = eval_search(tasks.tasks, adapter)
    assert result["metrics"]["top3"] == 1
    assert result["per_task"][0]["ranks"] == {"2": 2}
    empty = eval_search(tasks.tasks, SearchApplicationAdapter(FakeSearchApplication("index_empty")))
    assert empty["metrics"]["top20"] == 0
    assert empty["misses"] == ["f"]


def test_recommendation_application_adapter_fields_and_context(tmp_path):
    tasks = load_tasks(write_tasks(tmp_path, [create("c", [2], ["layout"])]))
    app = FakeRecommendationApplication()
    adapter = RecommendationApplicationAdapter(
        app, context_provider=lambda ids: {2: {"sha256": "sha-2",
                                               "analyses": {"layout": {"columns": ["multi"]}}}})
    result = eval_recommendation(tasks.tasks, adapter)
    assert result["metrics"]["image_recall"] == 1.0
    assert result["metrics"]["purpose_coverage"] == 1.0
    assert result["metrics"]["evidence_valid"] == 1
    assert result["metrics"]["video_scope_missing"] == 0
    assert result["metrics"]["sha_violations"] == 0
    assert app.calls == [("brief c", None)]


def test_recommendation_application_adapter_non_ok_is_empty_not_error(tmp_path):
    tasks = load_tasks(write_tasks(tmp_path, [create("c", [2], ["layout"])]))
    result = eval_recommendation(tasks.tasks, RecommendationApplicationAdapter(
        FakeRecommendationApplication(status="no_recommendation")))
    assert result["errors"] == []
    assert result["per_task"][0]["image_recall"] == 0.0


def test_dev_default_and_final_includes_test_group(tmp_path):
    tasks = [
        {**find("dev-find", [1]), "tags": ["layout"], "group": "dev"},
        {**find("test-find", [2]), "tags": ["color"], "group": "test"},
    ]
    path = write_tasks(tmp_path, tasks)
    fixture = tmp_path / "fixture.json"
    fixture.write_text(json.dumps({"dev-find": [1], "test-find": [2]}), encoding="utf-8")
    out = tmp_path / "report.json"
    md = tmp_path / "report.md"
    from tools.eval_suite import main
    assert main(["--tasks", str(path), "--search-fixture", str(fixture),
                 "--output-json", str(out), "--output-md", str(md)]) == 0
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["meta"]["task_group"] == "dev"
    assert report["search"]["tasks_total"] == 1
    assert list(report["search"]["per_column"]) == ["layout"]
    lock = tmp_path / "test.lock.json"
    assert main(["--tasks", str(path), "--final", "--test-lock", str(lock),
                 "--search-fixture", str(fixture),
                 "--output-json", str(out), "--output-md", str(md)]) == 2
    assert main(["--tasks", str(path), "--seal-test", "--test-lock", str(lock)]) == 0
    assert main(["--tasks", str(path), "--search-fixture", str(fixture), "--final",
                 "--test-lock", str(lock),
                 "--output-json", str(out), "--output-md", str(md)]) == 0
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["meta"]["task_group"].startswith("dev+test")
    assert report["search"]["tasks_total"] == 2
    assert set(report["search"]["per_column"]) == {"layout", "color"}


def test_column_search_usage_and_top5(tmp_path):
    tasks = load_tasks(write_tasks(tmp_path, [
        {**find("f", [2]), "tags": ["layout"]},
    ]))
    result = eval_search(tasks.tasks, SearchApplicationAdapter(FakeSearchApplication(
        items=[{"image_id": 1, "score": .9}, {"image_id": 2, "score": .8}])))
    row = result["per_task"][0]
    assert row["column"] == "layout"
    assert row["first_expected_rank"] == 2 and row["requests"] == 1
    bucket = result["per_column"]["layout"]
    assert bucket["top5_hits"] == 1 and bucket["misses"] == 0
    assert bucket["requests"] == 1 and bucket["elapsed_ms"] >= 0


class FakeColumnsApplication:
    def __init__(self):
        self.calls = []

    def recommend(self, brief, previous):
        self.calls.append((brief, previous))
        return {"status": "ok", "effective_query": brief, "needs": {}, "items": [],
                "columns": [
                    {"purpose": "layout", "label": "排版", "status": "ok",
                     "items": [{"image_id": 2, "purpose": "layout", "purposes": [
                         {"purpose": "layout", "reason": "多栏", "evidence": []}]}],
                     "usage": {"requests": 2, "prompt_tokens": 40,
                               "completion_tokens": 10, "latency_ms": 7}},
                    {"purpose": "palette", "label": "配色", "status": "ok", "items": [],
                     "usage": {"requests": 2, "prompt_tokens": 40,
                               "completion_tokens": 10, "latency_ms": 8}},
                    {"purpose": "style", "label": "风格", "status": "ok", "items": [],
                     "usage": {"requests": 0, "prompt_tokens": None,
                               "completion_tokens": None, "latency_ms": 0}},
                ]}


def test_recommendation_columns_adapter_reports_top5_usage(tmp_path):
    tasks = load_tasks(write_tasks(tmp_path, [create("c", [2], [])]))
    result = eval_recommendation(tasks.tasks, RecommendationApplicationAdapter(FakeColumnsApplication()))
    assert result["per_task"][0]["image_recall"] == 1.0
    assert result["per_column"]["layout"]["top5_hits"] == 1
    assert result["per_column"]["layout"]["requests"] == 2
    assert result["per_column"]["layout"]["tokens"] == 50
    assert result["per_column"]["layout"]["elapsed_ms"] == 7


def test_one_create_task_scores_three_column_purposes_once(tmp_path):
    app = FakeColumnsApplication()
    tasks = load_tasks(write_tasks(tmp_path, [create("c", [2], ["layout", "palette", "style"])]))
    result = eval_recommendation(tasks.tasks, RecommendationApplicationAdapter(app))
    assert len(app.calls) == 1  # one recommendation for the whole three-column task
    assert abs(result["per_task"][0]["purpose_coverage"] - 1 / 3) < 1e-6  # only layout has an item
    assert set(result["per_column"]) == {"layout", "palette", "style"}


def test_meta_defaults_to_current_application_versions(tmp_path):
    tasks = load_tasks(write_tasks(tmp_path, []))
    meta = collect_meta(tasks)
    assert meta["search_algorithm_version"] == "search-application-v1"
    assert meta["recommendation_version"] == "day6-application-v1"


def test_cli_end_to_end_empty_tasks(tmp_path):
    from tools.eval_suite import main
    tasks = write_tasks(tmp_path, [])
    md = tmp_path / "report.md"
    js = tmp_path / "report.json"
    code = main(["--tasks", str(tasks), "--output-md", str(md), "--output-json", str(js)])
    assert code == 0
    assert md.exists() and js.exists()
    data = json.loads(js.read_text(encoding="utf-8"))
    assert data["meta"]["task_file_sha256"]
    assert data["search"]["tasks_total"] == 0
