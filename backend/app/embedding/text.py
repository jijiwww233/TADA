"""Deterministic text builder for the single combined embedding (combined_d_v1).

Input: one analysis row (AI fields) + Pillow colors. Output: one Chinese text in a
fixed field order. Nothing here calls the AI or reads the DB.

Rules:
- Field order: description, subject, layout, style, coarse colors, visible_text.
- "unknown" never becomes an affirmative statement: unknown values are skipped.
- Colors come ONLY from deterministic HSV mapping of Pillow hex values; never from
  AI style/description text, and no hex is written into the text.
- visible_text: normalize whitespace, drop empty/duplicate lines, then cap at 300 chars
  (first 200 + last 100).
"""
from __future__ import annotations

import colorsys
import json
import re
from collections.abc import Iterable, Mapping
from typing import Any

TEXT_VERSION = "combined_d_v1"

VISIBLE_TEXT_LIMIT = 300
VISIBLE_TEXT_HEAD = 200
MAX_COLOR_NAMES = 3

TITLE_POSITION = {
    "top": "标题在顶部",
    "middle": "标题在中部",
    "bottom": "标题在底部",
    "left": "标题在左侧",
    "right": "标题在右侧",
    "none": "无标题",
}
COLUMNS = {"single": "单栏", "double": "双栏", "multi": "多栏", "grid": "网格", "free": "自由版式"}
WHITESPACE = {"much": "留白多", "medium": "留白适中", "little": "留白少"}
IMAGE_TEXT_RATIO = {"image_dominant": "图片为主", "balanced": "图文均衡", "text_dominant": "文字为主"}
DENSITY = {"low": "密度低", "medium": "密度中", "high": "密度高"}
MEDIUM = {
    "photo": "摄影",
    "illustration": "插画",
    "3d": "3D",
    "typography": "字体设计",
    "ui": "UI",
    "mixed": "混合媒介",
}

_SPACE_RE = re.compile(r"[ \t\u3000]+")


def _is_unknown(value: Any) -> bool:
    return isinstance(value, str) and value.strip().lower() == "unknown"


def _load_jsonish(value: Any) -> Any:
    """DB rows store subject/layout/style as JSON strings; tests pass real objects."""
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None
    return value


def _mapping(value: Any) -> dict:
    loaded = _load_jsonish(value)
    return loaded if isinstance(loaded, Mapping) else {}


def _items(value: Any) -> list[str]:
    """Flatten string / list of strings, drop empty and unknown values, keep order."""
    loaded = _load_jsonish(value)
    if loaded is None:
        return []
    values = [loaded] if isinstance(loaded, str) else loaded
    if not isinstance(values, (list, tuple)):
        return []
    out: list[str] = []
    for item in values:
        if not isinstance(item, str):
            continue
        item = _SPACE_RE.sub(" ", item).strip()
        if not item or _is_unknown(item):
            continue
        out.append(item)
    return out


def _labeled(value: str) -> str:
    value = _SPACE_RE.sub(" ", str(value)).strip()
    return "" if not value or _is_unknown(value) else value


def _part(mapping: Mapping, key: str, labels: Mapping[str, str]) -> list[str]:
    raw = mapping.get(key)
    values = [raw] if isinstance(raw, str) else (raw if isinstance(raw, (list, tuple)) else [])
    out: list[str] = []
    for value in values:
        if not isinstance(value, str):
            continue
        label = labels.get(value.strip().lower())
        if label:
            out.append(label)
    return out


