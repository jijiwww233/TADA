"""Local discovery over saved text and measured image palettes."""
import colorsys
import os
import re
from functools import lru_cache

from PIL import Image, ImageOps

from . import config, palette
from .embedding.search import SearchHit
from .embedding.search_application import attach_evidence, hydrate_hits
from .recommendation import COLORS
from .recommendation_service import load_candidate_context

HUE_FAMILIES = (15, 42, 68, 164, 200, 251, 324)
HUE_BUCKETS = tuple(next((i for i, boundary in enumerate(HUE_FAMILIES) if hue * 360 / 256 < boundary), 0)
                    for hue in range(256))

LIMIT = 24
COLOR_PAIRS = {"蓝紫": "蓝色、紫色", "黑白": "黑色、白色", "红黑": "红色、黑色"}
SPOKEN_COLORS = "红橙黄绿蓝紫黑白灰"
COLOR_TERMS = sorted(set(COLOR_PAIRS) | {term for terms in COLORS.values() for term in terms}, key=len, reverse=True)
HUE_LIMITS = {
    "#ff0000": (345, 17), "#ffa500": (16, 45), "#ffff00": (41, 72),
    "#008000": (70, 166), "#0000ff": (184, 258), "#800080": (251, 325),
    # Search-only pigments: the brief vocabulary stops at ten colours, but a query
    # may name pink or cyan as a hard requirement. Hue tolerance, never RGB distance,
    # so a saturated pink/teal neighbour still counts.
    "#ff9ec4": (315, 355), "#00a8a8": (165, 205), "#ffb84d": (24, 70),
    # 蓝绿 compound colours are teal/mint hues; RGB distance rejected pale mint.
    "#2e8b8b": (150, 200),
}

# A colour family may name a pale palette colour: 浅薄荷 #d7e5e3 has saturation .06, so
# the generic chromatic cut (sat >= .16) would reject a real mint. Only this teal family
# relaxes the two floors; every other hue keeps the strict values.
SAT_FLOORS = {"#2e8b8b": (0.05, 0.55)}  # (min saturation, min value)


def normalize_colors(query):
    for pair, full in COLOR_PAIRS.items():
        query = query.replace(pair, full)
    return re.sub(rf"(?<![0-9A-Fa-f])(?:偏|浅|深|暗|淡|亮)?([{SPOKEN_COLORS}])(?:\1+(?:的)?|调|的|一点|些|系|(?=色|$))",
                  lambda match: match[1] if query[match.end():].startswith("色") else match[1] + "色", query)


def color_affinity(rgb, target):
    hue, sat, value = colorsys.rgb_to_hsv(*(c / 255 for c in rgb))
    low_high = HUE_LIMITS.get(target.hex)
    if low_high:
        angle = hue * 360
        low, high = low_high
        inside = low <= angle <= high if low < high else angle >= low or angle <= high
        min_sat, min_value = SAT_FLOORS.get(target.hex, (.16, .16))
        return (0.65 + 0.35 * sat) if inside and sat >= min_sat and value >= min_value else 0
    if target.hex == "#000000":
        return 1.0 - value if value <= .37 else 0
    if target.hex in {"#ffffff", "#f5f0e6"}:
        return value if sat <= .20 and value >= .74 else 0
    if target.hex == "#808080":
        return 1.0 - sat if sat <= .20 and .25 <= value <= .80 else 0
    wanted = palette.rgb(target.hex)
    if wanted is None:
        return 0
    distance = sum((actual - expected) ** 2 for actual, expected in zip(rgb, wanted)) ** .5
    return max(0, 1 - distance / 105)



# Compound palette words the general parser does not know. Kept here rather than
# in the recommendation vocabulary so search-only colours do not leak into briefs.
COMPOUND_COLOR_TARGETS = {
    "蓝绿": "#2e8b8b",
    "蓝白": "#4a90d9",
    "黄棕": "#b8860b",
    "棕": "#8b5a2b",
}


def color_targets(query):
    targets = list(palette.targets(normalize_colors(query) if isinstance(query, str) else query))
    if isinstance(query, str):
        for word, hex_value in COMPOUND_COLOR_TARGETS.items():
            if word in query:
                targets.append(palette.Target(hex_value, "为主" in query))
    merged = {}
    for target in targets:
        merged[target.hex] = palette.Target(target.hex, target.main or merged.get(target.hex, target).main)
    return tuple(merged.values())



