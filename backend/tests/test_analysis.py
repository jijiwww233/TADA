"""Vision analysis + Pillow colors. No network: the model endpoint is an httpx.MockTransport."""
import io
import json
import logging
from threading import Event
import time

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import analysis, colors, db, importer, vision
from app.main import create_app
from conftest import png

SECRET = "sk-test-SECRET-do-not-leak"

GOOD = {
    "visible_text": "SUMMER\n夏日展",
    "subject": ["展览", "海报"],
    "layout": {"title_position": ["top"], "columns": "single", "whitespace": ["Much"],
               "image_text_ratio": ["balanced"], "density": ["low"],
               "layout_note": "大标题压顶，下方留白"},
    "style": {"tags": ["极简", "瑞士风格"], "medium": "typography", "style_note": "无衬线大字"},
    "description": "一张极简的展览海报",
    "palette": ["red"],  # not asked for: dropped
}


@pytest.fixture(autouse=True)
def ai_env(monkeypatch):
    """Never the real .env endpoint/key in tests."""
    monkeypatch.setenv("AI_BASE_URL", "https://model.invalid/v1")
    monkeypatch.setenv("AI_API_KEY", SECRET)
    monkeypatch.setenv("VISION_MODEL", "fake-vl")
    monkeypatch.setattr(vision, "_json_mode", {})


class FakeModel:
    """Scripted responses. Each entry: dict (answer JSON), str (raw answer text),
    int (HTTP status), Exception, or callable(request) -> httpx.Response."""

    def __init__(self, *script, usage=(100, 20)):
        self.script = list(script)
        self.requests: list[httpx.Request] = []
        self.usage = usage

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        step = self.script.pop(0) if self.script else GOOD
        if isinstance(step, Exception):
            raise step
        if callable(step):
            return step(request)
        if isinstance(step, int):
            return httpx.Response(step, json={"error": {"message": f"status {step}"}})
        content = step if isinstance(step, str) else json.dumps(step, ensure_ascii=False)
        body = {"choices": [{"message": {"content": content}}]}
        if self.usage:
            body["usage"] = {"prompt_tokens": self.usage[0], "completion_tokens": self.usage[1]}
        return httpx.Response(200, json=body)

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self))

    @property
    def calls(self) -> int:
        return len(self.requests)


def add_image(color="red") -> int:
    r = importer.import_images([importer.Upload(f"{color}.png", png(color))])
    return r["added"][0]["id"]


def row(image_id):
    conn = db.connect()
    try:
        return dict(conn.execute("SELECT * FROM analyses WHERE image_id = ?", (image_id,)).fetchone())
    finally:
        conn.close()


def run(image_id, model: FakeModel):
    with model.client() as client:
        return analysis.process_image(image_id, client=client, sleep=lambda s: None)


@pytest.fixture
def lib(data_dir):
    db.init_db()
    return data_dir


# ---- validation ----

def test_validate_normalizes_unambiguous_things_only():
    out = vision.validate(GOOD)
    assert out["layout"]["columns"] == ["single"]
    assert out["layout"]["whitespace"] == ["much"]
    assert out["style"] == {"tags": ["极简", "瑞士风格"], "medium": "typography", "style_note": "无衬线大字"}
    assert "palette" not in out
    unknown = json.loads(json.dumps(GOOD))
    unknown["layout"].update(title_position=["unknown"], density=["unknown"])
    assert vision.validate(unknown)["layout"]["title_position"] == ["unknown"]
    fenced = "```json\n" + json.dumps(GOOD, ensure_ascii=False) + "\n```"
    assert vision.parse_answer(fenced)["description"] == "一张极简的展览海报"


@pytest.mark.parametrize("mutate, message", [
    (lambda d: d.pop("description"), "缺少字段"),
    (lambda d: d["layout"].pop("density"), "缺少字段"),
    (lambda d: d["layout"].update(columns=["three"]), "不合法"),
    (lambda d: d["style"].update(medium="photo, ui"), "不合法"),
    (lambda d: d["style"].update(medium=["photo"]), "字符串"),
    (lambda d: d.update(subject=[]), "为空"),
    (lambda d: d.update(subject=[1, 2]), "字符串数组"),
    (lambda d: d.update(layout="top"), "对象"),
    (lambda d: d.update(description=""), "为空"),
])
def test_validate_rejects(mutate, message):
    data = json.loads(json.dumps(GOOD))
    mutate(data)
    with pytest.raises(ValueError, match=message):
        vision.validate(data)


