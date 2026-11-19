"""Day6 HTTP + real repository + SearchService + recommendation core on temp schema-v3 SQLite.

No real models, user DB, backfill, vision or pins writes. One fake query embedding per request.
"""
import hashlib
import json
import re
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app import config, db
from app.embedding.adapter import EmbeddingAdapter
from app.embedding.service import EmbeddingSpec, vector_to_blob
from app.embedding.store import load_pending
from app.main import create_app
from app.recommendation import recommend
from app.recommendation_query import EmptyEffectiveQuery, resolve_brief
from app.recommendation_service import build_candidates, load_candidate_context
from conftest import png
from test_search_wiring import SETTINGS, FakeQueryFactory, dump_database

# id: (post_ids, subject, style tags, layout, colors, description)
LIBRARY = {
    10: ([1], ["建筑"], ["现代", "极简"], {"columns": ["multi"], "density": ["high"], "whitespace": ["much"]},
         [("#F5F0E6", .6), ("#202020", .3)], "现代极简的建筑展板"),
    9: ([1], ["建筑"], ["现代"], {"columns": ["double"], "density": ["low"], "whitespace": ["much"]},
        [("#3050C0", .7)], "现代建筑海报"),
    8: ([1], ["建筑"], ["极简"], {"columns": ["single"], "density": ["low"], "whitespace": ["medium"]},
        [("#30A040", .7)], "极简建筑立面"),
    7: ([2], ["建筑"], [], {}, [("#808080", .5)], "建筑参观视频封面"),
    6: ([3], ["建筑"], ["复古"], {"columns": ["multi"], "density": ["high"], "whitespace": ["little"]},
        [("#C03020", .5)], "复古建筑展览海报"),
    5: ([3, 4], ["建筑", "展览"], ["现代"], {"columns": ["grid"], "density": ["medium"], "whitespace": ["medium"]},
        [("#E0C020", .4)], "现代建筑展览展板"),
    4: ([5], ["食品"], ["日系"], {}, [("#FFFFFF", .8)], "日式便当"),
    3: ([5], ["食品"], [], {}, [], "咖啡"),
}


@pytest.fixture
def seeded(data_dir, monkeypatch):
    from app import vision
    def forbidden_vision(*args, **kwargs): raise AssertionError('recommendation triggered vision')
    monkeypatch.setattr(vision, 'analyze', forbidden_vision)
    for key, value in (('AI_BASE_URL', SETTINGS.base_url), ('AI_API_KEY', SETTINGS.api_key),
                       ('EMBEDDING_MODEL', SETTINGS.model), ('EMBEDDING_DIMENSIONS', '1024')):
        monkeypatch.setenv(key, value)
    db.init_db()
    conn = db.connect()
    try:
        for post_id in range(1, 6):
            conn.execute("INSERT INTO posts(id,platform,source_id,title,is_video) VALUES(?,'xhs',?,?,?)",
                         (post_id, f'note-{post_id}', f'帖子 {post_id}', int(post_id == 2)))
        for image_id, (posts, subject, tags, layout, colors, description) in LIBRARY.items():
            path = config.images_dir() / f'rec-{image_id}.png'
            path.write_bytes(png((image_id * 20, 0, 0)))
            conn.execute("""INSERT INTO images(id,post_id,position,sha256,file_path,width,height)
                VALUES(?,?,?,?,?,1080,1440)""", (image_id, posts[0], image_id, hashlib.sha256(path.read_bytes()).hexdigest(),
                                               f'images/rec-{image_id}.png'))
            for post_id in posts:
                conn.execute("INSERT INTO post_images(post_id,position,image_id) VALUES(?,?,?)", (post_id, image_id, image_id))
            style = {"tags": tags, "medium": "typography", "style_note": ""}
            conn.execute("""INSERT INTO analyses(image_id,status,description,subject,layout_json,style_json,visible_text)
                VALUES(?,'done',?,?,?,?,'')""", (image_id, description, json.dumps(subject, ensure_ascii=False),
                                                json.dumps(layout), json.dumps(style, ensure_ascii=False)))
            for rank, (hex_value, ratio) in enumerate(colors):
                conn.execute("INSERT INTO colors(image_id,rank,hex,ratio) VALUES(?,?,?,?)", (image_id, rank, hex_value, ratio))
        spec = EmbeddingSpec.from_settings(SETTINGS)
        pending, _ = load_pending(conn, spec)
        for item in pending:  # fake query is [1,0,...]: higher image_id ranks higher
            conn.execute("INSERT INTO embeddings(image_id,dimension,model,vector,dimensions,text_hash) VALUES(?,?,?,?,?,?)",
                (item.image_id, spec.scheme, spec.model, vector_to_blob([float(item.image_id), 1.] + [0.] * 1022), 1024, item.text_hash))
    finally:
        conn.close()
    return config.db_path()


