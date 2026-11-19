"""搜索同义词：输入「云」时也能找到云朵、云层和天空。"""
from app import local_discovery


def test_cloud_expands_to_cloud_and_sky_words():
    assert set(local_discovery.SHORT_EXPANSIONS["云"]) == {"云", "云朵", "云层", "天空"}
