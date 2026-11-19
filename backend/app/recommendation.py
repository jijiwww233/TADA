"""Pure recommendation core. No database, environment, model, or filesystem access.

API: parse_brief(brief, previous=None) -> Brief; recommend(brief, candidates,
limit=6) -> dict. A follow-up is parsed with previous=the earlier Brief; new positive
terms replace exclusions for the same term and vice versa. Unmentioned terms persist.

Candidates are mappings with image_id, semantic_score, sha256, analyses (subject,
layout, style, description, visible_text), colors=[{hex, ratio}], and optional
post_id/post_ids/post_images=[{post_id}], is_video. Input is never mutated. Only
the highest-scoring 20 candidates are considered. Evidence paths address the
representative candidate verbatim; merged source IDs carry their own post IDs.

Eligibility (Day6 tightening): a candidate needs at least one explicit requirement
hit with evidence. Design artifacts named in the brief (展板/海报/封面) are requirements
matched only by the candidate's own subject / style tags (identity labels, not prose); only then does it
get a layout purpose, never merely because its medium is typography/ui/mixed. With a
named topic, topic-matched candidates are ordered first and style-only cards are
capped (STYLE_ONLY_CAP); results are never padded to six.

Local promotion is bounded to two places per candidate, never a global weighted
score. Missing/unknown fields are not matches or similarity evidence. Color rules
are deliberately coarse RGB/HSV rules, not a claim about background or pixels.
Explicit '明显/大面积/主色' requires each named color to occupy at least 10%.
Negation handles short clauses, not arbitrary Chinese grammar. Unknown concepts
remain in Brief.text for the upstream semantic search, never invented constraints.
"""
from __future__ import annotations

import colorsys
import math
import re
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass


TOPICS = {
    "建筑": ("建筑",), "展览": ("展览",), "食品": ("食品", "美食"),
    "咖啡": ("咖啡",), "人物": ("人物", "人像"), "植物": ("植物",),
    "海洋": ("海洋",), "音乐": ("音乐",), "电影": ("电影",),
    "服装": ("服装", "时尚"), "包装": ("包装",), "插画": ("插画",),
    "界面": ("界面", "UI"),
}
STYLES = {s: (s,) for s in ("复古", "极简", "现代", "瑞士", "手绘", "赛博", "日系", "拼贴", "装饰艺术")}
STYLES["复古"] += ("中古",)
STYLES["瑞士"] += ("瑞士国际主义",)
# Design artifacts: a brief naming one asks for references that are that artifact.
ARTIFACTS = {"展板": ("展板",), "海报": ("海报",), "封面": ("封面",)}
STYLE_ONLY_CAP = 2  # with a named topic, at most this many style-only cards
COLORS = {
    "米白": ("米白色", "米白", "奶油色"), "白": ("白色", "纯白"),
    "黑": ("黑色", "纯黑"), "灰": ("灰色", "灰调"),
    "红": ("红色",), "蓝": ("蓝色",), "绿": ("绿色",),
    "黄": ("黄色",), "橙": ("橙色",), "紫": ("紫色",),
}
LAYOUT = {
    ("columns", "multi"): ("多栏", "多列"),
    ("columns", "double"): ("双栏", "两栏", "双列"),
    ("columns", "single"): ("单栏", "单列"),
    ("columns", "grid"): ("网格", "网格化"),
    ("columns", "free"): ("自由排版",),
    ("density", "high"): ("高密度", "文字很多", "文字密集"),
    ("density", "low"): ("低密度",),
    ("density", "medium"): ("中等密度",),
    ("whitespace", "much"): ("留白很多", "留白多", "大面积留白", "很多留白"),
    ("whitespace", "little"): ("留白少", "少留白"),
    ("whitespace", "medium"): ("适中留白",),
    ("title_position", "top"): ("顶部标题", "标题在顶部", "大标题在顶部"),
    ("title_position", "bottom"): ("底部标题", "标题在底部", "底部一行大标题"),
    ("title_position", "left"): ("左侧标题", "左边大标题", "标题在左"),
    ("title_position", "right"): ("右侧标题", "右边大标题", "标题在右"),
    ("image_text_ratio", "text_dominant"): ("文字为主",),
    ("image_text_ratio", "image_dominant"): ("图片为主",),
    ("image_text_ratio", "balanced"): ("图文均衡",),
}
LABELS = {key: aliases[0] for key, aliases in LAYOUT.items()}
LABELS.update({("title_position", "middle"): "中部标题", ("title_position", "none"): "无标题"})
_NEGATIVE = re.compile(r"(不想要|不需要|不要|排除|避免|不含|不带|不用|别用|不想)")
_TRANSITION = re.compile(r"[，,。；;！!？?\n]|(?:改成|改为|换成|换为|但要|而要)")


