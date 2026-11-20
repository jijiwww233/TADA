"""Widen posts.platform so a link import can mark its source by domain.

The v5 schema only allows local / xhs / huaban / pinterest / zcool.  Links copied from
other sites (dribbble.com, behance.net ...) are imported with the bare domain as the
platform, so the library and the detail page can show where the image came from without
inventing a local import.

SQLite cannot ALTER a CHECK constraint, so posts is rebuilt once.  The rebuild mirrors
``huaban_migration``: foreign keys off outside the transaction, copy, drop, rename,
recreate indexes/triggers, foreign_key_check, then raise user_version.  It is idempotent:
a table that already allows domains is left alone.
"""
from __future__ import annotations

import re
import sqlite3

from .huaban_migration import POSTS_COLUMNS, MigrationError

TARGET_VERSION = 16

# Known platforms keep their short keys; anything else must look like a domain
# (letters/digits/dot/hyphen, contains a dot).
KNOWN_PLATFORMS = ("local", "xhs", "huaban", "pinterest", "zcool")

DOMAIN_CHECK = (
    "length(platform) BETWEEN 3 AND 63"
    " AND platform GLOB '[a-z0-9]*'"
    " AND platform NOT GLOB '*[^a-z0-9.-]*'"
    " AND platform LIKE '%.%'"
)

POSTS_NEW_SQL = """
CREATE TABLE posts__new (
    id               INTEGER PRIMARY KEY,
    platform         TEXT NOT NULL CHECK (platform IN ('local', 'xhs', 'huaban', 'pinterest', 'zcool')
                         OR ({domain_check})),
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
""".format(domain_check=DOMAIN_CHECK)


def posts_allow_domains(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'posts'"
    ).fetchone()
    if row is None:
        raise MigrationError("posts 表不存在")
    return bool(re.search(r"platform\s+GLOB", row[0] or "", re.I))


def _table_columns(conn: sqlite3.Connection) -> set[str]:
    return {row[1] for row in conn.execute("PRAGMA table_info(posts)")}


def _capture_table_objects(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        """SELECT sql FROM sqlite_master
           WHERE tbl_name = 'posts' AND type IN ('index', 'trigger')
             AND sql IS NOT NULL ORDER BY type, name"""
    ).fetchall()
    return [row[0] for row in rows]


def ensure_open_platforms(conn: sqlite3.Connection,
                          target_version: int = TARGET_VERSION) -> bool:
    """Return True when posts was rebuilt, False when already open."""
    if conn.in_transaction:
        raise MigrationError("ensure_open_platforms 不能在已打开的事务中调用")
    if posts_allow_domains(conn):
        current = conn.execute("PRAGMA user_version").fetchone()[0]
        if current < target_version:
            conn.execute(f"PRAGMA user_version = {int(target_version)}")
        return False

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
