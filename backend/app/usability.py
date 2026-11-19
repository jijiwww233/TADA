"""「能否使用」评价：看 create 三栏的展示结果，逐张点能用 / 不能用。

数据只来自已保存的推荐响应（不调用模型、不重跑 test），图库 DB 只读；页面只写
eval/usability.json。每一栏固定展示最多 5 张，未点评的图也算“已展示”，因此
可用率 = 能用数 / 展示数；另附已评/未评数与整体可用率。
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from . import config

VERSION = 1
MAX_ITEMS = 5
PANELS = (("layout", "排版"), ("palette", "配色"), ("style", "风格"))
PANEL_LABELS = dict(PANELS)


def default_path() -> Path:
    return config.PROJECT_ROOT / "eval" / "usability.json"


def _image_urls(db_path: Path, image_ids: list[int]) -> dict[int, str]:
    if not image_ids:
        return {}
    conn = sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        marks = ",".join("?" * len(image_ids))
        return {row[0]: "/" + row[1] for row in conn.execute(
            f"SELECT id, file_path FROM images WHERE id IN ({marks})", image_ids)}
    finally:
        conn.close()


def build(responses: dict, tasks: list[dict], db_path: Path, *, source: dict | None = None) -> dict:
    """Saved product responses -> rows for the requested create tasks (display only)."""
    by_id = {task["id"]: task for task in tasks}
    entries: list[dict] = []
    image_ids: list[int] = []
    for task_id, raw in responses.items():
        task = by_id.get(task_id)
        if task is None or task.get("type") != "create" or not isinstance(raw, dict):
            continue
        columns = []
        for column in raw.get("columns") or []:
            if not isinstance(column, dict) or column.get("purpose") not in PANEL_LABELS:
                continue
            items = []
            for item in (column.get("items") or [])[:MAX_ITEMS]:
                if not isinstance(item, dict) or not isinstance(item.get("image_id"), int):
                    continue
                items.append({
                    "image_id": item["image_id"],
                    "reference_point": item.get("reference_point") or "",
                    "media_scope": item.get("media_scope") or "image",
                    "usable": None,
                    "note": "",
                })
                image_ids.append(item["image_id"])
            columns.append({"panel": column["purpose"], "label": PANEL_LABELS[column["purpose"]],
                            "status": column.get("status"), "items": items})
        if columns:
            order = {panel: index for index, (panel, _) in enumerate(PANELS)}
            columns.sort(key=lambda c: order[c["panel"]])
            entries.append({"id": task_id, "query": task.get("query", ""),
                            "split": task.get("split", ""), "columns": columns})
    order = {task["id"]: index for index, task in enumerate(tasks)}
    entries.sort(key=lambda entry: order.get(entry["id"], 0))
    urls = _image_urls(db_path, sorted(set(image_ids)))
    for entry in entries:
        for column in entry["columns"]:
            for item in column["items"]:
                item["url"] = urls.get(item["image_id"], "")
    return {"version": VERSION, "source": source or {}, "tasks": entries}


def read(path: Path) -> dict:
    if not path.exists():
        return {"version": VERSION, "source": {}, "tasks": []}
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict) or not isinstance(payload.get("tasks"), list):
        raise ValueError("usability.json 格式不对")
    return payload


def write(path: Path, payload: dict) -> dict:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)
    return payload


def _find(payload: dict, task_id: str, panel: str, image_id: int) -> dict:
    for task in payload.get("tasks", []):
        if task.get("id") != task_id:
            continue
        for column in task.get("columns", []):
            if column.get("panel") != panel:
                continue
            for item in column.get("items", []):
                if item.get("image_id") == image_id:
                    return item
    raise KeyError((task_id, panel, image_id))


def apply_vote(payload: dict, *, task_id: str, panel: str, image_id: int,
               usable: bool | None, note: str | None = None) -> dict:
    item = _find(payload, task_id, panel, image_id)
    if usable not in (True, False, None):
        raise ValueError("usable 只能是 true / false / null")
    item["usable"] = usable
    if note is not None:
        item["note"] = str(note)[:500]
    return payload


def summary(payload: dict) -> dict:
    """Per-column and overall usable/displayed, plus the voted/unvoted split."""
    totals = {panel: {"displayed": 0, "usable": 0, "unusable": 0, "unvoted": 0} for panel, _ in PANELS}
    for task in payload.get("tasks", []):
        for column in task.get("columns", []):
            bucket = totals.get(column.get("panel"))
            if bucket is None:
                continue
            for item in column.get("items", []):
                bucket["displayed"] += 1
                if item.get("usable") is True:
                    bucket["usable"] += 1
                elif item.get("usable") is False:
                    bucket["unusable"] += 1
                else:
                    bucket["unvoted"] += 1
    for panel, label in PANELS:
        bucket = totals[panel]
        bucket["label"] = label
        bucket["rate"] = (bucket["usable"] / bucket["displayed"]) if bucket["displayed"] else None
    displayed = sum(bucket["displayed"] for bucket in totals.values())
    usable = sum(bucket["usable"] for bucket in totals.values())
    return {**{panel: totals[panel] for panel, _ in PANELS},
            "overall": {"displayed": displayed, "usable": usable,
                        "unusable": sum(bucket["unusable"] for bucket in totals.values()),
                        "unvoted": sum(bucket["unvoted"] for bucket in totals.values()),
                        "rate": (usable / displayed) if displayed else None}}


def merge_votes(old: dict, fresh: dict) -> dict:
    """Keep already-clicked verdicts when the display payload is rebuilt."""
    for task in old.get("tasks", []):
        for column in task.get("columns", []):
            for item in column.get("items", []):
                try:
                    target = _find(fresh, task.get("id"), column.get("panel"), item.get("image_id"))
                except KeyError:
                    continue
                target["usable"] = item.get("usable")
                target["note"] = item.get("note") or ""
    return fresh
