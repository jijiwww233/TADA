"""ZCOOL process adapter: works + singles JSON -> sync-reader/1 page, fresh done.json only.

The offline synthetic-fixture tests run vendor/zcool/scripts/collect.mjs with
--offline (no Chrome, no network) to prove that a backfill walk really continues past
page 1 / item 30 and resumes from out/state.json.
"""
import io
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest
from PIL import Image

from app.sync import zcool_service as zs
from app.sync.contract import parse_page

NODE = shutil.which("node")
requires_node = pytest.mark.skipif(NODE is None, reason="需要 Node.js 运行离线合成样例")


def _out(tmp_path):
    (tmp_path / "data" / "works").mkdir(parents=True)
    (tmp_path / "images" / "w").mkdir(parents=True)
    return tmp_path


def _fake_run(out, done):
    def run(args, timeout):
        if done is not None:
            (out / "done.json").write_text(json.dumps(done), encoding="utf-8")
        class P:
            returncode = 0
            stderr = ""
        return P()
    return run


def _sha(char="a"):
    return char * 64


# ---------------------------------------------------------------- reader: works

def test_reader_converts_works_and_images(monkeypatch, tmp_path):
    out = _out(tmp_path)
    img = out / "images" / "w" / "01.png"
    img.write_bytes(b"not-a-real-png-but-readable")
    work = {"encodeId": "Z123=", "title": "t", "author": "a",
            "url": "https://www.zcool.com.cn/work/Z123=.html",
            "images": [{"idx": 1, "url": "https://img.zcool.cn/x.png", "file": "images/w/01.png",
                        "sha256": "abc", "width": 1, "height": 1}]}
    (out / "data" / "works" / "Z123=.json").write_text(json.dumps(work), encoding="utf-8")
    monkeypatch.setattr(zs, "out_dir", lambda: out)
    monkeypatch.setattr(zs, "_run_node",
                        _fake_run(out, {"status": "done", "auth_state": "logged_in"}))
    with zs.ZcoolFileReader(limit=1) as reader:
        page = reader.fetch_page("fav", None, 1)
    item = page["items"][0]
    assert item["work_key"] == "Z123=" and item["source_url"].startswith("https://www.zcool")
    assert item["images"][0]["idx"] == 1 and item["images"][0]["local_path"] == str(img)
    assert item["images"][0]["download_state"] == "ok"
    assert item["images"][0]["sha256"] is None  # invalid sha is dropped, not passed to contract
    parse_page(page)  # still a valid sync-reader/1 page


def test_reader_maps_missing_login(monkeypatch, tmp_path):
    out = _out(tmp_path)
    monkeypatch.setattr(zs, "out_dir", lambda: out)
    monkeypatch.setattr(zs, "_run_node",
                        _fake_run(out, {"status": "error", "auth_state": "not_logged_in"}))
    try:
        with zs.ZcoolFileReader(limit=1):
            pass
    except zs.engine.ReaderError as exc:
        assert exc.kind == "needs_login"
    else:
        raise AssertionError("expected needs_login")


def test_stale_done_json_does_not_report_success(monkeypatch, tmp_path):
    """A previous run's done.json must not make a failed/timeout run look successful."""
    out = _out(tmp_path)
    stale = out / "done.json"
    stale.write_text(json.dumps({"status": "done", "auth_state": "logged_in"}), encoding="utf-8")
    old = time.time() - 3600
    os.utime(stale, (old, old))
    monkeypatch.setattr(zs, "out_dir", lambda: out)
    monkeypatch.setattr(zs, "_run_node", _fake_run(out, None))
    try:
        with zs.ZcoolFileReader(limit=1):
            pass
    except zs.engine.ReaderError as exc:
        assert exc.kind == "network"
        assert "done.json" in str(exc)
    else:
        raise AssertionError("stale done.json must not be accepted")


def test_login_stale_done_json_is_not_success(monkeypatch, tmp_path):
    out = _out(tmp_path)
    stale = out / "done.json"
    stale.write_text(json.dumps({"status": "done", "auth_state": "logged_in"}), encoding="utf-8")
    old = time.time() - 3600
    os.utime(stale, (old, old))
    monkeypatch.setattr(zs, "out_dir", lambda: out)
    monkeypatch.setattr(zs, "profile_dir", lambda: tmp_path / "profile")
    monkeypatch.setattr(zs, "_run_node", _fake_run(out, None))
    result = zs.login(timeout_s=1)
    assert result["ok"] is False


