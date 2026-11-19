#!/usr/bin/env python
"""One-run offline recommendation comparison for the dev brief "复古建筑展板".

WHAT THIS DOES
--------------
Without --dry-run, exactly one CreativeApplication.recommend() call is made:
  * one text-only needs decomposition request,
  * one embedding request carrying the three per-column query texts,
  * one vision review request per column (3 columns) with the real images.
No batch analysis happens here; it only reads the existing analysed library.

recommend() itself returns only the reviewed picks (reviewed_ids + items). This
harness records the intermediates it does not return by wrapping, in-process only:
  * creative.dimension_recall / creative.palette_recall -> full recall order
    (image_id + semantic score) for every column,
  * the reviewer callable -> the exact candidate order sent to review, the exact
    review prompt text and the review result/usage,
  * the embedding client factory -> the exact three query texts in the batch.
Nothing in backend/app is modified on disk and no function stays patched after
the single run (try/finally restores the module globals).

ARTIFACTS (default: app/docs/refactor_acceptance/reco_compare/)
  run.json                    brief / effective query / needs / per-column data /
                              actual usage / price-based actual cost / pre-run plan
  columns/<column>.json       recall order, first five recalled, candidate order
                              sent to review, review result JSON + reason text
  report.html                 side-by-side top5 BEFORE review vs top5 AFTER review,
                              with local image files (relative paths; --copy-images
                              copies them into report/images/ for a portable report)
  images/                     only with --copy-images

--dry-run calls NO model and writes NO file: it reads the local DB read-only,
rebuilds the exact prompts/token counts it can know offline and prints
requests / tokens / RMB with the published price sources.

PRICE SOURCES (checked 2026-11-05)
  * qwen3.7-flash, 华北2（北京）, input<=32k: 0.2 RMB / M input tokens,
    0.8 RMB / M output tokens.
    https://help.aliyun.com/zh/model-studio/qwen3-7-flash
  * qwen3.7-text-embedding-flash, text input: 0.125 RMB / M tokens.
    https://www.qianwenai.com/models/qwen3.7-text-embedding-flash
  * Image token rule (Qwen3-VL: one vision token per 32x32 px, image_tokens =
    h_bar*w_bar/1024 + 2); third-party write-up of the platform resize logic,
    the API response's usage is the billing authority.
    https://www.cnblogs.com/rolandhe/p/19329636

The exact qwen3.7-flash price above IS published, so the fallback "use the
closest published qwen3-VL-flash price" rule from the task is not triggered.

USAGE
  # estimate only (no network, no file writes)
  python scripts/reco_compare.py --brief "复古建筑展板" --dry-run
  # the one real run (do not repeat casually; it costs a few RMB 0.001)
  python scripts/reco_compare.py --brief "复古建筑展板"
"""
from __future__ import annotations

import argparse
import html
import json
import math
import os
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

BACKEND_DIR = Path(__file__).resolve().parents[1]
APP_ROOT = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app import config, creative, vision  # noqa: E402
from app.embedding import dimensions  # noqa: E402
from app.embedding.client import EmbeddingClient, EmbeddingSettings  # noqa: E402
from app.embedding.search_application import readonly_connection  # noqa: E402
from app.recommendation_query import resolve_brief  # noqa: E402

DEFAULT_BRIEF = "复古建筑展板"
DEFAULT_OUT = APP_ROOT / "docs" / "refactor_acceptance" / "reco_compare"

# Published prices, checked 2026-11-05. Do not edit without a fresh source URL.
PRICES = {
    "checked_at": "2026-11-05",
    "region": "阿里云百炼 华北2（北京）",
    "vision": {
        "model": "qwen3.7-flash",
        "tier": "输入<=32k tokens",
        "input_rmb_per_million": 0.2,
        "output_rmb_per_million": 0.8,
        "source": "https://help.aliyun.com/zh/model-studio/qwen3-7-flash",
        "note": "原价；不含限时优惠。图片 token 计入输入 token（响应 usage 中 image_tokens 含在 input/prompt_tokens 内）。",
    },
    "embedding": {
        "model": "qwen3.7-text-embedding-flash",
        "unit": "文本输入（同步 /embeddings）",
        "input_rmb_per_million": 0.125,
        "source": "https://www.qianwenai.com/models/qwen3.7-text-embedding-flash",
    },
    "image_tokens": {
        "rule": "Qwen3-VL 每个视觉 token 对应 32x32 像素：image_tokens = h_bar*w_bar/1024 + 2，"
                "h_bar/w_bar 是把图缩到 max_side 后按 32 取整（另受 max_pixels/min_pixels 约束）。",
        "patch_px": 32,
        "formula": "h_bar*w_bar/(32*32) + 2",
        "source": "https://www.cnblogs.com/rolandhe/p/19329636",
        "authority": "第三方转述平台的图像缩放逻辑；实际计费以 API 响应 usage 为准。",
    },
}

# Measured on this repository's 131 done analyses (768 long side, 32px patches).
# Only used when the local DB cannot be read.
REFERENCE_IMAGE_TOKENS = {
    "count": 131,
    "min": 242,
    "median": 410,
    "max": 578,
    "source": "本仓库 131 张已分析图在 REVIEW_MAX_SIDE=768 下的估算分布",
}

# Constants used by the estimate (documented, not measured claims).
NEEDS_OUTPUT_SAMPLE = "分别为排版、配色、风格写一句短需求"
REVIEW_REFERENCE_SAMPLE = "参考它的多栏网格、留白比例与顶部标题层级和图文关系"  # ~30 chars, one pick
COMPLETION_UPPER_FACTOR = 2.0


# ---------------------------------------------------------------------------
# token / cost estimation (offline, deterministic)
# ---------------------------------------------------------------------------
def _resized_size(width: int, height: int, max_side: int = creative.REVIEW_MAX_SIDE) -> tuple[int, int]:
    """Long side <= max_side, aspect kept: what vision.image_data_uri() sends."""
    if not width or not height or width <= 0 or height <= 0:
        return 0, 0
    scale = min(1.0, max_side / max(width, height))
    return max(1, int(round(width * scale))), max(1, int(round(height * scale)))


