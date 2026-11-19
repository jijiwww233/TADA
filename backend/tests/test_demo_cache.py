import json
from app import demo_cache


def test_explicit_mode_exact_task_and_usage_separation(tmp_path,monkeypatch):
    cache=tmp_path/'cache.json'
    original={'status':'ok','notices':[], 'usage':{'requests':5},'columns':[]}
    cache.write_text(json.dumps({'responses':{'建筑展板':original}}),encoding='utf-8')
    monkeypatch.setenv('WOMINMIN_DEMO_CACHE',str(cache))
    monkeypatch.delenv('WOMINMIN_DEMO',raising=False)
    assert demo_cache.lookup('建筑展板') is None
    monkeypatch.setenv('WOMINMIN_DEMO','1')
    value=demo_cache.lookup('建筑展板')
    assert value['offline_demo'] and value['usage']['requests']==0
    assert value['cached_usage']['requests']==5
    assert '离线演示' in value['notices'][0]
    assert demo_cache.lookup('其他任务') is None
    assert demo_cache.lookup('建筑展板',previous='此前任务') is None
    assert demo_cache.lookup('建筑展板',include_nonvisual=True) is None
    assert json.loads(cache.read_text())['responses']['建筑展板']==original


def test_corrupt_cache_fails_closed(tmp_path,monkeypatch):
    p=tmp_path/'cache.json';p.write_text('bad')
    monkeypatch.setenv('WOMINMIN_DEMO','1');monkeypatch.setenv('WOMINMIN_DEMO_CACHE',str(p))
    assert demo_cache.lookup('建筑展板') is None


def test_offline_api_never_enters_live_recommender(data_dir,monkeypatch,backend_options):
    from fastapi.testclient import TestClient
    from app.main import create_app
    from app.creative import CreativeApplication
    p=data_dir/'demo.json'
    p.write_text(json.dumps({'responses':{'建筑展板':{'status':'ok','columns':[], 'images':[], 'notices':[]}}}),encoding='utf-8')
    monkeypatch.setenv('WOMINMIN_DEMO_CACHE',str(p))
    monkeypatch.setenv('WOMINMIN_DEMO','1');monkeypatch.setenv('WOMINMIN_OFFLINE_DEMO','1')
    def forbidden(*a,**k): raise AssertionError('Offline mode called paid recommendation')
    monkeypatch.setattr(CreativeApplication,'recommend',forbidden)
    client=TestClient(create_app(),backend_options=backend_options)
    response=client.post('/api/recommendations/columns',json={'brief':'建筑展板'})
    assert response.status_code==200 and response.json()['offline_demo']
    assert client.post('/api/recommendations/columns',json={'brief':'其他'}).status_code==503
