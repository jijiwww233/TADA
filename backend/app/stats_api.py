"""Collection activity counts for the calendar."""
from fastapi import APIRouter

from . import db
from .reference_value import labels

router = APIRouter(prefix="/api/stats", tags=["stats"])


@router.get("/daily")
def stats_daily(days: int = 371):
    """Count stored images by their recorded date, with no demo rows."""
    days = max(1, min(days, 2000))
    conn = db.connect()
    try:
        rows = conn.execute(
            """SELECT date(created_at) AS day, COUNT(*) AS n
               FROM images
               WHERE date(created_at) >= date('now', ?) AND deleted_at IS NULL
               GROUP BY day ORDER BY day""",
            (f"-{days} days",),
        ).fetchall()
    finally:
        conn.close()
    return [{"date": row["day"], "count": row["n"]} for row in rows]


@router.get("/platforms")
def stats_platforms():
    """Count unique stored images by their first source post's platform."""
    conn = db.connect()
    try:
        rows = conn.execute(
            """SELECT p.platform AS platform, COUNT(*) AS n
               FROM images i JOIN posts p ON p.id = i.post_id
               WHERE i.deleted_at IS NULL
               GROUP BY p.platform ORDER BY n DESC, p.platform""",
        ).fetchall()
    finally:
        conn.close()
    return [{"platform": row["platform"], "count": row["n"]} for row in rows]

@router.get("/library")
def stats_library():
    """Count the effective visual classification from the same image snapshot."""
    conn = db.connect()
    try:
        rows = labels(conn).values()
        return {
            "collections": len(rows),
            "references": sum(row["category"] == "visual_ref" and row["manual_reference"] != 0 for row in rows),
        }
    finally:
        conn.close()