# Search-only pigments absent from both COLORS and COMPOUND_COLOR_TARGETS. Only full
# colour words count, so 青年/粉丝 style compounds never become a hard requirement.
SEARCH_COLOR_WORDS = {
    "粉": ("#ff9ec4", ("粉色", "粉红", "粉调", "粉系")),
    "青": ("#00a8a8", ("青色", "青调", "青系", "藏青", "靛青")),
    # 暖黄 is an amber, not pure yellow: the warm lamp glow extracts as hue 28-36, so a
    # #ffff00 requirement alone would reject the very lamp the query names.
    "暖黄": ("#ffb84d", ("暖黄", "鹅黄", "米黄")),
}
COLOR_MIN_SHARE = float(os.environ.get("SEARCH_COLOR_MIN_SHARE", "0.10"))


def search_color_targets(query):
    """Concrete colour words a result's dominant palette must contain (any one of them).

    Reuses `color_targets` (brief vocabulary + compound palette words) and adds the
    search-only pigments. Vague colour talk (颜色很多 / 暖色 / 深色 / 彩色) yields no
    target: only a named colour may become a hard requirement.
    """
    if not isinstance(query, str):
        return ()
    targets = list(color_targets(query))
    for hex_value, forms in SEARCH_COLOR_WORDS.values():
        if any(form in query for form in forms):
            targets.append(palette.Target(hex_value, False))
    merged = {}
    for target in targets:
        merged[target.hex] = palette.Target(target.hex, target.main or merged.get(target.hex, target).main)
    return tuple(merged.values())


def palette_covers(conn, image_ids, targets, min_share=COLOR_MIN_SHARE):
    """Image ids whose dominant swatches cover one of the target colour families.

    A swatch counts when its hue affinity times the swatch share reaches min_share, so
    a stray 2% accent cannot satisfy a colour requirement. Rows without an extracted
    palette are returned as covered (unknown, not proven wrong), so a missing palette
    never empties the wall by itself.
    """
    ids = [int(image_id) for image_id in image_ids]
    if not targets or not ids:
        return set(ids)
    marks = ",".join("?" for _ in ids)
    best = {}
    seen = set()
    for row in conn.execute(
            f"SELECT image_id,hex,ratio FROM colors WHERE image_id IN ({marks})", ids):
        image_id = int(row["image_id"])
        seen.add(image_id)
        rgb = palette.rgb(row["hex"])
        try:
            share = float(row["ratio"] or 0)
        except (TypeError, ValueError):
            continue
        if not rgb or share <= 0:
            continue
        for target in targets:
            affinity = color_affinity(rgb, target)
            if affinity and affinity * share > best.get(image_id, 0):
                best[image_id] = affinity * share
    return {image_id for image_id in ids if best.get(image_id, 0) >= min_share} | (set(ids) - seen)


def palette_matches(conn, targets):
    if not targets:
        return {}
    values = {}
    for row in conn.execute("SELECT image_id,hex,ratio FROM colors ORDER BY image_id,rank"):
        values.setdefault(row["image_id"], []).append({"hex": row["hex"], "ratio": row["ratio"]})
    scored = {}
    for image_id, colors in values.items():
        swatches = [(palette.rgb(row["hex"]), row["ratio"]) for row in colors]
        matches = [sum(color_affinity(rgb, target) * share for rgb, share in swatches if rgb and share > 0)
                   for target in targets]
        if max(matches, default=0) >= .06:
            scored[image_id] = (sum(matches) + min(matches)) / (len(matches) + 1)
    return dict(sorted(scored.items(), key=lambda it: (-it[1], it[0])))


def color_only_query(query):
    rest = re.sub(r"#[0-9a-fA-F]{6}|" + "|".join(map(re.escape, COLOR_TERMS)), "", normalize_colors(query.strip()))
    return not re.sub(r"[色\s、，,和与]+", "", rest)


def with_colors(conn, query, found):
    scores = palette_matches(conn, color_targets(query))
    if not scores:
        return found
    hits = found["items"]
    if hits and not color_only_query(query):
        ordered = sorted(hits, key=lambda hit: -(hit["score"] + scores.get(hit["image_id"], 0)))
        return {**found, "items": ordered}
    items = hydrate_hits(conn, [SearchHit(i, score) for i, score in list(scores.items())[:LIMIT]])
    attach_evidence(items, palette_score=scores)
    return {"status": "ok", "degraded": True, "items": items}


