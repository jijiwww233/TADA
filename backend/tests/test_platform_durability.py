"""F2b resources/collections plus true OS process termination on synthetic streams.
No real credentials, website traffic or model requests in these tests.
"""
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
import pytest
from app import db
from app.sync import engine,store,pinterest_service as ps
from conftest import png


def vendor():
    p=Path(__file__).resolve().parents[2]/'vendor/pinterest/scripts/pinterest_reader.py'
    spec=importlib.util.spec_from_file_location('resource_reader',p);mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod);return mod


def test_resolutions_are_one_resource_carousel_and_story_are_ordered():
    reader=vendor().Reader
    variants={'orig':{'url':'https://i/a.jpg'},'236x':{'url':'https://i/a-small.jpg'}}
    assert len(reader.resources({'images':variants}))==1
    raw={'images':variants,'carousel_data':{'carousel_slots':[{'images':{'orig':{'url':'https://i/2.jpg'}}},{},{'images':{'orig':{'url':'https://i/1.jpg'}}}]}}
    assert [x['url']if x else None for x in reader.resources(raw)]==['https://i/2.jpg',None,'https://i/1.jpg']
    story={'story_pin_data':{'pages':[{'blocks':[{'image':{'images':variants}}]},{'blocks':[{'image':{'images':{'orig':{'url':'https://i/b.jpg'}}}}]}]}}
    assert [x['url']for x in reader.resources(story)]==['https://i/a.jpg','https://i/b.jpg']


def test_pin_slots_fail_in_place_no_variant_duplication(tmp_path):
    r=vendor().Reader({},'fixture',str(tmp_path),download_images=False)
    got=r._item({'id':'1','raw':{'carousel_data':{'carousel_slots':[{'images':{'orig':{'url':'https://i/a.jpg'}}},{},{'images':{'orig':{'url':'https://i/c.jpg'}}}]}},'url':'https://www.pinterest.com/pin/1/'},1)
    assert [(i['idx'],i['download_state'])for i in got['images']]==[(1,'remote_only'),(2,'failed'),(3,'remote_only')]
    assert got['media_type']=='image'


def test_collection_page_names_no_collision_and_cap_not_fake_end(tmp_path):
    r=vendor().Reader({},'fixture',str(tmp_path));r.account_key='fixture';r.display_name='fixture'
    r._page('fav','f','default_favorites',[],None,'bookmark',True,1);r._page('board:1','one','board',[],None,None,False,1)
    assert len(list(tmp_path.glob('page-*.json')))==2
    parent=ps.PinterestExternalReader('bootstrap',tmp_path);parent.pages={'fav':[{'page':{'index':4,'has_more':True}}]}
    assert parent.fetch_page('fav',None,4)['page']['index']==4
    with pytest.raises(engine.ReaderError)as exc:parent.fetch_page('fav',None,5)
    assert exc.value.kind=='limit'


def test_collection_memberships_atomic_ordered_and_no_duplicate_posts(data_dir):
    db.init_db();from app.sync.contract import parse_page
    def page(key,index,ids):
        return parse_page({'contract_version':'sync-reader/1','platform':'pinterest','collection':{'collection_key':key,'title':key,'kind':'board'},'page':{'index':index,'has_more':False},'items':[{'work_key':id,'images':[],'media_type':'unknown'}for id in ids]})
    for key,ids in [('board:a',['p2','p1']),('board:b',['p1'])]:
        rid=store.start_run('pinterest',key,'bootstrap','account');store.apply_page(rid,page(key,1,ids),'bootstrap',{},'account')
    c=db.connect();assert c.execute('select count(*) from posts').fetchone()[0]==2
    assert c.execute('select count(*) from post_collections').fetchone()[0]==3
    assert [x[0]for x in c.execute("select p.source_id from post_collections pc join posts p on p.id=pc.post_id where collection_key='board:a' order by position")]==['p2','p1']
    assert c.execute('pragma user_version').fetchone()[0]==db.SCHEMA_VERSION;c.close()
    from fastapi.testclient import TestClient
    from app.main import create_app
    response=TestClient(create_app()).get('/api/sync/collections')
    assert response.status_code==200
    assert {x['collection_key']:x['posts']for x in response.json()['items']}=={'board:a':2,'board:b':1}



