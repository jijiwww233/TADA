"""PiP scene persistence: pure contract, SQLite store, HTTP API and v8->v9 migration.

Every test uses a temporary SQLite database (the data_dir fixture); the real
data/app.db is never touched. No network, model or sync code is involved.
"""
import copy
import json
import sqlite3

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import config, db
from app.pip_scene import validate_scene
from app.pip_scene_api import pip_scene_router
from app.pip_scene_store import InvalidStoredScene, PipSceneStore, RevisionConflict
from app.project_store import ImageNotFound, PersistenceError, ProjectNotFound


# ---------------------------------------------------------------- scene builders

def scene(*, version=1, view=None, items=None, notes=None):
    return {
        "version": version,
        "view": view if view is not None else {"x": 0.0, "y": -12.5, "scale": 1.0},
        "items": items if items is not None else [item()],
        "notes": notes if notes is not None else [note()],
    }


def item(**overrides):
    value = {
        "image_id": 1,
        "x": 10.5,
        "y": -20.0,
        "width": 320.0,
        "height": 240.0,
        "rotation": -15.5,
        "flip_x": False,
        "grayscale": True,
        "opacity": 0.85,
        "crop": None,
    }
    value.update(overrides)
    return value


def crop(**overrides):
    value = {"x": 0.1, "y": 0.2, "width": 0.5, "height": 0.6}
    value.update(overrides)
    return value


def note(**overrides):
    value = {"id": "note-1", "x": 0.0, "y": 0.0, "text": "hello",
             "color": "#a1b2c3", "font_size": 14}
    value.update(overrides)
    return value


def scene_for(image_ids):
    return scene(items=[item(image_id=image_id) for image_id in image_ids])


def changed(mutate):
    candidate = copy.deepcopy(scene())
    mutate(candidate)
    return candidate


# ---------------------------------------------------------------- pure validation

def test_valid_scene_is_normalized_and_json_serializable():
    candidate = scene(
        view={"x": -120.25, "y": 4000.0, "scale": 2.5},
        items=[
            item(image_id=7, crop=crop()),
            item(image_id=9, x=-300.0, rotation=90, flip_x=True, grayscale=False,
                 opacity=1, crop=None),
        ],
        notes=[
            note(id="n-1", text="参考图小窗", color="#AABBCC", font_size=13),
            note(id="n-2", text="", x=5, y=6, color="#000000", font_size=8),
        ],
    )
    result = validate_scene(candidate)
    assert result == candidate
    assert result is not candidate
    assert json.loads(json.dumps(result, ensure_ascii=False)) == candidate


def test_boundary_values_are_accepted():
    candidate = scene(
        view={"x": -1_000_000, "y": 1_000_000, "scale": 0.05},
        items=[
            item(image_id=1, x=-1_000_000, y=1_000_000, width=1, height=100_000,
                 rotation=-3600, opacity=0, crop=crop(x=0, y=0, width=1, height=1)),
            item(image_id=2, x=1_000_000, y=-1_000_000, width=100_000, height=1,
                 rotation=3600, opacity=1, flip_x=True, grayscale=False,
                 crop=crop(x=0.5, y=0.5, width=0.5, height=0.5)),
        ],
        notes=[
            note(id="a" * 64, text="x" * 2000, font_size=8, color="#abcdef"),
            note(id="z", text="", font_size=200, color="#000000"),
        ],
    )
    assert validate_scene(candidate) == candidate


def test_crop_accepts_representable_float_sum_of_one():
    # 0.8 + 0.2 == 1.0000000000000002 in binary floats; this is not an out-of-range crop.
    candidate = scene(items=[item(crop=crop(x=0.8, y=0.9, width=0.2, height=0.1))])
    assert validate_scene(candidate) == candidate


def test_scene_must_be_an_object():
    for value in ([], None, "scene", 3):
        with pytest.raises(ValueError):
            validate_scene(value)


