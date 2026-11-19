"""Sync contract: store + engine with a fake reader (no platform, no network)."""
import sqlite3

import pytest

from app import db, importer
from app.sync import engine, store, safety
from conftest import png

PLATFORM, FAV = "xhs", "fav"


@pytest.fixture
def lib(data_dir):
    db.init_db()
    return data_dir


class FakeReader:
    """pages: {page_index: page dict | ReaderError | list of them (one per call)}"""

    platform = PLATFORM

    def __init__(self, pages, auth="ok"):
        self.pages, self.auth, self.calls = pages, auth, []

    def check_auth(self):
        return self.auth

    def fetch_page(self, collection_key, cursor, page_index):
        self.calls.append((cursor, page_index))
        p = self.pages[page_index]
        if isinstance(p, list):
            p = p.pop(0) if len(p) > 1 else p[0]
        if isinstance(p, Exception):
            raise p
        return p


class Lib:
    """Writes reader-side image files and builds contract pages."""

    def __init__(self, tmp):
        self.dir = tmp / "reader_out"
        self.dir.mkdir()

    def img(self, idx, color=None, **kw):
        ref = {"idx": idx, "url": f"https://img.example/{color or 'x'}-{idx}.png", **kw}
        if color:
            path = self.dir / f"{color}.png"
            path.write_bytes(png(color))
            ref["local_path"] = str(path)
        return ref


def item(key, *images, **kw):
    return {"work_key": key, "title": f"t-{key}", "media_type": "image",
            "canonical_url": f"https://www.xiaohongshu.com/explore/{key}",
            "images": list(images), **kw}


def page(index, items, has_more, failures=None):
    p = {"contract_version": "sync-reader/1", "platform": PLATFORM,
         "collection": {"collection_key": FAV},
         "page": {"index": index, "cursor_in": None if index == 1 else f"c{index - 1}",
                  "cursor_out": f"c{index}" if has_more else None, "has_more": has_more},
         "items": items}
    if failures is not None:
        p["failures"] = failures
    return p


def run(pages, mode="bootstrap", **kw):
    reader = FakeReader(pages, auth=kw.pop("auth", "ok"))
    result = engine.run(reader, FAV, mode, sleep=lambda s: None, **kw)
    return result, reader


def q(sql, *args):
    conn = db.connect()
    try:
        return [tuple(r) for r in conn.execute(sql, args)]
    finally:
        conn.close()


def post_state(key):
    return q("SELECT fav_state, miss_streak, source_state FROM posts WHERE source_id = ?", key)[0]


def n_files(lib):
    return len(list((lib / "images").iterdir()))


@pytest.fixture
def L(tmp_path):
    return Lib(tmp_path)


# ------------------------------------------------------------ basic landing

def test_bootstrap_lands_posts_images_order_and_cursor(lib, L):
    pages = {1: page(1, [item("n1", L.img(1, "red"), L.img(2, "green"))], True),
             2: page(2, [item("n2", L.img(1, "blue"))], False)}
    r, reader = run(pages)
    assert (r["status"], r["pages_done"], r["items_new"], r["images_ok"]) == ("completed", 2, 2, 3)
    assert reader.calls == [(None, 1), ("c1", 2)]
    post_id = q("SELECT id FROM posts WHERE source_id = 'n1'")[0][0]
    pis = importer.list_post_images(post_id)
    assert [(p["position"], p["download_state"]) for p in pis] == [(0, "ok"), (1, "ok")]
    assert post_state("n1") == ("present", 0, "ok")
    assert store.load_cursor(PLATFORM, FAV)["bootstrap_complete"] is True
    assert q("SELECT COUNT(*) FROM analyses WHERE status = 'pending'") == [(3,)]
    assert n_files(lib) == 3


def test_same_work_key_is_one_post_and_resync_changes_nothing(lib, L):
    pages = {1: page(1, [item("n1", L.img(1, "red"))], False)}
    run(pages)
    run(pages, mode="full")
    assert q("SELECT COUNT(*) FROM posts") == [(1,)]
    assert q("SELECT COUNT(*) FROM images") == [(1,)]
    assert q("SELECT COUNT(*) FROM post_images") == [(1,)]


# ------------------------------------------------------------ resume / pause

def fixture_manual_resume():
    # Simulate a user-authorized later retry in this test's isolated safety dir.
    # Never clears the real machine-wide incident hold.
    safety.path().write_text('{"until":0,"manual_hold":false,"auto_paused":false}',encoding='utf8')


