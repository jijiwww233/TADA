"""Tests for the day-4 embedding core (no network, no DB writes)."""
from __future__ import annotations

import copy
import json
import math
import struct

import httpx
import pytest

from app.embedding import (
    EmbeddingClient,
    EmbeddingError,
    EmbeddingItem,
    EmbeddingSettings,
    EmbeddingSpec,
    build_combined_d_v1,
    build_text,
    coarse_color_name,
    cosine_similarity,
    excerpt_visible_text,
    make_spec,
    needs_rebuild,
    prepare_embedding,
    rank,
    text_hash,
    vector_to_blob,
    blob_to_vector,
)
from app.embedding.text import TEXT_VERSION, normalize_visible_text


def sample_analysis() -> dict:
    return {
        "description": "一张米白底红色标题的展览海报",
        "subject": json.dumps(["展览", "建筑"], ensure_ascii=False),
        "layout_json": json.dumps(
            {
                "title_position": ["top"],
                "columns": ["double"],
                "whitespace": ["much"],
                "image_text_ratio": ["balanced"],
                "density": ["medium"],
                "layout_note": "右侧留白偏多",
            },
            ensure_ascii=False,
        ),
        "style_json": json.dumps(
            {"tags": ["极简", "瑞士风格"], "medium": "photo", "style_note": "冷静克制"},
            ensure_ascii=False,
        ),
        "visible_text": "2026 设计展\n\n2026 设计展\nA 馆 一层",
    }


def sample_colors() -> list[dict]:
    return [
        {"hex": "#111111", "ratio": 0.6, "rank": 0},
        {"hex": "#f5f0e8", "ratio": 0.2, "rank": 1},
    ]


# ----------------------------- text builder -----------------------------

def test_combined_d_v1_is_deterministic_and_ordered():
    analysis = sample_analysis()
    first = build_combined_d_v1(analysis, sample_colors())
    second = build_combined_d_v1(analysis, sample_colors())
    assert first == second
    assert first == (
        "描述：一张米白底红色标题的展览海报\n"
        "主题：展览、建筑\n"
        "排版：标题在顶部、双栏、留白多、图文均衡、密度中、右侧留白偏多\n"
        "风格：极简、瑞士风格、摄影、冷静克制\n"
        "色彩：黑、白\n"
        "图内文字：2026 设计展\nA 馆 一层"
    )
    assert first.split("\n")[0].startswith("描述：")
    assert first.split("\n")[1].startswith("主题：")


def test_unknown_never_becomes_affirmative_text():
    analysis = {
        "description": "一张图",
        "subject": ["unknown"],
        "layout_json": json.dumps(
            {
                "title_position": ["unknown"],
                "columns": ["unknown"],
                "whitespace": ["unknown"],
                "image_text_ratio": ["unknown"],
                "density": ["unknown"],
                "layout_note": "unknown",
            }
        ),
        "style_json": json.dumps({"tags": ["unknown"], "medium": "unknown", "style_note": "unknown"}),
        "visible_text": "",
    }
    text = build_combined_d_v1(analysis, [])
    assert text == "描述：一张图"
    assert "排版：" not in text
    assert "风格：" not in text
    assert "未知" not in text


def test_unknown_values_are_skipped_inside_known_fields():
    analysis = {
        "description": "图",
        "layout": {"title_position": ["top", "unknown"], "columns": ["unknown", "grid"]},
        "style": {"tags": ["极简", "unknown"], "medium": "illustration"},
    }
    text = build_combined_d_v1(analysis, [])
    assert "排版：标题在顶部、网格" in text
    assert "风格：极简、插画" in text
    assert text.count("unknown") == 0


def test_visible_text_collapses_whitespace_and_duplicate_lines():
    assert normalize_visible_text("  标题  \n标题\n\n第二 行\n第二 行\n") == "标题\n第二 行"
    assert normalize_visible_text("a\r\nb\r\nc") == "a\nb\nc"
    assert normalize_visible_text("") == ""


def test_visible_text_short_is_kept_whole():
    short = "标题\n一句话"
    assert excerpt_visible_text(short) == short


