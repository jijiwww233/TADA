"""Local keyword signals for search and the final relevance gate.

The demo library stores a Chinese AI description and tags for every image, so a
query that literally names an object should be able to lean on that text instead
of trusting a weak CLIP neighbour. This module also refuses obviously absent
long-sentence objects: when the head object of a long query appears in no
description or tag, search returns no rows instead of a wall of unrelated images.

The last step of /api/search also lives here. The fused 0..1 score is a ranking
signal only (both routes are min-max normalised, so its first row is ~1.0 even when
nothing is close), therefore the keep/drop decision is made on the absolute evidence
the search layer attaches to each row: raw text-route cosine, raw local-CLIP cosine,
literal subject words, palette affinity and the named-colour requirement. Every
surviving row is marked strong or weak for the search wall.

A phrase query that names no library object and no colour (复古 / 国潮 / 胶片) has no
trustworthy semantic anchor: the 0..1 fused score only orders whatever the two routes
happened to recall, so a row whose stored title/description/tags literally contain the
query could be missing entirely. For those queries the gate additionally recalls the
literal hits itself and marks every one strong, so a query word that is written on the
image text is never answered with "没找到很像的".
"""
from __future__ import annotations

import json
import os

from . import config, local_discovery
from .embedding.search import SearchHit
from .embedding.search_application import hydrate_hits, readonly_connection