@pytest.fixture
def wiring(seeded, backend_options):
    factory = FakeQueryFactory()
    def forbidden(_settings): raise AssertionError('recommendation triggered image embedding')
    adapter = EmbeddingAdapter(SETTINGS, client_factory=forbidden)
    with TestClient(create_app(embedder=adapter, search_client_factory=factory), backend_options=backend_options) as client:
        yield client, factory, adapter


def post(client, brief, previous=None):
    return client.post('/api/recommendations', json={'brief': brief, **({'previous_brief': previous} if previous else {})})


def resolve(candidate, path):
    """Follow 'analyses.style.tags[0]' / 'colors[1].ratio' through the candidate verbatim."""
    value = candidate
    for name, index in re.findall(r'([a-z_]+)(?:\[(\d+)\])?', path):
        value = value[name]
        if index:
            value = value[int(index)]
    return value


def test_normal_brief_one_search_and_supported_cards(wiring):
    client, fake, adapter = wiring
    before = dump_database()
    response = post(client, '我想做一个现代极简的建筑展板')
    assert response.status_code == 200
    body = response.json()
    assert body['status'] == 'ok' and body['effective_query'] == '我想做一个现代极简的建筑展板'
    assert fake.queries == ['我想做一个现代极简的建筑展板']  # exactly one semantic Top20
    ids = [i['image_id'] for i in body['items']]
    assert ids and ids[0] == 10 and len(ids) <= 6
    assert not {3, 4} & set(ids)  # no evidence -> not filled in even with a score
    assert all(i['purposes'] for i in body['items'])
    assert {i['image_id'] for i in body['images']} == set(ids)
    assert all('url' in i and 'sources' in i for i in body['images'])
    assert dump_database() == before and adapter.state()['queued_total'] == 0


def test_every_evidence_resolves_to_the_stored_field(wiring, seeded):
    client, _, _ = wiring
    items = post(client, '米白色、多栏的现代建筑展板').json()['items']
    conn = sqlite3.connect(seeded); conn.row_factory = sqlite3.Row
    try: context = load_candidate_context(conn, [i['image_id'] for i in items])
    finally: conn.close()
    checked = 0
    for item in items:
        for purpose in item['purposes']:
            for evidence in purpose['evidence']:
                assert resolve(context[item['image_id']], evidence['field']) == evidence['value']
                checked += 1
    assert checked and any(p['purpose'] == 'palette' for i in items for p in i['purposes'])


def test_same_post_first_pass_then_at_most_two(wiring):
    items = post(wiring[0], '我想做一个现代极简的建筑展板').json()['items']
    per_post = {}
    for item in items:
        for post_id in item['post_ids']:
            per_post[post_id] = per_post.get(post_id, 0) + 1
    assert per_post[1] == 2  # 10, 9, 8 share post 1; 8 is not used to fill
    assert 8 not in [i['image_id'] for i in items]
    # First pass: one card per post (5 is promoted and claims posts 3 and 4); the
    # second pass then admits 9 (post 1) and 6 (post 3) as each post's second card.
    assert [i['image_id'] for i in items] == [10, 5, 7, 9, 6]


def test_multi_post_image_keeps_all_relations(wiring):
    item = next(i for i in post(wiring[0], '建筑展览展板').json()['items'] if i['image_id'] == 5)
    assert item['post_ids'] == [3, 4]


def test_same_sha_candidates_merge_into_one_card(seeded):
    conn = sqlite3.connect(seeded); conn.row_factory = sqlite3.Row
    try: context = load_candidate_context(conn, [10, 9])
    finally: conn.close()
    context[9]['sha256'] = context[10]['sha256']  # same file reached via two image rows
    hits = [{'image_id': 10, 'score': .9, 'is_video': False, 'sources': [{'post_id': 1, 'position': 0}]},
            {'image_id': 9, 'score': .8, 'is_video': False, 'sources': [{'post_id': 7, 'position': 0}]}]
    result = recommend('现代建筑展板', build_candidates(hits, context))
    assert [r['image_id'] for r in result['recommendations']] == [10]
    assert result['recommendations'][0]['post_ids'] == [1, 7]