def test_visible_text_long_is_truncated_to_300_first200_last100():
    long_text = "甲" * 300 + "乙" * 700
    excerpt = excerpt_visible_text(long_text)
    assert len(excerpt) == 300
    assert excerpt == "甲" * 200 + "乙" * 100


def test_colors_are_deterministic_from_hex_not_ai_text():
    analysis = {
        "description": "红色大标题的海报",
        "style": {"tags": ["红色系"]},
        "visible_text": "",
    }
    text = build_combined_d_v1(analysis, [{"hex": "#808080", "ratio": 0.9, "rank": 0}])
    assert "色彩：灰" in text
    assert "色彩：红" not in text


@pytest.mark.parametrize(
    "value,name",
    [
        ("#000000", "黑"),
        ("#ffffff", "白"),
        ("#808080", "灰"),
        ("#cc0000", "红"),
        ("#0055ff", "蓝"),
        ("#00cc44", "绿"),
        ("#ffdd00", "黄"),
        ("#8b5a2b", "棕"),
    ],
)
def test_coarse_color_names(value, name):
    assert coarse_color_name(value) == name


def test_color_names_are_deduped_and_limited():
    colors = [
        {"hex": "#111111", "ratio": 0.5, "rank": 0},
        {"hex": "#000000", "ratio": 0.2, "rank": 1},
        {"hex": "#ffffff", "ratio": 0.1, "rank": 2},
        {"hex": "#cccccc", "ratio": 0.1, "rank": 3},
    ]
    text = build_combined_d_v1({"description": "图"}, colors)
    assert text == "描述：图\n色彩：黑、白、浅灰"


def test_text_hash_is_stable_and_sensitive():
    assert text_hash("abc") == text_hash("abc")
    assert text_hash("abc") != text_hash("abd")
    assert text_hash("") == text_hash("")


# ----------------------------- client -----------------------------

def make_settings(dimensions: int = 4) -> EmbeddingSettings:
    return EmbeddingSettings(
        base_url="https://example.test/v1",
        api_key="test-key-123456",
        model="qwen3.7-text-embedding-flash",
        dimensions=dimensions,
    )


def make_client(handler, dimensions: int = 4) -> EmbeddingClient:
    transport = httpx.MockTransport(handler)
    return EmbeddingClient(make_settings(dimensions), http_client=httpx.Client(transport=transport))


def test_client_batches_at_most_10_and_keeps_order():
    batches: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        batches.append(len(payload["input"]))
        data = [
            {"index": i, "embedding": [float(i + 1), 1.0, 1.0, 1.0]}
            for i in range(len(payload["input"]))
        ]
        return httpx.Response(200, json={"data": data})

    client = make_client(handler)
    vectors = client.embed([f"文本{i}" for i in range(23)])
    assert batches == [10, 10, 3]
    assert len(vectors) == 23
    assert all(len(vector) == 4 for vector in vectors)
    assert "test-key" not in repr(client)


def test_client_reorders_by_index():
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        n = len(payload["input"])
        data = [{"index": i, "embedding": [float(i + 1), 1.0, 1.0, 1.0]} for i in range(n)]
        data.reverse()
        return httpx.Response(200, json={"data": data})

    client = make_client(handler)
    vectors = client.embed(["a", "b", "c"])
    assert [vector[0] for vector in vectors] == [1.0, 2.0, 3.0]


def test_client_rejects_wrong_dimension():
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        data = [{"index": i, "embedding": [1.0, 1.0, 1.0]} for i in range(len(payload["input"]))]
        return httpx.Response(200, json={"data": data})

    client = make_client(handler, dimensions=4)
    with pytest.raises(EmbeddingError) as exc:
        client.embed(["a"])
    assert exc.value.kind == "dimension"


def test_client_rejects_wrong_count():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0, 1.0, 1.0, 1.0]}]})

    client = make_client(handler)
    with pytest.raises(EmbeddingError) as exc:
        client.embed(["a", "b"])
    assert exc.value.kind == "count"


