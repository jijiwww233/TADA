from copy import deepcopy
from dataclasses import replace
import hashlib
import json
import sqlite3

import httpx
import pytest

from app.classification_backend import (ClassificationSettings, OpenAITextClassificationBackend,
    TextLimits, build_text_packet, classify_many_cached, sanitize_text, sanitized_document)
from app.collection_classification import (ClassificationState, apply_automatic, effective_category,
                                         input_hash, set_manual)
from app.classification_visibility import visible_image_sources
from app.classification_live_evaluation import (export_posts, evaluate_predictions, load_ground_truth,
                                               label_template, render_report)

KEY = "sk-fixture-secret-classification"


def doc(title="建筑海报设计参考"):
    return {"post": {"title": title, "description": "已有标题简介", "is_video": False}, "images": []}


class FakeEndpoint:
    def __init__(self, *, status=200, error=None, mutate=None, usage=True):
        self.requests = []
        self.status, self.error, self.mutate, self.usage = status, error, mutate, usage

    def handle(self, request):
        self.requests.append(request)
        if self.error:
            raise self.error
        if self.status != 200:
            return httpx.Response(self.status, text="Authorization: Bearer " + KEY)
        request_body = json.loads(request.content)
        posts = json.loads(request_body["messages"][1]["content"])["posts"]
        answer = {"results": []}
        for post in posts:
            evidence = [{"source": post["fields"][0]["source"], "text": post["fields"][0]["text"][:50]}] if post["fields"] else []
            answer["results"].append({"id": post["id"], "category": "design_reference" if evidence else "uncertain",
                                      "confidence": .86, "evidence": evidence})
        if self.mutate:
            self.mutate(answer)
        body = {"choices": [{"message": {"content": json.dumps(answer, ensure_ascii=False)}}]}
        if self.usage:
            body["usage"] = {"prompt_tokens": 100, "completion_tokens": 20}
        return httpx.Response(200, json=body)

    def backend(self, **kwargs):
        settings = ClassificationSettings("https://text.invalid/v1", KEY, **kwargs)
        return OpenAITextClassificationBackend(settings, client=httpx.Client(transport=httpx.MockTransport(self.handle)))


def test_text_only_request_correct_json_no_key_in_body_and_no_visual_processing():
    fake = FakeEndpoint()
    backend = fake.backend()
    result = classify_many_cached([doc()], backend)
    assert result[0]["category"] == "design_reference"
    payload = json.loads(fake.requests[0].content)
    assert payload["model"] == "qwen3.7-flash" and payload["enable_thinking"] is False
    assert payload["response_format"] == {"type": "json_object"}
    assert all(isinstance(m["content"], str) for m in payload["messages"])
    assert "image_url" not in fake.requests[0].content.decode() and KEY not in fake.requests[0].content.decode()
    assert fake.requests[0].headers["Authorization"] == "Bearer " + KEY
    assert KEY not in repr(backend.settings)


def test_batches_at_most_ten_and_same_input_is_deduplicated_and_cached():
    fake = FakeEndpoint()
    backend = fake.backend()
    documents = [doc(str(i)) for i in range(21)]
    results = classify_many_cached(documents + [documents[0]], backend)
    assert len(results) == 22 and len(fake.requests) == 3
    counts = [len(json.loads(json.loads(r.content)["messages"][1]["content"])["posts"]) for r in fake.requests]
    assert counts == [10, 10, 1]
    assert backend.usage.posts_requested == 21
    assert classify_many_cached(documents + [documents[0]], backend, previous=results) == results
    assert len(fake.requests) == 3


def test_bad_individual_result_does_not_lose_good_peers_or_cause_retry():
    fake = FakeEndpoint(mutate=lambda answer: answer["results"][1].update(category="invented"))
    backend = fake.backend()
    results = classify_many_cached([doc("a"), doc("b"), doc("c")], backend)
    assert [r["status"] for r in results] == ["ok", "backend_error", "ok"]
    assert len(fake.requests) == 1


