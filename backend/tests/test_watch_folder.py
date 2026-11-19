"""Watched local folders: import, cap, pause, source read-only. No paid calls."""
import os
import subprocess
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import analysis, config, db, importer, vision, watch_folders
from app.main import create_app
from conftest import png


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    library = tmp_path / "library"
    monkeypatch.setenv("WOMINMIN_DATA_DIR", str(library))
    return library


@pytest.fixture(autouse=True)
def offline_analysis(monkeypatch):
    monkeypatch.setenv("AI_API_KEY", "")


def write_image(path: Path, color=(10, 20, 30), mtime=None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(png(color))
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def image_rows():
    conn = db.connect()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT i.*, p.platform FROM images i JOIN posts p ON p.id=i.post_id ORDER BY i.id")]
    finally:
        conn.close()


def analysis_count():
    conn = db.connect()
    try:
        return conn.execute("SELECT COUNT(*) FROM analyses").fetchone()[0]
    finally:
        conn.close()


def deferred_count():
    conn = db.connect()
    try:
        return conn.execute("SELECT COUNT(*) FROM watch_deferred").fetchone()[0]
    finally:
        conn.close()


def run_until_imported(service, start=0.0):
    stats = service.scan_once(now=start)
    assert stats["imported"] == 0
    return service.scan_once(now=start + watch_folders.STABLE_SECONDS + 0.2)


def test_new_image_imported_with_mtime_and_analysis_row(data_dir):
    db.init_db()
    folder = data_dir.parent / "watch"
    folder.mkdir()
    service = watch_folders.WatchFolders()
    added = service.add_folder(str(folder))
    assert added["id"] > 0 and added["enabled"] is True and added["imported_count"] == 0
    path = write_image(folder / "new.png", mtime=1_700_000_000)
    stats = run_until_imported(service)
    assert stats["imported"] == 1
    rows = image_rows()
    assert len(rows) == 1 and rows[0]["platform"] == "local"
    assert rows[0]["original_name"] == "new.png"
    expected = __import__("datetime").datetime.fromtimestamp(1_700_000_000).astimezone().isoformat(timespec="seconds")
    assert rows[0]["created_at"] == expected
    assert deferred_count() == 1
    folder_row = service.get_folder(added["id"])
    assert folder_row["imported_count"] == 1 and folder_row["last_import_at"]
    conn = db.connect()
    try:
        watched = conn.execute("SELECT * FROM watch_files").fetchall()
    finally:
        conn.close()
    assert len(watched) == 1 and watched[0]["image_id"] == rows[0]["id"]


def test_duplicate_content_is_skipped(data_dir):
    db.init_db()
    first = importer.import_images([importer.Upload("already.png", png((1, 2, 3)), 1_700_000_000)])
    image_id = first["added"][0]["id"]
    folder = data_dir.parent / "watch"
    folder.mkdir()
    service = watch_folders.WatchFolders()
    record = service.add_folder(str(folder))
    write_image(folder / "copy.png", color=(1, 2, 3), mtime=1_700_000_001)
    stats = run_until_imported(service)
    assert stats["duplicates"] == 1 and stats["imported"] == 0
    assert len(image_rows()) == 1 and analysis_count() == 1
    folder_row = service.get_folder(record["id"])
    assert folder_row["imported_count"] == 0


def test_half_written_file_is_not_read_early(data_dir, monkeypatch):
    db.init_db()
    folder = data_dir.parent / "watch"
    folder.mkdir()
    service = watch_folders.WatchFolders()
    service.add_folder(str(folder))
    data = png((5, 6, 7))
    path = folder / "big.png"
    path.write_bytes(data[:10])
    opened = []
    real_open = Path.open

    def track_open(self, mode="r", *args, **kwargs):
        if self == path and mode == "rb":
            opened.append(self)
        return real_open(self, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", track_open)
    assert service.scan_once(now=0.0)["imported"] == 0
    path.write_bytes(data)
    assert service.scan_once(now=1.0)["imported"] == 0
    assert service.scan_once(now=2.5)["imported"] == 0
    assert opened == []
    assert service.scan_once(now=3.2)["imported"] == 1
    assert opened == [path]
    assert len(image_rows()) == 1


def test_paused_folder_does_not_import_until_resumed(data_dir):
    db.init_db()
    folder = data_dir.parent / "watch"
    folder.mkdir()
    service = watch_folders.WatchFolders()
    record = service.add_folder(str(folder))
    service.update_folder(record["id"], enabled=False)
    write_image(folder / "later.png")
    assert service.scan_once(now=0.0)["imported"] == 0
    assert service.scan_once(now=2.2)["imported"] == 0
    assert len(image_rows()) == 0
    service.update_folder(record["id"], enabled=True)
    assert run_until_imported(service, start=10.0)["imported"] == 1


def test_daily_cap_still_imports_and_defers_analysis(data_dir, monkeypatch):
    db.init_db()
    watch_folders.set_daily_limit(1)
    folder = data_dir.parent / "watch"
    folder.mkdir()
    service = watch_folders.WatchFolders()
    record = service.add_folder(str(folder))
    write_image(folder / "a.png", color=(1, 1, 1))
    write_image(folder / "b.png", color=(2, 2, 2))
    stats = run_until_imported(service)
    assert stats["imported"] == 2
    assert len(image_rows()) == 2
    assert analysis_count() == 0 and deferred_count() == 2
    monkeypatch.setenv("WOMINMIN_ALLOW_AUTO_SYNC", "1")
    assert watch_folders.status()["auto_analysis_enabled"] is False
    assert analysis.claim("paused", prepare=service.prepare_analysis) is None
    watch_folders.set_auto_analysis(True)
    model_paths = []

    def fake_analyze(path, settings, *, calls: vision.Calls, **kwargs):
        model_paths.append(path)
        calls.prompt_tokens = 100
        calls.completion_tokens = 20
        return {"subject": [], "layout": {}, "style": {}, "description": "离线测试"}

    monkeypatch.setattr(vision, "analyze", fake_analyze)
    worker = analysis.Worker(prepare_claim=service.prepare_analysis)
    worker._running.set()
    assert worker.run_once(None)
    assert not worker.run_once(None)
    assert len(model_paths) == 1 and analysis_count() == 1
    status = watch_folders.status()
    assert status["analyzed_today"] == 1 and status["estimated_cost_today"] > 0
    assert deferred_count() == 1
    folder_row = service.get_folder(record["id"])
    assert folder_row["imported_count"] == 2
    watch_folders.set_auto_analysis(False)
    service.analyze_now()
    assert worker.run_once(None) and len(model_paths) == 2
    assert analysis_count() == 2 and deferred_count() == 0
    conn = db.connect()
    try:
        auto = conn.execute("SELECT COUNT(*) FROM watch_analysis_log WHERE auto=1").fetchone()[0]
        manual = conn.execute("SELECT COUNT(*) FROM watch_analysis_log WHERE auto=0").fetchone()[0]
    finally:
        conn.close()
    assert auto == 1 and manual == 1


def test_deferred_images_are_processed_next_day(data_dir, monkeypatch):
    db.init_db()
    watch_folders.set_daily_limit(1)
    folder = data_dir.parent / "watch"
    folder.mkdir()
    service = watch_folders.WatchFolders()
    service.add_folder(str(folder))
    write_image(folder / "a.png", color=(1, 1, 1))
    write_image(folder / "b.png", color=(2, 2, 2))
    run_until_imported(service)
    monkeypatch.setenv("WOMINMIN_ALLOW_AUTO_SYNC", "1")
    watch_folders.set_auto_analysis(True)
    assert analysis.claim("test", prepare=service.prepare_analysis) is not None
    assert analysis.claim("test", prepare=service.prepare_analysis) is None
    assert analysis_count() == 1 and deferred_count() == 1
    monkeypatch.setattr(watch_folders, "_today", lambda: "2099-01-02")
    assert analysis.claim("test", prepare=service.prepare_analysis) is not None
    assert analysis_count() == 2 and deferred_count() == 0


def test_source_folder_is_never_modified(data_dir):
    db.init_db()
    folder = data_dir.parent / "watch"
    folder.mkdir()
    service = watch_folders.WatchFolders()
    service.add_folder(str(folder))
    path = write_image(folder / "photo.png", mtime=1_700_000_000)
    before = (path.read_bytes(), path.stat().st_size, path.stat().st_mtime_ns)
    run_until_imported(service)
    after = (path.read_bytes(), path.stat().st_size, path.stat().st_mtime_ns)
    assert before == after
    assert [p.name for p in folder.iterdir()] == ["photo.png"]


def test_heic_file_is_converted_to_jpeg(data_dir):
    db.init_db()
    folder = data_dir.parent / "watch"
    folder.mkdir()
    service = watch_folders.WatchFolders()
    service.add_folder(str(folder))
    source = Path(__file__).parent / "fixtures" / "sample.heic"
    target = folder / "phone.heic"
    target.write_bytes(source.read_bytes())
    stats = run_until_imported(service)
    assert stats["imported"] == 1
    row = image_rows()[0]
    assert row["mime"] == "image/jpeg" and row["file_path"].endswith(".jpg")
    assert row["original_name"] == "phone.heic"
    stored = (config.data_dir() / row["file_path"]).read_bytes()
    assert stored[:3] == b"\xff\xd8\xff"


def test_non_images_temp_and_hidden_files_are_ignored(data_dir):
    db.init_db()
    folder = data_dir.parent / "watch"
    folder.mkdir()
    service = watch_folders.WatchFolders()
    service.add_folder(str(folder))
    (folder / "notes.txt").write_text("hi", encoding="utf-8")
    (folder / "photo.jpg.part").write_bytes(png((1, 2, 3)))
    (folder / ".hidden.png").write_bytes(png((4, 5, 6)))
    (folder / "sync.tmp.png").write_bytes(png((4, 5, 6)))
    write_image(folder / ".hidden-subfolder" / "other.png")
    stats = run_until_imported(service)
    assert stats["imported"] == 0 and stats["failed"] == 0
    assert len(image_rows()) == 0


def test_subfolders_enabled_by_default_and_can_be_disabled(data_dir):
    db.init_db()
    service = watch_folders.WatchFolders()
    folder = data_dir.parent / "inbox"
    folder.mkdir()
    added = service.add_folder(str(folder))
    assert added["include_subfolders"] is True
    write_image(folder / "2026-11-07" / "album" / "same.png", color=(1, 2, 3))
    write_image(folder / "2026-11-08" / "same.png", color=(4, 5, 6))
    assert run_until_imported(service)["imported"] == 2

    service.update_folder(added["id"], include_subfolders=False)
    write_image(folder / "2026-11-08" / "new.png", color=(7, 8, 9))
    write_image(folder / "root.png", color=(10, 11, 12))
    assert run_until_imported(service, start=10)["imported"] == 1
    service.update_folder(added["id"], include_subfolders=True)
    assert run_until_imported(service, start=20)["imported"] == 1
    assert len(image_rows()) == 4


def test_recursive_scan_skips_links_to_other_folders(data_dir):
    db.init_db()
    service = watch_folders.WatchFolders()
    folder = data_dir.parent / "inbox"
    folder.mkdir()
    outside = data_dir.parent / "outside"
    write_image(outside / "outside.png", color=(4, 5, 6))
    link = folder / "linked-album"
    if os.name == "nt":
        subprocess.run(["cmd.exe", "/c", "mklink", "/J", str(link), str(outside)],
                       check=True, capture_output=True)
    else:
        link.symlink_to(outside, target_is_directory=True)
    service.add_folder(str(folder))
    write_image(folder / "album" / "inside.png")
    assert run_until_imported(service)["imported"] == 1
    assert [row["original_name"] for row in image_rows()] == ["inside.png"]


def test_api_flow_and_daily_limit(data_dir, backend_options, monkeypatch):
    monkeypatch.delenv("WOMINMIN_ALLOW_AUTO_SYNC", raising=False)
    db.init_db()
    folder = data_dir.parent / "api-watch"
    folder.mkdir()
    client = TestClient(create_app(), backend_options=backend_options)
    status = client.get("/api/watch-folders").json()
    assert status["allowed"] is False and status["watching"] is False
    added = client.post("/api/watch-folders", json={"path": str(folder)})
    assert added.status_code == 200 and added.json()["path"]
    assert added.json()["include_subfolders"] is True
    folder_id = added.json()["id"]
    assert client.post("/api/watch-folders", json={"path": str(folder)}).status_code == 409
    assert client.post("/api/watch-folders", json={"path": "relative/path"}).status_code == 422
    write_image(folder / "one.png")
    assert client.post("/api/watch-folders/scan-now").json()["imported"] == 1
    assert client.put("/api/watch-folders/settings", json={"daily_limit": 1}).json() == {"daily_limit": 1}
    assert client.put("/api/watch-folders/settings", json={"daily_limit": 10001}).status_code == 422
    write_image(folder / "two.png", color=(9, 9, 9))
    assert client.post("/api/watch-folders/scan-now").json()["imported"] == 1
    assert client.get("/api/watch-folders").json()["deferred"] == 2
    assert analysis.claim("test", prepare=watch_folders.WatchFolders().prepare_analysis) is None
    now = client.post("/api/watch-folders/analyze-now").json()
    assert now["queued"] == 2 and now["deferred"] == 0 and now["started"] is False
    assert client.put("/api/watch-folders/" + str(folder_id), json={"enabled": False}).json()["enabled"] is False
    updated = client.put("/api/watch-folders/" + str(folder_id), json={"include_subfolders": False})
    assert updated.json()["include_subfolders"] is False and updated.json()["enabled"] is False
    assert client.put("/api/watch-folders/99999", json={"enabled": True}).status_code == 404
    assert client.delete("/api/watch-folders/" + str(folder_id)).json() == {"removed": True}
    assert client.get("/api/watch-folders").json()["folders"] == []


def test_background_scan_imports_only_when_enabled(data_dir, monkeypatch):
    db.init_db()
    service = watch_folders.WatchFolders(interval=0.02, stable_seconds=0.04)
    folder = data_dir.parent / "inbox"
    folder.mkdir()
    service.add_folder(str(folder))
    monkeypatch.delenv("WOMINMIN_ALLOW_AUTO_SYNC", raising=False)
    service.start()
    assert not service.watching
    monkeypatch.setenv("WOMINMIN_ALLOW_AUTO_SYNC", "1")
    service.start()
    try:
        write_image(folder / "synced.png")
        deadline = time.monotonic() + 4
        while not image_rows() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert len(image_rows()) == 1
        assert deferred_count() == 1
    finally:
        service.stop()


def test_only_entry_instance_starts_automatic_watching(data_dir, backend_options, monkeypatch):
    db.init_db()
    monkeypatch.delenv("WOMINMIN_ALLOW_AUTO_SYNC", raising=False)
    with TestClient(create_app(), backend_options=backend_options) as client:
        body = client.get("/api/watch-folders").json()
        assert body["allowed"] is False and body["watching"] is False
    monkeypatch.setenv("WOMINMIN_ALLOW_AUTO_SYNC", "1")
    with TestClient(create_app(), backend_options=backend_options) as client:
        body = client.get("/api/watch-folders").json()
        assert body["allowed"] is True and body["watching"] is True


def test_auto_analysis_requires_explicit_enable_on_formal_instance(data_dir, backend_options, monkeypatch):
    monkeypatch.setenv("WOMINMIN_ALLOW_AUTO_SYNC", "0")
    monkeypatch.setenv("AI_BASE_URL", "http://127.0.0.1:1")
    monkeypatch.setenv("VISION_MODEL", "offline-test")
    starts = []
    monkeypatch.setattr(analysis.Worker, "start", lambda worker: starts.append(worker))
    client = TestClient(create_app(), backend_options=backend_options)
    url = "/api/watch-folders/auto-analysis"
    assert client.put(url, json={"enabled": True}).status_code == 403
    monkeypatch.setenv("WOMINMIN_ALLOW_AUTO_SYNC", "1")
    assert client.put(url, json={"enabled": True}).status_code == 409
    monkeypatch.setenv("AI_API_KEY", "fake-test-key")
    assert client.get("/api/watch-folders").json()["auto_analysis_enabled"] is False
    assert not starts
    assert client.put(url, json={"enabled": True}).json()["auto_analysis_enabled"] is True
    assert watch_folders.auto_analysis_enabled() and len(starts) == 1
    assert client.put(url, json={"enabled": False}).json()["auto_analysis_enabled"] is False
    assert not watch_folders.auto_analysis_enabled()


def test_existing_v12_folders_receive_recursive_default(data_dir):
    db.init_db()
    folder = data_dir.parent / "inbox"
    folder.mkdir()
    service = watch_folders.WatchFolders()
    added = service.add_folder(str(folder))
    conn = db.connect()
    try:
        conn.execute("ALTER TABLE watch_folders DROP COLUMN include_subfolders")
        conn.execute("PRAGMA user_version = 12")
    finally:
        conn.close()
    db.init_db()
    assert service.get_folder(added["id"])["include_subfolders"] is True


def test_v12_migration_adds_tables_and_backs_up(data_dir):
    db.init_db()
    conn = db.connect()
    try:
        conn.execute("INSERT INTO posts(platform) VALUES(?)", ("local",))
        for table in ("watch_analysis_log", "watch_deferred", "watch_files",
                      "watch_folders", "watch_settings"):
            conn.execute("DROP TABLE " + table)
        conn.execute("PRAGMA user_version = 11")
    finally:
        conn.close()
    db.init_db()
    conn = db.connect()
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()
    assert {"watch_folders", "watch_files", "watch_deferred",
            "watch_analysis_log", "watch_settings"} <= names
    assert list(config.data_dir().glob("app.db.bak-*-v11"))
