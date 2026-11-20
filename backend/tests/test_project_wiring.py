"""Day7 on temporary SQLite only. No recommendation imports or model calls."""
from concurrent.futures import ThreadPoolExecutor
import json
import threading

import pytest
from fastapi.testclient import TestClient

from app import db, importer, vision
from app.embedding.adapter import EmbeddingAdapter
from app.main import create_app
from app.project_store import ProjectStore, PersistenceError
from conftest import png


@pytest.fixture
def library(data_dir, monkeypatch):
    def forbidden(*args, **kwargs): raise AssertionError('Day7 invoked AI or sync')
    monkeypatch.setattr(vision, 'analyze', forbidden)
    db.init_db()
    result = importer.import_images([importer.Upload('reference.png', png('red'))])
    image_id = result['added'][0]['id']
    conn = db.connect()
    conn.execute("UPDATE analyses SET status='done',description='keep analysis',raw_json='{\"stable\":true}' WHERE image_id=?", (image_id,))
    conn.execute("INSERT INTO posts(platform,source_id,url,title,is_video) VALUES('xhs','second','https://example.org/post/second','封面',1)")
    source_id = conn.execute('SELECT max(id) FROM posts').fetchone()[0]
    conn.execute('INSERT INTO post_images(post_id,position,image_id) VALUES(?,129,?)', (source_id, image_id))
    conn.close()
    return image_id


@pytest.fixture
def api(library, backend_options, monkeypatch):
    def forbidden(*args, **kwargs): raise AssertionError('Day7 started embedding or sync')
    adapter = EmbeddingAdapter(client_factory=forbidden)
    from app.sync.runner import SyncRunner
    monkeypatch.setattr(SyncRunner, 'start_sync', forbidden)
    monkeypatch.setattr(SyncRunner, 'start_login', forbidden)
    with TestClient(create_app(embedder=adapter), backend_options=backend_options) as client:
        yield client
    assert adapter.stats['queued_total'] == 0


def rows(table):
    conn = db.connect()
    try: return [tuple(row) for row in conn.execute(f'SELECT * FROM {table} ORDER BY 1')]
    finally: conn.close()


def make_project(api, name='建筑展板', brief='多栏'):
    response = api.post('/api/projects', json={'name': name, 'brief': brief})
    assert response.status_code == 201, response.text
    return response.json()['id']


def pin(api, project_id, image_id, purposes, **extra):
    return api.post(f'/api/projects/{project_id}/pins', json={'image_id': image_id, 'purposes': purposes, **extra})


def remove(api, project_id, image_id, purpose):
    return api.delete(f'/api/projects/{project_id}/pins/{image_id}/purposes/{purpose}')


def test_create_list_detail_rename_brief_and_soft_delete(api):
    project_id = make_project(api, ' 展板 ', ' 建筑 ')
    listing = api.get('/api/projects').json()
    assert listing['items'][0]['name'] == '展板'
    assert listing['allowed_purposes'] == ['layout', 'style', 'palette', 'subject']
    detail = api.get(f'/api/projects/{project_id}').json()
    assert detail['brief'] == '建筑' and detail['pins'] == detail['images'] == detail['image_ids'] == []
    response = api.patch(f'/api/projects/{project_id}', json={'brief': ' 现代 '})
    assert response.json()['brief'] == '现代'
    # P0-3: name 也能改；一个字段都不给或给未知字段 → 422
    renamed = api.patch(f'/api/projects/{project_id}', json={'name': ' 新展板 ', 'brief': ' 极简 '})
    assert renamed.status_code == 200
    assert (renamed.json()['name'], renamed.json()['brief']) == ('新展板', '极简')
    assert api.patch(f'/api/projects/{project_id}', json={}).status_code == 422
    assert api.patch(f'/api/projects/{project_id}', json={'name': '  '}).status_code == 422
    assert api.patch(f'/api/projects/{project_id}', json={'note': 'not a project field'}).status_code == 422
    # P0-3: DELETE 是软删除（200 + 标记），详情/列表消失，清标记即恢复
    deleted = api.delete(f'/api/projects/{project_id}')
    assert deleted.status_code == 200 and deleted.json() == {'id': project_id, 'deleted': True}
    assert api.get(f'/api/projects/{project_id}').status_code == 404
    assert api.get('/api/projects').json()['items'] == []
    conn = db.connect()
    conn.execute('UPDATE projects SET deleted_at=NULL WHERE id=?', (project_id,))
    conn.close()
    assert api.get(f'/api/projects/{project_id}').json()['name'] == '新展板'


