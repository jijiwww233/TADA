"""Write contract pages into the library. Platform-agnostic.

Rules (docs/progress.md, 2026-11-04 同步契约):
- Post identity is (platform, source_id = work_key); image identity is sha256.
- Never DELETE local data. Missing / failed / unavailable only change state columns.
- A page lands in one transaction together with its cursor. Each item has its own
  SAVEPOINT: a failing item is rolled back alone and persisted as a sync_items row
  (work_key + stage) in the same transaction. If even that cannot be written, the
  whole page rolls back and the cursor does not move.
"""
import json
from dataclasses import dataclass, field
from pathlib import Path

from .. import analysis, config, db, thumbnails
from ..importer import Upload, _check, _write_file
from .contract import Failure, ImageRef, Item, Page


@dataclass
class PageResult:
    items_new: int = 0
    items_failed: int = 0      # distinct work_keys that need a retry
    images_ok: int = 0
    images_failed: int = 0
    failed_keys: set[str] = field(default_factory=set)


class _ItemFailed(Exception):
    def __init__(self, stage: str, error: str):
        super().__init__(error)
        self.stage = stage


# ---------------------------------------------------------------- runs

def start_run(platform: str, collection_key: str, mode: str, account_key: str = "") -> int:
    conn = db.connect()
    try:
        return conn.execute(
            """INSERT INTO sync_runs (platform, collection_key, mode, status, items_found,
                                      items_new, updated_at, account_key)
               VALUES (?, ?, ?, 'running', 0, 0, datetime('now'), ?)""",
            (platform, collection_key, mode, account_key or ""),
        ).lastrowid
    finally:
        conn.close()


def set_run_account(run_id: int, account_key: str) -> None:
    conn = db.connect()
    try:
        conn.execute("UPDATE sync_runs SET account_key = ? WHERE id = ?", (account_key or "", run_id))
    finally:
        conn.close()


def set_run_mode(run_id: int, mode: str) -> None:
    conn = db.connect()
    try:
        conn.execute("UPDATE sync_runs SET mode = ? WHERE id = ?", (mode, run_id))
    finally:
        conn.close()


def finish_run(run_id: int, status: str, *, pause_reason: str | None = None,
               error: str | None = None, full_scan: bool = False) -> None:
    conn = db.connect()
    try:
        conn.execute(
            """UPDATE sync_runs SET status = ?, pause_reason = ?, error = ?, full_scan = ?,
                      finished_at = datetime('now'), updated_at = datetime('now')
               WHERE id = ?""",
            (status, pause_reason, error, int(full_scan), run_id),
        )
    finally:
        conn.close()


def get_run(run_id: int) -> dict:
    conn = db.connect()
    try:
        return dict(conn.execute("SELECT * FROM sync_runs WHERE id = ?", (run_id,)).fetchone())
    finally:
        conn.close()


def latest_run(platform: str) -> dict | None:
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT * FROM sync_runs WHERE platform = ? ORDER BY id DESC LIMIT 1", (platform,)
        ).fetchone()
    finally:
        conn.close()
    return dict(row) if row else None


def note_run(run_id: int, note: str) -> None:
    """Append an informational note to a run's error column."""
    conn = db.connect()
    try:
        conn.execute(
            """UPDATE sync_runs SET error = CASE WHEN error IS NULL THEN ?
                                                 ELSE error || '; ' || ? END,
                      updated_at = datetime('now') WHERE id = ?""",
            (note, note, run_id),
        )
    finally:
        conn.close()


def fail_open_runs(platform: str, error: str) -> int:
    """A runner crashed outside the engine: never leave its run 'running'."""
    conn = db.connect()
    try:
        return conn.execute(
            """UPDATE sync_runs SET status = 'failed', error = ?, finished_at = datetime('now'),
                      updated_at = datetime('now')
               WHERE platform = ? AND status = 'running'""",
            (error, platform),
        ).rowcount
    finally:
        conn.close()


