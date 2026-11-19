"""Vision model: one image -> all analysis fields (CLAUDE.md), via an OpenAI-compatible
/chat/completions endpoint configured in .env. No colors here: those come from Pillow.

Failure kinds (VisionError.kind):
- config:  base url / key / model missing
- auth:    401 / 403
- model:   404, the model name is not available to this key
  (config / auth / model are STOP_KINDS: every image would fail, so the queue stops)
- network: request surely not processed (connect failure, 429, 502/503/504), still
           failing after RETRY_DELAYS
- unknown: ReadTimeout / WriteTimeout / dropped connection: the server may have run and
           billed it, so it is never resent automatically
- http:    any other non-2xx response, including 500 (not resent, same reason)
- invalid: the model answered twice, neither answer passed validate()
- limit:   MAX_REQUESTS reached

`response_format=json_object` being rejected is an endpoint compatibility issue, not an
invalid answer: the request is resent without it (at most once per image, then remembered
per model) and does not use up the invalid-answer retry. Every path counts against
MAX_REQUESTS. All error text goes through redact().
"""
import base64
import io
import json
import os
import re
import time
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Callable

import httpx
from PIL import Image, ImageOps

from . import titles

MAX_REQUESTS = 3       # hard cap on real model requests per image, across every retry kind
RETRY_DELAYS = (2, 8)  # only for errors where the request surely did not run
RETRY_STATUS = {429, 502, 503, 504}
# the request never reached the server: safe to resend
NOT_SENT_ERRORS = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)
RESULT_UNKNOWN = "结果未知，服务端可能已经处理并产生费用，请手动重试"
TIMEOUT = 120
MAX_ERROR_LEN = 500

ENUMS = {
    "title_position": ("top", "middle", "bottom", "left", "right", "none", "unknown"),
    "columns": ("single", "double", "multi", "grid", "free", "unknown"),
    "whitespace": ("much", "medium", "little", "unknown"),
    "image_text_ratio": ("image_dominant", "balanced", "text_dominant", "unknown"),
    "density": ("low", "medium", "high", "unknown"),
}
MEDIUMS = ("photo", "illustration", "3d", "typography", "ui", "mixed")

PROMPT = f"""你是设计参考图的分析助手。观察这张图，只输出一个 JSON 对象，不要输出任何其他文字。

字段（全部必填，键名必须完全一致）：
{{
  "visible_text": 字符串。图中能看清的文字，按阅读顺序原样转写，保留原语言，分行处用 \\n；看不清的部分不要猜；没有文字时为 ""。
  "subject": 字符串数组，1-5 个中文内容主题标签，例如 建筑 / 食品 / 人物 / 展览 / UI / 插画 / 海报 / 摄影 / 字体 / 包装。
  "layout": {{
    "title_position": 数组，可多选：{" / ".join(ENUMS["title_position"])}。没有标题用 none。
    "columns": 数组，可多选：{" / ".join(ENUMS["columns"])}。
    "whitespace": 数组，可多选：{" / ".join(ENUMS["whitespace"])}。
    "image_text_ratio": 数组，可多选：{" / ".join(ENUMS["image_text_ratio"])}。
    "density": 数组，可多选：{" / ".join(ENUMS["density"])}。
    "layout_note": 字符串，一句中文补充上面选项说不清的排版特点。
  }},
  "style": {{
    "tags": 字符串数组，1-6 个中文风格标签，例如 极简 / 复古 / 瑞士风格 / 手绘 / 赛博 / 日系。
    "medium": 字符串，只能是其中一个：{" / ".join(MEDIUMS)}。
    "style_note": 字符串，一句中文说明风格特点。
  }},
  "description": 字符串，一句中文整体描述这张图。
  "title": 字符串，这张图的短标题：12 个汉字以内，是一个能独立成立的完整说法（不要半句话，
    不要以「的/与/和/及/在/了/是」结尾，不要用「这是/图中/画面」开头，不要书名号和句号）。
}}

规则：
- layout 各项只能使用上面列出的英文取值；判断不了就写 ["unknown"]。
- 你不负责颜色分析，颜色由程序另行提取。subject / layout / style / description 中都不要提到主色、配色或具体颜色名称，也不要根据颜色判断风格。visible_text 只照抄图中文字，文字本身含颜色词时照抄，不要额外描述颜色。
- title 宁可短一点也不要断句；实在写不出完整短语时，用 subject 里最具体的那个词加一个名词（例如「极简展览海报」）。
- 只输出 JSON。"""


STOP_KINDS = {"config", "auth", "model"}  # problems that would fail every image


_DATA_URI = re.compile(r"data:image/[\w.+-]*;base64,[A-Za-z0-9+/=]*")
_BEARER = re.compile(r"(?i)\bbearer\s+[^\s\"',;}\]]+")
_AUTH = re.compile(r"""(?i)(authorization["']?\s*[:=]\s*["']?)(?!bearer\s)[^\s"',;}\]]+""")


