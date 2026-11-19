"""Real Day5/6/7 HTTP contracts together, temp DB and fake query embeddings only."""
from app import db
from test_recommendation_wiring import seeded, wiring  # shared temp fixtures, not real data


def snapshot(table):
    conn = db.connect()
    try: return [tuple(r) for r in conn.execute(f'SELECT * FROM {table} ORDER BY 1')]
    finally: conn.close()


def test_find_recommend_explicit_pin_project_pip_data_and_remove(wiring):
    client, fake, adapter = wiring
    tables = ('images', 'analyses', 'posts', 'post_images', 'colors', 'embeddings')
    before = {table: snapshot(table) for table in tables}
    search = client.post('/api/search', json={'query': '建筑展板'})
    assert search.status_code == 200 and search.json()['items']
    recommendation = client.post('/api/recommendations', json={'brief': '现代极简的建筑展板'})
    assert recommendation.status_code == 200
    image_id = recommendation.json()['items'][0]['image_id']
    assert not snapshot('pins')  # neither search nor recommendation saves anything
    project = client.post('/api/projects', json={'name': '期末展板', 'brief': '建筑'}).json()['id']
    assert client.patch(f'/api/projects/{project}', json={'brief': '现代建筑'}).json()['brief'] == '现代建筑'
    path = f'/api/projects/{project}/pins'
    first = client.post(path, json={'image_id': image_id, 'purposes': ['layout'], 'note': '参考分栏'}).json()
    merged = client.post(path, json={'image_id': image_id, 'purposes': ['style','layout']}).json()
    assert first['id'] == merged['id'] and merged['purposes'] == ['layout','style']
    assert merged['note'] == '参考分栏'
    detail = client.get(f'/api/projects/{project}').json()
    assert detail['image_ids'] == [image_id]
    assert detail['images'][0]['image_id'] == image_id
    assert detail['images'][0]['sources'][0]['position'] == image_id  # fixture stores original values
    second = client.post('/api/projects', json={'name': '另一项目'}).json()['id']
    client.post(f'/api/projects/{second}/pins', json={'image_id': image_id, 'purposes': ['palette']})
    assert len(snapshot('pins')) == 2
    response = client.delete(f'{path}/{image_id}/purposes/layout')
    assert response.json()['pin']['purposes'] == ['style']
    assert client.delete(f'{path}/{image_id}/purposes/style').json() == {'pin': None}
    assert client.get(f'/api/projects/{project}').json()['image_ids'] == []
    assert client.get(f'/api/projects/{second}/pins').json()['items'][0]['purposes'] == ['palette']
    assert all(snapshot(table) == data for table, data in before.items())
    assert len(fake.queries) == 2 and adapter.stats['queued_total'] == 0
