"""Original-plan recommender: per-dimension index, three columns, vision review of real images.

Temp schema-v3 SQLite only. Embeddings are fake; the vision reviewer is either a fake
callable or review_column() over httpx.MockTransport. No real model, user DB or pins.
"""
import json
import sqlite3

import httpx
import pytest
from fastapi.testclient import TestClient

from app import config, creative, db
from app.embedding import dimension_backfill, dimensions
from app.embedding.adapter import EmbeddingAdapter
from app.embedding.client import EmbeddingError
from app.embedding.service import vector_to_blob
from app.main import create_app
from app.recommendation import parse_brief
from test_recommendation_wiring import LIBRARY, seeded  # noqa: F401  (fixture)
from test_search_wiring import SETTINGS, FakeQueryFactory, dump_database


def _vector(image_id):
    return [float(image_id), 1.] + [0.] * 1022  # fake query [1,0,...] ranks higher ids first


def index_dimensions(conn, schemes=dimensions.SCHEMES, vector=_vector):
    for scheme in schemes:
        spec = dimensions.spec_for(SETTINGS, scheme)
        pending, _ = dimensions.load_pending(conn, spec)
        for p in pending:
            conn.execute("INSERT INTO embeddings(image_id,dimension,model,vector,dimensions,text_hash) VALUES(?,?,?,?,?,?)",
                         (p.image_id, scheme, spec.model, vector_to_blob(vector(p.image_id)), spec.dimensions, p.text_hash))


@pytest.fixture
def indexed(seeded):  # noqa: F811
    conn = db.connect()
    try: index_dimensions(conn)
    finally: conn.close()
    return seeded


class FakeReviewer:
    """Picks the candidates in reverse order (proves the model order is used), max 5."""
    def __init__(self, fail=()):
        self.calls, self.fail = [], set(fail)
    def __call__(self, column, brief, candidates, settings, images_dir):
        self.calls.append((column, brief, [c.image_id for c in candidates]))
        usage = {"requests": 1, "prompt_tokens": 100, "completion_tokens": 20, "latency_ms": 5}
        if column in self.fail:
            return {"status": "review_failed", "error": "invalid: x", "picks": [], "usage": usage}
        picks = [{"image_id": c.image_id, "reference_point": f"参考{column}{c.image_id}"} for c in reversed(candidates)][:5]
        return {"status": "ok", "picks": picks, "usage": usage}


def fake_needs(brief, settings):
    return {"needs": {"layout_need": "排版：" + brief,
                      "color_need": "配色：" + brief,
                      "style_need": "风格：" + brief},
            "usage": {"requests": 1, "prompt_tokens": 30, "completion_tokens": 10, "latency_ms": 2}}


@pytest.fixture
def app(indexed, backend_options):
    factory, reviewer = FakeQueryFactory(), FakeReviewer()
    def forbidden(_settings): raise AssertionError('recommendation triggered image embedding')
    adapter = EmbeddingAdapter(SETTINGS, client_factory=forbidden)
    with TestClient(create_app(embedder=adapter, search_client_factory=factory,
                              creative_reviewer=reviewer, creative_needs_parser=fake_needs),
                    backend_options=backend_options) as client:
        yield client, factory, reviewer


def post(client, brief, previous=None):
    return client.post('/api/recommendations/columns', json={'brief': brief, **({'previous_brief': previous} if previous else {})})


# ---- dimension texts / index ----

def test_decompose_needs_single_text_request_and_strict_parse():
    calls = []
    def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({
            "layout_need": "多栏", "color_need": "米白", "style_need": "极简"})}}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 6}})
    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = creative.decompose_needs("多栏米白极简展板", {
        "base_url": "https://fake.invalid/v1", "api_key": "sk-test", "vision_model": "text-model"}, client=client)
    client.close()
    assert len(calls) == 1 and "image_url" not in json.dumps(calls[0])
    assert result["needs"] == {"layout_need": "多栏", "color_need": "米白", "style_need": "极简"}
    assert result["usage"]["requests"] == 1 and result["usage"]["prompt_tokens"] == 12


def test_decompose_needs_malformed_falls_back_to_local_parse():
    def handler(request):
        return httpx.Response(200, json={"choices": [{"message": {"content": "not json"}}]})
    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = creative.decompose_needs("现代极简建筑展板", {
        "base_url": "https://fake.invalid/v1", "api_key": "sk-test", "vision_model": "text-model"}, client=client)
    client.close()
    assert result["needs"]["layout_need"] or result["needs"]["style_need"]


