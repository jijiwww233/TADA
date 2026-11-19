"""XhsReader + real engine/store, with a fake browser (no network, no Chrome)."""
import hashlib
import json
import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import config, db, importer
from app.main import create_app
from app.sync import engine, runner, store
from app.sync.readers import xhs
from conftest import png

UID = "68f4cbcd000000003201e7bf"


def nid(n: int) -> str:
    return f"{n:024x}"


def color(key: str):
    h = hashlib.sha256(key.encode()).digest()
    return (h[0], h[1], h[2])


class FakeBrowser:
    """A favorites list revealed 10 at a time by scrolling, like the site."""

    def __init__(self, notes: list[dict]):
        self.notes = notes            # mutable between runs (new favorites, removals)
        self.details: dict = {}       # note_id -> override dict for fetch_detail
        self.download_fail: set[str] = set()
        self.viewer_id = UID
        self.own = {"logged_in": True, "user_id": UID}
        self.fav_state = "ok"
        self.stuck_after: int | None = None   # list stops growing although has_more
        self.api_error: str | None = None
        self.detail_calls: list[str] = []
        self.opened = self.closed = 0
        self.revealed = 0

    def open(self):
        self.opened += 1

    def close(self):
        self.closed += 1

    def own_identity(self):
        return self.own

    def open_favorites(self, uid):
        assert uid == UID
        self.revealed = min(10, len(self.notes))
        return self.fav_state

    def viewer(self):
        return {"logged_in": bool(self.viewer_id), "user_id": self.viewer_id}

    def list_snapshot(self):
        return {"notes": [dict(n) for n in self.notes[:self.revealed]],
                "has_more": self.revealed < len(self.notes), "api_error": self.api_error}

    def scroll_list(self):
        limit = len(self.notes) if self.stuck_after is None else self.stuck_after
        self.revealed = min(self.revealed + 10, limit)

    def fetch_detail(self, note_id, token):
        self.detail_calls.append(note_id)
        assert token == f"tok-{note_id}"
        if note_id in self.details:
            return self.details[note_id]
        n = next(x for x in self.notes if x["note_id"] == note_id)
        card = {"noteId": note_id, "title": f"标题 {note_id[-3:]}", "desc": f"简介 {note_id[-3:]}",
                "type": n["type"], "user": {"nickname": n["author"]}, "time": 1}
        images = [{"index": i, "url": f"https://sns-img.example/{note_id}/{i}.png"}
                  for i in range(n["n_images"])]
        return {"status": "ok", "card": card, "images": images}

    def download(self, url):
        if url in self.download_fail:
            raise RuntimeError("HTTP 403")
        return png(color(url)), "image/png"


def note(n: int, n_images: int = 1, type_: str = "normal") -> dict:
    key = nid(n)
    return {"note_id": key, "xsec_token": f"tok-{key}", "title": f"列表标题 {n}", "author": f"作者{n}",
            "cover": f"https://sns-img.example/{key}/cover.png", "type": type_, "n_images": n_images}


@pytest.fixture
def lib(data_dir):
    db.init_db()
    xhs.write_account(runner.profile_dir(), UID)
    return data_dir


def factory(browser):
    def make():
        return xhs.XhsReader(browser, runner.profile_dir(), config.sync_staging_dir("xhs"),
                             is_complete=lambda k: store.is_complete("xhs", k),
                             delay=0, sleep=lambda s: None)
    return make


def sync(browser, **kw):
    return runner.run_sync(factory(browser), sleep=lambda s: None, **kw)


def q(sql, *args):
    conn = db.connect()
    try:
        return [tuple(r) for r in conn.execute(sql, args)]
    finally:
        conn.close()


def counts():
    return q("SELECT (SELECT COUNT(*) FROM posts), (SELECT COUNT(*) FROM images),"
             " (SELECT COUNT(*) FROM post_images)")[0]


