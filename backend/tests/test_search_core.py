"""Tests for day-5 search core: SearchService, index service, evaluation, repository."""
from __future__ import annotations

import random
import sqlite3
import time
from array import array

import pytest

from app.embedding import (
    EmbeddingClient,
    EmbeddingError,
    EmbeddingIndexService,
    EmbeddingSettings,
    EmbeddingSpec,
    EvalQuery,
    IndexTask,
    RepositoryError,
    SCOPE_GLOBAL,
    SCOPE_INPUT,
    SCOPE_UNKNOWN,
    SearchRow,
    SearchService,
    build_embedding_components,
    evaluate,
    load_embeddings,
    load_queries,
    load_search_rows,
    upsert_embedding,
)
from app.embedding.index_service import IndexedEmbedding
from app.embedding.service import needs_rebuild, vector_to_blob
from app.embedding.text import build_combined_d_v1, text_hash

SPEC = EmbeddingSpec(model="qwen3.7-text-embedding-flash", dimensions=1024, scheme="combined_d_v1")
HASH = "a" * 64


def valid_row(image_id: int, vector=None, **overrides) -> SearchRow:
    data = dict(
        image_id=image_id,
        vector=vector if vector is not None else [1.0] + [0.0] * 1023,
        model=SPEC.model,
        dimension=SPEC.scheme,
        dimensions=SPEC.dimensions,
        text_hash=HASH,
        current_text_hash=HASH,
    )
    data.update(overrides)
    return SearchRow(**data)


class QueryClient:
    def __init__(self, vector=None, error: Exception | None = None):
        self.vector = vector if vector is not None else [1.0] + [0.0] * 1023
        self.error = error
        self.calls = 0

    def embed_one(self, text: str):
        self.calls += 1
        if self.error:
            raise self.error
        return self.vector


# ----------------------------- SearchService -----------------------------

def test_search_returns_top20_sorted():
    client = QueryClient([1.0, 0.0])
    service = SearchService(client, EmbeddingSpec(model="m", dimensions=2, scheme="combined_d_v1"))
    rows = [
        valid_row(1, [1.0, 0.0], model="m", dimensions=2),
        valid_row(2, [0.5, 0.5], model="m", dimensions=2),
        valid_row(3, [0.0, 1.0], model="m", dimensions=2),
    ]
    hits = service.search("黑底白线", rows, top_n=20)
    assert [hit.image_id for hit in hits] == [1, 2, 3]
    assert len(hits) == 3
    assert client.calls == 1


def test_search_returns_empty_without_valid_vectors_and_does_not_call_api():
    client = QueryClient([1.0, 0.0])
    service = SearchService(client, EmbeddingSpec(model="m", dimensions=2, scheme="combined_d_v1"))
    rows = [valid_row(1, [1.0, 0.0], model="old", dimensions=2)]
    assert service.search("query", rows) == []
    assert client.calls == 0


def test_search_ignores_old_model_dimensions_scheme_and_stale_hash():
    client = QueryClient([1.0, 0.0])
    service = SearchService(client, EmbeddingSpec(model="m", dimensions=2, scheme="combined_d_v1"))
    rows = [
        valid_row(1, [1.0, 0.0], model="m", dimensions=2),                     # valid
        valid_row(2, [1.0, 0.0], model="old", dimensions=2),                   # old model
        valid_row(3, [1.0, 0.0], model="m", dimensions=2, dimension="old"),    # old scheme
        valid_row(4, [1.0, 0.0], model="m", dimensions=3),                     # old dimensions
        valid_row(5, [1.0, 0.0], model="m", dimensions=2, text_hash="b" * 64), # stale text
        valid_row(6, [1.0, 0.0], model="m", dimensions=2, text_hash="nothex"),
        valid_row(7, [1.0, 0.0, 0.0], model="m", dimensions=2),                # bad length
    ]
    hits = service.search("query", rows, top_n=20)
    assert [hit.image_id for hit in hits] == [1]


