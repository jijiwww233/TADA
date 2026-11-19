"""Local image import: validate -> sha256 -> dedupe -> save file -> insert rows.

One import call (one drag-and-drop) becomes one local post; images keep the drop order.
Duplicates (already in the library, or repeated within the same batch) are skipped:
no file is written and no row is inserted. If every file is a duplicate or invalid,
no post is created.
"""
import hashlib
import io
import json
import math
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from PIL import Image

from . import analysis, config, db, thumbnails

MAX_BYTES = 50 * 1024 * 1024
# ISO-BMFF HEIF brands; bytes 4..8 are "ftyp" and bytes 8..12 the major brand.
HEIC_BRANDS = {b"heic", b"heix", b"hevc", b"hevx", b"mif1", b"msf1"}
HEIC_UNAVAILABLE = "HEIC 需要 pillow-heif 组件，当前环境未安装；请先转成 JPG/PNG 再导入"

# Pillow format -> (extension, mime)
FORMATS = {
    "JPEG": ("jpg", "image/jpeg"),
    "PNG": ("png", "image/png"),
    "WEBP": ("webp", "image/webp"),
    "GIF": ("gif", "image/gif"),
    "BMP": ("bmp", "image/bmp"),
}


@dataclass
class Upload:
    name: str
    data: bytes
    modified_at: float | None = None  # epoch seconds; None = import time


@dataclass
class _Checked:
    name: str
    data: bytes
    sha256: str
    ext: str
    mime: str
    width: int
    height: int
    modified_at: float | None = None


def _is_heic(data: bytes) -> bool:
    return (len(data) >= 12 and data[4:8] == b"ftyp"
            and data[8:12] in HEIC_BRANDS)


def _heic_opener():
    """Optional pillow_heif plugin. A missing package only fails HEIC uploads."""
    try:
        import pillow_heif
    except ImportError:
        return None
    try:
        pillow_heif.register_heif_opener()
    except Exception:
        return None
    return pillow_heif


def _convert_heic(data: bytes) -> tuple[bytes | None, str | None]:
    """HEIC -> JPEG (RGB, quality=92); returns (bytes, None) or (None, reason)."""
    if _heic_opener() is None:
        return None, HEIC_UNAVAILABLE
    try:
        with Image.open(io.BytesIO(data)) as im:
            buffer = io.BytesIO()
            im.convert("RGB").save(buffer, format="JPEG", quality=92)
            return buffer.getvalue(), None
    except Exception:
        return None, "HEIC 文件无法解码"


