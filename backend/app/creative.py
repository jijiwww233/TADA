"""Creative recommendation, original plan: three columns (排版 / 配色 / 风格).

Per request:
  1. effective brief (previous/current handling + exclusions, recommendation_query)
  2. ONE query embedding, compared against the per-dimension indexes
       layout column  -> layout_v1 Top20
       style column   -> style_v1 Top20
       palette column -> Pillow colors (hex + ratio) when the brief names colors;
                         otherwise description_v1 Top20 (task relevance; the vision
                         review then judges the palette itself)
     Layout fields only rank; nothing is hard-filtered by them. Topic is never required:
     a food poster can be a layout reference for an architecture board.
  3. per column: SHA dedupe, same-post diversity, video scope, first REVIEW_CANDIDATES
  4. per column ONE vision request with the real images: pick at most PICKS_PER_COLUMN,
     rank them, one sentence "参考它的哪里" each. Ids outside the batch are dropped.
     Fewer useful images -> fewer cards; never padded.

Read-only on the library DB. Old Day6 rule core (recommendation.py) is not on this path.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import httpx

from . import config, demo_cache, vision, palette
from .reference_value import eligible_ids
from . import recommendation_feedback as feedback
from .embedding import dimensions
from .embedding.client import EmbeddingClient, EmbeddingSettings
from .embedding.search import EmbeddingItem, SearchHit, rank
from .embedding.search_application import hydrate_hits, readonly_connection
from .embedding.service import blob_to_vector
from .recommendation import COLORS, Brief, _excluded, _posts, parse_brief
from .recommendation_query import resolve_brief

log = logging.getLogger(__name__)

RECALL_TOP = 30
REVIEW_CANDIDATES = 10
PICKS_PER_COLUMN = 5
REVIEW_MAX_SIDE = 768  # long side of each image sent for review; the file is not touched

COLUMNS = {
    "layout": {"label": "排版", "scheme": "layout_v1",
               "goal": "排版参考：版面结构、网格与分栏、信息层级、标题位置、留白与密度、图文关系。"},
    "palette": {"label": "配色", "scheme": "description_v1",
                "goal": "配色参考：主色与辅助色的搭配、颜色比例、明度与对比、整体色调是否适合这个任务。"},
    "style": {"label": "风格", "scheme": "style_v1",
              "goal": "风格参考：视觉风格、质感与媒介、字体气质、整体调性。"},
}

NEED_PROMPT = """你是设计任务的拆解助手。把用户的创作需求拆成三个互不混用的检索目标。
1. layout_need：这个任务需要什么样的版面结构（标题位置、分栏/网格、图文关系、留白与密度、信息层级）。即使任务没有直接说排版，也要根据交付物类型（海报/展板/界面等）给出最可能的版式方向，写一句具体中文，不要留空。
2. style_need：这个任务需要什么样的视觉风格、质感与调性。即使没有直接说风格，也要根据任务主题与交付物给出一般性方向；不要编造用户没提到的具体流派名。
3. color_need：只列用户明确提到或同义的颜色，用 hex 表示并标 "为主" 或 "点缀"；用户没说颜色就返回空数组。不要新增颜色词。

只输出严格 JSON：
{{"layout_need":"...","color_need":[{{"hex":"#f5f0e6","role":"为主"}}],"style_need":"..."}}
每项一句简短中文；不要输出其他字段。

用户需求：
{brief}"""


REVIEW_PROMPT = """你是设计专业学生的参考图筛选助手。用户当前的创作任务是：
「{brief}」

这一栏只负责：{goal}