BAD_SCENES = [
    pytest.param(lambda s: s.pop("version"), id="missing-version"),
    pytest.param(lambda s: s.pop("view"), id="missing-view"),
    pytest.param(lambda s: s.pop("items"), id="missing-items"),
    pytest.param(lambda s: s.pop("notes"), id="missing-notes"),
    pytest.param(lambda s: s.update(extra=1), id="extra-top-level-key"),
    pytest.param(lambda s: s.update(version=2), id="version-2"),
    pytest.param(lambda s: s.update(version=0), id="version-0"),
    pytest.param(lambda s: s.update(version=True), id="version-bool"),
    pytest.param(lambda s: s.update(version="1"), id="version-string"),
    pytest.param(lambda s: s.update(version=1.0), id="version-float"),
    pytest.param(lambda s: s.update(view=None), id="view-null"),
    pytest.param(lambda s: s.update(view=[]), id="view-array"),
    pytest.param(lambda s: s["view"].update(extra=1), id="view-extra-key"),
    pytest.param(lambda s: s["view"].pop("scale"), id="view-missing-scale"),
    pytest.param(lambda s: s["view"].update(x=1_000_001), id="view-x-too-large"),
    pytest.param(lambda s: s["view"].update(x=-1_000_001), id="view-x-too-small"),
    pytest.param(lambda s: s["view"].update(y=1_000_001), id="view-y-too-large"),
    pytest.param(lambda s: s["view"].update(scale=0.049), id="scale-too-small"),
    pytest.param(lambda s: s["view"].update(scale=20.01), id="scale-too-large"),
    pytest.param(lambda s: s["view"].update(scale="1"), id="scale-string"),
    pytest.param(lambda s: s["view"].update(x=True), id="view-x-bool"),
    pytest.param(lambda s: s["view"].update(x=float("nan")), id="view-x-nan"),
    pytest.param(lambda s: s["view"].update(y=float("inf")), id="view-y-inf"),
    pytest.param(lambda s: s["view"].update(scale=float("-inf")), id="scale-minus-inf"),
    pytest.param(lambda s: s.update(items={}), id="items-object"),
    pytest.param(lambda s: s.update(items="items"), id="items-string"),
    pytest.param(lambda s: s.update(items=[item(image_id=i) for i in range(1, 202)]),
                 id="items-201"),
    pytest.param(lambda s: s.update(items=["item"]), id="item-not-object"),
    pytest.param(lambda s: s.update(items=[{"image_id": 1}]), id="item-missing-keys"),
    pytest.param(lambda s: s.update(items=[item(extra=1)]), id="item-extra-key"),
    pytest.param(lambda s: s["items"][0].pop("crop"), id="item-missing-crop"),
    pytest.param(lambda s: s["items"][0].update(image_id=0), id="image-id-0"),
    pytest.param(lambda s: s["items"][0].update(image_id=-1), id="image-id-negative"),
    pytest.param(lambda s: s["items"][0].update(image_id=True), id="image-id-bool"),
    pytest.param(lambda s: s["items"][0].update(image_id="1"), id="image-id-string"),
    pytest.param(lambda s: s["items"][0].update(image_id=1.0), id="image-id-float"),
    pytest.param(lambda s: s.update(items=[item(image_id=1), item(image_id=1)]),
                 id="duplicate-image-id"),
    pytest.param(lambda s: s["items"][0].update(x=1_000_001), id="item-x-too-large"),
    pytest.param(lambda s: s["items"][0].update(y=-1_000_001), id="item-y-too-small"),
    pytest.param(lambda s: s["items"][0].update(x="0"), id="item-x-string"),
    pytest.param(lambda s: s["items"][0].update(x=float("nan")), id="item-x-nan"),
    pytest.param(lambda s: s["items"][0].update(y=float("inf")), id="item-y-inf"),
    pytest.param(lambda s: s["items"][0].update(width=0), id="width-0"),
    pytest.param(lambda s: s["items"][0].update(width=100_001), id="width-too-large"),
    pytest.param(lambda s: s["items"][0].update(width=True), id="width-bool"),
    pytest.param(lambda s: s["items"][0].update(height=0), id="height-0"),
    pytest.param(lambda s: s["items"][0].update(height=100_001), id="height-too-large"),
    pytest.param(lambda s: s["items"][0].update(rotation=3601), id="rotation-too-large"),
    pytest.param(lambda s: s["items"][0].update(rotation=-3601), id="rotation-too-small"),
    pytest.param(lambda s: s["items"][0].update(rotation="0"), id="rotation-string"),
    pytest.param(lambda s: s["items"][0].update(flip_x=1), id="flip-x-int"),
    pytest.param(lambda s: s["items"][0].update(flip_x="false"), id="flip-x-string"),
    pytest.param(lambda s: s["items"][0].update(grayscale=None), id="grayscale-null"),
    pytest.param(lambda s: s["items"][0].update(opacity=-0.01), id="opacity-too-small"),
    pytest.param(lambda s: s["items"][0].update(opacity=1.01), id="opacity-too-large"),
    pytest.param(lambda s: s["items"][0].update(opacity="0.5"), id="opacity-string"),
    pytest.param(lambda s: s["items"][0].update(opacity=False), id="opacity-bool"),
    pytest.param(lambda s: s["items"][0].update(crop=[]), id="crop-array"),
    pytest.param(lambda s: s["items"][0].update(crop=crop(extra=1)), id="crop-extra-key"),
    pytest.param(lambda s: s["items"][0].update(crop={"x": 0, "y": 0, "width": 0.5}),
                 id="crop-missing-key"),
    pytest.param(lambda s: s["items"][0].update(crop=crop(x=-0.01)), id="crop-x-negative"),
    pytest.param(lambda s: s["items"][0].update(crop=crop(x=1.01)), id="crop-x-too-large"),
    pytest.param(lambda s: s["items"][0].update(crop=crop(y=-0.01)), id="crop-y-negative"),
    pytest.param(lambda s: s["items"][0].update(crop=crop(y=1.01)), id="crop-y-too-large"),
    pytest.param(lambda s: s["items"][0].update(crop=crop(width=0)), id="crop-width-0"),
    pytest.param(lambda s: s["items"][0].update(crop=crop(width=1.01)), id="crop-width-too-large"),
    pytest.param(lambda s: s["items"][0].update(crop=crop(height=0)), id="crop-height-0"),
    pytest.param(lambda s: s["items"][0].update(crop=crop(height=1.01)),
                 id="crop-height-too-large"),
    pytest.param(lambda s: s["items"][0].update(crop=crop(x=0.6, width=0.5)),
                 id="crop-x-plus-width"),
    pytest.param(lambda s: s["items"][0].update(crop=crop(y=0.6, height=0.5)),
                 id="crop-y-plus-height"),
    pytest.param(lambda s: s["items"][0].update(crop=crop(width="0.5")), id="crop-width-string"),
    pytest.param(lambda s: s["items"][0].update(crop=crop(width=True)), id="crop-width-bool"),
    pytest.param(lambda s: s["items"][0].update(crop=crop(x=float("nan"))), id="crop-x-nan"),
    pytest.param(lambda s: s.update(notes={}), id="notes-object"),
    pytest.param(lambda s: s.update(notes="notes"), id="notes-string"),
    pytest.param(lambda s: s.update(notes=[note(id=f"n{i}") for i in range(101)]),
                 id="notes-101"),
    pytest.param(lambda s: s.update(notes=["note"]), id="note-not-object"),
    pytest.param(lambda s: s.update(notes=[{"id": "n"}]), id="note-missing-keys"),
    pytest.param(lambda s: s.update(notes=[note(extra=1)]), id="note-extra-key"),
    pytest.param(lambda s: s["notes"][0].update(id=""), id="note-id-empty"),
    pytest.param(lambda s: s["notes"][0].update(id="x" * 65), id="note-id-too-long"),
    pytest.param(lambda s: s["notes"][0].update(id=123), id="note-id-int"),
    pytest.param(lambda s: s["notes"][0].update(id=None), id="note-id-null"),
    pytest.param(lambda s: s.update(notes=[note(id="dup"), note(id="dup")]),
                 id="duplicate-note-id"),
    pytest.param(lambda s: s["notes"][0].update(x=1_000_001), id="note-x-too-large"),
    pytest.param(lambda s: s["notes"][0].update(y=-1_000_001), id="note-y-too-small"),
    pytest.param(lambda s: s["notes"][0].update(x=float("nan")), id="note-x-nan"),
    pytest.param(lambda s: s["notes"][0].update(text="x" * 2001), id="note-text-too-long"),
    pytest.param(lambda s: s["notes"][0].update(text=5), id="note-text-int"),
    pytest.param(lambda s: s["notes"][0].update(text=None), id="note-text-null"),
    pytest.param(lambda s: s["notes"][0].update(color="red"), id="color-name"),
    pytest.param(lambda s: s["notes"][0].update(color="#12345"), id="color-too-short"),
    pytest.param(lambda s: s["notes"][0].update(color="#1234567"), id="color-too-long"),
    pytest.param(lambda s: s["notes"][0].update(color="#gggggg"), id="color-non-hex"),
    pytest.param(lambda s: s["notes"][0].update(color=123456), id="color-int"),
    pytest.param(lambda s: s["notes"][0].update(font_size=7), id="font-size-too-small"),
    pytest.param(lambda s: s["notes"][0].update(font_size=201), id="font-size-too-large"),
    pytest.param(lambda s: s["notes"][0].update(font_size="12"), id="font-size-string"),
    pytest.param(lambda s: s["notes"][0].update(font_size=True), id="font-size-bool"),
    pytest.param(lambda s: s["notes"][0].update(font_size=float("inf")), id="font-size-inf"),
]


