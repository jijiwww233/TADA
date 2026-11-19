"""Executable tests for the Huaban posts.platform migration (no models, no app startup)."""
from __future__ import annotations

import sqlite3

import pytest

from app import db
from app.sync.huaban_migration import (
    MigrationError,
    migrate_huaban_platform,
    posts_allows_huaban,
)

TABLES = ("posts", "images", "post_images", "analyses", "colors", "embeddings",
          "projects", "pins", "sync_runs", "sync_items", "sync_cursors")


def legacy_db(path) -> sqlite3.Connection:
    conn = sqlite3.connect(path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(db.SCHEMA)
    for steps in db.MIGRATIONS:
        for sql in steps:
            conn.execute(sql)
    conn.execute(f"PRAGMA user_version = {len(db.MIGRATIONS)}")
    return conn


def seed(conn: sqlite3.Connection) -> None:
    conn.execute(
        """INSERT INTO posts (id, platform, source_id, url, title, description, is_video,
                              fav_state, source_state, miss_streak, last_seen_at,
                              last_seen_run_id, author_name, extra_json)
           VALUES (1, 'local', NULL, NULL, '本地图', 'd', 0, NULL, NULL, NULL, NULL, NULL, NULL, NULL),
                  (2, 'xhs', 'note-1', 'https://xhs/note-1', '收藏', 'desc', 1,
                   'present', 'ok', 0, '2026-11-04', 7, '作者', '{"k":1}')"""
    )
    conn.execute(
        """INSERT INTO images (id, post_id, position, sha256, file_path, width, height, mime,
                               original_name)
           VALUES (10, 1, 0, ?, 'images/a.jpg', 10, 10, 'image/jpeg', 'a.jpg'),
                  (11, 2, 0, ?, 'images/b.jpg', 20, 20, 'image/png', 'b.png')""",
        ("a" * 64, "b" * 64),
    )
    conn.execute(
        """INSERT INTO post_images (post_id, position, image_id, remote_url, download_state)
           VALUES (1, 0, 10, NULL, 'ok'), (2, 0, 11, 'https://remote/b', 'ok')"""
    )
    conn.execute(
        """INSERT INTO analyses (image_id, status, model, description)
           VALUES (10, 'done', 'v', 'a'), (11, 'done', 'v', 'b')"""
    )
    conn.execute("INSERT INTO colors (image_id, rank, hex, ratio) VALUES (10, 0, '#000000', 0.5)")
    conn.execute("INSERT INTO embeddings (image_id, dimension, model, vector) VALUES (10, 'combined_d_v1', 'm', ?)",
                 (b"1234",))
    conn.execute("INSERT INTO projects (id, name, brief) VALUES (5, 'P', 'brief')")
    conn.execute("INSERT INTO pins (project_id, image_id, note, purpose) VALUES (5, 10, 'n', 'layout')")
    conn.execute(
        """INSERT INTO sync_runs (id, platform, status, collection_key, mode)
           VALUES (7, 'xhs', 'completed', 'fav', 'bootstrap')"""
    )
    conn.execute(
        """INSERT INTO sync_items (run_id, platform, work_key, stage, status, error)
           VALUES (7, 'xhs', 'note-1', 'list', 'failed', 'old')"""
    )
    conn.execute(
        """INSERT INTO sync_cursors (platform, collection_key, phase, cursor_json, bootstrap_complete)
           VALUES ('xhs', 'fav', 'bootstrap', '{"next_cursor":"note-1","has_more":false}', 1)"""
    )
    conn.commit()


def dump(conn: sqlite3.Connection, table: str):
    cols = [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]
    order = cols[0]
    rows = conn.execute(f"SELECT {', '.join(cols)} FROM {table} ORDER BY {order}").fetchall()
    return [tuple(row) for row in rows]


def schema_objects(conn: sqlite3.Connection):
    return {
        (row[0], row[1]): row[2]
        for row in conn.execute(
            "SELECT type, name, sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
        )
    }


def test_migration_preserves_data_and_relations(tmp_path):
    conn = legacy_db(tmp_path / "app.db")
    seed(conn)
    assert posts_allows_huaban(conn) is False
    before = {table: dump(conn, table) for table in TABLES}
    before_objs = schema_objects(conn)
    before_fks = {
        table: [tuple(row) for row in conn.execute(f"PRAGMA foreign_key_list({table})")]
        for table in ("images", "post_images", "pins")
    }

    assert migrate_huaban_platform(conn, target_version=5) is True
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 5
    assert posts_allows_huaban(conn) is True

    after = {table: dump(conn, table) for table in TABLES}
    assert after == before  # all rows, including local/xhs posts and child tables
    after_objs = schema_objects(conn)
    assert set(before_objs) <= set(after_objs)
    assert "huaban" in after_objs[("table", "posts")]
    assert [tuple(row) for row in conn.execute("PRAGMA foreign_key_list(images)")] == before_fks["images"]
    assert [tuple(row) for row in conn.execute("PRAGMA foreign_key_list(post_images)")] == before_fks["post_images"]
    assert [tuple(row) for row in conn.execute("PRAGMA foreign_key_list(pins)")] == before_fks["pins"]
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_new_huaban_row_and_illegal_platform(tmp_path):
    conn = legacy_db(tmp_path / "app.db")
    seed(conn)
    migrate_huaban_platform(conn)
    conn.execute(
        "INSERT INTO posts (platform, source_id, url, title) VALUES ('huaban', 'pin-1', 'https://huaban.com/pins/1', 't')"
    )
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM posts WHERE platform='huaban'").fetchone()[0] == 1
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO posts (platform, source_id) VALUES ('weibo', 'x')")
    conn.rollback()


def test_second_call_is_idempotent(tmp_path):
    conn = legacy_db(tmp_path / "app.db")
    seed(conn)
    assert migrate_huaban_platform(conn) is True
    before = {table: dump(conn, table) for table in TABLES}
    assert migrate_huaban_platform(conn) is False
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 5
    assert {table: dump(conn, table) for table in TABLES} == before


def test_failure_rolls_back_table_and_version(tmp_path):
    conn = legacy_db(tmp_path / "app.db")
    seed(conn)
    before = {table: dump(conn, table) for table in TABLES}
    conn.execute("CREATE TABLE posts__new (x INTEGER)")
    with pytest.raises(MigrationError):
        migrate_huaban_platform(conn)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 3
    assert posts_allows_huaban(conn) is False
    assert {table: dump(conn, table) for table in TABLES} == before
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1

    conn.execute("DROP TABLE posts__new")
    assert migrate_huaban_platform(conn) is True
    assert posts_allows_huaban(conn) is True


def test_explicit_indexes_and_triggers_are_recreated(tmp_path):
    conn = legacy_db(tmp_path / "app.db")
    seed(conn)
    conn.execute("CREATE INDEX idx_posts_platform ON posts(platform)")
    conn.execute(
        """CREATE TRIGGER trg_posts_touch AFTER UPDATE ON posts
           BEGIN SELECT 1; END"""
    )
    assert migrate_huaban_platform(conn) is True
    names = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE tbl_name='posts' AND type IN ('index','trigger')"
        )
    }
    assert "idx_posts_platform" in names
    assert "trg_posts_touch" in names


def test_refuses_to_run_inside_transaction(tmp_path):
    conn = legacy_db(tmp_path / "app.db")
    seed(conn)
    conn.execute("BEGIN")
    with pytest.raises(MigrationError):
        migrate_huaban_platform(conn)
    conn.execute("ROLLBACK")