def slots(key):
    return q("""SELECT pi.position, pi.download_state, pi.remote_url, i.sha256
                FROM post_images pi JOIN posts p ON p.id = pi.post_id
                LEFT JOIN images i ON i.id = pi.image_id
                WHERE p.source_id = ? ORDER BY pi.position""", key)


def bootstrap(browser):
    for _ in range(20):
        r = sync(browser)
        if store.load_cursor("xhs", "fav")["bootstrap_complete"]:
            return r
    raise AssertionError("bootstrap did not finish")


# ------------------------------------------------------------ landing

def test_first_sync_lands_posts_in_order_video_cover_only(lib):
    b = FakeBrowser([note(1, 9), note(2, 1, "video"), *[note(n) for n in range(3, 8)]])
    b.notes[1]["n_images"] = 3  # the video detail lists frames; only the cover is kept
    r = sync(b)
    assert (r["status"], r["mode"], r["items_new"], r["images_new"]) == ("completed", "bootstrap", 7, 15)
    s = slots(nid(1))
    assert [x[0] for x in s] == list(range(9))
    assert [x[2] for x in s] == [f"https://sns-img.example/{nid(1)}/{i}.png" for i in range(9)]
    assert [x[3] for x in s] == [hashlib.sha256(png(color(x[2]))).hexdigest() for x in s]
    assert q("SELECT is_video, url, title, description, author_name FROM posts WHERE source_id = ?", nid(2)) == [
        (1, f"https://www.xiaohongshu.com/explore/{nid(2)}", f"标题 {nid(2)[-3:]}", f"简介 {nid(2)[-3:]}", "作者2")]
    assert len(slots(nid(2))) == 1
    assert q("SELECT COUNT(*) FROM posts WHERE url LIKE '%xsec%' OR extra_json LIKE '%tok-%'") == [(0,)]
    assert q("SELECT COUNT(*), SUM(status = 'pending') FROM analyses") == [(15, 15)]
    assert not any(config.sync_staging_dir("xhs").glob("*"))  # staging cleared
    assert b.opened == b.closed == 1
    # the new images are in the asset grid, newest post first
    grid = importer.list_images()
    assert len(grid) == 15 and all(g["platform"] == "xhs" for g in grid)


def test_resync_is_cheap_and_creates_nothing(lib):
    b = FakeBrowser([note(n, 2) for n in range(1, 6)])
    sync(b)
    before = counts()
    b.detail_calls.clear()
    r = sync(b)
    assert (r["mode"], r["items_new"], r["images_new"], r["status"]) == ("incremental", 0, 0, "completed")
    assert counts() == before == (5, 10, 10)
    assert b.detail_calls == []  # complete posts are not re-opened


def test_fast_path_keeps_metadata_and_slots(lib):
    b = FakeBrowser([note(1, 4), note(2, 1, "video")])
    sync(b)
    cols = "title, description, author_name, url, is_video, extra_json, fav_state, source_state"
    meta = q(f"SELECT source_id, {cols} FROM posts ORDER BY id")
    pis = [slots(nid(1)), slots(nid(2))]
    seen = q("SELECT last_seen_run_id FROM posts")
    for n in b.notes:  # list data differs from detail data; must not overwrite
        n["title"] = "列表里不同的标题"
        n["type"] = "normal"
    sync(b)
    assert q(f"SELECT source_id, {cols} FROM posts ORDER BY id") == meta
    assert [slots(nid(1)), slots(nid(2))] == pis
    assert q("SELECT last_seen_run_id FROM posts") != seen


# ------------------------------------------------------------ per-item failures

def test_detail_note_id_mismatch_is_a_detail_failure_then_retried(lib):
    b = FakeBrowser([note(1), note(2)])
    b.details[nid(2)] = {"status": "failed", "error": "详情 note_id 与请求不一致，已丢弃（防串帖）"}
    r = sync(b)
    assert (r["status"], r["items_failed"]) == ("partial", 1)
    assert q("SELECT source_id FROM posts") == [(nid(1),)]
    assert store.run_failures(r["id"]) == [
        {"work_key": nid(2), "stage": "detail", "error": "详情 note_id 与请求不一致，已丢弃（防串帖）"}]
    del b.details[nid(2)]
    r = sync(b)
    assert r["items_new"] == 1 and store.unresolved_failures("xhs") == []


