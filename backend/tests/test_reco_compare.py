"""Offline tests for scripts/reco_compare.py.

No network, no real model, no user DB:
  * the one-run path uses a fake embedding client / fake reviewer / fake needs parser
    on the temp schema-v3 SQLite from the existing wiring fixtures,
  * artifact assembly and the HTML top5 before-vs-after layout are pure functions,
  * --dry-run estimation is exercised against the temp DB only.
"""
import json

import pytest

from app import config, creative, db
from app.embedding import dimensions
from app.embedding.service import vector_to_blob
from conftest import png
from scripts import reco_compare as rc
from test_recommendation_wiring import LIBRARY, seeded  # noqa: F401  (fixture)
from test_search_wiring import SETTINGS


def _vector(image_id):
    return [float(image_id), 1.] + [0.] * 1022


def index_dimensions(conn, schemes=dimensions.SCHEMES):
    for scheme in schemes:
        spec = dimensions.spec_for(SETTINGS, scheme)
        pending, _ = dimensions.load_pending(conn, spec)
        for item in pending:
            conn.execute("INSERT INTO embeddings(image_id,dimension,model,vector,dimensions,text_hash)"
                         " VALUES(?,?,?,?,?,?)",
                         (item.image_id, scheme, spec.model, vector_to_blob(_vector(item.image_id)),
                          spec.dimensions, item.text_hash))


@pytest.fixture
def indexed(seeded):  # noqa: F811
    conn = db.connect()
    try:
        index_dimensions(conn)
    finally:
        conn.close()
    return seeded


class FakeEmbedClient:
    def __init__(self, settings, sink):
        self.settings, self.sink = settings, sink

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def close(self):
        pass

    def embed(self, texts):
        self.sink.append(list(texts))
        return [[1., 0.] + [0.] * (self.settings.dimensions - 2) for _ in texts]


class FakeReviewer:
    """Marks itself as called and returns the candidates in reverse order, <=5."""

    def __init__(self):
        self.calls = []

    def __call__(self, column, brief, candidates, settings, images_dir):
        self.calls.append((column, brief, [c.image_id for c in candidates]))
        picks = [{"image_id": c.image_id, "reference_point": f"参考{column}{c.image_id}"}
                 for c in reversed(candidates)][:5]
        return {"status": "ok",
                "picks": picks,
                "usage": {"requests": 1, "prompt_tokens": 100, "completion_tokens": 20,
                          "latency_ms": 5}}


def fake_needs(brief, settings):
    return {"needs": {"layout_need": "排版：" + brief,
                      "color_need": "配色：" + brief,
                      "style_need": "风格：" + brief},
            "usage": {"requests": 1, "prompt_tokens": 30, "completion_tokens": 10,
                      "latency_ms": 2}}


# ---------------------------------------------------------------------------
# token / dry-run estimate
# ---------------------------------------------------------------------------
def test_image_token_formula_matches_documented_32px_rule():
    # 768x576 is already inside the 768 long-side limit: 24*18 patches + 2
    assert rc.estimate_image_tokens(768, 576) == 434
    # the long side is capped at 768, aspect kept: 1536x1152 -> 768x576
    assert rc.estimate_image_tokens(1536, 1152) == 434
    # 1080x1440 (the fixture image size) -> 576x768
    assert rc.estimate_image_tokens(1080, 1440) == 434
    # smaller square: 320x240 -> 320x256 patches -> 80 + 2
    assert rc.estimate_image_tokens(320, 240) == 82
    assert rc.estimate_image_tokens(0, 0) == 0
    assert rc.estimate_image_tokens(1536, 1152) == rc.estimate_image_tokens(768, 576)


def test_text_token_estimate_is_deterministic_and_monotone():
    short = rc.estimate_text_tokens("复古建筑展板")
    long = rc.estimate_text_tokens("复古建筑展板" * 10)
    assert 4 <= short <= 8 and long > short
    assert rc.estimate_text_tokens("") == 0 and rc.estimate_text_tokens(None) == 0
    assert rc.estimate_text_tokens('{"picks": []}') > 0