def recover_stale_runs() -> int:
    """Runs left 'running' by a previous process become paused; their cursor is kept."""
    conn = db.connect()
    try:
        return conn.execute(
            """UPDATE sync_runs SET status = 'paused', pause_reason = 'stale_process',
                      updated_at = datetime('now')
               WHERE status = 'running'"""
        ).rowcount
    finally:
        conn.close()


# ---------------------------------------------------------------- cursors

def load_cursor(platform: str, collection_key: str, account_key: str = "") -> dict | None:
    conn = db.connect()
    try:
        row = conn.execute(
            """SELECT * FROM sync_cursors_v2
               WHERE platform = ? AND collection_key = ? AND account_key = ?""",
            (platform, collection_key, account_key or ""),
        ).fetchone()
        if row is None and not account_key:
            # Convenience for legacy callers/tests: only when exactly one account has a row.
            rows = conn.execute(
                """SELECT * FROM sync_cursors_v2 WHERE platform = ? AND collection_key = ?""",
                (platform, collection_key),
            ).fetchall()
            row = rows[0] if len(rows) == 1 else None
    finally:
        conn.close()
    if row is None:
        return None
    return {
        "phase": row["phase"],
        "cursor": json.loads(row["cursor_json"]),
        "bootstrap_complete": bool(row["bootstrap_complete"]),
    }


def other_collections(platform: str, collection_key: str, account_key: str = "") -> list[str]:
    conn = db.connect()
    try:
        rows = conn.execute(
            """SELECT collection_key FROM sync_cursors_v2
               WHERE platform = ? AND collection_key <> ? AND account_key = ?""",
            (platform, collection_key, account_key or ""),
        ).fetchall()
    finally:
        conn.close()
    return [r[0] for r in rows]


def _save_cursor(conn, platform, collection_key, phase, cursor: dict, account_key: str = "") -> None:
    done = phase in ("bootstrap", "full") and cursor.get("has_more") is False
    conn.execute(
        """INSERT INTO sync_cursors_v2 (platform, collection_key, account_key, phase,
                                        cursor_json, bootstrap_complete, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
           ON CONFLICT (platform, collection_key, account_key) DO UPDATE SET
               phase = excluded.phase,
               cursor_json = excluded.cursor_json,
               bootstrap_complete = MAX(bootstrap_complete, excluded.bootstrap_complete),
               updated_at = excluded.updated_at""",
        (platform, collection_key, account_key or "", phase, json.dumps(cursor), int(done)),
    )


# ---------------------------------------------------------------- failures

def _record_failure(conn, run_id: int, platform: str, f: Failure, account_key: str = "") -> None:
    conn.execute(
        """INSERT INTO sync_items (run_id, platform, work_key, stage, status, error, account_key)
           VALUES (?, ?, ?, ?, 'failed', ?, ?)""",
        (run_id, platform, f.work_key, f.stage, f.error, account_key or ""),
    )


def _resolve_failures(conn, platform: str, work_key: str, account_key: str = "") -> None:
    conn.execute(
        """UPDATE sync_items SET status = 'resolved', updated_at = datetime('now')
           WHERE platform = ? AND work_key = ? AND status = 'failed'
             AND COALESCE(account_key, '') = ?""",
        (platform, work_key, account_key or ""),
    )