def redact(text, max_len: int | None = MAX_ERROR_LEN) -> str:
    """Strip secrets from any text that may be stored, shown or raised: the configured
    API key, Authorization / Bearer values, and image data URIs. Then cap the length."""
    if text is None:
        return None
    text = str(text)
    key = os.environ.get("AI_API_KEY", "")
    if len(key) >= 8:
        text = text.replace(key, "<已隐藏>")
    text = _DATA_URI.sub("data:image/...;base64,<已省略>", text)
    text = _BEARER.sub("Bearer <已隐藏>", text)
    text = _AUTH.sub(r"\1<已隐藏>", text)
    if max_len is not None and len(text) > max_len:
        text = text[:max_len] + "…"
    return text


class VisionError(Exception):
    def __init__(self, kind: str, message: str):
        super().__init__(redact(message))
        self.kind = kind


@dataclass
class Calls:
    """Real model calls made for one image. Tokens only from responses that report usage."""
    count: int = 0
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    raw: str | None = None          # last model answer text
    json_fallback: bool = False     # response_format already dropped for this image
    notes: list[str] = field(default_factory=list)

    def add_usage(self, usage: dict | None) -> None:
        if not usage:
            return
        if usage.get("prompt_tokens") is not None:
            self.prompt_tokens = (self.prompt_tokens or 0) + int(usage["prompt_tokens"])
        if usage.get("completion_tokens") is not None:
            self.completion_tokens = (self.completion_tokens or 0) + int(usage["completion_tokens"])


# model name -> False once the endpoint rejected response_format for it
_json_mode: dict[str, bool] = {}


def image_data_uri(path: Path, max_side: int) -> str:
    """JPEG data URI, long side <= max_side. The file on disk is not touched."""
    with Image.open(path) as im:
        im = ImageOps.exif_transpose(im)
        if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
            im = im.convert("RGBA")
            bg = Image.new("RGB", im.size, (255, 255, 255))
            bg.paste(im, mask=im.getchannel("A"))
            im = bg
        else:
            im = im.convert("RGB")
        if max(im.size) > max_side:
            im.thumbnail((max_side, max_side), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=90)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


# ---- validation ----

def _str_list(value, name: str, *, min_len: int = 1) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ValueError(f"{name} 应为字符串数组")
    out = []
    for v in (v.strip() for v in value):
        if v and v not in out:
            out.append(v)
    if len(out) < min_len:
        raise ValueError(f"{name} 为空")
    return out


def _str(value, name: str, *, allow_empty: bool = True) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} 应为字符串")
    value = value.strip()
    if not value and not allow_empty:
        raise ValueError(f"{name} 为空")
    return value


def _obj(value, name: str) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"{name} 应为对象")
    return value


def _require(obj: dict, keys, name: str) -> None:
    missing = [k for k in keys if k not in obj]
    if missing:
        raise ValueError(f"{name} 缺少字段：{', '.join(missing)}")


def validate(data) -> dict:
    """Return the normalized result or raise ValueError. Only unambiguous tidying:
    case / whitespace, a lone string wrapped into a list, duplicates removed.
    Unknown keys are dropped."""
    data = _obj(data, "顶层")
    _require(data, ("visible_text", "subject", "layout", "style", "description"), "顶层")
    layout = _obj(data["layout"], "layout")
    _require(layout, (*ENUMS, "layout_note"), "layout")
    out_layout = {}
    for key, allowed in ENUMS.items():
        values = [v.lower() for v in _str_list(layout[key], f"layout.{key}")]
        bad = [v for v in values if v not in allowed]
        if bad:
            raise ValueError(f"layout.{key} 取值不合法：{', '.join(bad)}")
        out_layout[key] = list(dict.fromkeys(values))
    out_layout["layout_note"] = _str(layout["layout_note"], "layout.layout_note")

    style = _obj(data["style"], "style")
    _require(style, ("tags", "medium", "style_note"), "style")
    medium = _str(style["medium"], "style.medium").lower()
    if medium not in MEDIUMS:
        raise ValueError(f"style.medium 取值不合法：{medium}")

    return {
        "visible_text": _str(data["visible_text"], "visible_text"),
        "subject": _str_list(data["subject"], "subject"),
        "layout": out_layout,
        "style": {
            "tags": _str_list(style["tags"], "style.tags"),
            "medium": medium,
            "style_note": _str(style["style_note"], "style.style_note"),
        },
        "description": _str(data["description"], "description", allow_empty=False),
        # 短标题可选：模型没给或不合规就留空，analysis 不因此失败，后面可用
        # scripts/backfill_titles.py 用文字模型补。
        "title": titles.normalize(data.get("title")),
    }


def parse_answer(text: str | None) -> dict:
    if not text or not text.strip():
        raise ValueError("模型返回为空")
    text = text.strip()
    if text.startswith("```"):  # ```json ... ``` wrapper
        text = text.split("\n", 1)[1] if "\n" in text else ""
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"不是合法 JSON：{exc.msg}（第 {exc.pos} 个字符）") from None
    return validate(data)