COLOR_RICH = re.compile(
    r"(?:(?:颜色|色彩)(?:很|挺|比较|特别)?(?:多|丰富)|很多颜色|多色|五颜六色|彩色(?:的图)?|多彩|花花绿绿|缤纷)"
)
SHORT_TOPICS = (
    ("海", "大海", "海边", "海浪", "海洋", "波浪", "浪花"),
    ("山", "高山", "山峰", "山脉"),
    ("花", "花朵", "鲜花", "花卉", "雏菊"),
    ("猫", "小猫", "猫咪", "家猫"),
    ("狗", "小狗", "狗狗", "犬类"),
    ("人", "人物", "人像", "行人"),
    ("字", "文字", "字体", "书法"),
    ("楼", "大楼", "楼房", "建筑"),
    ("云", "云朵", "云层", "天空"),
    ("天", "天空", "蓝天", "白云"),
    ("夜", "夜晚", "夜景", "夜色"),
)
SHORT_EXPANSIONS = {word: (word, *(alias for alias in topic if alias != word))
                    for topic in SHORT_TOPICS for word in topic if len(word) <= 2}
SCENES = (
    (
        re.compile(r"(?:海|大海|海洋|海浪|海边|海滨|海滩|沙滩|海岸(?:线)?)(?:的?(?:图|照片|风景))?"),
        ("海岸沙滩 海浪拍打海岸 海边风景照片", "海岸线或海边沙滩，可见海水和浪"),
        ("岩石纹理特写，没有海水", "插画海报 书籍排版", "森林绿植 建筑废墟", "抽象渐变色块平面设计"),
    ),
)


def representative_hits(items, limit=LIMIT):
    """Keep the highest scoring image from each post on the search wall."""
    posts_seen = set()
    results = []
    for hit in items:
        post_ids = {source["post_id"] for source in hit["sources"]}
        if post_ids & posts_seen:
            continue
        posts_seen.update(post_ids)
        results.append(hit)
        if len(results) == limit:
            break
    return results


@lru_cache(maxsize=1024)
def color_spread(path, digest):
    """Use a thumbnail of the image, since five dominant swatches hide small accents."""
    try:
        with Image.open(path) as image:
            image.draft("RGB", (160, 160))
            image.thumbnail((112, 112))
            rgb = ImageOps.exif_transpose(image).convert("RGBA")
            background = Image.new("RGBA", rgb.size, "white")
            background.alpha_composite(rgb)
            hsv = background.convert("RGB").convert("HSV")
    except (OSError, ValueError):
        return 0
    # Circular red/pink family avoids counting the hue wrap as extra colors.
    counts = [0] * len(HUE_FAMILIES)
    channels = hsv.tobytes()
    for hue, saturation, value in zip(channels[::3], channels[1::3], channels[2::3]):
        if saturation >= 78 and value >= 70:
            counts[HUE_BUCKETS[hue]] += 1
    pixels = hsv.width * hsv.height
    shares = sorted((count / pixels for count in counts if count >= pixels * .035), reverse=True)
    if len(shares) < 3 or sum(shares) < .25 or shares[2] < shares[0] * .13:
        return 0
    # Hue count leads the ranking; coverage and balance only break ties.
    return len(shares) + .5 * sum(min(share, .18) for share in shares) - .1 * shares[0]


def diverse_colors(conn):
    scores = {}
    for row in conn.execute("SELECT id,file_path,sha256 FROM images"):
        score = color_spread(config.data_dir() / row["file_path"], row["sha256"])
        if score:
            scores[row["id"]] = score
    return scores


def search_subject(query):
    return re.sub(r"[，。、！？,.!?\s]+$", "", query.strip())


def short_scores(query, similarity):
    variants = SHORT_EXPANSIONS.get(search_subject(query))
    if not variants:
        return {}
    return {image_id: .6 * max(scores) + .4 * (sum(scores) / len(scores))
            for image_id, scores in similarity.text_evidence(variants).items()}