def _safe_modified(value) -> float | None:
    """Epoch seconds, or None when the client value is missing/invalid."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value) or value < 0:
        return None
    return float(value)


def _iso_local(modified_at: float | None) -> str | None:
    """ISO local time with offset, e.g. 2024-05-01T12:03:04+09:00."""
    if modified_at is None:
        return None
    try:
        return datetime.fromtimestamp(modified_at).astimezone().isoformat(timespec="seconds")
    except (OverflowError, OSError, ValueError):
        return None


def parse_modified(raw, count: int) -> list[float | None]:
    """Parse /api/images/import's optional modified JSON (epoch ms per file).

    Parse errors, a length mismatch and invalid items all degrade to None so a bad
    client value can only lose the file mtime, never fail the import request.
    """
    if count <= 0:
        return []
    if not isinstance(raw, str) or not raw:
        return [None] * count
    try:
        values = json.loads(raw)
    except (TypeError, ValueError):
        return [None] * count
    if not isinstance(values, list) or len(values) != count:
        return [None] * count
    result = []
    for value in values:
        seconds = _safe_modified(value)
        result.append(None if seconds is None else seconds / 1000.0)
    return result


def _check(upload: Upload) -> _Checked | str:
    """Return checked image, or an error reason string."""
    if not upload.data:
        return "空文件"
    if len(upload.data) > MAX_BYTES:
        return "文件超过 50MB"
    data = upload.data
    if _is_heic(data):
        converted, reason = _convert_heic(data)
        if reason:
            return reason
        data = converted
    try:
        with Image.open(io.BytesIO(data)) as im:
            fmt = im.format
            width, height = im.size
            im.verify()
    except Exception:
        return "不是可识别的图片"
    if fmt not in FORMATS:
        return f"不支持的图片格式 {fmt}"
    ext, mime = FORMATS[fmt]
    sha = hashlib.sha256(data).hexdigest()
    return _Checked(upload.name, data, sha, ext, mime, width, height,
                    _safe_modified(upload.modified_at))


def _write_file(rel_path: str, data: bytes) -> bool:
    """Write atomically. Returns True if this call created the file."""
    path = config.data_dir() / rel_path
    if path.exists():
        return False
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)
    return True


def import_images(uploads: list[Upload],
                 on_analysis: Callable[[object, int], None] | None = None) -> dict:
    """Validate, dedupe, save and queue analysis for one batch.

    on_analysis (optional) replaces the default ``analysis.ensure_analysis`` call for
    each newly inserted image, in the same transaction. The watched-folder importer
    uses it to apply its daily auto-analysis cap and defer the rest.
    """
    hook = on_analysis or analysis.ensure_analysis
    added: list[dict] = []
    duplicates: list[dict] = []
    failed: list[dict] = []

    checked: list[_Checked] = []
    for up in uploads:
        result = _check(up)
        if isinstance(result, str):
            failed.append({"original_name": up.name, "reason": result})
        else:
            checked.append(result)

    if not checked:
        return {"post_id": None, "added": added, "duplicates": duplicates, "failed": failed}

    # One drop = one post. Images keep their own file mtime; the post uses the batch's
    # first file time (others may differ), falling back to datetime('now') when absent.
    post_created_at = _iso_local(checked[0].modified_at)
    conn = db.connect()
    created_files: list[str] = []
    post_id = None
    try:
        # Take the write lock before checking duplicates so two imports can't race.
        conn.execute("BEGIN IMMEDIATE")
        batch: dict[str, int] = {}  # sha256 -> image id added in this batch
        position = 0
        for item in checked:
            row = conn.execute("SELECT id FROM images WHERE sha256 = ?", (item.sha256,)).fetchone()
            existing_id = row["id"] if row else batch.get(item.sha256)
            if existing_id is not None:
                duplicates.append({
                    "original_name": item.name,
                    "sha256": item.sha256,
                    "existing_image_id": existing_id,
                })
                continue

            if post_id is None:
                post_id = conn.execute(
                    "INSERT INTO posts (platform, created_at) VALUES ('local', COALESCE(?, datetime('now')))",
                    (post_created_at,),
                ).lastrowid

            rel_path = f"images/{item.sha256}.{item.ext}"
            if _write_file(rel_path, item.data):
                created_files.append(rel_path)
            image_id = conn.execute(
                """INSERT INTO images
                   (post_id, position, sha256, file_path, width, height, mime, original_name, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, COALESCE(?, datetime('now')))""",
                (post_id, position, item.sha256, rel_path,
                 item.width, item.height, item.mime, item.name,
                 _iso_local(item.modified_at)),
            ).lastrowid
            conn.execute(
                "INSERT INTO post_images (post_id, position, image_id) VALUES (?, ?, ?)",
                (post_id, position, image_id),
            )
            if config.local_library_enabled():
                from .colors import ensure_colors
                ensure_colors(conn, image_id, config.data_dir() / rel_path)
            hook(conn, image_id)
            batch[item.sha256] = image_id
            position += 1
            created_at = conn.execute(
                "SELECT created_at FROM images WHERE id = ?", (image_id,)).fetchone()[0]
            added.append({
                "id": image_id,
                "original_name": item.name,
                "sha256": item.sha256,
                "url": "/" + rel_path,
                "created_at": created_at,
            })
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        for rel_path in created_files:
            (config.data_dir() / rel_path).unlink(missing_ok=True)
        raise
    finally:
        conn.close()

    for row in added:
        thumbnails.warm(row["url"].rsplit("/", 1)[-1])
    return {"post_id": post_id, "added": added, "duplicates": duplicates, "failed": failed}


def list_images(*, include_palette: bool = False) -> list[dict]:
    """Global asset grid: each image exactly once, grouped by its first source post
    (images.post_id / position). Newest post first, original order inside a post.
    For a post's own composition use list_post_images()."""
    conn = db.connect()
    try:
        # A pre-v5 (v0) library has no post_images yet; migration tests list it before
        # init_db(). Fall back to the images table so that read still works.
        has_slots = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='post_images'"
        ).fetchone() is not None
        # A v0 library has neither the short-title table nor necessarily analyses:
        # list it (migration tests read before init_db) without the title columns.
        has_titles = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='image_titles_manual'"
        ).fetchone() is not None
        title_sql = "COALESCE(t.title, a.title) AS title" if has_titles else "NULL AS title"
        title_joins = ("""LEFT JOIN analyses a ON a.image_id = i.id
                LEFT JOIN image_titles_manual t ON t.image_id = i.id"""
                       if has_titles else "")
        count_sql = ("(SELECT COUNT(*) FROM post_images pi WHERE pi.post_id=i.post_id)"
                     if has_slots else
                     "(SELECT COUNT(*) FROM images i2 WHERE i2.post_id=i.post_id)")
        rows = conn.execute(
            f"""SELECT i.id, i.post_id, i.position, i.file_path, i.width, i.height,
                       i.original_name, i.created_at, p.platform, p.is_video,
                       {count_sql} AS image_count,
                       {title_sql}
                FROM images i JOIN posts p ON p.id = i.post_id
                {title_joins}
                ORDER BY i.post_id DESC, i.position ASC"""
        ).fetchall()
        palettes = {}
        if include_palette:
            for color in conn.execute(
                "SELECT image_id, rank, hex, ratio FROM colors ORDER BY image_id, rank"
            ):
                palettes.setdefault(color["image_id"], []).append(
                    {key: color[key] for key in ("rank", "hex", "ratio")}
                )
    finally:
        conn.close()
    return [
        {
            "id": r["id"],
            "post_id": r["post_id"],
            "position": r["position"],
            "url": "/" + r["file_path"],
            "width": r["width"],
            "height": r["height"],
            "original_name": r["original_name"],
            "created_at": r["created_at"],
            "platform": r["platform"],
            "is_video": bool(r["is_video"]),
            "image_count": r["image_count"],
            # 生效短标题（人工优先，其次分析生成）；还没有标题时是 None
            "title": r["title"],
            **({"colors": palettes.get(r["id"], [])} if include_palette else {}),
        }
        for r in rows
    ]


