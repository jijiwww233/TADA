"""Pinterest adapter wiring: external reader page exposure and runner state (no network)."""
import time

from app.sync import pinterest_service as ps


def page(collection, index, items, has_more=False):
    return {"contract_version": "sync-reader/1", "platform": "pinterest",
            "collection": {"collection_key": collection},
            "page": {"index": index, "cursor_in": None, "cursor_out": None,
                     "has_more": has_more},
            "items": items, "failures": []}


def test_external_reader_exposes_pages_and_terminal_page(monkeypatch, tmp_path):
    reader = ps.PinterestExternalReader("bootstrap", tmp_path)
    reader.pages = {"fav": [page("fav", 1, [{"work_key": "1"}], True),
                            page("fav", 2, [{"work_key": "2"}], False)]}
    got = reader.fetch_page("fav", None, 1)
    assert got["page"]["index"] == 1 and got["page"]["has_more"] is True
    got = reader.fetch_page("fav", "c", 2)
    assert got["page"]["index"] == 2 and got["page"]["has_more"] is False
    got = reader.fetch_page("fav", "c", 3)
    assert got["items"] == [] and got["page"]["has_more"] is False


def test_runner_mutex_and_state_without_auth(monkeypatch):
    called = []
    def fake_sync():
        called.append(1)
        time.sleep(0.05)
        return {"error": None}
    runner = ps.PinterestSyncRunner(sync=fake_sync, login_fn=lambda: {"message": "x"})
    assert runner.start_sync() is True
    assert runner.start_sync() is False  # one action at a time
    time.sleep(0.2)
    assert called == [1]


def test_login_missing_node_is_reported(monkeypatch, tmp_path):
    monkeypatch.setattr(ps, "_node", lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError()))
    result = ps.login(1)
    assert result["ok"] is False and "Node" in result["message"]
