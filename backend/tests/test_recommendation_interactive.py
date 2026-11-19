import io
import threading
from types import SimpleNamespace
import numpy as np
from PIL import Image
from fastapi.testclient import TestClient
from app import creative,db,recommendation_feedback as feedback
from app.main import create_app
from app.embedding.adapter import EmbeddingAdapter
from test_creative import indexed,index_dimensions,FakeReviewer,fake_needs  # noqa
from test_recommendation_wiring import seeded  # noqa
from test_search_wiring import SETTINGS,FakeQueryFactory


def visual_index(ids):
    matrix=np.zeros((len(ids),len(ids)));np.fill_diagonal(matrix,1)
    return SimpleNamespace(_cache=lambda:{'ids':np.array(ids),'matrix':matrix,'positions':{i:n for n,i in enumerate(ids)},'by_id':{i:{'sha256':str(i)}for i in ids}})


def test_first_finished_column_emits_before_other_reviews_finish(indexed):
    released=threading.Event();seen=[];reviewer=FakeReviewer()
    def review(column,*args):
        if column!='style':assert released.wait(3),'producer waited for first column rather than first completed column'
        return reviewer(column,*args)
    def partial(value):
        seen.append(value['columns'][0]['purpose']);released.set()
    app=creative.CreativeApplication(database=lambda:indexed,settings_provider=lambda:SETTINGS,
        client_factory=FakeQueryFactory(),vision_settings=lambda:{},reviewer=review,needs_parser=fake_needs)
    result=app.recommend('建筑展板',on_column=partial)
    assert seen[0]=='style' and len(seen)==3
    assert [c['purpose']for c in result['columns']]==['layout','palette','style']


def test_project_signals_are_soft_scoped_and_feedback_does_not_remove_pins(indexed):
    conn=db.connect();ids=[r[0]for r in conn.execute('SELECT id FROM images LIMIT 3')]
    p=conn.execute("INSERT INTO projects(name) VALUES ('one')").lastrowid
    other=conn.execute("INSERT INTO projects(name) VALUES ('two')").lastrowid
    conn.execute('INSERT INTO pins(project_id,image_id,purpose) VALUES (?,?,?)',(p,ids[0],'["layout"]'))
    index=visual_index(ids);adjust=feedback.adjuster(conn,index,'layout',p)
    assert adjust(ids[0],.5)==.53 and adjust(ids[1],.5)==.5
    assert feedback.adjuster(conn,index,'layout',other) is None
    conn.close();feedback.record(p,ids[0],'layout',False)
    conn=db.connect();adjust=feedback.adjuster(conn,index,'layout',p)
    assert adjust(ids[0],.5)==.47
    assert conn.execute('SELECT COUNT(*) FROM pins').fetchone()[0]==1
    conn.close()


def test_more_like_only_reviews_requested_column_without_embedding_or_decomposition(indexed):
    conn=db.connect();ids=[r[0]for r in conn.execute('SELECT id FROM images')];conn.close()
    reviewer=FakeReviewer()
    def forbidden(*args):raise AssertionError('more-like requested text decomposition or embedding')
    app=creative.CreativeApplication(database=lambda:indexed,reviewer=reviewer,vision_settings=lambda:{},
        client_factory=forbidden,needs_parser=forbidden,image_similarity=visual_index(ids))
    result=app.more_like('海报','style',ids[0],negative_ids=[ids[1]])
    assert len(reviewer.calls)==1 and reviewer.calls[0][0]=='style'
    assert ids[0] not in reviewer.calls[0][2] and ids[1] not in reviewer.calls[0][2]
    assert len(result['columns'])==1


def test_manual_color_overrides_reach_only_palette_need(indexed):
    reviewer=FakeReviewer();factory=FakeQueryFactory()
    app=creative.CreativeApplication(database=lambda:indexed,settings_provider=lambda:SETTINGS,
        client_factory=factory,vision_settings=lambda:{},reviewer=reviewer,needs_parser=fake_needs)
    result=app.recommend('建筑展板',color_need=[{'hex':'#123456','role':'为主'},{'hex':'#ffdd00','role':'点缀'}])
    assert result['needs']['color_need'][0]['hex']=='#123456'
    assert '#123456' in next(c[1]for c in reviewer.calls if c[0]=='palette')
    assert '#123456' not in next(c[1]for c in reviewer.calls if c[0]=='layout')


def test_palette_upload_and_ndjson_api_are_local_and_wired(indexed,backend_options):
    factory=FakeQueryFactory();reviewer=FakeReviewer()
    client=TestClient(create_app(embedder=EmbeddingAdapter(SETTINGS),search_client_factory=factory,
        creative_reviewer=reviewer,creative_needs_parser=fake_needs),backend_options=backend_options)
    image=Image.new('RGB',(100,100),'#112233');buffer=io.BytesIO();image.save(buffer,format='PNG')
    colors=client.post('/api/palette/extract',files={'file':('palette.png',buffer.getvalue(),'image/png')})
    assert colors.status_code==200 and colors.json()['colors']==[{'hex':'#112233','ratio':1.0}]
    assert not factory.queries
    response=client.post('/api/recommendations/stream',json={'brief':'建筑展板'})
    assert response.status_code==200 and 'application/x-ndjson' in response.headers['content-type']
    import json
    events=[json.loads(line)for line in response.iter_lines() if line]
    assert [e['event']for e in events].count('column')==3 and events[-1]['event']=='done'


def test_fast_decomposition_is_explicitly_opt_in_after_dev_regression():
    import json,httpx
    seen=[]
    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps({'layout_need':'网格','style_need':'现代','color_need':[]})}}],
            'usage':{'prompt_tokens':1,'completion_tokens':1}})
    settings={'base_url':'https://fixture.invalid/v1','api_key':'fixture','vision_model':'fixture','enable_thinking':False}
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        creative.decompose_needs('海报',settings,client=client)
        creative.decompose_needs('海报',{**settings,'fast_recommendation_needs':True},client=client)
    assert 'enable_thinking' not in seen[0]
    assert seen[1]['enable_thinking'] is False
