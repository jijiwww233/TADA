"""Offline tests for the Huaban reader -> sync-reader/1 contract (no browser/network)."""
from __future__ import annotations

import inspect
import json

import pytest

from app.sync.contract import ContractError, parse_page
from app.sync.engine import ReaderError
from app.sync.readers import huaban
from app.sync.readers.huaban import (
    HuabanBrowser,
    HuabanReader,
    board_collection_key,
    boards_path,
    clean,
    guess_ext,
    image_url,
    normalize_pin,
    parse_collection_key,
    pin_url,
    pins_path,
)


def pin(pin_id, *, key=None, title="", raw="", desc="", link="", user="alice",
        board_id=12, media_type="image"):
    return {
        "pin_id": pin_id,
        "board_id": board_id,
        "title": title,
        "raw_text": raw,
        "description": desc,
        "link": link,
        "user": {"username": user},
        "media_type": media_type,
        "file": {"key": key if key is not None else f"key{pin_id}",
                 "url": f"https://signed.example/{pin_id}?auth_key=secret"},
    }


def page(pins, *, has_more=None, next_cursor=None):
    payload = {"pins": pins}
    if has_more is not None:
        payload["has_more"] = has_more
    if next_cursor is not None:
        payload["next_cursor"] = next_cursor
    return payload


class FakeBrowser:
    """Implements the Browser protocol; records calls and never opens a second tab."""

    def __init__(self, *, login=None, board_pages=None, pin_pages=None,
                 downloads=None, download_errors=()):
        self.login = login if login is not None else {"state": "ok", "urlname": "me"}
        self.board_pages = board_pages or {}
        self.pin_pages = pin_pages or {}
        self.downloads = downloads or {}
        self.download_errors = set(download_errors)
        self.calls: list[tuple] = []
        self.tabs = 0
        self.new_tab_calls = 0
        self.download_calls: list[str] = []
        self.opened = False

    def open(self):
        self.opened = True
        self.tabs = 1

    def close(self):
        self.opened = False

    def check_login(self):
        if isinstance(self.login, Exception):
            raise self.login
        return self.login

    def list_boards(self, urlname, cursor, limit):
        self.calls.append(("boards", urlname, cursor, limit))
        return self.board_pages[cursor]

    def list_pins(self, board_id, cursor, limit):
        self.calls.append(("pins", board_id, cursor, limit))
        return self.pin_pages[cursor]

    def download(self, url):
        self.download_calls.append(url)
        if url in self.download_errors:
            raise RuntimeError("download failed")
        return self.downloads.get(url, (b"image-bytes", "image/jpeg"))


def reader(browser, tmp_path, **kwargs):
    return HuabanReader(browser, tmp_path / "staging", sleep=lambda _: None,
                        delay=0, **kwargs)


# ----------------------------- paths / mapping -----------------------------

def test_boards_path_uses_joined_and_urlname():
    path = boards_path("my name", "42", 30)
    assert path.startswith("/v3/my%20name/boards?limit=30")
    assert "max=42" in path
    assert "joined=1" in path
    assert "urlname=my%20name" in path
    from urllib.parse import unquote
    assert "boards:BOARD" in unquote(path)
    assert "page_size" in unquote(path)


def test_pins_path_uses_max_and_fields():
    path = pins_path("987", "123", 50)
    assert path.startswith("/v3/boards/987/pins?limit=50")
    assert "max=123" in path
    from urllib.parse import unquote
    assert "pins:PIN" in unquote(path)
    assert "board:BOARD_DETAIL" in unquote(path)


def test_collection_key_helpers():
    assert board_collection_key(12) == "board:12"
    assert parse_collection_key("board:12") == "12"
    with pytest.raises(ValueError):
        parse_collection_key("12")
    with pytest.raises(ValueError):
        parse_collection_key("board:abc")


def test_normalize_pin_fields_and_canonical_url():
    p = pin(123, title="标题", desc="描述", link="https://source.example/post",
            user="bob", media_type="video")
    meta = normalize_pin(p)
    assert meta["work_key"] == "123"
    assert meta["title"] == "标题" and meta["description"] == "描述"
    assert meta["author_name"] == "bob"
    assert meta["canonical_url"] == "https://huaban.com/pins/123"
    assert meta["media_type"] == "video"
    assert meta["image_url"] == "https://gd-hbimg.huaban.com/key123"
    assert meta["source_url"] == "https://source.example/post"