@dataclass(frozen=True)
class Brief:
    text: str
    topics: tuple[str, ...] = ()
    layout: tuple[tuple[str, str], ...] = ()
    styles: tuple[str, ...] = ()
    colors: tuple[str, ...] = ()
    artifacts: tuple[str, ...] = ()
    excluded_topics: tuple[str, ...] = ()
    excluded_layout: tuple[tuple[str, str], ...] = ()
    excluded_styles: tuple[str, ...] = ()
    excluded_colors: tuple[str, ...] = ()
    excluded_artifacts: tuple[str, ...] = ()
    palette_only: bool = False
    layout_interest: bool = False
    content_interest: bool = False
    prominent_colors: bool = False


def _hits(text, vocabulary):
    """Longest aliases win: 米白色 must not also become 白色."""
    aliases = {alias: key for key, terms in vocabulary.items() for alias in terms}
    pattern = "|".join(re.escape(a) for a in sorted(aliases, key=lambda a: (-len(a), a)))
    return [aliases[m.group()] for m in re.finditer(pattern, text, re.I)]


def parse_brief(brief: str, previous: Brief | None = None) -> Brief:
    """Parse explicit vocabulary only; optionally apply a follow-up to a prior Brief."""
    if not isinstance(brief, str):
        raise TypeError("brief must be a string")
    names = ("topics", "layout", "styles", "colors", "artifacts")
    positive = {n: list(getattr(previous, n)) if previous else [] for n in names}
    negative = {n: list(getattr(previous, "excluded_" + n)) if previous else [] for n in names}
    # Expand a few unambiguous compound color phrases without matching 小红书.
    text = brief.replace("红蓝", "红色蓝色").replace("蓝白", "蓝色白色").replace("红黑", "红色黑色")
    for clause in _TRANSITION.split(text):
        chunks = _NEGATIVE.split(clause)
        is_negative = False
        for chunk in chunks:
            if _NEGATIVE.fullmatch(chunk):
                is_negative = True
                continue
            for name, vocab in zip(names, (TOPICS, LAYOUT, STYLES, COLORS, ARTIFACTS)):
                for term in _hits(chunk, vocab):
                    target, other = (negative, positive) if is_negative else (positive, negative)
                    if term not in target[name]:
                        target[name].append(term)
                    if term in other[name]:
                        other[name].remove(term)
    combined = (previous.text + "\n" if previous else "") + brief
    palette_only = previous.palette_only if previous else False
    if re.search(r"只(?:要|看|参考|想要).{0,4}(?:配色|颜色|色板)", brief):
        palette_only = True
    elif re.search(r"也要|还要|加上|不仅|不只", brief):
        palette_only = False
    return Brief(
        text=combined,
        **{n: tuple(positive[n]) for n in names},
        **{"excluded_" + n: tuple(negative[n]) for n in names},
        palette_only=palette_only,
        layout_interest=bool(re.search(r"排版|版式|展板|海报|封面", combined)),
        content_interest=bool(re.search(r"内容组织|信息组织|内容结构", combined)),
        prominent_colors=bool(re.search(r"明显|大面积|主色", combined)),
    )


def _mapping(value):
    return value if isinstance(value, Mapping) else {}


def _strings(value):
    if isinstance(value, str):
        return [value] if value else []
    return [v for v in value if isinstance(v, str)] if isinstance(value, (list, tuple)) else []


def _atoms(value, path):
    if isinstance(value, str):
        return [(path, value)] if value else []
    if isinstance(value, (list, tuple)):
        return [(f"{path}[{i}]", v) for i, v in enumerate(value) if isinstance(v, str) and v]
    return []


def _evidence(path, value):
    return {"field": path, "value": deepcopy(value)}


