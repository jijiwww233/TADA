"""Offline fixtures only: no app factory, database, model, environment or images."""
from copy import deepcopy
import re

import pytest

from app.recommendation import parse_brief, recommend


_BOARD = object()


def candidate(i, *, score=None, subject=None, layout=None, tags=None, colors=None,
              post=None, sha=None, description=_BOARD, medium="typography", **extra):
    # Default candidate is a board. Its identity lives in subject (an identity label),
    # not in prose: a description that merely mentions 展板 is not artifact evidence.
    subject = list(subject or [])
    if description is _BOARD:
        description, subject = "一张展板", subject + ["展板"]
    return {
        "image_id": i, "semantic_score": score if score is not None else 1 - i / 100,
        "sha256": sha or f"sha-{i}", "post_images": [{"post_id": post if post is not None else i}],
        "analyses": {"subject": subject, "layout": layout or {},
                     "style": {"tags": tags or [], "medium": medium, "style_note": ""},
                     "description": description, "visible_text": ""},
        "colors": colors or [], **extra,
    }


def palette(*entries):
    return [{"hex": h, "ratio": ratio} for h, ratio in entries]


def results(brief, rows, **kwargs):
    out = recommend(brief, rows, **kwargs)["recommendations"]
    for item in out:
        original = next(c for c in rows if c["image_id"] == item["image_id"])
        for purpose in item["purposes"]:
            assert purpose["evidence"]
            for evidence in purpose["evidence"]:
                assert resolve(original, evidence["field"]) == evidence["value"]
    return out


def kinds(result):
    return {p["purpose"] for p in result["purposes"]}


def resolve(obj, path):
    for part in re.findall(r"[^.\[\]]+", path):
        obj = obj[int(part)] if isinstance(obj, (list, tuple)) else obj[part]
    return obj


def test_multicolumn_high_density_and_small_local_promotion():
    rows = [candidate(1, layout={"columns": ["single"]}),
            candidate(2, layout={"columns": ["multi"], "density": ["high"], "title_position": ["top"]})]
    out = results("多栏高密度的展板，顶部标题", rows)
    assert out[0]["image_id"] == 2
    reason = next(p for p in out[0]["purposes"] if p["purpose"] == "layout")
    assert all(word in reason["reason"] for word in ("多栏", "高密度", "顶部标题"))
    # The fixture's description "展板" is also real artifact evidence; layout fields stay exactly three.
    assert len([e for e in reason["evidence"] if e["field"].startswith("analyses.layout.")]) == 3


def test_cream_and_whitespace_are_independent_evidence():
    rows = [candidate(1, layout={"whitespace": ["much"]}, colors=palette(("#f1e9da", .58))),
            candidate(2, layout={"whitespace": ["little"]}, colors=palette(("#f1e9da", .70)))]
    out = results("米白色，留白很多", rows)
    assert kinds(out[0]) == {"layout", "palette"}
    assert kinds(out[1]) == {"palette"}
    assert "背景" not in str(out)


def test_retro_architecture_and_photo_board_mixture():
    photo = candidate(1, subject=["建筑"], description="建筑照片", medium="photo")
    board = candidate(2, subject=["建筑"], tags=["复古"], layout={"columns": ["multi"]})
    out = results("复古建筑展板", [photo, board])
    assert out[0]["image_id"] == 2
    assert kinds(out[0]) == {"subject", "style", "layout"}
    assert kinds(out[1]) == {"subject"}


def test_only_palette_emits_no_other_purposes():
    c = candidate(1, subject=["建筑"], tags=["复古"], layout={"columns": ["multi"]}, colors=palette(("#f1e9da", .6)))
    assert kinds(results("只要米白配色", [c])[0]) == {"palette"}


def test_palette_without_named_color_can_expose_measured_values():
    # A valid muted color need not belong to the small named-color vocabulary.
    c = candidate(1, colors=palette(("#ffc0cb", .6)))
    out = results("只参考配色", [c])
    assert kinds(out[0]) == {"palette"}
    assert "#ffc0cb" in out[0]["purposes"][0]["reason"]