def test_cached_complete_pins_do_not_redownload(tmp_path):
    r=vendor().Reader({},'fixture',str(tmp_path));r.known_complete={'1'}
    r._image_item=lambda *a:(_ for _ in()).throw(AssertionError('no download'))
    assert r._item({'id':'1'},1)['images']==[]


def test_early_auth_and_captcha_are_paused(data_dir):
    db.init_db();from app.sync import huaban_service as hs,zcool_service as zs
    class Bad:
        def __exit__(self,*args):pass
        def __enter__(self):raise engine.ReaderError('needs_login')
    r=hs.run_sync(reader_factory=Bad);assert (r['status'],r['pause_reason'])==('paused','needs_login')
    class Block:
        def __exit__(self,*args):pass
        def __enter__(self):raise engine.ReaderError('blocked')
    old=zs.ZcoolFileReader;zs.ZcoolFileReader=lambda **kwargs:Block()
    try:r=zs.run_sync();assert (r['status'],r['pause_reason'])==('paused','blocked')
    finally:zs.ZcoolFileReader=old

CHILD=r'''
import os,sys,time,pathlib,json
from app import db
from app.sync import engine,store
from PIL import Image
root=pathlib.Path(os.environ['WOMINMIN_DATA_DIR']);db.init_db();platform=os.environ['FIXTURE_PLATFORM'];point=os.environ.get('KILL_POINT','');ready=root/'ready'
class Reader:
    account_key='synthetic-account'
    def check_auth(self):return'ok'
    def fetch_page(self,key,cursor,index):
        if point=='fetch'and index==3:
            ready.write_text('after 20 committed items')
            while True:time.sleep(.05)
        items=[]
        for n in range((index-1)*10,min(index*10,60)):
            file=root/f'input-{n}.png'
            if not file.exists():Image.new('RGB',(8,8),(n,n*3%255,n*7%255)).save(file)
            items.append({'work_key':str(n),'images':[{'idx':1,'local_path':str(file),'download_state':'ok'}]})
        return {'contract_version':'sync-reader/1','platform':platform,'collection':{'collection_key':'fav'},'page':{'index':index,'cursor_in':cursor,'cursor_out':str(index)if index<6 else None,'has_more':index<6},'items':items}
Reader.platform=platform
if point=='transaction':
    original=store._save_cursor
    def pause(*args,**kwargs):
        if args[4].get('page_index')==2:
            ready.write_text('inside uncommitted page 2')
            while True:time.sleep(.05)
        return original(*args,**kwargs)
    store._save_cursor=pause
store.recover_stale_runs();result=engine.run(Reader(),'fav',mode='incremental');print(json.dumps({'status':result['status']}))
'''