@pytest.mark.parametrize("mutate", BAD_SCENES)
def test_invalid_scene_is_rejected(mutate):
    with pytest.raises(ValueError):
        validate_scene(changed(mutate))


# ---------------------------------------------------------------- store fixtures

@pytest.fixture
def database(data_dir):
    db.init_db()
    return data_dir


def seed_project(conn, name="参考板"):
    return conn.execute("INSERT INTO projects(name, brief) VALUES(?, '')", (name,)).lastrowid


def seed_image(conn, image_id):
    conn.execute("INSERT OR IGNORE INTO posts(id, platform) VALUES (1, 'local')")
    conn.execute(
        "INSERT INTO images(id, post_id, position, sha256, file_path, width, height)"
        " VALUES(?, 1, ?, ?, ?, 8, 8)",
        (image_id, image_id, f"sha-{image_id}", f"images/{image_id}.png"))


@pytest.fixture
def seeded(database):
    conn = db.connect()
    try:
        project_id = seed_project(conn)
        seed_image(conn, 1)
        seed_image(conn, 2)
    finally:
        conn.close()
    return {"project_id": project_id, "image_ids": (1, 2)}


def scene_rows():
    conn = db.connect()
    try:
        return [tuple(row) for row in conn.execute(
            "SELECT project_id, revision, scene_json FROM project_pip_scenes ORDER BY project_id")]
    finally:
        conn.close()