def list_post_images(post_id: int) -> list[dict]:
    """One post's images in post order (post_images is the source of truth).
    Slots whose bytes are not saved yet have image_id/url = None."""
    conn = db.connect()
    try:
        rows = conn.execute(
            """SELECT pi.position, pi.image_id, pi.remote_url, pi.download_state, i.file_path
               FROM post_images pi LEFT JOIN images i ON i.id = pi.image_id
               WHERE pi.post_id = ? ORDER BY pi.position""",
            (post_id,),
        ).fetchall()
    finally:
        conn.close()
    return [
        {
            "position": r["position"],
            "image_id": r["image_id"],
            "url": "/" + r["file_path"] if r["file_path"] else None,
            "remote_url": r["remote_url"],
            "download_state": r["download_state"],
        }
        for r in rows
    ]


def get_image(image_id: int) -> dict | None:
    """One image plus every post it belongs to (via post_images), for the detail view."""
    conn = db.connect()
    try:
        r = conn.execute(
            """SELECT id, post_id, position, file_path, width, height, original_name, created_at
               FROM images WHERE id = ?""", (image_id,)
        ).fetchone()
        if r is None:
            return None
        posts = conn.execute(
            """SELECT p.id, p.platform, p.url, p.title, pi.position,p.is_video,p.fav_state,p.source_state,
                      (SELECT si.error FROM sync_items si WHERE si.platform=p.platform AND si.work_key=p.source_id AND si.status='failed' ORDER BY si.id DESC LIMIT 1)AS sync_error,
                      (SELECT COUNT(*)FROM post_images allpi WHERE allpi.post_id=p.id)AS image_count
               FROM post_images pi JOIN posts p ON p.id = pi.post_id
               WHERE pi.image_id = ? ORDER BY p.id""", (image_id,)
        ).fetchall()
    finally:
        conn.close()
    return {
        "id": r["id"],
        "url": "/" + r["file_path"],
        "width": r["width"],
        "height": r["height"],
        "original_name": r["original_name"],
        "created_at": r["created_at"],
        "posts": [{**dict(p),"is_video":bool(p["is_video"]),"images":list_post_images(p["id"])} for p in posts],
    }