def test_search_propagates_embedding_failure():
    client = QueryClient(error=EmbeddingError("http", "boom"))
    service = SearchService(client, EmbeddingSpec(model="m", dimensions=2, scheme="combined_d_v1"))
    with pytest.raises(EmbeddingError):
        service.search("query", [valid_row(1, [1.0, 0.0], model="m", dimensions=2)])


def test_search_ties_are_stable_numeric_image_id_not_string():
    client = QueryClient([1.0, 0.0])
    service = SearchService(client, EmbeddingSpec(model="m", dimensions=2, scheme="combined_d_v1"))
    rows = [valid_row(i, [1.0, 0.0], model="m", dimensions=2) for i in (10, 3, 2)]
    hits = service.search("query", rows, top_n=20)
    assert [hit.image_id for hit in hits] == [2, 3, 10]  # string sort would put 10 first
    assert all(hit.score == pytest.approx(1.0) for hit in hits)


def test_search_performance_smoke_5000_vectors():
    dims = 1024
    rng = random.Random(42)
    rows = []
    for image_id in range(5000):
        vector = array("f", (rng.random() for _ in range(dims)))
        rows.append(SearchRow(
            image_id=image_id,
            vector=vector,
            model=SPEC.model,
            dimension=SPEC.scheme,
            dimensions=dims,
            text_hash=HASH,
            current_text_hash=HASH,
        ))
    client = QueryClient(array("f", (rng.random() for _ in range(dims))))
    service = SearchService(client, SPEC)
    started = time.perf_counter()
    hits = service.search("很空的黑底白线海报", rows, top_n=20)
    elapsed = time.perf_counter() - started
    assert len(hits) == 20
    assert elapsed < 8.0, f"5000 x 1024 search took {elapsed:.2f}s"


# ----------------------------- index service -----------------------------

class BatchClient:
    def __init__(self, dims=1024, fail_tokens=(), scope=SCOPE_UNKNOWN, fail_all=False):
        self.dims = dims
        self.fail_tokens = tuple(fail_tokens)
        self.scope = scope
        self.fail_all = fail_all
        self.batch_sizes: list[int] = []
        self.calls = 0

    def _vector(self):
        return [0.1] * self.dims

    def embed(self, texts):
        self.calls += 1
        self.batch_sizes.append(len(texts))
        if self.fail_all or any(token in " ".join(texts) for token in self.fail_tokens):
            raise EmbeddingError("http", "batch boom", scope=self.scope)
        return [self._vector() for _ in texts]

    def embed_one(self, text):
        return self.embed([text])[0]


def task(image_id: int, text: str) -> IndexTask:
    return IndexTask(image_id=image_id, analysis={"description": text}, colors=[])


def test_index_batches_at_most_10_and_reports_stats():
    client = BatchClient()
    service = EmbeddingIndexService(client, SPEC)
    tasks = [task(i, f"图片 {i}") for i in range(23)]
    report = service.index(tasks)
    assert report.indexed == 23
    assert report.skipped == 0
    assert report.failed == 0
    assert client.batch_sizes == [10, 10, 3]


def test_index_rerun_does_not_call_api_again():
    client = BatchClient()
    service = EmbeddingIndexService(client, SPEC)
    tasks = [task(i, f"图片 {i}") for i in range(5)]
    first = service.index(tasks)
    assert first.indexed == 5
    service.mark_persisted(first.rows)
    calls_after_first = client.calls
    second = service.index(tasks)
    assert second.indexed == 0
    assert second.skipped == 5
    assert client.calls == calls_after_first


def test_index_rerun_with_persisted_rows_skips_after_restart():
    client = BatchClient()
    service = EmbeddingIndexService(client, SPEC)
    tasks = [task(i, f"图片 {i}") for i in range(4)]
    first = service.index(tasks)
    calls_after_first = client.calls
    restarted = EmbeddingIndexService(client, SPEC, existing=first.rows)
    second = restarted.index(tasks)
    assert second.indexed == 0
    assert second.skipped == 4
    assert client.calls == calls_after_first


