"""Field-level user annotation overlay (schema v11) and its local search channel.

No paid calls: search tests use the fake query client from test_search_wiring and a
warming local CLIP double; annotations read only the local SQLite database.
"""
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app import annotations, db, importer
from app.embedding.adapter import EmbeddingAdapter
from app.main import create_app
from conftest import png
from test_search_wiring import SETTINGS, FakeQueryFactory, seeded  # noqa: F401


def rows(data_dir, sql, args=()):
    conn = sqlite3.connect(data_dir / "app.db")
    conn.row_factory = sqlite3.Row
    try:
        return [tuple(row) for row in conn.execute(sql, args)]
    finally:
        conn.close()


@pytest.fixture
def library(data_dir):
    db.init_db()
    result = importer.import_images([
        importer.Upload("a.png", png("red")),
        importer.Upload("b.png", png("blue")),
        importer.Upload("c.png", png("green")),
    ])
    return data_dir, [a["id"] for a in result["added"]]


@pytest.fixture
def annotated(library):
    data_dir, ids = library
    annotations.save(ids[0], "现代建筑展板 多栏网格", ["建筑", "展板", "极简"])
    annotations.save(ids[1], "日式便当美食", ["美食", "Poster"])
    annotations.save(ids[2], "建筑摄影立面")  # tags left without override
    return data_dir, ids


# ---------------------------------------------------------------- migration

def make_v9_library(data_dir):
    """A current-shaped library rolled back to v9, without the annotation table."""
    db.init_db()
    conn = db.connect()
    try:
        conn.execute("INSERT INTO posts (id, platform, created_at) "
                     "VALUES (1, 'local', '2024-01-01T00:00:00+09:00')")
        conn.execute("""INSERT INTO images (id, post_id, position, sha256, file_path, width, height,
                                             mime, original_name)
                        VALUES (1, 1, 0, ?, 'images/x.png', 8, 8, 'image/png', 'x.png')""",
                     ("a" * 64,))
        conn.execute("INSERT INTO analyses (image_id, status, description) VALUES (1, 'done', '旧分析')")
        conn.execute("DROP TABLE image_annotations")
        conn.execute("PRAGMA user_version = 9")
    finally:
        conn.close()


def test_v9_library_upgrades_to_current_version_with_backup(data_dir):
    make_v9_library(data_dir)
    db.init_db()
    conn = db.connect()
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
        assert [row[1] for row in conn.execute("PRAGMA table_info(image_annotations)")] == [
            "image_id", "description", "tags_json", "edited_at", "updated_at"]
        assert conn.execute("SELECT COUNT(*) FROM image_annotations").fetchone()[0] == 0
        assert conn.execute("SELECT description FROM analyses WHERE image_id = 1").fetchone()[0] == "旧分析"
        assert conn.execute("SELECT COUNT(*) FROM images").fetchone()[0] == 1
    finally:
        conn.close()
    assert len(list(data_dir.glob("app.db.bak-*-v9"))) == 1