# Common library objects/phrases, used only to find the head object of a long
# query. Keeping the list explicit is safer than guessing Chinese word boundaries.
OBJECT_TERMS = (
    "蘑菇房子", "草莓蛋糕", "洗衣店", "自行车", "茶杯", "鲸鱼", "翅膀", "蘑菇",
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

# Spatial/relational words start the subordinate phrase of a long query; the object
# people mean lives in the main clause before them. 暖黄色的灯 / 里面好像有星星 is a
# lamp query, not a star query, while 长着翅膀的茶杯 keeps 茶杯 as its head object.
_CLAUSE_MARKERS = (
    "里面", "中间", "内部", "上方", "上面", "下方", "下面", "背景", "旁边", "前面",
    "后面", "外面", "对面", "周围", "四周",
)

# Relevance gate: judged on absolute evidence, never on the fused ranking score.
# Override per run without a deploy, e.g. SEARCH_STRONG_FLOOR=0.3 for an offline sweep.
def _env_number(name: str, default, kind=float):
    try:
        return kind(os.environ.get(name, default))
    except (TypeError, ValueError):
        return kind(default)

# A mood query (no named colour, no library object) keeps the strict pair.
STRONG_FLOOR = _env_number("SEARCH_STRONG_FLOOR", 0.36)
STRONG_TOP_RATIO = _env_number("SEARCH_STRONG_TOP_RATIO", 0.86)
# A named colour with no subject word (蓝色衣服) must be genuinely close to look strong.
COLOUR_FLOOR = _env_number("SEARCH_COLOUR_FLOOR", 0.44)
# A subject word gives literal text support, so a lower cosine is enough - but the row
# still has to stay close to the best hit, so a stray neighbour cannot become strong.
LITERAL_FLOOR = _env_number("SEARCH_LITERAL_FLOOR", 0.30)
LITERAL_TOP_RATIO = _env_number("SEARCH_LITERAL_TOP_RATIO", 0.75)
# A subject query with no literal hit in the library has to be very similar.
SEMANTIC_FLOOR = _env_number("SEARCH_SEMANTIC_FLOOR", 0.52)
WEAK_FLOOR = _env_number("SEARCH_WEAK_FLOOR", 0.22)
WEAK_TOP_RATIO = _env_number("SEARCH_WEAK_TOP_RATIO", 0.55)
WEAK_MAX = _env_number("SEARCH_WEAK_MAX", 6, int)
# Palette route rows carry no cosine; a colour query keeps only the best palettes.
PALETTE_TOP_RATIO = _env_number("SEARCH_PALETTE_TOP_RATIO", 0.75)
# annotations.search_matches scores a description hit at 2.0 and a tag hit at 3.0+, so
# anything at that scale is a literal local text match and never needs a cosine line.
LITERAL_SCORE = 2.0
EVIDENCE_KEYS = ("_text_cos", "_clip_cos", "_local_cos")


def debug_enabled() -> bool:
    return os.environ.get("SEARCH_DEBUG") == "1"


def _clean(text: str, term: str) -> str:
    if len(term) == 1:
        for compound in _FALSE_COMPOUNDS.get(term, ()):
            text = text.replace(compound, " ")
    return text


def _row_text(title, description, subject, style_json, annotation_description, tags_json) -> str:
    # Deliberately exclude OCR visible_text: incidental article sentences mention
    # 海洋/月海 and would make a query for 海 match moon infographics.
    parts = [title or "", description or "", subject or "", annotation_description or ""]
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


def _document_rows(conn) -> list[dict]:
    """Every image with its effective title and the literal text search may trust.

    The card title is user-visible memory aid (manual title first, generated title
    second) and counts as literal evidence exactly like description and tags. Images
    without a finished analysis still contribute their manual title.
    """
    rows = conn.execute(
        """SELECT i.id AS image_id, COALESCE(m.title, a.title) AS title,
                  a.description, a.subject, a.style_json,
                  an.description AS annotation_description, an.tags_json
           FROM images i
           LEFT JOIN analyses a ON a.image_id = i.id AND a.status = 'done'
           LEFT JOIN image_titles_manual m ON m.image_id = i.id
           LEFT JOIN image_annotations an ON an.image_id = i.id
           ORDER BY i.id"""
    )
    out = []
    for row in rows:
        title = row["title"] or ""
        out.append({"image_id": int(row["image_id"]), "title": title,
                    "text": _row_text(title, row["description"], row["subject"],
                                      row["style_json"], row["annotation_description"],
                                      row["tags_json"])})
    return out


def _texts(conn) -> list[tuple[int, str]]:
    return [(row["image_id"], row["text"]) for row in _document_rows(conn)]


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


def _main_clause(query: str) -> str:
    """The part of a long query before its first spatial/relational marker."""
    end = len(query)
    for marker in _CLAUSE_MARKERS:
        index = query.find(marker)
        if 0 <= index < end:
            end = index
    return query[:end]


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
    clause = _main_clause(query)
    objects = find_objects(clause) or find_objects(query)
    if not objects:
        return False
    head = objects[-1][2]
    with readonly_connection(config.db_path()) as conn:
        texts = _texts(conn)
    return not any(_contains(text, head) for _, text in texts)


# ---------------------------------------------------------------- relevance gate

def color_need(query: str) -> tuple:
    """Colour families a result must show when the query names a concrete colour."""
    try:
        return local_discovery.search_color_targets(query)
    except Exception:
        return ()


def subject_terms(query: str) -> tuple[str, ...]:
    """Object words the query itself names (find_objects only, never the synonyms)."""
    try:
        return tuple(dict.fromkeys(term for _, _, term in find_objects(query)))
    except Exception:
        return ()


def _colour_covered(image_ids, targets) -> set[int]:
    if not targets or not image_ids:
        return set(image_ids)
    with readonly_connection(config.db_path()) as conn:
        return local_discovery.palette_covers(conn, image_ids, targets)


def _subject_hits(image_ids, terms) -> set[int]:
    """Image ids whose stored title/description/tags literally contain a subject word."""
    ids = [int(image_id) for image_id in image_ids]
    if not terms or not ids:
        return set()
    marks = ",".join("?" for _ in ids)
    hits: set[int] = set()
    with readonly_connection(config.db_path()) as conn:
        for row in conn.execute(
                f"""SELECT i.id AS image_id, COALESCE(m.title, a.title) AS title,
                           a.description, a.subject, a.style_json,
                           an.description AS annotation_description, an.tags_json
                    FROM images i
                    LEFT JOIN analyses a ON a.image_id = i.id AND a.status = 'done'
                    LEFT JOIN image_titles_manual m ON m.image_id = i.id
                    LEFT JOIN image_annotations an ON an.image_id = i.id
                    WHERE i.id IN ({marks})""", ids):
            text = _row_text(row["title"], row["description"], row["subject"], row["style_json"],
                             row["annotation_description"], row["tags_json"])
            if any(_contains(text, term) for term in terms):
                hits.add(int(row["image_id"]))
    return hits


# An exact multi-character phrase can be recalled from the stored text itself. One
# character (花 / 海) stays on the object/subject pipeline: a single character matches
# too many unrelated compounds (海报 for 海) to be a reliable literal anchor.
_LITERAL_PHRASE_MAX = 24
_PHRASE_TRIM = "，。、！？,.!?;；:：…·“”\"'「」『』（）()【】[] \t\r\n"


def _literal_phrase(query: str) -> str:
    """The whole query as an exact phrase, when it is specific enough to match text."""
    if not isinstance(query, str):
        return ""
    phrase = query.strip().strip(_PHRASE_TRIM).strip()
    if not phrase or len(phrase) < 2 or len(phrase) > _LITERAL_PHRASE_MAX:
        return ""
    if any(char.isspace() for char in phrase):
        return ""
    return phrase


def literal_hits(query: str) -> list[int]:
    """Image ids whose title/description/tags literally contain the query phrase.

    Title hits come first (the wall card title is the most direct evidence the user can
    see), then the remaining text hits; both groups keep numeric image-id order so the
    same query returns a stable wall.
    """
    phrase = _literal_phrase(query)
    if not phrase:
        return []
    with readonly_connection(config.db_path()) as conn:
        rows = _document_rows(conn)
    title_hits = [row["image_id"] for row in rows if _contains(row["title"], phrase)]
    title_set = set(title_hits)
    text_hits = [row["image_id"] for row in rows
                 if row["image_id"] not in title_set and _contains(row["text"], phrase)]
    return title_hits + text_hits


def _evidence(item: dict) -> dict:
    values = {}
    for key in EVIDENCE_KEYS:
        value = item.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            values[key[1:]] = round(float(value), 4)
    return values


def _number(item: dict, key: str):
    value = item.get(key)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return round(float(value), 4)
    return None


def _public_row(item: dict) -> dict:
    return {key: value for key, value in item.items() if not str(key).startswith("_")}


def apply_relevance(result: dict, query: str) -> dict:
    """Gate one /api/search response into strong rows plus at most WEAK_MAX weak rows.

    The fused score only orders rows, so every line is judged on the absolute evidence
    the search layer attached. The rule depends on how the query meets the library:
    a named colour with no subject word, a subject word with literal text support, a
    subject word with no literal hit (must be very similar), a mood query, or the
    palette route that carries no cosine at all. A row that misses the named-colour
    requirement may still be weak, never strong; literal annotation hits stay strong;
    palette rows of a colour query keep only the best palettes.

    For a phrase query that names no library object and no colour (复古 / 国潮 / 胶片)
    the stored text is the only trustworthy anchor, so the gate recalls every image
    whose title, description or tags literally contain the phrase and keeps those rows
    strong - including ones the semantic routes failed to recall at all. Never raises.
    """
    if not isinstance(result, dict):
        return result
    items = result.get("items") or []
    if not items:
        return result
    debug = debug_enabled()
    targets = color_need(query)
    terms = subject_terms(query)
    # Only a phrase that names neither a library object nor a colour leans on exact
    # text recall; object/colour queries keep their existing subject/colour rules.
    phrase = _literal_phrase(query) if not terms and not targets else ""
    image_ids = [int(item["image_id"]) for item in items
                 if isinstance(item.get("image_id"), int) and not isinstance(item.get("image_id"), bool)]
    try:
        covered = _colour_covered(image_ids, targets)
    except Exception:
        covered = set(image_ids)
    try:
        subject_hits = _subject_hits(image_ids, terms)
    except Exception:
        subject_hits = set()
    try:
        phrase_hits = literal_hits(query) if phrase else []
    except Exception:
        phrase_hits = []
    phrase_set = set(phrase_hits)
    lanes = []
    for item in items:
        row = _public_row(item)
        evidence = _evidence(item)
        image_id = row.get("image_id")
        score = row.get("score")
        lanes.append({
            "row": row,
            "evidence": evidence,
            "value": max(evidence.values()) if evidence else None,
            "palette": _number(item, "_palette_score"),
            "literal": bool(item.get("_literal")) or (
                isinstance(score, (int, float)) and not isinstance(score, bool) and score >= LITERAL_SCORE),
            "colour_ok": True if not targets else image_id in covered,
            "subject_hit": image_id in subject_hits,
            "phrase_hit": image_id in phrase_set,
            "boost": bool(item.get("_boost")),
        })
    best = max((lane["value"] for lane in lanes if lane["value"] is not None), default=0.0)
    best_palette = max((lane["palette"] for lane in lanes if lane["palette"] is not None), default=0.0)
    strong, weak = [], []
    for lane in lanes:
        value = lane["value"]
        if lane["literal"] or lane["phrase_hit"]:
            # An annotation hit or a literal title/description/tag hit is direct
            # evidence, even when the two semantic routes did not rank it well.
            lane["relevance"] = "strong"
        elif value is None:
            # No comparable cosine: the palette route (or a channel that already made
            # its own selection). A colour query keeps only its best palettes.
            if lane["palette"] is None or not targets:
                lane["relevance"] = "strong"
            elif lane["colour_ok"] and lane["palette"] >= best_palette * PALETTE_TOP_RATIO:
                lane["relevance"] = "strong"
            else:
                lane["relevance"] = "weak"
        elif terms:
            if lane["subject_hit"]:
                lane["relevance"] = "strong" if (
                    lane["colour_ok"] and value >= LITERAL_FLOOR
                    and value >= best * LITERAL_TOP_RATIO) else "weak"
            else:
                lane["relevance"] = "strong" if (
                    lane["colour_ok"] and value >= SEMANTIC_FLOOR) else "weak"
        elif targets:
            lane["relevance"] = "strong" if (
                lane["colour_ok"] and value >= COLOUR_FLOOR
                and value >= best * STRONG_TOP_RATIO) else "weak"
        else:
            lane["relevance"] = "strong" if (
                value >= STRONG_FLOOR and value >= best * STRONG_TOP_RATIO) else "weak"
        (strong if lane["relevance"] == "strong" else weak).append(lane)
    kept_weak = []
    for lane in weak:
        if lane["value"] is None:
            kept_weak.append(lane)  # palette order already is the ranking
        elif lane["value"] >= WEAK_FLOOR and lane["value"] >= best * WEAK_TOP_RATIO:
            kept_weak.append(lane)
    kept = strong + kept_weak[:max(0, WEAK_MAX)]
    policy = {
        "strong_floor": STRONG_FLOOR, "strong_top_ratio": STRONG_TOP_RATIO,
        "colour_floor": COLOUR_FLOOR, "literal_floor": LITERAL_FLOOR,
        "literal_top_ratio": LITERAL_TOP_RATIO, "semantic_floor": SEMANTIC_FLOOR,
        "weak_floor": WEAK_FLOOR, "weak_top_ratio": WEAK_TOP_RATIO,
        "weak_max": WEAK_MAX, "palette_top_ratio": PALETTE_TOP_RATIO,
        "colour_min_share": local_discovery.COLOR_MIN_SHARE,
        "colour_need": [target.hex for target in targets],
        "subject_terms": list(terms), "literal_phrase": phrase, "best": round(best, 4),
        "best_palette": round(best_palette, 4),
    }
    output = []
    for lane in kept:
        row = {**lane["row"], "relevance": lane["relevance"]}
        if debug:
            row["debug"] = {**lane["evidence"], "value": lane["value"],
                            "palette_score": lane["palette"], "boost": lane["boost"],
                            "colour_ok": lane["colour_ok"],
                            "subject_hit": lane["subject_hit"],
                            "phrase_hit": lane["phrase_hit"],
                            "colour_need": policy["colour_need"],
                            "best": policy["best"]}
        output.append(row)
    if phrase_hits:
        # Recall the literal rows the semantic routes missed. They are already ordered
        # title hits first; rows that were ranked keep their order and their score.
        found = {row.get("image_id"): row for row in output}
        missing = [image_id for image_id in phrase_hits if image_id not in found]
        hydrated: dict[int, dict] = {}
        if missing:
            try:
                with readonly_connection(config.db_path()) as conn:
                    for row in hydrate_hits(conn, [SearchHit(image_id, 1.0)
                                                   for image_id in missing]):
                        hydrated[int(row["image_id"])] = row
            except Exception:
                hydrated = {}
        ordered: list[dict] = []
        included: set[int] = set()
        for image_id in phrase_hits:
            row = found.get(image_id) or hydrated.get(image_id)
            if row is None or image_id in included:
                continue
            included.add(image_id)
            ordered.append({**row, "relevance": "strong"})
        for row in output:
            image_id = row.get("image_id")
            if image_id in included:
                continue
            included.add(image_id)
            ordered.append(row)
        output = ordered
    gated = dict(result)
    gated["items"] = output
    if debug:
        gated["debug_rows"] = [
            {"image_id": lane["row"].get("image_id"), "score": lane["row"].get("score"),
             "relevance": lane["relevance"], **lane["evidence"],
             "value": lane["value"], "palette_score": lane["palette"],
             "boost": lane["boost"], "colour_ok": lane["colour_ok"],
             "subject_hit": lane["subject_hit"], "phrase_hit": lane["phrase_hit"]}
            for lane in lanes]
        known = {entry["image_id"] for entry in gated["debug_rows"]}
        gated["debug_rows"].extend(
            {"image_id": image_id, "score": None, "relevance": "strong",
             "literal_phrase": True}
            for image_id in phrase_hits if image_id not in known)
        gated["debug_policy"] = policy
    return gated
