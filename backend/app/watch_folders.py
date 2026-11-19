"""Watch local folders for new images and import them automatically.

Source folders are read-only: files are opened, stat-ed and read, never written,
moved or deleted. Imports use importer.import_images, so sha256 dedupe, HEIC -> JPEG,
the local platform label, original filename and file mtime as saved time are identical
to a manual drag-and-drop.

The separate analysis worker claims this queue against a per-local-day cap.
"""
from __future__ import annotations

import math
import logging
import os
import stat
import threading
import time
from datetime import date, datetime
from pathlib import Path

from . import analysis, config, db, importer

log = logging.getLogger(__name__)

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"}
TEMP_SUFFIXES = {".tmp", ".part", ".crdownload", ".download", ".partial", ".filepart", ".aria2"}
DEFAULT_DAILY_LIMIT = 50
MAX_DAILY_LIMIT = 10000
SCAN_INTERVAL_SECONDS = 2.0
STABLE_SECONDS = 2.0
DEFAULT_INPUT_TOKENS = 2500
DEFAULT_OUTPUT_TOKENS = 500
INPUT_PRICE_PER_MILLION = 0.2
OUTPUT_PRICE_PER_MILLION = 0.8


class DuplicateFolder(ValueError):
    pass


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, ""))
    except (TypeError, ValueError):
        return default


def _price(name: str, default: float) -> float:
    value = _env_float(name, default)
    return value if math.isfinite(value) and value >= 0 else default


def input_price() -> float:
    return _price("AI_PRICE_INPUT_PER_MILLION", INPUT_PRICE_PER_MILLION)


def output_price() -> float:
    return _price("AI_PRICE_OUTPUT_PER_MILLION", OUTPUT_PRICE_PER_MILLION)


def _today() -> str:
    return date.today().isoformat()


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _row_to_folder(row, pending: dict) -> dict:
    return {
        "id": int(row["id"]),
        "path": row["path"],
        "enabled": bool(row["enabled"]),
        "include_subfolders": bool(row["include_subfolders"]),
        "last_import_at": row["last_import_at"],
        "imported_count": int(row["imported_count"]),
        "last_error": row["last_error"],
        "pending_files": pending.get(int(row["id"]), 0),
    }


def _folder_row(conn, folder_id: int):
    return conn.execute("SELECT * FROM watch_folders WHERE id=?", (folder_id,)).fetchone()


def daily_limit(conn=None) -> int:
    own = conn is None
    conn = conn or db.connect()
    try:
        row = conn.execute("SELECT value FROM watch_settings WHERE key=? ", ("daily_limit",)).fetchone()
        try:
            value = int(row[0]) if row else DEFAULT_DAILY_LIMIT
        except (TypeError, ValueError):
            value = DEFAULT_DAILY_LIMIT
        return value if 0 <= value <= MAX_DAILY_LIMIT else DEFAULT_DAILY_LIMIT
    finally:
        if own:
            conn.close()