def make_v10_library(data_dir):
    """A v10 library whose old overlay copied AI text into fields the user never edited."""
    db.init_db()
    conn = db.connect()
    try:
        conn.execute("INSERT INTO posts (id, platform, created_at) "
                     "VALUES (1, 'local', '2024-01-01T00:00:00+09:00')")
        for image_id in range(1, 5):
            conn.execute("""INSERT INTO images (id, post_id, position, sha256, file_path,
                                                 width, height, mime, original_name)
                            VALUES (?, 1, ?, ?, ?, 8, 8, 'image/png', ?)""",
                         (image_id, image_id - 1, f"sha{image_id}",
                          f"images/x{image_id}.png", f"x{image_id}.png"))
            conn.execute("INSERT INTO analyses (image_id, status, description, subject) "
                         "VALUES (?, 'done', ?, ?)",
                         (image_id, f"AI 描述 {image_id}",
                          json.dumps(["建筑"], ensure_ascii=False)))
        conn.execute("INSERT INTO image_annotations (image_id, description, tags_json) VALUES (1, ?, ?)",
                     ("AI 描述 1", json.dumps(["建筑"], ensure_ascii=False)))
        conn.execute("INSERT INTO image_annotations (image_id, description, tags_json) VALUES (2, ?, ?)",
                     ("用户自己的描述", json.dumps(["用户标签"], ensure_ascii=False)))
        conn.execute("INSERT INTO image_annotations (image_id, description, tags_json) VALUES (3, ?, ?)",
                     ("AI 描述 3", json.dumps(["用户标签"], ensure_ascii=False)))
        # image 4: the AI subject is malformed -> the tag must be kept (bad JSON != equal).
        conn.execute("UPDATE analyses SET subject = 'not json' WHERE image_id = 4")
        conn.execute("INSERT INTO image_annotations (image_id, description, tags_json) VALUES (4, NULL, ?)",
                     (json.dumps(["建筑"], ensure_ascii=False),))
        conn.execute("PRAGMA user_version = 10")
    finally:
        conn.close()


def test_v10_to_v11_cleans_only_ai_copies_and_backs_up(data_dir):
    make_v10_library(data_dir)
    analyses_before = rows(data_dir, "SELECT * FROM analyses ORDER BY id")
    db.init_db()
    assert rows(data_dir, "SELECT * FROM analyses ORDER BY id") == analyses_before

    conn = db.connect()
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
        stored = {row[0]: (row[1], row[2]) for row in conn.execute(
            "SELECT image_id, description, tags_json FROM image_annotations")}
    finally:
        conn.close()
    assert 1 not in stored  # both fields were AI copies -> row deleted
    assert stored[2] == ("用户自己的描述", json.dumps(["用户标签"], ensure_ascii=False))
    assert stored[3] == (None, json.dumps(["用户标签"], ensure_ascii=False))
    assert stored[4] == (None, json.dumps(["建筑"], ensure_ascii=False))
    assert len(list(data_dir.glob("app.db.bak-*-v10"))) == 1


def test_fresh_database_has_annotation_table(data_dir):
    db.init_db()
    conn = db.connect()
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "image_annotations" in tables
    finally:
        conn.close()


# ---------------------------------------------------------------- field-level get / save

def test_get_is_none_then_save_both_fields_round_trips(library):
    data_dir, ids = library
    assert annotations.get(ids[0]) is None
    saved = annotations.save(ids[0], "  用户描述  ", ["  标签一 ", "标签二", "标签一", "  "])
    assert saved["description"] == "用户描述"
    assert saved["tags"] == ["标签一", "标签二"]
    assert saved["edited"] is True and saved["edited_at"]
    assert annotations.get(ids[0]) == saved
    assert rows(data_dir, "SELECT image_id FROM image_annotations") == [(ids[0],)]


def test_save_only_description_leaves_tags_without_override(library):
    _, ids = library
    saved = annotations.save(ids[0], "只有描述")
    assert saved["description"] == "只有描述" and saved["tags"] is None


def test_save_only_tags_leaves_description_without_override(library):
    _, ids = library
    saved = annotations.save(ids[0], tags=["只有标签"])
    assert saved["tags"] == ["只有标签"] and saved["description"] is None


def test_partial_update_keeps_the_other_field(library):
    _, ids = library
    annotations.save(ids[0], "原描述", ["原标签"])
    assert annotations.save(ids[0], "新描述")["tags"] == ["原标签"]
    loaded = annotations.save(ids[0], tags=["新标签"])
    assert loaded["description"] == "新描述" and loaded["tags"] == ["新标签"]


def test_omitted_fields_do_not_touch_the_stored_row(library):
    data_dir, ids = library
    annotations.save(ids[0], "描述", ["标签"])
    with sqlite3.connect(data_dir / "app.db") as conn:
        conn.execute("UPDATE image_annotations SET edited_at='2000-01-01', updated_at='2000-01-01' WHERE image_id=?", (ids[0],))
    saved = annotations.get(ids[0])
    assert annotations.save(ids[0]) == saved
    assert annotations.save(ids[0], tags=annotations.UNSET)["tags"] == ["标签"]