@pytest.mark.parametrize("mode", ["auth", "forbidden", "timeout", "unknown", "rate_limit", "server"])
def test_global_errors_stop_without_batch_to_single_resubmission_and_are_sanitized(mode):
    options = {"auth": {"status": 401}, "forbidden": {"status": 403},
               "timeout": {"error": httpx.ReadTimeout("Bearer " + KEY)},
               "unknown": {"error": RuntimeError(KEY)}, "rate_limit": {"status": 429}, "server": {"status": 503}}
    fake = FakeEndpoint(**options[mode])
    backend = fake.backend()
    results = classify_many_cached([doc(str(i)) for i in range(15)], backend)
    assert len(fake.requests) == 1
    assert all(r["status"] == "backend_error" for r in results)
    assert KEY not in json.dumps(results)
    assert backend.usage.prompt_tokens is None


def test_request_budget_is_shared_and_never_exceeded():
    fake = FakeEndpoint()
    backend = fake.backend(max_requests=2)
    results = backend.predict_batch([doc(str(i)) for i in range(21)])
    assert len(fake.requests) == backend.usage.requests == 2
    assert results[-1]["error"] == "not_attempted_after_stop_or_budget"


def test_invalid_json_has_no_repair_request_and_usage_is_still_counted():
    def handler(request):
        return httpx.Response(200, json={"choices": [{"message": {"content": "not json"}}],
                                        "usage": {"prompt_tokens": 9, "completion_tokens": 3}})
    backend = OpenAITextClassificationBackend(ClassificationSettings("https://text.invalid/v1", KEY),
        client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert backend.predict_batch([doc()])[0]["status"] == "backend_error"
    assert backend.usage.requests == 1 and backend.usage.completion_tokens == 3


@pytest.mark.parametrize("mutation", [
    lambda answer: answer["results"][0]["evidence"][0].update(source="post.platform"),
    lambda answer: answer["results"][0]["evidence"][0].update(text="hallucinated quotation"),
    lambda answer: answer["results"][0].update(confidence=True),
    lambda answer: answer["results"][0].update(extra="arbitrary"),
    lambda answer: answer["results"].append(deepcopy(answer["results"][0])),
])
def test_strict_result_evidence_and_duplicate_ids_are_rejected(mutation):
    fake = FakeEndpoint(mutate=mutation)
    assert fake.backend().predict_batch([doc()])[0]["status"] == "backend_error"
    assert len(fake.requests) == 1


def test_weak_appearance_evidence_does_not_turn_into_design_reference():
    fake = FakeEndpoint()
    result = classify_many_cached([doc("排版精致")], fake.backend())[0]
    assert result["category"] == "uncertain" and result["status"] == "ok"


def test_short_packet_is_whitelisted_redacted_and_preserves_actual_sample_indices():
    payload = doc("title https://example.com/a?token=secret " + KEY)
    payload["embedding_vector"] = [0.1] * 100
    payload["images"] = [{"subject": ["人物"], "description": "d" * 500,
        "layout": {"columns": ["multi"], "vector": [1] * 100},
        "style": {"tags": ["复古"], "url": "https://example.com"}, "visible_text": "OCR" * 500} for _ in range(20)]
    payload["image_base64"] = "data:image/png;base64,private"
    packet, fields = build_text_packet(payload, secrets=(KEY,))
    wire = json.dumps(packet, ensure_ascii=False)
    assert sum(len(v) for v in fields.values()) <= 3000
    assert len(packet["sampled_image_indices"]) == 6 and packet["sampled_image_indices"][-1] == 19
    assert not any(s in wire for s in (KEY, "https://", "data:image", "embedding_vector", '"vector"'))
    assert all(len(v) <= 100 for k, v in fields.items() if k.endswith("visible_text"))
    payload["images"] = [{"subject": {"vector": [0.1]}, "description": {"base64": "private"},
                          "visible_text": [0.1, 0.2]}]
    packet, fields = build_text_packet(payload, secrets=(KEY,))
    assert not any(k.startswith("images[") for k in fields)


def test_model_prompt_limits_define_version_and_preserve_manual_override():
    first = FakeEndpoint().backend()
    second = FakeEndpoint().backend(model="other-text-model")
    assert first.classifier_version != second.classifier_version
    third = OpenAITextClassificationBackend(first.settings, client=first.client, limits=TextLimits(visible_text=50))
    assert third.classifier_version != first.classifier_version
    state = set_manual(ClassificationState(), "life")
    automatic = classify_many_cached([doc()], first)[0]
    assert effective_category(apply_automatic(state, automatic)) == {"category": "life", "source": "manual"}


def test_truncated_excerpt_maps_to_verifiable_original_field():
    fake = FakeEndpoint()
    payload = doc("建筑展板参考" * 100)
    result = classify_many_cached([payload], fake.backend())[0]
    assert result["evidence"][0]["field"] == "post.title"
    assert result["evidence"][0]["value"] == payload["post"]["title"]


def test_core_bridge_never_returns_unsanitized_evidence_field():
    fake = FakeEndpoint()
    payload = doc("建筑展板 https://private.example/a " + KEY)
    result = classify_many_cached([payload], fake.backend())[0]
    assert result["status"] == "backend_error"
    assert KEY not in json.dumps(result)


def test_usage_without_api_usage_is_not_estimated():
    backend = FakeEndpoint(usage=False).backend()
    backend.predict_batch([doc()])
    assert backend.usage.prompt_tokens is None and backend.usage.completion_tokens is None


@pytest.fixture
def export_database(tmp_path):
    path = tmp_path / "source.db"
    c = sqlite3.connect(path)
    c.executescript("""CREATE TABLE posts(id INTEGER,source_id TEXT,title TEXT,description TEXT,platform TEXT,is_video INTEGER);
    CREATE TABLE post_images(post_id INTEGER,image_id INTEGER,position INTEGER);
    CREATE TABLE analyses(image_id INTEGER,status TEXT,subject TEXT,description TEXT,layout_json TEXT,style_json TEXT,visible_text TEXT);""")
    c.execute("INSERT INTO posts VALUES(1,'work-a',?,?, 'xhs',0)", ("建筑设计参考", "link https://private.example/a " + KEY))
    c.execute("INSERT INTO post_images VALUES(1,1,29)")
    c.execute("INSERT INTO analyses VALUES(1,'done','[\"建筑\"]','建筑展板','{}','{}','文字')")
    c.commit()
    c.close()
    return path


def test_database_export_is_read_only_and_does_not_export_paths_tokens_or_ids(export_database):
    before = hashlib.sha256(export_database.read_bytes()).hexdigest()
    records = export_posts(export_database, secrets=(KEY,))
    assert hashlib.sha256(export_database.read_bytes()).hexdigest() == before
    assert records[0]["coverage"] == {"images": 1, "analyzed_images": 1}
    text = json.dumps(records)
    assert KEY not in text and "https://" not in text and "file_path" not in text
    assert records[0]["work_key"] == "work-a"
    assert "post_id" not in text


def test_unlabeled_report_never_claims_accuracy_and_labeled_metrics_work(export_database):
    records = export_posts(export_database, secrets=(KEY,))
    report = evaluate_predictions(records, FakeEndpoint().backend())
    assert report["summary"]["accuracy"] is None and report["summary"]["labeled"] == 0
    assert "待人工标注" in render_report(report)
    assert report["usage"]["average_reported_tokens_per_requested_post"] == 120
    gt = label_template(records)
    gt[0]["expected"] = "life"
    labeled = evaluate_predictions(records, FakeEndpoint().backend(), ground_truth=gt)
    assert labeled["summary"]["accuracy"] == 0
    assert labeled["confusion"]["life"]["design_reference"] == 1
    assert labeled["high_confidence_wrong_work_keys"] == ["work-a"]
    assert labeled["per_category"]["life"]["recall"] == 0


def test_ground_truth_invalid_or_duplicate_labels_rejected():
    with pytest.raises(ValueError): load_ground_truth([{"work_key": "a", "expected": "fake"}])
    with pytest.raises(ValueError): load_ground_truth([{"work_key": "a"}, {"work_key": "a"}])


def state(category, manual=None):
    return ClassificationState({"category": category}, manual)


def test_shared_image_appears_once_with_only_surface_visible_sources():
    images = [{"image_id": 1, "url": "/images/a.png", "sources": [{"post_id": 10}, {"post_id": 20}]},
              {"image_id": 1, "sources": [{"post_id": 10}]},
              {"image_id": 2, "sources": [{"post_id": 20}]}]
    before = deepcopy(images)
    states = {10: state("design_reference"), 20: state("life")}
    output = visible_image_sources(images, states, "search")
    assert len(output) == 1 and output[0]["image_id"] == 1
    assert [s["post_id"] for s in output[0]["sources"]] == [10]
    assert "category" not in output[0] and images == before
    assert len(visible_image_sources(images, states, "library")) == 2


def test_manual_override_is_per_post_not_a_permanent_shared_image_category():
    images = [{"image_id": 1, "sources": [{"post_id": 10}, {"post_id": 20}]}]
    states = {10: state("design_reference", "life"), 20: state("life", "art_reference")}
    result = visible_image_sources(images, states, "recommendation")
    assert [s["post_id"] for s in result[0]["sources"]] == [20]
    assert result[0]["sources"][0]["effective_classification"] == {"category": "art_reference", "source": "manual"}


def test_life_only_hidden_uncertain_separate_and_missing_state_not_dropped_from_library():
    images = [{"image_id": 1, "sources": [{"post_id": 10}]}, {"image_id": 2, "sources": [{"post_id": 99}]}]
    states = {10: state("life")}
    assert visible_image_sources(images, states, "design_workspace") == []
    assert [r["image_id"] for r in visible_image_sources(images, states, "life")] == [1]
    assert [r["image_id"] for r in visible_image_sources(images, states, "uncertain")] == [2]
    assert len(visible_image_sources(images, states, "library")) == 2


def test_excerpt_joined_with_ellipsis_is_rejected_with_safe_diagnostic_no_retry():
    fake = FakeEndpoint(mutate=lambda a: a["results"][0]["evidence"][0].update(text="建筑……参考"))
    backend = fake.backend()
    result = backend.predict_batch([doc()])[0]
    assert result["error"] == "evidence_not_exact_excerpt"
    backend.predict_batch([doc()])
    assert len(fake.requests) == 1


def test_video_packet_and_result_explicitly_limit_scope():
    payload = doc()
    payload["post"]["is_video"] = True
    fake = FakeEndpoint()
    result = classify_many_cached([payload], fake.backend())[0]
    packet = json.loads(json.loads(fake.requests[0].content)["messages"][1]["content"])["posts"][0]
    assert packet["video_scope"] == result["scope"] == "cover_and_description"
    assert "未观看视频" in result["limitations"][0]


def test_resume_with_labels_scores_cached_results_without_any_inference(export_database):
    records = export_posts(export_database, secrets=(KEY,))
    first = evaluate_predictions(records, FakeEndpoint().backend())
    fake = FakeEndpoint()
    second = evaluate_predictions(records, fake.backend(),
        ground_truth=[{"work_key": "work-a", "expected": "design_reference"}],
        previous={"work-a": first["cases"][0]["prediction"]})
    assert fake.requests == [] and second["summary"]["accuracy"] == 1
    assert second["usage"]["average_reported_tokens_per_requested_post"] is None


def test_source_positions_pass_through_without_assuming_zero_based():
    image = {"image_id": 1, "category": "life", "sources": [{"post_id": 10, "position": 29}]}
    output = visible_image_sources([image], {10: state("design_reference")}, "search")
    assert output[0]["sources"][0]["position"] == 29
    assert "category" not in output[0]


def test_cli_default_only_exports_and_preserves_existing_labels(export_database, tmp_path, monkeypatch, capsys):
    import importlib.util
    from pathlib import Path
    import sys
    spec = importlib.util.spec_from_file_location("eval_classification_cli", Path(__file__).parents[1] / "tools/eval_classification.py")
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    output = tmp_path / "report"
    output.mkdir()
    labels = '[{"work_key":"work-a","expected":"life"}]'
    (output / "ground_truth.json").write_text(labels, encoding="utf-8")
    monkeypatch.setattr(OpenAITextClassificationBackend, "__init__", lambda *a, **k: pytest.fail("unexpected model client"))
    monkeypatch.setattr(sys, "argv", ["eval_classification.py", "--db", str(export_database), "--output-dir", str(output)])
    assert cli.main() == 0
    assert "not_requested" in capsys.readouterr().out
    assert (output / "ground_truth.json").read_text(encoding="utf-8") == labels
    assert (output / "inputs.json").is_file() and not (output / "report.json").exists()