def test_real_browser_rejects_a_foreign_detail(lib):
    """XhsBrowser.fetch_detail picks only the payload whose note_id equals the request."""
    other = {"code": 0, "data": {"items": [{"note_card": {"noteId": "other", "imageList": []}}]}}

    class Resp:
        url = "https://edith.xiaohongshu.com" + xhs.FEED_API

        def json(self):
            return other

    class Page:
        url = "https://www.xiaohongshu.com/explore/mine"

        def on(self, event, fn): fn(Resp())  # the page's own feed response arrives
        def remove_listener(self, *a): pass
        def bring_to_front(self): pass
        def wait_for_timeout(self, ms): pass
        def evaluate(self, js, arg=None): return ""  # no SSR entry for "mine"

    b = xhs.XhsBrowser(lib / "p")
    b.context, b.detail_page = object(), Page()
    b._goto_ready = lambda page, url, tries=3: True
    out = b.fetch_detail("mine", "")
    assert out["status"] == "failed" and "不一致" in out["error"]


def test_image_failure_keeps_slot_and_url_then_fills(lib):
    b = FakeBrowser([note(1, 3)])
    bad = f"https://sns-img.example/{nid(1)}/1.png"
    b.download_fail.add(bad)
    r = sync(b)
    assert (r["status"], r["images_failed"]) == ("partial", 1)
    assert [(x[0], x[1], x[2]) for x in slots(nid(1))] == [
        (0, "ok", f"https://sns-img.example/{nid(1)}/0.png"), (1, "failed", bad),
        (2, "ok", f"https://sns-img.example/{nid(1)}/2.png")]
    assert store.run_failures(r["id"])[0]["stage"] == "image_download"
    assert not store.is_complete("xhs", nid(1))
    b.download_fail.clear()
    r = sync(b)
    assert [x[1] for x in slots(nid(1))] == ["ok", "ok", "ok"]
    assert r["images_new"] == 1 and store.unresolved_failures("xhs") == []


def test_incremental_reaches_old_failure_on_page_6(lib, monkeypatch):
    monkeypatch.setenv("SYNC_MAX_PAGES", "5")
    b = FakeBrowser([note(n) for n in range(1, 71)])   # 7 pages
    deep = nid(55)                                       # page 6
    b.details[deep] = {"status": "failed", "error": "临时失败"}
    bootstrap(b)
    assert [f["work_key"] for f in store.unresolved_failures("xhs")] == [deep]
    del b.details[deep]
    b.detail_calls.clear()
    r = sync(b)  # page 1 holds only complete old favorites
    assert r["mode"] == "incremental" and r["pages_done"] >= 6
    assert b.detail_calls == [deep]
    assert store.unresolved_failures("xhs") == [] and store.is_complete("xhs", deep)
    r = sync(b)
    assert r["pages_done"] == 1  # nothing outstanding: stops at the first page


def test_incremental_scans_to_end_if_failed_item_was_unfavorited(lib):
    b = FakeBrowser([note(n) for n in range(1, 31)])
    b.details[nid(5)] = {"status": "failed", "error": "x"}
    bootstrap(b)
    b.notes = [n for n in b.notes if n["note_id"] != nid(5)]
    r = sync(b)
    assert (r["pages_done"], r["status"], r["full_scan"]) == (3, "completed", 0)
    assert q("SELECT fav_state FROM posts WHERE source_id = ?", nid(6)) == [("present",)]


# ------------------------------------------------------------ run-level failures never delete

def _seed(lib):
    b = FakeBrowser([note(1, 2), note(2)])
    sync(b)
    importer.import_images([importer.Upload("local.png", png((1, 2, 3)))])
    return b, counts(), len(list((lib / "images").iterdir()))


