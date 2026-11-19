import importlib.util
import json
from pathlib import Path
import pytest
from app import eval_api
from app.eval_tasks import validate
from test_eval_label_api import make_client
spec=importlib.util.spec_from_file_location('f3',Path(__file__).resolve().parents[1]/'tools/eval_f3.py');f3=importlib.util.module_from_spec(spec);spec.loader.exec_module(f3)

def task(id='dev',kind='create',split='dev'):
    return dict(id=id,type=kind,query='建筑展板',split=split,expected=dict(find=[],layout=[1,2],color=[3],style=[4]),note='')

def test_columns_score_separately_and_top5_cutoff():
    t=task();response={'columns':[{'purpose':'layout','items':[{'image_id':i}for i in [1,9,8,7,6,2]]},{'purpose':'palette','items':[{'image_id':4}]},{'purpose':'style','items':[{'image_id':4}]}]}
    r=f3.evaluate({'version':1,'tasks':[t]},None,lambda *a:response)['per_task'][0]['columns']
    assert r['layout']['hits']==[1] and r['layout']['missing']==[2]
    assert r['color']['hits']==[] and r['color']['missing']==[3] and r['style']['hits']==[4]

def test_default_dev_never_calls_test_and_usage():
    a=task();b=task('secret',split='test');calls=[]
    def respond(id,q):
        calls.append(id);return {'usage':{'requests':2,'prompt_tokens':10,'completion_tokens':3}}
    result=f3.evaluate({'version':1,'tasks':[a,b]},respond,respond)
    assert calls==['dev'] and result['usage']['tokens']==13 and result['usage']['requests']==2
    f3.evaluate({'version':1,'tasks':[a,b]},respond,respond,final=True);assert calls[-1]=='secret'

def test_unlabeled_never_passes_and_errors_redacted():
    a=task();a['expected']={k:[]for k in a['expected']}
    r=f3.evaluate({'version':1,'tasks':[a]},None,lambda *a:{})
    assert r['unlabeled_columns']==3 and all(c['hit']is None for c in r['per_task'][0]['columns'].values())
    def fail(*args):raise ValueError('secret-key')
    r=f3.evaluate({'version':1,'tasks':[a]},None,fail);assert 'secret-key' not in json.dumps(r)

@pytest.mark.parametrize('ids',[[0],[True],['1'],[-1]])
def test_invalid_ids_rejected(ids):
    a=task();a['expected']['color']=ids
    with pytest.raises(ValueError):validate({'version':1,'tasks':[a]})

def test_canonical_roundtrip_and_all_source_positions(tmp_path,data_dir):
    client,path=make_client(tmp_path,data_dir);a=task();a['expected']=dict(find=[],layout=[1],color=[1],style=[])
    assert client.put('/api/eval/tasks',json={'version':1,'tasks':[a]}).status_code==200
    assert eval_api.read_tasks(path)['tasks']==[a]
    assert client.get('/api/eval/library').json()['items'][0]['platforms']==['xhs']
    # Same image in a second post must still be a single thumbnail with both platforms.
    import sqlite3
    c=sqlite3.connect(data_dir/'app.db');c.execute("PRAGMA ignore_check_constraints=ON");c.execute("INSERT INTO posts(id,platform,source_id,title)VALUES(2,'pinterest','p2','two')");c.execute("INSERT INTO post_images(post_id,position,image_id,download_state)VALUES(2,1,1,'ok')");c.commit();c.close()
    items=client.get('/api/eval/library').json()['items'];assert len(items)==1 and items[0]['platforms']==['pinterest','xhs']

def test_test_cli_requires_final_before_invocation(tmp_path):
    with pytest.raises(SystemExit)as exc:f3.main(['--split','test','--output',str(tmp_path/'out.json')])
    assert exc.value.code==2 and not (tmp_path/'out.json').exists()

def test_mock_cli_is_zero_calls_and_no_overwrite(tmp_path):
    t=task();p=tmp_path/'tasks.json';p.write_text(json.dumps({'version':1,'tasks':[t]}));out=tmp_path/'result.json'
    assert f3.main(['--tasks',str(p),'--mock','--output',str(out)])==0
    result=json.loads(out.read_text());assert result['usage']['requests']==0 and result['usage']['tokens']==0
    assert all(c['status']=='mock_unscored'for c in result['per_task'][0]['columns'].values())
    with pytest.raises(SystemExit):f3.main(['--tasks',str(p),'--mock','--output',str(out)])

def test_cli_reads_the_f4_live_package_wrapper(tmp_path):
    t=task();p=tmp_path/'tasks.json';p.write_text(json.dumps({'version':1,'tasks':[t]}))
    response={'format':'f4-live-responses-v1','split':'dev',
              'responses':{'dev':{'columns':[{'purpose':'layout','items':[{'image_id':1}]},
                                             {'purpose':'palette','items':[]},
                                             {'purpose':'style','items':[]}]}}}
    rp=tmp_path/'responses.json';rp.write_text(json.dumps(response));out=tmp_path/'result.json'
    assert f3.main(['--tasks',str(p),'--responses',str(rp),'--output',str(out)])==0
    result=json.loads(out.read_text())
    layout=result['per_task'][0]['columns']['layout']
    assert layout['status']=='scored' and layout['hits']==[1]


def test_find_rank_and_top20_counts():
    t=task(kind='find')
    t['expected']=dict(find=[20],layout=[],color=[],style=[])
    result=f3.evaluate({'version':1,'tasks':[t]},lambda *args:{'items':list(range(1,22))},None)
    assert result['per_task'][0]['columns']['find']['rank']==20
    assert result['find_summary']=={'scored':1,'top5':0,'top20':1}
