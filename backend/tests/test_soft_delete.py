"""P0-3 软删除：所有读路径看不到 deleted 图片/项目，清标记后完整恢复。

只使用 data_dir 临时目录；不碰 .run/public-demo 的真库，也不写任何生产数据。
"""
import hashlib

import pytest
from fastapi.testclient import TestClient

from app import annotations, db, importer, vision
from app.embedding import repository
from app.embedding.adapter import EmbeddingAdapter
from app.embedding.client import EmbeddingSettings
from app.main import create_app
from app.similarity import ImageSimilarity
from conftest import png
from scripts.restore_deleted import main as restore_main

NO_KEY = EmbeddingSettings("", "")


class FakeEncoder:
    """Chinese-CLIP replacement: deterministic 512d vectors, never loads a model."""

    identity = "fake-clip-512-v1"

    def encode_bytes(self, data):
        digest = hashlib.sha256(data).digest()
        values = [digest[index % len(digest)] / 255.0 for index in range(512)]
        return values

    def encode_paths(self, paths):
        return [self.encode_bytes(path.read_bytes()) for path in paths]

    def encode_texts(self, texts):
        return [[1.0] + [0.0] * 511 for _ in texts]

    def warmup(self):
        pass


@pytest.fixture
def env(data_dir, monkeypatch, backend_options):
    def forbidden(*args, **kwargs):
        raise AssertionError("P0-3 soft-delete test must not call AI")

    monkeypatch.setattr(vision, "analyze", forbidden)
    db.init_db()
    ids = [row["id"] for row in importer.import_images([
        importer.Upload("a.png", png("red")),
        importer.Upload("b.png", png("blue")),
        importer.Upload("c.png", png("green")),
    ])["added"]]
    similar = ImageSimilarity(encoder=FakeEncoder(), interval=.02)
    while similar.index_pending():
        pass  # build the local index synchronously; no model download
    adapter = EmbeddingAdapter(NO_KEY)
    app = create_app(embedder=adapter, image_similarity=similar)
    with TestClient(app, backend_options=backend_options) as client:
        yield {"client": client, "ids": ids, "similar": similar}


def test_v14_library_migrates_to_soft_delete_columns_idempotently(data_dir):
    db.init_db()
    conn = db.connect()
    try:
        conn.execute("INSERT INTO posts(id,platform) VALUES(1,'local')")
        conn.execute(
            """INSERT INTO images(id,post_id,position,sha256,file_path,width,height,original_name)
               VALUES(1,1,0,?,?,8,8,'x.png')""", ("a" * 64, "images/x.png"))
        conn.execute("INSERT INTO projects(id,name,brief) VALUES(1,'老项目','旧 brief')")
        conn.execute("ALTER TABLE images DROP COLUMN deleted_at")
        conn.execute("ALTER TABLE projects DROP COLUMN deleted_at")
        conn.execute("PRAGMA user_version = 14")
    finally:
        conn.close()

    db.init_db()  # 老库直接起：补列 + 升到当前版本
    db.init_db()  # 幂等：第二次不重复迁移、不再备份

    conn = db.connect()
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION == 15
        assert db.has_column(conn, "images", "deleted_at")
        assert db.has_column(conn, "projects", "deleted_at")
        assert conn.execute("SELECT name FROM projects WHERE id=1").fetchone()[0] == "老项目"
        assert conn.execute("SELECT file_path FROM images WHERE id=1").fetchone()[0] == "images/x.png"
    finally:
        conn.close()
    assert [row["id"] for row in importer.list_images()] == [1]  # 迁移后老数据照常可见
    assert len(list(data_dir.glob("app.db.bak-*"))) == 1  # 迁移前自动留了一份备份