def empty_scene_view():
    return {"revision": 0, "updated_at": None, "scene": None}


# ---------------------------------------------------------------- store behaviour

def test_get_scene_without_row_returns_zero(seeded):
    assert PipSceneStore().get_scene(seeded["project_id"]) == empty_scene_view()


def test_save_increments_revision_persists_and_reloads(seeded):
    project_id = seeded["project_id"]
    store = PipSceneStore()
    first = scene_for([1, 2])
    first["notes"][0]["text"] = "便签"
    saved = store.save_scene(project_id, 0, first)
    assert saved["revision"] == 1
    assert saved["scene"] == first
    assert isinstance(saved["updated_at"], str) and saved["updated_at"]

    rows = scene_rows()
    assert len(rows) == 1 and rows[0][0] == project_id and rows[0][1] == 1
    assert rows[0][2] == json.dumps(first, ensure_ascii=False, separators=(",", ":"))
    assert "便签" in rows[0][2]

    # A brand-new connection (new store) sees the committed row.
    loaded = PipSceneStore().get_scene(project_id)
    assert loaded == {"revision": 1, "updated_at": saved["updated_at"], "scene": first}

    second = scene_for([2])
    saved_again = store.save_scene(project_id, 1, second)
    assert saved_again["revision"] == 2
    assert saved_again["scene"] == second
    assert PipSceneStore().get_scene(project_id)["scene"] == second
    assert len(scene_rows()) == 1


def test_stale_revision_conflicts_and_keeps_stored_scene(seeded):
    project_id = seeded["project_id"]
    store = PipSceneStore()
    stored = scene_for([1])
    store.save_scene(project_id, 0, stored)
    with pytest.raises(RevisionConflict) as excinfo:
        store.save_scene(project_id, 0, scene_for([2]))
    assert excinfo.value.current_revision == 1
    assert excinfo.value.current_scene == stored
    assert store.get_scene(project_id)["scene"] == stored
    assert scene_rows()[0][1] == 1


def test_first_save_requires_revision_zero(seeded):
    store = PipSceneStore()
    with pytest.raises(RevisionConflict) as excinfo:
        store.save_scene(seeded["project_id"], 3, scene_for([1]))
    assert excinfo.value.current_revision == 0
    assert excinfo.value.current_scene is None
    assert store.get_scene(seeded["project_id"]) == empty_scene_view()
    assert scene_rows() == []


def test_project_not_found(seeded):
    store = PipSceneStore()
    with pytest.raises(ProjectNotFound):
        store.get_scene(999)
    with pytest.raises(ProjectNotFound):
        store.save_scene(999, 0, scene_for([1]))
    assert scene_rows() == []


def test_missing_image_is_rejected_without_writing(seeded):
    project_id = seeded["project_id"]
    store = PipSceneStore()
    with pytest.raises(ImageNotFound):
        store.save_scene(project_id, 0, scene_for([1, 999]))
    assert store.get_scene(project_id) == empty_scene_view()
    assert scene_rows() == []


