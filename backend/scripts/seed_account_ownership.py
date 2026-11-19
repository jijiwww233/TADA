"""One-off, idempotent attribution of already-synced data to the accounts that produced it.

Evidence:
- XHS current account 651ad4ff0000000023025570 (data/sync_profiles/xiaohongshu/account.json,
  login at 12:55). Run 12 imported posts under it.
- Pinterest auth.json username jijiwww233; run 15 imported 2 pins.
- ZCOOL out/state.json account id; run 31 imported 10 works.

The 55 older XHS posts were synced before this login; their account id was never stored in
full, so they are left without a favorite relation instead of guessing.
"""
import json
import sqlite3
from pathlib import Path

from app import config, db

XHS = "651ad4ff0000000023025570"
PINTEREST = "jijiwww233"


def zcool_account() -> str:
    state = json.loads((config.PROJECT_ROOT / "vendor" / "zcool" / "out" / "state.json")
                       .read_text(encoding="utf-8"))
    account = state.get("account") or {}
    return str(account.get("idStr") or account.get("id") or "")


def legacy(conn, platform: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM sync_cursors WHERE platform = ? ORDER BY updated_at DESC LIMIT 1",
        (platform,)).fetchone()


def seed() -> dict:
    db.init_db()
    conn = db.connect()
    out = {"relations": {}, "cursors": []}
    try:
        conn.execute("BEGIN IMMEDIATE")
        # (platform, account, posts import run id, minimum post id created by that run)
        mapping = [
            ("xhs", XHS, 12, 70),
            ("pinterest", PINTEREST, 15, 170),
            ("zcool", zcool_account(), 31, 172),
        ]
        for platform, account, run_id, min_id in mapping:
            if not account:
                continue
            rows = conn.execute(
                "SELECT id, last_seen_at, last_seen_run_id FROM posts WHERE platform = ? AND id >= ?",
                (platform, min_id)).fetchall()
            for row in rows:
                conn.execute(
                    """INSERT INTO post_favorites (account_key, post_id, first_seen_run_id,
                                                   last_seen_at, last_seen_run_id)
                       VALUES (?, ?, ?, ?, ?)
                       ON CONFLICT (account_key, post_id) DO NOTHING""",
                    (account, row["id"], run_id, row["last_seen_at"], row["last_seen_run_id"]))
            out["relations"][platform] = len(rows)
            conn.execute("UPDATE sync_runs SET account_key = ? WHERE platform = ? AND id >= ?",
                         (account, platform, run_id))
        # XHS: fresh account cursor proves bootstrap was done; incremental only from now on.
        conn.execute(
            """INSERT INTO sync_cursors_v2
               (platform, collection_key, account_key, phase, cursor_json, bootstrap_complete, updated_at)
               VALUES ('xhs', 'fav', ?, 'bootstrap', ?, 1, datetime('now'))
               ON CONFLICT (platform, collection_key, account_key) DO NOTHING""",
            (XHS, json.dumps({"page_index": 0, "has_more": False, "next_cursor": None,
                              "scan_clean": True})))
        out["cursors"].append(("xhs", XHS, "new-incremental"))
        # Pinterest / ZCOOL: adopt the legacy cursor created under the same account.
        for platform, account in (("pinterest", PINTEREST), ("zcool", zcool_account())):
            row = legacy(conn, platform)
            if not row:
                continue
            conn.execute(
                """INSERT INTO sync_cursors_v2
                   (platform, collection_key, account_key, phase, cursor_json, bootstrap_complete, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
                   ON CONFLICT (platform, collection_key, account_key) DO NOTHING""",
                (platform, row["collection_key"], account, row["phase"],
                 row["cursor_json"], row["bootstrap_complete"]))
            out["cursors"].append((platform, account, "adopted-legacy"))
        conn.execute("COMMIT")
    except Exception:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()
    return out


if __name__ == "__main__":
    print(json.dumps(seed(), ensure_ascii=False))