def test_image_soft_delete_hides_every_read_path_and_restore_brings_it_back(env):
    client, ids, similar = env["client"], env["ids"], env["similar"]
    gone, keep = ids[0], ids[1]
    annotations.save(gone, tags=["海报排版"])
    annotations.save(keep, tags=["海报排版"])
    project = client.post("/api/projects", json={"name": "项目"}).json()
    assert client.post(f"/api/projects/{project['id']}/pins",
                       json={"image_id": gone, "purposes": ["layout"]}).status_code == 200
    assert client.post(f"/api/projects/{project['id']}/pins",
                       json={"image_id": keep, "purposes": ["style"]}).status_code == 200
    deleted_project = client.post("/api/projects", json={"name": "待删项目"}).json()

    assert gone in {row["id"] for row in client.get("/api/images").json()}
    assert gone in client.get(f"/api/projects/{project['id']}").json()["image_ids"]

    response = client.delete(f"/api/images/{gone}")
    assert response.status_code == 200 and response.json() == {"id": gone, "deleted": True}
    assert client.delete(f"/api/images/{gone}").status_code == 200  # 幂等
    assert client.delete("/api/images/999999").status_code == 404

    # 图片列表 / 详情
    assert gone not in {row["id"] for row in client.get("/api/images").json()}
    assert client.get(f"/api/images/{gone}").status_code == 404
    assert client.get(f"/api/images/{keep}").status_code == 200
    # 项目详情 / pins
    detail = client.get(f"/api/projects/{project['id']}").json()
    assert gone not in detail["image_ids"]
    assert gone not in {pin["image_id"] for pin in detail["pins"]}
    assert keep in detail["image_ids"]
    assert gone not in {row["image_id"] for row in
                        client.get(f"/api/projects/{project['id']}/pins").json()["images"]}
    # 待确认 / 参考标签 / 决策
    assert gone not in {row["id"] for row in client.get("/api/reference/review").json()["items"]}
    labels = client.get("/api/reference-labels").json()
    assert gone not in {row["id"] for row in labels["items"]}
    assert client.post("/api/reference/decisions",
                       json={"ids": [gone], "is_reference": True}).status_code == 404
    # 统计
    assert sum(row["count"] for row in client.get("/api/stats/daily").json()) == 2
    assert sum(row["count"] for row in client.get("/api/stats/platforms").json()) == 2
    assert client.get("/api/stats/library").json()["collections"] == 2
    # 写接口按「不存在」处理
    assert client.put(f"/api/images/{gone}/annotation",
                      json={"tags": ["x"]}).status_code == 404
    assert client.put(f"/api/images/{gone}/title",
                      json={"title": "x"}).status_code == 404
    assert client.post(f"/api/images/{gone}/confirm-analysis").status_code == 404
    # 本地相似：索引里没有它，用它做查询也 404
    assert gone not in similar.valid_ids()
    assert client.get(f"/api/images/{gone}/similar").status_code == 404
    assert gone not in {item["image_id"] for item in
                        client.get(f"/api/images/{keep}/similar").json()["items"]}
    # 搜索的注释/帖子字面通道也不返回它
    matches = {match["image_id"] for match in annotations.search_matches("海报")}
    assert gone not in matches and keep in matches
    assert client.post("/api/search", json={"query": "海报"}).status_code == 200
    # 恢复脚本：清 deleted_at 后立刻重新出现
    assert restore_main(["--ids", str(gone)]) == 0
    assert gone in {row["id"] for row in client.get("/api/images").json()}
    assert client.get(f"/api/images/{gone}").status_code == 200
    assert gone in client.get(f"/api/projects/{project['id']}").json()["image_ids"]
    assert annotations.search_matches("海报")  # 注释通道恢复
    # 删除项目（另一个对象）也不影响图片
    assert client.delete(f"/api/projects/{deleted_project['id']}").status_code == 200


def test_project_rename_and_soft_delete(env):
    client = env["client"]
    project = client.post("/api/projects", json={"name": "旧名字", "brief": "旧简介"}).json()
    project_id = project["id"]

    renamed = client.patch(f"/api/projects/{project_id}",
                           json={"name": "  新名字  ", "brief": "  新简介  "})
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "新名字" and renamed.json()["brief"] == "新简介"
    only_brief = client.patch(f"/api/projects/{project_id}", json={"brief": ""})
    assert only_brief.json()["name"] == "新名字" and only_brief.json()["brief"] == ""
    only_name = client.patch(f"/api/projects/{project_id}", json={"name": "只改名字"})
    assert only_name.json()["brief"] == ""
    assert client.patch(f"/api/projects/{project_id}", json={"name": "   "}).status_code == 422
    assert client.patch(f"/api/projects/{project_id}", json={}).status_code == 422
    assert client.patch(f"/api/projects/{project_id}", json={"note": "x"}).status_code == 422
    assert client.patch("/api/projects/999999", json={"name": "x"}).status_code == 404

    response = client.delete(f"/api/projects/{project_id}")
    assert response.status_code == 200 and response.json() == {"id": project_id, "deleted": True}
    assert project_id not in {row["id"] for row in client.get("/api/projects").json()["items"]}
    assert client.get(f"/api/projects/{project_id}").status_code == 404
    assert client.get(f"/api/projects/{project_id}/pins").status_code == 404
    assert client.patch(f"/api/projects/{project_id}", json={"name": "x"}).status_code == 404
    assert client.delete(f"/api/projects/{project_id}").status_code == 200  # 幂等
    assert client.delete("/api/projects/999999").status_code == 404

    assert restore_main(["--projects", "--ids", str(project_id)]) == 0
    restored = client.get(f"/api/projects/{project_id}")
    assert restored.status_code == 200 and restored.json()["name"] == "只改名字"


def test_restore_script_list_and_scope(env, capsys):
    client, ids = env["client"], env["ids"]
    assert client.delete(f"/api/images/{ids[0]}").status_code == 200
    assert client.delete(f"/api/images/{ids[1]}").status_code == 200
    assert restore_main(["--list"]) == 0
    listed = capsys.readouterr().out
    assert f"图片 #{ids[0]}" in listed and f"图片 #{ids[1]}" in listed
    assert restore_main(["--images", "--ids", str(ids[0])]) == 0
    assert {row["id"] for row in client.get("/api/images").json()} >= {ids[0], ids[2]}
    listed_after = client.get("/api/images").json()
    assert ids[1] not in {row["id"] for row in listed_after}
    assert restore_main(["--all"]) == 0  # --all 把剩下那张也恢复
    assert ids[1] in {row["id"] for row in client.get("/api/images").json()}


def test_search_repository_filter_matches_api_visibility(env):
    conn = db.connect()
    try:
        visible = repository._visible_image_ids(conn, env["ids"])
    finally:
        conn.close()
    assert set(env["ids"]) == visible  # 未删除时全部可见
    env["client"].delete(f"/api/images/{env['ids'][0]}")
    conn = db.connect()
    try:
        visible = repository._visible_image_ids(conn, env["ids"])
    finally:
        conn.close()
    assert env["ids"][0] not in visible and env["ids"][1] in visible
