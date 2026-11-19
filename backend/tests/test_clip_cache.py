import time
from app import db
from test_similarity import indexed

def test_hot_text_query_encodes_once_and_reuses_matrix(indexed):
    service,encoder,ids=indexed;calls=[]
    encoder.encode_texts=lambda texts:(calls.append(texts)or [[1.,0.,0.]+[0.]*509])
    matrix=service._cache()['matrix'];before=service._rows
    def forbidden():raise AssertionError('hot query must not reload image vectors')
    service._rows=forbidden
    a=service.search_text('a');b=service.search_text('b')
    assert len(calls)==2 and len(a)==25 and a==b and service._cache()['matrix']is matrix
    service._rows=before;service.shutdown()

def test_cache_invalidates_after_external_commit_and_model_change(indexed):
    service,encoder,ids=indexed;service._cache()
    conn=db.connect();conn.execute('UPDATE images SET sha256=? WHERE id=?',('changed',ids[0]));conn.close()
    assert ids[0]not in service.valid_ids()
    encoder.identity='changed-model';assert not service.valid_ids();service.shutdown()

def test_start_preloads_off_main_thread_without_queries(indexed):
    service,encoder,ids=indexed;called=[]
    encoder.warmup=lambda:called.append('preloaded')
    service.start();deadline=time.monotonic()+3
    while not service.warmed and time.monotonic()<deadline:time.sleep(.01)
    assert service.warmed and called==['preloaded'] and service.warmup_ms is not None
    service.shutdown()
