"""Pure post-level classification policy over a pluggable TEXT/analysis backend.

No model client, OCR, image loading, database, search or recommendation access.
The backend assesses existing post text and image analyses; this module verifies
field evidence and combines scoped claims conservatively. It cannot establish that
a backend's semantic interpretation is true. Offline fixed backends test policy,
not real classifier quality. Confidence is conservative supplied support, not a
calibrated probability (uncertain always reports 0).
"""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, replace
import hashlib
import json
import math
import re
from typing import Protocol

CLASSIFIER_VERSION = "collection_policy_v1"
CATEGORIES = ("design_reference", "art_reference", "life", "uncertain")
REFERENCE_CATEGORIES = frozenset(("design_reference", "art_reference"))


def _category(value):
    if value not in CATEGORIES:
        raise ValueError("unknown collection category")
    return value


def normalize_input(payload: Mapping) -> dict:
    """Keep supported existing fields only; canonical JSON hash has no image bytes."""
    if not isinstance(payload, Mapping):
        raise ValueError("classification input must be a mapping")
    post = payload.get("post")
    if post is None:
        post = {}
    if not isinstance(post, Mapping):
        raise ValueError("post must be a mapping")
    images = payload.get("images")
    if images is None:
        images = []
    if not isinstance(images, (list, tuple)):
        raise ValueError("images must be a list")
    normalized = {"post": {key: post.get(key) if post.get(key) is not None else ""
                           for key in ("title", "description", "platform")}, "images": []}
    normalized["post"]["is_video"] = post.get("is_video", False)
    if any(not isinstance(normalized["post"][key], str) for key in ("title", "description", "platform")):
        raise ValueError("post text fields must be strings")
    if not isinstance(normalized["post"]["is_video"], bool):
        raise ValueError("post.is_video must be boolean")
    for image in images:
        if not isinstance(image, Mapping):
            raise ValueError("image analysis must be a mapping")
        normalized["images"].append({key: deepcopy(image.get(key)) for key in
                                     ("subject", "description", "layout", "style", "visible_text")})
    # Reject non-JSON/nonfinite values deterministically before calling a backend.
    json.dumps(normalized, ensure_ascii=False, sort_keys=True, allow_nan=False)
    return normalized


