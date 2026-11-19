"""Day4 wiring: v3 migration, store validity, EmbeddingAdapter, analyses.done -> enqueue,
backfill, CLI --pending. No network: vision and embedding endpoints are MockTransports."""
import json
import math
import sqlite3
import struct
import time

import httpx
import pytest

from app import analysis, db, importer, vision
from app.embedding import backfill, store
from app.embedding.adapter import EmbeddingAdapter
from app.embedding.client import EmbeddingClient, EmbeddingSettings
from app.embedding.service import EmbeddingSpec, vector_to_blob
from app.embedding.text import TEXT_VERSION
from conftest import png

DIMS = 4
SECRET = "sk-test-SECRET-do-not-leak"
SETTINGS = EmbeddingSettings(base_url="https://emb.invalid/v1", api_key=SECRET,
                             model="fake-emb", dimensions=DIMS)
SPEC = EmbeddingSpec(model="fake-emb", dimensions=DIMS)

ANSWER = {
    "visible_text": "SUMMER", "subject": ["海报"],
    "layout": {"title_position": ["top"], "columns": ["single"], "whitespace": ["much"],
               "image_text_ratio": ["balanced"], "density": ["low"], "layout_note": ""},
    "style": {"tags": ["极简"], "medium": "typography", "style_note": ""},
    "description": "一张极简海报",
}


@pytest.fixture(autouse=True)
def ai_env(monkeypatch):
    """Never the real .env endpoint/key in tests."""
    monkeypatch.setenv("AI_BASE_URL", "https://model.invalid/v1")
    monkeypatch.setenv("AI_API_KEY", SECRET)
    monkeypatch.setenv("VISION_MODEL", "fake-vl")
    monkeypatch.setattr(vision, "_json_mode", {})


@pytest.fixture
def lib(data_dir):
    db.init_db()
    return data_dir


