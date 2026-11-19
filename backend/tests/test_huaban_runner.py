"""Executable tests for multi-board Huaban orchestration and cursor semantics."""
from __future__ import annotations

import hashlib

import pytest
from PIL import Image

from app import db
from app.sync.contract import CONTRACT_VERSION
from app.sync.engine import ReaderError
from app.sync.huaban_migration import migrate_huaban_platform
from app.sync.huaban_runner import run_boards


@pytest.fixture
def app_db(tmp_path, monkeypatch):
    monkeypatch.setenv("WOMINMIN_DATA_DIR", str(tmp_path))
    db.init_db()
    conn = db.connect()
    try:
        migrate_huaban_platform(conn)
    finally:
        conn.close()
    return tmp_path


def page(collection_key, work_keys, *, index=1, cursor_in=None, cursor_out=None,
         has_more=False):
    if cursor_out is None and work_keys:
        cursor_out = str(work_keys[-1])
    return {
        "contract_version": CONTRACT_VERSION,
        "platform": "huaban",
        "collection": {"collection_key": collection_key},
        "page": {"index": index, "cursor_in": cursor_in,
                 "cursor_out": cursor_out, "has_more": has_more},
        "items": [
            {
                "work_key": str(key),
                "title": f"pin {key}",
                "canonical_url": f"https://huaban.com/pins/{key}",
                "media_type": "image",
                "images": [{"idx": 1, "url": f"https://img.example/{key}.jpg",
                            "download_state": "remote_only"}],
            }
            for key in work_keys
        ],
        "failures": [],
    }


class FakeReader:
    platform = "huaban"

    def __init__(self, boards, pages):
        self.boards = boards
        self.pages = pages
        self.calls: list[tuple[str, str | None, int]] = []

    def check_auth(self):
        return "ok"

    def list_boards(self):
        return self.boards

    def fetch_page(self, collection_key, cursor, page_index):
        self.calls.append((collection_key, cursor, page_index))
        value = self.pages.get((collection_key, cursor))
        if value is None:
            raise ReaderError("network", f"no page for {collection_key}/{cursor}")
        if isinstance(value, Exception):
            raise value
        return value


def boards(*keys):
    return [
        {"collection_key": key, "board_id": str(i + 1), "title": f"board {i + 1}",
         "pin_count": 0, "is_private": False, "urlname": "me"}
        for i, key in enumerate(keys)
    ]


def count_posts() -> int:
    conn = db.connect()
    try:
        return conn.execute("SELECT COUNT(*) FROM posts WHERE platform='huaban'").fetchone()[0]
    finally:
        conn.close()


def test_two_boards_run_independently_with_totals(app_db):
    reader = FakeReader(
        boards("board:A", "board:B"),
        {
            ("board:A", None): page("board:A", [102, 101], has_more=False),
            ("board:B", None): page("board:B", [202], has_more=False),
        },
    )
    report = run_boards(reader, sleep=lambda _: None)
    assert [b.collection_key for b in report.boards] == ["board:A", "board:B"]
    assert [b.status for b in report.boards] == ["completed", "completed"]
    assert report.totals["boards"] == 2
    assert report.totals["completed"] == 2
    assert report.totals["items_new"] == 3
    assert count_posts() == 3

    conn = db.connect()
    try:
        cursors = conn.execute(
            "SELECT collection_key, bootstrap_complete FROM sync_cursors_v2 ORDER BY collection_key"
        ).fetchall()
    finally:
        conn.close()
    assert [(c["collection_key"], c["bootstrap_complete"]) for c in cursors] == [
        ("board:A", 1), ("board:B", 1)
    ]


def test_same_pin_in_two_collections_is_not_duplicated(app_db):
    reader = FakeReader(
        boards("board:A", "board:B"),
        {
            ("board:A", None): page("board:A", [999, 1], has_more=False),
            ("board:B", None): page("board:B", [999, 2], has_more=False),
        },
    )
    report = run_boards(reader, sleep=lambda _: None)
    assert report.totals["items_new"] == 3  # 999 first seen once + 1 + 2
    conn = db.connect()
    try:
        assert conn.execute("SELECT COUNT(*) FROM posts WHERE platform='huaban'").fetchone()[0] == 3
        assert conn.execute("SELECT COUNT(*) FROM posts WHERE source_id='999'").fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM post_images pi JOIN posts p ON p.id=pi.post_id "
            "WHERE p.source_id='999'"
        ).fetchone()[0] == 1
    finally:
        conn.close()


def test_same_pin_image_is_deduplicated_by_sha256(app_db):
    # A valid PNG so store._check accepts the local file.
    image_path = app_db / "pin.png"
    Image.new("RGB", (2, 2), (255, 0, 0)).save(image_path, "PNG")
    digest = hashlib.sha256(image_path.read_bytes()).hexdigest()

    def with_local(page_dict):
        page_dict["items"][0]["images"] = [{
            "idx": 1, "url": "https://img.example/same.jpg",
            "local_path": str(image_path), "sha256": digest, "download_state": "ok",
        }]
        return page_dict

    reader = FakeReader(
        boards("board:A", "board:B"),
        {
            ("board:A", None): with_local(page("board:A", [999], has_more=False)),
            ("board:B", None): with_local(page("board:B", [999], has_more=False)),
        },
    )
    run_boards(reader, sleep=lambda _: None)
    conn = db.connect()
    try:
        assert conn.execute("SELECT COUNT(*) FROM posts WHERE source_id='999'").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM images").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM post_images").fetchone()[0] == 1
    finally:
        conn.close()