def test_dry_run_estimates_requests_tokens_and_rmb_without_network(indexed, monkeypatch):
    def forbidden(*args, **kwargs):  # a dry-run must never construct a model client
        raise AssertionError("dry-run touched a model client")
    monkeypatch.setattr(rc.EmbeddingClient, "__init__", forbidden)
    monkeypatch.setattr(creative.CreativeApplication, "recommend", forbidden)

    report = rc.estimate_run("复古建筑展板", None, config.data_dir())
    assert report["network_calls"] == 0
    assert report["requests"]["expected"] == {
        "needs": 1, "embedding": 1, "review_per_column": 1, "review_total": 3, "total": 5}
    assert report["requests"]["worst_case"]["total"] == 13
    assert report["tokens"]["expected"]["vision_input"] > 0
    assert report["tokens"]["expected"]["vision_output"] > 0
    assert report["cost_rmb"]["expected"] > 0
    assert report["cost_rmb"]["budget_ceiling"] > report["cost_rmb"]["expected"]
    # price sources are always recorded next to the number
    assert "help.aliyun.com" in report["prices"]["vision"]["source"]
    assert "qianwenai.com" in report["prices"]["embedding"]["source"]
    # no colours in this brief: palette recall is description_v1, not Pillow colours
    assert report["columns"]["palette"]["scheme"] == "description_v1"
    assert report["columns"]["layout"]["scheme"] == "layout_v1"
    # candidate count never exceeds REVIEW_CANDIDATES and is capped by the index size
    library = report["library"]
    assert report["columns"]["layout"]["review_candidates"] == min(
        creative.REVIEW_CANDIDATES, library["schemes"]["layout_v1"]["valid_rows"])
    assert report["columns"]["palette"]["review_candidates"] == min(
        creative.REVIEW_CANDIDATES, library["schemes"]["description_v1"]["valid_rows"])
    assert report["library"]["analyses_done"] == len(LIBRARY)


def test_resolve_data_dir_prefers_explicit_then_env(indexed, monkeypatch, tmp_path):
    explicit = tmp_path / "explicit"
    explicit.mkdir()
    assert rc.resolve_data_dir(str(explicit))[0] == explicit
    monkeypatch.setenv("WOMINMIN_DATA_DIR", str(tmp_path / "envdata"))
    assert rc.resolve_data_dir(None)[0] == tmp_path / "envdata"


# ---------------------------------------------------------------------------
# one run + capture (fake client/reviewer, no network)
# ---------------------------------------------------------------------------
def test_recommend_once_uses_one_embedding_batch_three_reviews_and_restores_patches(indexed):
    original_dimension, original_palette = creative.dimension_recall, creative.palette_recall
    embedding_calls, reviewer = [], FakeReviewer()
    result, captures = rc.recommend_once(
        brief="复古建筑展板", previous=None, data_dir=config.data_dir(),
        settings_provider=lambda: SETTINGS,
        client_factory=lambda settings: FakeEmbedClient(settings, embedding_calls),
        reviewer=reviewer, needs_parser=fake_needs)

    # module globals are restored even though the run patched them
    assert creative.dimension_recall is original_dimension
    assert creative.palette_recall is original_palette
    # exactly one 3-text embedding batch and one review per column
    assert len(embedding_calls) == 1 and len(embedding_calls[0]) == 3
    assert sorted(column for column, _, _ in reviewer.calls) == ["layout", "palette", "style"]
    assert all(ids for _, _, ids in reviewer.calls) and all(len(ids) <= creative.REVIEW_CANDIDATES
                                                           for _, _, ids in reviewer.calls)
    # recall order captured per index scheme; palette used description_v1 (no colours)
    assert set(captures.recalls) == {"layout_v1", "style_v1", "description_v1"}
    for purpose, edge in (("layout", "layout_v1"), ("style", "style_v1"), ("palette", "description_v1")):
        assert captures.recalls[edge] and len(captures.recalls[edge]) <= creative.RECALL_TOP
        assert captures.columns[purpose]["candidate_ids"]
        assert captures.columns[purpose]["prompt"].startswith("你是设计专业学生的参考图筛选助手")
    assert result["status"] == "ok"
    assert [c["purpose"] for c in result["columns"]] == ["layout", "palette", "style"]


def test_recommend_once_restores_patches_when_the_run_fails(indexed):
    original_dimension, original_palette = creative.dimension_recall, creative.palette_recall

    class BrokenFactory:
        def __call__(self, settings):
            raise RuntimeError("embedding down")

    with pytest.raises(Exception):
        rc.recommend_once(brief="复古建筑展板", previous=None, data_dir=config.data_dir(),
                          settings_provider=lambda: SETTINGS, client_factory=BrokenFactory(),
                          reviewer=FakeReviewer(), needs_parser=fake_needs)
    assert creative.dimension_recall is original_dimension
    assert creative.palette_recall is original_palette


