"""Local keyword signals for search.

The demo library stores a Chinese AI description and tags for every image, so a
query that literally names an object should be able to lean on that text instead
of trusting a weak CLIP neighbour. This module also refuses obviously absent
long-sentence objects: when the head object of a long query appears in no
description or tag, search returns no rows instead of a wall of unrelated images.
"""
from __future__ import annotations

import json

from . import config, local_discovery
from .embedding.search_application import readonly_connection

# Common library objects/phrases, used only to find the head object of a long
# query. Keeping the list explicit is safer than guessing Chinese word boundaries.
OBJECT_TERMS = (
    "蘑菇房子", "草莓蛋糕", "自行车", "茶杯", "鲸鱼", "翅膀", "蘑菇",
    "蛋糕", "草莓", "月亮", "月球", "海洋", "大海", "海边", "沙滩", "海",
    "花朵", "雏菊", "玫瑰", "花束", "花", "云朵", "云层", "天空", "云",
    "高山", "山峰", "山脉", "山", "猫", "狗", "人物", "建筑", "大楼", "房子",
    "街道", "商店", "地铁", "汽车", "车", "船", "桥", "树", "森林", "鸟",
    "鱼", "台灯", "灯", "书", "海报", "字体", "球", "星星", "太阳", "彩虹",
    "窗户", "门", "蜡烛", "花瓶", "风筝", "香囊", "图案", "纹样", "界面",
    "展板", "封面", "插画", "包装", "标志", "版式", "画册", "书籍",
    "手机", "杯子", "椅子", "桌子",
)

# Single-character terms can be part of unrelated compounds (海 -> 海报/上海).
_FALSE_COMPOUNDS = {
    "海": ("海报", "上海", "人海", "海量", "海派", "海归", "海外", "海选"),
}

_MIN_LONG_QUERY = 6


def _clean(text: str, term: str) -> str:
    if len(term) == 1:
        for compound in _FALSE_COMPOUNDS.get(term, ()):
            text = text.replace(compound, " ")
    return text


def _row_text(description, subject, style_json, annotation_description, tags_json) -> str:
    # Deliberately exclude OCR visible_text: incidental article sentences mention
    # 海洋/月海 and would make a query for 海 match moon infographics.
    parts = [description or "", subject or "", annotation_description or ""]
    for raw in (style_json, tags_json):
        if not raw:
            continue
        try:
            value = json.loads(raw) if isinstance(raw, str) else raw
        except (TypeError, ValueError):
            continue
        if isinstance(value, dict):
            value = value.get("tags")
        if isinstance(value, list):
            parts.extend(item for item in value if isinstance(item, str))
    return " ".join(part for part in parts if part)


def _texts(conn) -> list[tuple[int, str]]:
    rows = conn.execute(
        """SELECT a.image_id, a.description, a.subject, a.visible_text, a.style_json,
                  an.description AS annotation_description, an.tags_json
           FROM analyses a
           LEFT JOIN image_annotations an ON an.image_id = a.image_id
           WHERE a.status = 'done'"""
    )
    return [(int(row["image_id"]),
             _row_text(row["description"], row["subject"],
                       row["style_json"], row["annotation_description"], row["tags_json"]))
            for row in rows]


def _contains(text: str, term: str) -> bool:
    return term in _clean(text, term)


def find_objects(query: str) -> list[tuple[int, int, str]]:
    """Longest non-overlapping object matches as (start, end, term), in text order."""
    found: list[tuple[int, int, str]] = []
    for term in sorted(OBJECT_TERMS, key=len, reverse=True):
        start = 0
        while True:
            index = query.find(term, start)
            if index < 0:
                break
            end = index + len(term)
            if not any(index < used_end and end > used_start for used_start, used_end, _ in found):
                found.append((index, end, term))
            start = index + 1
    found.sort()
    return found


def _query_terms(query: str) -> tuple[str, ...]:
    subject = local_discovery.search_subject(query)
    expanded = local_discovery.SHORT_EXPANSIONS.get(subject)
    if expanded:
        return tuple(dict.fromkeys(term for term in expanded if term))
    return tuple(dict.fromkeys(term for _, _, term in find_objects(query)))


def keyword_boosts(query: str) -> dict[int, float]:
    """Per-image bonus from literal description/tag matches (0..0.3)."""
    if not isinstance(query, str) or not query.strip():
        return {}
    terms = _query_terms(query)
    if not terms:
        return {}
    with readonly_connection(config.db_path()) as conn:
        texts = _texts(conn)
    # A rare term (雏菊) is stronger evidence than a common one (花).
    document_frequency = {term: sum(1 for _, text in texts if _contains(text, term))
                          for term in terms}
    boosts: dict[int, float] = {}
    for image_id, text in texts:
        weight = 0.0
        for term in terms:
            if _contains(text, term):
                weight += 0.06 + 0.12 / (1.0 + document_frequency.get(term, 0))
        if weight:
            boosts[image_id] = min(0.30, weight)
    return boosts


def missing_main_object(query: str) -> bool:
    """True when a long query's head object is absent from every description/tag."""
    if not isinstance(query, str):
        return False
    query = query.strip()
    if len(query) < _MIN_LONG_QUERY:
        return False
    objects = find_objects(query)
    if not objects:
        return False
    head = objects[-1][2]
    with readonly_connection(config.db_path()) as conn:
        texts = _texts(conn)
    return not any(_contains(text, head) for _, text in texts)
