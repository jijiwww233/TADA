"""Import logic tests. Call the importer directly (no HTTP / event loop)."""
import sqlite3

import pytest

from app import db, importer
from conftest import counts, png


@pytest.fixture
def lib(data_dir):
    db.init_db()
    return data_dir


def run(*files):
    return importer.import_images([importer.Upload(name, data) for name, data in files])


def test_all_tables_created(lib):
    conn = sqlite3.connect(lib / "app.db")
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    assert {"posts", "images", "analyses", "colors", "embeddings",
            "projects", "pins", "sync_runs"} <= names


def test_import_then_reimport_is_deduped(lib):
    red, blue = png("red"), png("blue")

    r1 = run(("a.png", red), ("b.png", blue))
    assert len(r1["added"]) == 2 and not r1["duplicates"] and not r1["failed"]
    assert counts(lib) == (1, 2, 2)

    # same content, different file names -> duplicates, nothing new stored, no new post
    r2 = run(("a-copy.png", red), ("b-copy.png", blue))
    assert not r2["added"] and r2["post_id"] is None
    assert {d["existing_image_id"] for d in r2["duplicates"]} == {a["id"] for a in r1["added"]}
    assert counts(lib) == (1, 2, 2)


def test_duplicate_within_one_batch(lib):
    red = png("red")
    r = run(("x.png", red), ("y.png", png("green")), ("x-again.png", red))
    assert [a["original_name"] for a in r["added"]] == ["x.png", "y.png"]
    assert r["duplicates"][0]["existing_image_id"] == r["added"][0]["id"]
    assert counts(lib) == (1, 2, 2)


def test_mixed_batch_new_and_old(lib):
    red = png("red")
    run(("old.png", red))
    r = run(("old-again.png", red), ("new.png", png("blue")))
    assert [a["original_name"] for a in r["added"]] == ["new.png"]
    assert len(r["duplicates"]) == 1
    assert counts(lib) == (2, 2, 2)


def test_one_drop_is_one_post_in_order(lib):
    run(("1.png", png("red")), ("2.png", png("green")), ("3.png", png("blue")))
    run(("4.png", png("white")))
    images = importer.list_images()
    # newest post first, drop order kept inside a post
    assert [i["original_name"] for i in images] == ["4.png", "1.png", "2.png", "3.png"]
    assert [i["position"] for i in images[1:]] == [0, 1, 2]
    assert all(i["platform"] == "local" for i in images)
    assert images[0]["url"].startswith("/images/") and images[0]["width"] == 8


def test_invalid_files_fail_without_side_effects(lib):
    r = run(("notes.txt", b"hello"), ("empty.png", b""), ("fake.jpg", b"\xff\xd8garbage"))
    assert not r["added"] and len(r["failed"]) == 3
    assert counts(lib) == (0, 0, 0)


def test_db_failure_rolls_back_files(lib, monkeypatch):
    real_connect = db.connect

    class Boom(Exception):
        pass

    def connect_that_fails_on_image_insert():
        conn = real_connect()

        class Wrapped:
            def execute(self, sql, *args):
                if sql.lstrip().startswith("INSERT INTO images"):
                    raise Boom()
                return conn.execute(sql, *args)

            def close(self):
                conn.close()

        return Wrapped()

    monkeypatch.setattr(db, "connect", connect_that_fails_on_image_insert)
    with pytest.raises(Boom):
        run(("a.png", png("red")))
    monkeypatch.setattr(db, "connect", real_connect)
    assert counts(lib) == (0, 0, 0)
