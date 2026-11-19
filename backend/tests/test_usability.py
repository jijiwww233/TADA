import sqlite3

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import usability
from app.usability_api import usability_router


def _db(tmp_path):
    path = tmp_path / "app.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE IF NOT EXISTS images (id INTEGER PRIMARY KEY, file_path TEXT)")
    conn.executemany("INSERT OR REPLACE INTO images VALUES (?,?)", [(5, "images/a.png"), (6, "images/b.png")])
    conn.commit()
    conn.close()
    return path


def _payload(tmp_path):
    responses = {
        "t1": {"columns": [
            {"purpose": "layout", "status": "ok",
             "items": [{"image_id": 5, "reference_point": "网格"}]},
            {"purpose": "palette", "status": "ok",
             "items": [{"image_id": 6, "reference_point": "米白"}]},
        ]},
        "t2": {"columns": [{"purpose": "layout", "status": "ok", "items": [{"image_id": 6}]}]},
    }
    tasks = [{"id": "t1", "type": "create", "query": "展板", "split": "test"},
             {"id": "t2", "type": "find", "query": "找图", "split": "test"},
             {"id": "t3", "type": "create", "query": "别的", "split": "dev"}]
    selected = [t for t in tasks if t["type"] == "create" and t["split"] == "test"]
    return usability.build(responses, selected, _db(tmp_path))


def test_build_keeps_create_columns_and_hydrates_image_urls(tmp_path):
    payload = _payload(tmp_path)
    assert [task["query"] for task in payload["tasks"]] == ["展板"]
    layout, palette = payload["tasks"][0]["columns"]
    assert (layout["label"], palette["label"]) == ("排版", "配色")
    assert layout["items"][0]["url"] == "/images/a.png"
    assert layout["items"][0]["reference_point"] == "网格"
    assert layout["items"][0]["usable"] is None


def test_votes_and_summary_are_usable_over_displayed(tmp_path):
    payload = _payload(tmp_path)
    usability.apply_vote(payload, task_id="t1", panel="layout", image_id=5, usable=True)
    usability.apply_vote(payload, task_id="t1", panel="palette", image_id=6, usable=False)
    summary = usability.summary(payload)
    assert summary["layout"] == {"displayed": 1, "usable": 1, "unusable": 0, "unvoted": 0,
                                 "label": "排版", "rate": 1.0}
    assert summary["palette"]["rate"] == 0.0
    assert summary["overall"] == {"displayed": 2, "usable": 1, "unusable": 1, "unvoted": 0, "rate": 0.5}
    with pytest.raises(KeyError):
        usability.apply_vote(payload, task_id="t1", panel="layout", image_id=99, usable=True)


def test_unvoted_items_stay_in_the_denominator_and_can_be_cleared(tmp_path):
    payload = _payload(tmp_path)
    usability.apply_vote(payload, task_id="t1", panel="layout", image_id=5, usable=True)
    usability.apply_vote(payload, task_id="t1", panel="layout", image_id=5, usable=None)
    summary = usability.summary(payload)
    assert summary["layout"]["displayed"] == 1 and summary["layout"]["rate"] == 0.0
    assert summary["layout"]["unvoted"] == 1


def test_rebuild_keeps_the_clicks_already_saved(tmp_path):
    payload = _payload(tmp_path)
    usability.apply_vote(payload, task_id="t1", panel="layout", image_id=5, usable=True)
    merged = usability.merge_votes(payload, _payload(tmp_path))
    assert merged["tasks"][0]["columns"][0]["items"][0]["usable"] is True


def test_page_and_vote_api_roundtrip(tmp_path):
    path = tmp_path / "usability.json"
    usability.write(path, _payload(tmp_path))
    app = FastAPI()
    app.include_router(usability_router(data_path=path))
    with TestClient(app) as client:
        page = client.get("/eval/usability")
        assert page.status_code == 200 and "能否使用" in page.text
        data = client.get("/api/usability/data").json()
        assert data["tasks"][0]["query"] == "展板" and "summary" in data
        response = client.post("/api/usability/vote",
                               json={"task_id": "t1", "panel": "layout", "image_id": 5, "usable": True})
        assert response.status_code == 200 and response.json()["summary"]["layout"]["usable"] == 1
        saved = usability.read(path)
        assert saved["tasks"][0]["columns"][0]["items"][0]["usable"] is True
        unknown = client.post("/api/usability/vote",
                              json={"task_id": "t1", "panel": "style", "image_id": 5, "usable": True})
        assert unknown.status_code == 404