@pytest.mark.parametrize("setup, status, reason", [
    (lambda b: setattr(b, "fav_state", "blank"), "paused", "network"),
    (lambda b: setattr(b, "fav_state", "login"), "paused", "needs_login"),
    (lambda b: setattr(b, "fav_state", "captcha"), "paused", "blocked"),
    (lambda b: setattr(b, "viewer_id", ""), "paused", "needs_login"),
    (lambda b: setattr(b, "viewer_id", "0" * 24), "paused", "needs_login"),
    (lambda b: setattr(b, "api_error", "rate_limited"), "paused", "rate_limited"),
])
def test_run_failures_keep_all_data(lib, setup, status, reason):
    b, before, files = _seed(lib)
    b.notes.insert(0, note(9))
    setup(b)
    r = sync(b)
    assert (r["status"], r["pause_reason"]) == (status, reason)
    assert counts() == before and len(list((lib / "images").iterdir())) == files
    assert q("SELECT COUNT(*) FROM posts WHERE fav_state <> 'present'") == [(0,)]


def test_login_redirect_mid_page_rolls_back_page_only(lib):
    b, before, _ = _seed(lib)
    b.notes[0:0] = [note(8), note(9)]   # 8 fetches fine, then 9 hits the login page
    b.details[nid(9)] = {"status": "login"}
    r = sync(b)
    assert (r["status"], r["pause_reason"]) == ("paused", "needs_login")
    assert counts() == before  # note 8's page was rolled back as a whole


def test_truncated_list_is_not_a_finished_scan(lib):
    b = FakeBrowser([note(n) for n in range(1, 31)])
    b.stuck_after = 10
    r = sync(b)
    assert (r["status"], r["pause_reason"], r["pages_done"], r["full_scan"]) == ("paused", "network", 0, 0)
    assert not store.load_cursor("xhs", "fav")
    b.stuck_after = None
    from test_sync import fixture_manual_resume
    fixture_manual_resume()
    r = sync(b)
    assert r["status"] == "completed" and store.load_cursor("xhs", "fav")["bootstrap_complete"]


def test_needs_login_without_account_file(lib):
    (runner.profile_dir() / "account.json").unlink()
    b = FakeBrowser([note(1)])
    b.own = {"logged_in": False, "user_id": ""}
    r = sync(b)
    assert (r["status"], r["pause_reason"]) == ("paused", "needs_login")
    b.own = {"logged_in": True, "user_id": UID}
    assert sync(b)["status"] == "completed"
    assert xhs.read_account(runner.profile_dir()) == UID
    assert (config.data_dir() / "sync_profiles" / "xiaohongshu" / "account.json").exists()


def test_browser_start_failure_is_a_failed_run(lib):
    class Broken(FakeBrowser):
        def open(self):
            raise RuntimeError("profile in use")
    r = sync(Broken([note(1)]))
    assert r["status"] == "failed" and "profile in use" in r["error"]


# ------------------------------------------------------------ resume

def test_resume_from_anchor_without_gap_or_duplicate(lib):
    b = FakeBrowser([note(n) for n in range(1, 26)])  # 3 pages
    r = sync(b, max_pages=1)
    assert (r["status"], r["pause_reason"]) == ("paused", "page_limit")
    assert store.load_cursor("xhs", "fav")["cursor"]["next_cursor"] == nid(10)
    b.notes.insert(0, note(100))  # a new favorite on top meanwhile shifts every page
    b.detail_calls.clear()
    r = sync(b)
    assert r["status"] == "completed" and r["mode"] == "bootstrap"
    assert b.detail_calls == [nid(n) for n in range(11, 26)]  # exactly the rest, once each
    assert q("SELECT COUNT(*), COUNT(DISTINCT source_id) FROM posts") == [(25, 25)]
    r = sync(b)  # incremental picks up the one added on top
    assert r["items_new"] == 1 and q("SELECT COUNT(*) FROM posts") == [(26,)]