def test_red_and_blue_both_required_and_prominent():
    rows = [candidate(1, colors=palette(("#ff0000", .8))),
            candidate(2, colors=palette(("#ff0000", .45), ("#0000ff", .4))),
            candidate(3, colors=palette(("#ff0000", .8), ("#0000ff", .01)))]
    assert [r["image_id"] for r in results("只要红蓝配色，两色都明显", rows)] == [2]


@pytest.mark.parametrize("negation", ["不要", "不需要", "不想要", "排除", "避免", "不用"])
def test_negation_is_not_a_positive_match(negation):
    spec = parse_brief(f"建筑海报，{negation}复古和红色，换成现代")
    assert spec.styles == ("现代",)
    assert spec.excluded_styles == ("复古",)
    assert spec.excluded_colors == ("红",)


def test_exclusions_are_never_relaxed_on_empty_result():
    c = candidate(1, subject=["建筑"], tags=["复古"], colors=palette(("#ff0000", .5)))
    assert not results("建筑海报，不要复古，不要红色", [c])


def test_excluded_layout_and_topic():
    rows = [candidate(1, subject=["建筑"], layout={"columns": ["multi"]}),
            candidate(2, subject=["食品"], layout={"columns": ["single"]}),
            candidate(3, subject=["建筑"], layout={"columns": ["single"]})]
    assert [r["image_id"] for r in results("展板，不要多栏，排除食品", rows)] == [3]


def test_no_complete_match_is_honest_and_does_not_claim_whole_library_empty():
    c = candidate(1, subject=["食品"], layout={"columns": ["multi"]})
    response = recommend("海洋主题的多栏展板", [c])
    assert kinds(response["recommendations"][0]) == {"layout"}
    assert response["recommendations"][0]["unmet_requirements"] == [{"kind": "subject", "value": "海洋"}]
    assert "不代表整个收藏库没有" in "".join(response["notices"])


def test_highly_similar_candidates_are_deferred_not_used_to_fill_six():
    layout = {"columns": ["multi"], "whitespace": ["much"], "density": ["high"]}
    rows = [candidate(i, layout=layout, tags=["复古"], colors=palette(("#f1e9da", .7))) for i in range(1, 8)]
    rows.append(candidate(8, layout={"columns": ["single"]}, tags=["现代"], colors=palette(("#0000ff", .7))))
    assert [r["image_id"] for r in results("复古展板", rows)] == [1, 8]


def test_missing_fields_cannot_establish_similarity():
    rows = [candidate(i, subject=["建筑"], tags=["复古"], colors=palette(("#f1e9da", .7))) for i in range(1, 4)]
    assert len(results("复古建筑", rows)) == 3


def test_same_post_first_pass_then_at_most_two():
    rows = [candidate(i, subject=["建筑"], post=10) for i in range(1, 5)]
    rows.append(candidate(5, subject=["建筑"], post=20))
    out = results("建筑", rows)
    assert [r["image_id"] for r in out] == [1, 5, 2]


def test_shared_post_in_any_relation_counts_not_just_first_source():
    a = candidate(1, subject=["建筑"], post=10, post_ids=[20])
    b = candidate(2, subject=["建筑"], post=20)
    c = candidate(3, subject=["建筑"], post=30)
    assert [r["image_id"] for r in results("建筑", [a, b, c])] == [1, 3, 2]


def test_same_sha_cross_post_merges_sources_and_retains_multiple_purposes():
    a = candidate(1, sha="same", post=10, subject=["建筑"], tags=["复古"])
    b = candidate(2, sha="same", post=20, subject=["建筑"], tags=["复古"])
    out = results("复古建筑", [a, b])
    assert len(out) == 1
    assert out[0]["post_ids"] == [10, 20]
    assert [s["image_id"] for s in out[0]["sources"]] == [1, 2]
    assert kinds(out[0]) == {"style", "subject"}


def test_ocr_errors_do_not_remove_subject_and_ocr_alone_does_not_prove_topic():
    a = candidate(1, subject=["建筑"], description="建筑图")
    a["analyses"]["visible_text"] = "建笂展扳"
    b = candidate(2, description="食品照片", medium="photo")
    b["analyses"]["visible_text"] = "建筑展板"
    assert [r["image_id"] for r in results("建筑", [a, b])] == [1]