def test_explicit_none_clears_one_field_then_deletes_when_both_are_none(library):
    data_dir, ids = library
    annotations.save(ids[0], "描述", ["标签"])
    loaded = annotations.save(ids[0], description=None)
    assert loaded["description"] is None and loaded["tags"] == ["标签"]
    assert rows(data_dir, "SELECT COUNT(*) FROM image_annotations") == [(1,)]
    assert annotations.save(ids[0], tags=None) is None
    assert annotations.get(ids[0]) is None
    assert rows(data_dir, "SELECT COUNT(*) FROM image_annotations") == [(0,)]
    assert annotations.save(ids[1], description=None) is None  # clearing fields never creates a row


def test_empty_string_and_empty_list_are_explicit_clears(library):
    _, ids = library
    loaded = annotations.save(ids[0], "", [])
    assert loaded["description"] == "" and loaded["tags"] == []
    assert annotations.get(ids[0])["description"] == ""
    assert annotations.get(ids[0])["tags"] == []
    assert annotations.save(ids[0], description=None, tags=None) is None


@pytest.mark.parametrize("kwargs", [
    {"description": "字" * 2001},
    {"description": 5},
    {"tags": "标签"},
    {"tags": {"a": 1}},
    {"tags": ["ok", 5]},
    {"tags": ["字" * 61]},
    {"tags": [f"标签{i}" for i in range(21)]},
])
def test_invalid_annotation_values_raise_and_write_nothing(library, kwargs):
    data_dir, ids = library
    with pytest.raises(ValueError):
        annotations.save(ids[0], **kwargs)
    assert rows(data_dir, "SELECT COUNT(*) FROM image_annotations") == [(0,)]


def test_missing_image_raises_key_error(library):
    with pytest.raises(KeyError):
        annotations.save(99999, "描述")


def test_reanalysis_never_overwrites_annotation_or_embeddings(library):
    data_dir, ids = library
    # The user edits only tags: description has no override, so it must fall back to AI.
    annotations.save(ids[0], tags=["用户标签"])
    analyses_before = rows(data_dir, "SELECT * FROM analyses ORDER BY id")
    embeddings_before = rows(data_dir, "SELECT * FROM embeddings ORDER BY id")
    assert rows(data_dir, "SELECT * FROM analyses ORDER BY id") == analyses_before
    assert rows(data_dir, "SELECT * FROM embeddings ORDER BY id") == embeddings_before

    conn = db.connect()
    try:
        conn.execute("UPDATE analyses SET description = 'AI 重新生成的描述', status = 'done', "
                     "updated_at = datetime('now') WHERE image_id = ?", (ids[0],))
    finally:
        conn.close()
    loaded = annotations.get(ids[0])
    assert loaded["description"] is None        # frontend falls back to the new AI description
    assert loaded["tags"] == ["用户标签"]

    annotations.save(ids[1], "用户保留描述", ["用户标签"])
    conn = db.connect()
    try:
        conn.execute("UPDATE analyses SET description = 'AI 又一次描述' WHERE image_id = ?", (ids[1],))
    finally:
        conn.close()
    assert annotations.get(ids[1])["description"] == "用户保留描述"


# ---------------------------------------------------------------- local search channel

def test_search_matches_tag_and_description_scores(annotated):
    _, ids = annotated
    assert annotations.search_matches("展板") == [{"image_id": ids[0], "score": 4.0}]
    assert annotations.search_matches("现代 建筑") == [
        {"image_id": ids[0], "score": 4.0},   # tag 建筑 is contained in the query
        {"image_id": ids[2], "score": 2.0},   # description token 建筑
    ]
    assert annotations.search_matches("多栏") == [{"image_id": ids[0], "score": 2.0}]


