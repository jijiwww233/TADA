import threading,time
from types import SimpleNamespace
import httpx
from app import db,importer
from app.sync import connection,safety
from app.embedding.client import EmbeddingClient,EmbeddingSettings
from conftest import png
from test_creative import indexed,seeded,FakeReviewer,fake_needs
from test_search_wiring import FakeQueryFactory,SETTINGS

def test_four_connection_states_use_local_evidence():
    assert connection.describe('pinterest',{'account_known':True})['connection_label']=='已连接'
    assert connection.describe('pinterest',{'last_run':{'pause_reason':'needs_login'}})['connection_label']=='需要登录'
    assert connection.describe('zcool',{'last_run':{'pause_reason':'blocked'}})['connection_label']=='需要验证'
    assert connection.describe('huaban',{'last_run':{'pause_reason':'blocked'}})['connection_label']=='被拦截'
    safety.trip(manual=True)
    assert connection.describe('xhs',{'account_known':True})['connection_label']=='被拦截'

def test_image_details_keep_gallery_slots_video_and_state(data_dir):
    db.init_db();result=importer.import_images([importer.Upload('one.png',png('red')),importer.Upload('two.png',png('blue'))])
    id=result['added'][0]['id'];post=result['post_id'];c=db.connect();c.execute("UPDATE posts SET is_video=1,source_state='unavailable',fav_state='possibly_removed' WHERE id=?",(post,));c.close()
    detail=importer.get_image(id);p=detail['posts'][0]
    assert p['is_video'] and p['source_state']=='unavailable' and p['fav_state']=='possibly_removed'
    assert p['image_count']==2 and [x['position']for x in p['images']]==[0,1]
    assert importer.list_images()[0]['image_count']==2

def test_huaban_verification_does_not_poll_remote_until_human_ack(data_dir,monkeypatch):
    from app.sync import huaban_service as hs
    db.init_db();ack=threading.Event();calls=[]
    class Browser:
        def __init__(self):self.page=self
        def open(self):calls.append('visible-open')
        def goto(self,*args,**kwargs):calls.append('navigate-once')
        def wait_for_timeout(self,*args):
            assert 'check-login'not in calls
            ack.set() # simulated user gesture, no HTTP
        def is_closed(self):return False
        def check_login(self):calls.append('check-login');return {'state':'ok','urlname':'fixture'}
        def close(self):calls.append('closed')
    browser=Browser()
    def boards(reader,**kwargs):
        assert reader.browser is browser and ack.is_set()is False
        calls.append('sync-same-window');return SimpleNamespace(totals={'boards':1})
    monkeypatch.setattr(hs,'run_boards',boards)
    r=hs.verify_and_sync(ack,lambda:browser)
    assert r['ok'] and calls==['visible-open','navigate-once','check-login','sync-same-window','closed']

def test_zcool_manual_gate_requires_local_user_ack(data_dir,monkeypatch):
    from app.sync import zcool_service as zs
    db.init_db();waiting=threading.Event();finished=threading.Event()
    def fake(confirmation=None):
        assert confirmation is not None and not confirmation.exists()
        waiting.set()
        deadline=time.monotonic()+3
        while not confirmation.exists()and time.monotonic()<deadline:time.sleep(.005)
        assert confirmation.exists();finished.set();return {'status':'completed'}
    monkeypatch.setattr(zs,'run_sync',fake);r=zs.ZcoolSyncRunner()
    assert not r.confirm_verification()
    assert r.start_verification() and waiting.wait(1)
    assert not finished.is_set() and not r.start_sync()
    assert r.confirm_verification();r._thread.join(2)
    assert finished.is_set() and not r.busy

def test_embedding_usage_and_total_reco_meter_are_additive(indexed):
    with httpx.Client(transport=httpx.MockTransport(lambda req:httpx.Response(200,json={'data':[{'embedding':[1.,0.]}],'usage':{'total_tokens':7}})))as hc:
        client=EmbeddingClient(EmbeddingSettings(base_url='https://mock.invalid',api_key='fixture',model='fake',dimensions=2),http_client=hc)
        client.embed(['fixture']);assert client.requests==1 and client.prompt_tokens==7 and client.usage_complete
    from app.creative import CreativeApplication
    factory=FakeQueryFactory()
    def measured(settings):
        client=factory(settings);client.requests=1;client.prompt_tokens=7;client.completion_tokens=0;client.usage_complete=True;return client
    r=CreativeApplication(settings_provider=lambda:SETTINGS,client_factory=measured,database=lambda:indexed,vision_settings=lambda:{},reviewer=FakeReviewer(),needs_parser=fake_needs).recommend('建筑展板')
    assert r['usage']['requests']==5 and r['usage']['prompt_tokens']==337 and r['usage']['completion_tokens']==70 and r['usage']['complete']

def test_main_wires_analysis_to_all_four_dimensions(data_dir,monkeypatch):
    from app import main
    from app.embedding import dimensions
    captured={};original=main.EmbeddingAdapter;worker=main.analysis.Worker
    def adapter(**kwargs):captured.update(kwargs);return original(**kwargs)
    def make_worker(**kwargs):captured['on_done']=kwargs['on_done'];return worker(**kwargs)
    monkeypatch.setattr(main,'EmbeddingAdapter',adapter);monkeypatch.setattr(main.analysis,'Worker',make_worker)
    main.create_app();assert tuple(captured['dimension_schemes'])==dimensions.SCHEMES and callable(captured['on_done'])