def test_duplicate_pin_merges_canonical_purposes_preserves_row_identity_and_note(api, library):
    project_id = make_project(api)
    first = pin(api, project_id, library, ['style', 'layout'], note='看排版').json()
    merged = pin(api, project_id, library, ['palette', 'style', 'palette']).json()
    assert merged['purposes'] == ['layout', 'style', 'palette']
    assert (merged['id'], merged['created_at'], merged['note']) == (first['id'], first['created_at'], '看排版')
    assert len(rows('pins')) == 1
    assert json.loads(rows('pins')[0][4]) == ['layout', 'style', 'palette']
    assert pin(api, project_id, library, ['style'], note='').json()['note'] == ''


def test_remove_one_then_last_keeps_images_analysis_and_sources(api, library):
    project_id = make_project(api)
    protected = {table: rows(table) for table in ('images', 'analyses', 'posts', 'post_images', 'colors', 'embeddings')}
    pin(api, project_id, library, ['layout', 'style'])
    assert remove(api, project_id, library, 'layout').json()['pin']['purposes'] == ['style']
    # Removing an absent use is idempotent.
    assert remove(api, project_id, library, 'layout').json()['pin']['purposes'] == ['style']
    assert remove(api, project_id, library, 'style').json() == {'pin': None}
    assert remove(api, project_id, library, 'style').json() == {'pin': None}
    assert rows('pins') == []
    assert all(rows(t) == before for t, before in protected.items())


def test_same_image_in_different_projects_is_independent(api, library):
    a, b = make_project(api, 'a'), make_project(api, 'b')
    pin(api, a, library, ['layout', 'style']); pin(api, b, library, ['palette'])
    remove(api, a, library, 'style')
    assert api.get(f'/api/projects/{a}/pins').json()['items'][0]['purposes'] == ['layout']
    assert api.get(f'/api/projects/{b}/pins').json()['items'][0]['purposes'] == ['palette']
    assert len(rows('pins')) == 2


def test_project_image_order_and_removal_preserve_library_and_other_projects(api, library):
    second, third = [row['id'] for row in importer.import_images([
        importer.Upload('blue.png', png('blue')), importer.Upload('green.png', png('green')),
    ])['added']]
    a, b = make_project(api, 'a'), make_project(api, 'b')
    pin(api, a, library, ['style'])
    pin(api, a, second, ['layout', 'palette'])
    pin(api, b, second, ['subject'])
    assert api.get(f'/api/projects/{a}').json()['image_ids'] == [library, second]
    move = f'/api/projects/{a}/pins/{second}/position'
    assert api.patch(move, json={'direction': 'previous'}).json()['image_ids'] == [second, library]
    pin(api, a, third, ['subject'])
    assert api.get(f'/api/projects/{a}').json()['image_ids'] == [second, library, third]
    assert api.patch(move, json={'direction': 'next'}).json()['image_ids'] == [library, second, third]
    assert api.delete(f'/api/projects/{a}/pins/{second}').json()['image_ids'] == [library, third]
    assert api.patch(move, json={'direction': 'next'}).status_code == 404
    assert api.get(f'/api/projects/{b}').json()['image_ids'] == [second]
    assert api.get(f'/api/images/{second}').status_code == 200