下面按顺序给出 {n} 张候选图，每张图前有它的编号（image_id）。请直接看图判断每张图在“{label}”这一点上对这个任务有没有参考价值。
- 主题不同也可以作为参考（例如食品海报的排版可以给建筑展板参考），只看“{label}”这一点是否值得借鉴。
- 宁缺毋滥：只有确实能借鉴的才选，最多 {k} 张；如果只有 1-2 张站得住脚，就只返回 1-2 张。凑满 {k} 张但说不出具体借鉴点的，不要选。
- 每张选中的图给一句具体的“参考它的哪里”：必须指出图里的具体元素（如标题位置、分栏比例、色块占比、字体气质），并说明它怎么对应到当前任务；不要写“整体感觉不错”“很有参考价值”这类空话。
- 排除：手机/网页/软件界面截图、纯文字或聊天记录截图、表情包、与任务无关的广告图，除非任务明确就是要界面或截图参考。配色栏看的是实际色块与占比，白色/浅色背景占大面积本身不算配色参考。
- image_id 只能用下面给出的编号。
{video_note}
只输出 JSON，格式：
{{"picks": [{{"image_id": 编号, "rank": 1, "useful": true, "reference_point": "参考它的哪里"}}]}}"""


@dataclass
class Candidate:
    image_id: int
    score: float
    recall_rank: int
    post_ids: list
    is_video: bool
    file_path: str
    sha256: str


def _fallback_needs(final: Brief) -> dict[str, str]:
    layout = "、".join(label for _, label in getattr(final, "layout", ()))
    styles = "、".join(getattr(final, "styles", ()))
    colors = "、".join(getattr(final, "colors", ()))
    return {
        "layout_need": f"排版：{layout}" if layout else "",
        "color_need": f"配色：{colors}" if colors else "",
        "style_need": f"风格：{styles}" if styles else "",
    }


def parse_needs(text: str | None, fallback: dict[str, str]) -> dict[str, str]:
    """Strictly parse three optional strings; malformed output falls back locally."""
    if not text:
        return dict(fallback)
    raw = text.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1] if "\n" in raw else ""
        raw = raw.rstrip().removesuffix("```")
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return dict(fallback)
    if not isinstance(data, dict):
        return dict(fallback)
    out = {}
    for key in ("layout_need", "color_need", "style_need"):
        value = data.get(key)
        out[key] = (value if key == "color_need" and isinstance(value, list) else
                    value.strip() if isinstance(value, str) else "")
    if not any(out.values()):
        return dict(fallback)
    return out


def decompose_needs(brief: str, settings: dict, *, client: httpx.Client | None = None,
                    sleep=time.sleep) -> dict:
    """Exactly one text-only model request to split layout/color/style needs."""
    if not (settings.get("base_url") and settings.get("api_key") and settings.get("vision_model")):
        raise vision.VisionError("config", "未配置文本模型")
    calls = vision.Calls()
    own = client is None
    client = client or httpx.Client()
    started = time.perf_counter()
    try:
        payload = {"model": settings["vision_model"], "temperature": 0.1,
                   "messages": [{"role": "user", "content": NEED_PROMPT.format(brief=brief)}]}
        # Preserve the accepted baseline by default. The faster decomposition
        # variant did not pass dev non-regression; retain it only as an opt-in.
        if settings.get('fast_recommendation_needs'):
            payload['enable_thinking']=settings.get('enable_thinking',False)
        body = vision._post(client, settings, payload, calls, sleep)
        calls.add_usage(body.get("usage") if isinstance(body, dict) else None)
        try:
            text = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            text = None
        return {"needs": parse_needs(text, _fallback_needs(parse_brief(brief))),
                "usage": {"requests": calls.count, "prompt_tokens": calls.prompt_tokens,
                          "completion_tokens": calls.completion_tokens,
                          "latency_ms": round((time.perf_counter() - started) * 1000)}}
    finally:
        if own:
            client.close()


def _color_terms(spec: Brief) -> tuple[str, ...]:
    return tuple(c for c in spec.colors if c in COLORS)


def palette_recall(conn: sqlite3.Connection, colors, prominent: bool = False, eligible=None, score_adjust=None) -> list[tuple[int, float]]:
    """All target colors contribute nearest-swatch similarity × share, soft main roles."""
    desired = (palette.targets("", colors, prominent) if colors and isinstance(colors[0], str)
               else colors)
    rows = {}
    for r in conn.execute("""SELECT c.image_id,c.hex,c.ratio FROM colors c
                             JOIN analyses a ON a.image_id=c.image_id AND a.status='done'
                             ORDER BY c.image_id,c.rank"""):
        rows.setdefault(r["image_id"], []).append({"hex":r["hex"],"ratio":r["ratio"]})
    scored = [(i, palette.score(swatches, desired)) for i, swatches in rows.items() if eligible is None or i in eligible]
    scored = [(i, score_adjust(i,score) if score_adjust else score) for i, score in scored if score > 0]
    return sorted(scored, key=lambda x: (-x[1], x[0]))[:RECALL_TOP]


def dimension_recall(conn: sqlite3.Connection, settings: EmbeddingSettings, scheme: str,
                     query_vector: list[float], eligible=None, score_adjust=None) -> list[tuple[int, float]]:
    spec = dimensions.spec_for(settings, scheme)
    rows = dimensions.load_rows(conn, spec)
    if not rows:
        return []
    items = [EmbeddingItem(image_id=r.image_id, vector=blob_to_vector(r.vector, spec.dimensions), payload=None)
             for r in rows if eligible is None or r.image_id in eligible]
    hits=[(h.image_id,h.score) for h in rank(query_vector,items,top_n=len(items) if score_adjust else RECALL_TOP)]
    if score_adjust:hits=sorted(((i,score_adjust(i,v)) for i,v in hits),key=lambda x:(-x[1],x[0]))[:RECALL_TOP]
    return hits


def _context(conn: sqlite3.Connection, ids: list[int]) -> dict[int, dict]:
    """Bulk: image file/sha, post relations and video flag, analyses for exclusions."""
    if not ids:
        return {}
    marks = ",".join("?" * len(ids))
    out = {r["id"]: {"file_path": r["file_path"], "sha256": r["sha256"], "post_images": [], "is_video": False,
                     "analyses": {}, "colors": []}
           for r in conn.execute(f"SELECT id, file_path, sha256 FROM images WHERE id IN ({marks})", ids)}
    for r in conn.execute(f"""SELECT pi.image_id, pi.post_id, p.is_video FROM post_images pi
                              JOIN posts p ON p.id = pi.post_id WHERE pi.image_id IN ({marks})""", ids):
        if r["image_id"] in out:
            out[r["image_id"]]["post_images"].append({"post_id": r["post_id"]})
            out[r["image_id"]]["is_video"] |= bool(r["is_video"])
    for r in conn.execute(f"""SELECT image_id, subject, layout_json, style_json, description FROM analyses
                              WHERE status = 'done' AND image_id IN ({marks})""", ids):
        if r["image_id"] in out:
            load = lambda v: json.loads(v) if v else None  # noqa: E731
            out[r["image_id"]]["analyses"] = {"subject": load(r["subject"]), "layout": load(r["layout_json"]),
                                              "style": load(r["style_json"]), "description": r["description"]}
    for r in conn.execute(f"SELECT image_id, hex, ratio FROM colors WHERE image_id IN ({marks}) ORDER BY image_id, rank", ids):
        if r["image_id"] in out:
            out[r["image_id"]]["colors"].append({"hex": r["hex"], "ratio": r["ratio"]})
    return out


def shortlist(recalled: list[tuple[int, float]], context: dict[int, dict], spec: Brief) -> list[Candidate]:
    """SHA dedupe, exclusions, same-post diversity (one per post first, then two),
    then the first REVIEW_CANDIDATES. Order otherwise stays the recall order."""
    seen_sha, ranked = set(), []
    for position, (image_id, score) in enumerate(recalled, 1):
        ctx = context.get(image_id)
        if ctx is None or ctx["sha256"] in seen_sha or _excluded(ctx, spec):
            continue
        seen_sha.add(ctx["sha256"])
        ranked.append(Candidate(image_id, score, position, _posts(ctx), ctx["is_video"], ctx["file_path"], ctx["sha256"]))
    chosen, counts = [], {}
    for per_post in (1, 2):
        for c in ranked:
            if len(chosen) >= REVIEW_CANDIDATES:
                break
            if c in chosen or any(counts.get(p, 0) >= per_post for p in c.post_ids):
                continue
            chosen.append(c)
            for p in c.post_ids:
                counts[p] = counts.get(p, 0) + 1
    order = {c.image_id: i for i, c in enumerate(ranked)}
    return sorted(chosen, key=lambda c: order[c.image_id])


def parse_review(text: str | None, candidate_ids: list[int]) -> list[dict]:
    """Model JSON -> at most PICKS_PER_COLUMN picks, only ids that were sent, only useful
    ones with a non-empty reference_point, in the model's rank order. Raises ValueError."""
    if not text or not text.strip():
        raise ValueError("复看返回为空")
    raw = text.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1] if "\n" in raw else ""
        raw = raw.rstrip().removesuffix("```")
    data = json.loads(raw)
    picks = data.get("picks") if isinstance(data, dict) else None
    if not isinstance(picks, list):
        raise ValueError("复看结果缺少 picks 数组")
    allowed, out, used = set(candidate_ids), [], set()
    for i, p in enumerate(picks):
        if not isinstance(p, dict):
            continue
        image_id, point = p.get("image_id"), p.get("reference_point")
        if isinstance(image_id, str) and image_id.strip().isdigit():
            image_id = int(image_id)
        if (not isinstance(image_id, int) or isinstance(image_id, bool) or image_id not in allowed
                or image_id in used or p.get("useful") is False
                or not isinstance(point, str) or not point.strip()):
            continue
        rank_ = p.get("rank") if isinstance(p.get("rank"), (int, float)) and not isinstance(p.get("rank"), bool) else 1000 + i
        out.append({"image_id": image_id, "rank": rank_, "reference_point": point.strip(), "order": i})
        used.add(image_id)
    out.sort(key=lambda p: (p["rank"], p["order"]))
    return [{"image_id": p["image_id"], "reference_point": p["reference_point"]} for p in out[:PICKS_PER_COLUMN]]