def test_missing_null_and_unknown_fields_do_not_crash_or_gain_purposes():
    rows = [{"image_id": 1, "semantic_score": .99, "analyses": None, "colors": None},
            candidate(2, layout={"columns": ["unknown"], "density": None}, tags=["unknown"])]
    assert not results("多栏高密度复古米白", rows)


def test_followup_removes_old_preference_keeps_other_requirements_and_no_mutation():
    old = parse_brief("复古建筑展板，米白")
    new = parse_brief("不要复古，换成现代极简", previous=old)
    assert old.styles == ("复古",)
    assert new.styles == ("现代", "极简")
    assert new.topics == ("建筑",) and new.colors == ("米白",)
    rows = [candidate(1, subject=["建筑"], tags=["复古"]), candidate(2, subject=["建筑"], tags=["现代", "极简"])]
    assert [r["image_id"] for r in results(new, rows)] == [2]
    restored = parse_brief("还是要复古", previous=new)
    assert "复古" in restored.styles and "复古" not in restored.excluded_styles


def test_fewer_than_six_and_no_unrelated_fill_even_with_high_semantic_score():
    rows = [candidate(1, subject=["建筑"]), candidate(2, subject=["食品"], description="苹果照片", medium="photo")]
    assert [r["image_id"] for r in results("建筑", rows)] == [1]


def test_no_unvalidated_score_threshold():
    c = candidate(1, score=.41, subject=["建筑"])
    assert results("建筑", [c])[0]["semantic_score"] == .41


def test_no_evidence_means_no_local_promotion_and_sort_is_semantic():
    rows = [candidate(1, score=.4, layout={"columns": ["single"]}),
            candidate(2, score=.8, layout={"columns": ["multi"]}),
            candidate(3, score=.6, layout={"columns": ["grid"]})]
    assert [r["image_id"] for r in results("学术一点的展板，有冲击力", rows)] == [2, 3, 1]
    spec = parse_brief("学术一点，要有冲击力")
    assert not spec.layout and not spec.colors


def test_local_promotion_does_not_jump_more_than_two_slots():
    rows = [candidate(i, subject=["建筑"]) for i in range(1, 6)]
    rows[-1]["analyses"]["style"]["tags"] = ["复古"]
    assert [r["image_id"] for r in results("复古建筑", rows)] == [1, 2, 5, 3, 4]


def test_video_every_reason_discloses_limited_scope():
    c = candidate(1, subject=["建筑"], tags=["复古"], layout={"columns": ["multi"]},
                  colors=palette(("#f1e9da", .6)), is_video=True)
    out = results("复古建筑展板，米白色", [c])
    assert kinds(out[0]) == {"subject", "layout", "style", "palette"}
    assert all("仅分析封面与简介" in p["reason"] for p in out[0]["purposes"])


def test_all_emitted_evidence_resolves_verbatim_and_input_is_unchanged():
    rows = [candidate(1, subject=["建筑"], tags=["复古"], layout={"columns": ["multi"], "title_position": ["top"]},
                      colors=palette(("#f1e9da", .58)))]
    before = deepcopy(rows)
    out = results("复古建筑的多栏展板，顶部标题，米白色", rows)
    assert out and len(out[0]["purposes"]) == 4
    for item in out:
        original = next(c for c in rows if c["image_id"] == item["image_id"])
        for purpose in item["purposes"]:
            assert purpose["evidence"]
            assert not any(word in purpose["reason"] for word in ("高级", "专业", "大师感", "一定适合"))
            for e in purpose["evidence"]:
                assert resolve(original, e["field"]) == e["value"]
    assert rows == before
    out[0]["purposes"][0]["evidence"][0]["value"] = "changed"
    assert rows == before


def test_content_organization_uses_actual_note_not_quality_claims():
    c = candidate(1, subject=["建筑"], layout={"layout_note": "左侧说明，右侧图例"})
    out = results("建筑内容组织参考", [c])
    evidence = [e for p in out[0]["purposes"] for e in p["evidence"]]
    assert {"field": "analyses.layout.layout_note", "value": "左侧说明，右侧图例"} in evidence
    assert len(out[0]["purposes"]) == 1  # one purpose, with both sources of evidence