def test_lost_anchor_restarts_from_top_and_says_so(lib):
    b = FakeBrowser([note(n) for n in range(1, 26)])
    sync(b, max_pages=1)
    b.notes = [n for n in b.notes if n["note_id"] != nid(10)]
    r = sync(b)
    assert r["status"] == "completed" and "非断点续跑" in r["error"]
    assert q("SELECT COUNT(*) FROM posts") == [(24 + 1,)]


# ------------------------------------------------------------ AI / dedupe / safety

def test_done_analyses_untouched_new_images_pending(lib):
    b = FakeBrowser([note(1, 2)])
    sync(b)
    conn = db.connect()
    conn.execute("UPDATE analyses SET status = 'done', attempts = 1, description = 'x'")
    conn.close()
    b.notes.insert(0, note(2, 1))
    sync(b)
    assert q("SELECT status, attempts FROM analyses ORDER BY image_id") == [
        ("done", 1), ("done", 1), ("pending", 0)]


def test_image_shared_with_local_drop_is_one_asset_not_counted_new(lib):
    b = FakeBrowser([note(1, 1)])
    url = f"https://sns-img.example/{nid(1)}/0.png"
    importer.import_images([importer.Upload("same.png", png(color(url)))])
    r = sync(b)
    assert (r["items_new"], r["images_new"]) == (1, 0)
    assert q("SELECT COUNT(*) FROM images") == [(1,)]
    assert slots(nid(1))[0][1] == "ok"


def test_sync_never_deletes(lib, monkeypatch):
    statements = []
    real = db.connect

    def traced():
        conn = real()
        conn.set_trace_callback(statements.append)
        return conn

    monkeypatch.setattr(db, "connect", traced)
    b = FakeBrowser([note(n, 2) for n in range(1, 13)])
    b.download_fail.add(f"https://sns-img.example/{nid(3)}/1.png")
    b.details[nid(4)] = {"status": "failed", "error": "x"}
    sync(b)
    b.fav_state = "blank"
    sync(b)
    assert statements and not [s for s in statements if s.lstrip().upper().startswith("DELETE")]


def test_engine_full_mode_is_not_used(lib):
    b = FakeBrowser([note(1)])
    for _ in range(3):
        r = sync(b)
        assert r["mode"] in ("bootstrap", "incremental") and r["full_scan"] == 0


# ------------------------------------------------------------ API

def test_api_start_status_conflict_and_login(data_dir, backend_options):
    db.init_db()
    xhs.write_account(runner.profile_dir(), UID)
    b = FakeBrowser([note(1, 2)])
    gate = __import__("threading").Event()

    class SlowBrowser(FakeBrowser):
        def open(self):
            gate.wait(5)

    syncer = runner.SyncRunner(reader_factory=factory(b),
                               browser_factory=lambda: FakeLoginBrowser([GUEST, ME]))
    with TestClient(create_app(syncer=syncer), backend_options=backend_options) as c:
        assert c.get("/api/sync/xhs/status").json()["last_run"] is None
        assert c.post("/api/sync/xhs/start").status_code == 200
        syncer.join(10)
        st = c.get("/api/sync/xhs/status").json()
        assert st["busy"] is False
        assert (st["last_run"]["status"], st["last_run"]["items_new"], st["last_run"]["images_new"]) == (
            "completed", 1, 2)
        assert len(c.get("/api/images").json()) == 2

        syncer._reader_factory = factory(SlowBrowser([note(1)]))
        assert c.post("/api/sync/xhs/start").status_code == 200
        assert c.post("/api/sync/xhs/start").status_code == 409
        assert c.post("/api/sync/xhs/login").status_code == 409
        gate.set()
        syncer.join(10)

        assert c.post("/api/sync/xhs/login").status_code == 200
        syncer.join(10)
        assert c.get("/api/sync/xhs/status").json()["message"] == "登录成功"


# ------------------------------------------------------------ login window