def estimate_image_tokens(width: int, height: int, max_side: int = creative.REVIEW_MAX_SIDE,
                          patch: int = 32, max_pixels: int = 655_360,
                          min_pixels: int = 4 * 32 * 32) -> int:
    """Qwen3-VL image token estimate for one review image (32x32 px per token)."""
    width, height = _resized_size(width, height, max_side)
    if not width or not height:
        return 0
    w_bar = int(round(width / patch)) * patch
    h_bar = int(round(height / patch)) * patch
    w_bar, h_bar = max(w_bar, patch), max(h_bar, patch)
    if w_bar * h_bar > max_pixels:  # documented resize branch; not hit at 768px
        beta = math.sqrt(max_pixels / (width * height))
        w_bar = int(math.floor(width / beta / patch)) * patch
        h_bar = int(math.floor(height / beta / patch)) * patch
    elif w_bar * h_bar < min_pixels:
        beta = math.sqrt(min_pixels / (width * height))
        w_bar = int(math.ceil(width * beta / patch)) * patch
        h_bar = int(math.ceil(height * beta / patch)) * patch
    return max(1, (w_bar * h_bar) // (patch * patch)) + 2


_ASCII_PUNCT = set('{}[]",:;=_-/()<>')


def estimate_text_tokens(text: str | None) -> int:
    """Heuristic tokenizer for the estimate only (+/-20%).

    Qwen counts ~1 token per CJK / non-ASCII character; ASCII punctuation and
    JSON syntax ~1 token; other ASCII ~3.5 characters per token.
    """
    if not text:
        return 0
    non_ascii = sum(1 for ch in text if ord(ch) > 127)
    ascii_chars = [ch for ch in text if ord(ch) <= 127]
    punct = sum(1 for ch in ascii_chars if ch in _ASCII_PUNCT or ch == "\n")
    other = len(ascii_chars) - punct
    return non_ascii + punct + math.ceil(other / 3.5)


def _needs_completion_tokens() -> int:
    sample = json.dumps({"layout_need": NEEDS_OUTPUT_SAMPLE,
                         "color_need": NEEDS_OUTPUT_SAMPLE,
                         "style_need": NEEDS_OUTPUT_SAMPLE}, ensure_ascii=False)
    return estimate_text_tokens(sample)


def _review_completion_tokens(k: int = creative.PICKS_PER_COLUMN) -> int:
    sample = json.dumps({"picks": [
        {"image_id": 100 + i, "rank": i, "useful": True, "reference_point": REVIEW_REFERENCE_SAMPLE}
        for i in range(1, k + 1)]}, ensure_ascii=False)
    return estimate_text_tokens(sample)


def _rmb(vision_input: float, vision_output: float, embedding_input: float) -> float:
    v, e = PRICES["vision"], PRICES["embedding"]
    return (vision_input * v["input_rmb_per_million"]
            + vision_output * v["output_rmb_per_million"]
            + embedding_input * e["input_rmb_per_million"]) / 1_000_000


def _token_summary(tokens: list[int]) -> dict:
    if not tokens:
        return dict(REFERENCE_IMAGE_TOKENS, note="本地库无可用样本，用本仓库 131 图实测分布")
    return {"count": len(tokens), "min": min(tokens),
            "median": int(statistics.median(tokens)), "max": max(tokens)}


# ---------------------------------------------------------------------------
# data dir / library snapshot (read-only)
# ---------------------------------------------------------------------------
def resolve_data_dir(explicit: str | None = None) -> tuple[Path, str]:
    """Explicit flag > WOMINMIN_DATA_DIR > app/data > <copy>/data (run-backend.ps1 layout)."""
    if explicit:
        return Path(explicit), "命令行 --data-dir"
    env = os.environ.get("WOMINMIN_DATA_DIR")
    if env:
        return Path(env), "环境变量 WOMINMIN_DATA_DIR"
    default = config.data_dir()
    if (default / "app.db").exists():
        return default, "配置默认 data 目录"
    sibling = APP_ROOT.parent / "data"
    if (sibling / "app.db").exists():
        return sibling, "run-backend.ps1 布局：<副本根>/data（只读打开）"
    return default, "配置默认 data 目录（数据库不存在，估算回退到实测分布）"


def _library_snapshot(data_dir: Path, settings: EmbeddingSettings) -> dict:
    db = Path(data_dir) / "app.db"
    snapshot: dict = {"path": str(db), "exists": db.exists()}
    if not snapshot["exists"]:
        snapshot["note"] = "数据库不存在：估算使用本仓库 131 张已分析图的实测 token 分布"
        return snapshot
    try:
        with readonly_connection(db) as conn:
            snapshot["analyses_done"] = conn.execute(
                "SELECT COUNT(*) FROM analyses WHERE status='done'").fetchone()[0]
            dims = {row["id"]: (row["width"], row["height"])
                    for row in conn.execute("SELECT id, width, height FROM images")}
            schemes: dict[str, dict] = {}
            for scheme in ("layout_v1", "style_v1", "description_v1"):
                rows = dimensions.load_rows(conn, dimensions.spec_for(settings, scheme))
                tokens = [estimate_image_tokens(*dims[r.image_id])
                          for r in rows if r.image_id in dims and all(dims[r.image_id])]
                schemes[scheme] = {"valid_rows": len(rows), "image_tokens": _token_summary(tokens)}
            snapshot["schemes"] = schemes
            snapshot["colors_images"] = conn.execute(
                "SELECT COUNT(DISTINCT image_id) FROM colors").fetchone()[0]
    except Exception as exc:  # corrupt / older schema: the estimate falls back
        snapshot["error"] = vision.redact(str(exc))
    return snapshot


def _settings_or_default() -> tuple[EmbeddingSettings, str | None]:
    try:
        return EmbeddingSettings.from_env(), None
    except Exception as exc:
        return EmbeddingSettings(base_url="", api_key=""), vision.redact(str(exc))


def estimate_run(brief: str, previous: str | None, data_dir: Path) -> dict:
    """Offline plan: requests / tokens / RMB. Calls no model and writes nothing."""
    final, effective = resolve_brief(brief, previous)
    needs = creative._fallback_needs(final)
    colors = creative._color_terms(final)
    settings, settings_error = _settings_or_default()
    library = _library_snapshot(data_dir, settings)

    need_texts = [needs["layout_need"] or effective,
                  needs["style_need"] or effective,
                  needs["color_need"] or effective]
    need_prompt = creative.NEED_PROMPT.format(brief=effective)
    needs_in = estimate_text_tokens(need_prompt)
    needs_out = _needs_completion_tokens()
    embedding_in = sum(estimate_text_tokens(text) for text in need_texts)

    columns_plan: dict[str, dict] = {}
    expected_in, expected_out = needs_in, needs_out
    ceiling_in, ceiling_out = 3 * needs_in, 3 * needs_out
    need_keys = {"layout": "layout_need", "palette": "color_need", "style": "style_need"}
    for purpose, meta in creative.COLUMNS.items():
        if purpose == "palette" and colors:
            scheme, eligible = "Pillow 颜色占比", int(library.get("colors_images") or 0)
            summary = _token_summary([])
        else:
            scheme = meta["scheme"]
            info = (library.get("schemes") or {}).get(scheme) or {}
            eligible = int(info.get("valid_rows") or 0)
            summary = info.get("image_tokens") or _token_summary([])
        n = min(creative.REVIEW_CANDIDATES, eligible) if eligible else creative.REVIEW_CANDIDATES
        review_brief = (needs.get(need_keys[purpose]) or final.text).replace("\n", "；")
        prompt = creative.REVIEW_PROMPT.format(
            brief=review_brief, goal=meta["goal"], label=meta["label"], n=n,
            k=creative.PICKS_PER_COLUMN, video_note="")
        prompt_tokens = estimate_text_tokens(prompt)
        marker_tokens = sum(estimate_text_tokens(f"image_id={100 + i}") for i in range(n))
        median_img = int(summary.get("median") or REFERENCE_IMAGE_TOKENS["median"])
        max_img = int(summary.get("max") or REFERENCE_IMAGE_TOKENS["max"])
        col_in = prompt_tokens + marker_tokens + n * median_img
        col_in_max = prompt_tokens + marker_tokens + n * max_img
        col_out = _review_completion_tokens(creative.PICKS_PER_COLUMN)
        columns_plan[purpose] = {
            "label": meta["label"],
            "scheme": scheme,
            "eligible_images": eligible,
            "review_candidates": n,
            "image_tokens": summary,
            "prompt_text_tokens": prompt_tokens,
            "expected": {"input_tokens": col_in, "output_tokens": col_out},
            "upper_bound": {"input_tokens": col_in_max,
                            "output_tokens": int(col_out * COMPLETION_UPPER_FACTOR)},
        }
        expected_in += col_in
        expected_out += col_out
        ceiling_in += 3 * col_in_max
        ceiling_out += 3 * int(col_out * COMPLETION_UPPER_FACTOR)

    expected_cost = _rmb(expected_in, expected_out, embedding_in)
    ceiling_cost = _rmb(ceiling_in, ceiling_out, embedding_in)
    requests = {
        "expected": {"needs": 1, "embedding": 1, "review_per_column": 1, "review_total": 3, "total": 5},
        "worst_case": {"needs": 3, "embedding": 1, "review_per_column": 3, "review_total": 9, "total": 13},
        "note": "embedding 客户端一次批量、不自动重试；视觉/文本请求单逻辑调用最多 3 次真实请求"
                "（vision.MAX_REQUESTS=3，含 JSON 不合法重发、response_format 兼容重发、网络重试）。",
    }
    return {
        "dry_run": True,
        "generated_at": _iso_now(),
        "brief": brief,
        "previous_brief": previous,
        "effective_query": effective,
        "needs_fallback": needs,
        "need_texts": need_texts,
        "brief_parsed": {"topics": list(final.topics), "layout": [list(x) for x in final.layout],
                         "styles": list(final.styles), "colors": list(final.colors),
                         "artifacts": list(final.artifacts)},
        "config": _config_info(settings_error),
        "data_dir": str(data_dir),
        "data_dir_source": "见命令输出 / run.json",
        "library": library,
        "columns": columns_plan,
        "requests": requests,
        "tokens": {
            "expected": {"vision_input": expected_in, "vision_output": expected_out,
                         "embedding_input": embedding_in},
            "upper_bound": {"vision_input": ceiling_in, "vision_output": ceiling_out,
                            "embedding_input": embedding_in},
            "method": "文本 token 为启发式（非 ASCII≈1/字，ASCII 标点≈1，其余≈3.5 字符/token，±20%）；"
                      "图像 token 按 Qwen3-VL 32x32px 规则、REVIEW_MAX_SIDE=768 计算；"
                      "输出按 5 张 picks 每张一句参考点估算，上限 2 倍。",
        },
        "cost_rmb": {
            "expected": round(expected_cost, 6),
            "expected_fen": round(expected_cost * 100, 4),
            "budget_ceiling": round(ceiling_cost, 6),
            "budget_ceiling_fen": round(ceiling_cost * 100, 4),
            "formula": "vision_in/1e6*0.2 + vision_out/1e6*0.8 + embedding_in/1e6*0.125",
        },
        "prices": PRICES,
        "assumptions": [
            "needs 文本按本地 fallback 估算；真实运行时由一次文本请求生成，长度相近。",
            "每栏送审 ≤ REVIEW_CANDIDATES=8 张；实际数量由召回、SHA 去重、同帖分散和排除词决定。",
            "图片 token 用 Qwen3-VL 32x32px 规则估算，实际以响应 usage 为准（本仓库 131 图 median 410 / max 578）。",
            "worst_case 假设每栏视觉请求重发到 MAX_REQUESTS=3 次且输出翻倍，是预算上限，不是预期值。",
            "价格为本日公开原价，不含限时优惠；本次未使用 Batch 接口，因此按同步推理价。",
        ],
        "network_calls": 0,
    }


def _iso_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime())


