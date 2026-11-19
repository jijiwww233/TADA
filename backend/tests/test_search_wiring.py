"""Day5 HTTP + real repository + SearchService on temp schema-v3 SQLite.

No real models, user DB, backfill, watcher or platform readers are used.
"""
from dataclasses import replace
import hashlib
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app import config, db
from app.embedding.adapter import EmbeddingAdapter
from app.embedding.client import EmbeddingSettings
from app.embedding.search_application import SearchApplication, hydrate_hits, readonly_connection
from app.embedding.search import SearchHit
from app.embedding.service import EmbeddingSpec, vector_to_blob
from app.embedding.store import load_pending
from app.main import create_app
from conftest import png


SETTINGS = EmbeddingSettings('https://model.invalid/v1', 'sk-fake-query-only')


class FakeQueryClient:
    def __init__(self, settings): self.settings = settings
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def embed(self, texts):
        out = []
        for text in texts:
            self.owner.queries.append(text)
            if self.owner.callback: self.owner.callback()
            if self.owner.error: raise self.owner.error
            out.append([1., 0.] + [0.] * (self.settings.dimensions - 2))
        return out

    def embed_one(self, text):
        return self.embed([text])[0]


class FakeQueryFactory:
    def __init__(self): self.queries = []; self.settings_seen = []; self.error = None; self.callback = None
    def __call__(self, settings):
        self.settings_seen.append(settings)
        client = FakeQueryClient(settings); client.owner = self
        return client


@pytest.fixture
def seeded(data_dir, monkeypatch):
    from app import vision
    def forbidden_vision(*args, **kwargs): raise AssertionError('search triggered vision')
    monkeypatch.setattr(vision, 'analyze', forbidden_vision)
    monkeypatch.setenv('AI_BASE_URL', SETTINGS.base_url)
    monkeypatch.setenv('AI_API_KEY', SETTINGS.api_key)
    monkeypatch.setenv('EMBEDDING_MODEL', SETTINGS.model)
    monkeypatch.setenv('EMBEDDING_DIMENSIONS', '1024')
    db.init_db()
    conn = db.connect()
    try:
        # Neutral post text keeps these assertions focused on the vector route.
        conn.execute("INSERT INTO posts(id,platform,source_id,title,url) VALUES(1,'xhs','first','测试帖子','https://example.org/post/first')")
        conn.execute("INSERT INTO posts(id,platform,source_id,title,url,is_video) VALUES(2,'xhs','second','视频封面','https://example.org/post/second',1)")
        for i in range(1, 26):
            path = config.images_dir() / f'test-{i}.png'
            path.write_bytes(png((i, 0, 0)))
            conn.execute("""INSERT INTO images(id,post_id,position,sha256,file_path,width,height,original_name)
                VALUES(?,1,?,?,?,1080,1440,?)""",
                (i, i + 17, hashlib.sha256(path.read_bytes()).hexdigest(), f'images/test-{i}.png', f'image-{i}.png'))
            conn.execute("INSERT INTO post_images(post_id,position,image_id) VALUES(1,?,?)", (i + 17, i))
            conn.execute("INSERT INTO analyses(image_id,status,description,subject) VALUES(?,'done',?,'[\"展板\"]')", (i, f'多栏展板 {i}'))
        conn.execute("INSERT INTO post_images(post_id,position,image_id) VALUES(2,129,25)")
        spec = EmbeddingSpec.from_settings(SETTINGS)
        pending, _ = load_pending(conn, spec)
        for item in pending:
            conn.execute("INSERT INTO embeddings(image_id,dimension,model,vector,dimensions,text_hash) VALUES(?,?,?,?,?,?)",
                (item.image_id, spec.scheme, spec.model, vector_to_blob([float(item.image_id), 1.] + [0.] * 1022), 1024, item.text_hash))
    finally:
        conn.close()
    return config.db_path()


@pytest.fixture
def wiring(seeded, backend_options):
    factory = FakeQueryFactory()
    # Day4 image adapter must never create a client in any search request.
    def forbidden(_settings): raise AssertionError('search triggered image embedding')
    adapter = EmbeddingAdapter(SETTINGS, client_factory=forbidden)
    with TestClient(create_app(embedder=adapter, search_client_factory=factory), backend_options=backend_options) as client:
        yield client, factory, adapter