def test_parse_answer_rejects_non_json():
    with pytest.raises(ValueError, match="JSON"):
        vision.parse_answer("这是一张海报")


# ---- process_image ----

def test_valid_answer_is_stored_with_tokens(lib):
    image_id = add_image()
    model = FakeModel(GOOD)
    assert run(image_id, model)["status"] == "done"
    r = row(image_id)
    assert (r["status"], r["attempts"], r["prompt_tokens"], r["completion_tokens"]) == ("done", 1, 100, 20)
    assert r["model"] == "fake-vl" and r["error"] is None
    assert json.loads(r["subject"]) == ["展览", "海报"]
    assert json.loads(r["layout_json"])["title_position"] == ["top"]
    assert json.loads(r["style_json"])["medium"] == "typography"
    assert r["visible_text"] == "SUMMER\n夏日展"
    body = json.loads(model.requests[0].content)
    assert body["model"] == "fake-vl" and body["response_format"] == {"type": "json_object"}
    assert body["messages"][0]["content"][0]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert model.requests[0].url == "https://model.invalid/v1/chat/completions"


def test_invalid_then_valid_retries_once(lib):
    image_id = add_image()
    model = FakeModel("不是 JSON", GOOD)
    assert run(image_id, model)["status"] == "done"
    r = row(image_id)
    assert (r["attempts"], r["prompt_tokens"], r["completion_tokens"]) == (2, 200, 40)


def test_invalid_twice_fails_and_keeps_raw(lib):
    image_id = add_image()
    bad = json.loads(json.dumps(GOOD))
    bad["layout"]["columns"] = ["three"]
    model = FakeModel("不是 JSON", bad, GOOD)
    out = run(image_id, model)
    assert out["status"] == "failed" and model.calls == 2
    r = row(image_id)
    assert r["status"] == "failed" and r["error"].startswith("invalid:")
    assert "three" in r["raw_json"]
    assert (r["attempts"], r["prompt_tokens"]) == (2, 200)
    assert r["description"] is None


def test_response_format_fallback_is_not_an_invalid_answer(lib):
    def reject(request):
        return httpx.Response(400, json={"error": {"message": "response_format is not supported"}})
    image_id = add_image()
    # rejected request, then one invalid answer, then a valid one: still done, because
    # the compatibility fallback does not use up the single invalid-answer retry
    model = FakeModel(reject, "oops", GOOD)
    assert run(image_id, model)["status"] == "done"
    bodies = [json.loads(r.content) for r in model.requests]
    assert "response_format" in bodies[0]
    assert all("response_format" not in b for b in bodies[1:])
    r = row(image_id)
    assert r["attempts"] == 3                       # three real requests were sent
    assert r["prompt_tokens"] == 200                # the 400 reported no usage: not estimated
    # remembered for this model: the next image goes straight without it
    second = add_image("blue")
    model2 = FakeModel(GOOD)
    run(second, model2)
    assert "response_format" not in json.loads(model2.requests[0].content)


def test_network_errors_retry_then_fail_without_estimated_tokens(lib):
    image_id = add_image()
    model = FakeModel(httpx.ConnectTimeout("t"), 503, 429, GOOD)
    out = run(image_id, model)
    assert out["status"] == "failed" and model.calls == 3
    r = row(image_id)
    assert r["error"].startswith("network:")
    assert r["attempts"] == 3
    assert (r["prompt_tokens"], r["completion_tokens"]) == (None, None)


def test_rate_limit_respects_retry_after(lib):
    image_id = add_image()
    model = FakeModel(lambda _: httpx.Response(429, headers={"Retry-After": "7"}), GOOD)
    waits = []
    with model.client() as client:
        result = analysis.process_image(image_id, client=client, sleep=waits.append)
    assert result["status"] == "done" and model.calls == 2
    assert waits == [7]