def test_invalid_scene_is_rejected_before_project_lookup(seeded):
    with pytest.raises(ValueError):
        PipSceneStore().save_scene(999, 0, scene(version=2))
    assert scene_rows() == []


def test_store_accepts_injected_plain_sqlite_connection(seeded):
    project_id = seeded["project_id"]

    def connect():
        return sqlite3.connect(config.db_path(), isolation_level=None)

    store = PipSceneStore(connect=connect)
    saved = store.save_scene(project_id, 0, scene_for([1]))
    assert saved["revision"] == 1
    assert store.get_scene(project_id)["scene"] == scene_for([1])


@pytest.mark.parametrize("revision", [-1, True, "0", 1.0])
def test_store_rejects_invalid_revision(seeded, revision):
    with pytest.raises(ValueError):
        PipSceneStore().save_scene(seeded["project_id"], revision, scene_for([1]))
    assert scene_rows() == []


def test_hand_edited_row_raises_invalid_stored_scene(seeded):
    project_id = seeded["project_id"]
    store = PipSceneStore()
    store.save_scene(project_id, 0, scene_for([1]))
    conn = db.connect()
    try:
        conn.execute("UPDATE project_pip_scenes SET scene_json='{\"version\": 2}' WHERE project_id=?",
                     (project_id,))
    finally:
        conn.close()
    with pytest.raises(InvalidStoredScene):
        store.get_scene(project_id)
    with pytest.raises(InvalidStoredScene):
        store.save_scene(project_id, 1, scene_for([2]))


def test_sql_failure_rolls_back_and_maps_to_persistence_error(seeded):
    project_id = seeded["project_id"]
    store = PipSceneStore()
    store.save_scene(project_id, 0, scene_for([1]))
    before = store.get_scene(project_id)
    conn = db.connect()
    try:
        conn.execute("""CREATE TRIGGER fail_pip_scene BEFORE UPDATE ON project_pip_scenes
                        BEGIN SELECT RAISE(ABORT, 'Bearer secret-raw-database-error'); END""")
    finally:
        conn.close()
    with pytest.raises(PersistenceError):
        store.save_scene(project_id, 1, scene_for([2]))
    assert PipSceneStore().get_scene(project_id) == before
    assert scene_rows()[0][1] == 1


# ---------------------------------------------------------------- HTTP API

@pytest.fixture
def api(data_dir, backend_options):
    db.init_db()
    conn = db.connect()
    try:
        project_id = seed_project(conn)
        seed_image(conn, 1)
        seed_image(conn, 2)
    finally:
        conn.close()
    app = FastAPI()
    app.include_router(pip_scene_router())
    with TestClient(app, backend_options=backend_options) as client:
        yield client, project_id


def scene_url(project_id):
    return f"/api/projects/{project_id}/pip-scene"


def test_get_without_saved_scene_returns_200_empty(api):
    client, project_id = api
    response = client.get(scene_url(project_id))
    assert response.status_code == 200
    assert response.json() == empty_scene_view()


def test_put_then_get_round_trips(api):
    client, project_id = api
    saved_scene = scene_for([1, 2])
    response = client.put(scene_url(project_id), json={"revision": 0, "scene": saved_scene})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["revision"] == 1
    assert body["scene"] == saved_scene
    assert body["updated_at"]

    loaded = client.get(scene_url(project_id)).json()
    assert loaded["revision"] == 1
    assert loaded["scene"] == saved_scene
    assert loaded["updated_at"] == body["updated_at"]

    second = client.put(scene_url(project_id), json={"revision": 1, "scene": scene_for([2])})
    assert second.status_code == 200
    assert second.json()["revision"] == 2
    assert client.get(scene_url(project_id)).json()["scene"] == scene_for([2])


def test_put_stale_revision_returns_409_with_current_scene(api):
    client, project_id = api
    stored = scene_for([1])
    assert client.put(scene_url(project_id), json={"revision": 0, "scene": stored}).status_code == 200
    conflict = client.put(scene_url(project_id), json={"revision": 0, "scene": scene_for([2])})
    assert conflict.status_code == 409, conflict.text
    detail = conflict.json()["detail"]
    assert detail["code"] == "revision_conflict"
    assert detail["current_revision"] == 1
    assert detail["current_scene"] == stored
    assert client.get(scene_url(project_id)).json()["scene"] == stored