def _term_evidence(candidate, kind, term):
    a = _mapping(candidate.get("analyses"))
    style = _mapping(a.get("style"))
    fields = {
        "subject": [(a.get("subject"), "analyses.subject"), (a.get("description"), "analyses.description")],
        "style": [(style.get("tags"), "analyses.style.tags"), (style.get("style_note"), "analyses.style.style_note")],
        # Only identity labels: the image itself is recorded as this artifact (subject 海报,
        # tag 设计展板). Prose that merely mentions one (description / visible_text, e.g.
        # 关于方案展板的要求说明文档, 海报制作教程) does not prove the image is that artifact.
        "artifact": [(a.get("subject"), "analyses.subject"), (style.get("tags"), "analyses.style.tags")],
    }[kind]
    attribute = {"subject": "topics", "style": "styles", "artifact": "artifacts"}[kind]
    for value, path in fields:
        for atom_path, atom in _atoms(value, path):
            # Use the same negation parser on prose; '不是复古' is not evidence for 复古.
            normalized = atom.replace("不是", "不要").replace("非复古", "不要复古")
            parsed = parse_brief(normalized)
            if term in getattr(parsed, attribute):
                return _evidence(atom_path, atom)
    return None


def _layout_evidence(candidate):
    layout = _mapping(_mapping(candidate.get("analyses")).get("layout"))
    out = {}
    for field in ("title_position", "columns", "whitespace", "image_text_ratio", "density"):
        for path, value in _atoms(layout.get(field), f"analyses.layout.{field}"):
            if (field, value) in LABELS:
                out[(field, value)] = _evidence(path, value)
    return out