def test_resume_after_page_failure(lib, L):
    err = engine.ReaderError("network")
    pages = {1: page(1, [item("n1", L.img(1, "red"))], True),
             2: [err, page(2, [item("n2", L.img(1, "blue"))], False)]}
    r, reader = run(pages)
    assert (r["status"], r["pause_reason"]) == ("paused", "network")
    assert len(reader.calls) == 2  # page 1, page 2 once; XHS no longer auto retries
    fixture_manual_resume()
    assert store.load_cursor(PLATFORM, FAV)["cursor"]["page_index"] == 1

    r2, reader2 = run(pages)
    assert reader2.calls == [("c1", 2)]  # continues from the failed page
    assert r2["status"] == "completed"
    assert q("SELECT source_id FROM posts ORDER BY id") == [("n1",), ("n2",)]


def test_login_expired_pauses_without_retry_or_data_loss(lib, L):
    run({1: page(1, [item("n1", L.img(1, "red"))], False)})
    pages = {1: page(1, [item("n0", L.img(1, "blue"))], True),
             2: engine.ReaderError("needs_login")}
    r, reader = run(pages, mode="full")
    assert (r["status"], r["pause_reason"]) == ("paused", "needs_login")
    assert len(reader.calls) == 2
    assert q("SELECT COUNT(*) FROM posts") == [(2,)]
    assert post_state("n1") == ("present", 0, "ok")

    r2, reader2 = run(pages, auth="needs_login")
    assert (r2["status"], r2["pause_reason"], reader2.calls) == ("paused", "needs_login", [])


def test_recover_stale_runs(lib):
    run_id = store.start_run(PLATFORM, FAV, "bootstrap")
    assert store.recover_stale_runs() == 1
    assert store.get_run(run_id)["pause_reason"] == "stale_process"


# ------------------------------------------------------------ cursor vs failures

def test_item_store_failure_is_persisted_and_cursor_advances(lib, L, monkeypatch):
    real = store._apply_item

    def flaky(conn, run_id, platform, it, created, result, *args):
        if it.work_key == "bad":
            real(conn, run_id, platform, it, created, result)  # writes rows + file first
            raise RuntimeError("boom")
        return real(conn, run_id, platform, it, created, result)

    monkeypatch.setattr(store, "_apply_item", flaky)
    pages = {1: page(1, [item("ok1", L.img(1, "red")), item("bad", L.img(1, "green"))], True),
             2: page(2, [item("ok2", L.img(1, "blue"))], False)}
    r, _ = run(pages)
    assert (r["status"], r["items_failed"]) == ("partial", 1)
    assert q("SELECT source_id FROM posts ORDER BY id") == [("ok1",), ("ok2",)]
    assert n_files(lib) == 2  # the failed item's file was removed with its savepoint
    assert store.unresolved_failures(PLATFORM) == [
        {"work_key": "bad", "stage": "store", "error": "RuntimeError: boom"}]
    assert store.load_cursor(PLATFORM, FAV)["cursor"]["page_index"] == 2

    monkeypatch.setattr(store, "_apply_item", real)
    run({1: page(1, [item("bad", L.img(1, "green"))], False)}, mode="full")
    assert store.unresolved_failures(PLATFORM) == []


def test_cursor_stays_if_failure_cannot_be_recorded(lib, L, monkeypatch):
    run({1: page(1, [item("n1", L.img(1, "red"))], True),
         2: engine.ReaderError("network")})
    real = store._apply_item

    def flaky(conn, run_id, platform, it, created, result, *args):
        if it.work_key == "bad":
            raise RuntimeError("boom")
        return real(conn, run_id, platform, it, created, result)

    def cannot_record(*a, **kw):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(store, "_apply_item", flaky)
    monkeypatch.setattr(store, "_record_failure", cannot_record)
    fixture_manual_resume()
    r, _ = run({2: page(2, [item("ok2", L.img(1, "blue")), item("bad", L.img(1, "green"))], False)})
    assert r["status"] == "failed" and "disk I/O error" in r["error"]
    assert store.load_cursor(PLATFORM, FAV)["cursor"]["page_index"] == 1  # not past page 2
    assert q("SELECT source_id FROM posts") == [("n1",)]  # ok2 rolled back too
    assert n_files(lib) == 1


def test_item_without_work_key_rejects_page(lib, L):
    pages = {1: page(1, [item("n1", L.img(1, "red")), {"title": "no key", "images": []}], False)}
    r, _ = run(pages)
    assert r["status"] == "failed" and "work_key" in r["error"]
    assert store.load_cursor(PLATFORM, FAV) is None
    assert q("SELECT COUNT(*) FROM posts") == [(0,)]
    assert n_files(lib) == 0


def test_reader_failure_without_work_key_rejects_page(lib, L):
    pages = {1: page(1, [item("n1", L.img(1, "red"))], False,
                     failures=[{"stage": "detail", "error": "timeout"}])}
    r, _ = run(pages)
    assert r["status"] == "failed"
    assert store.load_cursor(PLATFORM, FAV) is None