def test_search_matches_counts_each_matched_tag(annotated):
    _, ids = annotated
    assert annotations.search_matches("建筑 展板 极简") == [
        {"image_id": ids[0], "score": 6.0},   # 3.0 + three matched tags
        {"image_id": ids[2], "score": 2.0},   # description token only
    ]


def test_search_matches_is_casefolded(annotated):
    _, ids = annotated
    assert annotations.search_matches("POSTER") == [{"image_id": ids[1], "score": 4.0}]


def test_search_matches_long_chinese_query_uses_bigrams(library):
    _, ids = library
    annotations.save(ids[0], "陶土色海报排版")
    for query in ("海报", "海报排版", "想要海报那种排版"):
        assert annotations.search_matches(query) == [{"image_id": ids[0], "score": 2.0}]
    assert annotations.search_matches("完全无关") == []


def test_search_matches_tag_bigrams_in_long_query(library):
    _, ids = library
    annotations.save(ids[0], tags=["陶土色海报排版参考"])
    assert annotations.search_matches("海报") == [{"image_id": ids[0], "score": 4.0}]
    assert annotations.search_matches("想要海报那种排版") == [{"image_id": ids[0], "score": 4.0}]


def test_search_matches_order_limit_and_no_match(annotated):
    _, ids = annotated
    for image_id in ids:
        annotations.save(image_id, "共同词", None)
    assert annotations.search_matches("共同词", limit=2) == [
        {"image_id": ids[0], "score": 2.0},
        {"image_id": ids[1], "score": 2.0},
    ]
    assert [m["image_id"] for m in annotations.search_matches("共同词")] == sorted(ids)
    assert annotations.search_matches("建") == []  # one-character queries do not scan text
    assert annotations.search_matches("完全不存在的词") == []


# ---------------------------------------------------------------- HTTP API

@pytest.fixture
def client(data_dir, backend_options):
    return TestClient(create_app(), backend_options=backend_options)


def upload(client):
    response = client.post("/api/images/import",
                           files=[("files", ("a.png", png("red"), "image/png"))])
    assert response.status_code == 200, response.text
    return response.json()["added"][0]["id"]


def test_image_detail_and_field_level_annotation_endpoints(client):
    image_id = upload(client)
    url = f"/api/images/{image_id}/annotation"
    assert client.get(f"/api/images/{image_id}").json()["annotation"] is None

    saved = client.put(url, json={"description": "用户描述", "tags": ["标签一", "标签二"]})
    assert saved.status_code == 200, saved.text
    annotation = saved.json()["annotation"]
    assert annotation["description"] == "用户描述"
    assert annotation["tags"] == ["标签一", "标签二"]
    assert annotation["edited"] is True and annotation["edited_at"]
    assert client.get(f"/api/images/{image_id}").json()["annotation"] == annotation

    # Only the description key: tags stay untouched.
    only_description = client.put(url, json={"description": "只改描述"}).json()["annotation"]
    assert only_description["description"] == "只改描述"
    assert only_description["tags"] == ["标签一", "标签二"]
    # Only tags = explicit null: only tags are cleared.
    only_tags_cleared = client.put(url, json={"tags": None}).json()["annotation"]
    assert only_tags_cleared["tags"] is None
    assert only_tags_cleared["description"] == "只改描述"
    # Empty string is an explicit clear, stored as "" and NOT falling back to AI.
    empty = client.put(url, json={"description": ""}).json()["annotation"]
    assert empty["description"] == ""
    # Neither key -> invalid.
    none = client.put(url, json={})
    assert none.status_code == 422 and none.json()["detail"]["code"] == "invalid_annotation"

    assert client.put(url, json={"description": "x", "extra": 1}).status_code == 422
    assert client.put(url, json={"tags": [1, 2]}).status_code == 422
    invalid = client.put(url, json={"description": "字" * 2001})
    assert invalid.status_code == 422
    assert invalid.json()["detail"]["code"] == "invalid_annotation"

    missing = client.put("/api/images/99999/annotation", json={"description": "x"})
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "image_not_found"

    restored = client.put(url, json={"description": None, "tags": None})
    assert restored.status_code == 200 and restored.json()["annotation"] is None
    assert client.get(f"/api/images/{image_id}").json()["annotation"] is None