def test_failed_board_does_not_erase_other_boards_or_its_committed_page(app_db):
    reader = FakeReader(
        boards("board:A", "board:B"),
        {
            ("board:A", None): page("board:A", [1], cursor_out="1", has_more=True),
            ("board:A", "1"): ReaderError("network", "boom"),
            ("board:B", None): page("board:B", [2], has_more=False),
        },
    )
    report = run_boards(reader, sleep=lambda _: None)
    by_key = {b.collection_key: b for b in report.boards}
    assert by_key["board:A"].status == "paused"
    assert by_key["board:A"].pause_reason == "network"
    assert by_key["board:B"].status == "completed"
    assert report.totals["paused"] == 1 and report.totals["completed"] == 1
    conn = db.connect()
    try:
        keys = {r[0] for r in conn.execute("SELECT source_id FROM posts WHERE platform='huaban'")}
        assert keys == {"1", "2"}  # board A page 1 and board B both committed
    finally:
        conn.close()


def test_bootstrap_interrupt_resumes_with_max_cursor(app_db):
    reader = FakeReader(
        boards("board:A"),
        {
            ("board:A", None): page("board:A", [10, 9], cursor_out="9", has_more=True),
            ("board:A", "9"): ReaderError("network", "interrupted"),
        },
    )
    first = run_boards(reader, sleep=lambda _: None)
    assert first.boards[0].status == "paused"
    conn = db.connect()
    try:
        cursor = conn.execute(
            "SELECT cursor_json, bootstrap_complete FROM sync_cursors_v2 WHERE collection_key='board:A'"
        ).fetchone()
    finally:
        conn.close()
    assert cursor["bootstrap_complete"] == 0
    assert '"next_cursor": "9"' in cursor["cursor_json"]

    # Resume: the first call must use max=9, i.e. continue towards older pins.
    reader.pages[("board:A", "9")] = page("board:A", [8], index=2, cursor_in="9", has_more=False)
    reader.calls.clear()
    second = run_boards(reader, sleep=lambda _: None)
    assert second.boards[0].status == "completed"
    assert reader.calls[0] == ("board:A", "9", 2)
    assert count_posts() == 3


def test_incremental_after_bootstrap_starts_at_top_not_at_old_cursor(app_db):
    reader = FakeReader(
        boards("board:A"),
        {("board:A", None): page("board:A", [10, 9], has_more=False)},
    )
    assert run_boards(reader, sleep=lambda _: None).boards[0].status == "completed"

    # A phone adds 11 at the top. If incremental wrongly resumed from cursor 9, it would
    # call cursor='9' and never see 11. Correct behavior: first call cursor=None.
    reader.pages[("board:A", None)] = page("board:A", [11, 10, 9], cursor_out="9", has_more=True)
    reader.pages[("board:A", "9")] = page("board:A", [9], index=2, cursor_in="9", has_more=False)
    reader.calls.clear()
    report = run_boards(reader, sleep=lambda _: None)
    assert report.boards[0].status == "completed"
    assert reader.calls[0] == ("board:A", None, 1)
    assert reader.calls[1] == ("board:A", "9", 2)
    conn = db.connect()
    try:
        keys = {r[0] for r in conn.execute("SELECT source_id FROM posts WHERE platform='huaban'")}
    finally:
        conn.close()
    assert keys == {"11", "10", "9"}


def test_each_board_has_own_cursor(app_db):
    reader = FakeReader(
        boards("board:A", "board:B"),
        {
            ("board:A", None): page("board:A", [1], has_more=False),
            ("board:B", None): page("board:B", [2], has_more=False),
        },
    )
    run_boards(reader, sleep=lambda _: None)
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT collection_key, cursor_json FROM sync_cursors_v2 ORDER BY collection_key"
        ).fetchall()
    finally:
        conn.close()
    assert len(rows) == 2
    assert '"1"' in rows[0]["cursor_json"]
    assert '"2"' in rows[1]["cursor_json"]


def test_full_mode_is_rejected(app_db):
    reader = FakeReader(boards("board:A"), {})
    with pytest.raises(ValueError):
        run_boards(reader, mode="full", sleep=lambda _: None)
    assert reader.calls == []


def test_run_one_exception_does_not_abort_later_boards(app_db):
    reader = FakeReader(boards("board:A", "board:B"), {})

    def fake_run(reader, key, *, mode, sleep):
        if key == "board:A":
            raise RuntimeError("boom")
        return {"id": 2, "status": "completed", "items_new": 4,
                "items_failed": 0, "pages_done": 1, "images_ok": 0, "images_failed": 0}

    report = run_boards(reader, run_one=fake_run, sleep=lambda _: None)
    assert report.boards[0].status == "failed"
    assert report.boards[0].error.startswith("RuntimeError")
    assert report.boards[1].status == "completed"
    assert report.totals["failed"] == 1 and report.totals["items_new"] == 4


def test_max_boards_limit(app_db):
    reader = FakeReader(
        boards("board:A", "board:B"),
        {("board:A", None): page("board:A", [1], has_more=False)},
    )
    report = run_boards(reader, max_boards=1, sleep=lambda _: None)
    assert len(report.boards) == 1
    assert report.max_boards == 1