def test_dimension_texts_split_fields_and_skip_unknown():
    a = {"subject": ["建筑", "unknown"], "visible_text": "看见\n看见\n咸安坊",
         "layout": {"columns": ["multi"], "density": ["unknown"], "layout_note": "网格化"},
         "style": {"tags": ["复古"], "medium": "mixed", "style_note": "学术感"}, "description": "一张展板"}
    assert dimensions.build_text("subject_v1", a) == "主题：建筑\n图内文字：看见\n咸安坊"
    assert dimensions.build_text("layout_v1", a) == "排版：多栏、网格化"
    assert dimensions.build_text("style_v1", a) == "风格：复古、混合媒介、学术感"
    assert dimensions.build_text("description_v1", a) == "描述：一张展板"
    assert dimensions.text_hash("layout_v1", "x") != dimensions.text_hash("style_v1", "x")


class BatchClient:
    def __init__(self, settings): self.settings, self.request_count, self.error = settings, 0, None
    def __enter__(self): return self
    def __exit__(self, *a): pass
    def embed(self, texts):
        self.request_count += 1
        if self.error: raise self.error
        return [_vector(len(t)) for t in texts]


def test_backfill_writes_only_dimension_rows_and_is_incremental(seeded):  # noqa: F811
    conn = db.connect()
    try:
        before_analyses = conn.execute("SELECT * FROM analyses ORDER BY id").fetchall()
        before_combined = conn.execute("SELECT * FROM embeddings WHERE dimension='combined_d_v1' ORDER BY id").fetchall()
        # images without e.g. any layout field have no layout text and get no row
        expected = {s: sum(bool(dimensions.build_text(s, dict(a)))
                           for a in conn.execute("SELECT * FROM analyses WHERE status='done'")) for s in dimensions.SCHEMES}
        assert expected["description_v1"] == len(LIBRARY) and expected["layout_v1"] < len(LIBRARY)
        plan = dimension_backfill.plan(conn, SETTINGS)
        assert plan["texts"] == sum(expected.values()) and plan["estimated_requests"] == 4
        report = dimension_backfill.run(conn, SETTINGS, client_factory=BatchClient)
        assert report["embedded"] == expected and report["requests"] == 4
        for s in dimensions.SCHEMES:
            assert len(dimensions.load_rows(conn, dimensions.spec_for(SETTINGS, s))) == expected[s]
        assert conn.execute("SELECT * FROM analyses ORDER BY id").fetchall() == before_analyses
        assert conn.execute("SELECT * FROM embeddings WHERE dimension='combined_d_v1' ORDER BY id").fetchall() == before_combined
        again = dimension_backfill.run(conn, SETTINGS, client_factory=BatchClient)
        assert again["requests"] == 0 and sum(again["embedded"].values()) == 0
        # a changed analysis makes only that image's affected rows stale, never served stale
        conn.execute("UPDATE analyses SET description='新的描述' WHERE image_id=10")
        assert 10 not in {r.image_id for r in dimensions.load_rows(conn, dimensions.spec_for(SETTINGS, "description_v1"))}
        assert dimension_backfill.plan(conn, SETTINGS)["texts"] == 1
    finally:
        conn.close()


def test_backfill_stops_on_global_error(seeded):  # noqa: F811
    class Failing(BatchClient):
        def embed(self, texts):
            self.request_count += 1
            raise EmbeddingError("auth", "401")
    conn = db.connect()
    try:
        report = dimension_backfill.run(conn, SETTINGS, client_factory=Failing)
        assert report["stopped"] == "auth" and report["requests"] == 1
        assert conn.execute("SELECT COUNT(*) FROM embeddings WHERE dimension!='combined_d_v1'").fetchone()[0] == 0
    finally:
        conn.close()


# ---- recall / shortlist ----

def ro(path):
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True); conn.row_factory = sqlite3.Row
    return conn


def test_palette_recall_is_soft_over_pillow_shares(indexed):
    conn = ro(indexed)
    try:
        assert [i for i, _ in creative.palette_recall(conn, ("米白",), False)][:2] == [4, 10]  # near-white also scores softly
        soft = creative.palette_recall(conn, ("米白", "红"), False)
        assert {10, 6}.issubset({image_id for image_id, _ in soft})  # absent color does not veto
        reds = [i for i, _ in creative.palette_recall(conn, ("红",), False)]
        assert reds[0] == 6 and len(reds) > 1  # continuous similarity, no named-color hard filter
    finally:
        conn.close()