def _rgb(hex_value):
    if not isinstance(hex_value, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", hex_value):
        return None
    return tuple(int(hex_value[i:i + 2], 16) for i in (1, 3, 5))


def _color_matches(name, rgb):
    r, g, b = rgb
    h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
    if name == "米白":
        return r >= 195 and g >= 180 and b >= 145 and r >= g >= b and 8 <= r - b <= 75
    if name == "白":
        return min(rgb) >= 225 and max(rgb) - min(rgb) <= 20
    if name == "黑":
        return max(rgb) <= 55
    if name == "灰":
        return max(rgb) - min(rgb) <= 20 and 55 < max(rgb) < 225
    if s < .35 or v < .25:
        return False
    return {"红": h <= .06 or h >= .94, "蓝": .52 <= h <= .72,
            "绿": .20 <= h < .52, "黄": .12 <= h < .20,
            "橙": .06 < h < .12, "紫": .72 < h < .94}.get(name, False)


def _color_evidence(candidate, color):
    found, share = [], 0.0
    for i, entry, rgb, ratio in _measured_colors(candidate):
        if _color_matches(color, rgb):
            share += ratio
            found.extend((_evidence(f"colors[{i}].hex", entry["hex"]),
                          _evidence(f"colors[{i}].ratio", ratio)))
    return found, min(share, 1.0)


def _measured_colors(candidate):
    colors = candidate.get("colors")
    for i, entry in enumerate(colors if isinstance(colors, (list, tuple)) else []):
        entry = _mapping(entry)
        rgb, ratio = _rgb(entry.get("hex")), entry.get("ratio")
        if (rgb is not None and not isinstance(ratio, bool) and isinstance(ratio, (int, float))
                and math.isfinite(ratio) and 0 < ratio <= 1):
            yield i, entry, rgb, ratio


def _posts(candidate):
    values = list(candidate.get("post_ids") or [])
    if candidate.get("post_id") is not None:
        values.append(candidate["post_id"])
    for relation in candidate.get("post_images") or []:
        if isinstance(relation, Mapping) and relation.get("post_id") is not None:
            values.append(relation["post_id"])
    return list(dict.fromkeys(values))


def _excluded(candidate, spec):
    layout = _layout_evidence(candidate)
    return (any(key in layout for key in spec.excluded_layout)
            or any(_term_evidence(candidate, "subject", t) for t in spec.excluded_topics)
            or any(_term_evidence(candidate, "style", t) for t in spec.excluded_styles)
            or any(_term_evidence(candidate, "artifact", t) for t in spec.excluded_artifacts)
            or any(_color_evidence(candidate, c)[0] for c in spec.excluded_colors))


def _purposes(candidate, spec):
    a = _mapping(candidate.get("analyses"))
    layout = _layout_evidence(candidate)
    matches = 0
    purposes = []
    video = bool(candidate.get("is_video") or candidate.get("video") or a.get("is_video"))

    def add(kind, labels, evidence):
        if not evidence:
            return
        title = {"layout": "排版", "style": "风格", "palette": "配色", "subject": "主题/信息组织"}[kind]
        reason = f"推荐作{title}参考：分析记录为" + "、".join(labels) + "。"
        if kind == "palette":
            reason = "推荐作配色参考：Pillow 色块记录包含" + "、".join(labels) + "；占比以 evidence 中 ratio 为准。"
        if video:
            reason += "仅分析封面与简介。"
        existing = next((p for p in purposes if p["purpose"] == kind), None)
        if existing:
            existing["reason"] += reason
            existing["evidence"].extend(e for e in evidence if e not in existing["evidence"])
        else:
            purposes.append({"purpose": kind, "reason": reason, "evidence": evidence})

    color_hits = [(c, *_color_evidence(candidate, c)) for c in spec.colors]
    if spec.colors and all(ev and (share >= .10 if spec.prominent_colors else share > 0)
                           for _, ev, share in color_hits):
        add("palette", [c for c, _, _ in color_hits], [e for _, ev, _ in color_hits for e in ev])
        matches += len(spec.colors)
    if spec.palette_only:
        # Without a named color, expose valid measured colors, never infer a palette.
        if not spec.colors:
            ev = []
            for i, entry, _, ratio in _measured_colors(candidate):
                ev.extend((_evidence(f"colors[{i}].hex", entry["hex"]),
                           _evidence(f"colors[{i}].ratio", ratio)))
            add("palette", [e["value"] for e in ev if e["field"].endswith(".hex")], ev)
            matches += bool(ev)  # the brief explicitly asks for palette; measured colors answer it
        return purposes, matches

    layout_hits = [key for key in spec.layout if key in layout]
    matches += len(layout_hits)
    if layout_hits:
        add("layout", [LABELS[k] for k in layout_hits], [layout[k] for k in layout_hits])
    # A named artifact is a layout reference only if the candidate is recorded as that
    # artifact; its medium alone (typography/ui/mixed) no longer qualifies it.
    artifact_hits = [(t, _term_evidence(candidate, "artifact", t)) for t in spec.artifacts]
    artifact_hits = [(t, ev) for t, ev in artifact_hits if ev]
    matches += len(artifact_hits)
    if artifact_hits:
        useful = [] if spec.layout else [key for key in layout if key != ("title_position", "none")]
        add("layout", [t for t, _ in artifact_hits] + [LABELS[k] for k in useful],
            [ev for _, ev in artifact_hits] + [layout[k] for k in useful])

    style_hits = [(term, _term_evidence(candidate, "style", term)) for term in spec.styles]
    style_hits = [(term, ev) for term, ev in style_hits if ev]
    matches += len(style_hits)
    add("style", [s for s, _ in style_hits], [e for _, e in style_hits])
    topic_hits = [(term, _term_evidence(candidate, "subject", term)) for term in spec.topics]
    topic_hits = [(term, ev) for term, ev in topic_hits if ev]
    matches += len(topic_hits)
    add("subject", [s for s, _ in topic_hits], [e for _, e in topic_hits])
    if spec.content_interest:
        note = _mapping(a.get("layout")).get("layout_note")
        # Prose is preserved as evidence; the template makes no narrative-quality claim.
        if isinstance(note, str) and note.strip() and (topic_hits or not spec.topics):
            add("subject", ["有信息组织说明"], [_evidence("analyses.layout.layout_note", note)])
    return purposes, matches


def _similar(a, b):
    """Conservative three-way similarity; unknowns cannot form a duplicate group."""
    la, lb = set(_layout_evidence(a)), set(_layout_evidence(b))
    required = {"columns", "whitespace", "density"}
    if not all(any(k[0] == field for k in la) and any(k[0] == field for k in lb) for field in required):
        return False
    if la != lb:
        return False
    sa = set(_strings(_mapping(_mapping(a.get("analyses")).get("style")).get("tags"))) - {"unknown"}
    sb = set(_strings(_mapping(_mapping(b.get("analyses")).get("style")).get("tags"))) - {"unknown"}
    if not sa or sa != sb:
        return False

    def dominant(c):
        measured = [(ratio, rgb) for _, _, rgb, ratio in _measured_colors(c)]
        return max(measured, default=(0, None))[1]

    ca, cb = dominant(a), dominant(b)
    return ca is not None and cb is not None and sum((x - y) ** 2 for x, y in zip(ca, cb)) <= 24 ** 2


def _unmet(candidate, spec):
    """Report explicit requirements not evidenced by this image, without inventing absence."""
    unmet = []
    layout = _layout_evidence(candidate)
    if not spec.palette_only:
        unmet.extend({"kind": "layout", "value": LABELS[k]} for k in spec.layout if k not in layout)
        unmet.extend({"kind": "layout", "value": t} for t in spec.artifacts
                     if not _term_evidence(candidate, "artifact", t))
        unmet.extend({"kind": "style", "value": t} for t in spec.styles
                     if not _term_evidence(candidate, "style", t))
        unmet.extend({"kind": "subject", "value": t} for t in spec.topics
                     if not _term_evidence(candidate, "subject", t))
    for color in spec.colors:
        ev, share = _color_evidence(candidate, color)
        if not ev or (spec.prominent_colors and share < .10):
            unmet.append({"kind": "palette", "value": color})
    return unmet


def recommend(brief: str | Brief, candidates: Sequence[Mapping], *, limit: int = 6) -> dict:
    """Return {brief, recommendations, notices}; only supported purposes are emitted.

    semantic_score is ordering input, never a probability or relevance threshold.
    Missing a named requirement produces a notice, not invented matches. Entirely
    unsupported candidates are omitted even if their semantic score is high.
    """
    spec = parse_brief(brief) if isinstance(brief, str) else brief
    if not isinstance(spec, Brief):
        raise TypeError("brief must be str or Brief")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 0 <= limit <= 6:
        raise ValueError("limit must be between 0 and 6")
    ranked = []
    for c in candidates:
        if not isinstance(c, Mapping) or "image_id" not in c:
            raise ValueError("candidate must have image_id")
        score = c.get("semantic_score")
        if isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score):
            raise ValueError("semantic_score must be finite")
        ranked.append(c)
    ranked.sort(key=lambda c: (-c["semantic_score"], str(c["image_id"])))
    merged = {}
    for rank, c in enumerate(ranked[:20], 1):
        digest = c.get("sha256")
        key = ("sha", digest) if digest else ("id", c["image_id"])
        source = {"image_id": c["image_id"], "post_ids": _posts(c)}
        if key in merged:
            merged[key]["sources"].append(source)
            merged[key]["post_ids"] = list(dict.fromkeys(merged[key]["post_ids"] + source["post_ids"]))
            continue
        merged[key] = {"candidate": c, "semantic_rank": rank, "post_ids": source["post_ids"].copy(), "sources": [source]}
    eligible = []
    for entry in merged.values():
        c = entry["candidate"]
        if _excluded(c, spec):
            continue
        purposes, hits = _purposes(c, spec)
        if purposes and hits > 0:  # zero explicit requirement hits never qualify
            topic = any(p["purpose"] == "subject" for p in purposes) and bool(spec.topics)
            style_only = {p["purpose"] for p in purposes} == {"style"}
            eligible.append({**entry, "purposes": purposes, "hits": hits, "topic": topic, "style_only": style_only})
    # Each original candidate may rise at most two slots, only on explicit hits.
    for entry in list(eligible):
        index = eligible.index(entry)
        for _ in range(2):
            if index == 0 or entry["hits"] <= eligible[index - 1]["hits"]:
                break
            eligible[index - 1], eligible[index] = eligible[index], eligible[index - 1]
            index -= 1
    if spec.topics:
        # Topic-matched candidates first (stable; semantic order kept within each group).
        eligible = [e for e in eligible if e["topic"]] + [e for e in eligible if not e["topic"]]
    selected = []
    counts = {}
    for per_post in (1, 2):
        for entry in eligible:
            if len(selected) >= limit:
                break
            if entry in selected or any(counts.get(p, 0) >= per_post for p in entry["post_ids"]):
                continue
            if (spec.topics and entry["style_only"]
                    and sum(e["style_only"] for e in selected) >= STYLE_ONLY_CAP):
                continue
            if any(_similar(entry["candidate"], other["candidate"]) for other in selected):
                continue
            selected.append(entry)
            for p in entry["post_ids"]:
                counts[p] = counts.get(p, 0) + 1
    recommendations = []
    for entry in selected:
        c = entry["candidate"]
        recommendations.append({"image_id": c["image_id"], "semantic_score": c["semantic_score"],
                                "semantic_rank": entry["semantic_rank"], "sha256": c.get("sha256"),
                                "post_ids": entry["post_ids"], "sources": entry["sources"],
                                "purposes": entry["purposes"], "unmet_requirements": _unmet(c, spec)})
    notices = []
    if recommendations and all(r["unmet_requirements"] for r in recommendations):
        notices.append("当前推荐没有逐项确认全部明确要求；每张图列出尚未确认的要求。")
    if spec.topics and not any(p["purpose"] == "subject" for r in recommendations for p in r["purposes"]):
        notices.append("当前候选未确认主题匹配；其他用途仅作间接参考，不代表整个收藏库没有。")
    if len(recommendations) < limit:
        notices.append("有证据且去重后的候选不足，不补入无依据的图片。")
    if not recommendations:
        notices.append("当前候选未找到可确认用途的参考；请调整描述或扩大上游候选。")
    return {"brief": spec, "recommendations": recommendations, "notices": notices}
