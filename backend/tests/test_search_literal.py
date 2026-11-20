"""Literal text recall and the relevance gate.

P0-2 fix: a query word that is literally written in an image's card title, description
or tags must be answerable as strong for phrase queries that name no library object and
no colour (复古 / 国潮 / 胶片), even when both semantic routes missed the row. Object and
colour queries keep their previous subject/colour rules, so 花 / 蓝色衣服 / 紫色小花 do
not suddenly turn every text neighbour into a strong match.
"""
import json

import pytest

from app import db, search_terms


@pytest.fixture
def library(data_dir):
    db.init_db()
    conn = db.connect()
    rows = [
        # image_id, title, description, style tags, manual title
        (1, "复古西部风情汽车旅馆", "一张汽车旅馆宣传插画，画面左侧是挥舞套索的牛仔", ["手绘"], None),
        (2, "花间集", "一张复古胶片风模糊枝叶摄影", [], None),
        (3, None, "一张桌面手账拼贴摄影", ["日系"], "复古手账合集"),
        (4, None, "淡紫色小花在深色背景上开放", [], None),
        (5, "城市夜景", "一条无人的街道与远处的路灯", [], None),
    ]
    try:
        conn.execute("INSERT INTO posts(id,platform,source_id,title,url) VALUES(1,'local','x','测试','')")
        for image_id, title, description, tags, manual in rows:
            conn.execute(
                "INSERT INTO images(id,post_id,position,sha256,file_path,width,height,original_name) "
                "VALUES(?,1,?,?,'images/%d.png',1,1,?)",
                (image_id, image_id, f"sha-{image_id}", image_id))
            conn.execute(
                "INSERT INTO analyses(image_id,status,description,subject,style_json,title) "
                "VALUES(?,'done',?,?,?,?)",
                (image_id, description, '["摄影"]',
                 json.dumps({"tags": tags}, ensure_ascii=False), title))
            if manual:
                conn.execute(
                    "INSERT INTO image_titles_manual(image_id,title,updated_at) VALUES(?,?,datetime('now'))",
                    (image_id, manual))
        conn.commit()
    finally:
        conn.close()
    return data_dir


def item(image_id, score=0.5, **evidence):
    return {"image_id": image_id, "score": score, **{f"_{k}": v for k, v in evidence.items()}}


def relevance_of(result):
    return {row["image_id"]: row["relevance"] for row in result["items"]}


def test_literal_phrase_is_specific_enough_to_recall():
    assert search_terms._literal_phrase("复古") == "复古"
    assert search_terms._literal_phrase(" 复古？ ") == "复古"
    # Single characters stay on the object pipeline; a whitespace query is not a phrase.
    assert search_terms._literal_phrase("花") == ""
    assert search_terms._literal_phrase("复古 海报") == ""


def test_literal_hits_order_title_hits_before_description_and_manual_titles(library):
    # image 1 has 复古 in its generated title, image 3 only in its manual title,
    # image 2 only in the description: both title rows lead, then the description row.
    assert search_terms.literal_hits("复古") == [1, 3, 2]
    assert search_terms.literal_hits("不存在的词") == []


def test_phrase_query_recalls_literal_rows_the_routes_missed(library):
    result = {"status": "ok", "items": [item(5, 0.5, text_cos=0.5)]}
    gated = search_terms.apply_relevance(result, "复古")
    by_id = relevance_of(gated)
    assert set(by_id) == {1, 2, 3, 5}
    assert by_id[1] == by_id[2] == by_id[3] == "strong"
    # Title hits are wall-first, and the already-ranked row keeps its place behind them.
    assert [row["image_id"] for row in gated["items"]][:3] == [1, 3, 2]
    assert gated["items"][-1]["image_id"] == 5
    # Recalled rows are hydrated presentation rows, not bare ids.
    assert gated["items"][0]["url"].startswith("/images/") and "score" in gated["items"][0]


def test_phrase_query_without_literal_hits_keeps_the_mood_gate(library):
    result = {"status": "ok", "items": [item(5, 0.2, clip_cos=0.2)]}
    gated = search_terms.apply_relevance(result, "复古建筑")  # has an object word
    assert gated["items"] == []
    result = {"status": "ok", "items": [item(5, 0.2, clip_cos=0.2)]}
    gated = search_terms.apply_relevance(result, "水墨")  # no literal hit at all
    assert gated["items"] == []


def test_object_and_colour_queries_do_not_inject_text_neighbours(library):
    # 花 names a library object: the subject/colour evidence rules stay in charge, so a
    # literal 花 row is not forced strong just because it is written in the text.
    gated = search_terms.apply_relevance(
        {"status": "ok", "items": [item(5, 0.5, text_cos=0.5)]}, "花")
    assert [row["image_id"] for row in gated["items"]] == [5]

    # 紫色小花 has a colour requirement: the purple row with weak evidence stays weak.
    gated = search_terms.apply_relevance(
        {"status": "ok", "items": [item(4, 0.5, text_cos=0.2)]}, "紫色小花")
    assert "strong" not in relevance_of(gated).values()

    # 蓝色衣服 has only a colour word: no strong row without enough colour evidence.
    gated = search_terms.apply_relevance(
        {"status": "ok", "items": [item(5, 0.5, clip_cos=0.2)]}, "蓝色衣服")
    assert "strong" not in relevance_of(gated).values()


def test_keyword_boost_sees_titles_and_manual_titles(library):
    boosts = search_terms.keyword_boosts("花")
    assert 2 in boosts  # 花间集 title
    assert 4 in boosts  # 紫色小花 description
    assert 5 not in boosts


def test_missing_main_object_reads_titles(library):
    # The head object only exists in the card title; it must not be reported missing.
    assert search_terms.missing_main_object("会发光的蘑菇房子") is True  # no such title
    conn = db.connect()
    try:
        conn.execute("UPDATE analyses SET title='会发光的蘑菇房子' WHERE image_id=5")
        conn.commit()
    finally:
        conn.close()
    assert search_terms.missing_main_object("会发光的蘑菇房子") is False