def test_index_only_rebuilds_missing_or_stale_rows():
    client = BatchClient()
    existing = IndexedEmbedding(
        image_id=1,
        vector=tuple([0.1] * 1024),
        model=SPEC.model,
        dimension=SPEC.scheme,
        dimensions=SPEC.dimensions,
        text_hash=text_hash(build_combined_d_v1({"description": "图片 1"}, [])),
    )
    service = EmbeddingIndexService(client, SPEC, existing=[existing])
    tasks = [task(1, "图片 1"), task(2, "图片 2"), task(3, "图片 3")]
    report = service.index(tasks)
    assert report.indexed == 2
    assert report.skipped == 1
    assert client.batch_sizes == [2]


def test_index_safe_input_error_is_isolated_per_image():
    client = BatchClient(fail_tokens=("坏",), scope=SCOPE_INPUT)
    service = EmbeddingIndexService(client, SPEC)
    tasks = [task(1, "好一"), task(2, "坏图"), task(3, "好二")]
    report = service.index(tasks, persist=lambda row: None)
    assert report.indexed == 2
    assert report.failed == 1
    assert [failure.image_id for failure in report.failures] == [2]
    assert set(service.rows) == {1, 3}
    assert client.batch_sizes == [3, 1, 1, 1]   # batch + one per item
    assert report.requests == 4


def test_index_auth_failure_is_one_request_no_split():
    client = BatchClient(fail_all=True, scope=SCOPE_GLOBAL)
    service = EmbeddingIndexService(client, SPEC)
    report = service.index([task(1, "一"), task(2, "二"), task(3, "三")])
    assert report.indexed == 0
    assert report.failed == 3
    assert report.requests == 1
    assert client.batch_sizes == [3]            # no per-image retry
    assert {failure.scope for failure in report.failures} == {SCOPE_GLOBAL}


def test_index_timeout_or_unknown_is_one_request_no_split():
    client = BatchClient(fail_all=True, scope=SCOPE_UNKNOWN)
    service = EmbeddingIndexService(client, SPEC)
    report = service.index([task(1, "一"), task(2, "二")])
    assert report.failed == 2
    assert report.requests == 1
    assert client.batch_sizes == [2]
    assert {failure.scope for failure in report.failures} == {SCOPE_UNKNOWN}


def test_index_persist_failure_is_retried_and_never_cached():
    client = BatchClient()
    service = EmbeddingIndexService(client, SPEC)
    tasks = [task(1, "一")]
    attempts: list[int] = []

    def broken_persist(row):
        attempts.append(row.image_id)
        raise RuntimeError("sqlite locked")

    first = service.index(tasks, persist=broken_persist)
    assert first.indexed == 0 and first.failed == 1
    assert first.failures[0].kind == "store"
    assert service.rows == {}
    assert attempts == [1]

    second = service.index(tasks, persist=lambda row: None)
    assert second.indexed == 1
    assert second.requests == 1   # per call, not cumulative
    assert set(service.rows) == {1}


def test_two_entry_points_share_persisted_row_via_provider():
    db: dict[int, dict] = {}
    client = BatchClient()

    def provider(image_id: int):
        return db.get(image_id)

    def persist(row):
        db[row.image_id] = {
            "image_id": row.image_id, "model": row.model, "dimension": row.dimension,
            "dimensions": row.dimensions, "text_hash": row.text_hash,
            "vector": vector_to_blob(list(row.vector)),
        }

    first_service = EmbeddingIndexService(client, SPEC, existing_provider=provider)
    first = first_service.index([task(1, "同一张")], persist=persist)
    assert first.indexed == 1 and first.requests == 1

    # A second entry point (e.g. backfill) re-reads the committed row under the
    # per-image lock and must not embed the same image again.
    second_service = EmbeddingIndexService(client, SPEC, existing_provider=provider)
    second = second_service.index([task(1, "同一张")], persist=persist)
    assert second.skipped == 1
    assert second.requests == 0