def test_video_is_cover_and_description_only(wiring):
    item = next(i for i in post(wiring[0], '我想做一个现代极简的建筑展板').json()['items'] if i['image_id'] == 7)
    assert item['media_scope'] == 'cover_and_description'
    assert all('仅分析封面与简介' in p['reason'] for p in item['purposes'])


def test_previous_brief_negation_and_replacement(wiring):
    client, fake, _ = wiring
    body = post(client, '不要复古，改现代极简', previous='复古建筑展板').json()
    assert fake.queries == ['建筑展板，现代极简']
    assert body['effective_query'] == '建筑展板，现代极简'
    ids = [i['image_id'] for i in body['items']]
    assert 6 not in ids and 10 in ids  # 复古 evidence excluded, never relaxed
    assert all(p['purpose'] != 'style' or '复古' not in p['reason'] for i in body['items'] for p in i['purposes'])


@pytest.mark.parametrize('previous,current,expected', [
    ('复古建筑展板', '不要复古，改现代极简', '建筑展板，现代极简'),
    (None, '我想做一个现代极简的建筑展板', '我想做一个现代极简的建筑展板'),
    (None, '现代海报，不要红色', '现代海报'),
    ('建筑展板', '不要复古的，但要多栏', '建筑展板，多栏'),
    ('现代建筑', '换成复古', '现代建筑，复古'),  # unmentioned terms persist, as in the core
])
def test_effective_query_keeps_valid_context(previous, current, expected):
    final, effective = resolve_brief(current, previous)
    assert effective == expected
    # Core still sees the full raw text for its own decision.
    assert final.text.endswith(current)


def test_effective_query_does_not_revive_negated_terms():
    final, effective = resolve_brief('不要红色', '红色海报')
    assert '红' not in effective and final.excluded_colors == ('红',)
    with pytest.raises(EmptyEffectiveQuery):
        resolve_brief('不要复古')


def test_only_exclusions_is_422_without_any_request(wiring):
    client, fake, _ = wiring
    response = post(client, '不要复古')
    assert response.status_code == 422 and response.json()['detail']['code'] == 'only_exclusions'
    assert fake.queries == []


@pytest.mark.parametrize('payload', [{'brief': ''}, {'brief': ' \n'}, {}, {'brief': 1}, {'brief': '建筑', 'previous_brief': 2}])
def test_invalid_brief_422(wiring, payload):
    client, fake, _ = wiring
    assert client.post('/api/recommendations', json=payload).status_code == 422
    assert fake.queries == []


def test_context_is_bulk_not_per_candidate(seeded):
    def count(ids):
        statements = []
        conn = sqlite3.connect(seeded); conn.row_factory = sqlite3.Row
        conn.set_trace_callback(statements.append)
        try: load_candidate_context(conn, ids)
        finally: conn.close()
        return len(statements)
    assert count([10]) == count(list(LIBRARY)) == 3


def test_recommendation_never_pins_or_writes_analyses(wiring, seeded):
    client, _, _ = wiring
    conn = sqlite3.connect(seeded)
    conn.execute("INSERT INTO projects(id,name) VALUES(1,'p')")
    conn.commit()
    snapshot = lambda: (conn.execute('SELECT COUNT(*) FROM pins').fetchone()[0],
                        conn.execute('SELECT * FROM analyses ORDER BY id').fetchall())
    before, dump = snapshot(), dump_database()
    body = post(client, '我想做一个现代极简的建筑展板').json()
    assert body['items'] and snapshot() == before and dump_database() == dump
    assert before[0] == 0
    conn.close()


def test_search_failure_is_503_not_empty(wiring):
    client, fake, _ = wiring
    fake.error = RuntimeError('provider body sk-secret')
    response = post(client, '现代建筑展板')
    assert response.status_code == 503
    assert response.json()['detail'] == {'code': 'recommendation_unavailable', 'message': '推荐服务暂不可用'}
    assert 'sk-secret' not in response.text


def test_index_empty_and_no_recommendation_are_distinct(wiring):
    client, fake, _ = wiring
    none = post(client, '海洋主题的插画').json()
    assert none['status'] == 'ok' and none['items'] == [] and none['notices']
    conn = db.connect(); conn.execute('DELETE FROM embeddings'); conn.close()
    empty = post(client, '现代建筑展板').json()
    assert empty['status'] == 'index_empty' and empty['items'] == []
    assert len(fake.queries) == 1  # index_empty never builds a query client