def test_detail_and_pins_supply_pip_display_without_frontend_or_search(api, library):
    project_id = make_project(api); pin(api, project_id, library, ['subject'])
    detail = api.get(f'/api/projects/{project_id}').json()
    assert detail['image_ids'] == [library]
    assert len(detail['images']) == 1
    image = detail['images'][0]
    assert image['image_id'] == library and image['url'].startswith('/images/')
    assert image['width'] == image['height'] == 8
    assert [s['position'] for s in image['sources']] == [0, 129]
    assert image['sources'][1]['canonical_url'] == 'https://example.org/post/second'
    assert image['sources'][1]['is_video'] is True
    assert 'file_path' not in image and 'analysis' not in image
    assert api.get(f'/api/projects/{project_id}/pins').json()['images'] == detail['images']


@pytest.mark.parametrize('command', [
    {'name': ''}, {'name': '  '}, {'name': 1}, {'name': 'ok', 'brief': None}, {'name': 'ok', 'note': 'no'},
])
def test_invalid_project_body_422_no_rows(api, command):
    assert api.post('/api/projects', json=command).status_code == 422
    assert rows('projects') == []


@pytest.mark.parametrize('purposes', [[], ['fake'], ['color'], 'layout', [None]])
def test_invalid_pin_purposes_422(api, library, purposes):
    project_id = make_project(api)
    assert pin(api, project_id, library, purposes).status_code == 422
    assert rows('pins') == []


@pytest.mark.parametrize('image_id', [0, -1, True, '1'])
def test_invalid_image_identity_422(api, image_id):
    assert pin(api, 1, image_id, ['layout']).status_code == 422


def test_invalid_project_image_and_api_error_statuses(api, library):
    project_id = make_project(api)
    assert api.get('/api/projects/999').status_code == 404
    assert api.get('/api/projects/0').status_code == 422
    assert api.get('/api/projects/999/pins').status_code == 404
    assert api.patch('/api/projects/999', json={'brief': 'a'}).status_code == 404
    assert pin(api, 999, library, ['style']).json()['detail']['code'] == 'project_not_found'
    assert pin(api, project_id, 999, ['style']).json()['detail']['code'] == 'image_not_found'
    assert remove(api, project_id, 999, 'style').status_code == 404
    assert remove(api, project_id, library, 'fake').status_code == 422


def test_sql_failure_rollback_and_sanitized_api_error(api, library):
    project_id = make_project(api); pin(api, project_id, library, ['style'])
    before = rows('pins')
    conn = db.connect()
    conn.execute("""CREATE TRIGGER fail_pin BEFORE UPDATE ON pins BEGIN
        SELECT RAISE(ABORT, 'Bearer secret-raw-database-error'); END""")
    conn.close()
    response = pin(api, project_id, library, ['palette'])
    assert response.status_code == 503
    assert response.json()['detail']['code'] == 'project_store_unavailable'
    assert 'secret' not in response.text and rows('pins') == before


def test_failure_after_successful_update_before_commit_rolls_back(library, monkeypatch):
    store = ProjectStore(); project_id = store.create_project('a')['id']
    store.pin_image(project_id, library, ['style'])
    before = rows('pins')
    def fail_after_write(_row): raise PersistenceError('fixed failure')
    monkeypatch.setattr(store, '_pin_view', fail_after_write)
    with pytest.raises(PersistenceError): store.pin_image(project_id, library, ['palette'])
    assert rows('pins') == before
    assert ProjectStore().pin_image(project_id, library, ['palette'])['purposes'] == ['style', 'palette']


def test_concurrent_duplicate_pin_transactions_merge_without_lost_update(library):
    store = ProjectStore(); project_id = store.create_project('a')['id']
    barrier = threading.Barrier(2)
    def concurrent(purpose):
        barrier.wait(timeout=5)
        return ProjectStore().pin_image(project_id, library, [purpose])
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(concurrent, p) for p in ('layout', 'style')]
        for future in futures: future.result(timeout=10)
    assert store.list_pins(project_id)['items'][0]['purposes'] == ['layout', 'style']
    assert len(rows('pins')) == 1