def test_run_once_writes_json_and_html_without_leaking_the_key(indexed, tmp_path):
    out = tmp_path / "report"
    run = rc.run_once("复古建筑展板", None, config.data_dir(), out,
                      settings_provider=lambda: SETTINGS,
                      client_factory=lambda settings: FakeEmbedClient(settings, []),
                      reviewer=FakeReviewer(), needs_parser=fake_needs,
                      plan={"cost_rmb": {"expected": 0.003}})
    for name in ("run.json", "report.html", "columns/layout.json",
                 "columns/palette.json", "columns/style.json"):
        assert (out / name).exists(), name
    saved = json.loads((out / "run.json").read_text(encoding="utf-8"))
    assert saved["status"] == "ok" and saved["effective_query"] == "复古建筑展板"
    assert saved["needs"]["layout_need"] == "排版：复古建筑展板"
    assert saved["plan"]["cost_rmb"]["expected"] == 0.003
    assert saved["config"]["api_key"] == "<redacted>"
    assert SETTINGS.api_key not in (out / "run.json").read_text(encoding="utf-8")
    assert SETTINGS.api_key not in (out / "report.html").read_text(encoding="utf-8")
    assert saved["requests_and_usage"]["embedding_requests"] == 1
    assert saved["requests_and_usage"]["review_requests"] == 3
    assert saved["cost_actual_rmb"]["total_rmb"] > 0
    # before-vs-after top5 cards always carry resolvable local image paths
    for column in saved["columns"]:
        assert column["recall_first_five"] or column["status"] != "ok"
        for card in column["before_first_five"] + column["after_first_five"]:
            if card.get("image_exists"):
                assert (out / card["image_href"]).resolve().exists()
    html_text = (out / "report.html").read_text(encoding="utf-8")
    assert html_text.count('<section class="column"') == 3


# ---------------------------------------------------------------------------
# pure artifact assembly / HTML top5 layout
# ---------------------------------------------------------------------------
def _synthetic_result():
    def item(image_id, reference, rank):
        return {"image_id": image_id, "recall_rank": rank, "semantic_score": 1.0 / rank,
                "reference_point": reference, "unreviewed": False, "media_scope": "image",
                "post_ids": [rank], "purposes": [{"purpose": "layout", "reason": reference}]}

    return {
        "status": "ok", "effective_query": "复古建筑展板",
        "needs": {"layout_need": "多栏", "color_need": "", "style_need": "复古"},
        "needs_usage": {"requests": 1, "prompt_tokens": 30, "completion_tokens": 10},
        "notices": [],
        "columns": [
            {"purpose": "layout", "label": "排版", "status": "ok", "error": None,
             "recall_source": "layout_v1", "reviewed_ids": [5, 4, 3],
             "items": [item(5, "参考它的五栏网格", 1), item(4, "参考它的标题层级", 2)],
             "usage": {"requests": 1, "prompt_tokens": 100, "completion_tokens": 20}},
            {"purpose": "palette", "label": "配色", "status": "ok", "error": None,
             "recall_source": "description_v1", "reviewed_ids": [2, 1],
             "items": [item(2, "参考它的米白<主色>", 1)],
             "usage": {"requests": 1, "prompt_tokens": 100, "completion_tokens": 20}},
            {"purpose": "style", "label": "风格", "status": "review_failed", "error": "invalid: x",
             "recall_source": "style_v1", "reviewed_ids": [9],
             "items": [dict(item(9, "未复看", 1), unreviewed=True)],
             "usage": {"requests": 1, "prompt_tokens": 100, "completion_tokens": 20}},
        ],
    }


def _synthetic_captures():
    captures = rc.Captures()
    captures.recalls = {
        "layout_v1": [(6, .9), (5, .8), (4, .7), (3, .6), (2, .5), (1, .4)],
        "description_v1": [(2, .9), (1, .8)],
        "style_v1": [(9, .9)],
    }
    captures.columns = {
        "layout": {"candidate_ids": [6, 5, 4, 3], "prompt": "PROMPT layout",
                   "candidates": [_candidate(6, 1), _candidate(5, 2), _candidate(4, 3), _candidate(3, 4)],
                   "result": {"status": "ok", "picks": [{"image_id": 5, "reference_point": "参考它的五栏网格"}],
                              "usage": {"requests": 1, "prompt_tokens": 100, "completion_tokens": 20}}},
        "palette": {"candidate_ids": [2, 1], "prompt": "PROMPT palette",
                    "candidates": [_candidate(2, 1), _candidate(1, 2)],
                    "result": {"status": "ok", "picks": [{"image_id": 2, "reference_point": "参考它的米白<主色>"}],
                               "usage": {"requests": 1, "prompt_tokens": 100, "completion_tokens": 20}}},
        "style": {"candidate_ids": [9], "prompt": "PROMPT style",
                  "candidates": [_candidate(9, 1)],
                  "result": {"status": "review_failed", "error": "invalid: x", "picks": [],
                             "usage": {"requests": 1, "prompt_tokens": 100, "completion_tokens": 20}}},
    }
    captures.embedding_calls = [{"texts": ["复古建筑展板", "风格：复古", "复古建筑展板"]}]
    return captures


