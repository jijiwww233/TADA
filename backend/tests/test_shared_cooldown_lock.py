"""Shared cooldown lock: one location, fail-closed reads, no in-process cache.

These tests cover the follow-up requirements around the persistent XHS lock:
- the lock lives in the consolidated data folder (WOMINMIN_SAFETY_DIR / <data>/safety)
  and the pre-consolidation LOCALAPPDATA file is still read as a fallback;
- an unreadable or corrupt lock is treated as locked;
- every network call re-reads the file, so a trip written by another process/instance
  stops the next request without a restart;
- only the entry-launched formal instance may auto-sync; an unreadable lock at startup
  is fail-closed (no watcher starts, manual platform actions are refused).
"""
import json
import os
import subprocess
import sys
import time

import pytest
from fastapi.testclient import TestClient

from app import db, imports
from app import settings as ui_settings
from app.main import create_app
from app.sync import safety

NOTE = "a" * 24


def write_lock(until=0, manual=False, auto=False, path=None):
    target = path or safety.path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({"until": until, "manual_hold": manual, "auto_paused": auto}),
                      encoding="utf8")


class FakeRunner:
    def __init__(self, counters):
        self.counters = counters

    busy = False

    def start_sync(self):
        self.counters["start"] += 1
        return True

    def start_login(self):
        self.counters["login"] += 1
        return True

    def state(self):
        return {"busy": False, "last_run": None}


def test_lock_defaults_to_the_shared_data_safety_folder(tmp_path, monkeypatch):
    monkeypatch.delenv("WOMINMIN_SAFETY_DIR", raising=False)
    monkeypatch.setenv("WOMINMIN_DATA_DIR", str(tmp_path / "data"))
    assert safety.path() == tmp_path / "data" / "safety" / "xhs.json"


