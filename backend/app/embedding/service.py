"""Incremental embedding preparation: pure rules, no DB writes.

Integration (see docs/embedding_core.md): the app does not call prepare_embedding()
directly; store.py + adapter.py batch done analyses into the existing embeddings table
(schema v3). An embedding failure never touches analyses.status.
"""
from __future__ import annotations

import math
import struct
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from . import text as text_builder
from .client import DEFAULT_DIMENSIONS, DEFAULT_MODEL, EmbeddingClient, EmbeddingError


@dataclass(frozen=True)
class EmbeddingSpec:
    model: str = DEFAULT_MODEL
    dimensions: int = DEFAULT_DIMENSIONS
    scheme: str = text_builder.TEXT_VERSION
    text_version: str = text_builder.TEXT_VERSION

    @classmethod
    def from_settings(cls, settings: Any) -> "EmbeddingSpec":
        return cls(
            model=settings.model,
            dimensions=settings.dimensions,
            scheme=settings.scheme,
            text_version=settings.text_version,
        )


@dataclass(frozen=True)
class EmbeddingRecord:
    image_id: int
    vector: list[float]
    model: str
    dimensions: int
    scheme: str
    text_hash: str
    text: str


def _vector_is_valid(vector: Any, dimensions: int) -> bool:
    if dimensions <= 0:
        return False
    if isinstance(vector, (bytes, bytearray, memoryview)):
        raw = bytes(vector)
        if len(raw) != dimensions * 4:
            return False
        values: Sequence[float] = struct.unpack("<" + "f" * dimensions, raw)
    else:
        try:
            values = [float(value) for value in vector]
        except (TypeError, ValueError):
            return False
        if len(values) != dimensions:
            return False
    if any(not math.isfinite(value) for value in values):
        return False
    return math.sqrt(sum(value * value for value in values)) > 1e-12


def needs_rebuild(existing: Mapping[str, Any] | None, spec: EmbeddingSpec,
                  current_text_hash: str) -> bool:
    """True unless the row has the same identity AND a usable vector.

    Identity: model, dimensions, scheme, text_version, text_hash. Visual model name is
    deliberately not an input: an unchanged combined text must not be re-embedded when
    only VISION_MODEL changed. A missing/corrupt/NaN/zero vector is always rebuilt.
    """
    if not existing:
        return True
    if existing.get("model") != spec.model:
        return True
    if existing.get("dimensions") != spec.dimensions:
        return True
    if existing.get("scheme") != spec.scheme:
        return True
    if existing.get("text_version") != spec.text_version:
        return True
    if existing.get("text_hash") != current_text_hash:
        return True
    if "vector" not in existing or existing.get("vector") is None:
        return True
    return not _vector_is_valid(existing.get("vector"), spec.dimensions)


def make_spec(model: str | None = None, dimensions: int | None = None,
              scheme: str | None = None, text_version: str | None = None) -> EmbeddingSpec:
    return EmbeddingSpec(
        model=(model or DEFAULT_MODEL).strip(),
        dimensions=int(DEFAULT_DIMENSIONS if dimensions is None else dimensions),
        scheme=(scheme or text_builder.TEXT_VERSION).strip(),
        text_version=(text_version or text_builder.TEXT_VERSION).strip(),
    )


def assert_same_config(client: Any, spec: EmbeddingSpec) -> None:
    """Reject a client/spec pair that did not come from one settings object."""
    settings = getattr(client, "settings", None)
    if settings is None:
        return
    pairs = (("model", settings.model, spec.model),
             ("dimensions", settings.dimensions, spec.dimensions),
             ("scheme", settings.scheme, spec.scheme),
             ("text_version", settings.text_version, spec.text_version))
    for name, left, right in pairs:
        if left != right:
            raise ValueError(f"client/spec 配置不一致: {name} {left!r} != {right!r}")


def build_text(analysis: Mapping[str, Any], colors: Iterable[Any] = ()) -> str:
    return text_builder.build_combined_d_v1(analysis, colors)


def prepare_embedding(client: EmbeddingClient, spec: EmbeddingSpec, image_id: int,
                      analysis: Mapping[str, Any], colors: Iterable[Any] = (),
                      existing: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Build text, decide incrementality, embed if needed.

    Returns one of:
      {"status": "skipped", "reason": "no_text"|"up_to_date", "text_hash": ...}
      {"status": "embedded", "record": EmbeddingRecord}
      {"status": "failed", "kind": ..., "scope": ..., "error": ..., "text_hash": ...}
    Never raises for API/validation failures and never mutates the analysis.
    """
    assert_same_config(client, spec)
    text = build_text(analysis, colors)
    if not text:
        return {"status": "skipped", "reason": "no_text", "text_hash": ""}
    current_hash = text_builder.text_hash(text)
    if not needs_rebuild(existing, spec, current_hash):
        return {"status": "skipped", "reason": "up_to_date", "text_hash": current_hash}
    try:
        vector = client.embed_one(text)
    except EmbeddingError as exc:
        return {"status": "failed", "kind": exc.kind, "scope": exc.scope,
                "error": exc.message, "text_hash": current_hash}
    return {
        "status": "embedded",
        "record": EmbeddingRecord(
            image_id=image_id,
            vector=vector,
            model=spec.model,
            dimensions=spec.dimensions,
            scheme=spec.scheme,
            text_hash=current_hash,
            text=text,
        ),
        "text_hash": current_hash,
    }


def vector_to_blob(vector: list[float]) -> bytes:
    """float32 little-endian BLOB, stored in embeddings.vector."""
    return struct.pack("<" + "f" * len(vector), *vector)


def blob_to_vector(blob: bytes, dimensions: int) -> list[float]:
    expected = 4 * dimensions
    if len(blob) != expected:
        raise ValueError(f"向量长度不符: {len(blob)} != {expected}")
    return list(struct.unpack("<" + "f" * dimensions, blob))
