import json
from app.recommendation_needs_cache import NeedsCache


def test_repeat_reuses_only_needs_and_records_no_new_paid_usage(tmp_path):
    calls=[]
    def parser(brief,settings):
        calls.append(brief)
        return {'needs':{'layout_need':'网格','style_need':'极简','color_need':[]},'usage':{'requests':1}}
    cache=NeedsCache(tmp_path,'prompt',parser);settings={'vision_model':'one','base_url':'https://example.invalid','enable_thinking':False}
    assert cache('海报',settings)['usage']['requests']==1
    repeat=cache('海报',settings)
    assert repeat['needs']['layout_need']=='网格' and repeat['usage']['requests']==0 and len(calls)==1
    stored=json.loads(cache.path('海报',settings).read_text(encoding='utf-8'))
    assert set(stored)=={'needs'}


def test_corrupt_existing_cache_is_preserved_and_model_provider_change_misses(tmp_path):
    cache=NeedsCache(tmp_path,'prompt',lambda *_:{'needs':{'layout_need':'网格'}})
    settings={'vision_model':'one','base_url':'provider'}
    path=cache.path('海报',settings);path.write_text('corrupt')
    assert cache('海报',settings)['needs']['layout_need']=='网格'
    assert path.read_text()=='corrupt'
    assert cache.path('海报',{**settings,'vision_model':'two'})!=path