def test_worker_processes_two_distinct_images_in_parallel(lib):
    image_ids = {add_image(color) for color in ("red", "green", "blue")}
    release = Event()
    model = FakeModel()

    def hold_request(request):
        release.wait(10)
        return model(request)

    worker = analysis.Worker(concurrency=2, client_factory=lambda: httpx.Client(
        transport=httpx.MockTransport(hold_request)))
    worker.start()
    try:
        deadline = time.monotonic() + 5
        while len(worker.state()["current_image_ids"]) < 2 and time.monotonic() < deadline:
            time.sleep(.01)
        current = set(worker.state()["current_image_ids"])
        assert len(current) == 2 and current <= image_ids
        worker.pause()
    finally:
        release.set()
        worker.shutdown()
    assert model.calls == 2
    assert {i for i in image_ids if row(i)["status"] == "done"} == current


def test_missing_usage_is_not_estimated(lib):
    image_id = add_image()
    run(image_id, FakeModel(GOOD, usage=None))
    r = row(image_id)
    assert r["status"] == "done" and r["prompt_tokens"] is None and r["attempts"] == 1


def test_auth_error_fails_and_key_never_leaks(lib, caplog):
    caplog.set_level(logging.DEBUG)
    image_id = add_image()
    model = FakeModel(401)
    with pytest.raises(vision.VisionError) as exc:
        run(image_id, model)
    assert exc.value.kind == "auth"
    assert model.requests[0].headers["authorization"] == f"Bearer {SECRET}"
    r = row(image_id)
    assert r["status"] == "failed"
    assert SECRET not in json.dumps(r, ensure_ascii=False)
    assert SECRET not in str(exc.value) and SECRET not in caplog.text


def test_unknown_model_stops_the_queue(lib):
    first, second = add_image("red"), add_image("blue")
    model = FakeModel(404, 404)
    worker = analysis.Worker(client_factory=model.client, sleep=lambda s: None)
    worker._running.set()  # as if started, without the thread
    with model.client() as client:
        assert worker.run_once(client) is False
    assert model.calls == 1                      # stopped after the first image
    assert worker.last_error.startswith("model:") and "fake-vl" in worker.last_error
    assert not worker.running
    assert row(second)["status"] == "failed" and row(first)["status"] == "pending"


def test_missing_config_leaves_pending(lib, monkeypatch):
    monkeypatch.setenv("AI_API_KEY", "")
    image_id = add_image()
    model = FakeModel(GOOD)
    with pytest.raises(vision.VisionError, match="未配置"):
        run(image_id, model)
    assert model.calls == 0 and row(image_id)["status"] == "pending"


# ---- idempotency ----

def test_done_is_never_reanalyzed(lib):
    image_id = add_image()
    run(image_id, FakeModel(GOOD))
    before = row(image_id)

    importer.import_images([importer.Upload("again.png", png("red"))])  # duplicate drop
    assert analysis.retry(image_id) == 0 and analysis.retry() == 0     # retry ignores done
    model = FakeModel(GOOD)
    assert run(image_id, model)["status"] == "done"
    assert model.calls == 0
    assert row(image_id) == before
    assert analysis.claim("someone") is None  # done rows are never claimable


def test_failed_can_be_retried_and_tokens_accumulate(lib):
    image_id = add_image()
    run(image_id, FakeModel(503, 503, 503))
    assert row(image_id)["status"] == "failed"
    assert analysis.retry(image_id) == 1
    run(image_id, FakeModel(GOOD))
    r = row(image_id)
    assert (r["status"], r["attempts"], r["prompt_tokens"], r["error"]) == ("done", 4, 100, None)


def test_record_only_writes_pending_rows(lib):
    image_id = add_image()
    run(image_id, FakeModel(GOOD))
    conn = db.connect()
    try:
        assert not analysis._record(conn, image_id, "tok", "failed", vision.Calls(count=1), "x", error="late")
    finally:
        conn.close()
    assert row(image_id)["status"] == "done"


# ---- colors ----

def image_file(tmp_path, img, name="c.png"):
    path = tmp_path / name
    img.save(path)
    return path


def test_colors_ratio_and_rank(tmp_path):
    img = Image.new("RGB", (40, 40), (255, 0, 0))
    img.paste((0, 0, 255), (0, 0, 40, 10))  # top quarter blue
    assert colors.extract(image_file(tmp_path, img)) == [("#ff0000", 0.75), ("#0000ff", 0.25)]


