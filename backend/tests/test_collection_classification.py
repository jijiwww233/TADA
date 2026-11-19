from copy import deepcopy
import json
from pathlib import Path

import pytest

from app.collection_classification import (
    Assessment, Claim, Evidence, FixedBackend, ClassificationState, CATEGORIES,
    CLASSIFIER_VERSION, apply_automatic, classify_collection, classify_cached,
    clear_manual, effective_category, input_hash, needs_recompute, normalize_input,
    resolve_evidence, set_manual, should_include,
)
from app.classification_evaluation import audit_evidence, evaluate_cases, recorded_assessment, render_markdown

FIXTURE = Path(__file__).parent / "fixtures" / "collection_categories_v1.json"
CASES = json.loads(FIXTURE.read_text(encoding="utf-8"))["cases"]


def backend(case):
    return FixedBackend({input_hash(case["input"]): recorded_assessment(case)})


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_recorded_policy_fixture(case):
    before = deepcopy(case["input"])
    fake = backend(case)
    result = classify_collection(case["input"], fake)
    assert result["status"] == "ok" and result["category"] == case["expected"]
    assert not audit_evidence(case["input"], result)
    assert result == classify_collection(case["input"], fake)
    assert case["input"] == before
    if result["category"] == "uncertain":
        assert result["confidence"] == 0
    if case["input"]["post"]["is_video"]:
        assert result["scope"] == "cover_and_description"
        assert "未观看视频" in "".join(result["limitations"])


@pytest.mark.parametrize("category", CATEGORIES)
@pytest.mark.parametrize("surface", ["library", "design_workspace", "search", "recommendation", "life", "uncertain"])
def test_default_visibility_never_discards_collections(category, surface):
    expected = (surface == "library" or
                (surface in {"design_workspace", "search", "recommendation"} and category in {"design_reference", "art_reference"}) or
                (surface == "life" and category == "life") or
                (surface == "uncertain" and category == "uncertain"))
    assert should_include(category, surface) == expected


def test_manual_always_wins_across_reanalysis_resync_and_version_upgrade():
    case = CASES[0]
    first = classify_collection(case["input"], backend(case))
    state = set_manual(ClassificationState(first), "life")
    original = deepcopy(state.automatic)
    for version in ("reanalysis-v2", "resync-v3", "classifier-v4"):
        automatic = classify_collection(case["input"], backend(case), classifier_version=version)
        state = apply_automatic(state, automatic)
        assert effective_category(state) == {"category": "life", "source": "manual"}
        assert not should_include(effective_category(state)["category"], "search")
    assert original == first  # no input/old automatic mutation
    restored = clear_manual(state)
    assert effective_category(restored) == {"category": "design_reference", "source": "automatic"}
    assert effective_category(state)["source"] == "manual"


def test_manual_uncertain_is_real_override_and_clear_without_auto_keeps_visible():
    state = set_manual(ClassificationState(), "uncertain")
    assert effective_category(state)["source"] == "manual"
    assert effective_category(clear_manual(state)) == {"category": "uncertain", "source": "automatic"}
    assert should_include("uncertain", "library") and should_include("uncertain", "uncertain")


def test_same_hash_and_version_skip_backend_and_return_detached_result():
    case = CASES[0]
    fake = backend(case)
    first = classify_cached(case["input"], fake)
    second = classify_cached(case["input"], fake, previous=first)
    assert first == second and fake.calls == 1
    second["evidence"][0]["value"] = "edited"
    assert first["evidence"][0]["value"] != "edited"


def test_version_or_input_change_requires_recompute_but_vision_model_name_does_not():
    case = CASES[0]
    fake = backend(case)
    result = classify_cached(case["input"], fake)
    assert not needs_recompute(result, case["input"])
    assert needs_recompute(result, case["input"], "new-version")
    classify_cached(case["input"], fake, previous=result, classifier_version="new-version")
    assert fake.calls == 2
    updated = deepcopy(case["input"])
    updated["post"]["description"] += "changed"
    assert needs_recompute(result, updated)
    classify_cached(updated, fake, previous=result)
    assert fake.calls == 3
    model_only = deepcopy(case["input"])
    model_only["vision_model"] = "different"
    assert input_hash(model_only) == input_hash(case["input"])


def test_hash_is_key_order_independent_but_image_order_is_part_of_evidence_identity():
    payload = {"images": [{"description": "one"}, {"description": "two"}], "post": {"title": "a"}}
    assert input_hash(payload) == input_hash({"post": {"title": "a"}, "images": payload["images"]})
    swapped = deepcopy(payload)
    swapped["images"].reverse()
    assert input_hash(payload) != input_hash(swapped)


def test_style_or_layout_cannot_claim_intent_even_if_backend_marks_it_so():
    payload = {"images": [{"style": {"tags": ["精致"]}, "layout": {"columns": ["multi"]}}]}
    assessment = Assessment((Claim("design_reference", .99, (
        Evidence("images[0].style.tags[0]", "精致", "intent"),
        Evidence("images[0].layout.columns[0]", "multi", "content"))),))
    result = classify_collection(payload, FixedBackend({input_hash(payload): assessment}))
    assert result["category"] == "uncertain"
    assert result["evidence"] == [] and len(result["ignored_evidence"]) == 2


