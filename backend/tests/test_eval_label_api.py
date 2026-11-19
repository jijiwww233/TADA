import json
import sqlite3
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import db
from app.eval_api import eval_router, read_tasks


def make_client(tmp_path, data_dir):
    # Minimal real-shaped library in a temp DB; never touches the production one.
    conn = sqlite3.connect(data_dir / "app.db")
    conn.executescript(db.SCHEMA)
    for steps in db.MIGRATIONS:
        for sql in steps:
            conn.execute(sql)
    conn.execute("INSERT INTO posts (id, platform, source_id, title) VALUES (1, 'xhs', 'p1', '标题')")
    conn.execute("INSERT INTO images (id, post_id, position, sha256, file_path) "
                 "VALUES (1, 1, 0, ?, 'images/1.jpg'), (2, 1, 1, ?, 'images/2.jpg')",
                 ("a" * 64, "b" * 64))
    conn.execute("INSERT INTO post_images (post_id, position, image_id, download_state) "
                 "VALUES (1, 0, 1, 'ok'), (1, 1, 2, 'ok')")
    conn.execute(
        "INSERT INTO analyses (image_id, status, subject, description) VALUES (?, ?, ?, ?)",
        (1, "done", '["建筑"]', "一张建筑展板"),
    )
    conn.execute(
        "INSERT INTO analyses (image_id, status, subject, description) VALUES (?, ?, ?, ?)",
        (2, "pending", None, None),
    )
    conn.execute("INSERT INTO colors (image_id, rank, hex, ratio) VALUES (1, 0, '#112233', 0.6)")
    conn.commit()
    conn.close()
    tasks = tmp_path / "tasks.json"
    app = FastAPI()
    app.include_router(eval_router(tasks_path=tasks, db_path=data_dir / "app.db"))
    return TestClient(app), tasks


def test_label_page_library_and_task_roundtrip(tmp_path, data_dir):
    client, tasks_path = make_client(tmp_path, data_dir)
    page = client.get("/eval/label")
    assert page.status_code == 200
    assert "评测标注" in page.text and "/api/eval/library" in page.text
    library = client.get("/api/eval/library").json()["items"]
    assert [item["image_id"] for item in library] == [1]
    assert library[0]["subject"] == ["建筑"]
    assert library[0]["colors"][0]["hex"] == "#112233"
    assert library[0]["url"] == "/images/1.jpg"

    assert client.get("/api/eval/tasks").json() == {"version": 1, "tasks": []}
    payload = {"version": 1, "tasks": [{
        "id": "find-layout-1", "type": "find", "query": "多栏建筑展板", "group": "dev",
        "expected_image_ids": [1], "tags": ["layout"], "note": "",
    }, {
        "id": "find-style-1", "type": "find", "query": "极简风格", "group": "test",
        "expected_image_ids": [], "tags": ["style"],
    }]}
    saved = client.put("/api/eval/tasks", json=payload)
    assert saved.status_code == 200
    assert len(read_tasks(tasks_path)["tasks"]) == 2
    assert client.get("/api/eval/tasks").json()["tasks"][0]["group"] == "dev"
    created = {"version": 1, "tasks": [{
        "id": "create-1", "type": "create", "brief": "做一个建筑展板", "group": "dev",
        "expected_image_ids": [1], "expected_purposes": ["layout", "palette", "style"],
        "tags": ["recommendation"], "note": ""}]}
    assert client.put("/api/eval/tasks", json=created).status_code == 200
    saved_create = read_tasks(tasks_path)["tasks"][0]
    assert saved_create["type"] == "create"
    assert saved_create["expected_purposes"] == ["layout", "palette", "style"]


def test_invalid_tasks_are_rejected_without_overwrite(tmp_path, data_dir):
    client, tasks_path = make_client(tmp_path, data_dir)
    bad = {"version": 1, "tasks": [{"id": "x", "type": "find", "query": "q", "group": "prod"}]}
    assert client.put("/api/eval/tasks", json=bad).status_code == 422
    assert not tasks_path.exists()
    bad2 = {"version": 1, "tasks": [{"id": "x", "type": "find", "query": "q",
                                     "expected_image_ids": [0]}]}
    assert client.put("/api/eval/tasks", json=bad2).status_code == 422