def test_decomposed_colors_reach_palette_even_when_original_brief_parser_misses_black(indexed, monkeypatch):
    original = creative.palette_recall
    seen = []
    def capture(conn, colors, prominent=False, eligible=None):
        seen.extend(colors)
        return original(conn, colors, prominent, eligible=eligible)
    monkeypatch.setattr(creative, 'palette_recall', capture)
    factory = FakeQueryFactory()
    app = creative.CreativeApplication(settings_provider=lambda: SETTINGS, client_factory=factory,
        database=lambda: indexed, vision_settings=lambda: {}, reviewer=FakeReviewer(),
        needs_parser=lambda *args: {'needs': {'color_need': '采用米白与黑色作为主色调。'}})
    app.recommend('米白黑海报')
    assert [target.hex for target in seen] == ['#f5f0e6', '#000000']


def test_shortlist_dedupes_sha_excludes_and_keeps_post_diversity(indexed):
    conn = ro(indexed)
    try:
        recalled = [(i, 1 - i / 100) for i in (10, 9, 8, 7, 6, 5)]
        ctx = creative._context(conn, [i for i, _ in recalled])
        ctx[9]["sha256"] = ctx[10]["sha256"]
        picked = creative.shortlist(recalled, ctx, parse_brief("建筑展板，不要复古"))
        ids = [c.image_id for c in picked]
        assert 9 not in ids and 6 not in ids  # same file; excluded 复古
        assert ids == [10, 8, 7, 5]  # recall order kept; 8 is post 1's second image, admitted in pass two
        assert next(c for c in picked if c.image_id == 7).is_video
    finally:
        conn.close()


def test_shortlist_caps_at_review_candidates():
    recalled = [(i, 1.0) for i in range(1, 30)]
    ctx = {i: {"sha256": f"s{i}", "post_images": [{"post_id": i}], "is_video": False, "file_path": "x",
               "analyses": {}, "colors": []} for i in range(1, 30)}
    assert len(creative.shortlist(recalled, ctx, parse_brief("海报"))) == creative.REVIEW_CANDIDATES


# ---- review parsing / request ----

def test_parse_review_only_sent_ids_useful_ranked_and_capped():
    text = json.dumps({"picks": [
        {"image_id": 99, "rank": 1, "useful": True, "reference_point": "编造的"},
        {"image_id": 3, "rank": 3, "useful": True, "reference_point": "标题层级"},
        {"image_id": "1", "rank": 1, "useful": True, "reference_point": "网格"},
        {"image_id": 2, "rank": 2, "useful": False, "reference_point": "没用"},
        {"image_id": 4, "rank": 4, "useful": True, "reference_point": ""},
        {"image_id": 1, "rank": 5, "useful": True, "reference_point": "重复"},
        *({"image_id": i, "rank": 10 + i, "useful": True, "reference_point": f"点{i}"} for i in range(5, 9)),
    ]})
    picks = creative.parse_review("```json\n" + text + "\n```", list(range(1, 9)))
    assert [p["image_id"] for p in picks] == [1, 3, 5, 6, 7]
    assert picks[0]["reference_point"] == "网格"
    with pytest.raises(ValueError):
        creative.parse_review('{"nope": []}', [1])
    with pytest.raises(ValueError):
        creative.parse_review("not json", [1])
    assert creative.parse_review('{"picks": []}', [1]) == []  # nothing useful -> empty, not padded


def test_review_column_sends_real_images_in_one_request(indexed):
    sent = []
    def handler(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"usage": {"prompt_tokens": 1234, "completion_tokens": 56}, "choices": [
            {"message": {"content": json.dumps({"picks": [{"image_id": 7, "rank": 1, "useful": True,
                                                           "reference_point": "封面的分栏"}]})}}]})
    conn = ro(indexed)
    try:
        recalled = [(i, 1.0) for i in (10, 7, 6)]
        cands = creative.shortlist(recalled, creative._context(conn, [10, 7, 6]), parse_brief("海报"))
    finally:
        conn.close()
    settings = {"base_url": "https://model.invalid/v1", "api_key": "sk-fake", "vision_model": "qwen-vl", "enable_thinking": False}
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        out = creative.review_column("layout", "海报", cands, settings, config.images_dir(), client=client)
    assert out["status"] == "ok" and out["picks"] == [{"image_id": 7, "reference_point": "封面的分栏"}]
    assert out["usage"]["requests"] == 1 and out["usage"]["prompt_tokens"] == 1234
    assert len(sent) == 1 and sent[0]["enable_thinking"] is False
    parts = sent[0]["messages"][0]["content"]
    assert sum(p["type"] == "image_url" for p in parts) == 3
    assert all(p["image_url"]["url"].startswith("data:image/jpeg;base64,") for p in parts if p["type"] == "image_url")
    assert "image_id=7" in [p.get("text") for p in parts] and "视频笔记" in parts[0]["text"]