def test_login_fresh_done_json_reports_success(monkeypatch, tmp_path):
    out = _out(tmp_path)
    monkeypatch.setattr(zs, "out_dir", lambda: out)
    monkeypatch.setattr(zs, "profile_dir", lambda: tmp_path / "profile")
    monkeypatch.setattr(zs, "_run_node",
                        _fake_run(out, {"status": "done", "auth_state": "logged_in"}))
    result = zs.login(timeout_s=1)
    assert result["ok"] is True


# ---------------------------------------------------------------- reader: singles + modes

def test_reader_ingests_single_image_favorites(monkeypatch, tmp_path):
    """Folder type 29 records from out/data/singles must reach the same page as works."""
    out = _out(tmp_path)
    single_img = out / "images" / "_single" / "图-77652284-abc.png"
    single_img.parent.mkdir(parents=True)
    single_img.write_bytes(b"single-bytes")
    page_url = "https://www.zcool.com.cn/workimage/ZMzEwNjA5MTM2.html"
    record = {"id": "77652284", "title": "文学之都", "pageUrl": page_url,
              "favoriteTime": 1790039072000, "width": 1968, "height": 4528,
              "url": "https://img.zcool.cn/community/x.jpg?k=1&t=2",
              "urlBig": "https://img.zcool.cn/community/x.jpg?k=1&t=2",
              "file": "images/_single/图-77652284-abc.png", "sha256": _sha(), "bytes": 12,
              "folder": "我喜欢的单图"}
    singles = out / "data" / "singles"
    singles.mkdir()
    (singles / "77652284.json").write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    work = {"encodeId": "Z123=", "title": "t", "author": "a",
            "url": "https://www.zcool.com.cn/work/Z123=.html", "images": []}
    (out / "data" / "works" / "Z123=.json").write_text(json.dumps(work), encoding="utf-8")
    monkeypatch.setattr(zs, "out_dir", lambda: out)
    monkeypatch.setattr(zs, "_run_node",
                        _fake_run(out, {"status": "done", "auth_state": "logged_in", "mode": "head"}))
    with zs.ZcoolFileReader(limit=1) as reader:
        page = reader.fetch_page("fav", None, 1)
    assert [it["work_key"] for it in page["items"]] == ["Z123=", "img29|77652284"]
    single = page["items"][1]
    assert single["canonical_url"] == page_url and single["source_url"] == page_url
    assert single["media_type"] == "image"
    assert single["images"][0]["idx"] == 1          # position 0 in the store
    assert single["images"][0]["local_path"] == str(single_img)
    assert single["images"][0]["download_state"] == "ok"
    assert single["images"][0]["sha256"] == _sha()
    assert single["extra"]["kind"] == "img29" and single["extra"]["order"] == 0
    parsed = parse_page(page)
    assert parsed.items[1].work_key == "img29|77652284"
    assert parsed.items[1].images[0].idx == 1


def test_reader_unknown_mode_rejected():
    with pytest.raises(ValueError):
        zs.ZcoolFileReader(mode="sideways")


def test_reader_mode_cli_args(monkeypatch, tmp_path):
    out = _out(tmp_path)
    calls = []

    def run(args, timeout):
        calls.append(list(args))
        mode = args[args.index("--mode") + 1]
        (out / "done.json").write_text(
            json.dumps({"status": "done", "auth_state": "logged_in", "mode": mode}),
            encoding="utf-8")
        class P:
            returncode = 0
            stderr = ""
        return P()

    monkeypatch.setattr(zs, "out_dir", lambda: out)
    monkeypatch.setattr(zs, "_run_node", run)
    with zs.ZcoolFileReader(limit=7, mode="head"):
        pass
    assert calls[-1] == ["scripts/collect.mjs", "--mode", "head", "--limit", "7", "--login-timeout", "0"]
    with zs.ZcoolFileReader(mode="backfill", max_pages=5, folders=20):
        pass
    assert calls[-1] == ["scripts/collect.mjs", "--mode", "backfill", "--limit", "0", "--login-timeout", "0",
                         "--max-pages", "5", "--folders", "20"]


