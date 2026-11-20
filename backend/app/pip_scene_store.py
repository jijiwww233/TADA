"""SQLite persistence for one PiP canvas scene per project (schema v9).

save_scene runs as a single BEGIN IMMEDIATE transaction: check the project,
compare the caller's revision with the stored one, check referenced images, then
upsert. A stale frontend therefore gets RevisionConflict instead of overwriting
newer work; image existence is checked inside the write transaction.
"""
from contextlib import contextmanager
import json
import sqlite3

from . import db, projects as core
from .pip_scene import validate_scene
from .project_store import ImageNotFound, PersistenceError, ProjectNotFound


class RevisionConflict(Exception):
    """The caller's revision does not match the stored revision."""

    def __init__(self, current_revision, current_scene):
        super().__init__(f"revision conflict: stored revision is {current_revision}")
        self.current_revision = current_revision
        self.current_scene = current_scene


class InvalidStoredScene(Exception):
    """The row exists but scene_json is not a valid scene (hand-edited database)."""


class PipSceneStore:
    def __init__(self, connect=None):
        self.connect = connect or db.connect

    @contextmanager
    def transaction(self, *, write=False):
        conn = None
        try:
            conn = self.connect()
            if not write:
                conn.execute("PRAGMA query_only=ON")
            conn.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield conn
            conn.execute("COMMIT")
        except BaseException as exc:
            if conn is not None and conn.in_transaction:
                conn.rollback()
            if isinstance(exc, sqlite3.Error):
                raise PersistenceError("pip scene persistence unavailable") from None
            raise
        finally:
            if conn is not None:
                conn.close()

    @staticmethod
    def _project(conn, project_id):
        core._id(project_id, "project_id")
        if conn.execute("SELECT 1 FROM projects WHERE id=? AND deleted_at IS NULL",
                        (project_id,)).fetchone() is None:
            raise ProjectNotFound()

    @staticmethod
    def _revision(value):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError("revision must be a non-negative integer")
        return value

    @staticmethod
    def _decode(scene_json):
        try:
            scene = json.loads(scene_json)
        except (TypeError, ValueError):
            raise InvalidStoredScene() from None
        try:
            return validate_scene(scene)
        except ValueError:
            raise InvalidStoredScene() from None

    @staticmethod
    def _row(conn, project_id):
        return conn.execute(
            "SELECT revision, scene_json, updated_at FROM project_pip_scenes WHERE project_id=?",
            (project_id,)).fetchone()

    @staticmethod
    def _require_images(conn, image_ids):
        if not image_ids:
            return
        marks = ",".join("?" for _ in image_ids)
        found = {row[0] for row in conn.execute(
            f"SELECT id FROM images WHERE id IN ({marks}) AND deleted_at IS NULL", image_ids)}
        missing = sorted(set(image_ids) - found)
        if missing:
            raise ImageNotFound(missing)

    def get_scene(self, project_id):
        with self.transaction() as conn:
            self._project(conn, project_id)
            row = self._row(conn, project_id)
            if row is None:
                return {"revision": 0, "updated_at": None, "scene": None}
            # Positional access keeps injected plain sqlite3 connections working
            # (they may not set row_factory=sqlite3.Row like db.connect does).
            return {"revision": row[0], "updated_at": row[2],
                    "scene": self._decode(row[1])}

    def save_scene(self, project_id, revision, scene):
        revision = self._revision(revision)
        # Structural validation is pure and happens before the write lock, so an
        # invalid scene never reaches storage (the API reports 422, not 404).
        scene = validate_scene(scene)
        with self.transaction(write=True) as conn:
            self._project(conn, project_id)
            row = self._row(conn, project_id)
            current_revision = 0 if row is None else row[0]
            current_scene = None if row is None else self._decode(row[1])
            if revision != current_revision:
                raise RevisionConflict(current_revision, current_scene)
            self._require_images(conn, [item["image_id"] for item in scene["items"]])
            new_revision = current_revision + 1
            payload = json.dumps(scene, ensure_ascii=False, separators=(",", ":"))
            conn.execute(
                """INSERT INTO project_pip_scenes(project_id, revision, scene_json)
                   VALUES(?,?,?)
                   ON CONFLICT(project_id) DO UPDATE SET
                       revision=excluded.revision,
                       scene_json=excluded.scene_json,
                       updated_at=datetime('now')""",
                (project_id, new_revision, payload))
            updated_at = conn.execute(
                "SELECT updated_at FROM project_pip_scenes WHERE project_id=?",
                (project_id,)).fetchone()[0]
            return {"revision": new_revision, "updated_at": updated_at, "scene": scene}
