"""Account-scoped favorites/cursors (v7) and XHS source-token resolution."""
import json
import logging

from app import db
from app.sync import store
from app.sync.contract import Item, Page
from app.sync import xhs_source


def _page(work_key, index=1, has_more=False, cursor_out=None):
    return Page(platform="xhs", collection_key="fav", index=index, cursor_in=None,
                cursor_out=cursor_out, has_more=has_more,
                items=[Item(work_key=work_key, title="t", canonical_url="https://x/1")],
                failures=[])


def _run(account):
    run_id = store.start_run("xhs", "fav", "bootstrap", account)
    return run_id


def test_v7_migration_creates_account_tables(data_dir):
    db.init_db()
    conn = db.connect()
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"post_favorites", "sync_cursors_v2"} <= tables
        assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    finally:
        conn.close()


def test_two_accounts_share_post_and_images_but_have_own_favorite(data_dir):
    db.init_db()
    run_a = _run("acct-a")
    store.apply_page(run_a, _page("note-1"), "bootstrap", {"scan_start_run_id": run_a}, "acct-a")
    run_b = _run("acct-b")
    result = store.apply_page(run_b, _page("note-1"), "bootstrap",
                              {"scan_start_run_id": run_b}, "acct-b")
    conn = db.connect()
    try:
        posts = conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0]
        relations = conn.execute(
            "SELECT account_key FROM post_favorites WHERE post_id = 1 ORDER BY account_key"
        ).fetchall()
    finally:
        conn.close()
    assert posts == 1                     # same post, not duplicated
    assert [r[0] for r in relations] == ["acct-a", "acct-b"]
    assert result.items_new == 0          # relation added, post already existed


def test_cursors_and_runs_are_per_account(data_dir):
    db.init_db()
    for account, index in (("acct-a", 1), ("acct-b", 2)):
        run_id = _run(account)
        store.apply_page(run_id, _page("n", index=index, has_more=True, cursor_out=f"c{index}"),
                         "bootstrap", {"scan_start_run_id": run_id}, account)
        store.finish_run(run_id, "paused", pause_reason="page_limit")
    assert store.load_cursor("xhs", "fav", "acct-a")["cursor"]["page_index"] == 1
    assert store.load_cursor("xhs", "fav", "acct-b")["cursor"]["page_index"] == 2
    conn = db.connect()
    try:
        rows = conn.execute("SELECT account_key FROM sync_runs ORDER BY id").fetchall()
    finally:
        conn.close()
    assert [r[0] for r in rows] == ["acct-a", "acct-b"]


class FakeSourceBrowser:
    token = "SECRET-XSEC-TOKEN"
    def __init__(self, *a, **k):
        self.notes = [{"note_id": "note-70", "xsec_token": self.token}]
        self.closed = False
    def open(self): pass
    def close(self): self.closed = True
    def own_identity(self): return {"logged_in": True, "user_id": "u1"}
    def open_favorites(self, uid): return "ok"
    def list_snapshot(self): return {"notes": self.notes, "has_more": False}
    def scroll_list(self): pass
    def fetch_detail(self, note_id, token):
        assert token == self.token
        return {"status": "ok", "card": {"title": "原来vibe coding 不是指用AI写code呀…"}}


def test_source_resolves_token_in_memory_only(monkeypatch, tmp_path, caplog):
    monkeypatch.setattr(xhs_source.xhs, "XhsBrowser", FakeSourceBrowser)
    monkeypatch.setattr(xhs_source.xhs, "read_account", lambda p: "u1")
    with caplog.at_level(logging.DEBUG):
        out = xhs_source.resolve_note(tmp_path, "note-70", "原来vibe coding 不是指用AI写code呀")
    assert out["ok"] is True
    assert out["url"].startswith("https://www.xiaohongshu.com/explore/note-70?xsec_token=")
    assert FakeSourceBrowser.token not in caplog.text
    assert FakeSourceBrowser.token not in json.dumps({k: v for k, v in out.items() if k != "url"})


def test_source_stops_on_title_mismatch(monkeypatch, tmp_path):
    monkeypatch.setattr(xhs_source.xhs, "XhsBrowser", FakeSourceBrowser)
    monkeypatch.setattr(xhs_source.xhs, "read_account", lambda p: "u1")
    out = xhs_source.resolve_note(tmp_path, "note-70", "完全不同的标题")
    assert out["ok"] is False
    assert "不一致" in out["reason"]
    assert FakeSourceBrowser.token not in json.dumps(out, ensure_ascii=False)


def test_source_reports_missing_note(monkeypatch, tmp_path):
    class Missing(FakeSourceBrowser):
        def list_snapshot(self): return {"notes": [{"note_id": "other"}], "has_more": False}
    monkeypatch.setattr(xhs_source.xhs, "XhsBrowser", Missing)
    monkeypatch.setattr(xhs_source.xhs, "read_account", lambda p: "u1")
    out = xhs_source.resolve_note(tmp_path, "note-70", "t")
    assert out["ok"] is False and "找不到" in out["reason"]