@pytest.mark.parametrize("bad_scene", [
    scene(version=2),
    scene(view={"x": 0, "y": 0, "scale": 100}),
    scene(items=[item(image_id=1, extra=1)]),
    scene(notes=[note(color="red")]),
])
def test_put_invalid_scene_422(api, bad_scene):
    client, project_id = api
    response = client.put(scene_url(project_id), json={"revision": 0, "scene": bad_scene})
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "invalid_scene"
    assert client.get(scene_url(project_id)).json() == empty_scene_view()


def test_put_invalid_scene_wins_over_missing_project(api):
    client, _ = api
    response = client.put(scene_url(999), json={"revision": 0, "scene": scene(version=2)})
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_scene"


def test_put_missing_project_404(api):
    client, _ = api
    response = client.put(scene_url(999), json={"revision": 0, "scene": scene_for([1])})
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "project_not_found"


def test_put_missing_image_404_and_does_not_write(api):
    client, project_id = api
    response = client.put(scene_url(project_id), json={"revision": 0, "scene": scene_for([1, 999])})
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "image_not_found"
    assert client.get(scene_url(project_id)).json() == empty_scene_view()


def test_get_missing_project_404(api):
    client, _ = api
    response = client.get(scene_url(999))
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "project_not_found"


def test_put_extra_body_field_422(api):
    client, project_id = api
    response = client.put(scene_url(project_id),
                          json={"revision": 0, "scene": scene_for([1]), "extra": 1})
    assert response.status_code == 422
    assert client.get(scene_url(project_id)).json() == empty_scene_view()


@pytest.mark.parametrize("revision", [-1, True, "0", 1.5, None])
def test_put_invalid_revision_422(api, revision):
    client, project_id = api
    response = client.put(scene_url(project_id),
                          json={"revision": revision, "scene": scene_for([1])})
    assert response.status_code == 422


def test_put_missing_scene_422(api):
    client, project_id = api
    assert client.put(scene_url(project_id), json={"revision": 0}).status_code == 422


def test_project_id_must_be_positive(api):
    client, _ = api
    assert client.get("/api/projects/0/pip-scene").status_code == 422
    assert client.put("/api/projects/0/pip-scene",
                      json={"revision": 0, "scene": scene_for([1])}).status_code == 422


def test_get_invalid_stored_scene_409(api):
    client, project_id = api
    assert client.put(scene_url(project_id),
                      json={"revision": 0, "scene": scene_for([1])}).status_code == 200
    conn = db.connect()
    try:
        conn.execute("UPDATE project_pip_scenes SET scene_json='not json' WHERE project_id=?",
                     (project_id,))
    finally:
        conn.close()
    response = client.get(scene_url(project_id))
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "invalid_stored_scene"


def test_store_failure_is_503_without_leaking_sql(api):
    client, project_id = api
    conn = db.connect()
    try:
        conn.execute("""CREATE TRIGGER fail_pip_scene_insert BEFORE INSERT ON project_pip_scenes
                        BEGIN SELECT RAISE(ABORT, 'Bearer secret-raw-database-error'); END""")
    finally:
        conn.close()
    response = client.put(scene_url(project_id),
                          json={"revision": 0, "scene": scene_for([1])})
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "pip_scene_store_unavailable"
    assert "secret" not in response.text
    assert client.get(scene_url(project_id)).json() == empty_scene_view()


# ---------------------------------------------------------------- migration

def table_names():
    conn = db.connect()
    try:
        return {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()


def test_fresh_database_has_pip_scene_table(database):
    assert db.SCHEMA_VERSION >= 9
    conn = db.connect()
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    finally:
        conn.close()
    assert "project_pip_scenes" in table_names()


def test_v8_database_migrates_to_current_version_and_keeps_projects(database):
    conn = db.connect()
    try:
        project_id = seed_project(conn, "旧项目")
        conn.execute("DROP TABLE project_pip_scenes")
        conn.execute("PRAGMA user_version = 8")
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 8
        assert "project_pip_scenes" not in {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()

    db.init_db()

    conn = db.connect()
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
        assert [row[1] for row in conn.execute("PRAGMA table_info(project_pip_scenes)")] == [
            "project_id", "revision", "scene_json", "updated_at"]
        assert tuple(conn.execute(
            "SELECT name, brief FROM projects WHERE id=?", (project_id,)).fetchone()) == ("旧项目", "")
    finally:
        conn.close()

    db.init_db()  # idempotent at the current version, no rebuild of either table
    assert "project_pip_scenes" in table_names()
