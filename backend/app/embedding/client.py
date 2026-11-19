"""OpenAI-compatible embedding client for the single combined image text.

One config object (EmbeddingSettings) is the only source of model / dimensions /
scheme / text_version. build_embedding_components() derives the client and the
EmbeddingSpec from the same object, so the two can never silently use different
defaults.

Batch size is capped at 10. Every response is validated for count, order, dimensions,
finite values and non-zero norm. Errors carry a scope used by the index service:

  global   auth / permission / rate limit / connect / config: the whole batch failed;
           never split it into per-image requests.
  unknown  read/write timeout, remote protocol, malformed result: the request may have
           been processed; never automatically retry per image (duplicate billing).
  input    the request was definitely rejected because of one bad input string; only
           this scope may be split into per-image retries.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any

import httpx

from .. import config
from .text import TEXT_VERSION

DEFAULT_MODEL = "qwen3.7-text-embedding-flash"
DEFAULT_DIMENSIONS = 1024
MAX_BATCH = 10
TIMEOUT_SECONDS = 60
STOP_KINDS = {"config", "auth", "model"}  # every later batch would fail the same way

SCOPE_GLOBAL = "global"
SCOPE_UNKNOWN = "unknown"
SCOPE_INPUT = "input"


@dataclass(frozen=True)
class EmbeddingSettings:
    """Single source of truth for client + spec."""

    base_url: str
    api_key: str
    model: str = DEFAULT_MODEL
    dimensions: int = DEFAULT_DIMENSIONS
    scheme: str = TEXT_VERSION
    text_version: str = TEXT_VERSION

    @classmethod
    def from_env(cls) -> "EmbeddingSettings":
        ai = config.ai_settings()  # single source for all AI config
        raw_dim = ai["embedding_dimensions"]
        try:
            dimensions = int(raw_dim)
        except ValueError as exc:
            raise EmbeddingError("config", f"EMBEDDING_DIMENSIONS 不是整数: {raw_dim!r}",
                                 scope=SCOPE_GLOBAL) from exc
        return cls(
            base_url=ai["base_url"],
            api_key=ai["api_key"],
            model=ai["embedding_model"],
            dimensions=dimensions,
            scheme=TEXT_VERSION,
            text_version=TEXT_VERSION,
        )

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.api_key)


# Backwards-compatible name used in early docs/tests.
EmbeddingConfig = EmbeddingSettings


class EmbeddingError(Exception):
    """Embedding-only failure. Never means the visual analysis failed."""

    def __init__(self, kind: str, message: str, *, settings: EmbeddingSettings | None = None,
                 scope: str = SCOPE_UNKNOWN):
        self.kind = kind
        self.scope = scope
        self.message = _redact(message, settings)
        super().__init__(self.message)


def _redact(message: str, settings: EmbeddingSettings | None) -> str:
    text = str(message)
    if settings and len(settings.api_key) >= 8:
        text = text.replace(settings.api_key, "<已隐藏>")
    text = re.sub(r"(?i)bearer\s+[^\s\"',;}\]]+", "Bearer <已隐藏>", text)
    return text


def _http_scope(status: int, body: str) -> str:
    # HTTP status alone is never enough to attribute a failure to ONE input item.
    # 400 is a rejected request with no valid result, but it may be a global parameter
    # error; the safe default is "unknown" (no split, no per-image retry).
    if status in (401, 403, 405, 429):
        return SCOPE_GLOBAL
    return SCOPE_UNKNOWN


class EmbeddingClient:
    def __init__(self, settings: EmbeddingSettings | None = None,
                 http_client: httpx.Client | None = None, timeout: float = TIMEOUT_SECONDS):
        self.settings = settings or EmbeddingSettings.from_env()
        self._client = http_client or httpx.Client(timeout=timeout)
        self._owns_client = http_client is None
        self.request_count = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.usage_complete = True

    @property
    def requests(self) -> int:
        """Day4 adapter compatibility: both names count the same actual requests."""
        return self.request_count

    def __repr__(self) -> str:
        return (f"EmbeddingClient(model={self.settings.model!r}, "
                f"dimensions={self.settings.dimensions}, scheme={self.settings.scheme!r}, "
                f"key=<redacted>)")

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> "EmbeddingClient":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed texts in batches of at most MAX_BATCH, preserving input order."""
        if not isinstance(texts, list) or not texts:
            raise EmbeddingError("input", "texts 必须是非空 list[str]",
                                 settings=self.settings, scope=SCOPE_INPUT)
        for text in texts:
            if not isinstance(text, str) or not text.strip():
                raise EmbeddingError("input", "embedding 文本不能为空",
                                     settings=self.settings, scope=SCOPE_INPUT)
        vectors: list[list[float]] = []
        for start in range(0, len(texts), MAX_BATCH):
            vectors.extend(self._embed_batch(texts[start:start + MAX_BATCH]))
        return vectors

    def embed_one(self, text: str) -> list[float]:
        return self.embed([text])[0]

    def _endpoint(self) -> str:
        return self.settings.base_url.rstrip("/") + "/embeddings"

    def _embed_batch(self, batch: list[str]) -> list[list[float]]:
        if not self.settings.base_url or not self.settings.api_key:
            raise EmbeddingError("config", "缺少 AI_BASE_URL / AI_API_KEY",
                                 settings=self.settings, scope=SCOPE_GLOBAL)
        payload = {
            "model": self.settings.model,
            "input": batch,
            "dimensions": self.settings.dimensions,
        }
        self.request_count += 1
        try:
            response = self._client.post(
                self._endpoint(),
                json=payload,
                headers={"Authorization": "Bearer " + self.settings.api_key},
            )
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ProxyError) as exc:
            raise EmbeddingError("http", f"embedding 连接失败: {exc}",
                                 settings=self.settings, scope=SCOPE_GLOBAL) from exc
        except httpx.TimeoutException as exc:
            raise EmbeddingError("http", f"embedding 超时（结果未知）: {exc}",
                                 settings=self.settings, scope=SCOPE_UNKNOWN) from exc
        except httpx.RemoteProtocolError as exc:
            raise EmbeddingError("http", f"embedding 远端协议错误（结果未知）: {exc}",
                                 settings=self.settings, scope=SCOPE_UNKNOWN) from exc
        except httpx.HTTPError as exc:
            raise EmbeddingError("http", f"embedding 请求失败（结果未知）: {exc}",
                                 settings=self.settings, scope=SCOPE_UNKNOWN) from exc
        if response.status_code != 200:
            body = response.text[:500]
            kind = {401: "auth", 403: "auth", 404: "model"}.get(response.status_code, "http")
            raise EmbeddingError(
                kind,
                f"embedding HTTP {response.status_code}: {body}",
                settings=self.settings,
                scope=_http_scope(response.status_code, body),
            )
        try:
            body = response.json()
        except ValueError as exc:
            raise EmbeddingError("parse", "embedding 响应不是 JSON（结果未知）",
                                 settings=self.settings, scope=SCOPE_UNKNOWN) from exc
        usage = body.get('usage',{}) if isinstance(body,dict) else {}
        tokens = usage.get('prompt_tokens',usage.get('input_tokens',usage.get('total_tokens')))
        if type(tokens) is int and tokens>=0:
            self.prompt_tokens += tokens
        else:
            self.usage_complete = False
        data = body.get("data") if isinstance(body, dict) else None
        if not isinstance(data, list) or len(data) != len(batch):
            raise EmbeddingError(
                "count",
                f"embedding 返回数量不符: {0 if not isinstance(data, list) else len(data)} != {len(batch)}",
                settings=self.settings, scope=SCOPE_UNKNOWN,
            )

        # Order: OpenAI-compatible responses should carry index; validate when present.
        indexes = [item.get("index") for item in data if isinstance(item, dict)]
        if all(isinstance(index, int) for index in indexes) and len(indexes) == len(data):
            if sorted(indexes) != list(range(len(batch))):
                raise EmbeddingError("order", f"embedding index 非法: {indexes}",
                                     settings=self.settings, scope=SCOPE_UNKNOWN)
            ordered = [item for _, item in sorted(zip(indexes, data), key=lambda pair: pair[0])]
        else:
            ordered = data

        vectors: list[list[float]] = []
        for item in ordered:
            vector = item.get("embedding") if isinstance(item, dict) else None
            if not isinstance(vector, list) or len(vector) != self.settings.dimensions:
                length = len(vector) if isinstance(vector, list) else 0
                raise EmbeddingError(
                    "dimension",
                    f"embedding 维度不符: {length} != {self.settings.dimensions}",
                    settings=self.settings, scope=SCOPE_UNKNOWN,
                )
            try:
                values = [float(value) for value in vector]
            except (TypeError, ValueError) as exc:
                raise EmbeddingError("value", "embedding 含非数值", settings=self.settings,
                                     scope=SCOPE_UNKNOWN) from exc
            if any(not math.isfinite(value) for value in values):
                raise EmbeddingError("nonfinite", "embedding 含 NaN/Inf",
                                     settings=self.settings, scope=SCOPE_UNKNOWN)
            if math.sqrt(sum(value * value for value in values)) <= 1e-12:
                raise EmbeddingError("zero_norm", "embedding 是零向量",
                                     settings=self.settings, scope=SCOPE_UNKNOWN)
            vectors.append(values)
        return vectors


def build_embedding_components(settings: EmbeddingSettings | None = None) -> tuple[EmbeddingClient, Any]:
    """Derive client + spec from the SAME settings object (single config entry)."""
    from .service import EmbeddingSpec

    settings = settings or EmbeddingSettings.from_env()
    return EmbeddingClient(settings), EmbeddingSpec.from_settings(settings)