def test_invalid_item_and_reader_failure_are_retryable(lib, L):
    pages = {1: page(1, [item("n1", L.img(1, "red")),
                         item("weird", {"idx": 0, "url": "https://x/y.png"})], False,
                     failures=[{"work_key": "n9", "stage": "detail", "error": "timeout"}])}
    r, _ = run(pages)
    assert (r["status"], r["items_failed"]) == ("partial", 2)
    assert {(f["work_key"], f["stage"]) for f in store.unresolved_failures(PLATFORM)} == \
        {("weird", "contract"), ("n9", "detail")}
    assert store.load_cursor(PLATFORM, FAV)["bootstrap_complete"] is True


def test_image_download_failure_keeps_slot_and_retries(lib, L):
    pages = {1: page(1, [item("n1", L.img(1, "red"), L.img(2, download_state="failed"),
                              L.img(3, "blue"))], False)}
    r, _ = run(pages)
    assert (r["status"], r["images_ok"], r["images_failed"]) == ("partial", 2, 1)
    post_id = q("SELECT id FROM posts")[0][0]
    slots = importer.list_post_images(post_id)
    assert [(s["position"], s["download_state"]) for s in slots] == \
        [(0, "ok"), (1, "failed"), (2, "ok")]
    assert slots[1]["remote_url"] == "https://img.example/x-2.png"
    assert store.unresolved_failures(PLATFORM)[0]["stage"] == "image_download"

    run({1: page(1, [item("n1", L.img(1, "red"), L.img(2, "green"), L.img(3, "blue"))], False)},
        mode="full")
    assert [s["download_state"] for s in importer.list_post_images(post_id)] == ["ok"] * 3
    assert store.unresolved_failures(PLATFORM) == []

    # a later failed fetch must not downgrade a saved image
    run({1: page(1, [item("n1", L.img(1, download_state="failed"))], False)}, mode="full")
    assert importer.list_post_images(post_id)[0]["download_state"] == "ok"


# ------------------------------------------------------------ dedupe across sources

def test_shared_image_is_one_asset_posts_stay_separate(lib, L):
    local = importer.import_images([importer.Upload("mine.png", png("red"))])
    run({1: page(1, [item("n1", L.img(1, "blue"), L.img(2, "red")),
                     item("n2", L.img(1, "red"))], False)})
    assert q("SELECT COUNT(*) FROM images") == [(2,)]
    assert q("SELECT platform, COUNT(*) FROM posts GROUP BY platform") == [("local", 1), ("xhs", 2)]
    red_id = local["added"][0]["id"]
    n1 = q("SELECT id FROM posts WHERE source_id = 'n1'")[0][0]
    assert [s["image_id"] for s in importer.list_post_images(n1)][1] == red_id
    grid = importer.list_images()
    assert sorted(i["id"] for i in grid) == sorted({i["id"] for i in grid})  # each image once
    assert len(grid) == 2
    assert n_files(lib) == 2


# ------------------------------------------------------------ never delete / presence

def test_missing_items_marked_only_by_clean_full_scans(lib, L):
    importer.import_images([importer.Upload("mine.png", png("white"))])
    abc = [item("A", L.img(1, "red")), item("B", L.img(1, "green")), item("C", L.img(1, "blue"))]
    run({1: page(1, abc, False)})
    ab = {1: page(1, abc[:2], False)}

    run(ab, mode="incremental")  # incremental never marks
    assert post_state("C") == ("present", 0, "ok")

    run(ab, mode="full")
    assert post_state("C") == ("unverified", 1, "ok")
    assert post_state("A") == ("present", 0, "ok")

    r, _ = run({1: page(1, abc[:2], False, failures=[{"work_key": "Z", "stage": "detail"}])},
               mode="full")
    assert (r["status"], r["full_scan"]) == ("partial", 0)
    assert post_state("C") == ("unverified", 1, "ok")

    r, _ = run({1: page(1, abc[:2], True), 2: engine.ReaderError("parse")}, mode="full")
    assert (r["status"], r["full_scan"]) == ("failed", 0)
    assert post_state("C") == ("unverified", 1, "ok")