class WarmingSimilarity:
    """Local CLIP double: warming means the text route is not queried at all."""
    warming = True

    def start(self):
        pass

    def shutdown(self):
        pass

    def status(self):
        return {"warming": True}

    def search_text(self, *args, **kwargs):
        raise AssertionError("search must not use the local clip text tower in this test")


@pytest.fixture
def search_client(seeded, backend_options):  # noqa: F811
    factory = FakeQueryFactory()

    def forbidden(_settings):
        raise AssertionError("search triggered image embedding")

    adapter = EmbeddingAdapter(SETTINGS, client_factory=forbidden)
    client = TestClient(create_app(embedder=adapter, search_client_factory=factory,
                                   image_similarity=WarmingSimilarity()),
                        backend_options=backend_options)
    return client, factory


def test_search_prepends_annotation_hit_without_extra_embedding(search_client):
    client, factory = search_client
    baseline = client.post("/api/search", json={"query": "独特标注词"}).json()
    assert baseline["status"] == "ok"
    assert 1 not in [item["image_id"] for item in baseline["items"]]

    annotations.save(1, "独特标注词 多栏展板", ["独特标注词"])
    response = client.post("/api/search", json={"query": "独特标注词"})
    assert response.status_code == 200, response.text
    body = response.json()
    ids = [item["image_id"] for item in body["items"]]
    assert ids[0] == 1 and ids.count(1) == 1
    assert body["items"][0]["score"] >= 3.0
    assert body["items"][0]["url"].startswith("/images/")
    assert len(factory.queries) == 2  # one fake embedding per search; annotation adds none


def test_search_survives_annotation_channel_failure(search_client, monkeypatch):
    client, _ = search_client

    def boom(*args, **kwargs):
        raise RuntimeError("annotation db down")

    monkeypatch.setattr(annotations, "search_matches", boom)
    response = client.post("/api/search", json={"query": "展板"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "ok" and body["items"]


def test_index_empty_still_answers_from_local_annotations(search_client):
    client, _ = search_client
    annotations.save(1, "独特标注词", ["独特标注词"])
    conn = db.connect()
    try:
        conn.execute("DELETE FROM embeddings")
    finally:
        conn.close()
    body = client.post("/api/search", json={"query": "独特标注词"}).json()
    assert body["status"] == "ok" and body["degraded"] is True
    assert [item["image_id"] for item in body["items"]] == [1]
    # Nothing local matches either -> the existing empty-index response is unchanged.
    empty = client.post("/api/search", json={"query": "完全无关"}).json()
    assert empty == {"status": "index_empty", "items": []}


def test_search_finds_post_text_without_model_and_keeps_manual_first(search_client, monkeypatch):
    client, factory = search_client
    monkeypatch.setenv("WOMINMIN_DEMO", "1")
    monkeypatch.setenv("WOMINMIN_OFFLINE_DEMO", "1")
    conn = db.connect()
    try:
        post_id = conn.execute("SELECT post_id FROM images WHERE id=1").fetchone()["post_id"]
        conn.execute("UPDATE posts SET title=?,description=?,author_name=? WHERE id=?",
                     ("海岛画册", "收录作品 #纸上城市[话题]#", "Studio Atlas", post_id))
        conn.commit()
    finally:
        conn.close()

    for query in ("海岛画册", "纸上城市", "studio atlas"):
        items = client.post("/api/search", json={"query": query}).json()["items"]
        assert items[0]["image_id"] == 1
    annotations.save(2, None, ["纸上城市"])
    items = client.post("/api/search", json={"query": "纸上城市"}).json()["items"]
    assert [item["image_id"] for item in items[:2]] == [2, 1]
    assert factory.queries == []