def test_composite_layout_relation_not_invented_from_left_title():
    c = candidate(1, layout={"title_position": ["left"], "columns": ["double"]})
    out = results("左边大标题，右边图片，上下分割", [c])
    text = str(out)
    assert "左侧标题" in text and "右边图片" not in text and "上下分割" not in text


def test_declared_non_retro_prose_does_not_match_retro():
    c = candidate(1, description="不是复古", tags=[])
    c["analyses"]["style"]["style_note"] = "不是复古"
    assert not results("复古", [c])


def test_ties_are_stable_and_top20_is_a_hard_input_scope():
    rows = [candidate(i, score=.5, subject=["建筑"]) for i in range(1, 23)]
    assert results("建筑", rows) == results("建筑", list(reversed(rows)))
    unsupported = [candidate(i, score=1, subject=[], description="", medium="photo") for i in range(1, 21)]
    assert not results("建筑", unsupported + [candidate(21, score=.1, subject=["建筑"])])


@pytest.mark.parametrize("score", [float("nan"), float("inf"), None, True])
def test_invalid_score_is_rejected(score):
    c = candidate(1)
    c["semantic_score"] = score
    with pytest.raises(ValueError, match="semantic_score"):
        recommend("建筑", [c])


def test_zero_limit_and_invalid_limit():
    assert not results("建筑", [candidate(1, subject=["建筑"])], limit=0)
    with pytest.raises(ValueError):
        recommend("建筑", [], limit=7)


def test_palette_similarity_needs_all_three_kinds_of_evidence():
    layout = {"columns": ["multi"], "whitespace": ["much"], "density": ["high"]}
    rows = [candidate(1, subject=["建筑"], layout=layout, tags=["复古"]),
            candidate(2, subject=["建筑"], layout=layout, tags=["复古"])]
    assert len(results("建筑", rows)) == 2  # colors absent, cannot call them duplicates


def test_unknown_does_not_count_as_similarity_or_layout_evidence():
    rows = [candidate(i, subject=["建筑"], tags=["复古"],
                      layout={"columns": ["unknown"], "whitespace": ["unknown"], "density": ["unknown"]},
                      colors=palette(("#f1e9da", .6))) for i in (1, 2)]
    out = results("复古建筑展板", rows)
    assert len(out) == 2
    # unknown never becomes layout evidence (the 展板 description may still be artifact evidence)
    assert not any(e["field"].startswith("analyses.layout.") for r in out for p in r["purposes"] for e in p["evidence"])


def test_malformed_colors_do_not_crash_or_claim_palette():
    c = candidate(1, colors=[None, {"hex": "red", "ratio": .5},
                             {"hex": "#ff0000", "ratio": float("nan")},
                             {"hex": "#ff0000", "ratio": -1},
                             {"hex": "#ff0000", "ratio": True}])
    assert not results("只要红色配色", [c])


def test_additional_purpose_can_end_palette_only_mode():
    spec = parse_brief("只要米白配色")
    updated = parse_brief("还要建筑主题", previous=spec)
    c = candidate(1, subject=["建筑"], colors=palette(("#f1e9da", .6)))
    assert kinds(results(updated, [c])[0]) == {"palette", "subject"}