def review_column(column: str, brief_text: str, candidates: list[Candidate], settings: dict,
                  images_dir: Path, client: httpx.Client | None = None, sleep=time.sleep) -> dict:
    """One vision request for one column with the real candidate images.

    A missing or corrupt image file is skipped (logged, counted) so the rest of
    the column still gets reviewed; if no image can be read the column degrades
    to review_failed instead of aborting the whole three-column response.
    """
    meta = COLUMNS[column]
    usage = {"requests": 0, "prompt_tokens": None, "completion_tokens": None, "latency_ms": 0}
    if not candidates:
        return {"status": "no_candidates", "picks": [], "usage": usage,
                "reviewed_ids": [], "skipped_images": 0}
    if not (settings.get("base_url") and settings.get("api_key") and settings.get("vision_model")):
        return {"status": "review_failed", "error": "未配置视觉模型", "picks": [], "usage": usage,
                "reviewed_ids": [c.image_id for c in candidates], "skipped_images": 0}
    readable, skipped = [], 0
    for c in candidates:
        path = images_dir.parent / c.file_path if not Path(c.file_path).is_absolute() else Path(c.file_path)
        try:
            readable.append((c, vision.image_data_uri(path, REVIEW_MAX_SIDE)))
        except Exception as exc:
            skipped += 1
            log.warning("review image skipped: column=%s image_id=%s path=%s error=%s",
                        column, c.image_id, c.file_path, type(exc).__name__)
    if not readable:
        return {"status": "review_failed", "error": "候选图片全部无法读取", "picks": [], "usage": usage,
                "reviewed_ids": [], "skipped_images": skipped}
    videos = [c.image_id for c, _ in readable if c.is_video]
    video_note = (f"- 编号 {', '.join(map(str, videos))} 来自视频笔记，只是封面图，只能按封面判断。" if videos else "")
    content = [{"type": "text", "text": REVIEW_PROMPT.format(brief=brief_text, goal=meta["goal"], label=meta["label"],
                                                             n=len(readable), k=PICKS_PER_COLUMN, video_note=video_note)}]
    for c, data_uri in readable:
        content.append({"type": "text", "text": f"image_id={c.image_id}"})
        content.append({"type": "image_url", "image_url": {"url": data_uri}})
    calls = vision.Calls()
    own = client is None
    client = client or httpx.Client()
    started = time.perf_counter()
    try:
        calls.raw = vision._ask(client, settings, [{"role": "user", "content": content}], calls, sleep)
        picks = parse_review(calls.raw, [c.image_id for c, _ in readable])
        status, error = "ok", None
    except vision.VisionError as exc:
        picks, status, error = [], "review_failed", f"{exc.kind}: {exc}"
    except (ValueError, json.JSONDecodeError) as exc:
        picks, status, error = [], "review_failed", vision.redact(f"invalid: {exc}")
    finally:
        if own:
            client.close()
    usage.update(requests=calls.count, prompt_tokens=calls.prompt_tokens, completion_tokens=calls.completion_tokens,
                 latency_ms=round((time.perf_counter() - started) * 1000))
    return {"status": status, "error": error, "picks": picks, "usage": usage,
            "reviewed_ids": [c.image_id for c, _ in readable], "skipped_images": skipped}