def _candidate(image_id, order):
    return {"order": order, "image_id": image_id, "recall_rank": order, "semantic_score": 1.0 / order,
            "post_ids": [order], "is_video": False, "file_path": f"images/{image_id}.png",
            "sha256": f"sha{image_id}"}


def _images(tmp_path):
    (tmp_path / "images").mkdir(parents=True)
    images = {}
    for image_id in (1, 2, 3, 4, 5, 6, 9):
        path = tmp_path / "images" / f"{image_id}.png"
        path.write_bytes(png((image_id, 0, 0)))
        images[image_id] = {"file_path": f"images/{image_id}.png", "width": 1080, "height": 1440,
                            "original_name": f"{image_id}.png", "subject": ["建筑"], "description": f"图{image_id}"}
    return images


def test_assemble_artifacts_keeps_recall_candidate_and_review_orders(tmp_path):
    out_dir = tmp_path / "out"
    run = rc.assemble_artifacts(_synthetic_result(), _synthetic_captures(),
                                brief="复古建筑展板", previous=None, data_dir=tmp_path,
                                out_dir=out_dir, images=_images(tmp_path),
                                plan={"cost_rmb": {"expected": 0.003}})
    layout = run["columns"][0]
    assert [c["image_id"] for c in layout["recall_order"]] == [6, 5, 4, 3, 2, 1]
    assert [c["image_id"] for c in layout["recall_first_five"]] == [6, 5, 4, 3, 2]
    assert [c["image_id"] for c in layout["candidates_sent_to_review"]] == [6, 5, 4, 3]
    assert [c["picked"] for c in layout["candidates_sent_to_review"]] == [False, True, True, False]
    assert layout["squeezed_out_ids"] == [6, 3]
    # before = candidate order, after = model order with its own reason
    assert [c["image_id"] for c in layout["before_first_five"]] == [6, 5, 4, 3]
    assert [c["image_id"] for c in layout["after_first_five"]] == [5, 4]
    assert layout["after_first_five"][0]["reference_point"] == "参考它的五栏网格"
    assert layout["after_first_five"][0]["rank"] == 1
    assert layout["review_result"]["picks"][0]["image_id"] == 5
    assert layout["review_prompt"] == "PROMPT layout"
    # failed column keeps its unreviewed fallback picks, status and error
    style = run["columns"][2]
    assert style["status"] == "review_failed" and style["error"] == "invalid: x"
    assert style["after_first_five"][0]["unreviewed"] is True
    # every card resolves to the local image file relative to the report dir
    card = layout["before_first_five"][0]
    assert card["image_exists"] and (out_dir / card["image_href"]).resolve().exists()
    assert run["plan"]["cost_rmb"]["expected"] == 0.003
    # actual usage is a price computation from the reported tokens
    assert run["requests_and_usage"]["total_requests"] == 1 + 1 + 3
    assert run["requests_and_usage"]["prompt_tokens_reported"] == 300 + 30
    assert run["cost_actual_rmb"]["total_rmb"] > 0


def test_report_html_has_five_before_after_rows_per_column_and_escapes(tmp_path):
    out_dir = tmp_path / "out"
    run = rc.assemble_artifacts(_synthetic_result(), _synthetic_captures(),
                                brief="复古建筑展板", previous=None, data_dir=tmp_path,
                                out_dir=out_dir, images=_images(tmp_path))
    html_text = rc.render_report_html(run)
    assert html_text.count('<section class="column"') == 3
    # one 5-row before-vs-after table per column
    assert html_text.count('<td class="side before">') == 15
    assert html_text.count('<td class="side after">') == 15
    # 15 rows x 2 cells; before cells 4/2/1 filled, after cells 2/1/1 filled
    assert html_text.count('<div class="card placeholder">') == (5 - 4) + (5 - 2) + (5 - 1) \
        + (5 - 2) + (5 - 1) + (5 - 1)
    assert "参考它的五栏网格" in html_text
    # raw < > in a model reason must be escaped, never injected
    assert "参考它的米白&lt;主色&gt;" in html_text and "<主色>" not in html_text
    # repeated source ids in a report appear as local relative links
    assert 'src="images/' not in html_text  # images live outside the report dir here
    assert "images/5.png" in html_text
    assert "复看前" in html_text and "复看后" in html_text
    # review result JSON is embedded for human/audit use
    assert "review_failed" in html_text