def _config_info(settings_error: str | None = None) -> dict:
    try:
        ai = config.ai_settings()
    except Exception:
        ai = {}
    base_url = ai.get("base_url") or ""
    return {
        "base_url": base_url,
        "base_url_host": urlparse(base_url).hostname or "",
        "vision_model": ai.get("vision_model"),
        "vision_max_side_env": ai.get("vision_max_side"),
        "enable_thinking": ai.get("enable_thinking"),
        "embedding_model": ai.get("embedding_model"),
        "embedding_dimensions": ai.get("embedding_dimensions"),
        "review_max_side": creative.REVIEW_MAX_SIDE,
        "recall_top": creative.RECALL_TOP,
        "review_candidates": creative.REVIEW_CANDIDATES,
        "picks_per_column": creative.PICKS_PER_COLUMN,
        "api_key": "<redacted>",
        "settings_error": settings_error,
    }


# ---------------------------------------------------------------------------
# one-run capture
# ---------------------------------------------------------------------------
@dataclass
class Captures:
    recalls: dict[str, list[tuple[int, float]]] = field(default_factory=dict)
    columns: dict[str, dict] = field(default_factory=dict)
    embedding_calls: list[dict] = field(default_factory=list)
    patch_errors: list[str] = field(default_factory=list)


class _RecordingClient:
    """Transparent wrapper: counts the embedding batch and keeps the query texts."""

    def __init__(self, client, sink: list[dict]):
        self._client, self._sink = client, sink

    def __enter__(self):
        enter = getattr(self._client, "__enter__", None)
        if enter is not None:
            enter()
        return self

    def __exit__(self, *exc):
        exit_ = getattr(self._client, "__exit__", None)
        if exit_ is not None:
            return exit_(*exc)
        close = getattr(self._client, "close", None)
        if close is not None:
            close()
        return False

    def __getattr__(self, name):
        return getattr(self._client, name)

    def embed(self, texts):
        self._sink.append({"texts": list(texts)})
        return self._client.embed(texts)


