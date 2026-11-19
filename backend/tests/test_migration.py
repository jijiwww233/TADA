"""Schema migration v0 -> v1 on a library that already holds local imports."""
import sqlite3

import pytest

from app import db, importer
from conftest import counts, png


def make_v0_library(data_dir):
    """Build a v0 database (baseline schema only) with two local drops, like day 1."""
    (data_dir / "images").mkdir(parents=True, exist_ok=True)
    conn = db.connect()
    conn.executescript(db.SCHEMA)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 0
    for post_id, colors in ((1, ["red", "green"]), (2, ["blue"])):
        conn.execute("INSERT INTO posts (id, platform) VALUES (?, 'local')", (post_id,))
        for pos, color in enumerate(colors):
            sha = f"{post_id}{pos}".ljust(64, "0")
            rel = f"images/{sha}.png"
            (data_dir / rel).write_bytes(png(color))
            conn.execute(
                """INSERT INTO images (post_id, position, sha256, file_path, width, height,
                                       mime, original_name)
                   VALUES (?, ?, ?, ?, 8, 8, 'image/png', ?)""",
                (post_id, pos, sha, rel, f"{color}.png"),
            )
    conn.close()


def columns(table):
    conn = db.connect()
    try:
        return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
    finally:
        conn.close()


def test_migrate_existing_library_keeps_data(data_dir):
    make_v0_library(data_dir)
    before = importer.list_images()
    assert counts(data_dir) == (2, 3, 3)

    db.init_db()

    conn = db.connect()
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    # post_images backfilled 1:1 from images(post_id, position)
    assert [tuple(r) for r in conn.execute(
        "SELECT post_id, position, image_id, download_state FROM post_images ORDER BY 1, 2")] == \
        [tuple(r) + ("ok",) for r in conn.execute(
            "SELECT post_id, position, id FROM images ORDER BY 1, 2")]
    # every image queued for analysis exactly once
    assert tuple(conn.execute("SELECT COUNT(*), COUNT(DISTINCT image_id) FROM analyses").fetchone()) == (3, 3)
    # local posts get no sync semantics
    assert [tuple(r) for r in conn.execute(
        "SELECT fav_state, source_state, miss_streak, last_seen_at FROM posts")] == [(None,) * 4] * 2
    conn.close()

    assert importer.list_images() == before
    assert counts(data_dir) == (2, 3, 3)
    assert len(list(data_dir.glob("app.db.bak-*-v0"))) == 1
    assert {"fav_state", "source_state", "miss_streak", "last_seen_run_id"} <= columns("posts")
    assert {"mode", "pause_reason", "full_scan", "pages_done"} <= columns("sync_runs")


def test_dedupe_still_works_after_migration(data_dir):
    db.init_db()
    r1 = importer.import_images([importer.Upload("a.png", png("red"))])
    db.init_db()  # second start: no-op
    r2 = importer.import_images([importer.Upload("a-copy.png", png("red")),
                                 importer.Upload("b.png", png("blue"))])
    assert [d["existing_image_id"] for d in r2["duplicates"]] == [r1["added"][0]["id"]]
    assert counts(data_dir) == (2, 2, 2)
    assert [i["position"] for i in importer.list_post_images(r2["post_id"])] == [0]


def test_init_db_is_idempotent_and_fresh_db_has_no_backup(data_dir):
    db.init_db()
    db.init_db()
    assert not list(data_dir.glob("app.db.bak-*"))
    conn = db.connect()
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    conn.close()


def test_failed_migration_rolls_back(data_dir, monkeypatch):
    make_v0_library(data_dir)
    monkeypatch.setattr(db, "MIGRATIONS", [db.MIGRATIONS[0] + ["THIS IS NOT SQL"]])
    monkeypatch.setattr(db, "SCHEMA_VERSION", 1)
    with pytest.raises(sqlite3.OperationalError):
        db.init_db()
    conn = db.connect()
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 0
    assert "post_images" not in {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'")}
    conn.close()
    assert "fav_state" not in columns("posts")
    assert counts(data_dir) == (2, 3, 3)


# --- v8 -> v9 (merged core polish: pip scenes + reference/feedback tables) ---

NEW_V9_TABLES = (
    "project_pip_scenes",
    "reference_manual_decisions",
    "reference_manual_events",
    "reference_local_features",
    "project_recommendation_feedback",
)


def make_v8_library(data_dir):
    """A v8-shaped library with real rows, missing every table added by the merged branches."""
    db.init_db()
    (data_dir / "images").mkdir(parents=True, exist_ok=True)
    conn = db.connect()
    try:
        for index, color in enumerate(("red", "blue"), start=1):
            sha = f"v8{index}".ljust(64, "0")
            rel = f"images/{sha}.png"
            (data_dir / rel).write_bytes(png(color))
            conn.execute("INSERT INTO posts (id, platform) VALUES (?, 'local')", (index,))
            conn.execute(
                """INSERT INTO images (post_id, position, sha256, file_path, width, height, mime, original_name)
                   VALUES (?, 0, ?, ?, 8, 8, 'image/png', ?)""",
                (index, sha, rel, f"{color}.png"),
            )
        conn.execute("INSERT INTO projects (id, name, brief) VALUES (1, '迁移前项目', 'v8')")
        conn.execute("INSERT INTO project_pip_scenes (project_id, revision, scene_json) VALUES (1, 3, '{}')")
        for table in NEW_V9_TABLES:
            conn.execute(f"DROP TABLE IF EXISTS {table}")
        conn.execute("PRAGMA user_version = 8")
    finally:
        conn.close()


def test_v8_library_upgrades_to_current_version_and_keeps_data(data_dir):
    make_v8_library(data_dir)
    assert counts(data_dir) == (2, 2, 2)

    db.init_db()

    conn = db.connect()
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert set(NEW_V9_TABLES) <= tables
        assert conn.execute("SELECT COUNT(*) FROM project_pip_scenes").fetchone()[0] == 0
        assert [tuple(r) for r in conn.execute("SELECT id, post_id FROM images ORDER BY id")] == [(1, 1), (2, 2)]
        assert conn.execute("SELECT name FROM projects WHERE id=1").fetchone()[0] == "迁移前项目"
        assert {"before_reference", "after_reference"} <= columns("reference_manual_events")
    finally:
        conn.close()
    assert counts(data_dir) == (2, 2, 2)
    assert len(list(data_dir.glob("app.db.bak-*-v8"))) == 1


def test_current_library_reopen_is_noop_and_keeps_data(data_dir):
    db.init_db()
    conn = db.connect()
    try:
        conn.execute("INSERT INTO posts (id, platform) VALUES (1, 'local')")
        conn.execute("INSERT INTO images (post_id, position, sha256, file_path, width, height, mime, original_name) "
                     "VALUES (1, 0, ?, 'images/x.png', 8, 8, 'image/png', 'x.png')", ("a" * 64,))
        conn.execute("INSERT INTO projects (id, name, brief) VALUES (1, 'v9 项目', '')")
        conn.execute("INSERT INTO project_pip_scenes (project_id, revision, scene_json) "
                     "VALUES (1, 7, '{\"version\":1,\"items\":[],\"notes\":[]}')")
    finally:
        conn.close()
    before = counts(data_dir)

    db.init_db()  # reopening an already-v9 library must not migrate, downgrade or lose rows

    conn = db.connect()
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
        assert conn.execute("SELECT revision FROM project_pip_scenes WHERE project_id=1").fetchone()[0] == 7
    finally:
        conn.close()
    assert counts(data_dir) == before
    assert not list(data_dir.glob("app.db.bak-*"))