def test_review_column_failure_is_reported_not_filled(indexed):
    def handler(request):
        return httpx.Response(200, json={"choices": [{"message": {"content": "我觉得都不错"}}]})
    conn = ro(indexed)
    try:
        cands = creative.shortlist([(10, 1.0)], creative._context(conn, [10]), parse_brief("海报"))
    finally:
        conn.close()
    settings = {"base_url": "https://model.invalid/v1", "api_key": "sk-fake", "vision_model": "qwen-vl"}
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        out = creative.review_column("style", "海报", cands, settings, config.images_dir(), client=client)
    assert out["status"] == "review_failed" and out["picks"] == []
    assert creative.review_column("style", "海报", [], settings, config.images_dir())["status"] == "no_candidates"


# ---- HTTP: three columns ----

def test_three_columns_one_batched_query_embedding_three_reviews_no_writes(app):
    client, fake, reviewer = app
    before = dump_database()
    body = post(client, '我想做一个现代极简的建筑展板').json()
    assert body['status'] == 'ok' and len(fake.queries) == 3
    assert [q.split('：', 1)[0] for q in fake.queries] == ['排版', '风格', '配色']
    assert all('我想做一个现代极简的建筑展板' in q for q in fake.queries)
    assert [c['purpose'] for c in body['columns']] == ['layout', 'palette', 'style']
    assert sorted(c for c, _, _ in reviewer.calls) == ['layout', 'palette', 'style']
    for column in body['columns']:
        assert column['status'] == 'ok' and 1 <= len(column['items']) <= 5
        assert len(column['reviewed_ids']) <= creative.REVIEW_CANDIDATES
        assert [i['rank'] for i in column['items']] == list(range(1, len(column['items']) + 1))
        assert all(i['reference_point'].startswith('参考' + column['purpose']) for i in column['items'])
        # the model's order is kept, not the recall order
        assert [i['image_id'] for i in column['items']] == list(reversed(column['reviewed_ids']))[:5]
    layout = body['columns'][0]
    assert layout['recall_source'] == 'layout_v1' and body['columns'][1]['recall_source'] == 'description_v1'
    video = [i for c in body['columns'] for i in c['items'] if i['image_id'] == 7]
    assert video and all(i['media_scope'] == 'cover_and_description' for i in video)
    assert {i['image_id'] for i in body['images']} == {i['image_id'] for c in body['columns'] for i in c['items']}
    assert dump_database() == before


class ColumnVectorClient:
    def __init__(self):
        self.queries = []
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def close(self):
        pass
    def embed(self, texts):
        self.queries = list(texts)
        vectors = []
        for text in texts:
            base = 3.0 if '配色' in text else 2.0 if '风格' in text else 1.0
            vectors.append([base] + [0.0] * 1023)
        return vectors


def test_columns_use_their_own_decomposed_query_vectors(indexed, monkeypatch):
    seen = {}
    def fake_dimension_recall(conn, settings, scheme, vector, eligible=None):
        seen[scheme] = vector
        return []
    monkeypatch.setattr(creative, 'dimension_recall', fake_dimension_recall)
    client = ColumnVectorClient()
    app = creative.CreativeApplication(
        settings_provider=lambda: SETTINGS, client_factory=lambda _s: client,
        database=lambda: indexed, vision_settings=lambda: {}, reviewer=FakeReviewer(),
        needs_parser=fake_needs)
    body = app.recommend('建筑展板')
    assert body['status'] == 'ok'
    assert len(client.queries) == 3
    assert seen['layout_v1'][0] == 1.0 and seen['style_v1'][0] == 2.0
    assert seen['description_v1'][0] == 3.0