def unresolved_failures(platform: str, account_key: str = "") -> list[dict]:
    """Items that still need a retry (latest failure per work_key), scoped to one account."""
    conn = db.connect()
    try:
        if account_key:
            rows = conn.execute(
                """SELECT work_key, stage, error, MAX(id) AS id FROM sync_items
                   WHERE platform = ? AND status = 'failed' AND COALESCE(account_key, '') = ?
                   GROUP BY work_key ORDER BY id""",
                (platform, account_key),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT work_key, stage, error, MAX(id) AS id FROM sync_items
                   WHERE platform = ? AND status = 'failed'
                   GROUP BY work_key ORDER BY id""",
                (platform,),
            ).fetchall()
    finally:
        conn.close()
    return [{"work_key": r["work_key"], "stage": r["stage"], "error": r["error"]} for r in rows]


def run_failures(run_id: int, limit: int = 5) -> list[dict]:
    conn = db.connect()
    try:
        rows = conn.execute(
            """SELECT work_key, stage, error FROM sync_items
               WHERE run_id = ? AND status = 'failed' ORDER BY id LIMIT ?""",
            (run_id, limit),
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------- read-only helpers for readers

def is_complete(platform: str, work_key: str) -> bool:
    """The post exists, has slots, every post_images slot is saved locally, and no
    sync_items failure is still open. Readers may skip re-fetching such a post."""
    conn = db.connect()
    try:
        row = conn.execute(
            """SELECT (SELECT COUNT(*) FROM post_images WHERE post_id = p.id) AS slots,
                      (SELECT COUNT(*) FROM post_images
                        WHERE post_id = p.id AND download_state <> 'ok') AS missing,
                      (SELECT COUNT(*) FROM sync_items
                        WHERE platform = p.platform AND work_key = p.source_id
                          AND status = 'failed') AS open_failures
               FROM posts p WHERE p.platform = ? AND p.source_id = ?""",
            (platform, work_key),
        ).fetchone()
    finally:
        conn.close()
    return bool(row and row["slots"] > 0 and row["missing"] == 0 and row["open_failures"] == 0)


def max_image_id() -> int:
    conn = db.connect()
    try:
        return conn.execute("SELECT COALESCE(MAX(id), 0) FROM images").fetchone()[0]
    finally:
        conn.close()


def images_created_since(platform: str, base_id: int) -> int:
    """Images first saved by a `platform` post with id > base_id. Sync runs are serialized,
    only sync creates images whose first source is a synced post, and ids only grow;
    so with base_id taken at run start this is exactly the run's new images."""
    conn = db.connect()
    try:
        return conn.execute(
            """SELECT COUNT(*) FROM images i JOIN posts p ON p.id = i.post_id
               WHERE i.id > ? AND p.platform = ?""",
            (base_id, platform),
        ).fetchone()[0]
    finally:
        conn.close()


# ---------------------------------------------------------------- items

def _resolve_image(conn, img: ImageRef, post_id: int, position: int,
                   created: list[str]) -> tuple[int | None, str, str | None]:
    """-> (image_id, download_state, error). Never raises for a bad image."""
    if img.local_path:
        try:
            data = Path(img.local_path).read_bytes()
        except OSError as exc:
            return None, "failed", f"读取本地文件失败: {exc}"
        checked = _check(Upload(Path(img.local_path).name, data))
        if isinstance(checked, str):
            return None, "failed", checked
        if img.sha256 and img.sha256 != checked.sha256:
            return None, "failed", "sha256 与文件内容不符"
        row = conn.execute("SELECT id FROM images WHERE sha256 = ?", (checked.sha256,)).fetchone()
        if row:
            return row["id"], "ok", None
        rel_path = f"images/{checked.sha256}.{checked.ext}"
        if _write_file(rel_path, checked.data):
            created.append(rel_path)
        image_id = conn.execute(
            """INSERT INTO images
               (post_id, position, sha256, file_path, width, height, mime, original_name)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (post_id, position, checked.sha256, rel_path,
             checked.width, checked.height, checked.mime, checked.name),
        ).lastrowid
        return image_id, "ok", None

    if img.sha256:
        row = conn.execute("SELECT id FROM images WHERE sha256 = ?", (img.sha256,)).fetchone()
        if row:
            return row["id"], "ok", None
    if img.download_state == "failed":
        return None, "failed", "读取器未能下载图片"
    if img.download_state == "remote_only":
        return None, "remote_only", None
    return None, "pending", None  # claims bytes exist but none were handed over


def _touch_favorite(conn, account_key: str, post_id: int, run_id: int) -> None:
    """Favorite relation is per account; the post and its images stay shared."""
    if not account_key:
        return
    conn.execute(
        """INSERT INTO post_favorites (account_key, post_id, first_seen_run_id,
                                       last_seen_at, last_seen_run_id)
           VALUES (?, ?, ?, datetime('now'), ?)
           ON CONFLICT (account_key, post_id) DO UPDATE SET
               last_seen_at = datetime('now'),
               last_seen_run_id = excluded.last_seen_run_id""",
        (account_key, post_id, run_id, run_id),
    )


def _upsert_post(conn, run_id: int, platform: str, item: Item,
                 account_key: str = "") -> tuple[int, bool]:
    # None fields keep the stored value (COALESCE) and media_type 'unknown' keeps is_video,
    # so a reader can refresh a complete post from list data alone (with images=[]).
    extra = json.dumps(item.extra, ensure_ascii=False) if item.extra is not None else None
    is_video = None if item.media_type == "unknown" else int(item.media_type == "video")
    row = conn.execute(
        "SELECT id FROM posts WHERE platform = ? AND source_id = ?", (platform, item.work_key)
    ).fetchone()
    if row is None:
        post_id = conn.execute(
            """INSERT INTO posts (platform, source_id, url, title, description, is_video,
                                  author_name, extra_json, fav_state, source_state,
                                  miss_streak, last_seen_at, last_seen_run_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'present', 'ok', 0, datetime('now'), ?)""",
            (platform, item.work_key, item.canonical_url, item.title, item.description,
             is_video or 0, item.author_name, extra, run_id),
        ).lastrowid
        _touch_favorite(conn, account_key, post_id, run_id)
        return post_id, True
    _touch_favorite(conn, account_key, row["id"], run_id)
    conn.execute(
        """UPDATE posts SET url = COALESCE(?, url), title = COALESCE(?, title),
                  description = COALESCE(?, description), is_video = COALESCE(?, is_video),
                  author_name = COALESCE(?, author_name), extra_json = COALESCE(?, extra_json),
                  fav_state = 'present', source_state = 'ok', miss_streak = 0,
                  last_seen_at = datetime('now'), last_seen_run_id = ?
           WHERE id = ?""",
        (item.canonical_url, item.title, item.description, is_video, item.author_name,
         extra, run_id, row["id"]),
    )
    return row["id"], False


def _apply_item(conn, run_id: int, platform: str, item: Item,
                created: list[str], result: PageResult, account_key: str = "") -> None:
    """Slots are upserted by position; positions absent from item.images are never
    touched, so images=[] only refreshes the post's seen state."""
    post_id, is_new = _upsert_post(conn, run_id, platform, item, account_key)
    image_errors = []
    for img in item.images:
        position = img.idx - 1
        image_id, state, error = _resolve_image(conn, img, post_id, position, created)
        if error:
            image_errors.append(f"#{img.idx}: {error}")
            result.images_failed += 1
        existing = conn.execute(
            "SELECT image_id FROM post_images WHERE post_id = ? AND position = ?",
            (post_id, position),
        ).fetchone()
        if image_id is None and existing and existing["image_id"] is not None:
            continue  # already saved locally; a later failed fetch must not downgrade it
        conn.execute(
            """INSERT INTO post_images (post_id, position, image_id, remote_url, download_state)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT (post_id, position) DO UPDATE SET
                   image_id = excluded.image_id,
                   remote_url = COALESCE(excluded.remote_url, remote_url),
                   download_state = excluded.download_state""",
            (post_id, position, image_id, img.url, state),
        )
        if image_id is not None:
            result.images_ok += 1
            analysis.ensure_analysis(conn, image_id)

    _resolve_failures(conn, platform, item.work_key, account_key)
    if image_errors:
        _record_failure(conn, run_id, platform,
                        Failure(item.work_key, "image_download", "; ".join(image_errors)),
                        account_key)
        result.failed_keys.add(item.work_key)
    if is_new:
        result.items_new += 1


def _apply_source_unavailable(conn, run_id: int, platform: str, f: Failure) -> None:
    """Still in favorites, but the original post is gone: keep everything, mark it."""
    conn.execute(
        """UPDATE posts SET source_state = 'unavailable', fav_state = 'present', miss_streak = 0,
                  last_seen_at = datetime('now'), last_seen_run_id = ?
           WHERE platform = ? AND source_id = ?""",
        (run_id, platform, f.work_key),
    )


def apply_page(run_id: int, page: Page, phase: str, cursor_base: dict | None,
               account_key: str = "") -> PageResult:
    """Land one page. cursor_base=None: do not touch sync_cursors (incremental runs).
    On any exception the page is rolled back entirely and the cursor stays put."""
    result = PageResult()
    created: list[str] = []
    conn = db.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("""INSERT INTO sync_collections(platform,account_key,collection_key,title,kind,last_seen_run_id)
            VALUES(?,?,?,?,?,?) ON CONFLICT(platform,account_key,collection_key) DO UPDATE SET
            title=COALESCE(excluded.title,title),kind=COALESCE(excluded.kind,kind),last_seen_run_id=excluded.last_seen_run_id""",
            (page.platform,account_key,page.collection_key,page.collection_title,page.collection_kind,run_id))
        for ordinal, item in enumerate(page.items):
            item_files: list[str] = []
            conn.execute("SAVEPOINT item")
            try:
                _apply_item(conn, run_id, page.platform, item, item_files, result, account_key)
                conn.execute("""INSERT INTO post_collections(platform,account_key,collection_key,post_id,position,last_seen_run_id)
                    SELECT ?,?,?,id,?,? FROM posts WHERE platform=? AND source_id=?
                    ON CONFLICT(platform,account_key,collection_key,post_id) DO UPDATE SET
                    position=excluded.position,last_seen_run_id=excluded.last_seen_run_id""",
                    (page.platform,account_key,page.collection_key,page.index*1000000+ordinal,run_id,page.platform,item.work_key))
                conn.execute("RELEASE item")
                created += item_files
            except Exception as exc:
                conn.execute("ROLLBACK TO item")
                conn.execute("RELEASE item")
                for rel in item_files:
                    (config.data_dir() / rel).unlink(missing_ok=True)
                # Outside the savepoint: if this insert fails, the whole page rolls back.
                _record_failure(conn, run_id, page.platform,
                                Failure(item.work_key, "store", f"{type(exc).__name__}: {exc}"),
                                account_key)
                result.failed_keys.add(item.work_key)

        for f in page.failures:
            _record_failure(conn, run_id, page.platform, f, account_key)
            if f.source_unavailable:
                _apply_source_unavailable(conn, run_id, page.platform, f)
            else:
                result.failed_keys.add(f.work_key)
        result.items_failed = len(result.failed_keys)

        if cursor_base is not None:
            cursor = dict(cursor_base)
            cursor.update(
                next_cursor=page.cursor_out,
                page_index=page.index,
                has_more=page.has_more,
                scan_clean=bool(cursor_base.get("scan_clean", True)) and result.items_failed == 0,
            )
            _save_cursor(conn, page.platform, page.collection_key, phase, cursor, account_key)

        conn.execute(
            """UPDATE sync_runs SET pages_done = pages_done + 1,
                      items_found = items_found + ?, items_new = items_new + ?,
                      items_failed = items_failed + ?, images_ok = images_ok + ?,
                      images_failed = images_failed + ?, updated_at = datetime('now')
               WHERE id = ?""",
            (len(page.items), result.items_new, result.items_failed,
             result.images_ok, result.images_failed, run_id),
        )
        conn.execute("COMMIT")
    except Exception:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        for rel in created:
            (config.data_dir() / rel).unlink(missing_ok=True)
        raise
    finally:
        conn.close()
    for rel in created:
        thumbnails.warm(Path(rel).name)
    return result


def apply_missing(platform: str, scan_start_run_id: int) -> int:
    """After a clean, complete full scan: synced posts not seen since the scan started
    get miss_streak + 1; at 3 they become possibly_removed. Nothing is deleted.
    Local posts (fav_state IS NULL) never take part."""
    conn = db.connect()
    try:
        return conn.execute(
            """UPDATE posts SET
                   miss_streak = miss_streak + 1,
                   fav_state = CASE WHEN miss_streak + 1 >= 3
                                    THEN 'possibly_removed' ELSE 'unverified' END
               WHERE platform = ? AND fav_state IS NOT NULL
                 AND (last_seen_run_id IS NULL OR last_seen_run_id < ?)""",
            (platform, scan_start_run_id),
        ).rowcount
    finally:
        conn.close()