def set_daily_limit(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MAX_DAILY_LIMIT:
        raise ValueError("每天自动分析上限必须是 0 到 10000 的整数")
    conn = db.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("INSERT INTO watch_settings(key,value) VALUES(?,?) "
                     "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                     ("daily_limit", str(value)))
        conn.execute("COMMIT")
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        raise
    finally:
        conn.close()
    return value


def auto_analysis_enabled(conn=None) -> bool:
    if conn is None:
        conn = db.connect()
        try:
            return auto_analysis_enabled(conn)
        finally:
            conn.close()
    row = conn.execute("SELECT value FROM watch_settings WHERE key='auto_analysis'").fetchone()
    return row is not None and row[0] == "1"


def set_auto_analysis(enabled: bool) -> None:
    conn = db.connect()
    try:
        conn.execute(
            """INSERT INTO watch_settings(key,value) VALUES('auto_analysis',?)
               ON CONFLICT(key) DO UPDATE SET value=excluded.value""", (str(int(enabled)),))
    finally:
        conn.close()


def _estimate_today(conn, day: str) -> float:
    tokens_in, tokens_out = conn.execute(
        """SELECT COALESCE(SUM(COALESCE(a.prompt_tokens, ?)), 0),
                  COALESCE(SUM(COALESCE(a.completion_tokens, ?)), 0)
           FROM watch_analysis_log l JOIN analyses a USING(image_id)
           WHERE l.day=? AND l.auto=1""",
        (DEFAULT_INPUT_TOKENS, DEFAULT_OUTPUT_TOKENS, day)).fetchone()
    price_in = tokens_in * input_price()
    price_out = tokens_out * output_price()
    return (price_in + price_out) / 1_000_000


def status(service=None) -> dict:
    conn = db.connect()
    try:
        folders = conn.execute("SELECT * FROM watch_folders ORDER BY id").fetchall()
        pending = service.pending_counts() if service is not None else {}
        day = _today()
        analyzed = conn.execute(
            """SELECT COUNT(*) FROM watch_analysis_log l JOIN analyses a USING(image_id)
               WHERE l.day=? AND l.auto=1 AND a.status='done'""",
            (day,)).fetchone()[0]
        deferred = conn.execute("SELECT COUNT(*) FROM watch_deferred WHERE manual=0").fetchone()[0]
        limit = daily_limit(conn)
        cost = _estimate_today(conn, day)
        auto_analysis = config.auto_sync_allowed() and auto_analysis_enabled(conn)
    finally:
        conn.close()
    per_image = ((DEFAULT_INPUT_TOKENS * input_price()
                  + DEFAULT_OUTPUT_TOKENS * output_price()) / 1_000_000)
    return {
        "allowed": bool(config.auto_sync_allowed()),
        "watching": bool(service is not None and service.watching),
        "auto_analysis_enabled": auto_analysis,
        "folders": [_row_to_folder(f, pending) for f in folders],
        "daily_limit": limit,
        "analyzed_today": int(analyzed),
        "deferred": int(deferred),
        "estimated_cost_today": round(cost, 6),
        "estimated_cost_at_limit": round(limit * per_image, 6),
        "prices": {"input_per_million": input_price(),
                   "output_per_million": output_price()},
    }


class WatchFolders:
    """Poll enabled folders and import stable, valid image files exactly once."""

    def __init__(self, clock=time.monotonic, interval=None, stable_seconds=None):
        self.clock = clock
        self.interval = SCAN_INTERVAL_SECONDS if interval is None else interval
        self.stable_seconds = STABLE_SECONDS if stable_seconds is None else stable_seconds
        self._stop = threading.Event()
        self._thread = None
        self._run_lock = threading.Lock()
        self._pending = {}
        self._pending_lock = threading.Lock()

    @property
    def watching(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.watching or not config.auto_sync_allowed():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="watch-folders", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=5)
        if thread is not None and not thread.is_alive():
            self._thread = None

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.scan_once()
            except Exception:
                log.exception("watch folder scan failed")
            self._stop.wait(self.interval)

    def pending_counts(self) -> dict:
        with self._pending_lock:
            counts = {}
            for folder_id, _relative in self._pending:
                counts[folder_id] = counts.get(folder_id, 0) + 1
            return counts

    def _reset_pending(self, folder_id: int) -> None:
        with self._pending_lock:
            for key in [k for k in self._pending if k[0] == folder_id]:
                self._pending.pop(key, None)

    def add_folder(self, raw_path: str) -> dict:
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise ValueError("请填写文件夹路径")
        path = Path(raw_path.strip())
        if not path.is_absolute():
            raise ValueError("请填写绝对路径")
        try:
            resolved_path = path.resolve(strict=True)
        except (OSError, RuntimeError):
            raise ValueError("文件夹不存在或无法访问") from None
        if not resolved_path.is_dir() or resolved_path != Path(os.path.abspath(path)):
            raise ValueError("请选择实际文件夹")
        self._check_source(resolved_path)
        resolved = str(resolved_path)
        conn = db.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            for row in conn.execute("SELECT path FROM watch_folders"):
                if os.path.normcase(row["path"]) == os.path.normcase(resolved):
                    raise DuplicateFolder("这个文件夹已经在列表里")
            folder_id = conn.execute(
                "INSERT INTO watch_folders(path,enabled) VALUES(?,1)",
                (resolved,)).lastrowid
            conn.execute("COMMIT")
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            raise
        finally:
            conn.close()
        return self.get_folder(folder_id)

    def get_folder(self, folder_id: int) -> dict:
        conn = db.connect()
        try:
            row = _folder_row(conn, folder_id)
        finally:
            conn.close()
        if row is None:
            raise KeyError(folder_id)
        return _row_to_folder(row, self.pending_counts())

    def update_folder(self, folder_id: int, *, enabled: bool | None = None,
                      include_subfolders: bool | None = None) -> dict:
        with self._run_lock:
            self._change_folder(folder_id, enabled, include_subfolders)
            return self.get_folder(folder_id)

    def remove_folder(self, folder_id: int) -> None:
        with self._run_lock:
            self._change_folder(folder_id, None, None, remove=True)

    def _change_folder(self, folder_id: int, enabled: bool | None,
                       include_subfolders: bool | None, *, remove: bool = False) -> None:
        conn = db.connect()
        try:
            if remove:
                changed = conn.execute("DELETE FROM watch_folders WHERE id=?", (folder_id,)).rowcount
            else:
                changed = conn.execute(
                    """UPDATE watch_folders SET enabled=COALESCE(?,enabled),
                       include_subfolders=COALESCE(?,include_subfolders) WHERE id=?""",
                    (enabled, include_subfolders, folder_id)).rowcount
        finally:
            conn.close()
        self._reset_pending(folder_id)
        if not changed:
            raise KeyError(folder_id)

    def scan_once(self, now=None) -> dict:
        with self._run_lock:
            stats = {"folders": 0, "imported": 0, "duplicates": 0, "failed": 0}
            conn = db.connect()
            try:
                folders = [dict(r) for r in conn.execute(
                    "SELECT id,path,include_subfolders FROM watch_folders WHERE enabled=1 ORDER BY id")]
            finally:
                conn.close()
            for folder in folders:
                stats["folders"] += 1
                try:
                    self._scan_folder(folder, now, stats)
                except Exception:
                    log.exception("watch folder scan failed: %s", folder["path"])
                    self._set_error(folder["id"], "扫描失败，请检查文件夹权限后重试")
            return stats

    def scan_manual(self) -> dict:
        """One UI click: scan now, wait out the stability window, scan again.

        Automatic polling already gets this for free (the next tick is after the
        stability window); the manual button is the only path on non-entry instances,
        so it does the second pass itself to make one click actually import files.
        """
        stats = self.scan_once()
        if not sum(self.pending_counts().values()):
            return stats
        if self.stable_seconds > 0:
            time.sleep(self.stable_seconds)
        second = self.scan_once()
        stats["folders"] = second["folders"]
        for key in ("imported", "duplicates", "failed"):
            stats[key] += second[key]
        return stats

    @staticmethod
    def _check_source(root: Path) -> None:
        images = config.images_dir().resolve()
        data_dir = config.data_dir().resolve()
        if any(root.is_relative_to(managed) or managed.is_relative_to(root)
               for managed in (data_dir, images)):
            raise ValueError("请选择图库数据目录之外的图片文件夹")

    def _scan_folder(self, folder: dict, now: float | None, stats: dict) -> None:
        root = Path(folder["path"])
        folder_id = folder["id"]
        conn = db.connect()
        try:
            known = {row[0]: (row[1], row[2]) for row in conn.execute(
                "SELECT rel_path,size,mtime FROM watch_files WHERE folder_id=?", (folder_id,))}
        finally:
            conn.close()
        seen = set()
        try:
            if root.resolve(strict=True) != root or not root.is_dir():
                raise ValueError("文件夹位置已变更")
            self._check_source(root)
            directories = [root]
            while directories and not self._stop.is_set():
                directory = directories.pop()
                if directory.resolve(strict=True) != directory:
                    continue
                with os.scandir(directory) as entries:
                    for entry in entries:
                        if self._stop.is_set():
                            return
                        if entry.name.startswith((".", "~$")):
                            continue
                        try:
                            current = entry.stat(follow_symlinks=False)
                        except FileNotFoundError:
                            continue
                        if (stat.S_ISLNK(current.st_mode) or
                                getattr(current, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT):
                            continue
                        path = Path(entry.path)
                        if stat.S_ISDIR(current.st_mode) and folder["include_subfolders"]:
                            directories.append(path)
                        elif stat.S_ISREG(current.st_mode) and self._image_name(path):
                            relative = str(path.relative_to(root))
                            seen.add(relative)
                            try:
                                current = path.lstat()
                            except FileNotFoundError:
                                continue
                            if (not stat.S_ISREG(current.st_mode) or
                                    getattr(current, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT):
                                continue
                            self._consider(folder_id, path, relative, current, known.get(relative),
                                           self.clock() if now is None else now, stats)
        except (OSError, ValueError, RuntimeError):
            self._set_error(folder_id, "文件夹不存在、位置已变更或无法访问")
            self._reset_pending(folder_id)
            return
        with self._pending_lock:
            for key in list(self._pending):
                if key[0] == folder_id and key[1] not in seen:
                    self._pending.pop(key)

    @staticmethod
    def _image_name(path: Path) -> bool:
        suffixes = [suffix.lower() for suffix in path.suffixes]
        return (bool(suffixes) and suffixes[-1] in IMAGE_EXTENSIONS
                and not any(suffix in TEMP_SUFFIXES for suffix in suffixes[:-1]))

    def _consider(self, folder_id: int, path: Path, relative: str, current,
                  known: tuple | None, now: float, stats: dict) -> None:
        signature = (current.st_size, current.st_mtime_ns)
        if known == signature:
            return

        key = (folder_id, relative)
        with self._pending_lock:
            pending = self._pending.get(key)
            if pending is None or pending[0] != signature:
                pending = self._pending[key] = (signature, now)
            if now - pending[1] < self.stable_seconds:
                return
            self._pending.pop(key)
        self._import_file(folder_id, path, relative, current, stats)

    def _import_file(self, folder_id: int, path: Path, relative: str, current, stats: dict) -> None:
        def matches(other):
            return (os.path.samestat(current, other) and current.st_size == other.st_size
                    and current.st_mtime_ns == other.st_mtime_ns)

        try:
            if path.resolve(strict=True) != path:
                return
            with path.open("rb") as source:
                if not matches(os.fstat(source.fileno())):
                    return
                data = source.read(importer.MAX_BYTES + 1)
                if (not matches(os.fstat(source.fileno())) or not matches(path.lstat())
                        or path.resolve(strict=True) != path):
                    return
        except OSError:
            self._set_error(folder_id, "读取文件失败，请检查文件权限")
            stats["failed"] += 1
            return

        def queue_analysis(conn, image_id):
            conn.execute("INSERT INTO watch_deferred(image_id) VALUES(?)", (image_id,))
            self._record_file(conn, folder_id, relative, current, image_id)
            conn.execute(
                """UPDATE watch_folders SET imported_count=imported_count+1,
                   last_import_at=?, last_error=NULL WHERE id=?""", (_now_iso(), folder_id))

        result = importer.import_images(
            [importer.Upload(name=path.name, data=data, modified_at=current.st_mtime)],
            on_analysis=queue_analysis)
        if result["added"]:
            stats["imported"] += 1
            return
        conn = db.connect()
        try:
            if result["duplicates"]:
                image_id = result["duplicates"][0]["existing_image_id"]
                self._record_file(conn, folder_id, relative, current, image_id)
                stats["duplicates"] += 1
            else:
                self._record_file(conn, folder_id, relative, current, None)
                stats["failed"] += 1
                self._set_error(folder_id, result["failed"][0]["reason"])
        finally:
            conn.close()

    @staticmethod
    def _record_file(conn, folder_id: int, name: str, current, image_id) -> None:
        conn.execute(
            """INSERT INTO watch_files(folder_id,rel_path,size,mtime,image_id,imported_at)
               VALUES(?,?,?,?,?,?)
               ON CONFLICT(folder_id,rel_path) DO UPDATE SET size=excluded.size,
               mtime=excluded.mtime, image_id=excluded.image_id, imported_at=excluded.imported_at""",
            (folder_id, name, current.st_size, current.st_mtime_ns, image_id, _now_iso()))

    def _set_error(self, folder_id: int, message: str | None) -> None:
        conn = db.connect()
        try:
            conn.execute("UPDATE watch_folders SET last_error=? WHERE id=?", (message, folder_id))
        finally:
            conn.close()

    def prepare_analysis(self, conn) -> int | None:
        """Select and reserve one slot inside the analysis claim transaction."""
        day = _today()
        used = conn.execute(
            "SELECT COUNT(*) FROM watch_analysis_log WHERE day=? AND auto=1", (day,)).fetchone()[0]
        allow_auto = (config.auto_sync_allowed() and auto_analysis_enabled(conn)
                      and used < daily_limit(conn))
        row = conn.execute(
            """SELECT image_id, manual FROM watch_deferred
               WHERE manual=1 OR ? ORDER BY manual DESC, image_id LIMIT 1""",
            (allow_auto,)).fetchone()
        if row is None:
            return None
        image_id, manual = row
        analysis.ensure_analysis(conn, image_id)
        conn.execute("INSERT INTO watch_analysis_log(image_id,day,auto) VALUES(?,?,?)",
                     (image_id, day, int(not manual)))
        conn.execute("DELETE FROM watch_deferred WHERE image_id=?", (image_id,))
        return image_id

    def analyze_now(self) -> dict:
        conn = db.connect()
        try:
            queued = conn.execute("UPDATE watch_deferred SET manual=1 WHERE manual=0").rowcount
        finally:
            conn.close()
        return {"queued": queued, "deferred": 0}