def test_index_duplicate_image_id_in_one_batch_requests_once():
    client = BatchClient()
    service = EmbeddingIndexService(client, SPEC)
    tasks = [task(1, "同一张"), task(1, "同一张重复"), task(2, "另一张")]
    report = service.index(tasks, persist=lambda row: None)
    assert report.duplicates == 1
    assert report.requests == 1
    assert client.batch_sizes == [2]
    assert [row.image_id for row in report.rows] == [1, 2]


def test_index_skips_empty_text_without_api_call():
    client = BatchClient()
    service = EmbeddingIndexService(client, SPEC)
    report = service.index([IndexTask(image_id=9, analysis={"visible_text": ""}, colors=[])])
    assert report.indexed == 0
    assert report.skipped == 1
    assert client.calls == 0


def test_index_mapping_existing_accepts_dict_and_blob():
    task_one = task(1, "图片 1")
    h = text_hash(build_combined_d_v1(task_one.analysis, []))
    valid = {
        "image_id": 1, "model": SPEC.model, "dimension": SPEC.scheme,
        "dimensions": SPEC.dimensions, "text_hash": h,
        "vector": vector_to_blob([0.1] * 1024),
    }
    client = BatchClient()
    service = EmbeddingIndexService(client, SPEC, existing=[valid])
    report = service.index([task_one])
    assert report.skipped == 1 and report.requests == 0

    corrupt = dict(valid, vector=b"bad")
    service2 = EmbeddingIndexService(client, SPEC, existing=[corrupt])
    report2 = service2.index([task_one])
    assert report2.indexed == 1 and report2.requests == 1


def test_client_and_spec_are_derived_from_same_config():
    settings = EmbeddingSettings(base_url="https://example.test/v1", api_key="k",
                                 model="m", dimensions=4)
    client, spec = build_embedding_components(settings)
    assert client.settings is settings
    assert (spec.model, spec.dimensions, spec.scheme, spec.text_version) == (
        "m", 4, "combined_d_v1", "combined_d_v1")
    SearchService(client, spec)
    EmbeddingIndexService(client, spec)
    with pytest.raises(ValueError):
        SearchService(client, EmbeddingSpec(model="other", dimensions=4, scheme="combined_d_v1"))
    with pytest.raises(ValueError):
        EmbeddingIndexService(client, EmbeddingSpec(model="m", dimensions=8, scheme="combined_d_v1"))


def test_needs_rebuild_rejects_nan_zero_and_corrupt_blob():
    spec = EmbeddingSpec(model=SPEC.model, dimensions=4, scheme=SPEC.scheme)
    base = {"model": SPEC.model, "dimensions": 4, "scheme": SPEC.scheme,
            "text_version": SPEC.text_version, "text_hash": HASH}
    assert needs_rebuild({**base, "vector": b"bad"}, spec, HASH) is True
    assert needs_rebuild({**base, "vector": [1.0, 0.0, 0.0, float("nan")]}, spec, HASH) is True
    assert needs_rebuild({**base, "vector": [0.0, 0.0, 0.0, 0.0]}, spec, HASH) is True
    assert needs_rebuild({**base, "vector": [1.0, 0.0, 0.0, 0.0]}, spec, HASH) is False


# ----------------------------- evaluation -----------------------------

class FakeHit:
    def __init__(self, image_id: int, score: float):
        self.image_id = image_id
        self.score = score


def test_evaluate_reports_metrics_ranks_and_failures():
    queries = [
        EvalQuery("q1", "命中第一", (10,)),
        EvalQuery("q2", "命中第三", (20,)),
        EvalQuery("q3", "未命中", (30,)),
        EvalQuery("q4", "诊断项", ()),
    ]

    def search(query: str, limit: int):
        if query == "命中第一":
            return [FakeHit(10, 0.9), FakeHit(11, 0.8)]
        if query == "命中第三":
            return [FakeHit(21, 0.9), FakeHit(22, 0.8), FakeHit(20, 0.7)]
        return [FakeHit(31, 0.5)]

    result = evaluate(search, queries, top_n=5, rank_depth=20)
    metrics = result["metrics"]
    assert metrics["queries"] == 3
    assert metrics["top1"] == 1
    assert metrics["top3"] == 2
    assert metrics["top5"] == 2
    assert [row["rank"] for row in result["per_query"]] == [1, 3, None, None]
    assert [failure["id"] for failure in result["failures"]] == ["q3"]
    assert result["per_query"][1]["top"][2]["image_id"] == 20


