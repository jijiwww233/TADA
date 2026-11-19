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

def test_network_meme_is_uncertain_not_a_design_reference():
    meme = dict(subject=json.dumps(['表情包', '插画', '网络文化']),
                description='一张展示情绪循环的网络表情包，配文表达崩溃心情',
                style_json=json.dumps({'medium': 'illustration',
                                       'style_note': '像素风企鹅与手绘线条，充满网络迷因感'}),
                layout_json=json.dumps({'image_text_ratio': ['balanced']}))
    for classify in (ref.heuristic, ref.candidate_heuristic):
        category, reason, _ = classify(meme)
        assert category == 'uncertain' and '表情包' in reason
    sticker = dict(subject=json.dumps(['贴纸', '聊天']), description='微信聊天里保存的搞笑贴纸',
                   style_json=json.dumps({'medium': 'illustration'}),
                   layout_json=json.dumps({'image_text_ratio': ['image_dominant']}))
    assert ref.heuristic(sticker)[0] == 'uncertain'
    assert ref.candidate_heuristic(sticker)[0] == 'uncertain'


def test_poster_copy_mentioning_expression_is_still_a_visual_reference():
    poster = dict(subject=json.dumps(['海报', '插画']),
                  description='人物表情夸张的节日海报设计，画面表情生动丰富',
                  style_json=json.dumps({'medium': 'illustration'}),
                  layout_json=json.dumps({'image_text_ratio': ['image_dominant']}))
    assert ref.heuristic(poster)[0] == 'visual_ref'
    assert ref.candidate_heuristic(poster)[0] == 'visual_ref'


def test_sticker_design_work_is_not_a_chat_sticker():
    # A sticker is a design product on its own; only network wording makes it a chat
    # sticker. This guards 文创贴纸/包装贴纸 showcases against the new rule.
    work = dict(subject=json.dumps(['贴纸', '包装', '平面设计']),
                description='一张展示圆形贴纸的包装样机，贴纸图案是抽象几何渐变',
                style_json=json.dumps({'medium': '3d'}),
                layout_json=json.dumps({'image_text_ratio': ['image_dominant']}))
    assert ref.heuristic(work)[0] == 'visual_ref'
    assert ref.candidate_heuristic(work)[0] == 'visual_ref'


def test_stale_reference_version_is_reclassified_before_analysis(indexed):
    conn = db.connect()
    image_id = conn.execute('SELECT id FROM images LIMIT 1').fetchone()[0]
    conn.execute('INSERT INTO image_reference_labels(image_id,auto_category,version) VALUES (?,?,?)',
                 (image_id, 'visual_ref', 'reference_value_v1'))
    assert not ref.allowed_analysis(conn, image_id)  # old-version auto labels are stale
    conn.execute('UPDATE image_reference_labels SET version=? WHERE image_id=?', (ref.VERSION, image_id))
    assert ref.allowed_analysis(conn, image_id)
    conn.close()