def test_reader_rejects_done_json_from_other_mode(monkeypatch, tmp_path):
    out = _out(tmp_path)
    monkeypatch.setattr(zs, "out_dir", lambda: out)
    monkeypatch.setattr(zs, "_run_node",
                        _fake_run(out, {"status": "done", "auth_state": "logged_in", "mode": "head"}))
    try:
        with zs.ZcoolFileReader(mode="backfill"):
            pass
    except zs.engine.ReaderError as exc:
        assert exc.kind == "network"
        assert "模式不符" in str(exc)
    else:
        raise AssertionError("head output must not be accepted as backfill")


def test_reader_exposes_account_key_scoped_from_state(monkeypatch, tmp_path):
    """The account-scoped engine/store reads reader.account_key (schema v7)."""
    out = _out(tmp_path)
    state = {"account": {"id": 12345678, "idStr": "ZMTIzNDU2Nzg", "username": "u"}}

    def run(args, timeout):
        # collect.mjs refreshes state.account during the run; read it before done.json.
        (out / "state.json").write_text(json.dumps(state), encoding="utf-8")
        mode = args[args.index("--mode") + 1]
        (out / "done.json").write_text(
            json.dumps({"status": "done", "auth_state": "logged_in", "mode": mode}),
            encoding="utf-8")
        class P:
            returncode = 0
            stderr = ""
        return P()

    monkeypatch.setattr(zs, "out_dir", lambda: out)
    monkeypatch.setattr(zs, "_run_node", run)
    reader = zs.ZcoolFileReader(limit=1)
    assert reader.account_key == ""          # nothing collected yet
    with reader:
        assert reader.account_key == "ZMTIzNDU2Nzg"   # idStr preferred over numeric id

    (out / "state.json").write_text(json.dumps({"account": {"id": 42}}), encoding="utf-8")
    assert zs.ZcoolFileReader(limit=1).account_key == "42"   # numeric fallback


def test_run_sync_backfill_uses_backfill_reader(monkeypatch):
    captured = {}

    class FakeReader:
        def __init__(self, **kwargs):
            captured["reader"] = kwargs

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(zs, "ZcoolFileReader", FakeReader)
    monkeypatch.setattr(zs.engine, "run",
                        lambda reader, collection, mode: captured.update(
                            engine_mode=mode) or {"status": "completed"})
    monkeypatch.setattr(zs.store, "max_image_id", lambda: 0)
    monkeypatch.setattr(zs.store, "latest_run", lambda platform: {"status": "completed"})
    monkeypatch.setattr(zs.store, "images_created_since", lambda platform, base: 0)
    result = zs.run_sync(mode="backfill", limit=0)
    assert captured["reader"]["mode"] == "backfill"
    assert captured["engine_mode"] == "bootstrap"  # one page, has_more=false
    assert result["status"] == "completed"


# ---------------------------------------------------------------- offline synthetic >30 proof

SYN_WORKS = 38
SYN_PAGE_SIZE = 15