def test_core_has_no_io_even_when_dependencies_are_unavailable(monkeypatch):
    import builtins
    import socket
    import sqlite3

    def forbidden(*args, **kwargs):
        raise AssertionError("pure recommendation core attempted IO")

    monkeypatch.setattr(builtins, "open", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(sqlite3, "connect", forbidden)
    c = candidate(1, subject=["建筑"], tags=["复古"])
    assert results("复古建筑", [c])


# ---- Day6 tightening: explicit hits, artifact evidence, topic priority, style-only cap ----

def test_zero_explicit_hits_never_qualify_even_for_typography_or_ui():
    memo = candidate(1, medium="ui", description="手机备忘录截图", layout={"columns": ["single"], "density": ["low"]})
    doc = candidate(2, medium="typography", description="课程说明文档", layout={"title_position": ["top"]})
    response = recommend("我想做复古建筑展板", [memo, doc])
    assert response["recommendations"] == []
    assert any("未找到可确认用途" in n for n in response["notices"])


def test_medium_alone_no_longer_grants_layout_purpose():
    ui = candidate(1, medium="ui", description="设置界面截图", tags=["现代"], layout={"columns": ["single"]})
    out = results("现代海报", [ui])
    assert [kinds(r) for r in out] == [{"style"}]
    assert {"kind": "layout", "value": "海报"} in out[0]["unmet_requirements"]


@pytest.mark.parametrize("field,value,path", [
    ("subject", ["字体", "海报"], "analyses.subject[1]"),
    ("tags", ["复古", "设计展板"], "analyses.style.tags[1]"),
    ("tags", ["海报设计", "极简"], "analyses.style.tags[0]"),
])
def test_artifact_layout_purpose_cites_the_candidates_own_field(field, value, path):
    kwargs = {"description": "图片"} | {field: value}
    c = candidate(1, layout={"columns": ["multi"]}, **kwargs)
    brief = "海报" if "海报" in str(value) else "展板"
    out = results(f"我想做一张{brief}", [c])
    layout = next(p for p in out[0]["purposes"] if p["purpose"] == "layout")
    assert {"field": path, "value": resolve(c, path)} in layout["evidence"]
    assert any(e["field"] == "analyses.layout.columns[0]" for e in layout["evidence"])


@pytest.mark.parametrize("description,visible_text,brief", [
    ("这是一份关于设计课程方案展板的要求说明文档", "", "我想做展板"),
    ("一张海报制作教程的截图", "", "我想做海报"),
    ("一张界面截图", "不要使用海报模板", "我想做海报"),
    ("杂志封面的拍摄花絮", "", "我想做封面"),
])
def test_text_mentioning_an_artifact_does_not_make_the_image_that_artifact(description, visible_text, brief):
    c = candidate(1, description=description, medium="typography", layout={"columns": ["single"]})
    c["analyses"]["visible_text"] = visible_text
    response = recommend(brief, [c])
    assert response["recommendations"] == []  # no identity label -> no artifact hit -> zero hits


def test_artifact_unmet_when_only_prose_mentions_it_but_other_purpose_stands():
    c = candidate(1, description="关于方案展板的要求说明文档", tags=["极简"])
    out = results("极简展板", [c])
    assert [kinds(r) for r in out] == [{"style"}]
    assert {"kind": "layout", "value": "展板"} in out[0]["unmet_requirements"]


def test_negated_artifact_in_prose_is_not_evidence_and_brief_negation_excludes():
    prose = candidate(1, description="不是海报，是界面截图")
    assert not results("我想做海报", [prose])
    poster = candidate(2, subject=["海报"], tags=["现代"])
    assert not results("现代风格，不要海报", [poster])


def test_topic_first_and_style_only_capped_without_padding():
    style_only = [candidate(i, tags=["现代", "极简"], description="文字排版", score=.9 - i / 100) for i in range(1, 6)]
    building = candidate(9, subject=["建筑"], description="建筑立面照片", score=.5)
    response = recommend("我想做一个现代极简的建筑展板", style_only + [building])
    ids = [r["image_id"] for r in response["recommendations"]]
    assert ids == [9, 1, 2]  # topic first, then at most two style-only, no padding to six
    assert any("候选不足" in n for n in response["notices"])


def test_style_only_cap_applies_only_with_a_named_topic():
    rows = [candidate(i, tags=["现代"], description="文字排版") for i in range(1, 5)]
    assert len(results("现代风格参考", rows)) == 4


def test_non_style_secondary_purposes_are_not_capped_as_style_only():
    rows = [candidate(i, subject=["海报"], tags=["极简"], layout={"columns": ["single"]}, post=i) for i in range(1, 5)]
    out = results("极简建筑海报", rows)
    assert len(out) == 4 and all(kinds(r) == {"layout", "style"} for r in out)
    assert all({"kind": "subject", "value": "建筑"} in r["unmet_requirements"] for r in out)