def _candidate_public(candidate: creative.Candidate, order: int) -> dict:
    return {
        "order": order,
        "image_id": candidate.image_id,
        "recall_rank": candidate.recall_rank,
        "semantic_score": candidate.score,
        "post_ids": list(candidate.post_ids),
        "is_video": candidate.is_video,
        "file_path": candidate.file_path,
        "sha256": candidate.sha256,
    }


def _prompt_for(column: str, brief_text: str, candidates: list) -> str:
    meta = creative.COLUMNS[column]
    videos = [c.image_id for c in candidates if c.is_video]
    video_note = (f"- 编号 {', '.join(map(str, videos))} 来自视频笔记，只是封面图，只能按封面判断。"
                  if videos else "")
    return creative.REVIEW_PROMPT.format(
        brief=brief_text, goal=meta["goal"], label=meta["label"],
        n=len(candidates), k=creative.PICKS_PER_COLUMN, video_note=video_note)


def recommend_once(*, brief: str, previous: str | None, data_dir: Path,
                   settings_provider=None, client_factory=None, reviewer=None,
                   needs_parser=None, vision_settings=None) -> tuple[dict, Captures]:
    """Exactly one CreativeApplication.recommend() call with in-process capture."""
    captures = Captures()
    original_dimension_recall = creative.dimension_recall
    original_palette_recall = creative.palette_recall
    real_reviewer = reviewer or creative.review_column

    def dimension_capture(conn, settings, scheme, query_vector, eligible=None):
        result = original_dimension_recall(conn, settings, scheme, query_vector, eligible=eligible)
        captures.recalls[scheme] = list(result)
        return result

    def palette_capture(conn, colors, prominent=False, eligible=None):
        result = original_palette_recall(conn, colors, prominent, eligible=eligible)
        captures.recalls["palette_colors"] = list(result)
        return result

    def reviewer_capture(column, brief_text, candidates, vsettings, images_dir):
        record = {
            "column": column,
            "brief_text": brief_text,
            "candidate_ids": [c.image_id for c in candidates],
            "candidates": [_candidate_public(c, i) for i, c in enumerate(candidates, 1)],
            "prompt": _prompt_for(column, brief_text, candidates),
            "result": None,
            "started_at": _iso_now(),
        }
        captures.columns[column] = record
        try:
            result = real_reviewer(column, brief_text, candidates, vsettings, images_dir)
        except Exception as exc:  # keep the record, then let recommend() degrade as designed
            record["exception"] = vision.redact(str(exc))
            raise
        record["result"] = result
        record["finished_at"] = _iso_now()
        return result

    def factory_capture(settings):
        real = client_factory or EmbeddingClient
        return _RecordingClient(real(settings), captures.embedding_calls)

    creative.dimension_recall = dimension_capture
    creative.palette_recall = palette_capture
    try:
        application = creative.CreativeApplication(
            settings_provider=settings_provider or EmbeddingSettings.from_env,
            client_factory=factory_capture,
            database=lambda: Path(data_dir) / "app.db",
            vision_settings=vision_settings or config.ai_settings,
            reviewer=reviewer_capture,
            needs_parser=needs_parser or creative.decompose_needs,
        )
        result = application.recommend(brief, previous)
    finally:
        creative.dimension_recall = original_dimension_recall
        creative.palette_recall = original_palette_recall
    return result, captures