def _synthetic_fixtures(root: Path) -> Path:
    """3 local SSR/list pages with 38 works + a singles page; no network needed."""
    fixtures = root / "fixtures"
    (fixtures / "works").mkdir(parents=True)
    (fixtures / "singles").mkdir()
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), (20, 120, 200)).save(buffer, "PNG")
    (fixtures / "image-01.png").write_bytes(buffer.getvalue())

    def work(i):
        return {"idStr": f"ZSYN{i:03d}=", "id": 900000 + i, "objectType": 3,
                "title": f"合成作品 {i:02d}",
                "pageUrl": f"https://www.zcool.com.cn/work/ZSYN{i:03d}=.html",
                "cover": f"https://img.zcool.cn/synthetic/{i}.jpg", "imageCount": 1,
                "favoriteTime": 1700000000000 - i * 1000}

    for page in range(3):
        first, last = page * SYN_PAGE_SIZE + 1, min((page + 1) * SYN_PAGE_SIZE, SYN_WORKS)
        payload = {"resultCode": "SUCCESS", "data": {
            "contentTotal": SYN_WORKS, "page": page + 1, "pageSize": SYN_PAGE_SIZE,
            "hasNext": page < 2, "contentList": [work(i) for i in range(first, last + 1)]}}
        (fixtures / "works" / f"page-{page + 1}.json").write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    singles = {"resultCode": "SUCCESS", "data": {
        "contentTotal": 2, "page": 1, "pageSize": 15, "hasNext": False, "contentList": [
            {"id": 77652284, "title": "合成单图一",
             "pageUrl": "https://www.zcool.com.cn/workimage/ZMzEwNjA5MTM2.html",
             "url": "https://img.zcool.cn/community/synthetic-single-1.jpg",
             "urlBig": "https://img.zcool.cn/community/synthetic-single-1.jpg",
             "width": 1968, "height": 4528, "favoriteTime": 1790039072000},
            {"id": 77652285, "title": "合成单图二",
             "pageUrl": "https://www.zcool.com.cn/workimage/ZMzEwNjA5MTM3.html",
             "url": "https://img.zcool.cn/community/synthetic-single-2.jpg",
             "urlBig": "https://img.zcool.cn/community/synthetic-single-2.jpg",
             "width": 1200, "height": 1600, "favoriteTime": 1790039071000},
        ]}}
    (fixtures / "singles" / "page-1.json").write_text(
        json.dumps(singles, ensure_ascii=False), encoding="utf-8")
    folders = {"version": 1, "account": {"id": 12345678, "username": "synthetic"},
               "image": "image-01.png", "folders": [
                   {"id": 0, "type": 28, "name": "合成作品夹", "contentCount": SYN_WORKS,
                    "pageUrl": "https://www.zcool.com.cn/collection/ZMA==",
                    "pages": ["works/page-1.json", "works/page-2.json", "works/page-3.json"]},
                   {"id": 0, "type": 29, "name": "合成单图夹", "contentCount": 2,
                    "pageUrl": "https://www.zcool.com.cn/favorite/ZMA==",
                    "pages": ["singles/page-1.json"]},
               ]}
    (fixtures / "folders.json").write_text(json.dumps(folders, ensure_ascii=False), encoding="utf-8")
    return fixtures


def _offline(fixtures: Path, out: Path, *extra: str):
    return subprocess.run(
        [NODE, "scripts/collect.mjs", "--offline", "--fixtures", str(fixtures),
         "--out", str(out), *extra],
        cwd=str(zs.vendor_dir()), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=120)


@requires_node
def test_offline_backfill_reads_works_past_page_one(tmp_path, monkeypatch):
    fixtures = _synthetic_fixtures(tmp_path)
    out = tmp_path / "out"
    proc = _offline(fixtures, out, "--mode", "backfill", "--limit", "0")
    assert proc.returncode == 0, proc.stdout + proc.stderr

    works = sorted((out / "data" / "works").glob("*.json"))
    assert len(works) == SYN_WORKS
    # Items 31..38 only exist on the third page: reaching them proves >30 continuation.
    assert (out / "data" / "works" / "ZSYN031=.json").is_file()
    assert (out / "data" / "works" / "ZSYN038=.json").is_file()
    assert len(list((out / "data" / "singles").glob("*.json"))) == 2

    state = json.loads((out / "state.json").read_text(encoding="utf-8"))
    progress = state["pagination"]["folders"]["folder:28:0"]
    assert progress["pages_seen"] == 3
    assert progress["items_seen"] == SYN_WORKS
    assert progress["done"] is True
    done = json.loads((out / "done.json").read_text(encoding="utf-8"))
    assert done["status"] == "done" and done["mode"] == "backfill"
    assert done["pages_written"] >= 3 and done["has_more"] is False

    # Same sync-reader/1 page the store consumes: 38 works + 2 singles.
    monkeypatch.setattr(zs, "out_dir", lambda: out)
    monkeypatch.setattr(zs, "_run_node",
                        _fake_run(out, {"status": "done", "auth_state": "logged_in",
                                        "mode": "backfill"}))
    with zs.ZcoolFileReader(limit=30, mode="backfill") as reader:
        page = reader.fetch_page("fav", None, 1)
    keys = {item["work_key"] for item in page["items"]}
    assert len(page["items"]) == 40
    assert {f"ZSYN{i:03d}=" for i in range(31, 39)} <= keys
    singles = [item for item in page["items"] if item["work_key"].startswith("img29|")]
    assert {item["work_key"] for item in singles} == {"img29|77652284", "img29|77652285"}
    assert all(item["images"][0]["idx"] == 1 for item in singles)
    assert all(item["images"][0]["local_path"] for item in singles)
    assert len(parse_page(page).items) == 40