def test_load_queries_accepts_list_and_object(tmp_path):
    payload = [
        {"id": "a", "query": "查询一", "expected": [1, 2]},
        {"id": "b", "query": "查询二", "expected": 3},
    ]
    path = tmp_path / "q.json"
    path.write_text(__import__("json").dumps(payload, ensure_ascii=False), encoding="utf-8")
    queries = load_queries(path)
    assert queries[0].expected == (1, 2)
    assert queries[1].expected == (3,)

    path.write_text(__import__("json").dumps({"queries": payload}, ensure_ascii=False), encoding="utf-8")
    assert len(load_queries(path)) == 2


# ----------------------------- repository -----------------------------

def make_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE analyses (
            image_id INTEGER PRIMARY KEY, status TEXT, visible_text TEXT,
            subject TEXT, layout_json TEXT, style_json TEXT, description TEXT
        );
        CREATE TABLE colors (
            id INTEGER PRIMARY KEY, image_id INTEGER, rank INTEGER, hex TEXT, ratio REAL
        );
        CREATE TABLE embeddings (
            id INTEGER PRIMARY KEY, image_id INTEGER, dimension TEXT, model TEXT,
            vector BLOB, dimensions INTEGER, text_hash TEXT,
            UNIQUE(image_id, dimension)
        );
        """
    )
    return conn


def test_repository_loads_only_current_rows_and_upserts():
    conn = make_db()
    analysis = {
        "description": "一张黑底白线的极简海报",
        "subject": '["海报"]',
        "layout_json": '{"title_position":["top"],"columns":["single"]}',
        "style_json": '{"tags":["极简"],"medium":"typography"}',
        "visible_text": "TYPE FORM",
    }
    conn.execute(
        "INSERT INTO analyses VALUES (1,'done',?,?,?,?,?)",
        (analysis["visible_text"], analysis["subject"], analysis["layout_json"],
         analysis["style_json"], analysis["description"]),
    )
    conn.execute("INSERT INTO colors (image_id, rank, hex, ratio) VALUES (1,0,'#111111',0.8)")
    text = build_combined_d_v1(analysis, [{"hex": "#111111", "ratio": 0.8, "rank": 0}])
    h = text_hash(text)
    conn.execute(
        "INSERT INTO embeddings (image_id, dimension, model, vector, dimensions, text_hash) VALUES (1,?,?,?,?,?)",
        (SPEC.scheme, SPEC.model, vector_to_blob([1.0] + [0.0] * 1023), SPEC.dimensions, h),
    )
    rows = load_search_rows(conn, SPEC)
    assert len(rows) == 1
    assert rows[0].text_hash == rows[0].current_text_hash == h

    replacement = IndexedEmbedding(
        image_id=1, vector=tuple([0.5] * 1024), model=SPEC.model,
        dimension=SPEC.scheme, dimensions=SPEC.dimensions, text_hash="c" * 64,
    )
    upsert_embedding(conn, replacement)
    stored = conn.execute("SELECT text_hash, dimensions FROM embeddings WHERE image_id=1").fetchone()
    assert stored["text_hash"] == "c" * 64
    assert stored["dimensions"] == SPEC.dimensions
    assert conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0] == 1


def test_repository_explains_missing_migration_columns():
    conn = sqlite3.connect(":memory:")
    conn.executescript("CREATE TABLE embeddings (image_id INTEGER, dimension TEXT, model TEXT, vector BLOB)")
    with pytest.raises(RepositoryError) as exc:
        load_search_rows(conn, SPEC)
    assert "迁移" in str(exc.value)