# ---- HTTP ----

def _error_text(resp: httpx.Response) -> str:
    """Status code plus the endpoint's own error message (redacted). Never request headers."""
    try:
        body = resp.json()
        err = body.get("error") if isinstance(body, dict) else None
        msg = (err.get("message") if isinstance(err, dict) else None) or body.get("message") or ""
    except Exception:
        msg = resp.text
    return redact(f"HTTP {resp.status_code} {str(msg)[:300]}".strip())


def _rate_limit_delay(header: str, fallback: int) -> float:
    try:
        seconds = float(header) if header.isdecimal() else parsedate_to_datetime(header).timestamp() - time.time()
    except (ValueError, TypeError, OverflowError, OSError):
        return fallback
    return min(60, max(fallback, seconds))


def _post(client: httpx.Client, settings: dict, payload: dict, calls: Calls,
          sleep: Callable[[float], None]) -> dict:
    """One logical request with limited retries for errors where the request surely did
    not run (connect failures, 429, 502/503/504). Never exceeds MAX_REQUESTS per image."""
    url = settings["base_url"] + "/chat/completions"
    headers = {"Authorization": f"Bearer {settings['api_key']}"}
    for delay in (*RETRY_DELAYS, None):
        if calls.count >= MAX_REQUESTS:
            raise VisionError("limit", f"已达到单张图 {MAX_REQUESTS} 次请求上限")
        calls.count += 1
        try:
            resp = client.post(url, json=payload, headers=headers, timeout=TIMEOUT)
        except NOT_SENT_ERRORS as exc:
            problem = type(exc).__name__
        except httpx.HTTPError as exc:  # ReadTimeout, WriteTimeout, RemoteProtocolError, ...
            raise VisionError("unknown", f"{type(exc).__name__}：{RESULT_UNKNOWN}") from None
        else:
            if resp.status_code == 200:
                try:
                    return resp.json()
                except ValueError:
                    raise VisionError("http", "接口返回不是 JSON") from None
            if resp.status_code in (401, 403):
                raise VisionError("auth", _error_text(resp))
            if resp.status_code == 404:
                raise VisionError("model", f"{_error_text(resp)}（模型：{settings['vision_model']}）")
            if resp.status_code not in RETRY_STATUS:
                raise VisionError("http", _error_text(resp))
            problem = _error_text(resp)
            retry_after = resp.headers.get("Retry-After", "").strip()
            if resp.status_code == 429 and delay is not None and retry_after:
                delay = _rate_limit_delay(retry_after, delay)
        if delay is None or calls.count >= MAX_REQUESTS:
            raise VisionError("network", problem)
        calls.notes.append(f"{problem}，{delay}s 后重试")
        sleep(delay)
    raise AssertionError("unreachable")


def _ask(client, settings, messages, calls, sleep) -> str | None:
    model = settings["vision_model"]
    payload = {"model": model, "messages": messages, "temperature": 0.1}
    if settings.get("enable_thinking") is not None:  # DashScope: top-level body field
        payload["enable_thinking"] = settings["enable_thinking"]
    if _json_mode.get(model, True) and not calls.json_fallback:
        try:
            body = _post(client, settings, {**payload, "response_format": {"type": "json_object"}},
                         calls, sleep)
        except VisionError as exc:
            if exc.kind != "http" or "response_format" not in str(exc).lower():
                raise
            _json_mode[model] = False
            calls.json_fallback = True
            calls.notes.append("接口不支持 response_format，已去掉该参数重发")
            body = _post(client, settings, payload, calls, sleep)
    else:
        body = _post(client, settings, payload, calls, sleep)
    calls.add_usage(body.get("usage"))
    try:
        return body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return None


def analyze(path: Path, settings: dict, *, calls: Calls, client: httpx.Client | None = None,
            sleep: Callable[[float], None] = time.sleep) -> dict:
    """One image -> validated fields. `calls` is filled in even when this raises."""
    if not (settings.get("base_url") and settings.get("api_key") and settings.get("vision_model")):
        raise VisionError("config", "未配置 AI_BASE_URL / AI_API_KEY / VISION_MODEL")
    uri = image_data_uri(path, settings.get("vision_max_side", 1280))
    messages = [{"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": uri}},
        {"type": "text", "text": PROMPT},
    ]}]
    own = client is None
    client = client or httpx.Client()
    try:
        problem = None
        for _ in range(2):  # invalid answer: one retry
            calls.raw = _ask(client, settings, messages, calls, sleep)
            try:
                return parse_answer(calls.raw)
            except ValueError as exc:
                problem = str(exc)
                calls.notes.append(f"输出不合法：{problem}")
        raise VisionError("invalid", f"两次输出都不合法：{problem}")
    finally:
        if own:
            client.close()
