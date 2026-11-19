"""Offline demo mode must not reach cloud models from any recommendation entry.

Covers review finding 1: /api/recommendations/more-like used to call the vision
reviewer unconditionally; the explicit offline demo switch must keep every
recommendation entry local.
"""
import numpy as np
import pytest
from fastapi.testclient import TestClient

from app import db
from app.main import create_app
from test_creative import indexed, index_dimensions, FakeReviewer, fake_needs  # noqa: F401
from test_recommendation_wiring import seeded  # noqa: F401
from test_search_wiring import SETTINGS, FakeQueryFactory


def visual_index(ids):
    """Minimal local CLIP cache double: identity matrix, unique sha per id."""
    matrix = np.zeros((len(ids), len(ids)))
    np.fill_diagonal(matrix, 1)
    return type('VisualIndex', (), {
        '_cache': lambda self: {
            'ids': np.array(ids),
            'matrix': matrix,
            'positions': {i: n for n, i in enumerate(ids)},
            'by_id': {i: {'sha256': str(i)} for i in ids},
        },
    })()


def cloud_ids():
    conn = db.connect()
    try:
        return [row[0] for row in conn.execute('SELECT id FROM images ORDER BY id')]
    finally:
        conn.close()


@pytest.fixture
def offline(monkeypatch):
    monkeypatch.setenv('WOMINMIN_DEMO', '1')
    monkeypatch.setenv('WOMINMIN_OFFLINE_DEMO', '1')


def test_more_like_offline_uses_local_similarity_and_never_reviews(indexed, offline, backend_options):
    ids = cloud_ids()
    reviewer, factory = FakeReviewer(), FakeQueryFactory()

    def forbidden_needs(*args, **kwargs):
        raise AssertionError('offline mode called the needs parser')

    client = TestClient(create_app(creative_reviewer=reviewer, creative_needs_parser=forbidden_needs,
                                   image_similarity=visual_index(ids)),
                        backend_options=backend_options)
    response = client.post('/api/recommendations/more-like',
                           json={'brief': '建筑展板', 'purpose': 'style', 'image_id': ids[0]})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body['offline'] is True
    assert any('离线' in notice for notice in body['notices'])
    assert reviewer.calls == []
    assert factory.queries == []
    column = body['columns'][0]
    assert 0 < len(column['items']) <= 5
    assert all(item['unreviewed'] and item['reference_point'] == '未复看' for item in column['items'])
    assert body['usage']['requests'] == 0


def test_more_like_offline_without_local_model_still_returns_200(indexed, offline, backend_options):
    ids = cloud_ids()
    reviewer = FakeReviewer()
    client = TestClient(create_app(creative_reviewer=reviewer, image_similarity=None),
                        backend_options=backend_options)
    response = client.post('/api/recommendations/more-like',
                           json={'brief': '建筑展板', 'purpose': 'layout', 'image_id': ids[0]})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body['offline'] is True and body['columns'][0]['items'] == []
    assert reviewer.calls == []


def test_legacy_recommendations_offline_refuses_without_cloud(indexed, offline, backend_options):
    ids = cloud_ids()
    factory = FakeQueryFactory()
    client = TestClient(create_app(search_client_factory=factory, image_similarity=visual_index(ids)),
                        backend_options=backend_options)
    response = client.post('/api/recommendations', json={'brief': '建筑展板'})
    assert response.status_code == 503
    assert factory.queries == []