GUEST_ID = "6aeb1abc0000000005039c04"
# What the site showed in the real run: explore stays loggedIn=false, /user/me knows the guest.
GUEST = {"ssr": {"loggedIn": False, "userId": GUEST_ID}, "me": {"user_id": GUEST_ID, "guest": True}, "nav": ""}
# After the scan: the explore store may still say false; the sidebar "我" link appears.
ME = {"ssr": {"loggedIn": False, "userId": GUEST_ID}, "me": None, "nav": UID}


class FakeLoginBrowser:
    """probes: one login_probe() result per poll (the last repeats); an Exception item is
    raised. verify_account(uid) succeeds only for UID once a ME probe has been returned
    (or from the start if already_logged_in). close_after: is_closed() after n polls."""

    def __init__(self, probes, close_after=None, already_logged_in=False):
        self.probes, self.close_after = list(probes), close_after
        self.logged_in = already_logged_in
        self.opened = self.closed = self.polls = 0
        self.shown = False
        self.events, self.verified = [], []

    def open(self):
        self.opened += 1

    def show_login(self):
        self.shown = True

    def is_closed(self):
        return self.close_after is not None and self.polls >= self.close_after

    def login_probe(self):
        self.polls += 1
        self.events.append(("poll", self.closed))
        r = self.probes.pop(0) if len(self.probes) > 1 else self.probes[0]
        if isinstance(r, Exception):
            raise r
        if r is ME:
            self.logged_in = True
        return r

    def verify_account(self, uid):
        self.verified.append(uid)
        return {"ok": self.logged_in and uid == UID}

    def pause(self, seconds):
        self.events.append(("pause", self.closed))

    def close(self):
        self.closed += 1


class Clock:
    def __init__(self, step):
        self.t, self.step = 0.0, step

    def __call__(self):
        self.t += self.step
        return self.t


def login(browser, **kw):
    return runner.run_login(lambda: browser, **kw)


def test_login_keeps_window_open_until_scan_then_closes(lib):
    (runner.profile_dir() / "account.json").unlink()
    b = FakeLoginBrowser([GUEST, GUEST, GUEST, ME])
    out = login(b, poll_s=0)
    assert out == {"ok": True, "message": "登录成功"}
    assert b.shown and b.opened == 1 and b.closed == 1
    assert b.polls == 4 and all(closed == 0 for _, closed in b.events)  # open while waiting
    assert b.verified == [UID]  # the guest id is never even tried
    assert xhs.read_account(runner.profile_dir()) == UID
    log = (config.data_dir() / "sync_logs" / "xhs_login.log").read_text(encoding="utf-8")
    assert "me.guest=True" in log and "nav=68f4cbcd" in log


def test_guest_without_flag_never_passes_verification(lib):
    unflagged = {"ssr": {}, "me": {"user_id": GUEST_ID}, "nav": ""}
    b = FakeLoginBrowser([unflagged])
    out = login(b, timeout_s=100, clock=Clock(10))
    assert "超时" in out["message"] and set(b.verified) == {GUEST_ID}
    assert 1 <= len(b.verified) <= 3  # throttled to one try per 30 s, not every poll
    assert xhs.read_account(runner.profile_dir()) == UID  # the fixture's value, untouched


def test_failed_verification_is_retried(lib):
    """Real run: the uid was found at once, the first check failed, and never got another."""
    b = FakeLoginBrowser([ME])
    results = iter([{"ok": False}, {"ok": True}])
    b.verify_account = lambda uid: (b.verified.append(uid), next(results))[1]
    out = login(b, timeout_s=480, clock=Clock(20))
    assert out["ok"] is True and b.verified == [UID, UID]


NODE = Path(__file__).resolve().parents[2].parent / "runtime" / "node" / "node.exe"


