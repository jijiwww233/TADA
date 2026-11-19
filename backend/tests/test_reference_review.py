import pytest
from app import db, reference_review as review
from app.reference_value import labels,eligible_ids,allowed_analysis
from test_recommendation_wiring import seeded  # noqa


def test_manual_binary_is_separate_from_content_category_and_undo_restores(seeded):
    conn=db.connect();ids=[r[0] for r in conn.execute('SELECT id FROM images LIMIT 2')]
    for i in ids:conn.execute("INSERT INTO image_reference_labels(image_id,auto_category) VALUES (?,'uncertain')",(i,))
    conn.close()
    result=review.apply(ids,False)
    conn=db.connect()
    for i in ids:
        assert labels(conn)[i]['category']=='uncertain'  # not falsely labelled as a text card
        assert labels(conn)[i]['manual_reference']==0 and i not in eligible_ids(conn)
        assert not allowed_analysis(conn,i)
    conn.close()
    assert review.undo(result['operation'])=={'restored':ids,'conflicts':[]}
    conn=db.connect();assert all(labels(conn)[i]['manual_reference'] is None for i in ids);conn.close()


def test_undo_never_overwrites_later_manual_choice_and_history_is_kept(seeded):
    conn=db.connect();ids=[r[0]for r in conn.execute('SELECT id FROM images LIMIT 2')];conn.close()
    first=review.apply(ids,True);review.apply([ids[0]],False)
    result=review.undo(first['operation'])
    assert result=={'restored':[ids[1]],'conflicts':[ids[0]]}
    conn=db.connect();assert labels(conn)[ids[0]]['manual_reference']==0
    assert conn.execute('SELECT COUNT(*) FROM reference_manual_events').fetchone()[0]==4
    assert conn.execute('SELECT COUNT(*) FROM images').fetchone()[0]>0;conn.close()


def test_missing_image_rolls_back_whole_batch(seeded):
    conn=db.connect();i=conn.execute('SELECT id FROM images LIMIT 1').fetchone()[0];conn.close()
    with pytest.raises(KeyError):review.apply([i,999999],False)
    conn=db.connect();assert conn.execute('SELECT COUNT(*) FROM reference_manual_events').fetchone()[0]==0;conn.close()


def test_manual_visual_and_confirmation_precedence(seeded):
    conn=db.connect();i=conn.execute('SELECT id FROM images LIMIT 1').fetchone()[0]
    conn.execute("INSERT INTO image_reference_labels(image_id,auto_category) VALUES (?,'text_card')",(i,));conn.close()
    review.apply([i],True)
    conn=db.connect();assert i in eligible_ids(conn) and allowed_analysis(conn,i);conn.close()
    review.apply([i],False)
    conn=db.connect();assert not allowed_analysis(conn,i)
    conn.execute('UPDATE image_reference_labels SET confirmed=1 WHERE image_id=?',(i,));conn.close()
    # A later explicit "allow paid analysis" confirmation is distinct from reference usefulness.
    conn=db.connect();assert allowed_analysis(conn,i);conn.close()


def test_manual_four_way_classification_can_replace_binary_choice(seeded):
    conn=db.connect();i=conn.execute('SELECT id FROM images LIMIT 1').fetchone()[0];conn.close()
    review.apply([i],False);review.apply([i],category='visual_ref')
    conn=db.connect();row=labels(conn)[i]
    assert row['manual_reference'] is None and row['manual_category']=='visual_ref' and i in eligible_ids(conn)
    conn.close()


def test_stale_candidate_auto_labels_cannot_enter_paid_analysis(seeded):
    conn=db.connect();i=conn.execute('SELECT id FROM images LIMIT 1').fetchone()[0]
    conn.execute("INSERT INTO image_reference_labels(image_id,auto_category,version) VALUES (?,'visual_ref','reference_candidate_v2')",(i,))
    assert not allowed_analysis(conn,i)
    conn.close()
    review.apply([i],True)
    conn=db.connect();assert allowed_analysis(conn,i);conn.close()