def test_single_anomalous_image_does_not_become_multiple_votes_by_repeating_claims():
    case = next(c for c in CASES if c["id"] == "l13")
    assessment = recorded_assessment(case)
    duplicated = Assessment((assessment.claims[0],) + (assessment.claims[1],) * 20)
    result = classify_collection(case["input"], FixedBackend({input_hash(case["input"]): duplicated}))
    assert result["category"] == "life"
    assert "single_image_outlier" in result["reasons"][0]


@pytest.mark.parametrize("bad", [
    Claim("design_reference", .9, (Evidence("post.title", "invented", "intent"),)),
    Claim("life", .9, (Evidence("post.platform", "xhs", "intent"),)),
    Claim("life", .9, (Evidence("images[999].description", "x", "content"),)),
    Claim("life", 2, (Evidence("post.title", "title", "intent"),)),
    Claim("life", float("nan"), (Evidence("post.title", "title", "intent"),)),
    Claim("life", .9, (Evidence("post.title", "title", "watched_video"),)),
])
def test_invalid_backend_claim_abstains_and_is_not_a_valid_cached_decision(bad):
    payload = {"post": {"title": "title", "platform": "xhs"}}
    result = classify_collection(payload, FixedBackend({input_hash(payload): Assessment((bad,))}))
    assert result["category"] == "uncertain" and result["status"] == "backend_error"
    assert needs_recompute(result, payload)


def test_backend_exception_is_not_leaked_or_allowed_to_override_manual():
    class Broken:
        def classify(self, document):
            raise RuntimeError("Bearer secret-must-not-leak")
    result = classify_collection({"post": {"title": "a"}}, Broken())
    assert "secret-must-not-leak" not in json.dumps(result)
    assert effective_category(apply_automatic(set_manual(ClassificationState(), "art_reference"), result))["category"] == "art_reference"


def test_backend_cannot_mutate_supplied_post_or_analysis():
    class Mutating:
        def classify(self, document):
            document["post"]["title"] = "changed"
            return Assessment()
    payload = {"post": {"title": "original"}, "images": [{"subject": ["人物"]}]}
    before = deepcopy(payload)
    classify_collection(payload, Mutating())
    assert payload == before


def test_scope_evidence_cannot_join_post_and_image_into_one_vote():
    payload = {"post": {"title": "a"}, "images": [{"description": "b"}]}
    mixed = Claim("life", .9, (Evidence("post.title", "a", "intent"), Evidence("images[0].description", "b", "content")))
    result = classify_collection(payload, FixedBackend({input_hash(payload): Assessment((mixed,))}))
    assert result["status"] == "backend_error"


def test_confidence_is_conservative_support_not_majority_or_probability():
    payload = {"post": {"title": "a", "description": "b"}}
    claims = Assessment((Claim("life", .95, (Evidence("post.title", "a", "intent"),)),
                         Claim("life", .6, (Evidence("post.description", "b", "content"),))))
    result = classify_collection(payload, FixedBackend({input_hash(payload): claims}))
    assert result["confidence"] == .6


def test_fixture_report_has_confusion_counts_and_exposes_wrong_backend():
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    report = evaluate_cases(fixture)
    assert report["summary"] == {"cases": 48, "passed": 48, "failed": 0}
    assert {k: v["fixtures"] for k, v in report["per_category"].items()} == {
        "design_reference": 12, "art_reference": 12, "life": 14, "uncertain": 10}
    wrong = evaluate_cases({**fixture, "cases": [fixture["cases"][0]]}, backend_factory=lambda _: FixedBackend({}))
    assert wrong["summary"]["failed"] == 1
    assert wrong["confusion"]["design_reference"]["uncertain"] == 1
    assert "not live model" in render_markdown(report)


def test_checked_in_reports_are_current_and_json_serializable():
    report = evaluate_cases(json.loads(FIXTURE.read_text(encoding="utf-8")))
    directory = Path(__file__).parents[1] / "classification_reports"
    assert json.loads((directory / "report.json").read_text(encoding="utf-8")) == report
    assert (directory / "report.md").read_text(encoding="utf-8") == render_markdown(report)
    assert json.loads(json.dumps(report)) == report


def test_no_model_db_file_or_image_access_needed(monkeypatch):
    import builtins
    import socket
    import sqlite3
    case = CASES[0]
    fake = backend(case)
    def forbidden(*args, **kwargs):
        raise AssertionError("unexpected IO")
    monkeypatch.setattr(builtins, "open", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(sqlite3, "connect", forbidden)
    assert classify_collection(case["input"], fake)["category"] == "design_reference"


@pytest.mark.parametrize("payload", [None, {"post": []}, {"images": "image"}, {"post": {"is_video": "false"}}])
def test_invalid_input_is_rejected_before_backend(payload):
    fake = FixedBackend({})
    with pytest.raises(ValueError):
        classify_collection(payload, fake)
    assert fake.calls == 0


def test_visibility_typo_is_not_silent_data_hiding():
    with pytest.raises(ValueError):
        should_include("life", "serach")
    with pytest.raises(ValueError):
        should_include("unknown-label", "library")