def test_colors_top5_of_many(tmp_path):
    img = Image.new("RGB", (70, 10))
    shades = [(255, 0, 0), (0, 160, 0), (0, 0, 255), (255, 255, 0), (0, 0, 0), (255, 255, 255), (128, 0, 128)]
    widths = [20, 15, 12, 10, 6, 4, 3]
    x = 0
    for c, w in zip(shades, widths):
        img.paste(c, (x, 0, x + w, 10))
        x += w
    out = colors.extract(image_file(tmp_path, img))
    assert len(out) == 5
    assert [h for h, _ in out] == ["#ff0000", "#00a000", "#0000ff", "#ffff00", "#000000"]
    assert [r for _, r in out] == sorted((r for _, r in out), reverse=True)


def test_colors_transparent_png(tmp_path):
    img = Image.new("RGBA", (20, 20), (0, 0, 0, 0))
    img.paste((0, 0, 255, 255), (0, 0, 10, 20))
    # transparent half is flattened onto white; equal shares, so order is not asserted
    assert sorted(colors.extract(image_file(tmp_path, img))) == [("#0000ff", 0.5), ("#ffffff", 0.5)]


def test_colors_written_once_even_if_ai_fails(lib):
    image_id = add_image("red")
    run(image_id, FakeModel(400))
    analysis.retry(image_id)
    run(image_id, FakeModel(GOOD))
    conn = db.connect()
    try:
        rows = [tuple(r) for r in conn.execute(
            "SELECT rank, hex, ratio FROM colors WHERE image_id = ?", (image_id,))]
    finally:
        conn.close()
    assert rows == [(0, "#ff0000", 1.0)]


def test_large_image_is_downscaled_for_the_model(tmp_path):
    path = image_file(tmp_path, Image.new("RGB", (3000, 1000), "white"), "big.png")
    import base64
    uri = vision.image_data_uri(path, 1280)
    with Image.open(io.BytesIO(base64.b64decode(uri.split(",", 1)[1]))) as im:
        assert im.size == (1280, 427)
    with Image.open(path) as im:
        assert im.size == (3000, 1000)  # original untouched


# ---- API + worker ----

def test_api_detail_status_and_worker(lib, backend_options):
    model = FakeModel(GOOD, 400)
    worker = analysis.Worker(client_factory=model.client, sleep=lambda s: None)
    with TestClient(create_app(worker), backend_options=backend_options) as c:
        a = add_image("red")
        b = add_image("blue")
        st = c.get("/api/analysis/status").json()
        assert (st["running"], st["pending"], st["total"], st["configured"]) == (False, 2, 2, True)
        assert SECRET not in json.dumps(st)
        time.sleep(0.2)
        assert model.calls == 0  # starts paused

        c.post("/api/analysis/start")
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            status = c.get("/api/analysis/status").json()
            if status["done"] + status["failed"] == status["total"]:
                break
            time.sleep(0.05)
        c.post("/api/analysis/pause")
        st = c.get("/api/analysis/status").json()
        assert (st["done"], st["failed"], st["prompt_tokens"], st["completion_tokens"]) == (1, 1, 100, 20)  # the 400 reported no usage

        # newest post first: b was analyzed first (done), a got the 400 (failed)
        detail = c.get(f"/api/images/{b}").json()
        assert detail["analysis"]["status"] == "done"
        assert detail["analysis"]["style"]["tags"] == ["极简", "瑞士风格"]
        assert detail["colors"] == [{"rank": 0, "hex": "#0000ff", "ratio": 1.0}]
        assert detail["posts"][0]["platform"] == "local"
        failed = c.get(f"/api/images/{a}").json()["analysis"]
        assert failed["status"] == "failed" and failed["error"].startswith("http:")

        assert c.post(f"/api/analysis/{b}/retry").json() == {"requeued": 0}
        assert c.post("/api/analysis/retry-failed").json() == {"requeued": 1}
        assert c.get("/api/images/99999").status_code == 404


def test_thinking_is_off_by_default_and_overridable(lib, monkeypatch):
    image_id = add_image()
    model = FakeModel(GOOD)
    run(image_id, model)
    assert json.loads(model.requests[0].content)["enable_thinking"] is False
    monkeypatch.setenv("VISION_ENABLE_THINKING", "true")
    second = add_image("blue")
    model2 = FakeModel(GOOD)
    run(second, model2)
    assert json.loads(model2.requests[0].content)["enable_thinking"] is True