def test_normalize_pin_rejects_missing_id():
    with pytest.raises(ValueError):
        normalize_pin({"file": {}})


def test_clean_redacts_auth_key():
    assert "secret" not in clean("https://x/?auth_key=secret&x=1")
    assert "auth_key=***" in clean("https://x/?auth_key=secret&x=1")


def test_pin_and_image_helpers():
    assert pin_url(9) == "https://huaban.com/pins/9"
    assert image_url({"file": {"key": "k"}}) == "https://gd-hbimg.huaban.com/k"
    assert image_url({"file": {"url": "https://signed/x"}}) == "https://signed/x"
    assert guess_ext("https://x/a.png") == ".png"
    assert guess_ext("https://x/a", "image/webp") == ".webp"


# ----------------------------- auth -----------------------------

def test_check_auth_ok_sets_urlname(tmp_path):
    browser = FakeBrowser(login={"state": "ok", "urlname": "alice"})
    with reader(browser, tmp_path) as r:
        assert r.check_auth() == "ok"
        assert r.urlname == "alice"
        assert browser.opened is True
    assert browser.opened is False


@pytest.mark.parametrize("state", ["needs_login", "blocked", "rate_limited"])
def test_check_auth_states(tmp_path, state):
    browser = FakeBrowser(login={"state": state})
    with reader(browser, tmp_path) as r:
        assert r.check_auth() == state


def test_blank_or_anomaly_login_is_network_not_empty(tmp_path):
    for login in ({"state": "blank"}, {"state": "anomaly"}, ReaderError("network", "blank page")):
        browser = FakeBrowser(login=login)
        with reader(browser, tmp_path) as r:
            with pytest.raises(ReaderError) as exc:
                r.check_auth()
            assert exc.value.kind == "network"


def test_login_uses_follow_page_not_v3_user():
    source = inspect.getsource(HuabanBrowser.check_login)
    assert "FOLLOW_URL" in source
    assert "/v3/user" not in source


def test_browser_is_always_headed():
    source = inspect.getsource(HuabanBrowser.open)
    assert "headless=False" in source
    assert "visible" in source


# ----------------------------- boards -----------------------------

def board(board_id, title="b", pins=0, private=False):
    return {"board_id": board_id, "title": title, "pin_count": pins,
            "is_private": private, "urlname": "me"}


def test_list_boards_paginates_and_dedupes(tmp_path):
    browser = FakeBrowser(board_pages={
        None: {"boards": [board(2), board(1)], "has_more": True, "next_cursor": "1"},
        "1": {"boards": [board(1), board(0)], "has_more": False},
    })
    with reader(browser, tmp_path) as r:
        assert r.check_auth() == "ok"
        boards = r.list_boards()
    assert [b["board_id"] for b in boards] == ["2", "1", "0"]
    assert boards[0]["collection_key"] == "board:2"
    assert [call[2] for call in browser.calls if call[0] == "boards"] == [None, "1"]


def test_list_boards_requires_login(tmp_path):
    browser = FakeBrowser(login={"state": "needs_login"})
    with reader(browser, tmp_path) as r:
        with pytest.raises(ReaderError) as exc:
            r.list_boards()
    assert exc.value.kind == "needs_login"


def test_list_boards_anomaly_is_network(tmp_path):
    browser = FakeBrowser(board_pages={None: {"oops": []}})
    with reader(browser, tmp_path) as r:
        with pytest.raises(ReaderError) as exc:
            r.list_boards()
    assert exc.value.kind == "network"


def test_list_boards_stuck_pagination_is_network(tmp_path):
    browser = FakeBrowser(board_pages={None: {"boards": [board(1)], "has_more": True}})
    with reader(browser, tmp_path) as r:
        with pytest.raises(ReaderError) as exc:
            r.list_boards()
    assert exc.value.kind == "network"


