import importlib.util
import json
import threading
import time
from pathlib import Path
from app.sync.metrics import measure

def test_metrics_never_records_payload_and_never_breaks_store(monkeypatch, tmp_path):
    path = tmp_path/'metrics.jsonl'
    monkeypatch.setenv('SYNC_METRICS_FILE', str(path))
    with measure('xhs', 'store', 10): pass
    row = json.loads(path.read_text())
    assert set(row) == {'platform','stage','units','seconds','success'}
    assert row['units'] == 10 and row['success'] and row['seconds'] >= 0
    monkeypatch.setenv('SYNC_METRICS_FILE', str(tmp_path))
    with measure('xhs', 'store'): pass

def test_pinterest_parallel_download_order_and_hard_cap(tmp_path):
    file = Path(__file__).resolve().parents[2]/'vendor/pinterest/scripts/pinterest_reader.py'
    spec = importlib.util.spec_from_file_location('speed_reader', file)
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    reader = mod.Reader({}, 'fixture', str(tmp_path))
    pins = [{'id': str(i), 'type':'pin','images': {'orig':{'url':f'https://i.pinimg.com/{i}.jpg'}}} for i in range(17)]
    reader.s.get = lambda _: {'resource_response': {'data':pins}, 'resource': {'options': {'bookmarks': ['-end-']}}}
    # Force one page, deterministic varied work completion order.
    lock = threading.Lock(); active = 0; peak = 0
    def item(pin, position):
        nonlocal active, peak
        with lock: active += 1; peak=max(peak,active)
        time.sleep(.005*(int(pin['id'])%3+1))
        with lock: active -= 1
        return {'work_key':pin['id'],'images':[]}
    reader._item = item
    reader.max_pages=1
    reader.account_key='fixture'; reader.display_name='fixture'
    reader._paginate('https://fixture', {}, '/', 'fav', 'fixture', 'default_favorites', None, 1, [])
    page=json.loads(next(tmp_path.glob('page-*.json')).read_text())
    assert [x['work_key'] for x in page['items']] == [str(i)for i in range(17)]
    assert peak == 4

def test_sync_all_starts_existing_runners_without_login(data_dir, monkeypatch, backend_options):
    from app import main
    from fastapi.testclient import TestClient
    called=[]
    class Runner:
        def start_sync(self): called.append('sync'); return True
        def start_login(self): raise AssertionError('must not login')
        def state(self): return {'busy':False}
    for name in ('HuabanSyncRunner','PinterestSyncRunner','ZcoolSyncRunner'):
        monkeypatch.setattr(main,name,Runner)
    app=main.create_app(syncer=Runner())
    response=TestClient(app,backend_options=backend_options).post('/api/sync/all')
    assert response.status_code==200 and len(called)==4
    assert all(x['started']for x in response.json().values())