@pytest.mark.skipif(not (shutil.which("node") or NODE.exists()), reason="node not available")
def test_page_js_reads_vue_refs_around_booleans(tmp_path):
    """Live pages hold loggedIn as a ref {_rawValue: true}; raw SSR as a plain boolean."""
    script = tmp_path / "t.js"
    script.write_text("""
const viewer = eval('(' + %s + ')');
const ref = (v) => ({ __v_isRef: true, dep: {}, _rawValue: v, _value: v });
const out = [];
for (const user of [
  { loggedIn: ref(true), userInfo: ref({ userId: 'A' }) },
  { loggedIn: ref(false), userInfo: ref({ userId: 'G' }) },
  { loggedIn: true, userInfo: { userId: 'A' } },
  { loggedIn: false },
]) { global.window = { __INITIAL_STATE__: { user } }; out.push(JSON.parse(viewer())); }
console.log(JSON.stringify(out));
""" % json.dumps(xhs._VIEWER_JS), encoding="utf-8")
    node = shutil.which("node") or str(NODE)
    got = json.loads(subprocess.run([node, str(script)], capture_output=True, text=True, check=True).stdout)
    assert [g["loggedIn"] for g in got] == [True, False, True, False]
    assert [g["userId"] for g in got] == ["A", "G", "A", ""]


def test_already_logged_in_profile_succeeds_on_first_poll(lib):
    b = FakeLoginBrowser([ME], already_logged_in=True)
    assert login(b, poll_s=0)["ok"] is True and b.polls == 1


def test_login_timeout_closes_and_writes_nothing(lib):
    (runner.profile_dir() / "account.json").unlink()
    b = FakeLoginBrowser([GUEST])
    out = login(b, timeout_s=480, clock=Clock(60))
    assert out["ok"] is False and "超时" in out["message"]
    assert b.closed == 1 and b.polls >= 7
    assert not (runner.profile_dir() / "account.json").exists()


def test_user_closing_the_window_ends_login(lib):
    b = FakeLoginBrowser([GUEST], close_after=2)
    assert login(b, poll_s=0) == {"ok": False, "message": "登录窗口已关闭"}
    assert b.polls == 2 and b.closed == 1


def test_target_closed_error_counts_as_window_closed(lib):
    b = FakeLoginBrowser([GUEST, RuntimeError("Target page, context or browser has been closed")])
    assert login(b, poll_s=0)["message"] == "登录窗口已关闭"
    assert b.closed == 1


def test_transient_errors_do_not_end_login(lib):
    b = FakeLoginBrowser([RuntimeError("Execution context was destroyed"), GUEST, ME])
    assert login(b, poll_s=0)["ok"] is True and b.closed == 1


def test_browser_launch_failure_is_reported(lib):
    class Broken(FakeLoginBrowser):
        def open(self):
            raise RuntimeError("profile in use")
    out = login(Broken([GUEST]))
    assert out["ok"] is False and "profile in use" in out["message"]


def test_guest_identity_is_not_a_login():
    no = {"logged_in": False, "user_id": ""}
    yes = {"logged_in": True, "user_id": UID}
    assert xhs.judge_identity({"loggedIn": False, "userId": GUEST_ID}, None) == no
    assert xhs.judge_identity({}, {"user_id": GUEST_ID, "guest": True}) == no
    assert xhs.judge_identity({}, {"user_id": GUEST_ID}) == no  # guest flag missing
    assert xhs.judge_identity({"loggedIn": True, "userId": UID}, None) == yes
    assert xhs.judge_identity({}, {"user_id": UID, "guest": False}) == yes
    assert xhs.login_candidates(GUEST) == []
    assert xhs.login_candidates(ME) == [UID]
    assert xhs.login_candidates({"ssr": {"loggedIn": True, "userId": UID}, "me": None}) == [UID]


def test_login_window_visible_sync_window_offscreen(lib):
    login_args, sync_args = xhs.launch_args(True), xhs.launch_args(False)
    assert not any("-32000" in a or "=-" in a for a in login_args)
    assert "--window-position=80,60" in login_args
    assert "--window-position=-32000,-32000" in sync_args
    assert runner.login_browser().visible is True
    assert runner.make_reader().browser.visible is False
    assert runner.SyncRunner()._browser_factory is runner.login_browser
