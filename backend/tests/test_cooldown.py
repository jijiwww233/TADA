import json
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from app import db
from app.sync import safety,engine,runner,xhs_source
from app.sync.readers import xhs

def test_manual_hold_survives_restart_and_more_than_six_hours():
    safety.trip('用户报告',manual=True,now=100)
    assert safety.state(now=100+7*3600)['blocked']
    saved=json.loads(safety.path().read_text(encoding='utf8'))
    assert saved['manual_hold'] and saved['auto_paused']

def test_timed_trip_stops_auto_even_after_hours(monkeypatch):
    safety.trip(now=100)
    assert safety.state(now=101)['blocked']
    assert not safety.state(now=100+7*3600)['blocked']
    assert safety.state(now=100+7*3600)['auto_blocked']

def test_bad_safety_file_fails_closed():
    safety.path().parent.mkdir(parents=True)
    safety.path().write_text('bad')
    assert safety.state()['blocked']

def test_api_sync_login_all_source_never_start_network_during_hold(data_dir,monkeypatch):
    from app import main
    safety.trip(manual=True)
    class NoNetwork:
        busy=False
        def start_sync(self):raise AssertionError('xhs request')
        def start_login(self):raise AssertionError('xhs login')
        def state(self):return {'busy':False,'last_run':None}
    class Other:
        def start_sync(self):return False
        def state(self):return {}
    for name in ['HuabanSyncRunner','PinterestSyncRunner','ZcoolSyncRunner']:monkeypatch.setattr(main,name,Other)
    client=TestClient(main.create_app(syncer=NoNetwork()))
    assert client.post('/api/sync/xhs/start').status_code==423
    assert client.post('/api/sync/xhs/login').status_code==423
    assert not client.post('/api/sync/all').json()['xhs']['started']
    assert not xhs_source.probe_open('https://www.xiaohongshu.com/explore/test')
    assert not xhs_source.resolve_note(Path('unused'),'test')['ok']

def test_cli_and_direct_browser_are_blocked_before_open(data_dir):
    db.init_db();safety.trip(manual=True)
    def network():raise AssertionError('browser must not start')
    assert runner.run_sync(reader_factory=network)['status']=='paused'
    assert not runner.run_login(browser_factory=network)['ok']
    with pytest.raises(safety.CoolingDown):xhs.XhsBrowser(Path('unused')).open()

def test_xhs_network_error_is_not_retried(data_dir):
    db.init_db();calls=[]
    class Reader:
        platform='xhs'
        def check_auth(self):return'ok'
        def fetch_page(self,*args):calls.append(1);raise engine.ReaderError('network')
    result=engine.run(Reader(),'fav',sleep=lambda _:(_ for _ in()).throw(AssertionError('retry')))
    assert len(calls)==1 and result['status']=='paused' and safety.state()['blocked']

@pytest.mark.parametrize('message',['系统检测到操作不像正常人','当前网络环境存在风险','安全警告','请完成滑动验证'])
def test_warning_success_envelope_is_still_blocked(message):
    assert xhs.classify_api_error(200,{'success':True,'msg':message})=='blocked'

def test_page_warning_stops_without_second_navigation():
    class Page:
        url='https://www.xiaohongshu.com/'
        def __init__(self):self.n=0
        def goto(self,*args,**kwargs):self.n+=1;return None
        def wait_for_timeout(self,*args):pass
        def content(self):return '<html>安全警告</html>'
        def inner_text(self,*args):return '你的操作不像正常人'
    page=Page()
    with pytest.raises(engine.ReaderError):xhs.XhsBrowser(Path('unused'))._goto_ready(page,page.url)
    assert page.n==1 and safety.state()['blocked']