@pytest.mark.parametrize("bad", ["nan", "zero"])
def test_client_rejects_nan_and_zero_vectors(bad):
    def handler(request: httpx.Request) -> httpx.Response:
        if bad == "nan":
            # httpx's json= refuses NaN, so send the raw JSON body Python can parse.
            body = '{"data": [{"index": 0, "embedding": [NaN, 1.0, 1.0, 1.0]}]}'
            return httpx.Response(200, content=body, headers={"content-type": "application/json"})
        body = '{"data": [{"index": 0, "embedding": [0.0, 0.0, 0.0, 0.0]}]}'
        return httpx.Response(200, content=body, headers={"content-type": "application/json"})

    client = make_client(handler)
    with pytest.raises(EmbeddingError) as exc:
        client.embed(["a"])
    assert exc.value.kind in {"nonfinite", "zero_norm"}


def test_client_does_not_put_key_in_body():
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        seen.append(payload)
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0, 1.0, 1.0, 1.0]}]})

    client = make_client(handler)
    client.embed(["a"])
    assert "test-key" not in json.dumps(seen, ensure_ascii=False)


def test_client_error_scopes_auth_and_unknown():
    def handler_401(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "unauthorized"})

    client = make_client(handler_401)
    with pytest.raises(EmbeddingError) as exc:
        client.embed(["a"])
    assert exc.value.scope == "global"

    def handler_400(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "invalid parameter"})

    client_400 = make_client(handler_400)
    with pytest.raises(EmbeddingError) as exc2:
        client_400.embed(["a"])
    assert exc2.value.scope == "unknown"

    def handler_timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("read timed out", request=request)

    client_timeout = make_client(handler_timeout)
    with pytest.raises(EmbeddingError) as exc3:
        client_timeout.embed(["a"])
    assert exc3.value.scope == "unknown"


def test_client_marks_empty_local_input_as_input_scope():
    client = make_client(lambda request: httpx.Response(200, json={"data": []}))
    with pytest.raises(EmbeddingError) as exc:
        client.embed([""])
    assert exc.value.scope == "input"


# ----------------------------- incremental -----------------------------

def test_needs_rebuild_rules():
    spec = EmbeddingSpec(model="m", dimensions=1024, scheme=TEXT_VERSION)
    vector = [1.0] + [0.0] * 1023
    current = {"model": "m", "dimensions": 1024, "scheme": TEXT_VERSION,
               "text_version": TEXT_VERSION, "text_hash": "h", "vector": vector}
    assert needs_rebuild(None, spec, "h") is True
    assert needs_rebuild(current, spec, "h") is False
    assert needs_rebuild({**current, "model": "other"}, spec, "h") is True
    assert needs_rebuild({**current, "dimensions": 512}, spec, "h") is True
    assert needs_rebuild({**current, "scheme": "other"}, spec, "h") is True
    assert needs_rebuild({**current, "text_version": "other"}, spec, "h") is True
    assert needs_rebuild({**current, "text_hash": "g"}, spec, "h") is True
    # visual model is irrelevant when text hash is unchanged
    assert needs_rebuild({**current, "visual_model": "vision-v9"}, spec, "h") is False


def test_needs_rebuild_rejects_missing_or_corrupt_vectors():
    spec = EmbeddingSpec(model="m", dimensions=4, scheme=TEXT_VERSION)
    base = {"model": "m", "dimensions": 4, "scheme": TEXT_VERSION,
            "text_version": TEXT_VERSION, "text_hash": "h"}
    assert needs_rebuild(base, spec, "h") is True                               # no vector
    assert needs_rebuild({**base, "vector": None}, spec, "h") is True
    assert needs_rebuild({**base, "vector": b"bad"}, spec, "h") is True        # wrong BLOB length
    assert needs_rebuild({**base, "vector": [1.0, 0.0, 0.0, math.nan]}, spec, "h") is True
    assert needs_rebuild({**base, "vector": [0.0, 0.0, 0.0, 0.0]}, spec, "h") is True
    assert needs_rebuild({**base, "vector": [1.0, 0.0, 0.0, 0.0]}, spec, "h") is False
    assert needs_rebuild({**base, "vector": struct.pack("<4f", 1.0, 0.0, 0.0, 0.0)},
                         spec, "h") is False