def test_legacy_localappdata_lock_is_read_until_the_new_file_exists(tmp_path, monkeypatch):
    monkeypatch.delenv("WOMINMIN_SAFETY_DIR", raising=False)
    monkeypatch.setenv("WOMINMIN_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    legacy = tmp_path / "local" / "Wominmin" / "safety" / "xhs.json"
    write_lock(until=9_999_999_999, path=legacy)
    assert safety.state()["blocked"] is True

    safety.trip("迁移后测试")
    assert safety.path().exists()
    # The write goes to the new location; the old file is left untouched.
    assert json.loads(legacy.read_text(encoding="utf8"))["until"] == 9_999_999_999


def test_unreadable_lock_path_is_treated_as_locked():
    safety.path().mkdir(parents=True, exist_ok=True)  # a directory is not readable as a file
    state = safety.state()
    assert state["blocked"] and state["auto_blocked"] and state["manual_hold"]
    with pytest.raises(safety.CoolingDown):
        safety.require()


@pytest.mark.parametrize("text", [
    "bad",
    "[]",
    '{"junk": 1}',
    '{"until": "soon", "manual_hold": false, "auto_paused": false}',
    '{"until": 0, "manual_hold": "no", "auto_paused": false}',
])
def test_corrupt_lock_shapes_fail_closed(text):
    safety.path().parent.mkdir(parents=True, exist_ok=True)
    safety.path().write_text(text, encoding="utf8")
    assert safety.state()["blocked"] is True


def test_each_network_call_rereads_the_lock_written_by_another_process(tmp_path):
    calls = []

    @safety.network
    def request():
        calls.append(1)
        return "ok"

    assert request() == "ok"
    # Another instance/process trips the lock; no restart or cache invalidation here.
    code = (
        "import json, os\n"
        "from pathlib import Path\n"
        "d = Path(os.environ['WOMINMIN_SAFETY_DIR'])\n"
        "d.mkdir(parents=True, exist_ok=True)\n"
        "(d / 'xhs.json').write_text(json.dumps({'until': 9999999999,"
        " 'manual_hold': False, 'auto_paused': False}), encoding='utf8')\n"
    )
    subprocess.run([sys.executable, "-c", code], env=dict(os.environ), check=True)
    with pytest.raises(safety.CoolingDown):
        request()
    assert calls == [1]


def test_open_backend_sees_lock_tripped_by_another_instance(data_dir, backend_options):
    counters = {"start": 0, "login": 0}
    with TestClient(create_app(syncer=FakeRunner(counters)), backend_options=backend_options) as client:
        assert client.post("/api/sync/xhs/start").status_code == 200
        # The other instance hits a risk page in the share import and writes the lock.
        with pytest.raises(imports.SafetyBlocked):
            imports.ShareImporter(
                fetcher=lambda url: (url, "<html>请完成安全验证后继续</html>")
            ).import_urls([f"https://www.xiaohongshu.com/explore/{NOTE}"])
        assert client.post("/api/sync/xhs/start").status_code == 423
        assert client.post("/api/sync/xhs/login").status_code == 423
        assert client.post("/api/import/share", json={"urls": [f"https://www.xiaohongshu.com/explore/{NOTE}"]}).status_code == 409
    assert counters == {"start": 1, "login": 0}


def test_auto_sync_requires_the_entry_flag(data_dir, backend_options, monkeypatch):
    counters = {"start": 0, "login": 0}
    ui_settings.write(auto_sync=True)
    monkeypatch.delenv("WOMINMIN_ALLOW_AUTO_SYNC", raising=False)

    with TestClient(create_app(syncer=FakeRunner(counters)), backend_options=backend_options) as client:
        body = client.get("/api/settings").json()
        assert body["auto_sync"] is False and body["auto_sync_allowed"] is False
        assert body["xhs"]["enabled"] is False
        assert client.post("/api/settings/auto-sync?enabled=true").status_code == 403
        time.sleep(0.25)
    assert counters == {"start": 0, "login": 0}

    monkeypatch.setenv("WOMINMIN_ALLOW_AUTO_SYNC", "1")
    with TestClient(create_app(syncer=FakeRunner(counters)), backend_options=backend_options) as client:
        body = client.get("/api/settings").json()
        assert body["auto_sync"] is True and body["auto_sync_allowed"] is True
        assert body["xhs"]["enabled"] is True
        assert client.post("/api/settings/auto-sync?enabled=true").status_code == 200
        time.sleep(0.25)  # startup delay is 15s; only the switch is asserted here
    assert counters == {"start": 0, "login": 0}


def test_unreadable_lock_at_startup_issues_no_platform_requests(data_dir, backend_options, monkeypatch):
    counts = {"start": 0, "login": 0, "fetch": 0}
    ui_settings.write(auto_sync=True)
    monkeypatch.setenv("WOMINMIN_ALLOW_AUTO_SYNC", "1")
    safety.path().mkdir(parents=True, exist_ok=True)  # unreadable as a lock file

    from app import main as app_main
    for name in ("HuabanSyncRunner", "PinterestSyncRunner", "ZcoolSyncRunner"):
        monkeypatch.setattr(app_main, name, lambda counters=counts: FakeRunner(counters))

    def forbidden_fetch(*_args, **_kwargs):
        counts["fetch"] += 1
        raise AssertionError("no platform request may leave a fail-closed backend")

    monkeypatch.setattr(imports, "_http_fetch", forbidden_fetch)

    with TestClient(create_app(syncer=FakeRunner(counts)), backend_options=backend_options) as client:
        body = client.get("/api/settings").json()
        assert body["auto_sync"] is False
        assert body["auto_sync_allowed"] is True
        assert body["auto_sync_lock_blocked"] is True
        for watcher in ("xhs", "huaban", "pinterest", "zcool"):
            assert body[watcher]["enabled"] is False
        assert client.post("/api/sync/xhs/start").status_code == 423
        assert client.post("/api/sync/xhs/login").status_code == 423
        assert client.post("/api/import/share",
                           json={"urls": [f"https://www.xiaohongshu.com/explore/{NOTE}"]}).status_code == 409
        time.sleep(0.3)
    assert counts == {"start": 0, "login": 0, "fetch": 0}