class FakeEmbed:
    """Embedding endpoint. script entries: int (HTTP status) or callable(texts) -> vectors."""

    def __init__(self, *script):
        self.script = list(script)
        self.batches: list[list[str]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        texts = json.loads(request.content)["input"]
        self.batches.append(texts)
        step = self.script.pop(0) if self.script else None
        if isinstance(step, int):
            return httpx.Response(step, json={"error": {"message": f"status {step} key={SECRET}"}})
        vectors = step(texts) if callable(step) else [[1.0, float(i + 1), 0.5, 0.25] for i in range(len(texts))]
        return httpx.Response(200, json={"data": [{"index": i, "embedding": v} for i, v in enumerate(vectors)]})

    def factory(self, settings):
        return EmbeddingClient(settings, http_client=httpx.Client(transport=httpx.MockTransport(self)))

    @property
    def requests(self) -> int:
        return len(self.batches)


class FakeVision:
    def __init__(self, *script):
        self.script = list(script)
        self.calls = 0

    def __call__(self, request):
        self.calls += 1
        step = self.script.pop(0) if self.script else ANSWER
        if isinstance(step, int):
            return httpx.Response(step, json={"error": {"message": "x"}})
        return httpx.Response(200, json={
            "choices": [{"message": {"content": json.dumps(step, ensure_ascii=False)}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5}})

    def client(self):
        return httpx.Client(transport=httpx.MockTransport(self))


def adapter(fake: FakeEmbed, settings=SETTINGS, flush_seconds=1000.0) -> EmbeddingAdapter:
    return EmbeddingAdapter(settings=settings, client_factory=fake.factory, flush_seconds=flush_seconds)


def add_images(n: int) -> list[int]:
    r = importer.import_images([importer.Upload(f"{i}.png", png((i * 9 % 256, i // 28 * 9, 7)))
                                for i in range(n)])
    return [a["id"] for a in r["added"]]


def mark_done(ids, description="一张极简海报"):
    conn = db.connect()
    try:
        for image_id in ids:
            conn.execute(
                """UPDATE analyses SET status = 'done', model = 'fake-vl', description = ?,
                       subject = '["海报"]', layout_json = '{"columns": ["single"]}',
                       style_json = '{"tags": ["极简"]}', visible_text = ?
                   WHERE image_id = ?""", (description, f"图 {image_id}", image_id))
    finally:
        conn.close()


def emb_rows():
    conn = db.connect()
    try:
        return {r["image_id"]: dict(r) for r in conn.execute("SELECT * FROM embeddings")}
    finally:
        conn.close()


def analyses_rows():
    conn = db.connect()
    try:
        return [tuple(r) for r in conn.execute("SELECT * FROM analyses ORDER BY image_id")]
    finally:
        conn.close()


def wait_for(predicate, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


# ---- migration ----

def test_migrate_v2_to_v3_keeps_legacy_embedding_rows(data_dir):
    (data_dir / "images").mkdir(parents=True, exist_ok=True)
    conn = db.connect()
    conn.executescript(db.SCHEMA)
    for steps in db.MIGRATIONS[:2]:
        for sql in steps:
            conn.execute(sql)
    conn.execute("PRAGMA user_version = 2")
    conn.execute("INSERT INTO posts (id, platform) VALUES (1, 'local')")
    conn.execute("INSERT INTO images (id, post_id, position, sha256, file_path) VALUES (1, 1, 0, 'a', 'images/a.png')")
    conn.execute("INSERT INTO embeddings (image_id, dimension, model, vector) VALUES (1, 'layout', 'old', x'00')")
    conn.close()

    db.init_db()

    conn = db.connect()
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION >= 3
        cols = {r["name"]: r["type"] for r in conn.execute("PRAGMA table_info(embeddings)")}
        assert cols["dimensions"] == "INTEGER" and cols["text_hash"] == "TEXT"
        row = conn.execute("SELECT * FROM embeddings").fetchone()
        assert (row["dimension"], row["model"], row["dimensions"], row["text_hash"]) == ("layout", "old", None, None)
        uniques = [i for i in conn.execute("PRAGMA index_list(embeddings)") if i["unique"]]
        assert [[c["name"] for c in conn.execute(f"PRAGMA index_info('{i['name']}')")] for i in uniques] \
            == [["image_id", "dimension"]]
    finally:
        conn.close()
    assert len(list(data_dir.glob("app.db.bak-*-v2"))) == 1


# ---- validity ----

def good_row(**over):
    row = {"model": "fake-emb", "dimension": TEXT_VERSION, "dimensions": DIMS,
           "text_hash": "h", "vector": vector_to_blob([0.1, 0.2, 0.3, 0.4])}
    row.update(over)
    return row


@pytest.mark.parametrize("over, reason", [
    ({}, None),
    ({"model": "other"}, "model"),
    ({"dimension": "combined_d_v2"}, "scheme"),
    ({"dimensions": 8}, "dimensions"),
    ({"dimensions": None}, "dimensions"),
    ({"text_hash": None}, "text_hash"),
    ({"text_hash": "changed"}, "text_hash"),
    ({"vector": vector_to_blob([0.1, math.nan, 0.3, 0.4])}, "vector"),
    ({"vector": vector_to_blob([0.1, math.inf, 0.3, 0.4])}, "vector"),
    ({"vector": vector_to_blob([0.0, 0.0, 0.0, 0.0])}, "vector"),
    ({"vector": vector_to_blob([0.1, 0.2, 0.3])}, "vector"),
    ({"vector": b"\x00" * 15}, "vector"),
    ({"vector": None}, "vector"),
])
def test_stale_reason(over, reason):
    assert store.stale_reason(good_row(**over), SPEC, "h") == reason
    assert store.stale_reason(None, SPEC, "h") == "missing"


@pytest.mark.parametrize("bad", [[0.1, math.nan, 0.3, 0.4], [0.0] * 4])
def test_corrupt_stored_vector_is_rebuilt_by_backfill(lib, bad):
    [image_id] = add_images(1)
    mark_done([image_id])
    fake = FakeEmbed()
    backfill.run(adapter(fake))
    conn = db.connect()
    conn.execute("UPDATE embeddings SET vector = ? WHERE image_id = ?",
                 (struct.pack("<4f", *bad), image_id))
    conn.close()

    report = backfill.plan(adapter(fake))
    assert (report["stale"], report["stale_reasons"]) == (1, {"vector": 1})
    backfill.run(adapter(fake))
    assert fake.requests == 2
    assert store.vector_problem(emb_rows()[image_id]["vector"], DIMS) is None


# ---- adapter ----

def test_enqueue_returns_at_once_and_sends_nothing_until_flush(lib):
    ids = add_images(3)
    mark_done(ids)
    fake = FakeEmbed()
    a = adapter(fake)
    assert [a.enqueue(i) for i in ids] == ["queued"] * 3
    time.sleep(0.2)
    assert fake.requests == 0 and emb_rows() == {}
    state = a.drain(timeout=5)
    assert fake.requests == 1 and state["embedded"] == 3
    a.shutdown()


def test_duplicates_merge_and_batches_are_at_most_ten(lib):
    ids = add_images(25)
    mark_done(ids)
    fake = FakeEmbed()
    a = adapter(fake)
    for i in ids + ids[:5]:
        a.enqueue(i)
    state = a.shutdown()
    assert [len(b) for b in fake.batches] == [10, 10, 5]
    assert (state["embedded"], state["requests"]) == (25, 3)
    # queued_total counts insertions, not globally unique ids: the consumer may
    # dequeue the first ids before their repeated notifications arrive. They are
    # rechecked against persisted vectors and must still cost exactly 3 requests.
    assert 25 <= state["queued_total"] <= 30
    rows = emb_rows()
    assert sorted(rows) == sorted(ids)
    assert {(r["model"], r["dimension"], r["dimensions"]) for r in rows.values()} == {("fake-emb", TEXT_VERSION, DIMS)}

    # a second pass over valid rows costs nothing
    state = backfill.run(adapter(fake))
    assert fake.requests == 3 and state["embedded"] == 0


def test_full_batch_is_sent_without_waiting_for_flush(lib):
    ids = add_images(10)
    mark_done(ids)
    fake = FakeEmbed()
    a = adapter(fake, flush_seconds=1000)
    for i in ids:
        a.enqueue(i)
    assert wait_for(lambda: len(emb_rows()) == 10)
    a.shutdown()


def test_partial_batch_is_sent_after_flush_seconds(lib):
    ids = add_images(2)
    mark_done(ids)
    fake = FakeEmbed()
    a = adapter(fake, flush_seconds=0.1)
    for i in ids:
        a.enqueue(i)
    assert wait_for(lambda: len(emb_rows()) == 2)
    assert fake.requests == 1
    a.shutdown()


def test_only_done_images_are_embedded(lib):
    ids = add_images(3)
    mark_done(ids[:1])
    fake = FakeEmbed()
    a = adapter(fake)
    for i in ids:
        a.enqueue(i)
    state = a.shutdown()
    assert sorted(emb_rows()) == ids[:1]
    assert (state["embedded"], state["skipped"]) == (1, 2)


def test_commit_failure_rolls_back_and_keeps_old_rows(lib, monkeypatch):
    ids = add_images(2)
    mark_done(ids)
    fake = FakeEmbed()
    adapter(fake).shutdown()
    backfill.run(adapter(fake))
    old = emb_rows()
    mark_done(ids, description="改过的描述")  # both now stale (text hash changed)

    real = store.vector_to_blob
    calls = []

    def flaky(vector):
        calls.append(1)
        if len(calls) == 2:
            raise sqlite3.OperationalError("disk I/O error")
        return real(vector)

    monkeypatch.setattr(store, "vector_to_blob", flaky)
    state = backfill.run(adapter(fake))
    assert (state["embedded"], state["failed"]) == (0, 2)
    assert emb_rows() == old  # first row of the batch was rolled back too


def test_one_failed_batch_does_not_stop_the_others(lib):
    ids = add_images(12)
    mark_done(ids)
    before = analyses_rows()
    fake = FakeEmbed(500)
    state = backfill.run(adapter(fake))
    assert (state["embedded"], state["failed"], state["stopped"]) == (2, 10, False)
    assert SECRET not in state["last_error"]
    assert len(emb_rows()) == 2
    assert analyses_rows() == before  # embedding never touches analyses
    # recovery: the next backfill embeds the ten that failed
    assert backfill.run(adapter(fake))["embedded"] == 10


@pytest.mark.parametrize("status", [401, 403, 404])
def test_auth_or_model_error_stops_the_adapter(lib, status):
    ids = add_images(12)
    mark_done(ids)
    fake = FakeEmbed(status)
    a = adapter(fake)
    for i in ids:
        a.enqueue(i)
    state = a.drain(timeout=5)
    assert state["stopped"] and fake.requests == 1
    assert (state["failed"], state["dropped"]) == (10, 2)
    assert a.enqueue(ids[0]) == "stopped" and fake.requests == 1
    assert emb_rows() == {}
    a.shutdown()


def test_not_configured_skips_without_client(lib):
    [image_id] = add_images(1)
    mark_done([image_id])

    def no_client(_):
        raise AssertionError("must not create a client")

    a = EmbeddingAdapter(settings=EmbeddingSettings(base_url="", api_key="", model="m", dimensions=DIMS),
                         client_factory=no_client)
    assert a.enqueue(image_id) == "not_configured"
    assert a.shutdown()["not_configured"] == 1


def test_dry_run_sends_nothing(lib):
    ids = add_images(3)
    mark_done(ids[:2])

    def no_client(_):
        raise AssertionError("dry-run must not create a client")

    report = backfill.plan(EmbeddingAdapter(settings=SETTINGS, client_factory=no_client))
    assert (report["done"], report["stale"], report["estimated_requests"]) == (2, 2, 1)
    assert report["stale_reasons"] == {"missing": 2}
    assert report["stale_ids"] == ids[:2]


# ---- analyses.done -> enqueue ----

def test_on_done_only_after_done_is_written(lib):
    ids = add_images(2)
    seen = []
    vis = FakeVision(ANSWER, 400)
    with vis.client() as c:
        assert analysis.process_image(ids[0], client=c, sleep=lambda s: None, on_done=seen.append)["status"] == "done"
        assert analysis.process_image(ids[1], client=c, sleep=lambda s: None, on_done=seen.append)["status"] == "failed"
    assert seen == [ids[0]]


def test_on_done_not_called_when_claim_was_lost(lib):
    [image_id] = add_images(1)
    seen = []
    assert analysis.claim("other", image_id) == image_id
    with FakeVision().client() as c:
        analysis.process_image(image_id, token="mine", client=c, sleep=lambda s: None, on_done=seen.append)
    assert seen == []


def test_on_done_failure_keeps_analysis_done(lib):
    [image_id] = add_images(1)

    def boom(_):
        raise RuntimeError(f"embedding down {SECRET}")

    with FakeVision().client() as c:
        assert analysis.process_image(image_id, client=c, sleep=lambda s: None, on_done=boom)["status"] == "done"
    assert analysis.get_analysis(image_id)["analysis"]["status"] == "done"


def test_default_process_image_makes_no_embedding_request(lib, monkeypatch):
    [image_id] = add_images(1)

    def forbidden(self, batch):
        raise AssertionError("no embedding HTTP by default")

    monkeypatch.setattr(EmbeddingClient, "_embed_batch", forbidden)
    with FakeVision().client() as c:
        assert analysis.process_image(image_id, client=c, sleep=lambda s: None)["status"] == "done"
    assert emb_rows() == {}


def test_worker_enqueues_and_adapter_embeds_in_one_batch(lib):
    ids = add_images(3)
    fake = FakeEmbed()
    a = adapter(fake)
    vis = FakeVision()
    worker = analysis.Worker(client_factory=vis.client, sleep=lambda s: None, on_done=a.enqueue)
    with vis.client() as c:
        worker._running.set()
        while worker.run_once(c):
            pass
    state = a.shutdown()
    assert vis.calls == 3
    assert (state["embedded"], fake.requests) == (3, 1)
    assert sorted(emb_rows()) == sorted(ids)


def test_cli_pending_runs_queue_without_redoing_done(lib):
    ids = add_images(4)
    mark_done(ids[:1])
    fake = FakeEmbed()
    a = adapter(fake)
    vis = FakeVision()
    lines = []
    with vis.client() as c:
        n = analysis.run_batch(client=c, on_done=a.enqueue, sleep=lambda s: None, out=lines.append)
    assert n == 3 and vis.calls == 3
    a.shutdown()
    assert analysis.summary()["done"] == 4
    assert sorted(emb_rows()) == sorted(ids[1:])  # the earlier done one waits for backfill
    assert backfill.plan(adapter(fake))["stale_ids"] == ids[:1]


def test_cli_pending_limit_and_ids(lib):
    ids = add_images(4)
    vis = FakeVision()
    with vis.client() as c:
        assert analysis.run_batch(client=c, limit=2, sleep=lambda s: None, out=lambda s: None) == 2
        assert analysis.run_batch(client=c, ids=[ids[0]], sleep=lambda s: None, out=lambda s: None) == 1
    assert vis.calls == 2  # --pending took ids[0], ids[1] (grid order); --ids ids[0] is done: no request
    assert analysis.summary()["done"] == 2


def test_create_app_wires_worker_to_the_embedder(lib, monkeypatch):
    from app import main

    captured = {}
    real = analysis.Worker

    def spy(*args, **kwargs):
        captured.update(kwargs)
        return real(*args, **kwargs)

    monkeypatch.setattr(main.analysis, "Worker", spy)
    fake = adapter(FakeEmbed())
    main.create_app(embedder=fake)
    assert captured["on_done"] == fake.enqueue
