"""Review finding 2: one missing/corrupt candidate image must not abort the review.

The old code encoded every image before the try block, so a FileNotFoundError
escaped review_column -> future.result() -> the whole three-column request.
"""
import functools
import json

import httpx
import pytest

from app import config, creative, db
from app.recommendation import parse_brief
from test_creative import indexed, index_dimensions, FakeReviewer, fake_needs  # noqa: F401
from test_recommendation_wiring import seeded  # noqa: F401
from test_search_wiring import SETTINGS, FakeQueryFactory

VISION = {"base_url": "https://model.invalid/v1", "api_key": "sk-fake", "vision_model": "qwen-vl"}


def candidates_for(image_ids):
    conn = db.connect()
    try:
        context = creative._context(conn, list(image_ids))
    finally:
        conn.close()
    return creative.shortlist([(image_id, 1.0) for image_id in image_ids], context, parse_brief('海报'))


def image_path(image_id):
    conn = db.connect()
    try:
        rel = conn.execute('SELECT file_path FROM images WHERE id=?', (image_id,)).fetchone()[0]
    finally:
        conn.close()
    return config.data_dir() / rel


def handler_from_request(sent, picks=2):
    def handler(request):
        body = json.loads(request.content)
        sent.append(body)
        ids = [int(part['text'].split('=', 1)[1]) for part in body['messages'][0]['content']
               if part.get('type') == 'text' and part['text'].startswith('image_id=')]
        chosen = [{'image_id': image_id, 'rank': rank, 'useful': True, 'reference_point': f'参考{image_id}'}
                  for rank, image_id in enumerate(ids[:picks], 1)]
        return httpx.Response(200, json={'usage': {'prompt_tokens': 10, 'completion_tokens': 5},
                                         'choices': [{'message': {'content': json.dumps({'picks': chosen})}}]})
    return handler


def test_review_column_skips_missing_and_corrupt_images(indexed):
    image_path(10).unlink()
    image_path(7).write_bytes(b'not an image')
    candidates = candidates_for((10, 7, 6))
    sent = []
    with httpx.Client(transport=httpx.MockTransport(handler_from_request(sent))) as client:
        out = creative.review_column('layout', '海报', candidates, VISION, config.images_dir(), client=client)
    assert out['status'] == 'ok'
    assert out['reviewed_ids'] == [6]
    assert out['skipped_images'] == 2
    assert len(sent) == 1
    image_parts = [part for part in sent[0]['messages'][0]['content'] if part['type'] == 'image_url']
    assert len(image_parts) == 1


def test_review_column_all_images_broken_degrades_without_request(indexed):
    for image_id in (10, 7, 6):
        image_path(image_id).unlink()
    candidates = candidates_for((10, 7, 6))
    calls = []

    def handler(request):
        calls.append(request)
        raise AssertionError('model must not be called when no image can be read')

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        out = creative.review_column('style', '海报', candidates, VISION, config.images_dir(), client=client)
    assert out['status'] == 'review_failed'
    assert out['reviewed_ids'] == [] and out['picks'] == []
    assert out['skipped_images'] == len(candidates)
    assert calls == []


def test_recommend_skips_bad_image_keeps_three_columns_and_notices(indexed):
    image_path(10).unlink()
    sent = []
    client = httpx.Client(transport=httpx.MockTransport(handler_from_request(sent)))
    try:
        application = creative.CreativeApplication(
            settings_provider=lambda: SETTINGS, client_factory=FakeQueryFactory(),
            database=lambda: indexed, vision_settings=lambda: VISION,
            reviewer=functools.partial(creative.review_column, client=client),
            needs_parser=fake_needs)
        result = application.recommend('建筑展板')
    finally:
        client.close()
    assert result['status'] == 'ok' and len(result['columns']) == 3
    for column in result['columns']:
        assert column['status'] == 'ok', column
        assert 10 not in column['reviewed_ids']
        assert column['skipped_images'] == 1
        assert column['items']
    assert any('无法读取' in notice for notice in result['notices'])
    assert 10 not in {item['image_id'] for item in result['images']}


def test_reviewer_exception_degrades_only_that_column(indexed):
    fake = FakeReviewer()

    def reviewer(column, *args):
        if column == 'style':
            raise RuntimeError('worker boom')
        return fake(column, *args)

    application = creative.CreativeApplication(
        settings_provider=lambda: SETTINGS, client_factory=FakeQueryFactory(),
        database=lambda: indexed, vision_settings=lambda: {}, reviewer=reviewer,
        needs_parser=fake_needs)
    result = application.recommend('建筑展板')
    assert result['status'] == 'ok' and len(result['columns']) == 3
    columns = {column['purpose']: column for column in result['columns']}
    assert columns['style']['status'] == 'review_failed'
    assert columns['style']['items'] and all(item['unreviewed'] for item in columns['style']['items'])
    assert columns['layout']['status'] == 'ok' and columns['layout']['items']
    assert columns['palette']['status'] == 'ok' and columns['palette']['items']
    assert result['notices']