def test_make_spec_defaults_to_flash_1024_combined_d_v1():
    spec = make_spec()
    assert spec.model == "qwen3.7-text-embedding-flash"
    assert spec.dimensions == 1024
    assert spec.scheme == TEXT_VERSION
    assert spec.text_version == TEXT_VERSION


class FakeClient:
    def __init__(self, vector=None, error: Exception | None = None):
        self.vector = vector if vector is not None else [1.0] * 1024
        self.error = error
        self.calls = 0

    def embed_one(self, text: str):
        self.calls += 1
        if self.error:
            raise self.error
        return self.vector


def test_prepare_embedding_creates_record_and_skips_up_to_date():
    spec = make_spec()
    analysis = sample_analysis()
    text = build_text(analysis, sample_colors())
    current_hash = text_hash(text)
    client = FakeClient()
    result = prepare_embedding(client, spec, 7, analysis, sample_colors(), None)
    assert result["status"] == "embedded"
    assert client.calls == 1
    record = result["record"]
    assert record.image_id == 7
    assert record.text_hash == current_hash
    assert record.model == spec.model and record.dimensions == spec.dimensions

    existing = {
        "model": spec.model,
        "dimensions": spec.dimensions,
        "scheme": spec.scheme,
        "text_version": spec.text_version,
        "text_hash": current_hash,
        "vector": record.vector,
    }
    result2 = prepare_embedding(client, spec, 7, analysis, sample_colors(), existing)
    assert result2["status"] == "skipped"
    assert result2["reason"] == "up_to_date"
    assert client.calls == 1


def test_prepare_embedding_failure_does_not_touch_analysis():
    spec = make_spec()
    analysis = sample_analysis()
    snapshot = copy.deepcopy(analysis)
    client = FakeClient(error=EmbeddingError("http", "boom"))
    result = prepare_embedding(client, spec, 7, analysis, sample_colors(), None)
    assert result["status"] == "failed"
    assert result["kind"] == "http"
    assert analysis == snapshot


def test_prepare_embedding_with_no_known_fields_skips_without_calling_api():
    client = FakeClient()
    result = prepare_embedding(client, make_spec(), 1, {"visible_text": ""}, [])
    assert result["status"] == "skipped"
    assert result["reason"] == "no_text"
    assert client.calls == 0


def test_vector_blob_roundtrip():
    vector = [0.5, -0.25, 1.0]
    blob = vector_to_blob(vector)
    assert len(blob) == 12
    assert blob_to_vector(blob, 3) == pytest.approx(vector)


# ----------------------------- search -----------------------------

def test_cosine_ranking_is_stable_when_scores_tie():
    items = [
        EmbeddingItem(image_id=3, vector=[1.0, 0.0]),
        EmbeddingItem(image_id=1, vector=[1.0, 0.0]),
        EmbeddingItem(image_id=2, vector=[1.0, 0.0]),
    ]
    hits = rank([1.0, 0.0], items, top_n=10)
    assert [hit.image_id for hit in hits] == [1, 2, 3]
    assert all(hit.score == pytest.approx(1.0) for hit in hits)


def test_search_returns_low_scores_without_threshold():
    items = [EmbeddingItem(image_id=9, vector=[0.0, 1.0])]
    hits = rank([1.0, 0.0], items, top_n=5)
    assert len(hits) == 1
    assert hits[0].score == pytest.approx(0.0)


def test_rank_respects_top_n_and_payload():
    items = [
        EmbeddingItem(image_id=1, vector=[1.0, 0.0], payload="a"),
        EmbeddingItem(image_id=2, vector=[0.0, 1.0], payload="b"),
    ]
    hits = rank([1.0, 0.0], items, top_n=1)
    assert len(hits) == 1
    assert hits[0].image_id == 1
    assert hits[0].payload == "a"
    assert rank([1.0, 0.0], items, top_n=0) == []


@pytest.mark.parametrize("bad", [[math.nan, 0.0], [0.0, 0.0], [1.0]])
def test_rank_rejects_invalid_vectors(bad):
    with pytest.raises(ValueError):
        rank(bad, [EmbeddingItem(image_id=1, vector=[1.0, 1.0])], top_n=1)
