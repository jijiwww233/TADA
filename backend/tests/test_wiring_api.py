"""Wiring API tests. All storage and lock reads use temporary local data."""
from contextlib import closing
from datetime import date, timedelta
from hashlib import sha256
from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import db
from app.main import create_app
from app.reference_value import eligible_ids
from app.sync import safety


@pytest.fixture
def api(data_dir, backend_options, monkeypatch):
    monkeypatch.setenv("AI_API_KEY", "")
    monkeypatch.setenv("WOMINMIN_ALLOW_AUTO_SYNC", "0")
    # Exercise the actual router without starting lifespan background workers.
    client = TestClient(create_app(), backend_options=backend_options)
    yield client
    client.close()


def seed_images(entries):
    with closing(db.connect()) as conn, conn:
        for image_id, (platform, created_at) in enumerate(entries, 1):
            conn.execute("INSERT INTO posts (id, platform) VALUES (?, ?)", (image_id, platform))
            conn.execute(
                """INSERT INTO images (id, post_id, position, sha256, file_path, created_at)
                   VALUES (?, ?, 0, ?, ?, ?)""",
                (image_id, image_id, str(image_id), f"images/{image_id}.png", created_at),
            )


def test_thumbnails_for_imported_and_existing_images(api, data_dir):
    def png(color):
        output = BytesIO()
        Image.new("RGB", (1200, 800), color).save(output, "PNG")
        return output.getvalue()

    original = png("coral")
    imported = api.post("/api/images/import", files={"files": ("new.png", original, "image/png")})
    assert imported.status_code == 200
    original_url = imported.json()["added"][0]["url"]
    filename = original_url.rsplit("/", 1)[-1]
    cache = data_dir / "thumbnails" / "v1"
    assert (cache / (sha256(original).hexdigest() + ".webp")).is_file()
    assert api.get(original_url).content == original
    assert "immutable" in api.get(original_url).headers["cache-control"]
    assert api.get("/images/thumb-v1/" + filename).headers["content-type"] == "image/webp"

    existing = png("blue")
    digest = sha256(existing).hexdigest()
    path = data_dir / "images" / (digest + ".png")
    path.write_bytes(existing)
    thumb_url = "/images/thumb-v1/" + path.name
    thumb = api.get(thumb_url)
    assert thumb.status_code == 200 and "immutable" in thumb.headers["cache-control"]
    assert len(thumb.content) <= 100 * 1024
    with Image.open(BytesIO(thumb.content)) as preview:
        assert preview.format == "WEBP" and preview.size == (600, 400)
    saved = cache / (digest + ".webp")
    mtime = saved.stat().st_mtime_ns
    assert api.get(thumb_url).content == thumb.content and saved.stat().st_mtime_ns == mtime
    path.unlink()
    assert api.get(thumb_url).status_code == 404


def test_calendar_returns_empty_lists_for_an_empty_library(api):
    for endpoint in ("/api/stats/daily", "/api/stats/platforms"):
        response = api.get(endpoint)
        assert response.status_code == 200
        assert response.json() == []


def test_calendar_aggregates_real_images_and_keeps_first_source(api):
    with closing(db.connect()) as conn:
        today = date.fromisoformat(conn.execute("SELECT date('now')").fetchone()[0])
    yesterday = today - timedelta(days=1)
    older = today - timedelta(days=5)
    seed_images([("local", yesterday.isoformat()), ("local", yesterday.isoformat()),
                 ("xhs", today.isoformat()), ("xhs", older.isoformat())])
    with closing(db.connect()) as conn, conn:
        conn.execute("INSERT INTO post_images (post_id, position, image_id) VALUES (3, 1, 1)")

    assert api.get("/api/stats/daily?days=1").json() == [
        {"date": yesterday.isoformat(), "count": 2},
        {"date": today.isoformat(), "count": 1},
    ]
    assert api.get("/api/stats/daily").json()[0] == {"date": older.isoformat(), "count": 1}
    assert api.get("/api/stats/platforms").json() == [
        {"platform": "local", "count": 2},
        {"platform": "xhs", "count": 2},
    ]


def test_key_status_never_returns_full_configuration(api, monkeypatch):
    endpoint = "/api/settings/api-key"
    assert api.get(endpoint).json() == {"configured": False, "last4": None}
    monkeypatch.setenv("AI_API_KEY", "synthetic-for-test-A7B9")
    assert api.get(endpoint).json() == {"configured": True, "last4": "A7B9"}
    monkeypatch.setenv("AI_API_KEY", "test")
    assert api.get(endpoint).json() == {"configured": True, "last4": None}


@pytest.mark.parametrize("lock_kind", ["manual", "corrupt", "unreadable"])
def test_cooldown_rereads_the_lock_and_fails_closed(api, lock_kind):
    endpoint = "/api/sync/xhs/cooldown"
    assert api.get(endpoint).json()["blocked"] is False
    path = safety.path()
    path.parent.mkdir(parents=True)
    if lock_kind == "unreadable":
        path.mkdir()
    elif lock_kind == "corrupt":
        path.write_text("not JSON", encoding="utf-8")
    else:
        path.write_text('{"manual_hold": true, "until": 0}', encoding="utf-8")
    assert api.get(endpoint).json() == {
        "blocked": True, "auto_blocked": True, "manual_hold": True, "message": safety.MESSAGE,
    }
    assert api.get("/api/sync/xhs/status").json()["connection_state"] == "blocked"