def render_column(column,candidates,review,source):
    meta=COLUMNS[column]
    by_id = {c.image_id: c for c in candidates}
    # A real reviewer reports which candidates it actually sent. Injected/fake
    # reviewers may omit it, in which case every candidate was considered.
    sent_ids = review.get("reviewed_ids")
    visible = candidates if sent_ids is None else [c for c in candidates if c.image_id in set(sent_ids)]
    items = []
    # Review failed: fall back to the recall top 5, explicitly marked unreviewed.
    # This is a degraded-but-honest answer, never a pretend successful review.
    if review["status"] == "review_failed":
        for position, c in enumerate(visible[:PICKS_PER_COLUMN], 1):
            items.append({
                "image_id": c.image_id, "rank": position, "recall_rank": c.recall_rank,
                "semantic_score": c.score, "semantic_rank": c.recall_rank,
                "media_scope": "cover_and_description" if c.is_video else "image",
                "post_ids": c.post_ids, "reference_point": "未复看", "unreviewed": True,
                "purpose": column,
                "purposes": [{"purpose": column, "reason": "未复看",
                              "evidence": []}],
                "unmet_requirements": [],
            })
    else:
        for position, pick in enumerate(review["picks"], 1):
            c = by_id[pick["image_id"]]
            items.append({
                "image_id": c.image_id, "rank": position, "recall_rank": c.recall_rank,
                "semantic_score": c.score, "semantic_rank": c.recall_rank,
                "media_scope": "cover_and_description" if c.is_video else "image",
                "post_ids": c.post_ids, "reference_point": pick["reference_point"],
                "unreviewed": False, "purpose": column,
                "purposes": [{"purpose": column,
                              "reason": pick["reference_point"],
                              "evidence": []}],
                "unmet_requirements": [],
            })
    recall_source = source
    return {"purpose": column, "label": meta["label"], "status": review["status"],
                    "error": review.get("error"), "recall_source": recall_source,
                    "reviewed_ids": ([c.image_id for c in visible] if sent_ids is None else list(sent_ids)),
                    "skipped_images": int(review.get("skipped_images") or 0),
                    "items": items, "usage": review["usage"]}