@requires_node
def test_offline_head_only_reads_newest_page(tmp_path):
    fixtures = _synthetic_fixtures(tmp_path)
    out = tmp_path / "out"
    proc = _offline(fixtures, out, "--mode", "head", "--limit", "30")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert len(list((out / "data" / "works").glob("*.json"))) == SYN_PAGE_SIZE
    assert not (out / "data" / "works" / "ZSYN031=.json").exists()
    state = json.loads((out / "state.json").read_text(encoding="utf-8"))
    assert state["pagination"]["folders"]["folder:28:0"]["pages_seen"] == 1
    done = json.loads((out / "done.json").read_text(encoding="utf-8"))
    assert done["mode"] == "head"
    # singles are still checked on the newest page
    assert done["singles_done"] == 2


@requires_node
def test_offline_backfill_resumes_from_saved_state(tmp_path):
    fixtures = _synthetic_fixtures(tmp_path)
    out = tmp_path / "out"
    first = _offline(fixtures, out, "--mode", "backfill", "--limit", "0", "--max-pages", "2")
    assert first.returncode == 0, first.stdout + first.stderr
    assert len(list((out / "data" / "works").glob("*.json"))) == 2 * SYN_PAGE_SIZE
    state = json.loads((out / "state.json").read_text(encoding="utf-8"))
    progress = state["pagination"]["folders"]["folder:28:0"]
    assert progress["next_page"] == 3 and progress["done"] is False

    second = _offline(fixtures, out, "--mode", "backfill", "--limit", "0")
    assert second.returncode == 0, second.stdout + second.stderr
    assert len(list((out / "data" / "works").glob("*.json"))) == SYN_WORKS
    state = json.loads((out / "state.json").read_text(encoding="utf-8"))
    progress = state["pagination"]["folders"]["folder:28:0"]
    assert progress["pages_seen"] == 3 and progress["items_seen"] == SYN_WORKS
    assert progress["done"] is True and progress["pages_fetched"] == 1
    done = json.loads((out / "done.json").read_text(encoding="utf-8"))
    assert done["resumed_from_page"]["folder:28:0"] == 3
    # The finished singles folder is not re-fetched by a later backfill.
    assert done["pages_written"] == 1


@requires_node
def test_head_run_keeps_backfill_resume_point(tmp_path):
    """A cheap head check between two backfills must not mark history done or lose progress."""
    fixtures = _synthetic_fixtures(tmp_path)
    out = tmp_path / "out"
    first = _offline(fixtures, out, "--mode", "backfill", "--limit", "0", "--max-pages", "2")
    assert first.returncode == 0, first.stdout + first.stderr

    head = _offline(fixtures, out, "--mode", "head", "--limit", "30")
    assert head.returncode == 0, head.stdout + head.stderr
    state = json.loads((out / "state.json").read_text(encoding="utf-8"))
    progress = state["pagination"]["folders"]["folder:28:0"]
    assert progress["done"] is False       # head check does not complete history
    assert progress["next_page"] == 3      # nor reset the resume point

    resume = _offline(fixtures, out, "--mode", "backfill", "--limit", "0")
    assert resume.returncode == 0, resume.stdout + resume.stderr
    assert len(list((out / "data" / "works").glob("*.json"))) == SYN_WORKS
    done = json.loads((out / "done.json").read_text(encoding="utf-8"))
    assert done["resumed_from_page"]["folder:28:0"] == 3


@requires_node
def test_node_walker_unit_suite():
    """The live capture/replay walker parsing is covered by node --test, without Chrome."""
    proc = subprocess.run([NODE, "--test"], cwd=str(zs.vendor_dir()), capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=120)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "fail 0" in proc.stdout
