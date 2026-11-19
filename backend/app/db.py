"""SQLite connection, v0 baseline schema and migrations.
Schema changes must be recorded in docs/progress.md."""
import json
import sqlite3
from datetime import datetime
from pathlib import Path

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS reference_manual_decisions (
 image_id INTEGER PRIMARY KEY REFERENCES images(id), is_reference INTEGER CHECK(is_reference IN (0,1))
);
CREATE TABLE IF NOT EXISTS reference_manual_events (
 id INTEGER PRIMARY KEY, operation TEXT NOT NULL, image_id INTEGER NOT NULL REFERENCES images(id),
 before_category TEXT, after_category TEXT, before_confirmed INTEGER, after_confirmed INTEGER,
 before_reference INTEGER, after_reference INTEGER,
 created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_reference_events_image ON reference_manual_events(image_id,id);
CREATE TABLE IF NOT EXISTS reference_local_features (
 image_id INTEGER PRIMARY KEY REFERENCES images(id),sha256 TEXT NOT NULL,features_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS project_recommendation_feedback (
 project_id INTEGER NOT NULL REFERENCES projects(id), image_id INTEGER NOT NULL REFERENCES images(id),
 purpose TEXT NOT NULL CHECK(purpose IN ('layout','palette','style')), usable INTEGER NOT NULL CHECK(usable IN (0,1)),
 updated_at TEXT NOT NULL DEFAULT (datetime('now')), PRIMARY KEY(project_id,image_id,purpose)
);

CREATE TABLE IF NOT EXISTS image_reference_labels (
 image_id INTEGER PRIMARY KEY REFERENCES images(id),
 auto_category TEXT CHECK(auto_category IN ('visual_ref','text_card','life','uncertain')),
 manual_category TEXT CHECK(manual_category IN ('visual_ref','text_card','life','uncertain')),
 confirmed INTEGER NOT NULL DEFAULT 0, reason TEXT, source TEXT, input_hash TEXT, version TEXT
);

CREATE TABLE IF NOT EXISTS posts (
    id          INTEGER PRIMARY KEY,
    platform    TEXT NOT NULL CHECK (platform IN ('local', 'xhs')),
    source_id   TEXT,                 -- note id on the platform; NULL for local
    url         TEXT,                 -- original post link
    title       TEXT,
    description TEXT,
    is_video    INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (platform, source_id)
);

CREATE TABLE IF NOT EXISTS images (
    id            INTEGER PRIMARY KEY,
    post_id       INTEGER NOT NULL REFERENCES posts(id),
    position      INTEGER NOT NULL,   -- order inside the post, from 0
    sha256        TEXT NOT NULL UNIQUE,
    file_path     TEXT NOT NULL,      -- relative to data dir, e.g. images/<sha256>.jpg
    width         INTEGER,
    height        INTEGER,
    mime          TEXT,
    original_name TEXT,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_images_post ON images(post_id, position);

CREATE TABLE IF NOT EXISTS analyses (
    id                INTEGER PRIMARY KEY,
    image_id          INTEGER NOT NULL UNIQUE REFERENCES images(id),
    status            TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'done', 'failed')),
    model             TEXT,
    visible_text      TEXT,
    subject           TEXT,           -- JSON array
    layout_json       TEXT,
    style_json        TEXT,
    description       TEXT,
    title             TEXT,           -- 短标题（v14）：≤12 字、完整短语，见 app/titles.py
    raw_json          TEXT,           -- full model output
    prompt_tokens     INTEGER,
    completion_tokens INTEGER,
    error             TEXT,
    attempts          INTEGER NOT NULL DEFAULT 0,
    created_at        TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at        TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS colors (
    id       INTEGER PRIMARY KEY,
    image_id INTEGER NOT NULL REFERENCES images(id),
    rank     INTEGER NOT NULL,        -- 0 = most dominant
    hex      TEXT NOT NULL,
    ratio    REAL NOT NULL,           -- 0..1
    UNIQUE (image_id, rank)
);

CREATE TABLE IF NOT EXISTS embeddings (
    id        INTEGER PRIMARY KEY,
    image_id  INTEGER NOT NULL REFERENCES images(id),
    dimension TEXT NOT NULL,          -- e.g. layout / style / subject / text
    model     TEXT,
    vector    BLOB NOT NULL,
    UNIQUE (image_id, dimension)
);

CREATE TABLE IF NOT EXISTS projects (
    id         INTEGER PRIMARY KEY,
    name       TEXT NOT NULL,
    brief      TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS pins (
    id         INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    image_id   INTEGER NOT NULL REFERENCES images(id),
    note       TEXT,
    purpose    TEXT,                  -- what this pin is referenced for (layout / color / style ...)
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (project_id, image_id)
);

CREATE TABLE IF NOT EXISTS project_pin_order (
    pin_id INTEGER PRIMARY KEY REFERENCES pins(id) ON DELETE CASCADE,
    position INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS sync_runs (
    id          INTEGER PRIMARY KEY,
    platform    TEXT NOT NULL,
    started_at  TEXT NOT NULL DEFAULT (datetime('now')),
    finished_at TEXT,
    status      TEXT NOT NULL DEFAULT 'running',
    items_found INTEGER,
    items_new   INTEGER,
    error       TEXT
);
"""


# Migrations, applied in order by PRAGMA user_version. Only add columns / tables;
# never drop or rebuild a table that holds user data.
MIGRATIONS: list[list[str]] = [
    # v1: sync contract (see docs/progress.md, 2026-11-04 同步契约)
    [
        # Sync-only post state. NULL for platform=local: local posts are not favorites.
        """ALTER TABLE posts ADD COLUMN fav_state TEXT
           CHECK (fav_state IN ('present', 'unverified', 'possibly_removed'))""",
        """ALTER TABLE posts ADD COLUMN source_state TEXT
           CHECK (source_state IN ('ok', 'unavailable'))""",
        "ALTER TABLE posts ADD COLUMN miss_streak INTEGER",  # consecutive clean full scans missing it
        "ALTER TABLE posts ADD COLUMN last_seen_at TEXT",
        "ALTER TABLE posts ADD COLUMN last_seen_run_id INTEGER",
        "ALTER TABLE posts ADD COLUMN author_name TEXT",
        "ALTER TABLE posts ADD COLUMN extra_json TEXT",   # platform-private fields, stored as-is

        # Post composition and order. images.post_id/position stay as "first source".
        """CREATE TABLE post_images (
               post_id        INTEGER NOT NULL REFERENCES posts(id),
               position       INTEGER NOT NULL CHECK (position >= 0),
               image_id       INTEGER REFERENCES images(id),   -- NULL until bytes are saved
               remote_url     TEXT,
               download_state TEXT NOT NULL DEFAULT 'ok'
                              CHECK (download_state IN ('ok', 'pending', 'failed', 'remote_only')),
               PRIMARY KEY (post_id, position),
               CHECK ((image_id IS NOT NULL) = (download_state = 'ok'))
           )""",
        "CREATE INDEX idx_post_images_image ON post_images(image_id)",
        """INSERT INTO post_images (post_id, position, image_id, download_state)
           SELECT post_id, position, id, 'ok' FROM images""",

        "ALTER TABLE sync_runs ADD COLUMN collection_key TEXT",
        "ALTER TABLE sync_runs ADD COLUMN mode TEXT",            # bootstrap / incremental / full
        "ALTER TABLE sync_runs ADD COLUMN pause_reason TEXT",
        "ALTER TABLE sync_runs ADD COLUMN full_scan INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE sync_runs ADD COLUMN pages_done INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE sync_runs ADD COLUMN items_failed INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE sync_runs ADD COLUMN images_ok INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE sync_runs ADD COLUMN images_failed INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE sync_runs ADD COLUMN updated_at TEXT",

        """CREATE TABLE sync_cursors (
               platform           TEXT NOT NULL,
               collection_key     TEXT NOT NULL,
               phase              TEXT NOT NULL CHECK (phase IN ('bootstrap', 'incremental', 'full')),
               cursor_json        TEXT NOT NULL DEFAULT '{}',
               bootstrap_complete INTEGER NOT NULL DEFAULT 0,
               updated_at         TEXT NOT NULL DEFAULT (datetime('now')),
               PRIMARY KEY (platform, collection_key)
           )""",
        # Per-item failures, persisted before the page cursor moves on.
        """CREATE TABLE sync_items (
               id            INTEGER PRIMARY KEY,
               run_id        INTEGER NOT NULL REFERENCES sync_runs(id),
               platform      TEXT NOT NULL,
               work_key      TEXT NOT NULL,
               stage         TEXT NOT NULL,    -- list / detail / image_download / store
               status        TEXT NOT NULL CHECK (status IN ('failed', 'resolved')),
               error         TEXT,
               created_at    TEXT NOT NULL DEFAULT (datetime('now')),
               updated_at    TEXT NOT NULL DEFAULT (datetime('now'))
           )""",
        "CREATE INDEX idx_sync_items_work ON sync_items(platform, work_key, status)",

        # Invariant from now on: every image has an analyses row.
        "INSERT OR IGNORE INTO analyses (image_id) SELECT id FROM images",
    ],
    # v2: atomic analysis claim across executors (worker / CLI). status stays
    # pending while claimed; only the claimer may call the model and write the result.
    [
        "ALTER TABLE analyses ADD COLUMN claimed_by TEXT",
        "ALTER TABLE analyses ADD COLUMN claimed_at INTEGER",  # Unix seconds (UTC), lease start
    ],
    # v3: one combined_d_v1 vector per image in the existing embeddings table
    # (dimension = text scheme, model = embedding model). Old rows keep NULL = stale.
    [
        "ALTER TABLE embeddings ADD COLUMN dimensions INTEGER",  # actual vector length, e.g. 1024
        "ALTER TABLE embeddings ADD COLUMN text_hash TEXT",      # sha256(scheme + "\n" + text)
    ],
]
SCHEMA_VERSION = 14  # v12 watched import folders, v13 project rediscovery, v14 short titles


def connect() -> sqlite3.Connection:
    # isolation_level=None: we manage transactions explicitly (BEGIN IMMEDIATE ... COMMIT)
    conn = sqlite3.connect(config.db_path(), isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _backup(conn: sqlite3.Connection, version: int) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = config.db_path().with_name(f"app.db.bak-{stamp}-v{version}")
    dest = sqlite3.connect(path)
    try:
        conn.backup(dest)
    finally:
        dest.close()
    return path


def migrate(conn: sqlite3.Connection) -> None:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version >= SCHEMA_VERSION:
        return
    has_data = conn.execute("SELECT EXISTS (SELECT 1 FROM posts)").fetchone()[0]
    if has_data:
        _backup(conn, version)
    if version == 12:
        _ensure_watch_folder_options(conn)
        _ensure_vibe_schema(conn)
        return
    # v1..v3 are ordinary additive SQL migrations.
    if version < 3:
        conn.execute("BEGIN IMMEDIATE")
        try:
            for steps in MIGRATIONS[version:3]:
                for sql in steps:
                    conn.execute(sql)
            conn.execute("PRAGMA user_version = 3")
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        version = 3
    # v4 rebuilds posts to widen the platform CHECK. It must manage its own
    # transaction because PRAGMA foreign_keys cannot be changed inside one.
    from .sync.huaban_migration import migrate_huaban_platform
    migrate_huaban_platform(conn, target_version=5)
    _ensure_classification_schema(conn)
    _ensure_account_schema(conn)
    _ensure_collection_schema(conn)
    _ensure_pip_scene_schema(conn)
    _ensure_annotation_schema(conn)
    _ensure_annotation_field_schema(conn)
    _ensure_watch_folder_schema(conn)
    _ensure_vibe_schema(conn)
    _ensure_title_schema(conn)


def _ensure_classification_schema(conn: sqlite3.Connection) -> None:
    """Renumbered classification candidate: v6, after platforms v5."""
    names = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    if {"post_classifications", "post_classification_manual"} <= names:
        if conn.execute("PRAGMA user_version").fetchone()[0] < 6:
            conn.execute("PRAGMA user_version = 6")
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute("""CREATE TABLE IF NOT EXISTS post_classifications (
               post_id             INTEGER PRIMARY KEY REFERENCES posts(id) ON DELETE CASCADE,
               category            TEXT NOT NULL CHECK (category IN ('design_reference', 'art_reference', 'life', 'uncertain')),
               confidence          REAL NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
               status              TEXT NOT NULL CHECK (status IN ('ok', 'backend_error')),
               evidence_json       TEXT NOT NULL DEFAULT '[]',
               ignored_evidence_json TEXT NOT NULL DEFAULT '[]',
               reasons_json        TEXT NOT NULL DEFAULT '[]',
               classifier_version  TEXT NOT NULL,
               input_hash          TEXT NOT NULL,
               scope               TEXT,
               computed_at         TEXT NOT NULL DEFAULT (datetime('now')),
               updated_at          TEXT NOT NULL DEFAULT (datetime('now'))
           )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS post_classification_manual (
               post_id    INTEGER PRIMARY KEY REFERENCES posts(id) ON DELETE CASCADE,
               category   TEXT NOT NULL CHECK (category IN ('design_reference', 'art_reference', 'life', 'uncertain')),
               note       TEXT,
               updated_at TEXT NOT NULL DEFAULT (datetime('now'))
           )""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_post_classifications_category ON post_classifications(category, status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_post_classification_manual_category ON post_classification_manual(category)")
        conn.execute("PRAGMA user_version = 6")
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def _ensure_account_schema(conn: sqlite3.Connection) -> None:
    """v7: favorites belong to an account; cursors and runs are account-scoped.

    The old sync_cursors rows are copied with account_key='' (ownership unknown) and are
    kept for audit; known accounts are seeded explicitly by scripts/seed_account_ownership.py.
    """
    if conn.execute("PRAGMA user_version").fetchone()[0] >= 7:
        return
    names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.execute("BEGIN IMMEDIATE")
    try:
        if "post_favorites" not in names:
            conn.execute("""CREATE TABLE post_favorites (
                   account_key       TEXT NOT NULL,
                   post_id           INTEGER NOT NULL REFERENCES posts(id),
                   first_seen_at     TEXT NOT NULL DEFAULT (datetime('now')),
                   first_seen_run_id INTEGER,
                   last_seen_at      TEXT,
                   last_seen_run_id  INTEGER,
                   PRIMARY KEY (account_key, post_id)
               )""")
            conn.execute("CREATE INDEX idx_post_favorites_post ON post_favorites(post_id)")
        if "sync_cursors_v2" not in names:
            conn.execute("""CREATE TABLE sync_cursors_v2 (
                   platform           TEXT NOT NULL,
                   collection_key     TEXT NOT NULL,
                   account_key        TEXT NOT NULL,
                   phase              TEXT NOT NULL,
                   cursor_json        TEXT NOT NULL DEFAULT '{}',
                   bootstrap_complete INTEGER NOT NULL DEFAULT 0,
                   updated_at         TEXT NOT NULL DEFAULT (datetime('now')),
                   PRIMARY KEY (platform, collection_key, account_key)
               )""")
            conn.execute("""INSERT INTO sync_cursors_v2
                   (platform, collection_key, account_key, phase, cursor_json,
                    bootstrap_complete, updated_at)
                   SELECT platform, collection_key, '', phase, cursor_json,
                          bootstrap_complete, updated_at FROM sync_cursors""")
        run_cols = {row[1] for row in conn.execute("PRAGMA table_info(sync_runs)")}
        if "account_key" not in run_cols:
            conn.execute("ALTER TABLE sync_runs ADD COLUMN account_key TEXT")
        item_cols = {row[1] for row in conn.execute("PRAGMA table_info(sync_items)")}
        if "account_key" not in item_cols:
            conn.execute("ALTER TABLE sync_items ADD COLUMN account_key TEXT")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_sync_items_account ON sync_items(platform, account_key, work_key, status)")
        conn.execute("PRAGMA user_version = 7")
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def init_db() -> None:
    config.images_dir().mkdir(parents=True, exist_ok=True)
    conn = connect()
    try:
        conn.executescript(SCHEMA)  # v0 baseline, all IF NOT EXISTS
        migrate(conn)
        event_columns={r[1] for r in conn.execute('PRAGMA table_info(reference_manual_events)')}
        for column in ('before_reference','after_reference'):
            if column not in event_columns:
                conn.execute(f'ALTER TABLE reference_manual_events ADD COLUMN {column} INTEGER')
    finally:
        conn.close()


def _ensure_collection_schema(conn):
    """v8 additive: named collections and many-to-many membership, no existing row rewrite."""
    conn.execute('BEGIN IMMEDIATE')
    try:
        conn.execute("""CREATE TABLE IF NOT EXISTS sync_collections (
            platform TEXT NOT NULL, account_key TEXT NOT NULL DEFAULT '',
            collection_key TEXT NOT NULL, title TEXT, kind TEXT,
            last_seen_run_id INTEGER REFERENCES sync_runs(id),
            PRIMARY KEY(platform,account_key,collection_key))""")
        conn.execute("""CREATE TABLE IF NOT EXISTS post_collections (
            platform TEXT NOT NULL, account_key TEXT NOT NULL DEFAULT '',
            collection_key TEXT NOT NULL, post_id INTEGER NOT NULL REFERENCES posts(id),
            position INTEGER, last_seen_run_id INTEGER REFERENCES sync_runs(id),
            PRIMARY KEY(platform,account_key,collection_key,post_id),
            FOREIGN KEY(platform,account_key,collection_key)
                REFERENCES sync_collections(platform,account_key,collection_key))""")
        conn.execute('PRAGMA user_version=8')
        conn.execute('COMMIT')
    except Exception:
        conn.execute('ROLLBACK'); raise


def _ensure_pip_scene_schema(conn):
    """v9 additive: one persisted PiP canvas scene per project, revision CAS.

    The revision is owned by the store; no existing table is touched.
    """
    if conn.execute("PRAGMA user_version").fetchone()[0] >= 9:
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute("""CREATE TABLE IF NOT EXISTS project_pip_scenes (
            project_id INTEGER PRIMARY KEY REFERENCES projects(id),
            revision   INTEGER NOT NULL,
            scene_json TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )""")
        conn.execute("PRAGMA user_version = 9")
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def _ensure_annotation_schema(conn):
    """v10 additive: user-editable annotation overlay per image.

    Only a new table; analyses, embeddings and their text hashes are untouched, so
    editing a user annotation never invalidates an existing vector.
    """
    if conn.execute("PRAGMA user_version").fetchone()[0] >= 10:
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute("""CREATE TABLE IF NOT EXISTS image_annotations (
            image_id    INTEGER PRIMARY KEY REFERENCES images(id),
            description TEXT,
            tags_json   TEXT,
            edited_at   TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at  TEXT NOT NULL DEFAULT (datetime('now')),
            CHECK (description IS NOT NULL OR tags_json IS NOT NULL)
        )""")
        conn.execute("PRAGMA user_version = 10")
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def _ensure_watch_folder_schema(conn):
    """v12 additive: folders watched for automatic local import.

    Only new tables; images/posts/analyses keep their existing shape. A watched import
    writes the normal local post/image rows, so the library and analysis queue need no
    change. Counters live here so the UI can show them after a restart.
    """
    if conn.execute("PRAGMA user_version").fetchone()[0] >= 12:
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute("""CREATE TABLE IF NOT EXISTS watch_folders (
            id             INTEGER PRIMARY KEY,
            path           TEXT NOT NULL UNIQUE,
            enabled        INTEGER NOT NULL DEFAULT 1,
            include_subfolders INTEGER NOT NULL DEFAULT 1,
            created_at     TEXT NOT NULL DEFAULT (datetime('now')),
            last_import_at TEXT,
            imported_count INTEGER NOT NULL DEFAULT 0,
            last_error     TEXT)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS watch_files (
            folder_id   INTEGER NOT NULL REFERENCES watch_folders(id) ON DELETE CASCADE,
            rel_path    TEXT NOT NULL,
            size        INTEGER NOT NULL,
            mtime       INTEGER NOT NULL,
            image_id    INTEGER REFERENCES images(id),
            imported_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY(folder_id, rel_path))""")
        conn.execute("""CREATE TABLE IF NOT EXISTS watch_deferred (
            image_id   INTEGER PRIMARY KEY REFERENCES images(id),
            manual     INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now')))""")
        conn.execute("""CREATE TABLE IF NOT EXISTS watch_analysis_log (
            image_id   INTEGER PRIMARY KEY REFERENCES images(id),
            day        TEXT NOT NULL,
            auto       INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT (datetime('now')))""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_watch_log_day ON watch_analysis_log(day, auto)")
        conn.execute("""CREATE TABLE IF NOT EXISTS watch_settings (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL)""")
        conn.execute("PRAGMA user_version = 12")
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def _ensure_watch_folder_options(conn):
    """Extend v12 libraries created by the first watched-folder preview."""
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(watch_folders)")}
    if columns and "include_subfolders" not in columns:
        conn.execute("BEGIN IMMEDIATE")
        try:
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(watch_folders)")}
            if "include_subfolders" not in columns:
                conn.execute("ALTER TABLE watch_folders ADD COLUMN include_subfolders INTEGER NOT NULL DEFAULT 1")
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise


def _ensure_vibe_schema(conn):
    """v13 additive: daily project batches and rejected images."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute("""CREATE TABLE IF NOT EXISTS vibe_rejected (
            project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            image_id INTEGER NOT NULL REFERENCES images(id) ON DELETE CASCADE,
            PRIMARY KEY (project_id,image_id))""")
        conn.execute("""CREATE TABLE IF NOT EXISTS vibe_batches (
            project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            day TEXT NOT NULL,
            position INTEGER NOT NULL,
            image_id INTEGER NOT NULL REFERENCES images(id) ON DELETE CASCADE,
            PRIMARY KEY (project_id,day,position),
            UNIQUE (project_id,day,image_id))""")
        conn.execute("PRAGMA user_version = 13")
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def _ensure_title_schema(conn):
    """v14 additive: a short title per image, plus the user's own title on top.

    analyses.title holds the generated title (vision answer or the text-model backfill);
    image_titles_manual holds a title the user typed, which no automatic writer ever
    touches — same rule as post_classification_manual. Reads resolve manual first.
    """
    if conn.execute("PRAGMA user_version").fetchone()[0] >= 14:
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(analyses)")}
        if "title" not in columns:
            conn.execute("ALTER TABLE analyses ADD COLUMN title TEXT")
        conn.execute("""CREATE TABLE IF NOT EXISTS image_titles_manual (
            image_id   INTEGER PRIMARY KEY REFERENCES images(id),
            title      TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )""")
        conn.execute("PRAGMA user_version = 14")
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


_INVALID_JSON = object()


def _decode_json(raw):
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return _INVALID_JSON


def _ensure_annotation_field_schema(conn):
    """v11 data clean (structure unchanged): the old overlay copied AI text into fields
    the user never edited. Rows whose description/tags now equal the current analysis
    are not user edits, so NULL those fields; drop rows that become fully empty.
    """
    if conn.execute("PRAGMA user_version").fetchone()[0] >= 11:
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        rows = conn.execute(
            """SELECT a.image_id, a.description, a.tags_json,
                      an.description AS ai_description, an.subject AS ai_subject
               FROM image_annotations a
               LEFT JOIN analyses an ON an.image_id = a.image_id""").fetchall()
        for row in rows:
            description, tags_json = row["description"], row["tags_json"]
            if (description is not None and row["ai_description"] is not None
                    and description == row["ai_description"]):
                description = None
            if tags_json is not None:
                tags = _decode_json(tags_json)
                subject = _decode_json(row["ai_subject"]) if row["ai_subject"] is not None else None
                if (isinstance(tags, list) and isinstance(subject, list)
                        and tags == subject):
                    tags_json = None
            if description is None and tags_json is None:
                conn.execute("DELETE FROM image_annotations WHERE image_id = ?",
                             (row["image_id"],))
            elif (description, tags_json) != (row["description"], row["tags_json"]):
                conn.execute(
                    "UPDATE image_annotations SET description = ?, tags_json = ? WHERE image_id = ?",
                    (description, tags_json, row["image_id"]))
        conn.execute("PRAGMA user_version = 11")
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