# ---------------------------------------------------------------------------
# hydrate + artifacts
# ---------------------------------------------------------------------------
def hydrate_images(data_dir: Path, ids: list[int]) -> dict[int, dict]:
    if not ids:
        return {}
    out: dict[int, dict] = {}
    marks = ",".join("?" * len(ids))
    with readonly_connection(Path(data_dir) / "app.db") as conn:
        for row in conn.execute(
                f"SELECT id, file_path, width, height, original_name FROM images WHERE id IN ({marks})", ids):
            out[row["id"]] = {"file_path": row["file_path"], "width": row["width"],
                              "height": row["height"], "original_name": row["original_name"],
                              "subject": [], "description": ""}
        for row in conn.execute(
                f"SELECT image_id, subject, description FROM analyses "
                f"WHERE status='done' AND image_id IN ({marks})", ids):
            info = out.get(row["image_id"])
            if info is None:
                continue
            try:
                subject = json.loads(row["subject"]) if row["subject"] else []
            except (TypeError, ValueError):
                subject = []
            info["subject"] = [s for s in subject if isinstance(s, str)] if isinstance(subject, list) else []
            info["description"] = row["description"] or ""
    return out


def _abs_image_path(data_dir: Path, file_path: str | None) -> Path | None:
    if not file_path:
        return None
    path = Path(file_path)
    return path if path.is_absolute() else Path(data_dir) / path


def _href_for(data_dir: Path, out_dir: Path, file_path: str | None) -> str | None:
    path = _abs_image_path(data_dir, file_path)
    if path is None:
        return None
    try:
        return Path(os.path.relpath(path, out_dir)).as_posix()
    except ValueError:  # different drive on Windows
        return path.as_uri()


def _card(image_id: int, images: dict[int, dict], data_dir: Path, out_dir: Path, **extra) -> dict:
    info = images.get(image_id) or {}
    path = _abs_image_path(data_dir, info.get("file_path"))
    return {
        "image_id": image_id,
        "subject": info.get("subject") or [],
        "description": info.get("description") or "",
        "file_path": info.get("file_path"),
        "image_href": _href_for(data_dir, out_dir, info.get("file_path")),
        "image_exists": bool(path and path.exists()),
        **extra,
    }


def _pick_card(pick: dict, images: dict[int, dict], data_dir: Path, out_dir: Path) -> dict:
    return _card(pick["image_id"], images, data_dir, out_dir,
                 rank=pick.get("rank"), recall_rank=pick.get("recall_rank"),
                 semantic_score=pick.get("semantic_score"),
                 reference_point=pick.get("reference_point"),
                 reason=pick.get("reason"),
                 unreviewed=pick.get("unreviewed", False))


def assemble_artifacts(result: dict, captures: Captures, *, brief: str, previous: str | None,
                       data_dir: Path, out_dir: Path, images: dict[int, dict],
                       plan: dict | None = None) -> dict:
    """Pure assembly of the JSON report (testable; no network, no DB)."""
    columns = []
    ids = set()
    for col in result.get("columns") or []:
        purpose = col["purpose"]
        source = col.get("recall_source")
        recalled = list(captures.recalls.get(source) or [])
        recall_order = [{"recall_rank": i, "image_id": image_id, "semantic_score": score}
                        for i, (image_id, score) in enumerate(recalled, 1)]
        capture = captures.columns.get(purpose) or {}
        sent = list(capture.get("candidates") or [])
        picks = []
        for rank, item in enumerate(col.get("items") or [], 1):
            purposes = item.get("purposes") or []
            picks.append({
                "rank": rank,
                "image_id": item.get("image_id"),
                "recall_rank": item.get("recall_rank"),
                "semantic_score": item.get("semantic_score"),
                "reference_point": item.get("reference_point"),
                "reason": (purposes[0].get("reason") if purposes else None) or item.get("reference_point"),
                "unreviewed": bool(item.get("unreviewed")),
                "media_scope": item.get("media_scope"),
                "post_ids": item.get("post_ids") or [],
            })
        picked_ids = {p["image_id"] for p in picks}
        candidates = []
        for candidate in sent:
            entry = dict(candidate)
            entry["picked"] = entry["image_id"] in picked_ids
            candidates.append(entry)
        before = [_card(c["image_id"], images, data_dir, out_dir,
                        recall_rank=c.get("recall_rank"), semantic_score=c.get("semantic_score"))
                  for c in candidates[:5]]
        after = [_pick_card(p, images, data_dir, out_dir) for p in picks[:5]]
        ids.update(p["image_id"] for p in picks)
        ids.update(c["image_id"] for c in candidates)
        review = capture.get("result") or {}
        columns.append({
            "purpose": purpose,
            "label": col.get("label"),
            "status": col.get("status"),
            "error": col.get("error"),
            "recall_source": source,
            "recall_order": recall_order,
            "recall_first_five": recall_order[:5],
            "candidates_sent_to_review": candidates,
            "before_first_five": before,
            "reviewed_picks": picks,
            "after_first_five": after,
            "squeezed_out_ids": [c["image_id"] for c in candidates if not c["picked"]],
            "review_prompt": capture.get("prompt"),
            "review_result": {
                "status": (review.get("status") if review else col.get("status")),
                "error": review.get("error") if review else col.get("error"),
                "picks": [{"image_id": p["image_id"], "reference_point": p["reference_point"]}
                          for p in picks],
                "usage": review.get("usage") if review else col.get("usage"),
            },
            "usage": col.get("usage"),
        })

    images_by_id = {block["image_id"]: {"subject": block["subject"], "description": block["description"],
                                        "file_path": block["file_path"]}
                    for block in (_all_cards(columns))}
    usage, actual_cost = _actual_usage(result, captures)
    return {
        "generated_at": _iso_now(),
        "brief": brief,
        "previous_brief": previous,
        "effective_query": result.get("effective_query"),
        "needs": result.get("needs"),
        "needs_usage": result.get("needs_usage"),
        "status": result.get("status"),
        "notices": result.get("notices") or [],
        "config": _config_info(),
        "git": _git_info(),
        "data_dir": str(data_dir),
        "out_dir": str(out_dir),
        "database": str(Path(data_dir) / "app.db"),
        "plan": plan or {},
        "columns": columns,
        "images": images_by_id,
        "requests_and_usage": usage,
        "cost_actual_rmb": actual_cost,
        "human_checklist": [
            "复看后（B）是否出现主题不同但排版/配色/风格可借鉴的图？",
            "复看前（A）里明显值得参考的图是否在复看后被挤掉？被挤掉的 id 见 squeezed_out_ids。",
            "复看返回少于 5 张时，是图确实不够，还是模型漏选/失败（看 status / error / review_result）。",
            "每张复看选中图的 reference_point 是否具体、可核对（不是泛泛而谈）。",
        ],
    }


