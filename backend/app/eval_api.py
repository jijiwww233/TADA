"""Read-only evaluation label page and task file API.

This is a small backend-served page, not the product frontend. It reads the real
library read-only, shows done images, and writes only eval/tasks.json. No model,
analysis, embedding, pin or project write happens here.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, StrictStr

from . import config
from .eval_tasks import validate as validate_f3

EVAL_DIR = config.PROJECT_ROOT / "eval"
TASKS_FILE = EVAL_DIR / "tasks.json"
LABEL_HTML = Path(__file__).with_name("eval_label.html")


def _read_db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def _json(value):
    if not value:
        return None
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return None


def load_library(db_path: Path) -> list[dict]:
    """Done images only; same fields used by the label page and library overview."""
    conn = _read_db(db_path)
    try:
        rows = conn.execute(
            """SELECT i.id AS image_id,i.file_path,i.width,i.height,i.original_name,
                      a.subject,a.description,a.layout_json,a.style_json
               FROM images i JOIN analyses a ON a.image_id=i.id AND a.status='done'
               ORDER BY i.id""").fetchall()
        sources = {}
        for source in conn.execute("""SELECT pi.image_id,p.platform,p.title
                FROM post_images pi JOIN posts p ON p.id=pi.post_id
                ORDER BY pi.image_id,p.id,pi.position"""):
            sources.setdefault(source['image_id'], []).append(dict(source))
        colors: dict[int, list[dict]] = {}
        for row in conn.execute(
                """SELECT c.image_id, c.hex, c.ratio FROM colors c
                   JOIN analyses a ON a.image_id = c.image_id AND a.status = 'done'
                   ORDER BY c.image_id, c.rank"""):
            colors.setdefault(row["image_id"], []).append(
                {"hex": row["hex"], "ratio": row["ratio"]})
    finally:
        conn.close()
    items = []
    for row in rows:
        items.append({
            "image_id": row["image_id"],
            "url": "/" + row["file_path"],
            "file_path": row["file_path"],
            "width": row["width"],
            "height": row["height"],
            "original_name": row["original_name"],
            "post_title": next((x["title"] for x in sources.get(row["image_id"], []) if x["title"]), ""),
            "platforms": sorted({x["platform"] for x in sources.get(row["image_id"], [])}),
            "subject": _json(row["subject"]) or [],
            "description": row["description"] or "",
            "layout": _json(row["layout_json"]),
            "style": _json(row["style_json"]),
            "colors": colors.get(row["image_id"], [])[:5],
        })
    return items


def _task_list(payload) -> list[dict]:
    if not isinstance(payload, dict) or payload.get("version") != 1:
        raise ValueError("tasks 根对象必须是 {version:1,tasks:[]}")
    if any("expected" in t or "split" in t for t in payload.get("tasks", []) if isinstance(t, dict)):
        return validate_f3(payload)["tasks"]
    tasks = payload.get("tasks")
    if not isinstance(tasks, list):
        raise ValueError("tasks 必须是数组")
    seen = set()
    out = []
    for index, item in enumerate(tasks, 1):
        if not isinstance(item, dict):
            raise ValueError(f"tasks[{index}] 必须是对象")
        task_id = item.get("id")
        if not isinstance(task_id, str) or not task_id.strip() or task_id in seen:
            raise ValueError(f"tasks[{index}].id 非法或重复")
        seen.add(task_id)
        kind = item.get("type")
        if kind not in ("find", "create"):
            raise ValueError(f"tasks[{index}].type 非法")
        if kind == "find" and (not isinstance(item.get("query"), str) or not item["query"].strip()):
            raise ValueError(f"tasks[{index}].query 不能为空")
        if kind == "create" and (not isinstance(item.get("brief"), str) or not item["brief"].strip()):
            raise ValueError(f"tasks[{index}].brief 不能为空")
        group = item.get("group", "dev")
        if group not in ("dev", "test"):
            raise ValueError(f"tasks[{index}].group 非法")
        expected = item.get("expected_image_ids", [])
        if not isinstance(expected, list) or not all(
                isinstance(x, int) and not isinstance(x, bool) and x > 0 for x in expected):
            raise ValueError(f"tasks[{index}].expected_image_ids 必须是正整数数组")
        tags = item.get("tags", [])
        if not isinstance(tags, list) or not all(isinstance(x, str) for x in tags):
            raise ValueError(f"tasks[{index}].tags 必须是字符串数组")
        task = {
            "id": task_id.strip(), "type": kind,
            **({"query": item["query"].strip()} if kind == "find" else {"brief": item["brief"].strip()}),
            "group": group,
            "expected_image_ids": list(dict.fromkeys(expected)),
            "tags": list(dict.fromkeys(tags)),
            "note": item.get("note") if isinstance(item.get("note"), str) else "",
        }
        if kind == "find":
            task["cross_theme_image_ids"] = [int(x) for x in item.get("cross_theme_image_ids", [])
                                              if isinstance(x, int) and not isinstance(x, bool)]
        else:
            previous = item.get("previous_brief")
            task["previous_brief"] = previous.strip() if isinstance(previous, str) else ""
            purposes = item.get("expected_purposes", [])
            task["expected_purposes"] = ([x for x in purposes if isinstance(x, str)]
                                         if isinstance(purposes, list) else [])
        metadata = item.get("metadata")
        if isinstance(metadata, dict):
            task["metadata"] = metadata
        out.append(task)
    return out


def read_tasks(path: Path = TASKS_FILE) -> dict:
    if not path.exists():
        return {"version": 1, "tasks": []}
    return json.loads(path.read_text(encoding="utf-8"))


def write_tasks(payload, path: Path = TASKS_FILE) -> dict:
    tasks = _task_list(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"version": 1, "tasks": tasks}, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    tmp.replace(path)
    return {"version": 1, "tasks": tasks}


class TasksPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int
    tasks: list[dict]


def eval_router(*, tasks_path: Path = TASKS_FILE, db_path: Path | None = None):
    router = APIRouter()

    @router.get("/eval/label", response_class=HTMLResponse)
    def label_page():
        return HTMLResponse(LABEL_HTML.read_text(encoding="utf-8"))

    @router.get("/api/eval/library")
    def library():
        return {"items": load_library(db_path or config.db_path())}

    similar_engine = None

    @router.get("/api/eval/similar/{image_id}")
    def similar(image_id: int):
        nonlocal similar_engine
        from .similarity import ImageSimilarity, SimilarityUnavailable
        try:
            if similar_engine is None:
                similar_engine = ImageSimilarity(database=lambda: db_path or config.db_path())
            result = similar_engine.search(image_id=image_id)
            allowed = {x['image_id'] for x in load_library(db_path or config.db_path())}
            return {**result, 'items': [x for x in result['items'] if x['image_id'] in allowed]}
        except KeyError:
            raise HTTPException(404, '图片不存在') from None
        except SimilarityUnavailable as exc:
            raise HTTPException(503, str(exc)) from None
        except Exception:
            raise HTTPException(503, '本地找相似暂不可用') from None

    @router.get("/api/eval/tasks")
    def tasks():
        try:
            return read_tasks(tasks_path)
        except Exception:
            raise HTTPException(500, detail={"code": "tasks_unreadable"}) from None

    @router.put("/api/eval/tasks")
    def save(payload: TasksPayload):
        try:
            return write_tasks(payload.model_dump(), tasks_path)
        except ValueError as exc:
            raise HTTPException(422, detail={"code": "invalid_tasks", "message": str(exc)}) from None
        except OSError:
            raise HTTPException(503, detail={"code": "tasks_write_failed"}) from None

    return router
