"""Search degrades to free local channels when the paid text route is down.

No network, no AI Key and no paid embedding: annotation hits come from SQLite and the
local CLIP text route is faked exactly like test_search_wiring.py does.
"""
import hashlib

import pytest
from fastapi.testclient import TestClient

from app import annotations, config, db
from app.embedding.adapter import EmbeddingAdapter
from app.embedding.client import EmbeddingSettings
from app.embedding.search_application import SearchApplication
from app.embedding.service import EmbeddingSpec, vector_to_blob
from app.embedding.store import load_pending
from app.main import create_app
from conftest import png


NO_KEY = EmbeddingSettings('', '')


class FakeClip:
    """Local Chinese-CLIP text tower double; never loads a model or calls the cloud."""
    warming = False

    def __init__(self, scores):
        self.scores = dict(scores)
        self.queries = []

    def search_text(self, text, top_n=None):
        self.queries.append(text)
        ordered = sorted(self.scores.items(), key=lambda pair: (-pair[1], pair[0]))
        return ordered if top_n is None else ordered[:top_n]

    def valid_ids(self):
        return set(self.scores)

    def start(self):
        pass

    def shutdown(self):
        pass


class ForbiddenFactory:
    """Any construction of the paid text-embedding client fails the test."""

    def __init__(self):
        self.calls = []

    def __call__(self, settings):
        self.calls.append(settings)
        raise AssertionError('search fallback constructed the paid embedding client')


def seed_image(conn, image_id, color):
    data = png(color)
    name = 'test-%d.png' % image_id
    (config.images_dir() / name).write_bytes(data)
    conn.execute("""INSERT INTO images(id,post_id,position,sha256,file_path,width,height,original_name)
        VALUES(?,1,?,?,?,8,8,?)""",
        (image_id, image_id, hashlib.sha256(data).hexdigest(), 'images/' + name, name))


def prepare():
    db.init_db()
    conn = db.connect()
    try:
        conn.execute("INSERT INTO posts(id,platform,title,url) VALUES(1,'local','本地导入','https://example.org/local')")
        seed_image(conn, 1, (10, 20, 30))
        seed_image(conn, 2, (40, 50, 60))
        seed_image(conn, 3, (70, 80, 90))
        conn.commit()
    finally:
        conn.close()


def no_key_adapter():
    return EmbeddingAdapter(NO_KEY)


def test_no_key_search_hits_user_tags_locally(data_dir, backend_options):
    prepare()
    annotations.save(1, tags=['海报排版'])
    client = TestClient(create_app(embedder=no_key_adapter()), backend_options=backend_options)
    response = client.post('/api/search', json={'query': '海报'})
    assert response.status_code == 200
    body = response.json()
    assert body['status'] == 'ok' and body['degraded'] is True
    assert [item['image_id'] for item in body['items']] == [1]


def test_no_key_search_uses_local_clip_text_route(data_dir, backend_options):
    prepare()
    clip = FakeClip({2: .9, 3: .4})
    forbidden = ForbiddenFactory()
    app = create_app(embedder=no_key_adapter(), image_similarity=clip,
                     search_client_factory=forbidden)
    client = TestClient(app, backend_options=backend_options)
    response = client.post('/api/search', json={'query': '暖色海报'})
    body = response.json()
    assert response.status_code == 200
    assert body['status'] == 'ok' and body['degraded'] is True
    # The relevance gate adds a relative line on the raw CLIP cosine: hit 3 (.4) is
    # only 0.44 of the best hit (.9), under the weak ratio (0.55), so it is dropped
    # instead of being shown as a "similar" row next to a much closer one.
    assert [item['image_id'] for item in body['items']] == [2]
    assert clip.queries == ['暖色海报']
    assert forbidden.calls == []


def test_no_key_with_existing_index_degrades_instead_of_503(data_dir, backend_options):
    db.init_db()
    conn = db.connect()
    try:
        conn.execute("INSERT INTO posts(id,platform,title,url) VALUES(1,'local','本地导入','https://example.org/local')")
        seed_image(conn, 1, (10, 20, 30))
        conn.execute("INSERT INTO analyses(image_id,status,description,subject) VALUES(1,'done','暖色海报排版',?)", ('["海报"]',))
        spec = EmbeddingSpec.from_settings(NO_KEY)
        pending, _ = load_pending(conn, spec)
        for item in pending:
            conn.execute("""INSERT INTO embeddings(image_id,dimension,model,vector,dimensions,text_hash)
                VALUES(?,?,?,?,?,?)""",
                (item.image_id, spec.scheme, spec.model,
                 vector_to_blob([1., 0.] + [0.] * (spec.dimensions - 2)),
                 spec.dimensions, item.text_hash))
        conn.commit()
    finally:
        conn.close()
    client = TestClient(create_app(embedder=no_key_adapter()), backend_options=backend_options)
    response = client.post('/api/search', json={'query': '海报'})
    assert response.status_code == 200
    body = response.json()
    assert body['status'] == 'ok' and body['degraded'] is True
    assert [item['image_id'] for item in body['items']] == [1]


def test_annotation_hits_stay_ahead_of_clip_hits(data_dir, backend_options):
    prepare()
    annotations.save(3, tags=['暖色海报'])
    clip = FakeClip({2: .9, 1: .8})
    client = TestClient(create_app(embedder=no_key_adapter(), image_similarity=clip),
                        backend_options=backend_options)
    body = client.post('/api/search', json={'query': '海报'}).json()
    assert body['degraded'] is True
    assert [item['image_id'] for item in body['items']] == [3, 2, 1]


def test_offline_demo_search_uses_local_annotations(data_dir, backend_options, monkeypatch):
    prepare()
    annotations.save(2, tags=['暖色海报'])
    monkeypatch.setenv('WOMINMIN_DEMO', '1')
    monkeypatch.setenv('WOMINMIN_OFFLINE_DEMO', '1')
    client = TestClient(create_app(embedder=no_key_adapter()), backend_options=backend_options)
    body = client.post('/api/search', json={'query': '海报'}).json()
    assert body['status'] == 'ok' and body['degraded'] is True
    assert [item['image_id'] for item in body['items']] == [2]


def test_search_local_is_safe_without_clip(data_dir):
    db.init_db()
    app = SearchApplication(lambda: NO_KEY, database=config.db_path, clip_search=None)
    assert app.search_local('海报') == {'status': 'ok', 'items': [], 'degraded': True}
    with pytest.raises(ValueError):
        app.search_local('   ')