def _all_cards(columns: list[dict]):
    for col in columns:
        for card in col["before_first_five"] + col["after_first_five"]:
            yield card


def _actual_usage(result: dict, captures: Captures) -> tuple[dict, dict]:
    needs_usage = result.get("needs_usage") or {}
    review_requests = 0
    prompt_tokens = int(needs_usage.get("prompt_tokens") or 0)
    completion_tokens = int(needs_usage.get("completion_tokens") or 0)
    for record in captures.columns.values():
        usage = (record.get("result") or {}).get("usage") or {}
        review_requests += int(usage.get("requests") or 0)
        prompt_tokens += int(usage.get("prompt_tokens") or 0)
        completion_tokens += int(usage.get("completion_tokens") or 0)
    embedding_tokens = sum(estimate_text_tokens(text)
                           for call in captures.embedding_calls for text in call.get("texts") or [])
    needs_requests = int(needs_usage.get("requests") or 0)
    usage = {
        "needs_requests": needs_requests,
        "embedding_requests": len(captures.embedding_calls),
        "review_requests": review_requests,
        "total_requests": needs_requests + len(captures.embedding_calls) + review_requests,
        "prompt_tokens_reported": prompt_tokens or None,
        "completion_tokens_reported": completion_tokens or None,
        "embedding_input_tokens_estimated": embedding_tokens,
        "needs_usage": needs_usage,
        "per_column": {c: (record.get("result") or {}).get("usage")
                       for c, record in captures.columns.items()},
        "note": "prompt/completion 来自接口响应 usage（若接口未返回则为 None）；"
                "图片 token 含在 prompt_tokens 内；embedding 响应不含 token 数，按文本启发式估算。",
    }
    cost = {
        "vision_input_rmb": round(prompt_tokens * PRICES["vision"]["input_rmb_per_million"] / 1_000_000, 6),
        "vision_output_rmb": round(completion_tokens * PRICES["vision"]["output_rmb_per_million"] / 1_000_000, 6),
        "embedding_input_rmb": round(embedding_tokens * PRICES["embedding"]["input_rmb_per_million"] / 1_000_000, 6),
    }
    cost["total_rmb"] = round(sum(value for key, value in cost.items() if key.endswith("_rmb")
                                 and key != "total_rmb"), 6)
    cost["total_fen"] = round(cost["total_rmb"] * 100, 4)
    cost["formula"] = "prompt_tokens/1e6*0.2 + completion_tokens/1e6*0.8 + embedding_tokens/1e6*0.125"
    cost["prices"] = {"vision_input": PRICES["vision"]["input_rmb_per_million"],
                      "vision_output": PRICES["vision"]["output_rmb_per_million"],
                      "embedding": PRICES["embedding"]["input_rmb_per_million"],
                      "source_vision": PRICES["vision"]["source"],
                      "source_embedding": PRICES["embedding"]["source"]}
    return usage, cost


def _git_info() -> dict:
    def run(*args):
        try:
            return subprocess.run(["git", *args], cwd=str(APP_ROOT), capture_output=True,
                                  text=True, timeout=10).stdout.strip()
        except Exception:
            return ""
    return {"branch": run("rev-parse", "--abbrev-ref", "HEAD"), "commit": run("rev-parse", "--short", "HEAD")}


def materialize_images(run: dict, data_dir: Path, out_dir: Path, copy: bool) -> None:
    """Optionally copy referenced images into <out>/images and rewrite hrefs."""
    if not copy:
        return
    target = Path(out_dir) / "images"
    target.mkdir(parents=True, exist_ok=True)
    mapping: dict[int, str] = {}
    for card in _all_cards(run["columns"]):
        image_id = card["image_id"]
        if image_id in mapping:
            card["image_href"] = mapping[image_id]
            card["image_exists"] = True
            continue
        path = _abs_image_path(data_dir, card.get("file_path"))
        if path is None or not path.exists():
            continue
        name = f"{image_id}_{path.name}"
        (target / name).write_bytes(path.read_bytes())
        mapping[image_id] = "images/" + name
        card["image_href"] = mapping[image_id]
        card["image_exists"] = True


# ---------------------------------------------------------------------------
# HTML report
# ---------------------------------------------------------------------------
def _html_card(card: dict | None, *, kind: str) -> str:
    if not card:
        return '<div class="card placeholder">—</div>'
    image_id = card["image_id"]
    href = card.get("image_href")
    if href and card.get("image_exists", True):
        img = f'<img loading="lazy" src="{html.escape(href)}" alt="image {image_id}">'
    else:
        img = '<div class="noimg">本地图片缺失</div>'
    subject = "、".join(card.get("subject") or []) or "—"
    meta = [f"#{image_id}"]
    if card.get("recall_rank") is not None:
        meta.append(f"召回#{card['recall_rank']}")
    if card.get("semantic_score") is not None:
        meta.append(f"score {float(card['semantic_score']):.4f}")
    if kind == "after":
        bottom = f'<p class="reason"><span class="tag">rank {card.get("rank")}</span> ' \
                 f'{html.escape(card.get("reference_point") or "")}</p>'
    else:
        bottom = f'<p class="reason">主题：{html.escape(subject)}</p>'
    return (f'<div class="card {kind}">{img}'
            f'<div class="meta">{" · ".join(html.escape(str(x)) for x in meta)}</div>'
            f'{bottom}</div>')


def _html_rows(column: dict) -> str:
    before = column.get("before_first_five") or []
    after = column.get("after_first_five") or []
    rows = []
    for i in range(5):
        b = before[i] if i < len(before) else None
        a = after[i] if i < len(after) else None
        rows.append(f'<tr><td class="side before">{_html_card(b, kind="before")}</td>'
                    f'<td class="side after">{_html_card(a, kind="after")}</td></tr>')
    return "\n".join(rows)