def coarse_color_name(hex_value: str) -> str:
    """Deterministic coarse Chinese color name from a #rrggbb value.

    Rules (documented so tests can pin them):
    - invalid/short hex -> "".
    - saturation < 0.10: value>=0.90 白; >=0.65 浅灰; >=0.35 灰; >=0.12 深灰; else 黑.
    - value < 0.18 and saturation < 0.35 -> 黑.
    - hue buckets: red/orange/yellow/yellow-green/green/cyan/blue/purple/pink.
    - brown: hue 15..50, value<0.55, 0.20<=saturation<0.70.
    """
    if not isinstance(hex_value, str):
        return ""
    match = re.fullmatch(r"#?([0-9a-fA-F]{6})", hex_value.strip())
    if not match:
        return ""
    raw = match.group(1)
    r, g, b = (int(raw[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
    h, s, v = colorsys.rgb_to_hsv(r, g, b)
    hue = h * 360.0
    if s < 0.10:
        if v >= 0.90:
            return "白"
        if v >= 0.65:
            return "浅灰"
        if v >= 0.35:
            return "灰"
        if v >= 0.12:
            return "深灰"
        return "黑"
    if v < 0.18 and s < 0.35:
        return "黑"
    if 15 <= hue < 50 and v < 0.55 and 0.20 <= s < 0.70:
        return "棕"
    if hue < 15 or hue >= 345:
        return "红"
    if hue < 40:
        return "橙"
    if hue < 65:
        return "黄"
    if hue < 85:
        return "黄绿"
    if hue < 150:
        return "绿"
    if hue < 185:
        return "青"
    if hue < 250:
        return "蓝"
    if hue < 290:
        return "紫"
    return "粉"


def _color_rows(colors: Iterable[Any]) -> list[tuple[float, int, str]]:
    rows: list[tuple[float, int, str]] = []
    for index, color in enumerate(colors or []):
        if isinstance(color, Mapping):
            hex_value = str(color.get("hex") or color.get("value") or "")
            ratio = float(color.get("ratio") or color.get("share") or 0.0)
            rank = int(color.get("rank") if color.get("rank") is not None else index)
        elif isinstance(color, (list, tuple)) and color:
            hex_value = str(color[0])
            ratio = float(color[1]) if len(color) > 1 else 0.0
            rank = index
        else:
            continue
        rows.append((ratio, rank, hex_value))
    rows.sort(key=lambda row: (-row[0], row[1], row[2]))
    return rows


def coarse_color_names(colors: Iterable[Any]) -> list[str]:
    """Top distinct coarse names, deterministic: ratio desc, then rank/hex."""
    names: list[str] = []
    for _ratio, _rank, hex_value in _color_rows(colors):
        name = coarse_color_name(hex_value)
        if name and name not in names:
            names.append(name)
        if len(names) >= MAX_COLOR_NAMES:
            break
    return names


def normalize_visible_text(text: str | None) -> str:
    """Collapse whitespace, drop empty lines and exact duplicate lines (first wins)."""
    if not text:
        return ""
    text = str(text).replace("\r\n", "\n").replace("\r", "\n")
    lines: list[str] = []
    seen: set[str] = set()
    for raw in text.split("\n"):
        line = _SPACE_RE.sub(" ", raw).strip()
        if not line or line in seen:
            continue
        seen.add(line)
        lines.append(line)
    return "\n".join(lines)


def excerpt_visible_text(text: str | None, limit: int = VISIBLE_TEXT_LIMIT,
                         head: int = VISIBLE_TEXT_HEAD) -> str:
    """Normalize then cap: <=limit keeps all; otherwise first head + last (limit-head)."""
    normalized = normalize_visible_text(text)
    if len(normalized) <= limit:
        return normalized
    tail = max(1, limit - head)
    return normalized[:head] + normalized[-tail:]


def _layout_line(layout: Mapping[str, Any]) -> str:
    parts: list[str] = []
    for key, labels in (
        ("title_position", TITLE_POSITION),
        ("columns", COLUMNS),
        ("whitespace", WHITESPACE),
        ("image_text_ratio", IMAGE_TEXT_RATIO),
        ("density", DENSITY),
    ):
        parts.extend(_part(layout, key, labels))
    note = _labeled(layout.get("layout_note") or "")
    if note:
        parts.append(note)
    return "、".join(parts)


def _style_line(style: Mapping[str, Any]) -> str:
    parts: list[str] = list(_items(style.get("tags")))
    medium = str(style.get("medium") or "").strip().lower()
    if medium in MEDIUM:
        parts.append(MEDIUM[medium])
    note = _labeled(style.get("style_note") or "")
    if note:
        parts.append(note)
    return "、".join(parts)


def build_combined_d_v1(analysis: Mapping[str, Any], colors: Iterable[Any] = ()) -> str:
    """Build the deterministic combined_d_v1 text (possibly "" if nothing is known)."""
    analysis = mapping_or_empty(analysis)
    lines: list[str] = []

    description = _labeled(analysis.get("description") or "")
    if description:
        lines.append("描述：" + description)

    subject = "、".join(_items(analysis.get("subject")))
    if subject:
        lines.append("主题：" + subject)

    layout = _mapping(analysis.get("layout") or analysis.get("layout_json"))
    layout_line = _layout_line(layout)
    if layout_line:
        lines.append("排版：" + layout_line)

    style = _mapping(analysis.get("style") or analysis.get("style_json"))
    style_line = _style_line(style)
    if style_line:
        lines.append("风格：" + style_line)

    color_line = "、".join(coarse_color_names(colors))
    if color_line:
        lines.append("色彩：" + color_line)

    visible = excerpt_visible_text(analysis.get("visible_text"))
    if visible:
        lines.append("图内文字：" + visible)

    return "\n".join(lines)


def mapping_or_empty(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    loaded = _load_jsonish(value)
    return loaded if isinstance(loaded, Mapping) else {}


def text_hash(text: str) -> str:
    """Hash of scheme + text; stable across processes and Python versions."""
    import hashlib

    return hashlib.sha256((TEXT_VERSION + "\n" + (text or "")).encode("utf-8")).hexdigest()