class CreativeUnavailable(RuntimeError):
    """Fixed public message: never includes provider response, Key or raw exception."""


class CreativeApplication:
    def __init__(self, settings_provider: Callable[[], EmbeddingSettings] = EmbeddingSettings.from_env,
                 client_factory: Callable = EmbeddingClient, database: Callable[[], Path] = config.db_path,
                 vision_settings: Callable[[], dict] = config.ai_settings, reviewer: Callable = review_column,
                 needs_parser: Callable = decompose_needs, image_similarity=None):
        cache_needs=needs_parser is decompose_needs
        self._http_client=None
        if client_factory is EmbeddingClient or reviewer is review_column or needs_parser is decompose_needs:
            self._http_client=httpx.Client()
            if client_factory is EmbeddingClient:
                client_factory=lambda settings:EmbeddingClient(settings,http_client=self._http_client)
            if reviewer is review_column:
                from functools import partial
                reviewer=partial(review_column,client=self._http_client)
            if needs_parser is decompose_needs:
                from functools import partial
                needs_parser=partial(decompose_needs,client=self._http_client)
        self.settings_provider = settings_provider
        self.client_factory = client_factory
        self.database = database
        self.vision_settings = vision_settings
        self.reviewer = reviewer
        if cache_needs:
            from .recommendation_needs_cache import NeedsCache
            needs_parser=NeedsCache(Path(database()).parent/'recommendation_cache/needs',NEED_PROMPT,needs_parser)
        self.needs_parser = needs_parser
        self.image_similarity = image_similarity

    def close(self):
        if self._http_client is not None:self._http_client.close()

    def recommend(self, brief: str, previous: str | None = None, *, include_nonvisual=False, color_need=None, project_id=None, negative_ids=(), on_column=None) -> dict:
        started = time.perf_counter()
        if project_id is not None:
            with readonly_connection(self.database()) as conn:
                if not conn.execute('SELECT 1 FROM projects WHERE id=?',(project_id,)).fetchone():raise KeyError('项目不存在')
        final, effective = resolve_brief(brief, previous)  # ValueError / EmptyEffectiveQuery before any request
        needs = _fallback_needs(final)
        needs_usage = {"requests": 0, "prompt_tokens": None, "completion_tokens": None, "latency_ms": 0}
        try:
            parsed = self.needs_parser(effective, self.vision_settings())
            if isinstance(parsed, dict) and isinstance(parsed.get("needs"), dict):
                needs.update({key: value for key, value in parsed["needs"].items()
                              if key in needs and value and (isinstance(value, str) or
                                  key == "color_need" and isinstance(value, list))})
                needs_usage.update(parsed.get("usage") or {})
        except Exception:
            # Decomposition is an optimization; the local parsed brief remains a safe fallback.
            pass
        try:
            settings = self.settings_provider()
            with readonly_connection(self.database()) as conn:
                indexed = {s: len(dimensions.load_rows(conn, dimensions.spec_for(settings, s)))
                           for s in ("layout_v1", "style_v1", "description_v1")}
            if not any(indexed.values()):
                return {"status": "index_empty", "effective_query": effective, "columns": [], "images": [],
                        "notices": ["分维度索引为空，请先运行 dimension_backfill。"]}
            # The decomposition controls the per-column semantic query. All three texts
            # are sent in ONE batch embedding request; they are NOT collapsed into one
            # shared vector. (The client batches via its normal /embeddings input list.)
            if color_need:
                needs['color_need']=color_need
            color_need = needs.get("color_need")
            if isinstance(color_need, list):
                color_text = json.dumps(color_need, ensure_ascii=False) if color_need else ""
            else:
                color_text = color_need or ""
            need_texts = [needs.get("layout_need") or effective,
                          needs.get("style_need") or effective,
                          color_text or effective]
            with self.client_factory(settings) as client:
                query_vectors = client.embed(need_texts)
                usage_complete = getattr(client, "usage_complete", False)
                embedding_usage = {
                    "requests": getattr(client, "requests", None),
                    "prompt_tokens": getattr(client, "prompt_tokens", None) if usage_complete else None,
                    "completion_tokens": getattr(client, "completion_tokens", None) if usage_complete else None,
                }
            if not isinstance(query_vectors, list) or len(query_vectors) != 3:
                raise CreativeUnavailable("推荐服务暂不可用")
            layout_vector, style_vector, color_vector = query_vectors
            colors = palette.targets(needs.get("color_need"), final.colors, final.prominent_colors)
            shortlists = {}
            with readonly_connection(self.database()) as conn:
                eligible = None if include_nonvisual else eligible_ids(conn)
                if negative_ids:
                    eligible=({r[0]for r in conn.execute('SELECT id FROM images')} if eligible is None else eligible)-set(negative_ids)
                for column, meta in COLUMNS.items():
                    adjust=feedback.adjuster(conn,self.image_similarity,column,project_id,negative_ids)
                    extra={'score_adjust':adjust} if adjust else {}
                    if column == "palette" and colors:
                        recalled = palette_recall(conn, colors, eligible=eligible,**extra)
                    elif column == "layout":
                        recalled = dimension_recall(conn, settings, meta["scheme"], layout_vector, eligible=eligible,**extra)
                    elif column == "style":
                        recalled = dimension_recall(conn, settings, meta["scheme"], style_vector, eligible=eligible,**extra)
                    else:
                        recalled = dimension_recall(conn, settings, meta["scheme"], color_vector, eligible=eligible,**extra)
                    context = _context(conn, [i for i, _ in recalled])
                    shortlists[column] = shortlist(recalled, context, final)
        except Exception:
            raise CreativeUnavailable("推荐服务暂不可用") from None
        vsettings = self.vision_settings()
        images_dir = Path(self.database()).parent / "images"
        need_keys = {"layout": "layout_need", "palette": "color_need", "style": "style_need"}
        with ThreadPoolExecutor(max_workers=len(COLUMNS)) as pool:
            futures = {column: pool.submit(
                self.reviewer, column,
                (json.dumps(needs[need_keys[column]], ensure_ascii=False)
                 if isinstance(needs.get(need_keys[column]), list) else
                 needs.get(need_keys[column]) or final.text).replace("\n", "；"),
                shortlists[column], vsettings, images_dir)
                for column in COLUMNS}
            reviews={};first_column_ms=None
            names={future:column for column,future in futures.items()}
            for future in as_completed(names):
                column=names[future]
                try:
                    reviews[column]=future.result()
                except Exception as exc:
                    # One broken column worker must not discard the other columns.
                    log.warning("review worker failed: column=%s error=%s", column, type(exc).__name__)
                    reviews[column]={"status":"review_failed","error":"该栏复看异常","picks":[],
                                     "usage":{"requests":0,"prompt_tokens":None,"completion_tokens":None,"latency_ms":0}}
                if first_column_ms is None:first_column_ms=round((time.perf_counter()-started)*1000)
                if on_column:
                    rendered=render_column(column,shortlists[column],reviews[column],
                        "Pillow 颜色占比" if column=='palette' and colors else COLUMNS[column]['scheme'])
                    with readonly_connection(self.database()) as conn:
                        partial_images=hydrate_hits(conn,[SearchHit(image_id=i['image_id'],score=0.,payload=None)for i in rendered['items']])
                    on_column({'columns':[rendered],'images':partial_images,'effective_query':effective,'first_column_ms':first_column_ms})
        columns, chosen = [], set()
        flat_items, flat_seen = [], set()
        for column, meta in COLUMNS.items():
            rendered=render_column(column,shortlists[column],reviews[column],
                "Pillow 颜色占比" if column=="palette" and colors else meta['scheme'])
            columns.append(rendered)
            for item in rendered['items']:
                chosen.add(item['image_id'])
                if item['image_id'] not in flat_seen:
                    flat_seen.add(item['image_id']);flat_items.append(item)
        with readonly_connection(self.database()) as conn:
            images = hydrate_hits(conn, [SearchHit(image_id=i, score=0.0, payload=None) for i in sorted(chosen)])
        notices = []
        if any(c["status"] == "review_failed" for c in columns):
            notices.append("部分栏目视觉复看失败，已退回该栏召回前5并标“未复看”。")
        skipped_total = sum(int(c.get("skipped_images") or 0) for c in columns)
        if skipped_total:
            notices.append(f"{skipped_total} 张候选图片无法读取，已跳过，其余候选继续复看。")
        buckets = [needs_usage, embedding_usage, *[c["usage"] for c in columns]]
        reported = sum(int(b.get("requests") or 0) for b in buckets)

        def _sum(key: str):
            values = [b.get(key) for b in buckets]
            return sum(v or 0 for v in values) if all(v is not None for v in values) else None

        usage = {
            "requests": reported if all(b.get("requests") is not None for b in buckets) else None,
            "reported_requests": reported,
            "first_column_ms": first_column_ms,
            "latency_ms": round((time.perf_counter() - started) * 1000),
            "prompt_tokens": _sum("prompt_tokens"),
            "completion_tokens": _sum("completion_tokens"),
        }
        usage["complete"] = all(usage[k] is not None for k in ("requests", "prompt_tokens", "completion_tokens"))
        return {"usage": usage, "embedding_usage": embedding_usage, "status": "ok", "effective_query": effective, "needs": needs,
                "needs_usage": needs_usage, "columns": columns, "items": flat_items,
                "images": images, "notices": notices}


    def _offline_more_like(self,column,image_id,*,final,effective,include_nonvisual,project_id,negative_ids,started):
        """Local-only degraded answer for the explicit offline demo switch.

        No reviewer, needs parser or cloud embedding client is touched. Local CLIP
        neighbours are shown as unreviewed recall (never presented as a review).
        """
        candidates=[]
        if self.image_similarity is None:
            notice='离线演示：本地图片模型未就绪，本栏暂无候选（未调用云端模型）。'
        else:
            try:
                with readonly_connection(self.database()) as conn:
                    eligible=None if include_nonvisual else eligible_ids(conn)
                    recalled=feedback.local_neighbors(self.image_similarity,image_id,eligible,set(negative_ids))
                    adjust=feedback.adjuster(conn,self.image_similarity,column,project_id,negative_ids)
                    if adjust:recalled=sorted(((i,adjust(i,v))for i,v in recalled),key=lambda x:(-x[1],x[0]))
                    candidates=shortlist(recalled,_context(conn,[i for i,_ in recalled]),final)
                notice='离线演示：未调用云端模型；本栏显示本地图片相似召回前 5 张，均未复看。'
            except Exception:
                candidates=[]
                notice='离线演示：本地图片相似暂不可用，本栏暂无候选（未调用云端模型）。'
        usage={'requests':0,'prompt_tokens':0,'completion_tokens':0,'latency_ms':0}
        review={'status':'review_failed' if candidates else 'no_candidates','error':None,'picks':[],
                'usage':usage,'reviewed_ids':[c.image_id for c in candidates]}
        rendered=render_column(column,candidates,review,'本地图片相似')
        with readonly_connection(self.database()) as conn:
            images=hydrate_hits(conn,[SearchHit(image_id=i['image_id'],score=0.,payload=None)for i in rendered['items']])
        return {'status':'ok','offline':True,'effective_query':effective,'columns':[rendered],'images':images,
                'notices':[notice],'usage':{**usage,'complete':True,
                'latency_ms':round((time.perf_counter()-started)*1000)}}

    def more_like(self,brief,column,image_id,*,previous=None,include_nonvisual=False,project_id=None,negative_ids=(),color_need=None):
        if column not in COLUMNS:raise ValueError('未知推荐栏')
        started=time.perf_counter();final,effective=resolve_brief(brief,previous)
        if demo_cache.offline():
            return self._offline_more_like(column,image_id,final=final,effective=effective,
                include_nonvisual=include_nonvisual,project_id=project_id,negative_ids=negative_ids,started=started)
        if self.image_similarity is None:raise ValueError('本地图片模型未就绪')
        with readonly_connection(self.database()) as conn:
            eligible=None if include_nonvisual else eligible_ids(conn)
            recalled=feedback.local_neighbors(self.image_similarity,image_id,eligible,set(negative_ids))
            adjust=feedback.adjuster(conn,self.image_similarity,column,project_id,negative_ids)
            if adjust:recalled=sorted(((i,adjust(i,v))for i,v in recalled),key=lambda x:(-x[1],x[0]))
            candidates=shortlist(recalled,_context(conn,[i for i,_ in recalled]),final)
        text=effective+(('；指定配色：'+json.dumps(color_need,ensure_ascii=False))if column=='palette' and color_need else '')
        review=self.reviewer(column,text,candidates,self.vision_settings(),Path(self.database()).parent/'images')
        rendered=render_column(column,candidates,review,'本地图片相似')
        with readonly_connection(self.database()) as conn:
            images=hydrate_hits(conn,[SearchHit(image_id=i['image_id'],score=0.,payload=None)for i in rendered['items']])
        return {'status':'ok','effective_query':effective,'columns':[rendered],'images':images,
            'notices':['该栏使用全库本地图片向量召回后复看；其他两栏保持原结果。'],
            'usage':{**review['usage'],'latency_ms':round((time.perf_counter()-started)*1000)}}