def _html_column(column: dict) -> str:
    purpose = column.get("purpose") or ""
    status = column.get("status")
    status_class = "ok" if status == "ok" else "warn"
    recall = column.get("recall_order") or []
    recall_list = "".join(
        f'<li>#{item["image_id"]} · score {float(item["semantic_score"]):.4f}</li>'
        for item in (column.get("recall_first_five") or []))
    candidate_rows = []
    for c in column.get("candidates_sent_to_review") or []:
        picked = "✅" if c.get("picked") else ""
        video = "（视频封面）" if c.get("is_video") else ""
        candidate_rows.append(
            f'<tr class="{ "picked" if c.get("picked") else ""}">'
            f'<td>{c.get("order")}</td><td>#{c["image_id"]}</td>'
            f'<td>{c.get("recall_rank")}</td><td>{float(c.get("semantic_score") or 0):.4f}</td>'
            f'<td>{html.escape(",".join(str(p) for p in (c.get("post_ids") or [])))}</td>'
            f'<td>{picked}{video}</td></tr>')
    review_json = json.dumps(column.get("review_result") or {}, ensure_ascii=False, indent=2)
    prompt = html.escape(column.get("review_prompt") or "")
    squeezed = column.get("squeezed_out_ids") or []
    return f"""
<section class="column" id="column-{html.escape(purpose)}">
  <h2>{html.escape(column.get("label") or purpose)}
      <span class="tag">{html.escape(purpose)} · {html.escape(column.get("recall_source") or "")}</span>
      <span class="status {status_class}">复看 {html.escape(str(status))}</span></h2>
  <p class="notice">召回 {len(recall)} 张（Top{creative.RECALL_TOP}）→ 送审 {len(column.get("candidates_sent_to_review") or [])} 张 →
     复看选中 {len(column.get("reviewed_picks") or [])} 张；未被选中的送审图 id：{html.escape(str(squeezed))}。</p>
  <div class="pair">
    <div class="pair-head"><span>复看前 · 候选前五（召回/去重后的送审顺序）</span>
                            <span>复看后 · 复看选中（模型排序）</span></div>
    <table class="compare"><tbody>
{_html_rows(column)}
    </tbody></table>
  </div>
  <div class="extra">
    <div>
      <h3>复看前 · 原始语义召回前五（未去重、未复看）</h3>
      <ol class="recall">{recall_list}</ol>
    </div>
    <div>
      <h3>送审候选顺序与结果（✅ = 复看选中）</h3>
      <table class="candidates">
        <thead><tr><th>送审顺序</th><th>image_id</th><th>召回排名</th><th>score</th><th>post</th><th>结果</th></tr></thead>
        <tbody>{''.join(candidate_rows)}</tbody>
      </table>
    </div>
  </div>
  <details><summary>复看结果 / reason JSON</summary><pre>{html.escape(review_json)}</pre></details>
  <details><summary>复看请求 prompt</summary><pre>{prompt}</pre></details>
</section>"""