def test_library_palettes_are_opt_in_ranked_and_empty_when_missing(api):
    seed_images([("local", "2026-11-07"), ("local", "2026-11-08")])
    with closing(db.connect()) as conn, conn:
        conn.executemany("INSERT INTO colors (image_id, rank, hex, ratio) VALUES (?, ?, ?, ?)",
                         [(1, 1, "#001122", 0.3), (1, 0, "#332211", 0.5)])

    assert all("colors" not in row for row in api.get("/api/images").json())
    by_id = {row["id"]: row["colors"] for row in api.get("/api/images?include_palette=true").json()}
    assert by_id == {
        1: [{"rank": 0, "hex": "#332211", "ratio": 0.5},
            {"rank": 1, "hex": "#001122", "ratio": 0.3}],
        2: [],
    }


def test_uncertain_queue_and_manual_decisions_override_the_model(api):
    seed_images([("local", "2026-11-07")] * 3)
    with closing(db.connect()) as conn, conn:
        conn.executemany("INSERT INTO image_reference_labels (image_id, auto_category) VALUES (?, ?)",
                         [(1, "uncertain"), (2, "uncertain"), (3, "text_card")])

    endpoint = "/api/reference/review"
    queue = endpoint + "?uncertain_only=true"
    assert {row["id"] for row in api.get(queue).json()["items"]} == {1, 2}

    for image_id, keep in [(1, True), (2, False)]:
        response = api.post("/api/reference/decisions", json={"ids": [image_id], "is_reference": keep})
        assert response.status_code == 200
        assert response.json()["updated"] == 1

    with closing(db.connect()) as conn, conn:
        conn.execute("UPDATE image_reference_labels SET auto_category = 'life' WHERE image_id = 1")
        conn.execute("UPDATE image_reference_labels SET auto_category = 'visual_ref' WHERE image_id = 2")
        assert 1 in eligible_ids(conn)
        assert 2 not in eligible_ids(conn)
    by_id = {row["id"]: row for row in api.get(endpoint).json()["items"]}
    assert (by_id[1]["category"], by_id[1]["manual_reference"]) == ("visual_ref", 1)
    assert by_id[2]["manual_reference"] == 0
    assert api.get(queue).json()["items"] == []
    assert by_id[3]["needs_confirmation"] is True

def test_library_reveal_counts_only_effective_visual_references(api):
    assert api.get("/api/stats/library").json() == {"collections": 0, "references": 0}
    seed_images([("local", "2026-11-07")] * 4)
    with closing(db.connect()) as conn, conn:
        conn.executemany("INSERT INTO image_reference_labels (image_id, auto_category) VALUES (?, ?)",
                         [(1, "visual_ref"), (2, "visual_ref"), (3, "uncertain")])
    assert api.post("/api/reference/decisions", json={"ids": [2], "is_reference": False}).status_code == 200
    assert api.post("/api/reference/decisions", json={"ids": [3], "is_reference": True}).status_code == 200
    assert api.get("/api/stats/library").json() == {"collections": 4, "references": 2}


@pytest.mark.parametrize("provider_status", [200, 401, 503])
def test_key_verification_and_local_save(api, tmp_path, monkeypatch, provider_status):
    import httpx
    from dotenv import dotenv_values
    from app import ai_key_api, config

    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(config, "AI_KEY_FROM_ENV", False)
    monkeypatch.setenv("AI_BASE_URL", "https://workspace.cn-beijing.maas.aliyuncs.com/compatible-mode/v1")
    monkeypatch.delenv("WOMINMIN_DEMO", raising=False)
    config_file = tmp_path / ".env"
    config_file.write_text("VISION_MODEL=example\n", encoding="utf-8")
    submitted = "synthetic-only-for-unit-test-A7B9"
    calls = []

    def provider(request):
        calls.append(request)
        assert request.method == "GET"
        assert request.url.path == "/api/v1/models"
        assert request.headers["Authorization"] == f"Bearer {submitted}"
        return httpx.Response(provider_status, json={"output": {"models": []}, "upstream": submitted})

    original_client = httpx.Client
    monkeypatch.setattr(ai_key_api.httpx, "Client",
                        lambda **kwargs: original_client(transport=httpx.MockTransport(provider), **kwargs))
    endpoint = "/api/settings/api-key"
    result = api.put(endpoint, json={"key": submitted})
    assert len(calls) == 1
    assert submitted not in result.text
    assert result.status_code == {200: 200, 401: 422, 503: 503}[provider_status]
    saved = dotenv_values(config_file)
    assert saved["VISION_MODEL"] == "example"
    if provider_status == 200:
        assert saved["AI_API_KEY"] == submitted
        assert result.json() == {"configured": True, "last4": "A7B9", "restart_required": True}
        assert api.get(endpoint).json() == result.json()
        assert not config.ai_settings()["api_key"]
    else:
        assert "AI_API_KEY" not in saved
        assert api.get(endpoint).json()["configured"] is False


def test_key_validation_errors_never_echo_the_submitted_value(api, monkeypatch):
    from app import ai_key_api, config

    monkeypatch.setattr(config, "AI_KEY_FROM_ENV", False)
    monkeypatch.delenv("WOMINMIN_DEMO", raising=False)
    monkeypatch.setattr(ai_key_api, "_verify_key", lambda key: pytest.fail("unexpected verification"))
    endpoint = "/api/settings/api-key"
    marker = "synthetic secret with spaces"
    invalid = api.put(endpoint, json={"key": marker})
    assert invalid.status_code == 422
    assert marker not in invalid.text
    monkeypatch.setattr(config, "AI_KEY_FROM_ENV", True)
    assert api.put(endpoint, json={"key": "synthetic-test-only"}).status_code == 409
