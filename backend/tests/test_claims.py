"""Audit fixes: atomic claim across executors, worker crash state, error redaction,
per-image request cap, schema v1 -> v2. No network (httpx.MockTransport)."""
import json
import logging
import sqlite3
import threading
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from app import analysis, db, vision
from app.main import create_app
from test_analysis import GOOD, SECRET, FakeModel, add_image, ai_env, lib, row, run  # noqa: F401


def slow(answer=GOOD, seconds=0.3):
    """A model step that takes a while, so concurrent executors really overlap."""
    def step(request):
        time.sleep(seconds)
        body = {"choices": [{"message": {"content": json.dumps(answer, ensure_ascii=False)}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 20}}
        return httpx.Response(200, json=body)
    return step


def wait_until(cond, timeout=10.0):
    deadline = time.monotonic() + timeout
    while not cond():
        if time.monotonic() > deadline:
            raise AssertionError("timed out")
        time.sleep(0.02)


def worker_for(model: FakeModel) -> analysis.Worker:
    return analysis.Worker(client_factory=model.client, sleep=lambda s: None)


# ---- 1. atomic claim ----

def test_two_cli_executors_send_one_request(lib):
    image_id = add_image()
    model = FakeModel(slow(), slow())
    barrier = threading.Barrier(2)
    results = []

    def executor():
        barrier.wait()
        results.append(run(image_id, model))

    threads = [threading.Thread(target=executor) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert model.calls == 1
    assert sorted(r["status"] for r in results) == ["claimed_elsewhere", "done"]
    r = row(image_id)
    assert (r["status"], r["attempts"], r["prompt_tokens"], r["completion_tokens"]) == ("done", 1, 100, 20)
    assert (r["claimed_by"], r["claimed_at"]) == (None, None)


def test_worker_and_cli_are_mutually_exclusive(lib):
    image_id = add_image()
    model = FakeModel(slow(seconds=0.5), slow())
    worker = worker_for(model)
    worker.start()
    try:
        wait_until(lambda: model.calls == 1)       # worker holds the claim, mid-request
        assert row(image_id)["claimed_by"] == worker.token
        assert run(image_id, model)["status"] == "claimed_elsewhere"
        wait_until(lambda: row(image_id)["status"] == "done")
    finally:
        worker.shutdown()
    assert model.calls == 1
    assert (row(image_id)["attempts"], row(image_id)["prompt_tokens"]) == (1, 100)


def test_two_starts_make_one_thread_and_one_request(lib, backend_options):
    image_id = add_image()
    model = FakeModel(slow(), slow())
    worker = worker_for(model)
    with TestClient(create_app(worker), backend_options=backend_options) as c:
        c.post("/api/analysis/start")
        c.post("/api/analysis/start")
        wait_until(lambda: row(image_id)["status"] == "done")
        alive = [t for t in threading.enumerate() if t.name == "analysis-worker" and t.is_alive()]
        assert len(alive) == 1
    assert model.calls == 1


def test_claim_records_unix_time_and_skips_claimed_rows(lib):
    a, b = add_image("red"), add_image("blue")
    before = int(time.time())
    first = analysis.claim("t1")
    second = analysis.claim("t2")
    assert {first, second} == {a, b}
    assert analysis.claim("t3") is None
    assert before <= row(first)["claimed_at"] <= int(time.time())
    assert isinstance(row(first)["claimed_at"], int)
    st = analysis.summary()
    assert (st["pending"], st["processing"]) == (0, 2)


def test_stale_claim_becomes_failed_and_is_not_resent(lib):
    image_id = add_image()
    t0 = 1_800_000_000
    assert analysis.claim("dead-executor", now=t0) == image_id
    ttl = analysis.ANALYSIS_CLAIM_TTL_SECONDS
    assert analysis.claim("other", now=t0 + ttl - 1) is None      # lease still valid
    assert row(image_id)["status"] == "pending"
    assert analysis.claim("other", now=t0 + ttl) is None          # expired -> failed, not resent
    r = row(image_id)
    assert (r["status"], r["error"], r["claimed_by"]) == ("failed", analysis.INTERRUPTED, None)
    assert "结果未知" in r["error"] and "手动重试" in r["error"]
    # the dead executor can no longer write a result
    conn = db.connect()
    try:
        assert not analysis._record(conn, image_id, "dead-executor", "done", vision.Calls(count=1),
                                    "x", result=vision.validate(GOOD))
    finally:
        conn.close()
    assert row(image_id)["status"] == "failed"
    assert analysis.retry(image_id) == 1 and analysis.claim("other") == image_id


def test_app_start_expires_stale_claims(lib, backend_options):
    image_id = add_image()
    analysis.claim("dead", now=int(time.time()) - analysis.ANALYSIS_CLAIM_TTL_SECONDS - 5)
    with TestClient(create_app(), backend_options=backend_options) as c:
        assert c.get(f"/api/images/{image_id}").json()["analysis"]["error"] == analysis.INTERRUPTED


def test_missing_config_releases_claim(lib, monkeypatch):
    monkeypatch.setenv("AI_API_KEY", "")
    image_id = add_image()
    with pytest.raises(vision.VisionError):
        run(image_id, FakeModel())
    r = row(image_id)
    assert (r["status"], r["claimed_by"]) == ("pending", None)


# ---- 2. worker exits cleanly ----

def test_worker_claim_error_clears_running(lib, monkeypatch):
    add_image()

    def broken(*args, **kwargs):
        raise sqlite3.OperationalError(f"disk I/O error Bearer {SECRET}")
    monkeypatch.setattr(analysis, "claim", broken)
    model = FakeModel()
    worker = worker_for(model)
    worker.start()
    wait_until(lambda: not worker._thread.is_alive())
    st = worker.state()
    assert st["running"] is False
    assert "OperationalError" in st["last_error"] and SECRET not in st["last_error"]
    assert model.calls == 0
    monkeypatch.undo()


def test_pause_lets_current_image_finish_but_claims_no_more(lib):
    first, second = add_image("red"), add_image("blue")
    worker = None

    def pause_mid_request(request):
        worker.pause()
        return slow(seconds=0.1)(request)
    model = FakeModel(pause_mid_request, GOOD)
    worker = worker_for(model)
    worker.start()
    try:
        wait_until(lambda: analysis.summary()["done"] == 1)
        time.sleep(0.3)
        assert model.calls == 1
        assert not worker.running
        statuses = sorted(row(i)["status"] for i in (first, second))
        assert statuses == ["done", "pending"]
        assert all(row(i)["claimed_by"] is None for i in (first, second))
    finally:
        worker.shutdown()


def test_single_image_failure_does_not_stop_the_queue(lib):
    add_image("red"), add_image("blue")
    model = FakeModel(400, GOOD)
    worker = worker_for(model)
    worker.start()
    try:
        wait_until(lambda: analysis.summary()["pending"] == 0 and analysis.summary()["processing"] == 0)
    finally:
        worker.shutdown()
    st = analysis.summary()
    assert (st["done"], st["failed"]) == (1, 1)
    assert worker.last_error is None


# ---- 3. redaction ----

B64 = "data:image/jpeg;base64," + "QUJD" * 50


def echo(status):
    def step(request):
        msg = f"bad request, got header Authorization: Bearer {SECRET} body {B64} key={SECRET}"
        return httpx.Response(status, json={"error": {"message": msg}})
    return step


def assert_clean(text):
    assert SECRET not in text
    assert "QUJDQUJD" not in text


def test_upstream_echo_is_redacted_everywhere(lib, backend_options, caplog):
    caplog.set_level(logging.DEBUG)
    plain, invalid, auth = add_image("red"), add_image("green"), add_image("blue")

    out = run(plain, FakeModel(echo(400)))
    assert out["status"] == "failed"
    assert_clean(out["error"])
    assert "<已隐藏>" in row(plain)["error"]

    leaky = f"not json Bearer {SECRET} {B64}"
    run(invalid, FakeModel(leaky, leaky))
    assert row(invalid)["error"].startswith("invalid:")

    with pytest.raises(vision.VisionError) as exc:
        run(auth, FakeModel(echo(401)))
    assert_clean(str(exc.value))

    for image_id in (plain, invalid, auth):
        assert_clean(json.dumps(row(image_id), ensure_ascii=False))

    worker = worker_for(FakeModel(echo(401)))
    analysis.retry(auth)
    with TestClient(create_app(worker), backend_options=backend_options) as c:
        c.post("/api/analysis/start")
        wait_until(lambda: not worker.running)
        assert "auth" in (worker.last_error or "")
        assert_clean(c.get("/api/analysis/status").text)
        for image_id in (plain, invalid, auth):
            assert_clean(c.get(f"/api/images/{image_id}").text)
    assert_clean(caplog.text)


def test_redact_patterns(monkeypatch):
    monkeypatch.setenv("AI_API_KEY", "sk-abcdefgh1234")
    out = vision.redact('k=sk-abcdefgh1234 Authorization: Bearer xyz {"authorization": "tok123"} '
                        "data:image/png;base64,AAAA+/== end")
    assert "sk-abcdefgh1234" not in out and "xyz" not in out and "tok123" not in out
    assert "AAAA" not in out and out.endswith("end")
    assert len(vision.redact("x" * 2000)) <= vision.MAX_ERROR_LEN + 1
    assert len(vision.redact("x" * 2000, max_len=None)) == 2000


# ---- 5. per-image request cap ----

def reject_response_format(request):
    return httpx.Response(400, json={"error": {"message": "response_format is not supported"}})


CONNECT = httpx.ConnectError("refused")


@pytest.mark.parametrize("script, calls, status, kind", [
    ([GOOD], 1, "done", None),
    ([CONNECT, 503, 429, GOOD], 3, "failed", "network"),
    ([CONNECT, GOOD], 2, "done", None),
    ([httpx.ReadTimeout("t"), GOOD], 1, "failed", "unknown"),
    ([httpx.WriteTimeout("t"), GOOD], 1, "failed", "unknown"),
    ([httpx.RemoteProtocolError("dropped"), GOOD], 1, "failed", "unknown"),
    ([500, GOOD], 1, "failed", "http"),
    ([reject_response_format, "bad", "bad", GOOD], 3, "failed", "invalid"),
    ([reject_response_format, 503, 503, GOOD], 3, "failed", "network"),
    (["bad", 503, 503, GOOD], 3, "failed", "network"),
    ([503, "bad", 503, GOOD], 3, "failed", "network"),
    ([CONNECT, CONNECT, "bad", GOOD], 3, "failed", "limit"),
    ([503, "bad", GOOD], 3, "done", None),
    # the fallback is not stacked: a second rejection is a plain failure
    ([reject_response_format, reject_response_format, GOOD], 2, "failed", "http"),
])
def test_request_cap_per_image(lib, script, calls, status, kind):
    image_id = add_image()
    model = FakeModel(*script)
    out = run(image_id, model)
    assert model.calls == calls <= vision.MAX_REQUESTS
    r = row(image_id)
    assert (out["status"], r["status"], r["attempts"]) == (status, status, calls)
    if kind:
        assert r["error"].startswith(kind + ":")
    if kind == "unknown":
        assert "结果未知" in r["error"] and r["prompt_tokens"] is None


# ---- schema v1 -> v2 ----

def test_migrate_v1_to_v2_keeps_analyses(data_dir):
    (data_dir / "images").mkdir(parents=True, exist_ok=True)
    conn = db.connect()
    conn.executescript(db.SCHEMA)
    for sql in db.MIGRATIONS[0]:
        conn.execute(sql)
    conn.execute("PRAGMA user_version = 1")
    conn.execute("INSERT INTO posts (id, platform) VALUES (1, 'local')")
    conn.execute("""INSERT INTO images (id, post_id, position, sha256, file_path)
                    VALUES (1, 1, 0, 'a', 'images/a.png'), (2, 1, 1, 'b', 'images/b.png')""")
    conn.execute("""INSERT INTO analyses (image_id, status, description, prompt_tokens, attempts)
                    VALUES (1, 'done', '旧结果', 1234, 2), (2, 'pending', NULL, NULL, 0)""")
    before = [tuple(r) for r in conn.execute("SELECT * FROM analyses ORDER BY image_id")]
    conn.close()

    db.init_db()
    db.init_db()  # idempotent

    conn = db.connect()
    try:
        # migrates to the latest version (v2 claim columns, plus anything added later)
        assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION >= 2
        after = conn.execute("SELECT * FROM analyses ORDER BY image_id").fetchall()
        assert [tuple(r)[:len(before[0])] for r in after] == before
        assert [(r["claimed_by"], r["claimed_at"]) for r in after] == [(None, None)] * 2
    finally:
        conn.close()
    assert len(list(data_dir.glob("app.db.bak-*-v1"))) == 1
    assert analysis.claim("t") == 2  # only the pending row is claimable