def render_report_html(run: dict) -> str:
    """Side-by-side top5 before-vs-after, one section per column (pure function)."""
    columns_html = "\n".join(_html_column(c) for c in run.get("columns") or [])
    needs = json.dumps(run.get("needs") or {}, ensure_ascii=False, indent=2)
    usage = json.dumps(run.get("requests_and_usage") or {}, ensure_ascii=False, indent=2)
    cost = json.dumps(run.get("cost_actual_rmb") or {}, ensure_ascii=False, indent=2)
    checklist = "".join(f"<li>{html.escape(item)}</li>" for item in run.get("human_checklist") or [])
    notices = run.get("notices") or []
    notice_html = ("<p class=\"notice warn\">" + html.escape("；".join(notices)) + "</p>") if notices else ""
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>推荐两版对比 · {html.escape(str(run.get("brief")))}</title>
<style>
  body {{ font-family: "Microsoft YaHei", system-ui, sans-serif; margin: 24px; color: #222; background: #fafafa; }}
  h1 {{ font-size: 22px; margin-bottom: 4px; }}
  h2 {{ font-size: 18px; border-bottom: 2px solid #ddd; padding-bottom: 6px; margin-top: 36px; }}
  h3 {{ font-size: 14px; margin: 8px 0; }}
  .tag {{ font-size: 12px; background: #eef; color: #335; border-radius: 4px; padding: 1px 6px; }}
  .status.ok {{ color: #176c2e; }} .status.warn {{ color: #a33; }}
  .notice {{ color: #555; }}
  .notice.warn {{ color: #a33; }}
  .pair-head {{ display: flex; gap: 16px; font-weight: 600; margin: 8px 0; }}
  .pair-head span {{ flex: 1; }}
  table.compare {{ width: 100%; border-collapse: separate; border-spacing: 8px; }}
  td.side {{ width: 50%; vertical-align: top; }}
  .card {{ background: #fff; border: 1px solid #ddd; border-radius: 8px; padding: 8px; }}
  .card img {{ width: 100%; max-height: 300px; object-fit: contain; background: #f0f0f0; }}
  .card .meta {{ font-size: 12px; color: #555; margin: 4px 0; }}
  .card .reason {{ font-size: 13px; margin: 2px 0 0; }}
  .card.placeholder, .card .noimg {{ color: #999; text-align: center; padding: 40px 0; }}
  .extra {{ display: flex; gap: 24px; margin-top: 12px; }}
  .extra > div {{ flex: 1; }}
  table.candidates {{ border-collapse: collapse; font-size: 12px; width: 100%; }}
  table.candidates th, table.candidates td {{ border: 1px solid #ddd; padding: 3px 6px; text-align: left; }}
  tr.picked {{ background: #e8f6e8; }}
  pre {{ white-space: pre-wrap; background: #f4f4f4; padding: 8px; border-radius: 6px; font-size: 12px; }}
  .checklist {{ background: #fff; border: 1px solid #ddd; border-radius: 8px; padding: 8px 20px; }}
  footer {{ margin-top: 40px; font-size: 12px; color: #777; }}
</style></head><body>
<h1>推荐两版对比 · {html.escape(str(run.get("brief")))}</h1>
<p>原始需求：{html.escape(str(run.get("brief")))}　有效查询：{html.escape(str(run.get("effective_query")))}
　复看时间：{html.escape(str(run.get("generated_at")))}　状态：{html.escape(str(run.get("status")))}</p>
{notice_html}
<h2>拆解出的三栏需求（needs）</h2>
<pre>{html.escape(needs)}</pre>
<h2>人工检查清单</h2>
<ul class="checklist">{checklist}</ul>
{columns_html}
<h2>本次真实用量与费用</h2>
<pre>{html.escape(usage)}</pre>
<pre>{html.escape(cost)}</pre>
<footer>
  报告由 backend/scripts/reco_compare.py 生成：一次 CreativeApplication.recommend()，未重跑、未批量分析。
  图片为本地文件 {html.escape(str(run.get("data_dir")))} 的相对链接。
  价格来源：{html.escape(PRICES["vision"]["source"])}（qwen3.7-flash 输入 0.2 / 输出 0.8 元每百万 token）、
  {html.escape(PRICES["embedding"]["source"])}（文本向量 0.125 元每百万 token）。
</footer>
</body></html>"""


# ---------------------------------------------------------------------------
# save + CLI
# ---------------------------------------------------------------------------
def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def save_artifacts(run: dict, out_dir: Path, *, copy_images: bool = False) -> dict:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    materialize_images(run, Path(run["data_dir"]), out_dir, copy_images)
    _write_json(out_dir / "run.json", run)
    for column in run.get("columns") or []:
        _write_json(out_dir / "columns" / f"{column['purpose']}.json", column)
    (out_dir / "report.html").write_text(render_report_html(run), encoding="utf-8")
    files = ["run.json", "report.html"] + [f"columns/{c['purpose']}.json" for c in run.get("columns") or []]
    return {"out_dir": str(out_dir), "files": files,
            "image_files": sorted(str(p.name) for p in (out_dir / "images").glob("*")) if copy_images else []}


def run_once(brief: str, previous: str | None, data_dir: Path, out_dir: Path, *,
             copy_images: bool = False, settings_provider=None, client_factory=None,
             reviewer=None, needs_parser=None, vision_settings=None, plan: dict | None = None) -> dict:
    """The one real recommendation run + all artifacts, in this order."""
    result, captures = recommend_once(
        brief=brief, previous=previous, data_dir=data_dir,
        settings_provider=settings_provider, client_factory=client_factory,
        reviewer=reviewer, needs_parser=needs_parser, vision_settings=vision_settings)
    ids = set()
    for record in captures.columns.values():
        ids.update(record.get("candidate_ids") or [])
    for col in result.get("columns") or []:
        ids.update(item.get("image_id") for item in col.get("items") or [] if item.get("image_id"))
    images = {}
    if ids:
        try:
            images = hydrate_images(data_dir, sorted(ids))
        except Exception as exc:
            captures.patch_errors.append(f"hydrate: {vision.redact(str(exc))}")
    artifacts = assemble_artifacts(result, captures, brief=brief, previous=previous,
                                   data_dir=data_dir, out_dir=out_dir, images=images, plan=plan)
    saved = save_artifacts(artifacts, out_dir, copy_images=copy_images)
    return {**artifacts, "saved": saved, "capture_errors": captures.patch_errors}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="One-run recommendation two-version comparison (offline harness)")
    parser.add_argument("--brief", default=DEFAULT_BRIEF, help=f"创作需求，默认 {DEFAULT_BRIEF}")
    parser.add_argument("--previous", default=None, help="上一版需求（用于复看前/后的追问对比）")
    parser.add_argument("--dry-run", action="store_true", help="只估算请求/token/费用，不调用模型、不写文件")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help=f"产物目录，默认 {DEFAULT_OUT}")
    parser.add_argument("--data-dir", default=None, help="data 目录（含 app.db 与 images/）；默认自动解析")
    parser.add_argument("--copy-images", action="store_true", help="把引用的图片复制到产物目录 images/")
    args = parser.parse_args(argv)

    data_dir, data_dir_source = resolve_data_dir(args.data_dir)
    if args.dry_run:
        plan = estimate_run(args.brief, args.previous, data_dir)
        plan["data_dir_source"] = data_dir_source
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return 0

    if not (data_dir / "app.db").exists():
        print(json.dumps({"error": "数据库不存在，无法执行真实推荐", "data_dir": str(data_dir)},
                         ensure_ascii=False, indent=2), file=sys.stderr)
        return 2
    plan: dict = {}
    try:
        plan = estimate_run(args.brief, args.previous, data_dir)
        plan["data_dir_source"] = data_dir_source
    except Exception as exc:  # the estimate is advisory only; never block the one run
        plan = {"error": vision.redact(str(exc))}
    try:
        run = run_once(args.brief, args.previous, data_dir, Path(args.out),
                       copy_images=args.copy_images, plan=plan)
    except Exception as exc:
        print(json.dumps({"error": vision.redact(str(exc)), "data_dir": str(data_dir)},
                         ensure_ascii=False, indent=2), file=sys.stderr)
        return 2
    summary = {
        "status": run.get("status"),
        "effective_query": run.get("effective_query"),
        "needs": run.get("needs"),
        "out_dir": run["saved"]["out_dir"],
        "files": run["saved"]["files"],
        "requests": run["requests_and_usage"].get("total_requests"),
        "cost_actual_rmb": run["cost_actual_rmb"].get("total_rmb"),
        "cost_actual_fen": run["cost_actual_rmb"].get("total_fen"),
        "plan_expected_rmb": (run.get("plan") or {}).get("cost_rmb", {}).get("expected"),
        "notices": run.get("notices"),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if run.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