def dump_database():
    conn = db.connect()
    try: return '\n'.join(conn.iterdump())
    finally: conn.close()


def test_top20_raw_query_one_embedding_current_day4_settings_and_no_writes(wiring):
    client, fake, adapter = wiring
    before = dump_database()
    query = '  米白色、多栏的建筑展板？\n'
    response = client.post('/api/search', json={'query': query})
    assert response.status_code == 200
    body = response.json()
    assert body['status'] == 'ok'
    assert 'degraded' not in body  # a reachable text route is never labelled as local-only
    assert [x['image_id'] for x in body['items']] == list(range(25, 5, -1))
    assert all(0 < x['score'] <= 1 for x in body['items'])
    assert fake.queries == [query] and fake.settings_seen == [adapter.settings]
    assert (adapter.spec.model, adapter.spec.dimensions, adapter.spec.scheme) == (
        'qwen3.7-text-embedding-flash', 1024, 'combined_d_v1')
    assert dump_database() == before
    assert adapter.state()['queued_total'] == 0


def test_sources_are_all_post_images_relations_and_positions_are_raw(wiring):
    client, _, _ = wiring
    items = client.post('/api/search', json={'query': '展板'}).json()['items']
    assert sum(i['image_id'] == 25 for i in items) == 1
    hit = items[0]
    assert (hit['width'], hit['height'], hit['url']) == (1080, 1440, '/images/test-25.png')
    assert [s['position'] for s in hit['sources']] == [42, 129]
    assert [s['post_id'] for s in hit['sources']] == [1, 2]
    assert hit['sources'][1]['canonical_url'] == 'https://example.org/post/second'
    assert hit['is_video'] and hit['sources'][1]['is_video'] and not hit['sources'][0]['is_video']
    assert 'file_path' not in hit and 'vector' not in hit and 'payload' not in hit


@pytest.mark.parametrize('payload', [{'query': ''}, {'query': ' \n\t'}, {}, {'query': None}, {'query': 1}])
def test_empty_or_invalid_query_422_no_model_request(wiring, payload):
    client, fake, _ = wiring
    assert client.post('/api/search', json=payload).status_code == 422
    assert fake.queries == fake.settings_seen == []


def test_index_empty_never_constructs_query_client_or_backfills(wiring):
    client, fake, adapter = wiring
    conn = db.connect(); conn.execute('DELETE FROM embeddings'); conn.close()
    before = dump_database()
    assert client.post('/api/search', json={'query': '展板'}).json() == {'status': 'index_empty', 'items': []}
    assert fake.settings_seen == [] and adapter.state()['queued_total'] == 0
    assert dump_database() == before


@pytest.mark.parametrize('change', ['model', 'dimensions', 'scheme', 'text_hash', 'pending', 'corrupt', 'nan', 'zero'])
def test_invalid_or_stale_index_is_not_searched_or_rebuilt(wiring, change):
    client, fake, _ = wiring
    conn = db.connect()
    if change == 'pending': conn.execute("UPDATE analyses SET status='pending'")
    elif change == 'corrupt': conn.execute("UPDATE embeddings SET vector=x'01'")
    elif change in ('nan', 'zero'):
        vector = [float('nan') if change == 'nan' else 0.] * 1024
        conn.execute('UPDATE embeddings SET vector=?', (vector_to_blob(vector),))
    else:
        column = 'dimension' if change == 'scheme' else change
        conn.execute(f'UPDATE embeddings SET {column}=?', (512 if change == 'dimensions' else 'stale',))
    conn.close()
    before = dump_database()
    response = client.post('/api/search', json={'query': '展板'})
    assert response.json()['status'] == 'index_empty'
    assert fake.settings_seen == [] and dump_database() == before


def test_query_embedding_failure_degrades_to_local_and_stays_sanitized(wiring):
    client, fake, _ = wiring
    fake.error = RuntimeError('Bearer sk-private upstream body cookie=secret')
    response = client.post('/api/search', json={'query': '展板'})
    assert response.status_code == 200
    body = response.json()
    assert body['status'] == 'ok' and body['items'] == [] and body['degraded'] is True
    assert len(fake.queries) == 1
    assert 'private' not in response.text and 'cookie' not in response.text