# ----------------------------- pins / pages -----------------------------

def test_pagination_cursor_and_has_more(tmp_path):
    browser = FakeBrowser(pin_pages={
        None: page([pin(102), pin(101)], has_more=True, next_cursor="101"),
        "101": page([pin(100)], has_more=False),
    })
    with reader(browser, tmp_path) as r:
        first = r.fetch_page("board:12", None, 1)
        second = r.fetch_page("board:12", "101", 2)
    assert first["page"] == {"index": 1, "cursor_in": None, "cursor_out": "101", "has_more": True}
    assert second["page"] == {"index": 2, "cursor_in": "101", "cursor_out": "100", "has_more": False}
    assert [item["work_key"] for item in first["items"]] == ["102", "101"]
    assert [item["work_key"] for item in second["items"]] == ["100"]
    assert parse_page(first).collection_key == "board:12"
    assert parse_page(second).has_more is False


def test_explicit_has_more_false_wins(tmp_path):
    browser = FakeBrowser(pin_pages={None: page([pin(1), pin(2)], has_more=False)})
    with reader(browser, tmp_path) as r:
        out = r.fetch_page("board:12", None, 1)
    assert out["page"]["has_more"] is False


def test_missing_pins_key_is_network_not_empty(tmp_path):
    browser = FakeBrowser(pin_pages={None: {"oops": []}})
    with reader(browser, tmp_path) as r:
        with pytest.raises(ReaderError) as exc:
            r.fetch_page("board:12", None, 1)
    assert exc.value.kind == "network"


def test_empty_pins_with_no_more_is_valid(tmp_path):
    browser = FakeBrowser(pin_pages={None: page([], has_more=False)})
    with reader(browser, tmp_path) as r:
        out = r.fetch_page("board:12", None, 1)
    assert out["items"] == [] and out["failures"] == []
    assert out["page"]["has_more"] is False
    assert out["page"]["cursor_out"] is None
    parse_page(out)


def test_cursor_not_advancing_is_network(tmp_path):
    browser = FakeBrowser(pin_pages={"100": page([pin(101)], has_more=True)})
    with reader(browser, tmp_path) as r:
        with pytest.raises(ReaderError) as exc:
            r.fetch_page("board:12", "100", 2)
    assert exc.value.kind == "network"


def test_missing_pin_id_is_network(tmp_path):
    browser = FakeBrowser(pin_pages={None: page([{"file": {}}], has_more=False)})
    with reader(browser, tmp_path) as r:
        with pytest.raises(ReaderError) as exc:
            r.fetch_page("board:12", None, 1)
    assert exc.value.kind == "network"


# ----------------------------- items / downloads -----------------------------

def test_item_fields_canonical_url_and_source_link(tmp_path):
    p = pin(123, title="标题", desc="描述", link="https://source.example/post",
            user="bob", media_type="video")
    browser = FakeBrowser(pin_pages={None: page([p], has_more=False)})
    with reader(browser, tmp_path) as r:
        out = r.fetch_page("board:12", None, 1)
    item = out["items"][0]
    assert item["work_key"] == "123"
    assert item["canonical_url"] == "https://huaban.com/pins/123"
    assert item["extra"]["source_url"] == "https://source.example/post"
    assert item["author_name"] == "bob"
    assert item["media_type"] == "video"
    assert item["images"][0]["url"] == "https://gd-hbimg.huaban.com/key123"
    assert item["images"][0]["download_state"] == "ok"
    assert len(item["images"][0]["sha256"]) == 64
    parse_page(out)


def test_download_failure_keeps_image_position(tmp_path):
    bad_url = "https://gd-hbimg.huaban.com/bad"
    good_url = "https://gd-hbimg.huaban.com/good"
    browser = FakeBrowser(
        pin_pages={None: page([pin(1, key="bad"), pin(2, key="good")], has_more=False)},
        download_errors={bad_url},
    )
    with reader(browser, tmp_path) as r:
        out = r.fetch_page("board:12", None, 1)
    assert [item["work_key"] for item in out["items"]] == ["1", "2"]
    failed = out["items"][0]["images"][0]
    assert failed == {"idx": 1, "url": bad_url, "download_state": "failed"}
    assert out["items"][1]["images"][0]["download_state"] == "ok"
    assert out["failures"] == [{"work_key": "1", "stage": "image_download",
                                "error": "download failed"}]
    parse_page(out)