def input_hash(payload: Mapping) -> str:
    text = json.dumps(normalize_input(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Evidence:
    field: str
    value: object
    kind: str  # intent | content | appearance; layout/style are always appearance


@dataclass(frozen=True)
class Claim:
    category: str
    confidence: float
    evidence: tuple[Evidence, ...]


@dataclass(frozen=True)
class Assessment:
    claims: tuple[Claim, ...] = ()  # no claims = abstain, not a fabricated category


class ClassificationBackend(Protocol):
    def classify(self, document: Mapping) -> Assessment:
        """Read only supplied existing text/analysis. Never re-read pixels or run OCR."""


class FixedBackend:
    """Offline recorded-assessment backend keyed by exact input hash; not a classifier.

    Missing recording abstains. Expected labels are never consulted. calls is a test
    spy; the core passes a copy so accidental backend mutation cannot affect inputs.
    """
    def __init__(self, recordings: Mapping[str, Assessment]):
        self.recordings = deepcopy(dict(recordings))
        self.calls = 0

    def classify(self, document: Mapping) -> Assessment:
        self.calls += 1
        return deepcopy(self.recordings.get(input_hash(document), Assessment()))


def resolve_evidence(document: Mapping, field: str):
    if not isinstance(field, str) or not re.fullmatch(
            r"(?:post\.(?:title|description)|images\[\d+\]\.(?:subject|description|layout|style|visible_text)(?:\.[a-z_]+|\[\d+\])*)", field):
        raise ValueError("unsupported evidence path")
    value = document
    for part in re.findall(r"[a-z_]+|\d+", field):
        value = value[int(part)] if isinstance(value, (list, tuple)) else value[part]
    return value


def _validated_claims(document, assessment):
    if not isinstance(assessment, Assessment):
        raise ValueError("backend must return Assessment")
    strong, ignored = [], []
    for claim in assessment.claims:
        if (not isinstance(claim, Claim) or claim.category not in CATEGORIES[:3]
                or isinstance(claim.confidence, bool) or not isinstance(claim.confidence, (int, float))
                or not math.isfinite(claim.confidence) or not 0 < claim.confidence <= 1 or not claim.evidence):
            raise ValueError("invalid scoped claim")
        evidence, sources = [], set()
        for entry in claim.evidence:
            if not isinstance(entry, Evidence) or entry.kind not in {"intent", "content", "appearance"}:
                raise ValueError("invalid evidence kind")
            actual = resolve_evidence(document, entry.field)
            if (actual != entry.value or actual in (None, "", [], {})
                    or isinstance(actual, str) and actual.strip().lower() == "unknown"):
                raise ValueError("evidence must equal a nonempty existing field")
            source = "post" if entry.field.startswith("post.") else entry.field.split(".", 1)[0]
            sources.add(source)
            kind = "appearance" if re.search(r"\.(?:layout|style)(?:\.|\[|$)", entry.field) else entry.kind
            record = {"field": entry.field, "value": deepcopy(actual), "kind": kind, "source": source,
                      "category": claim.category}
            if kind == "appearance":
                ignored.append({**record, "reason": "appearance_is_not_collection_intent"})
            else:
                evidence.append(record)
        if len(sources) != 1:
            raise ValueError("one claim must refer to one source (post or a particular image)")
        if evidence:
            strong.append({"category": claim.category, "confidence": claim.confidence,
                           "source": next(iter(sources)), "evidence": evidence,
                           "intent": any(e["kind"] == "intent" for e in evidence)})
    return strong, ignored


def classify_collection(payload: Mapping, backend: ClassificationBackend, *,
                        classifier_version: str = CLASSIFIER_VERSION) -> dict:
    """Post intent leads; one conflicting image is an outlier, not a new post category.

    Conflicting post intents -> uncertain. A decisive post intent survives a single
    conflicting image, but >=2 distinct conflicting images -> uncertain. Without a
    decisive post intent, conflicting supported post/image categories -> uncertain.
    No majority vote, image count weighting, or appearance-based design inference.
    """
    if not isinstance(classifier_version, str) or not classifier_version.strip():
        raise ValueError("classifier_version must be nonempty")
    document = normalize_input(payload)
    result = {"category": "uncertain", "confidence": 0.0, "evidence": [],
              "classifier_version": classifier_version, "input_hash": input_hash(document),
              "status": "ok", "reasons": [], "ignored_evidence": [],
              "scope": "cover_and_description" if document["post"]["is_video"] else "post_and_existing_image_analyses"}
    if document["post"]["is_video"]:
        result["limitations"] = ["仅依据封面、简介和已有图片分析；未观看视频"]
    try:
        claims, ignored = _validated_claims(document, backend.classify(deepcopy(document)))
    except Exception:
        # Backend errors/invalid evidence are NOT a valid cached automatic decision.
        # Never expose arbitrary exception bodies (which might contain credentials).
        result.update(status="backend_error", reasons=["backend_failed_or_invalid_evidence"])
        return result
    result["ignored_evidence"] = ignored
    if not claims:
        result["reasons"] = ["insufficient_nonappearance_evidence"]
        return result
    post_intents = {c["category"] for c in claims if c["source"] == "post" and c["intent"]}
    if len(post_intents) > 1:
        chosen, reason = None, "conflicting_post_intents"
    elif post_intents:
        chosen = next(iter(post_intents))
        conflicting_images = {c["source"] for c in claims if c["source"] != "post" and c["category"] != chosen}
        if len(conflicting_images) >= 2:
            chosen, reason = None, "multiple_images_conflict_with_post_intent"
        else:
            reason = "post_intent_with_single_image_outlier" if conflicting_images else "supported_post_intent"
    else:
        categories = {c["category"] for c in claims}
        chosen = next(iter(categories)) if len(categories) == 1 else None
        reason = "consistent_scoped_evidence" if chosen else "conflicting_scoped_categories"
    result["reasons"] = [reason]
    if chosen:
        result["category"] = chosen
        supporting = [c for c in claims if c["category"] == chosen]
        result["confidence"] = min(c["confidence"] for c in supporting)
        for c in claims:
            if c["category"] != chosen:
                result["ignored_evidence"].extend({**e, "reason": "outlier_or_contradicting_post_intent"} for e in c["evidence"])
    else:
        supporting = claims
    for claim in supporting:
        for evidence in claim["evidence"]:
            if evidence not in result["evidence"]:
                result["evidence"].append(evidence)
    return result


def needs_recompute(previous: Mapping | None, payload: Mapping, classifier_version=CLASSIFIER_VERSION) -> bool:
    if not isinstance(classifier_version, str) or not classifier_version.strip():
        raise ValueError("classifier_version must be nonempty")
    if not previous or previous.get("status") != "ok":
        return True
    confidence = previous.get("confidence")
    return (previous.get("category") not in CATEGORIES
            or isinstance(confidence, bool) or not isinstance(confidence, (int, float))
            or not math.isfinite(confidence) or not 0 <= confidence <= 1
            or previous.get("classifier_version") != classifier_version
            or previous.get("input_hash") != input_hash(payload))


def classify_cached(payload, backend, *, previous=None, classifier_version=CLASSIFIER_VERSION):
    if not needs_recompute(previous, payload, classifier_version):
        return deepcopy(dict(previous))
    return classify_collection(payload, backend, classifier_version=classifier_version)


@dataclass(frozen=True)
class ClassificationState:
    automatic: dict | None = None
    manual: str | None = None

    def __post_init__(self):
        if self.manual is not None:
            _category(self.manual)
        object.__setattr__(self, "automatic", deepcopy(self.automatic))


def apply_automatic(state: ClassificationState, result: Mapping) -> ClassificationState:
    _category(result.get("category"))
    return replace(state, automatic=deepcopy(dict(result)))


def set_manual(state: ClassificationState, category: str) -> ClassificationState:
    return replace(state, manual=_category(category))


def clear_manual(state: ClassificationState) -> ClassificationState:
    return replace(state, manual=None)


def effective_category(state: ClassificationState) -> dict:
    if state.manual is not None:
        return {"category": state.manual, "source": "manual"}
    return {"category": state.automatic["category"] if state.automatic else "uncertain", "source": "automatic"}


def should_include(category: str, surface: str) -> bool:
    """Resolve manual override FIRST; this only chooses a default view, never deletes."""
    _category(category)
    allowed = {"library": set(CATEGORIES), "design_workspace": REFERENCE_CATEGORIES,
               "search": REFERENCE_CATEGORIES, "recommendation": REFERENCE_CATEGORIES,
               "life": {"life"}, "uncertain": {"uncertain"}}
    if surface not in allowed:
        raise ValueError("unknown collection surface")
    return category in allowed[surface]
