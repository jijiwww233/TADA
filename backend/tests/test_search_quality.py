"""Search-quality helpers: object rejection, keyword boosts and palette fusion."""
import pytest

from app import db, local_discovery, search_terms
from app.embedding.search import SearchHit
from app.embedding.search_application import fuse_hits


@pytest.fixture
def library(data_dir):
    db.init_db()
    conn = db.connect()
    rows = [
        (1, "一只手捏着白色雏菊的花茎，另一只手下接住飘落的花瓣", '["摄影"]',
         '{"tags":["花卉","清新"]}'),
        (2, "三位身着植物叶片和花朵构成服饰的女性形象", '["插画"]',
         '{"tags":["复古"]}'),
        (3, "一张关于天文学中月球知识的深色背景信息图", '["海报"]',
         '{"tags":["海报"]}'),
        (4, "多栏展板排版，米白色页面与黑色标题", '["展板"]',
         '{"tags":["极简"]}'),
    ]
    try:
        conn.execute(
            "INSERT INTO posts(id,platform,source_id,title,url) VALUES(1,'local','x','测试','')")
        for image_id, description, subject, style in rows:
            conn.execute(
                "INSERT INTO images(id,post_id,position,sha256,file_path,width,height,original_name) "
                "VALUES(?,1,?,?,'images/%d.png',1,1,?)", (image_id, image_id, f"sha-{image_id}", image_id))
            conn.execute(
                "INSERT INTO analyses(image_id,status,description,subject,style_json) "
                "VALUES(?,'done',?,?,?)", (image_id, description, subject, style))
        conn.commit()
    finally:
        conn.close()
    return data_dir


def test_long_query_with_absent_head_object_is_rejected(library):
    assert search_terms.missing_main_object("一只透明的橙色鲸鱼在骑自行车") is True
    assert search_terms.missing_main_object("长着翅膀的茶杯") is True
    assert search_terms.missing_main_object("月亮形状的草莓蛋糕") is True
    assert search_terms.missing_main_object("会发光的蘑菇房子") is True
    # Short, colour/style and present-artifact queries keep their results.
    assert search_terms.missing_main_object("海") is False
    assert search_terms.missing_main_object("蓝绿色为主看起来很清爽") is False
    assert search_terms.missing_main_object("米白色、多栏的建筑展板？") is False


def test_object_finder_prefers_the_longest_compound(library):
    objects = search_terms.find_objects("会发光的蘑菇房子")
    assert objects[-1][2] == "蘑菇房子"
    assert [term for _, _, term in objects].count("蘑菇") == 0


def test_keyword_boost_rewards_the_rarer_matching_word(library):
    boosts = search_terms.keyword_boosts("花")
    assert boosts.get(1, 0) > 0
    assert boosts[1] > boosts.get(2, 0)  # 雏菊 is rarer than 花朵


def test_compound_colour_words_join_the_palette_channel():
    teal = {target.hex for target in local_discovery.color_targets("蓝绿色为主看起来很清爽")}
    assert {"#2e8b8b", "#008000"} <= teal
    warm = {target.hex for target in local_discovery.color_targets("暖黄色和棕色的复古色调")}
    assert {"#ffff00", "#8b5a2b"} <= warm


def test_fusion_boost_is_added_and_scores_stay_in_range():
    combined = [SearchHit(1, 0.9), SearchHit(2, 0.8), SearchHit(3, 0.1)]
    clip = {1: 0.1, 2: 0.2, 3: 1.0}
    boosted = fuse_hits(combined, clip, alpha=0.4, top_n=3, boosts={3: 0.6})
    assert boosted[0].image_id == 3
    assert all(0.0 <= hit.score <= 1.0 for hit in boosted)
