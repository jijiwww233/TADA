"""One-off SQLite migration: allow posts.platform='huaban'.

SQLite cannot ALTER a CHECK constraint, so posts must be rebuilt. This helper keeps the
existing columns/data/constraints/indexes and the child foreign keys:
- foreign_keys is turned OFF *outside* the transaction (SQLite ignores the pragma inside),
- BEGIN IMMEDIATE, create posts__new with the wider CHECK, copy rows, drop old, rename,
- recreate explicit indexes/triggers, run PRAGMA foreign_key_check,
- set user_version, COMMIT; on any error ROLLBACK and restore foreign_keys.

The helper is intentionally independent of db.py:
    db.init_db()                       # existing v0..v2 schema
    conn = db.connect()
    try: migrate_huaban_platform(conn, target_version=3)
    finally: conn.close()

Call it outside an open transaction. It is idempotent: if posts already allows huaban,
nothing is rebuilt; user_version is only raised to target_version if needed.
"""
from __future__ import annotations

import re
import sqlite3
from typing import Any

TARGET_VERSION = 5

# Current posts columns (v0 + v1 migrations). Unknown extra columns are rejected rather
# than silently dropped.
POSTS_COLUMNS = (
    "id", "platform", "source_id", "url", "title", "description", "is_video",
    "created_at", "fav_state", "source_state", "miss_streak", "last_seen_at",
    "last_seen_run_id", "author_name", "extra_json",
)

POSTS_NEW_SQL = """
CREATE TABLE posts__new (
    id               INTEGER PRIMARY KEY,
    platform         TEXT NOT NULL CHECK (platform IN ('local', 'xhs', 'huaban', 'pinterest', 'zcool')),
    source_id        TEXT,
    url              TEXT,
    title            TEXT,
    description      TEXT,
    is_video         INTEGER NOT NULL DEFAULT 0,
    created_at       TEXT NOT NULL DEFAULT (datetime('now')),
    fav_state        TEXT CHECK (fav_state IN ('present', 'unverified', 'possibly_removed')),
    source_state     TEXT CHECK (source_state IN ('ok', 'unavailable')),
    miss_streak      INTEGER,
    last_seen_at     TEXT,
    last_seen_run_id INTEGER,
    author_name      TEXT,
    extra_json       TEXT,
    UNIQUE (platform, source_id)
)
"""


class MigrationError(RuntimeError):
    pass


def posts_allows_huaban(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'posts'"
    ).fetchone()
    if row is None:
        raise MigrationError("posts 表不存在")
    sql = row[0] or ""
    # v5 requires all declared target platforms, not only the first one that was added.
    return bool(re.search(r"platform\s+IN\s*\([^)]*'huaban'[^)]*'pinterest'[^)]*'zcool'", sql, re.I))


def _table_columns(conn: sqlite3.Connection) -> set[str]:
    return {row[1] for row in conn.execute("PRAGMA table_info(posts)")}


def _capture_table_objects(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        """SELECT sql FROM sqlite_master
           WHERE tbl_name = 'posts' AND type IN ('index', 'trigger')
             AND sql IS NOT NULL ORDER BY type, name"""
    ).fetchall()
    return [row[0] for row in rows]


def migrate_huaban_platform(conn: sqlite3.Connection, *,
                            target_version: int = TARGET_VERSION) -> bool:
    """Return True if posts was rebuilt; False if already migrated/idempotent no-op."""
    if conn.in_transaction:
        raise MigrationError("migrate_huaban_platform 不能在已打开的事务中调用")
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    if posts_allows_huaban(conn):
        if current < target_version:
            conn.execute(f"PRAGMA user_version = {int(target_version)}")
        return False
    if current >= target_version:
        raise MigrationError(
            f"user_version={current} 已到 {target_version}，但 posts.platform 仍不支持 huaban"
        )
    if current < 2:
        raise MigrationError(f"期望 v2 schema（posts 已含同步列），当前 user_version={current}")

    columns = _table_columns(conn)
    missing = set(POSTS_COLUMNS) - columns
    if missing:
        raise MigrationError("posts 缺少列: " + ", ".join(sorted(missing)))
    extra = columns - set(POSTS_COLUMNS)
    if extra:
        raise MigrationError("posts 有未知列，拒绝重建以免丢数据: " + ", ".join(sorted(extra)))
    if conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'posts__new'").fetchone():
        raise MigrationError("posts__new 已存在，无法安全重建")

    object_sql = _capture_table_objects(conn)
    fk_on = bool(conn.execute("PRAGMA foreign_keys").fetchone()[0])
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute(POSTS_NEW_SQL)
            cols = ", ".join(POSTS_COLUMNS)
            conn.execute(f"INSERT INTO posts__new ({cols}) SELECT {cols} FROM posts")
            conn.execute("DROP TABLE posts")
            conn.execute("ALTER TABLE posts__new RENAME TO posts")
            for sql in object_sql:
                conn.execute(sql)
            violations = conn.execute("PRAGMA foreign_key_check").fetchall()
            if violations:
                raise MigrationError(f"重建后外键检查失败: {violations[:5]}")
            conn.execute(f"PRAGMA user_version = {int(target_version)}")
            conn.execute("COMMIT")
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
    finally:
        conn.execute("PRAGMA foreign_keys = ON" if fk_on else "OFF")
    return True


# Alias that reads better at an application startup call site.
ensure_huaban_platform = migrate_huaban_platform
