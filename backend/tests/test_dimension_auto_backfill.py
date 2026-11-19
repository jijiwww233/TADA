import json
import sqlite3

from app import db
from app.embedding import dimensions
from app.embedding.adapter import EmbeddingAdapter
from app.embedding.client import EmbeddingSettings
from app.embedding.service import vector_to_blob


SETTINGS = EmbeddingSettings(base_url="http://fake.invalid/v1", api_key="sk-test",
                             model="fake-embed", dimensions=8)


class FakeClient:
    def __init__(self):
        self.requests = 0
        self.texts = []

    def embed(self, texts):
        self.requests += 1
        self.texts.extend(texts)
        return [[float(len(text) % 7)] + [0.1] * 7 for text in texts]

    def close(self):
        pass


def seed(data_dir):
    db.init_db()
    conn = db.connect()
    conn.execute("INSERT INTO posts (id, platform, source_id, title) VALUES (1, 'xhs', 'p', 't')")
    conn.execute("INSERT INTO images (id, post_id, position, sha256, file_path) "
                 "VALUES (1, 1, 0, ?, 'images/1.jpg')", ("a" * 64,))
    conn.execute("INSERT INTO post_images (post_id, position, image_id, download_state) "
                 "VALUES (1, 0, 1, 'ok')")
    conn.execute(
        "INSERT INTO analyses (image_id, status, subject, layout_json, style_json, description) "
        "VALUES (1, 'done', ?, ?, ?, '描述')",
        (json.dumps(["建筑"], ensure_ascii=False),
         json.dumps({"columns": ["multi"]}), json.dumps({"tags": ["极简"]})),
    )
    conn.commit()
    conn.close()


def test_done_enqueues_dimensions_and_repeat_is_idempotent(data_dir):
    seed(data_dir)
    client = FakeClient()
    adapter = EmbeddingAdapter(SETTINGS, client_factory=lambda _s: client,
                               flush_seconds=0.01, dimension_schemes=("layout_v1", "style_v1"))
    assert adapter.enqueue(1) == "queued"
    adapter.drain(timeout=10)
    conn = db.connect()
    try:
        rows = conn.execute("SELECT dimension FROM embeddings WHERE image_id=1 ORDER BY dimension").fetchall()
        assert [r[0] for r in rows] == ["combined_d_v1", "layout_v1", "style_v1"]
        assert client.requests == 3  # one combined + one per dimension scheme
        before = client.requests
        assert adapter.enqueue(1) == "queued"
        adapter.drain(timeout=10)
        assert client.requests == before  # all rows valid -> no request
        assert client.requests == before
    finally:
        conn.close()


def test_dimension_adapter_skips_missing_text_without_failure(data_dir):
    seed(data_dir)
    conn = db.connect()
    conn.execute("UPDATE analyses SET layout_json='{}' WHERE image_id=1")
    conn.commit(); conn.close()
    client = FakeClient()
    adapter = EmbeddingAdapter(SETTINGS, client_factory=lambda _s: client,
                               flush_seconds=0.01, dimension_schemes=("layout_v1",))
    adapter.enqueue(1)
    adapter.drain(timeout=10)
    conn = db.connect()
    try:
        rows = conn.execute("SELECT dimension FROM embeddings WHERE image_id=1").fetchall()
        assert [r[0] for r in rows] == ["combined_d_v1"]
        assert adapter.stats["dimension_failed"] == 0
    finally:
        conn.close()
