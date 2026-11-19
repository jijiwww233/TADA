"""Day7 persistence over existing projects/pins. No model/sync/analysis writes.

Every mutation acquires BEGIN IMMEDIATE BEFORE reading the current pin. Read/merge/
upsert is one transaction, so concurrent duplicate pins cannot lose purposes.
"""
from contextlib import contextmanager
import sqlite3

from . import db, projects as core


class ProjectNotFound(Exception): pass
class ImageNotFound(Exception): pass
class InvalidStoredPin(Exception): pass
class PersistenceError(Exception): pass


class ProjectStore:
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
                raise PersistenceError("project persistence unavailable") from None
            raise
        finally:
            if conn is not None:
                conn.close()

    @staticmethod
    def _project(conn, project_id):
        core._id(project_id, "project_id")
        row = conn.execute("SELECT id,name,brief,created_at FROM projects WHERE id=?", (project_id,)).fetchone()
        if row is None:
            raise ProjectNotFound()
        return {**dict(row), "brief": row["brief"] or ""}

    @staticmethod
    def _image(conn, image_id):
        core._id(image_id, "image_id")
        if conn.execute("SELECT 1 FROM images WHERE id=?", (image_id,)).fetchone() is None:
            raise ImageNotFound()

    @staticmethod
    def _purposes(row):
        try:
            return core.decode_purposes(row["purpose"])
        except ValueError:
            raise InvalidStoredPin() from None

    @classmethod
    def _pin_view(cls, row):
        purposes = cls._purposes(row)
        return {"id": row["id"], "project_id": row["project_id"], "image_id": row["image_id"],
                "purposes": list(purposes), "note": row["note"] or "", "created_at": row["created_at"],
                "needs_purpose": not bool(purposes)}

    @staticmethod
    def _images(conn, image_ids):
        """Two bulk queries per <=400 IDs; sources and positions from post_images."""
        images = {}
        for start in range(0, len(image_ids), 400):
            ids = image_ids[start:start + 400]
            marks = ",".join("?" for _ in ids)
            for row in conn.execute(f"""SELECT id,file_path,width,height,original_name,created_at
                    FROM images WHERE id IN ({marks})""", ids):
                image = dict(row)
                image["image_id"] = image.pop("id")
                image["url"] = "/" + image.pop("file_path")
                images[image["image_id"]] = {**image, "sources": [], "is_video": False}
            for row in conn.execute(f"""SELECT pi.image_id,pi.position,p.id AS post_id,p.platform,
                    p.source_id,p.title,p.url AS canonical_url,p.is_video
                    FROM post_images pi JOIN posts p ON p.id=pi.post_id
                    WHERE pi.image_id IN ({marks}) ORDER BY pi.image_id,p.id,pi.position""", ids):
                source = dict(row); image_id = source.pop("image_id")
                source["is_video"] = bool(source["is_video"])
                images[image_id]["sources"].append(source)
                images[image_id]["is_video"] |= source["is_video"]
        return [images[i] for i in image_ids if i in images]

    def create_project(self, name, brief=""):
        draft = core.create_project(1, name, brief)  # validate before opening a write transaction
        with self.transaction(write=True) as conn:
            cursor = conn.execute("INSERT INTO projects(name,brief) VALUES(?,?)", (draft.name, draft.brief))
            return self._project(conn, cursor.lastrowid)

    def list_projects(self):
        with self.transaction() as conn:
            return [{**dict(row), "brief": row["brief"] or ""} for row in conn.execute(
                "SELECT id,name,brief,created_at FROM projects ORDER BY id")]

    def update_brief(self, project_id, brief):
        with self.transaction(write=True) as conn:
            row = self._project(conn, project_id)
            updated = core.update_brief(core.Project(row["id"], row["name"], row["brief"]), brief)
            conn.execute("UPDATE projects SET brief=? WHERE id=?", (updated.brief, project_id))
            return self._project(conn, project_id)

    def _pins(self, conn, project_id):
        return [self._pin_view(row) for row in conn.execute("""
            SELECT pins.* FROM pins LEFT JOIN project_pin_order AS ordering ON ordering.pin_id=pins.id
            WHERE project_id=? ORDER BY ordering.position IS NULL, ordering.position, pins.id
        """, (project_id,))]

    def remove_image(self, project_id, image_id):
        with self.transaction(write=True) as conn:
            self._project(conn, project_id)
            conn.execute("DELETE FROM pins WHERE project_id=? AND image_id=?", (project_id, image_id))
            return {"image_ids": [pin["image_id"] for pin in self._pins(conn, project_id)]}

    def move_image(self, project_id, image_id, direction):
        with self.transaction(write=True) as conn:
            self._project(conn, project_id)
            pins = self._pins(conn, project_id)
            ids = [pin["image_id"] for pin in pins]
            if image_id not in ids:
                raise ImageNotFound()
            index = ids.index(image_id)
            target = index + direction
            if 0 <= target < len(pins):
                pins[index], pins[target] = pins[target], pins[index]
                conn.executemany("""
                    INSERT INTO project_pin_order(pin_id, position) VALUES (?, ?)
                    ON CONFLICT(pin_id) DO UPDATE SET position=excluded.position
                """, ((pin["id"], position) for position, pin in enumerate(pins)))
            return {"image_ids": [pin["image_id"] for pin in pins]}

    def detail(self, project_id):
        with self.transaction() as conn:
            project = self._project(conn, project_id)
            pins = self._pins(conn, project_id)
            ids = [p["image_id"] for p in pins]
            return {**project, "pins": pins, "image_ids": ids, "images": self._images(conn, ids),
                    "allowed_purposes": list(core.PURPOSES)}

    def list_pins(self, project_id):
        detail = self.detail(project_id)
        return {"items": detail["pins"], "images": detail["images"], "allowed_purposes": detail["allowed_purposes"]}

    def pin_image(self, project_id, image_id, purposes, *, note=None):
        # Validate command via the unchanged business core.
        purposes = core.normalize_purposes(purposes)
        core.pin_image((), project_id, image_id, purposes, note=note)
        with self.transaction(write=True) as conn:
            self._project(conn, project_id); self._image(conn, image_id)
            row = conn.execute("SELECT * FROM pins WHERE project_id=? AND image_id=?", (project_id, image_id)).fetchone()
            current = ()
            if row is not None:
                existing = self._purposes(row)
                if existing:
                    current = (core.Pin(project_id, image_id, existing, row["note"] or ""),)
                elif note is None:
                    note = row["note"] or ""  # repair an explicit legacy empty pin only on user pin
            merged = core.pin_image(current, project_id, image_id, purposes, note=note)[0]
            conn.execute("""INSERT INTO pins(project_id,image_id,purpose,note) VALUES(?,?,?,?)
                ON CONFLICT(project_id,image_id) DO UPDATE SET purpose=excluded.purpose,note=excluded.note""",
                (project_id, image_id, core.encode_purposes(merged.purposes), merged.note))
            return self._pin_view(conn.execute("SELECT * FROM pins WHERE project_id=? AND image_id=?", (project_id, image_id)).fetchone())

    def remove_purpose(self, project_id, image_id, purpose):
        core.normalize_purposes((purpose,))
        with self.transaction(write=True) as conn:
            self._project(conn, project_id); self._image(conn, image_id)
            row = conn.execute("SELECT * FROM pins WHERE project_id=? AND image_id=?", (project_id, image_id)).fetchone()
            if row is None:
                return None  # idempotent deletion for an existing project/image
            values = self._purposes(row)
            if not values:
                return self._pin_view(row)  # do not silently delete legacy unassigned pins
            remaining = core.remove_purpose((core.Pin(project_id, image_id, values, row["note"] or ""),), project_id, image_id, purpose)
            if not remaining:
                conn.execute("DELETE FROM pins WHERE id=?", (row["id"],))
                return None
            conn.execute("UPDATE pins SET purpose=? WHERE id=?", (core.encode_purposes(remaining[0].purposes), row["id"]))
            return self._pin_view(conn.execute("SELECT * FROM pins WHERE id=?", (row["id"],)).fetchone())