def test_presentation_hydration_has_two_selects_for_one_or_twenty_hits(seeded):
    with readonly_connection(seeded) as conn:
        for ids in ([25], list(range(1, 21))):
            sql = []; conn.set_trace_callback(sql.append)
            result = hydrate_hits(conn, [SearchHit(i, .5) for i in ids])
            assert len(result) == len(ids)
            assert sum(s.lstrip().upper().startswith('SELECT') for s in sql) == 2


def test_application_usable_without_http_and_no_read_lock_held_during_embedding(seeded):
    fake = FakeQueryFactory()
    def change_text():
        conn = sqlite3.connect(seeded, timeout=0)
        conn.execute("UPDATE analyses SET description='changed during query' WHERE image_id=25")
        conn.commit(); conn.close()
    fake.callback = change_text
    app = SearchApplication(lambda: SETTINGS, fake, lambda: seeded)
    result = app.search('原样 query')
    assert result['status'] == 'ok' and len(result['items']) == 19
    assert 25 not in [i['image_id'] for i in result['items']]
    assert fake.queries == ['原样 query']


def test_mismatched_factory_client_is_rejected_before_query_embedding(seeded):
    fake = FakeQueryFactory()
    def wrong(settings): return fake(replace(settings, dimensions=512))
    from app.embedding.search_application import SearchUnavailable
    with pytest.raises(SearchUnavailable): SearchApplication(lambda: SETTINGS, wrong, lambda: seeded).search('x')
    assert fake.queries == []


def test_readonly_connection_enforces_no_writes(seeded):
    with readonly_connection(seeded) as conn:
        with pytest.raises(sqlite3.OperationalError): conn.execute('DELETE FROM embeddings')


class FakeClipSearch:
    def __init__(self, scores, valid=None):
        self.scores = scores
        self.queries = []
        self.valid = set(scores) if valid is None else set(valid)

    def search_text(self, text, top_n=None):
        self.queries.append(text)
        pairs = sorted(self.scores.items(), key=lambda pair: (-pair[1], pair[0]))
        return pairs if top_n is None else pairs[:top_n]

    def valid_ids(self):
        return self.valid


def test_clip_route_fusion_is_alpha_weighted_and_uses_one_text_embedding(seeded):
    fake = FakeQueryFactory()
    clip = FakeClipSearch({5: 1.0, 6: 0.9, 7: 0.8, 8: 0.7, 9: 0.6})
    app = SearchApplication(lambda: SETTINGS, fake, lambda: seeded, clip_search=clip)
    result = app.search('多栏展板')
    ids = [item['image_id'] for item in result['items']]
    assert len(ids) == 20 and fake.queries == ['多栏展板'] and clip.queries == ['多栏展板']
    # CLIP top hits get 0.8, the text-route winner only 0.2: both stay in the Top20.
    assert ids[0] == 5 and 25 in ids and ids.index(5) < ids.index(25)
    assert all(0 <= item['score'] <= 1 for item in result['items'])


def test_clip_failure_or_missing_model_keeps_the_text_route_unchanged(seeded):
    fake = FakeQueryFactory()
    baseline = SearchApplication(lambda: SETTINGS, fake, lambda: seeded).search('展板')
    class BrokenClip:
        def search_text(self, text, top_n=None):
            raise RuntimeError('本地图片模型未安装')
    fused = SearchApplication(lambda: SETTINGS, fake, lambda: seeded, clip_search=BrokenClip()).search('展板')
    assert fused == baseline


def test_clip_only_hits_are_dropped_when_the_image_vector_is_no_longer_current(seeded):
    fake = FakeQueryFactory()
    clip = FakeClipSearch({5: 1.0}, valid=set())  # e.g. the file changed after indexing
    result = SearchApplication(lambda: SETTINGS, fake, lambda: seeded, clip_search=clip).search('展板')
    ids = [item['image_id'] for item in result['items']]
    assert 5 not in ids and ids == list(range(25, 5, -1))
