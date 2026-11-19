"""Deterministic local tests for similarity dimensions and project rediscovery."""
from datetime import date, datetime, timezone
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app import db, importer, vision
from app.embedding import dimensions
from app.embedding.client import EmbeddingClient
from app.embedding.service import vector_to_blob
from app.main import create_app
from app.project_store import ImageNotFound, ProjectStore
from app.vibe import VibeService
from conftest import png


def no_network(*args, **kwargs):
    pytest.fail("Vibe must not call a paid model")


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(EmbeddingClient, "embed", no_network)
    monkeypatch.setattr(vision, "analyze", no_network)


class LocalIndex:
    def __init__(self):
        self.ranking = []
        self.queries = []
        self.penalties = {}
        self.excluded = None

    def search(self, *, image_id, exclude_ids=()):
        self.excluded = set(exclude_ids)
        return {"status": "index_empty", "items": [], "query_image_id": image_id}

    def search_text(self, text):
        self.queries.append(text)
        return self.ranking

    def rejection_scores(self, image_ids):
        return self.penalties if image_ids else {}


@pytest.fixture
def vibe(data_dir):
    db.init_db()
    clip = LocalIndex()
    service = VibeService(clip)
    return service, clip


def seed(count, *, year=2025):
    result = importer.import_images([
        importer.Upload(f"{index}.png", png((index * 7, 25, 65)),
                        datetime(year, 1, 1, tzinfo=timezone.utc).timestamp())
        for index in range(count)
    ])
    return [row["id"] for row in result["added"]]


def add_vectors(image_ids):
    analysis = {
        "subject": json.dumps(["图书"]),
        "layout_json": json.dumps({"layout_note": "居中"}),
        "style_json": json.dumps({"tags": ["复古"]}),
    }
    orders = {
        "layout_v1": [1, 2, 3],
        "subject_v1": [2, 3, 1],
        "style_v1": [3, 1, 2],
    }
    with ProjectStore().transaction(write=True) as conn:
        for index, image_id in enumerate(image_ids):
            conn.execute(
                "UPDATE analyses SET status='done',subject=?,layout_json=?,style_json=? WHERE image_id=?",
                (*analysis.values(), image_id),
            )
            hex_color = ["#ff0000", "#ff0100", "#00ff00", "#0000ff"][index]
            conn.execute("INSERT INTO colors(image_id,rank,hex,ratio) VALUES(?,0,?,1)", (image_id, hex_color))
            for scheme, order in orders.items():
                vector = [1.0, 0.0] if index in (0, order[0]) else [0.0, 1.0]
                text = dimensions.build_text(scheme, analysis)
                assert text
                conn.execute("""INSERT INTO embeddings(image_id,dimension,model,vector,dimensions,text_hash)
                                VALUES(?,?,?,?,?,?)""",
                             (image_id, scheme, "offline-vector", vector_to_blob(vector), 2,
                              dimensions.text_hash(scheme, text)))


def test_dimensions_return_different_rankings_and_exclude_rejected(vibe):
    service, clip = vibe
    ids = seed(4)
    add_vectors(ids)
    expected = {"palette": ids[1], "layout": ids[1], "subject": ids[2], "style": ids[3]}
    for dimension, first in expected.items():
        result = service.similar(ids[0], dimension)
        assert result["items"][0]["image_id"] == first
        assert ids[0] not in [item["image_id"] for item in result["items"]]
        assert set(result["available_dimensions"]) == {"combined", *expected}
    project = service.projects.create_project("海报")
    service.reject(project["id"], ids[1])
    assert ids[1] not in [item["image_id"] for item in service.similar(ids[0], "layout")["items"]]
    service.similar(ids[0], "combined")
    assert clip.excluded == {ids[1]}


def test_unanalyzed_image_api_only_offers_combined(vibe, backend_options):
    service, clip = vibe
    (image_id,) = seed(1)
    client = TestClient(create_app(image_similarity=clip), backend_options=backend_options)
    path = f"/api/images/{image_id}/similar"
    assert client.get(path).json()["available_dimensions"] == ["combined"]
    for mode in ("palette", "layout", "subject", "style"):
        assert client.get(path, params={"dimension": mode}).status_code == 422
    assert client.get(path, params={"dimension": "invalid"}).status_code == 422


def test_rediscovery_is_daily_project_specific_and_excludes_seen_and_pinned(vibe):
    service, clip = vibe
    today = [date(2026, 11, 7)]
    service.today = lambda: today[0]
    old = seed(12)
    recent = seed_recent(old[0])
    project = service.projects.create_project("展板", "建筑展板")
    another = service.projects.create_project("字体", "海报字体")
    blank = service.projects.create_project("空描述")
    service.pin_batch(project["id"], [old[0]], "layout")
    service.reject(project["id"], old[1])
    clip.ranking = [(recent, 0.99), *[(image, 0.9 - index / 100) for index, image in enumerate(old)]]
    clip.penalties = {old[2]: 1.0}
    assert service.resurface(blank["id"]) == []
    first = [item["image_id"] for item in service.resurface(project["id"])]
    assert first == old[3:8]
    assert [item["image_id"] for item in service.resurface(project["id"])] == first
    assert clip.queries == ["建筑展板"]
    assert old[0] in [item["image_id"] for item in service.resurface(another["id"])]
    service.pin_batch(project["id"], [first[0]], "style")
    assert [item["image_id"] for item in service.resurface(project["id"])] == first[1:]
    today[0] = date(2026, 11, 8)
    second = [item["image_id"] for item in service.resurface(project["id"])]
    assert len(second) == 5 and not set(second) & set(first)
    assert old[0] not in second and old[1] not in second


def seed_recent(first):
    result = importer.import_images([importer.Upload("recent.png", png((201, 15, 92)),
        datetime(2026, 11, 1, tzinfo=timezone.utc).timestamp())])
    image_id = result["added"][0]["id"]
    assert image_id != first
    return image_id


def test_bulk_pins_are_atomic_and_preserve_existing_purpose(vibe):
    service, _ = vibe
    first, second = seed(2)
    project_id = service.projects.create_project("海报", "复古海报")["id"]
    assert service.pin_batch(project_id, [first], "layout") == 1
    with pytest.raises(ImageNotFound):
        service.pin_batch(project_id, [second, 999999], "subject")
    assert service.projects.detail(project_id)["image_ids"] == [first]
    assert service.pin_batch(project_id, [first, second, second], "subject") == 1
    assert [pin["purposes"] for pin in service.projects.detail(project_id)["pins"]] == [["layout"], ["subject"]]


def test_v12_backup_precedes_vibe_schema_changes(vibe, data_dir):
    service, _ = vibe
    (image_id,) = seed(1)
    project_id = service.projects.create_project("现有项目")["id"]
    service.pin_batch(project_id, [image_id], "layout")
    with service.projects.transaction(write=True) as conn:
        conn.execute("DROP TABLE vibe_rejected")
        conn.execute("DROP TABLE vibe_batches")
        conn.execute("PRAGMA user_version = 12")

    db.init_db()
    (backup,) = list(data_dir.glob("app.db.bak-*-v12"))
    with sqlite3.connect(backup) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 12
        assert conn.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert conn.execute("SELECT name FROM sqlite_master WHERE name LIKE 'vibe_%'").fetchall() == []
        assert conn.execute("SELECT project_id,image_id FROM pins").fetchall() == [(project_id, image_id)]
    with service.projects.transaction() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 13
        assert {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE name LIKE 'vibe_%'")} == {
            "vibe_batches", "vibe_rejected",
        }
    assert service.projects.detail(project_id)["image_ids"] == [image_id]
