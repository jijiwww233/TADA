import random
import sqlite3

import pytest

from app import db, imports
from app.sync import safety

NOTE_A = "a" * 24
NOTE_B = "b" * 24


@pytest.fixture(autouse=True)
def fresh_last_results():
    imports._LAST.update(captured=None, share=None)
    yield


def captured(note_id, images=("https://img.example/1.jpg", "https://img.example/2.jpg")):
    return {"note_id": note_id, "title": "t", "description": "d",
            "images": [{"url": url} for url in images]}


def test_captured_import_keeps_order_and_dedupes_by_note_id(data_dir):
    db.init_db()
    first = imports.import_captured({"notes": [captured(NOTE_A), captured(NOTE_B, [])]})
    assert first["created"] == 2 and first["skipped"] == 0 and first["failed"] == []

    conn = sqlite3.connect(data_dir / "app.db")
    try:
        posts = conn.execute(
            "SELECT platform, source_id, url, fav_state, source_state FROM posts ORDER BY id").fetchall()
        assert [p[0] for p in posts] == ["xhs", "xhs"]
        assert [p[1] for p in posts] == [NOTE_A, NOTE_B]
        assert posts[0][2] == f"https://www.xiaohongshu.com/explore/{NOTE_A}"
        assert all(p[3] == "unverified" and p[4] == "ok" for p in posts)
        slots = conn.execute(
            "SELECT position, image_id, remote_url, download_state FROM post_images ORDER BY post_id, position").fetchall()
        assert slots == [(0, None, "https://img.example/1.jpg", "remote_only"),
                         (1, None, "https://img.example/2.jpg", "remote_only")]
        assert conn.execute("SELECT COUNT(*) FROM images").fetchone()[0] == 0
    finally:
        conn.close()

    again = imports.import_captured({"notes": [captured(NOTE_A), captured(NOTE_B)]})
    assert again["created"] == 0 and again["skipped"] == 2


def test_captured_rejects_empty_and_reports_bad_note_id(data_dir):
    db.init_db()
    with pytest.raises(imports.ImportRejected):
        imports.import_captured({"notes": []})
    result = imports.import_captured({"notes": [{"note_id": "not-a-note"}]})
    assert result["created"] == 0 and result["failed"]
    assert imports.import_captured({"notes": [captured(NOTE_A)]})["created"] == 1


def test_share_import_is_blocked_by_the_xhs_safety_lock(data_dir):
    db.init_db()
    safety.trip(manual=True)
    calls = []

    def fetcher(url):
        calls.append(url)
        return url, ""

    with pytest.raises(imports.SafetyBlocked):
        imports.ShareImporter(fetcher=fetcher).import_urls(
            [f"https://www.xiaohongshu.com/explore/{NOTE_A}"])
    assert calls == []


def test_share_import_dedupes_keeps_image_order_and_never_downloads(data_dir):
    db.init_db()
    html = (
        '<script>window.__INITIAL_STATE__={"note":{"noteDetailMap":{"' + NOTE_A + '":'
        '{"note":{"noteId":"' + NOTE_A + '","title":"标题","desc":"正文","imageList":['
        '{"urlDefault":"https://img.example/1.jpg","width":10,"height":20},'
        '{"urlPre":"https://img.example/2.jpg"},'
        '{"infoList":[{"url":"https://img.example/3.jpg"}]}],"optional":undefined}}}}}</script>'
    )
    sleeps = []
    importer = imports.ShareImporter(fetcher=lambda url: (url, html),
                                     sleep=sleeps.append, rng=random.Random(0))
    result = importer.import_urls([f"https://www.xiaohongshu.com/explore/{NOTE_A}"] * 2)
    assert result == {"mode": "share", "created": 1, "skipped": 1, "failed": [], "total": 2}
    assert len(sleeps) == 1 and 0.8 <= sleeps[0] <= 2.0

    conn = sqlite3.connect(data_dir / "app.db")
    try:
        assert conn.execute("SELECT title, description FROM posts").fetchone() == ("标题", "正文")
        slots = conn.execute(
            "SELECT position, remote_url, download_state FROM post_images ORDER BY position").fetchall()
        assert slots == [(0, "https://img.example/1.jpg", "remote_only"),
                         (1, "https://img.example/2.jpg", "remote_only"),
                         (2, "https://img.example/3.jpg", "remote_only")]
        assert conn.execute("SELECT COUNT(*) FROM images").fetchone()[0] == 0
    finally:
        conn.close()


def test_share_import_limits_batch_and_domains(data_dir):
    db.init_db()
    importer = imports.ShareImporter(fetcher=lambda url: (_ for _ in ()).throw(AssertionError("no network")))
    with pytest.raises(imports.ImportRejected):
        importer.import_urls([f"https://www.xiaohongshu.com/explore/{i:024x}" for i in range(21)])
    with pytest.raises(imports.ImportRejected):
        imports.note_id_from_url("https://evil.example/explore/" + NOTE_A)
    assert imports.note_id_from_url(f"https://www.xiaohongshu.com/explore/{NOTE_A}") == NOTE_A
    assert imports.note_id_from_url("https://xhslink.com/short") is None


def test_status_reports_cooldown_and_the_last_outcome(data_dir):
    db.init_db()
    assert imports.status()["blocked"] is False
    first = imports.import_captured({"notes": [captured(NOTE_A), captured(NOTE_B)]})
    last = imports.status()["last"]["captured"]
    assert last["created"] == first["created"] == 2
    assert last["total"] == 2 and last["at"]

    safety.trip(manual=True)
    blocked = imports.status()
    assert blocked["blocked"] is True and blocked["manual_hold"] is True
    assert blocked["message"]


def test_http_import_status_and_share_cooldown(data_dir, backend_options):
    from fastapi.testclient import TestClient
    from app.main import create_app

    db.init_db()
    safety.trip(manual=True)
    with TestClient(create_app(), backend_options=backend_options) as client:
        status = client.get("/api/import/status").json()
        assert status["blocked"] is True and status["last"]["share"] is None
        blocked = client.post("/api/import/share",
                              json={"urls": [f"https://www.xiaohongshu.com/explore/{NOTE_A}"]})
        assert blocked.status_code == 409
        assert blocked.json()["detail"]["code"] == "xhs_cooldown"
        # Passive capture stores local page data and never touches the network: it stays open
        # even with the lock on (the UI hides it while cooling, the extension posts directly).
        ok = client.post("/api/import/captured", json={"notes": [captured(NOTE_A)]})
        assert ok.status_code == 200 and ok.json()["created"] == 1