def test_no_image_url_is_detail_failure_but_item_kept(tmp_path):
    p = pin(1, key="")
    p["file"] = {}
    browser = FakeBrowser(pin_pages={None: page([p], has_more=False)})
    with reader(browser, tmp_path) as r:
        out = r.fetch_page("board:12", None, 1)
    assert out["items"][0]["images"] == []
    assert out["failures"][0]["stage"] == "detail"
    parse_page(out)


def test_complete_item_skips_download(tmp_path):
    browser = FakeBrowser(pin_pages={None: page([pin(5)], has_more=False)})
    with reader(browser, tmp_path) as r:
        out = r.fetch_page("board:12", None, 1)
    # is_complete is set on the second reader call
    browser2 = FakeBrowser(pin_pages={None: page([pin(5)], has_more=False)})
    with reader(browser2, tmp_path, is_complete=lambda key: key == "5") as r:
        out2 = r.fetch_page("board:12", None, 1)
    assert browser2.download_calls == []
    assert out2["items"][0] == {
        "work_key": "5", "author_name": "alice",
        "canonical_url": "https://huaban.com/pins/5",
        "media_type": "unknown", "images": [],
    }
    parse_page(out2)


# ----------------------------- stability / mobile / tabs -----------------------------

def test_repeated_output_is_stable(tmp_path):
    browser = FakeBrowser(pin_pages={None: page([pin(1), pin(1), pin(2)], has_more=False)})
    with reader(browser, tmp_path) as r:
        first = r.fetch_page("board:12", None, 1)
        second = r.fetch_page("board:12", None, 1)
    assert first == second
    assert [item["work_key"] for item in first["items"]] == ["1", "2"]
    assert first["page"]["cursor_out"] == "2"
    parse_page(first)


def test_mobile_new_pin_appears_on_page_one(tmp_path):
    browser = FakeBrowser(pin_pages={None: page([pin(101), pin(100)], has_more=False)})
    with reader(browser, tmp_path) as r:
        old = r.fetch_page("board:12", None, 1)
        # a phone adds pin 102 at the top between syncs
        browser.pin_pages[None] = page([pin(102), pin(101), pin(100)], has_more=False)
        new = r.fetch_page("board:12", None, 1)
    assert [item["work_key"] for item in old["items"]] == ["101", "100"]
    assert [item["work_key"] for item in new["items"]] == ["102", "101", "100"]


def test_resuming_cursor_does_not_replay_newer_pins(tmp_path):
    browser = FakeBrowser(pin_pages={"101": page([pin(100)], has_more=False)})
    with reader(browser, tmp_path) as r:
        out = r.fetch_page("board:12", "101", 2)
    assert [item["work_key"] for item in out["items"]] == ["100"]
    assert out["page"]["cursor_in"] == "101"


def test_no_additional_tabs(tmp_path):
    browser = FakeBrowser(pin_pages={
        None: page([pin(2)], has_more=True, next_cursor="2"),
        "2": page([pin(1)], has_more=False),
    })
    with reader(browser, tmp_path) as r:
        r.fetch_page("board:12", None, 1)
        r.fetch_page("board:12", "2", 2)
    assert browser.tabs == 1
    assert browser.new_tab_calls == 0


def test_page_works_with_real_contract_validation(tmp_path):
    browser = FakeBrowser(pin_pages={None: page([pin(1, title="t")], has_more=False)})
    with reader(browser, tmp_path) as r:
        raw = r.fetch_page("board:12", None, 1)
    page_obj = parse_page(raw)
    assert page_obj.platform == "huaban"
    assert page_obj.items[0].work_key == "1"
    assert page_obj.items[0].images[0].idx == 1


def test_parse_collection_key_is_required(tmp_path):
    browser = FakeBrowser()
    with reader(browser, tmp_path) as r:
        with pytest.raises(ValueError):
            r.fetch_page("fav", None, 1)