def test_topic_is_not_required_cross_topic_candidates_reach_review(app, indexed):
    client, _, reviewer = app
    conn = db.connect()
    try:  # give the 食品 image #4 a layout record so it has a layout_v1 row, ranked first
        conn.execute("""UPDATE analyses SET layout_json='{"columns": ["grid"], "density": ["high"]}' WHERE image_id=4""")
        index_dimensions(conn, ("layout_v1",), vector=lambda i: [100.0 if i == 4 else float(i), 1.] + [0.] * 1022)
    finally:
        conn.close()
    body = post(client, '我想做复古建筑展板').json()
    layout = next(c for c in body['columns'] if c['purpose'] == 'layout')
    assert layout['reviewed_ids'][0] == 4  # no 建筑 topic, still a layout candidate for an architecture board
    assert 4 in [i['image_id'] for i in layout['items']]


def test_named_colors_use_pillow_recall(app):
    client, _, reviewer = app
    body = post(client, '我想做一张米白色为主的海报').json()
    palette = next(c for c in body['columns'] if c['purpose'] == 'palette')
    assert palette['recall_source'] == 'Pillow 颜色占比' and palette['reviewed_ids'][:2] == [4, 10]


def test_previous_brief_negation_excludes_in_every_column(app):
    client, fake, reviewer = app
    body = post(client, '不要复古，改现代极简', previous='复古建筑展板').json()
    assert body['effective_query'] == '建筑展板，现代极简' and len(fake.queries) == 3
    assert all('建筑展板' in q and '现代极简' in q for q in fake.queries)
    assert all(6 not in ids for _, _, ids in reviewer.calls)
    assert all('建筑展板' in brief or '现代极简' in brief for _, brief, _ in reviewer.calls)


def test_failed_column_shows_no_unreviewed_items(indexed, backend_options):
    factory, reviewer = FakeQueryFactory(), FakeReviewer(fail={'style'})
    adapter = EmbeddingAdapter(SETTINGS, client_factory=lambda s: (_ for _ in ()).throw(AssertionError()))
    with TestClient(create_app(embedder=adapter, search_client_factory=factory,
                               creative_reviewer=reviewer, creative_needs_parser=fake_needs),
                    backend_options=backend_options) as client:
        body = post(client, '海报').json()
    style = next(c for c in body['columns'] if c['purpose'] == 'style')
    assert style['status'] == 'review_failed' and style['items'] and body['notices']
    assert all(item['unreviewed'] and item['reference_point'] == '未复看' for item in style['items'])
    assert len(style['items']) <= 5


def test_each_column_can_pin_to_project_with_column_purpose(app):
    client, _, _ = app
    body = post(client, '现代建筑展板').json()
    assert body['status'] == 'ok' and len(body['columns']) == 3
    project = client.post('/api/projects', json={'name': '三栏项目'}).json()
    pinned = []
    for column in body['columns']:
        if not column['items']:
            continue
        item = column['items'][0]
        response = client.post(f"/api/projects/{project['id']}/pins",
                               json={'image_id': item['image_id'], 'purposes': [item['purpose']]})
        assert response.status_code == 200
        assert column['purpose'] in response.json()['purposes']
        pinned.append(item['image_id'])
    detail = client.get(f"/api/projects/{project['id']}").json()
    assert {p['image_id'] for p in detail['pins']} == set(pinned)
    assert all(item['purpose'] in {'layout', 'palette', 'style'} for c in body['columns'] for item in c['items'])


def test_only_exclusions_422_and_embedding_failure_503(app):
    client, fake, reviewer = app
    assert post(client, '不要复古').status_code == 422
    fake.error = RuntimeError('provider body sk-secret')
    response = post(client, '海报')
    assert response.status_code == 503 and 'sk-secret' not in response.text
    assert reviewer.calls == []


def test_index_empty_without_dimension_rows(seeded, backend_options):  # noqa: F811
    factory, reviewer = FakeQueryFactory(), FakeReviewer()
    adapter = EmbeddingAdapter(SETTINGS, client_factory=lambda s: (_ for _ in ()).throw(AssertionError()))
    with TestClient(create_app(embedder=adapter, search_client_factory=factory,
                               creative_reviewer=reviewer, creative_needs_parser=fake_needs),
                    backend_options=backend_options) as client:
        body = post(client, '海报').json()
    assert body['status'] == 'index_empty' and factory.queries == [] and reviewer.calls == []
