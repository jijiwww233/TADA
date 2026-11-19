import json
import numpy as np
from app import db, reference_value as ref
from test_creative import indexed, index_dimensions, FakeReviewer, fake_needs  # noqa
from test_recommendation_wiring import seeded  # noqa
from test_search_wiring import SETTINGS, FakeQueryFactory
from app.creative import CreativeApplication, dimension_recall


def test_typography_poster_kept_but_combined_text_evidence_excluded():
    poster = dict(description='字体海报设计', visible_text='标题',
                  style_json=json.dumps({'medium':'typography'}),
                  layout_json=json.dumps({'image_text_ratio':['text_dominant']}))
    assert ref.heuristic(poster)[0] == 'visual_ref'
    poster.update(visible_text='长文字' * 100, description='纯文字卡片')
    assert ref.heuristic(poster)[0] == 'text_card'
    assert ref.heuristic({})[0] == 'uncertain'


def test_manual_wins_and_confirmation_gates_analysis(indexed):
    conn = db.connect()
    image_id = conn.execute('SELECT id FROM images LIMIT 1').fetchone()[0]
    assert not ref.allowed_analysis(conn,image_id)
    conn.execute('INSERT INTO image_reference_labels(image_id,auto_category,manual_category) VALUES (?,?,?)',
                 (image_id,'visual_ref','life'))
    assert ref.labels(conn)[image_id]['category'] == 'life'
    assert image_id not in ref.eligible_ids(conn)
    assert not ref.allowed_analysis(conn,image_id)
    conn.execute('UPDATE image_reference_labels SET confirmed=1 WHERE image_id=?',(image_id,))
    assert ref.allowed_analysis(conn,image_id)
    conn.close()


def test_filter_before_topn_and_include_switch(indexed):
    conn=db.connect()
    ids=[r[0] for r in conn.execute('SELECT id FROM images')]
    for i in ids:
        conn.execute('INSERT INTO image_reference_labels(image_id,auto_category) VALUES (?,?)',(i,'text_card'))
    conn.close()
    reviewer=FakeReviewer()
    app=CreativeApplication(database=lambda:indexed,settings_provider=lambda:SETTINGS,
        client_factory=FakeQueryFactory(),vision_settings=lambda:{},reviewer=reviewer,needs_parser=fake_needs)
    assert all(not c['items'] for c in app.recommend('海报')['columns'])
    assert any(c['items'] for c in app.recommend('海报',include_nonvisual=True)['columns'])


def test_zero_shot_is_local_and_manual_survives_reclassification(indexed):
    from types import SimpleNamespace
    conn=db.connect()
    i=conn.execute('SELECT id FROM images LIMIT 1').fetchone()[0]
    conn.execute("UPDATE analyses SET status='pending' WHERE image_id=?",(i,))
    conn.execute("INSERT INTO image_reference_labels(image_id,manual_category) VALUES (?,'visual_ref')",(i,))
    conn.close()
    class Encoder:
        def encode_texts(self,texts): return np.eye(3).tolist()
    similar=SimpleNamespace(encoder=Encoder(),database=lambda:indexed,_cache=lambda:{'ids':[i],'matrix':np.array([[0,1.,0]])})
    service=ref.ReferenceLabels(similar)
    service.classify_pending()
    conn=db.connect()
    row=ref.labels(conn)[i]
    assert row['auto_category']=='text_card' and row['category']=='visual_ref'
    assert ref.allowed_analysis(conn,i)
    conn.close()