@pytest.mark.parametrize('stored,expected', [('layout', ['layout']), ('["style","layout"]', ['layout','style']), (None, []), ('', [])])
def test_legacy_storage_read_compatibility_no_implicit_rewrite(api, library, stored, expected):
    project_id = make_project(api)
    conn = db.connect()
    conn.execute('INSERT INTO pins(project_id,image_id,purpose,note) VALUES(?,?,?,?)', (project_id, library, stored, 'old note'))
    conn.close(); before = rows('pins')
    item = api.get(f'/api/projects/{project_id}/pins').json()['items'][0]
    assert item['purposes'] == expected and item['needs_purpose'] == (not expected)
    assert rows('pins') == before
    merged = pin(api, project_id, library, ['subject']).json()
    assert merged['note'] == 'old note' and merged['id'] == item['id']


def test_bad_legacy_purpose_is_409_and_not_overwritten(api, library):
    project_id = make_project(api)
    conn = db.connect(); conn.execute("INSERT INTO pins(project_id,image_id,purpose) VALUES(?,?,'color')", (project_id, library)); conn.close()
    before = rows('pins')
    assert api.get(f'/api/projects/{project_id}').status_code == 409
    assert pin(api, project_id, library, ['style']).status_code == 409
    assert rows('pins') == before


def test_v0_existing_projects_pins_migrate_with_no_day7_schema_change(data_dir):
    data_dir.mkdir(parents=True, exist_ok=True)
    conn = db.connect(); conn.executescript(db.SCHEMA)
    conn.execute("INSERT INTO posts(id,platform) VALUES(1,'local')")
    conn.execute("INSERT INTO images(id,post_id,position,sha256,file_path) VALUES(1,1,7,'sha','images/a.png')")
    conn.execute("INSERT INTO projects(id,name,brief) VALUES(8,'old',NULL)")
    conn.execute("INSERT INTO pins(id,project_id,image_id,purpose,note) VALUES(9,8,1,'layout','keep')")
    # Day7 不重建 pins；P0-3 只给 projects 追加软删除标记列（v15），原有列和数据不动。
    pins_schema = [tuple(r) for r in conn.execute('PRAGMA table_info(pins)')]
    project_columns = [tuple(r)[1] for r in conn.execute('PRAGMA table_info(projects)')]
    conn.close(); before = rows('pins')
    db.init_db(); db.init_db()
    conn = db.connect()
    assert conn.execute('PRAGMA user_version').fetchone()[0] == db.SCHEMA_VERSION
    assert [tuple(r) for r in conn.execute('PRAGMA table_info(pins)')] == pins_schema
    assert [tuple(r)[1] for r in conn.execute('PRAGMA table_info(projects)')] ==         project_columns + ['deleted_at']
    conn.close()
    assert rows('pins') == before
    detail = ProjectStore().detail(8)
    assert detail['brief'] == '' and detail['pins'][0]['purposes'] == ['layout']
    assert detail['images'][0]['sources'][0]['position'] == 7


def test_detail_hydrates_many_pins_without_n_plus_one(library):
    store = ProjectStore(); project_id = store.create_project('a')['id']
    conn = db.connect()
    for i in range(2, 22):
        conn.execute("INSERT INTO images(id,post_id,position,sha256,file_path) VALUES(?,1,?,?,?)", (i, i, str(i), f'images/{i}.png'))
        conn.execute('INSERT INTO post_images(post_id,position,image_id) VALUES(1,?,?)', (i, i))
        conn.execute("INSERT INTO pins(project_id,image_id,purpose) VALUES(?,?,'layout')", (project_id, i))
    conn.close()
    sql = []
    def traced():
        conn = db.connect(); conn.set_trace_callback(sql.append); return conn
    detail = ProjectStore(traced).detail(project_id)
    assert len(detail['images']) == 20
    assert sum(s.lstrip().upper().startswith('SELECT') for s in sql) == 4