def visual_scores(query, similarity):
    subject = search_subject(query)
    scene = next((scene for scene in SCENES if scene[0].fullmatch(subject)), None)
    channel = "scene" if scene else "semantic"
    try:
        if scene:
            _, positive, negative = scene
            evidence = similarity.text_evidence((*positive, *negative))
            expanded = short_scores(subject, similarity)
            scores = {}
            for image_id, values in evidence.items():
                strength = sum(values[:len(positive)]) / len(positive)
                margin = strength - max(values[len(positive):])
                if strength >= .30 and margin >= .012:
                    scores[image_id] = margin + .25 * strength + .15 * expanded.get(image_id, 0)
            return scores, channel
        is_expanded = subject in SHORT_EXPANSIONS
        raw = short_scores(subject, similarity) if is_expanded else dict(similarity.search_text(subject, top_n=None))
        values = sorted(raw.values())
        if not values or values[-1] < (.34 if is_expanded else .39):
            return {}, channel
        floor = max(values[-1] - .038, values[len(values) // 2] + .024)
        if is_expanded:
            floor = min(floor, values[-1])  # keep the best match in small libraries
        return {image_id: score for image_id, score in raw.items() if score >= floor}, channel
    except (RuntimeError, ImportError, OSError, ValueError):
        return {}, channel


def visual_hits(conn, query, similarity):
    if COLOR_RICH.fullmatch(search_subject(query)):
        return hydrate_scores(conn, diverse_colors(conn), evidence="palette_score"), "palette"
    scores, channel = visual_scores(query, similarity)
    # Only the semantic route's score is a raw text/image cosine that the relevance gate
    # may compare across queries; a scene margin is a within-query decision, not a
    # similarity, so it is deliberately left without absolute evidence.
    return hydrate_scores(conn, scores, evidence="local_cos" if channel == "semantic" else None), channel


def hydrate_scores(conn, scores, evidence=None):
    ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))[:120]
    items = hydrate_hits(conn, [SearchHit(image_id, score) for image_id, score in ranked])
    if evidence:
        attach_evidence(items, **{evidence: dict(ranked)})
    return items


def local_columns(conn, effective, found, color_need=None, excluded=()):
    scores = palette_matches(conn, color_targets(color_need or effective))
    ranked = {hit["image_id"]: hit for hit in found["items"] if hit["image_id"] not in excluded}
    palette_ids = list(ranked) if ranked and not scores else [i for i in scores if i not in excluded][:LIMIT]
    extra = [SearchHit(i, scores[i]) for i in palette_ids if i not in ranked]
    images = {**ranked, **{hit["image_id"]: hit for hit in hydrate_hits(conn, extra)}}
    context = load_candidate_context(conn, list(images))
    used = set()
    columns = []
    for purpose, label, candidates in (("layout", "排版", ranked), ("palette", "配色", palette_ids), ("style", "风格", ranked)):
        rows = []
        posts_seen = set()
        for recall_rank, image_id in enumerate(candidates, 1):
            hit = images.get(image_id)
            ctx = context.get(image_id)
            if not hit or not ctx:
                continue
            sources = hit["sources"]
            post_ids = [source["post_id"] for source in sources]
            if any(post_id in posts_seen for post_id in post_ids):
                continue
            if purpose == "palette":
                swatches = ", ".join(s["hex"] for s in ctx["colors"][:3])
                reason = f"图中占比最高的颜色有 {swatches}。" if swatches else ""
            elif purpose == "layout":
                width, height = hit["width"], hit["height"]
                reason = ""
                if width and height:
                    direction = "横幅" if width > height else "竖幅" if width < height else "方幅"
                    reason = f"可参考原图的{direction}比例。"
            else:
                style = ctx["analyses"].get("style") or {}
                tags = style.get("tags") or []
                title = next((source["title"] for source in sources if source["title"]), None)
                reason = "风格标签为「" + "、".join(tags[:3]) + "」。" if tags else f"相关收藏「{title}」。" if title else ""
            if not reason:
                continue
            rows.append({"image_id": image_id, "rank": len(rows) + 1, "recall_rank": recall_rank,
                         "media_scope": "cover_and_description" if hit["is_video"] else "image",
                         "post_ids": post_ids, "purpose": purpose, "reference_point": reason, "unreviewed": True})
            posts_seen.update(post_ids)
            used.add(image_id)
            if len(rows) == 4:
                break
        columns.append({"purpose": purpose, "label": label, "status": "ok" if rows else "no_candidates",
                        "recall_source": "local_library", "reviewed_ids": [], "items": rows,
                        "usage": {"requests": 0, "prompt_tokens": None, "completion_tokens": None, "latency_ms": 0}})
    return {"status": "ok", "effective_query": effective, "columns": columns,
            "images": [hit for i, hit in images.items() if i in used],
            "notices": ["按本地收藏资料与主色推荐。"]}