def test_three_clean_full_scans_mark_possibly_removed_never_delete(lib, L):
    importer.import_images([importer.Upload("mine.png", png("white"))])
    abc = [item("A", L.img(1, "red")), item("B", L.img(1, "green")), item("C", L.img(1, "blue"))]
    run({1: page(1, abc, False)})
    ab = {1: page(1, abc[:2], False)}
    for streak in (1, 2):
        r, _ = run(ab, mode="full")
        assert r["full_scan"] == 1
        assert post_state("C") == ("unverified", streak, "ok")
    run(ab, mode="full")
    assert post_state("C") == ("possibly_removed", 3, "ok")
    # everything still on disk and in the db
    assert q("SELECT COUNT(*) FROM posts") == [(4,)]
    assert q("SELECT COUNT(*) FROM post_images") == [(4,)]
    assert n_files(lib) == 4
    # local post never takes part
    assert q("SELECT fav_state, miss_streak FROM posts WHERE platform = 'local'") == [(None, None)]

    run({1: page(1, [abc[2]], False)}, mode="incremental")  # reappears
    assert post_state("C") == ("present", 0, "ok")


def test_full_scan_resumed_across_runs_still_counts(lib, L):
    abc = [item("A", L.img(1, "red")), item("B", L.img(1, "green")), item("C", L.img(1, "blue"))]
    run({1: page(1, abc, False)})
    err = engine.ReaderError("network")
    pages = {1: page(1, [abc[0]], True), 2: [err, page(2, [abc[1]], False)]}
    r1, _ = run(pages, mode="full")
    assert r1["status"] == "paused"
    assert post_state("C") == ("present", 0, "ok")
    fixture_manual_resume()
    r2, reader = run(pages, mode="full")
    assert reader.calls == [("c1", 2)] and r2["full_scan"] == 1
    assert post_state("A") == ("present", 0, "ok")  # seen in the first run of the scan
    assert post_state("C") == ("unverified", 1, "ok")


def test_partial_page_in_resumed_full_scan_blocks_marking(lib, L):
    abc = [item("A", L.img(1, "red")), item("B", L.img(1, "green")), item("C", L.img(1, "blue"))]
    run({1: page(1, abc, False)})
    pages = {1: page(1, [abc[0]], True, failures=[{"work_key": "X", "stage": "detail"}]),
             2: engine.ReaderError("network")}
    run(pages, mode="full")
    r, _ = run({2: page(2, [abc[1]], False)}, mode="full")
    assert r["full_scan"] == 0
    assert post_state("C") == ("present", 0, "ok")


def test_source_unavailable_is_marked_not_deleted(lib, L):
    run({1: page(1, [item("n1", L.img(1, "red"))], False)})
    run({1: page(1, [], False, failures=[
        {"work_key": "n1", "stage": "detail", "error": "404", "source_unavailable": True}])},
        mode="full")
    assert post_state("n1") == ("present", 0, "unavailable")
    assert q("SELECT COUNT(*) FROM post_images") == [(1,)]


def test_sync_never_issues_delete(lib, L, monkeypatch):
    statements = []
    real_connect = db.connect

    def traced():
        conn = real_connect()
        conn.set_trace_callback(statements.append)
        return conn

    monkeypatch.setattr(db, "connect", traced)
    abc = [item("A", L.img(1, "red")), item("B", L.img(1, "green"))]
    run({1: page(1, abc, False)})
    for _ in range(3):
        run({1: page(1, abc[:1], False)}, mode="full")
    run({1: page(1, abc[:1], True), 2: engine.ReaderError("needs_login")}, mode="full")
    assert statements and not [s for s in statements if s.lstrip().upper().startswith("DELETE")]


# ------------------------------------------------------------ incremental / AI

def test_incremental_stops_at_first_page_without_new_posts(lib, L):
    run({1: page(1, [item("A", L.img(1, "red"))], False)})
    pages = {1: page(1, [item("N", L.img(1, "blue"))], True),
             2: page(2, [item("A", L.img(1, "red"))], True),
             3: page(3, [item("old", L.img(1, "green"))], False)}
    r, reader = run(pages, mode="incremental")
    assert [c[1] for c in reader.calls] == [1, 2]
    assert (r["mode"], r["items_new"]) == ("incremental", 1)


def test_incremental_before_bootstrap_becomes_bootstrap(lib, L):
    r, _ = run({1: page(1, [item("A", L.img(1, "red"))], False)}, mode="incremental")
    assert r["mode"] == "bootstrap"


def test_resync_never_reanalyzes(lib, L):
    pages = {1: page(1, [item("n1", L.img(1, "red"), L.img(2, "blue"))], False)}
    run(pages)
    conn = db.connect()
    conn.execute("UPDATE analyses SET status = 'done', attempts = 1, description = 'x'")
    conn.close()
    run(pages, mode="full")
    run(pages, mode="incremental")
    importer.import_images([importer.Upload("again.png", png("red"))])
    assert q("SELECT COUNT(*), SUM(status = 'done'), SUM(attempts) FROM analyses") == [(2, 2, 2)]