@pytest.mark.parametrize('platform',['xhs','pinterest'])
@pytest.mark.parametrize('point',['fetch','transaction'])
def test_force_kill_resume_over_55_without_duplicates(data_dir,platform,point):
    env=os.environ.copy();env.update(WOMINMIN_DATA_DIR=str(data_dir),FIXTURE_PLATFORM=platform,KILL_POINT=point)
    cwd=Path(__file__).resolve().parents[1]
    child=subprocess.Popen([sys.executable,'-c',CHILD],cwd=cwd,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    try:
        deadline=time.monotonic()+20
        while not (data_dir/'ready').exists()and child.poll()is None and time.monotonic()<deadline:time.sleep(.025)
        if not (data_dir/'ready').exists():
            child.kill();out,err=child.communicate(timeout=10);pytest.fail('fixture did not reach kill point: '+err.decode(errors='replace'))
        child.kill();child.wait(timeout=10)  # actual OS termination, not a fake exception
        c=sqlite3.connect(data_dir/'app.db');count=c.execute('select count(*) from posts').fetchone()[0];c.close()
        assert count==(20 if point=='fetch'else 10)
    finally:
        if child.poll()is None:child.kill();child.wait()
    env.pop('KILL_POINT')
    complete=subprocess.run([sys.executable,'-c',CHILD],cwd=cwd,env=env,capture_output=True,timeout=30)
    assert complete.returncode==0,complete.stderr.decode(errors='replace')
    assert json.loads(complete.stdout)['status']=='completed'
    c=sqlite3.connect(data_dir/'app.db');assert c.execute('select count(*) from posts').fetchone()[0]==60
    assert c.execute('select count(*) from images').fetchone()[0]==60
    assert len(list((data_dir/'images').iterdir()))==60;c.close()
    # One more real process restart: no duplicate rows, no additional files.
    again=subprocess.run([sys.executable,'-c',CHILD],cwd=cwd,env=env,capture_output=True,timeout=30)
    assert again.returncode==0
    c=sqlite3.connect(data_dir/'app.db');assert c.execute('select count(*) from posts').fetchone()[0]==60;c.close()


def test_pinterest_60_pin_pagination_and_board_indices_are_independent(tmp_path):
    r=vendor().Reader({},'fixture',str(tmp_path),download_images=False)
    r.s.whoami=lambda user:{'username':user,'id':'1'}
    r.s.boards=lambda *a,**k:[{'id':'b','name':'board'}]
    def pin(n):return {'id':str(n),'type':'pin','images':{'orig':{'url':f'https://i/{n}.jpg'}}}
    replies=iter([{'resource_response':{'data':[pin(n)for n in range(k*20,(k+1)*20)]},'resource':{'options':{'bookmarks':['-end-'if k==2 else f'b{k+1}']}}}for k in range(3)]+[{'resource_response':{'data':[pin(99)]},'resource':{'options':{'bookmarks':['-end-']}}}])
    r.s.get=lambda url:next(replies)
    r.run(None)
    pages=[json.loads(p.read_text(encoding='utf8'))for p in tmp_path.glob('page-*.json')]
    fav=sorted([p for p in pages if p['collection']['collection_key']=='fav'],key=lambda p:p['page']['index'])
    assert sum(len(p['items'])for p in fav)==60 and [p['page']['index']for p in fav]==[1,2,3]
    board=next(p for p in pages if p['collection']['collection_key']=='board:b')
    assert board['page']['index']==1 and len(pages)==4


def test_missing_xhs_url_preserves_native_slot(data_dir):
    from test_xhs_sync import FakeBrowser,note,nid,slots,UID
    from app.sync.readers import xhs
    db.init_db();profile=data_dir/'profile';xhs.write_account(profile,UID)
    browser=FakeBrowser([note(1,3)])
    browser.details[nid(1)]={'status':'ok','card':{'noteId':nid(1)},'images':[{'index':0,'url':'https://i/1.png'},{'index':1,'url':''},{'index':2,'url':'https://i/3.png'}]}
    with xhs.XhsReader(browser,profile,data_dir/'staging',sleep=lambda _:None)as reader:
        result=engine.run(reader,'fav','bootstrap')
    assert result['status']=='partial'
    assert [(x[0],x[1])for x in slots(nid(1))]==[(0,'ok'),(1,'failed'),(2,'ok')]


def test_pinterest_resume_input_comes_only_from_committed_database(data_dir,tmp_path,monkeypatch):
    db.init_db()
    c=db.connect();c.execute('BEGIN');store._save_cursor(c,'pinterest','fav','bootstrap',dict(next_cursor='DB-bookmark',page_index=2,has_more=True),'fixture');c.execute('COMMIT');c.close()
    out=tmp_path/'reader';cookie=tmp_path/'cookies.json';cookie.write_text('{}')
    vendor_checkpoint=tmp_path/'untrusted.json';vendor_checkpoint.write_text(json.dumps({'fav':{'cursor':'NOT-COMMITTED'}}))
    monkeypatch.setattr(ps,'cookies_path',lambda:cookie);monkeypatch.setattr(ps,'_auth_username',lambda:'fixture');monkeypatch.setattr(ps,'checkpoint_path',lambda:vendor_checkpoint)
    observed=[]
    def run(cmd,**kwargs):
        snapshot=json.loads(Path(cmd[cmd.index('--checkpoint')+1]).read_text(encoding='utf8'));observed.append(snapshot)
        (out/'done.json').write_text(json.dumps({'status':'ok'}))
        class Done:stderr='';returncode=0
        return Done()
    monkeypatch.setattr(ps.subprocess,'run',run)
    with ps.PinterestExternalReader('bootstrap',out):pass
    assert observed[0]['fav']['cursor']=='DB-bookmark' and observed[0]['fav']['page_index']==2
    assert json.loads(vendor_checkpoint.read_text())['fav']['cursor']=='NOT-COMMITTED'
