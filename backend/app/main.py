"""FastAPI entry. Run from backend/: uvicorn app.main:create_app --factory"""
from contextlib import asynccontextmanager

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, StrictStr, StrictInt, StrictBool, field_validator

from . import analysis, annotations, config, db, importer, learned_find, local_discovery, titles
from . import search_terms
from . import thumbnails
from .embedding import dimensions
from .embedding.adapter import EmbeddingAdapter
from .embedding.search import SearchHit
from .embedding.search_application import (
    SearchApplication, SearchUnavailable, hydrate_hits, readonly_connection,
)
from .recommendation_query import EmptyEffectiveQuery, resolve_brief
from .recommendation_service import RecommendationApplication, RecommendationUnavailable
from .creative import CreativeApplication, CreativeUnavailable
from .sync import runner as sync_runner
from .sync import safety, connection
from . import imports as importers
from .sync import store as sync_store
from .sync import huaban_service, pinterest_service, xhs_source, zcool_service
from .sync.huaban_service import HuabanSyncRunner
from .sync.pinterest_service import PinterestSyncRunner
from .sync.zcool_service import ZcoolSyncRunner
from .sync.auto_watch import AutoSync, file_mtime, newest_mtime
from .similarity import ImageSimilarity, SimilarityUnavailable
from . import settings as ui_settings
from .project_api import project_router
from .stats_api import router as stats_router
from .ai_key_api import ai_key_router
from .pip_scene_api import pip_scene_router
from .eval_api import eval_router
from .reference_value import ReferenceLabels, CATEGORIES, allowed_analysis
from . import demo_cache, reference_review
from . import demo_cache
from . import recommendation_feedback as feedback
from .watch_folders import WatchFolders, auto_analysis_enabled
from .watch_folder_api import watch_folder_router
from .vibe import VibeService
from .vibe_api import Dimension, vibe_router
from .phone_pairing import PhonePairing
from .wechat import verify_signature


class SearchRequest(BaseModel):
    query: StrictStr

    @field_validator("query")
    @classmethod
    def not_blank(cls, value: str):
        if not value.strip():
            raise ValueError("query must not be blank")
        return value  # preserve whitespace and punctuation in the embedding input


class TitleEdit(BaseModel):
    """人工标题：'title' 缺省不合法；null 表示清掉人工标题、回到自动标题。"""
    model_config = ConfigDict(extra="forbid")
    title: StrictStr | None = None


class AnnotationEdit(BaseModel):
    # Both fields are optional but the route distinguishes "omitted" from "explicit
    # null" via model_fields_set: omitted = keep, null = clear that overlay field.
    model_config = ConfigDict(extra="forbid")
    description: StrictStr | None = None
    tags: list[StrictStr] | None = None


def _merge_annotation_hits(result: dict, query: str, *, manual_only: bool = False,
                           blend_posts: bool = True) -> dict:
    """Keep manual matches first and blend free post text into the remaining hits."""
    # index_empty can still be answered by the local annotation channel: annotations are
    # written before any paid analysis, so a key-less/offline user must still find them.
    if result.get("status") not in ("ok", "index_empty"):
        return result
    try:
        matches = annotations.search_matches(query, manual_only=manual_only)
    except Exception:
        return result
    try:
        post_scores = annotations.post_search_matches(query) if blend_posts else {}
        if manual_only:
            post_scores = {image_id: score * .15 for image_id, score in post_scores.items()}
    except Exception:
        post_scores = {}
    if not matches and not post_scores:
        return result
    ranked = {item["image_id"]: item for item in result.get("items") or []}
    manual = {match["image_id"]: match["score"] for match in matches}
    scores = {image_id: item["score"] + post_scores.get(image_id, 0)
              for image_id, item in ranked.items() if image_id not in manual}
    scores.update((image_id, score) for image_id, score in sorted(post_scores.items())
                  if image_id not in ranked and image_id not in manual)
    best = sorted(scores, key=scores.__getitem__, reverse=True)[:max(20, len(ranked))]
    ordered = {**manual, **{image_id: scores[image_id] for image_id in best}}
    try:
        missing = [SearchHit(image_id=image_id, score=0.0) for image_id in ordered
                   if image_id not in ranked]
        if missing:
            with readonly_connection(config.db_path()) as conn:
                ranked.update((item["image_id"], item) for item in hydrate_hits(conn, missing))
    except Exception:
        return result
    # "_literal" keeps the annotation channel's literal match visible to the relevance
    # gate (search_terms.apply_relevance), which strips it before the response leaves.
    merged = [{**ranked[image_id], "score": score, **({"_literal": True} if image_id in manual else {})}
              for image_id, score in ordered.items() if image_id in ranked]
    if not merged:
        return result
    if result.get("status") == "index_empty":
        return {**result, "status": "ok", "items": merged, "degraded": True}
    return {**result, "items": merged}


def _search_boosts(query: str) -> dict[int, float]:
    """Local evidence added to the semantic fusion: literal text matches + palette."""
    boosts: dict[int, float] = {}
    try:
        boosts.update(search_terms.keyword_boosts(query))
    except Exception:
        pass
    try:
        targets = local_discovery.color_targets(query)
        if targets:
            with readonly_connection(config.db_path()) as conn:
                matches = local_discovery.palette_matches(conn, targets)
            if matches:
                low, high = min(matches.values()), max(matches.values())
                span = high - low
                if span > 1e-9:
                    for image_id, score in matches.items():
                        weight = 0.35 * (score - low) / span
                        if weight:
                            boosts[image_id] = min(0.60, boosts.get(image_id, 0.0) + weight)
    except Exception:
        pass
    return boosts


class ColorNeed(BaseModel):
    hex: StrictStr
    role: StrictStr = '点缀'

    @field_validator('hex')
    @classmethod
    def valid_hex(cls,value):
        from .palette import rgb
        if rgb(value) is None:raise ValueError('颜色须为#RRGGBB')
        return value.lower()

    @field_validator('role')
    @classmethod
    def valid_role(cls,value):
        if value not in ('点缀','为主','main','accent'):raise ValueError('颜色角色无效')
        return value


class RecommendationRequest(BaseModel):
    brief: StrictStr
    previous_brief: StrictStr | None = None
    include_nonvisual: bool = False
    project_id: StrictInt | None = None
    color_need: list[ColorNeed] | None = None
    negative_ids: list[StrictInt] = []

    @field_validator('color_need')
    @classmethod
    def max_colors(cls,value):
        if value is not None and len(value)>8:raise ValueError('最多8种颜色')
        return value

    @field_validator('negative_ids')
    @classmethod
    def valid_ids(cls,value):
        if len(value)>200 or any(i<=0 for i in value):raise ValueError('排除列表无效')
        return list(dict.fromkeys(value))

    @field_validator("brief")
    @classmethod
    def not_blank(cls, value: str):
        if not value.strip():
            raise ValueError("brief must not be blank")
        return value


def _source_page(reason: str, post_id: int, title: str = "") -> HTMLResponse:
    import html as _html
    verified = (f"<p>已核对标题：<b>{_html.escape(title)}</b></p>" if title else "")
    body = f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<title>暂时无法打开原帖</title>
<style>body{{font-family:system-ui,sans-serif;max-width:640px;margin:12vh auto;padding:0 24px;line-height:1.7}}
h1{{font-size:20px}} .muted{{color:#666}} a{{color:#c33}}</style></head>
<body><h1>暂时无法打开原帖</h1>
{verified}
<p>{_html.escape(reason)}</p>
<p class="muted">库内记录：post #{post_id}。可以稍后重试，或在小红书 App 里查看这篇笔记。</p>
<p><a href="/api/posts/{post_id}/source">重试</a> · <a href="/">返回工作台</a></p></body></html>"""
    return HTMLResponse(body)


def create_app(worker: analysis.Worker | None = None,
               syncer: sync_runner.SyncRunner | None = None,
               embedder: EmbeddingAdapter | None = None,
               search_client_factory=None,
               creative_reviewer=None,
               creative_needs_parser=None,
               image_similarity=None) -> FastAPI:
    db.init_db()
    sync_store.recover_stale_runs()
    analysis.expire_stale_claims()  # claims left by a dead executor -> failed (result unknown)
    # one adapter per app: done analyses are queued here and embedded in batches
    embedder = embedder or EmbeddingAdapter(dimension_schemes=dimensions.SCHEMES)
    similar = image_similarity or ImageSimilarity()
    vibe = VibeService(similar)
    references = ReferenceLabels(similar)
    worker = worker or analysis.Worker(on_done=embedder.enqueue, next_eligible=references.next_analysis,
                                       concurrency=config.analysis_concurrency())
    syncer = syncer or sync_runner.SyncRunner()  # manual only: nothing runs until the UI asks
    huaban_syncer = HuabanSyncRunner()  # shared profile separate from XHS
    pinterest_syncer = PinterestSyncRunner()
    zcool_syncer = ZcoolSyncRunner()
    saved = ui_settings.read()
    # Two independent gates for automatic platform requests:
    #  1) only the entry-launched formal instance may auto-sync (WOMINMIN_ALLOW_AUTO_SYNC);
    #  2) an unreadable/corrupt/active lock at startup keeps every watcher off (fail closed).
    allow_auto_sync = config.auto_sync_allowed()
    auto_lock_blocked = bool(safety.state().get("auto_blocked"))
    saved_auto = bool(saved.get("auto_sync")) and allow_auto_sync and not auto_lock_blocked
    flags = saved.get("auto_platforms") or {}

    def on(name: str) -> bool:
        return saved_auto and allow_auto_sync and bool(flags.get(name, True))

    xhs_auto = AutoSync("xhs", syncer.start_sync, syncer.state, enabled=on("xhs"),
                        account_mtime=lambda: file_mtime(sync_runner.profile_dir() / "account.json"))
    huaban_auto = AutoSync("huaban", huaban_syncer.start_sync, huaban_syncer.state, enabled=on("huaban"),
                           account_mtime=lambda: newest_mtime(huaban_service.profile_dir()))
    pinterest_auto = AutoSync("pinterest", pinterest_syncer.start_sync, pinterest_syncer.state, enabled=on("pinterest"),
                              account_mtime=lambda: file_mtime(pinterest_service.auth_path()))
    zcool_auto = AutoSync("zcool", zcool_syncer.start_sync, zcool_syncer.state, enabled=on("zcool"),
                          account_mtime=lambda: newest_mtime(zcool_service.profile_dir()))

    def embedding_settings():
        if embedder.settings is None:
            raise SearchUnavailable("搜索配置不可用")
        return embedder.settings  # same snapshot used by the Day4 image embedding adapter

    searcher = SearchApplication(settings_provider=embedding_settings,
        **({"client_factory": search_client_factory} if search_client_factory is not None else {}),
        clip_search=similar)

    recommender = RecommendationApplication(searcher)
    # Backend-only three-column preflight path; the existing /api/recommendations
    # remains the Day6-compatible endpoint until the frontend consumes columns.
    creative = CreativeApplication(settings_provider=embedding_settings, image_similarity=similar,
        **({"client_factory": search_client_factory} if search_client_factory is not None else {}),
        **({"reviewer": creative_reviewer} if creative_reviewer is not None else {}),
        **({"needs_parser": creative_needs_parser} if creative_needs_parser is not None else {}))
    # Watched import folders: only the entry-launched instance may poll automatically
    # (same WOMINMIN_ALLOW_AUTO_SYNC gate as auto sync). The HTTP API stays available
    # everywhere so a copy instance can still be tried with the manual scan button.
    watch_folders = WatchFolders()
    watch_worker = analysis.Worker(on_done=embedder.enqueue,
                                   prepare_claim=watch_folders.prepare_analysis)

    phone_pairing = PhonePairing()
    usb_phone_pairing = PhonePairing(usb=True)

    @asynccontextmanager
    async def lifespan(app):
        similar.start()
        references.classify_pending(analysis_only=True)
        references.start()
        if config.analysis_auto_start() and worker.state()["configured"]:
            worker.start()
        xhs_auto.start()
        huaban_auto.start()
        pinterest_auto.start()
        zcool_auto.start()
        if config.auto_sync_allowed():
            watch_folders.start()
            if auto_analysis_enabled() and watch_worker.state()["configured"]:
                watch_worker.start()
        yield
        phone_pairing.stop()
        usb_phone_pairing.stop()
        xhs_auto.stop()
        huaban_auto.stop()
        pinterest_auto.stop()
        zcool_auto.stop()
        watch_folders.stop()
        watch_worker.shutdown()
        references.shutdown()
        similar.shutdown()
        creative.close()
        worker.shutdown()
        embedder.shutdown()

    app = FastAPI(title="我明明收藏过", lifespan=lifespan)
    app.include_router(project_router())
    app.include_router(stats_router)
    app.include_router(ai_key_router())
    app.include_router(pip_scene_router())
    app.include_router(eval_router())
    app.include_router(watch_folder_router(watch_folders, watch_worker))
    app.include_router(vibe_router(vibe))

    @app.get("/api/reference-labels")
    def reference_labels():
        return references.snapshot()

    @app.get("/api/reference/review")
    def reference_review_items(uncertain_only: bool = False):
        items = reference_review.review_rows()
        if uncertain_only:
            items = [row for row in items if row["category"] == "uncertain"
                     and row["needs_confirmation"]]
        return {"items":items,"groups":{
            "visual_ref":sum(r['category']=='visual_ref' for r in items),
            "text_card":sum(r['category']=='text_card' for r in items),
            "life":sum(r['category']=='life' for r in items),
            "pending":sum(r['needs_confirmation'] for r in items)}}

    class ReferenceBatch(BaseModel):
        ids: list[StrictInt]
        is_reference: StrictBool

    @app.post("/api/reference/decisions")
    def reference_batch(request: ReferenceBatch):
        try:return reference_review.apply(request.ids,request.is_reference)
        except ValueError as exc:raise HTTPException(422,str(exc)) from None
        except KeyError:raise HTTPException(404,"图片不存在") from None

    @app.post("/api/reference/decisions/{operation}/undo")
    def reference_undo(operation: str):
        try:return reference_review.undo(operation)
        except KeyError:raise HTTPException(404,"操作不存在") from None

    class ReferenceEdit(BaseModel):
        category: str

    @app.put("/api/images/{image_id}/reference")
    def reference_edit(image_id: int, request: ReferenceEdit):
        if request.category not in CATEGORIES:
            raise HTTPException(422, "未知参考标签")
        try:
            result=reference_review.apply([image_id],category=request.category)
        except KeyError:raise HTTPException(404,"图片不存在") from None
        return {"category": request.category, "manual": True}

    @app.post("/api/images/{image_id}/confirm-analysis")
    def confirm_analysis(image_id: int):
        conn = db.connect()
        try:
            if not conn.execute("SELECT 1 FROM images WHERE id=? AND deleted_at IS NULL",
                                (image_id,)).fetchone():
                raise HTTPException(404, "图片不存在")
            conn.execute("""INSERT INTO image_reference_labels(image_id,confirmed) VALUES (?,1)
                ON CONFLICT(image_id) DO UPDATE SET confirmed=1""", (image_id,))
            conn.commit()
        finally: conn.close()
        return {"confirmed": True, "message": "已确认；仅在你启动分析队列后分析"}

    @app.get("/api/sync/collections")
    def sync_collections():
        from .embedding.search_application import readonly_connection
        with readonly_connection(config.db_path()) as conn:
            return {"items": [dict(r) for r in conn.execute("""SELECT c.platform,c.collection_key,c.title,c.kind,
                COUNT(pc.post_id) AS posts FROM sync_collections c LEFT JOIN post_collections pc
                ON pc.platform=c.platform AND pc.account_key=c.account_key AND pc.collection_key=c.collection_key
                GROUP BY c.platform,c.account_key,c.collection_key ORDER BY c.platform,c.collection_key""")]}

    @app.post("/api/sync/all")
    def sync_all():
        runners = {"xhs": syncer, "huaban": huaban_syncer, "pinterest": pinterest_syncer, "zcool": zcool_syncer}
        return {name: ({"started":False,"reason":safety.MESSAGE} if name=="xhs" and safety.state()["blocked"] else {"started":runner.start_sync()}) for name,runner in runners.items()}

    @app.get("/api/similar/status")
    def similar_status():
        return similar.status()

    def similar_result(**kwargs):
        try:
            return similar.search(**kwargs)
        except KeyError:
            raise HTTPException(404, "图片不存在") from None
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        except SimilarityUnavailable as exc:
            raise HTTPException(503, str(exc)) from None
        except Exception:
            raise HTTPException(503, "本地找相似暂不可用，请重试。") from None

    @app.get("/api/images/{image_id}/similar")
    def similar_image(image_id: int, dimension: Dimension = "combined"):
        try:
            try:
                result = vibe.similar(image_id, dimension)
            except SimilarityUnavailable:
                if not config.local_library_enabled() or dimension != "combined":
                    raise
                result = {"status": "index_empty", "items": [], "available_dimensions": ["combined"]}
            if config.local_library_enabled() and dimension == "combined" and not result["items"]:
                if "palette" in result["available_dimensions"] or result["status"] == "index_empty":
                    try:
                        fallback = vibe.similar(image_id, "palette")
                    except ValueError:
                        fallback = None
                    if fallback and fallback["items"]:
                        return {**fallback, "match_basis": "palette"}
            return result
        except KeyError:
            raise HTTPException(404, "图片不存在") from None
        except (ValueError, SimilarityUnavailable) as exc:
            raise HTTPException(422 if isinstance(exc, ValueError) else 503, str(exc)) from None
        except Exception:
            raise HTTPException(503, "本地找相似暂不可用，请重试。") from None

    @app.post("/api/search/image")
    async def similar_upload(file: UploadFile = File(...)):
        from .importer import MAX_BYTES
        data = await file.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise HTTPException(413, "图片超过50MB")
        # Run CPU inference outside the ASGI event loop, without importing the query.
        from starlette.concurrency import run_in_threadpool
        return await run_in_threadpool(lambda: similar_result(data=data, **vibe.upload_options()))

    def local_search(query: str, *, colors: bool = True):
        if not colors:
            return _merge_annotation_hits(searcher.search_local(query), query)
        with readonly_connection(config.db_path()) as conn:
            trained, reviewed = ([], set())
            if not local_discovery.color_only_query(query) and not local_discovery.COLOR_RICH.fullmatch(learned_find.clean_query(query)):
                trained, reviewed = learned_find.learned_hits(conn, query, similar)
            if trained and local_discovery.search_subject(query) in local_discovery.SHORT_EXPANSIONS:
                expanded, _ = local_discovery.visual_scores(query, similar)
                trained = sorted(trained, key=lambda hit: -(hit["score"] + .15 * expanded.get(hit["image_id"], 0)))
            if trained and conn.execute(
                    "SELECT COUNT(*) FROM images WHERE deleted_at IS NULL").fetchone()[0] == len(reviewed):
                return {"status": "ok", "items": local_discovery.representative_hits(trained)}
            hits, channel = local_discovery.visual_hits(conn, query, similar)
            result = {"status": "ok", "items": hits}
            if channel == "semantic":
                result = _merge_annotation_hits(result, query)
                result = local_discovery.with_colors(conn, query, result)
            elif channel == "scene":
                # Exact saved wording can still recover a relevant image with weak visual evidence.
                result = _merge_annotation_hits(result, query.strip().rstrip("，。、,."))
            ranked = trained + [hit for hit in result["items"] if hit["image_id"] not in reviewed] if trained else result["items"]
            return {**result, "items": local_discovery.representative_hits(ranked)}

    def desktop_search(query: str) -> dict:
        subject = local_discovery.search_subject(query)

        def fallback():
            return {**local_search(query), "search_mode": "本地检索"}

        if (not config.ai_settings()["api_key"] or demo_cache.offline()
                or local_discovery.COLOR_RICH.fullmatch(subject) or local_discovery.color_only_query(subject)):
            return fallback()
        with readonly_connection(config.db_path()) as conn:
            trained, reviewed = learned_find.learned_hits(conn, query, similar)
            all_reviewed = len(reviewed) == conn.execute(
                "SELECT COUNT(*) FROM images WHERE deleted_at IS NULL").fetchone()[0]
        scoped = None
        expanded_clip = None
        boosts = _search_boosts(query)
        if subject in local_discovery.SHORT_EXPANSIONS and not boosts:
            # No description/tag in the library mentions this short topic (for
            # example 海, whose only literal hits are the compound 海报). Keep the
            # previous visual-channel answer instead of letting a weak text match
            # invent a result.
            return fallback()
        if trained:
            visual, _ = local_discovery.visual_scores(query, similar)
            scoped = {hit["image_id"]: hit["score"] + .15 * visual.get(hit["image_id"], 0)
                      for hit in trained}
            if not all_reviewed:
                scoped.update((image_id, score) for image_id, score in visual.items() if image_id not in reviewed)
        elif subject in local_discovery.SHORT_EXPANSIONS:
            # Short topic words expand to synonyms for the local CLIP channel, but
            # they no longer hard-filter the paid text candidates: a weak visual
            # neighbour must not evict an image whose description actually matches.
            try:
                expanded_clip = local_discovery.short_scores(subject, similar) or None
            except Exception:
                expanded_clip = None
        try:
            if trained:
                result = searcher.search(query, visual_scores=scoped,
                                         restrict_to=set(scoped or ()), boosts=boosts)
            else:
                result = searcher.search(query, visual_scores=expanded_clip, boosts=boosts)
        except SearchUnavailable:
            return fallback()
        if result.get("search_mode") != "图文融合":
            return fallback()
        result = _merge_annotation_hits(result, query, manual_only=True, blend_posts=not scoped)
        return {**result, "items": local_discovery.representative_hits(result["items"])}

    @app.post("/api/search")
    def search_images(request: SearchRequest):
        if config.local_library_enabled():
            result = desktop_search(request.query)
        else:
            # Search degrades instead of failing: when the paid text route is unreachable
            # (no Key, offline, quota, timeout) the local annotation + CLIP channels answer.
            if demo_cache.offline():
                result = searcher.search_local(request.query)
            else:
                try:
                    result = searcher.search(request.query)
                except SearchUnavailable:
                    result = searcher.search_local(request.query)
                else:
                    if result.get("status") == "index_empty":
                        local = searcher.search_local(request.query)
                        if local.get("items"):
                            result = local
            result = _merge_annotation_hits(result, request.query)
        if result.get("status") == "ok":
            result.setdefault("search_mode", "本地检索")
            try:
                if search_terms.missing_main_object(request.query):
                    # Long sentence whose head object is absent from the library:
                    # prefer an empty result over unrelated neighbours.
                    result = {**result, "items": [], "degraded": True}
            except Exception:
                pass
            # Last step for every channel (paid fusion, local retrieval, annotations):
            # evidence-based relevance gate + strong/weak marks.
            result = search_terms.apply_relevance(result, request.query)
        return result

    @app.post("/api/recommendations")
    def recommend_images(request: RecommendationRequest):
        """Read-only; never pins. One semantic search per request."""
        if demo_cache.offline():
            raise HTTPException(503, "离线演示仅支持已缓存的三个任务")
        try:
            return recommender.recommend(request.brief, request.previous_brief)
        except EmptyEffectiveQuery as error:
            raise HTTPException(422, detail={"code": "only_exclusions", "message": str(error)}) from None
        except RecommendationUnavailable:
            raise HTTPException(503, detail={"code": "recommendation_unavailable", "message": "推荐服务暂不可用"}) from None

    def creative_options(request):
        result={'include_nonvisual':request.include_nonvisual}
        if request.color_need:result['color_need']=[c.model_dump() for c in request.color_need]
        if request.project_id is not None:result['project_id']=request.project_id
        if request.negative_ids:result['negative_ids']=request.negative_ids
        return result

    @app.post('/api/palette/extract')
    async def palette_extract(file: UploadFile = File(...)):
        from .importer import MAX_BYTES
        from .colors import extract
        from io import BytesIO
        data=await file.read(MAX_BYTES+1)
        if len(data)>MAX_BYTES:raise HTTPException(413,'图片超过50MB')
        try:return {'colors':[{'hex':h,'ratio':r}for h,r in extract(BytesIO(data))]}
        except Exception:raise HTTPException(422,'图片无法读取') from None

    class FeedbackRequest(BaseModel):
        image_id: StrictInt
        purpose: StrictStr
        usable: StrictBool

    @app.put('/api/projects/{project_id}/recommendation-feedback')
    def project_feedback(project_id:int,request:FeedbackRequest):
        try:feedback.record(project_id,request.image_id,request.purpose,request.usable)
        except KeyError:raise HTTPException(404,'项目或图片不存在') from None
        except ValueError:raise HTTPException(422,'反馈参数无效') from None
        return {'saved':True}

    class MoreLikeRequest(RecommendationRequest):
        image_id: StrictInt
        purpose: StrictStr

    @app.post('/api/recommendations/more-like')
    def recommendation_more_like(request:MoreLikeRequest):
        try:return creative.more_like(request.brief,request.purpose,request.image_id,
            previous=request.previous_brief,**creative_options(request))
        except KeyError:raise HTTPException(404,'项目或图片不存在') from None
        except (ValueError,SimilarityUnavailable):raise HTTPException(503,'本地相似推荐暂不可用') from None
        except Exception:raise HTTPException(503,'该栏推荐暂不可用') from None

    @app.post('/api/recommendations/stream')
    def recommendation_stream(request:RecommendationRequest):
        import json,queue,threading
        from fastapi.responses import StreamingResponse
        from starlette.concurrency import run_in_threadpool
        events=queue.Queue()
        def work():
            cached=None
            try:
                cached=demo_cache.lookup(request.brief,request.previous_brief,request.include_nonvisual) if not(request.color_need or request.project_id or request.negative_ids) else None
                if demo_cache.offline():
                    if cached:events.put({'event':'done','result':cached})
                    else:events.put({'event':'error','message':'离线演示只支持原缓存任务'})
                    return
                result=creative.recommend(request.brief,request.previous_brief,**creative_options(request),
                    on_column=lambda partial:events.put({'event':'column','result':partial}))
                if cached and any(c.get('status')=='review_failed' for c in result.get('columns',[])):result=cached
                events.put({'event':'done','result':result})
            except Exception:
                if cached:events.put({'event':'done','result':cached})
                else:events.put({'event':'error','message':'推荐服务暂不可用，请重试'})
            finally:events.put(None)
        async def generate():
            threading.Thread(target=work,name='creative-stream',daemon=True).start()
            while True:
                event=await run_in_threadpool(events.get)
                if event is None:break
                yield json.dumps(event,ensure_ascii=False)+'\n'
        return StreamingResponse(generate(),media_type='application/x-ndjson',headers={'Cache-Control':'no-store','X-Accel-Buffering':'no'})

    @app.post("/api/recommendations/columns")
    def recommend_columns(request: RecommendationRequest):
        """Backend-only original-plan path: three columns + per-column visual review."""
        cached = demo_cache.lookup(request.brief, request.previous_brief, request.include_nonvisual)
        if demo_cache.offline():
            if cached: return cached
            raise HTTPException(503, "离线演示仅支持已缓存的三个任务")
        try:
            if config.local_library_enabled() and not config.ai_settings()["api_key"]:
                _, effective = resolve_brief(request.brief, request.previous_brief)
                found = local_search(effective, colors=False)
                with readonly_connection(config.db_path()) as conn:
                    if request.project_id is not None and not conn.execute(
                        "SELECT 1 FROM projects WHERE id=? AND deleted_at IS NULL",
                        (request.project_id,)
                    ).fetchone():
                        raise HTTPException(404, "项目不存在")
                    return local_discovery.local_columns(
                        conn, effective, found, request.color_need, set(request.negative_ids or ())
                    )
            result = creative.recommend(request.brief, request.previous_brief, **creative_options(request))
            if cached and (result.get('status') == 'index_empty' or
                           any(c.get('status') == 'review_failed' for c in result.get('columns', []))):
                return cached
            return result
        except EmptyEffectiveQuery as error:
            raise HTTPException(422, detail={"code": "only_exclusions", "message": str(error)}) from None
        except CreativeUnavailable:
            if cached: return cached
            raise HTTPException(503, detail={"code": "recommendation_unavailable", "message": "推荐服务暂不可用"}) from None

    @app.get("/api/sync/huaban/status")
    def huaban_sync_status():
        return {**huaban_syncer.state(), **huaban_auto.status(), **connection.describe("huaban",huaban_syncer.state())}

    watchers = {"xhs": xhs_auto, "huaban": huaban_auto,
                "pinterest": pinterest_auto, "zcool": zcool_auto}

    @app.get("/api/settings")
    def get_settings():
        saved = ui_settings.read()
        blocked = bool(safety.state().get("auto_blocked"))
        return {"auto_sync": bool(saved.get("auto_sync")) and allow_auto_sync and not blocked,
                "auto_sync_allowed": allow_auto_sync,
                "auto_sync_lock_blocked": blocked,
                "auto_platforms": saved.get("auto_platforms"),
                "xhs": xhs_auto.status()["auto"],
                "huaban": huaban_auto.status()["auto"],
                "pinterest": pinterest_auto.status()["auto"],
                "zcool": zcool_auto.status()["auto"]}

    @app.post("/api/settings/auto-sync")
    def set_auto_sync(enabled: bool):
        if enabled and not allow_auto_sync:
            raise HTTPException(403, "只有从入口启动的正式实例可以开启自动同步")
        blocked = bool(safety.state().get("auto_blocked"))
        if enabled and blocked:
            raise HTTPException(423, safety.MESSAGE)
        ui_settings.write(auto_sync=enabled)
        saved = ui_settings.read()
        for name, watcher in watchers.items():
            watcher.set_enabled(enabled and allow_auto_sync and not blocked
                                and bool(saved["auto_platforms"].get(name, True)))
        return get_settings()

    @app.post("/api/settings/auto-sync/{platform}")
    def set_platform_auto(platform: str, enabled: bool):
        if platform not in watchers:
            raise HTTPException(404, "未知平台")
        ui_settings.write(auto_platforms={platform: enabled})
        saved = ui_settings.read()
        blocked = bool(safety.state().get("auto_blocked"))
        watchers[platform].set_enabled(bool(saved["auto_sync"]) and allow_auto_sync
                                       and not blocked and enabled)
        return get_settings()

    @app.get("/api/sync/pinterest/status")
    def pinterest_sync_status():
        return {**pinterest_syncer.state(), **pinterest_auto.status(), **connection.describe("pinterest",pinterest_syncer.state())}

    @app.post("/api/sync/pinterest/start")
    def pinterest_sync_start():
        if not pinterest_syncer.start_sync():
            raise HTTPException(409, "已有 Pinterest 同步或登录在进行")
        return pinterest_syncer.state()

    @app.post("/api/sync/pinterest/login")
    def pinterest_sync_login():
        if not pinterest_syncer.start_login():
            raise HTTPException(409, "已有 Pinterest 同步或登录在进行")
        return pinterest_syncer.state()

    @app.get("/api/sync/zcool/status")
    def zcool_sync_status():
        return {**zcool_syncer.state(), **zcool_auto.status(), **connection.describe("zcool",zcool_syncer.state())}

    @app.post("/api/sync/zcool/start")
    def zcool_sync_start():
        if not zcool_syncer.start_sync():
            raise HTTPException(409, "已有站酷同步或登录在进行")
        return zcool_syncer.state()

    @app.post("/api/sync/zcool/backfill")
    def zcool_sync_backfill():
        if not zcool_syncer.start_backfill():
            raise HTTPException(409, "已有站酷同步或登录在进行")
        return zcool_syncer.state()

    @app.post("/api/sync/zcool/login")
    def zcool_sync_login():
        if not zcool_syncer.start_login():
            raise HTTPException(409, "已有站酷同步或登录在进行")
        return zcool_syncer.state()

    @app.post("/api/sync/huaban/start")
    def huaban_sync_start():
        if not huaban_syncer.start_sync():
            raise HTTPException(409, "已有花瓣同步或登录在进行")
        return huaban_syncer.state()

    @app.post("/api/sync/huaban/login")
    def huaban_sync_login():
        if not huaban_syncer.start_login():
            raise HTTPException(409, "已有花瓣同步或登录在进行")
        return huaban_syncer.state()

    @app.post('/api/sync/{platform}/verify')
    def start_verification(platform: str):
        runner={'zcool':zcool_syncer,'huaban':huaban_syncer}.get(platform)
        if runner is None: raise HTTPException(404,'此平台不提供验证窗口')
        if not runner.start_verification(): raise HTTPException(409,'已有同步、登录或验证在进行')
        return runner.state()

    @app.post('/api/sync/{platform}/verification-complete')
    def confirm_verification(platform: str):
        runner={'zcool':zcool_syncer,'huaban':huaban_syncer}.get(platform)
        if runner is None: raise HTTPException(404,'此平台不提供验证窗口')
        if not runner.confirm_verification(): raise HTTPException(409,'验证窗口尚未打开')
        return runner.state()

    @app.get("/api/health")
    def health():
        return {"ok": True}

    @app.get("/api/wechat", response_class=PlainTextResponse)
    def verify_wechat(signature: str, timestamp: str, nonce: str, echostr: str):
        verify_signature(signature, timestamp, nonce)
        return echostr

    @app.post("/api/phone-import/pairing")
    def start_phone_pairing(request: Request, transport: str = "lan"):
        from fastapi.responses import JSONResponse

        if not config.local_library_enabled() or request.headers.get("x-library-client") != "desktop":
            raise HTTPException(404)
        try:
            pairing = usb_phone_pairing if transport == "usb" else phone_pairing
            url = pairing.start()
        except OSError:
            raise HTTPException(409, "电脑暂时没有可用于手机配对的局域网。") from None
        return JSONResponse({"url": url}, headers={"Cache-Control": "no-store"})

    @app.delete("/api/phone-import/pairing")
    def end_phone_pairing(request: Request, transport: str = "lan"):
        if not config.local_library_enabled() or request.headers.get("x-library-client") != "desktop":
            raise HTTPException(404)
        pairing = usb_phone_pairing if transport == "usb" else phone_pairing
        pairing.stop()
        return {"stopped": True}

    @app.get("/api/import/status")
    def import_status():
        """Cooldown lock + last import outcome per entry; the UI disables buttons from this."""
        return importers.status()

    @app.post("/api/import/captured")
    def import_captured(payload: dict):
        try:
            return importers.import_captured(payload)
        except importers.ImportRejected as exc:
            raise HTTPException(400, str(exc)) from None

    @app.post("/api/import/share")
    def import_share(payload: dict):
        urls = payload.get("urls")
        if urls is None and payload.get("url"):
            urls = [payload["url"]]
        try:
            return importers.ShareImporter().import_urls(urls)
        except importers.SafetyBlocked:
            raise HTTPException(409, detail={"code": "xhs_cooldown",
                                             "message": "小红书冷却中，已停止联网导入"}) from None
        except importers.ImportRejected as exc:
            raise HTTPException(400, str(exc)) from None

    @app.get("/api/images")
    def get_images(include_palette: bool = False):
        return importer.list_images(include_palette=include_palette)

    @app.post("/api/images/import")
    def import_images(files: list[UploadFile] = File(...), modified: str | None = Form(None)):
        # modified: JSON array of File.lastModified values (epoch ms), aligned by index.
        modified_times = importer.parse_modified(modified, len(files))
        uploads = [importer.Upload(f.filename or "unnamed", f.file.read(), modified_at)
                   for f, modified_at in zip(files, modified_times)]
        return importer.import_images(uploads)

    @app.delete("/api/images/{image_id}")
    def delete_image(image_id: int):
        """软删除：只写 images.deleted_at；文件和记录都留着，恢复脚本清标记即回来。"""
        result = importer.delete_image(image_id)
        if result is None:
            raise HTTPException(404, "图片不存在")
        return result

    @app.get("/api/images/{image_id}")
    def get_image(image_id: int):
        image = importer.get_image(image_id)
        if image is None:
            raise HTTPException(404, "图片不存在")
        runners={'xhs':syncer,'pinterest':pinterest_syncer,'zcool':zcool_syncer,'huaban':huaban_syncer}
        for post in image.get('posts',[]):
            runner=runners.get(post['platform'])
            if runner:post.update(connection.describe(post['platform'],runner.state()))
        return {**image, **analysis.get_analysis(image_id),
                "annotation": annotations.get(image_id),
                # 生效标题：人工优先，其次分析生成（前端直接读这个字段）
                "title": titles.effective_title(image_id)}

    @app.put("/api/images/{image_id}/annotation")
    def update_annotation(image_id: int, request: AnnotationEdit):
        """Field-level user annotation overlay; null+null restores the AI content.

        Only keys present in the request touch the overlay: omitted fields keep
        their stored value, explicit null clears (falls back to AI). Sending
        neither key is invalid.
        """
        present = request.model_fields_set & {"description", "tags"}
        if not present:
            raise HTTPException(422, detail={"code": "invalid_annotation"})
        description = request.description if "description" in present else annotations.UNSET
        tags = request.tags if "tags" in present else annotations.UNSET
        try:
            annotation = annotations.save(image_id, description, tags)
        except KeyError:
            raise HTTPException(404, detail={"code": "image_not_found"}) from None
        except ValueError:
            raise HTTPException(422, detail={"code": "invalid_annotation"}) from None
        return {"annotation": annotation}

    @app.put("/api/images/{image_id}/title")
    def update_title(image_id: int, request: TitleEdit):
        """人工标题覆盖；null 清掉人工标题、回到自动标题。

        自动生成（视觉分析 / scripts/backfill_titles.py）只写 analyses.title，
        永远不碰 image_titles_manual，所以人工标题不会被覆盖。
        """
        try:
            stored = titles.set_manual(image_id, request.title)
        except KeyError:
            raise HTTPException(404, detail={"code": "image_not_found"}) from None
        except ValueError as exc:
            raise HTTPException(422, detail={"code": "invalid_title", "message": str(exc)}) from None
        return {"manual_title": stored, "title": titles.effective_title(image_id)}

    @app.get("/api/analysis/status")
    def analysis_status():
        return {**analysis.summary(), **worker.state()}

    @app.post("/api/analysis/start")
    def analysis_start():
        worker.start()
        return analysis_status()

    @app.post("/api/analysis/pause")
    def analysis_pause():
        worker.pause()
        return analysis_status()

    @app.post("/api/analysis/retry-failed")
    def analysis_retry_failed():
        return {"requeued": analysis.retry()}

    @app.post("/api/analysis/{image_id}/retry")
    def analysis_retry(image_id: int):
        return {"requeued": analysis.retry(image_id)}

    @app.get("/api/sync/xhs/cooldown")
    def xhs_cooldown():
        lock = safety.state()
        return {field: lock[field] for field in ("blocked", "auto_blocked", "manual_hold", "message")}

    @app.get("/api/sync/xhs/status")
    def sync_status():
        return {**syncer.state(), **xhs_auto.status(), **connection.describe("xhs",syncer.state())}

    @app.post("/api/sync/xhs/start")
    def sync_start():
        if safety.state()["blocked"]: raise HTTPException(423,safety.MESSAGE)
        if not syncer.start_sync():
            raise HTTPException(409, "已有同步或登录在进行")
        return syncer.state()

    @app.post("/api/sync/xhs/login")
    def sync_login():
        if safety.state()["blocked"]: raise HTTPException(423,safety.MESSAGE)
        if not syncer.start_login():
            raise HTTPException(409, "已有同步或登录在进行")
        return syncer.state()


    @app.get("/api/posts/{post_id}/source")
    def open_post_source(post_id: int):
        """Open the original post. XHS stores a token-free URL, so resolve a token in memory
        (never DB/logs) and redirect to the verified note; otherwise explain plainly."""
        conn = db.connect()
        try:
            row = conn.execute(
                "SELECT id, platform, source_id, url, title FROM posts WHERE id = ?", (post_id,)
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            raise HTTPException(status_code=404, detail="图片不存在")
        # 链接导入存的是用户当时复制的原链接（小红书短链或带 xsec_token 的长链）；有就直接跳。
        url = row["url"] or ""
        share_link = url.startswith(("http://xhslink.", "https://xhslink."))
        if url and (row["platform"] != "xhs" or "xsec_token=" in url or share_link):
            return RedirectResponse(url, status_code=302)
        if row["platform"] != "xhs":
            if not row["url"]:
                return _source_page("这条记录没有原帖链接。", post_id)
            return RedirectResponse(row["url"], status_code=302)
        if safety.state()["blocked"]:
            return _source_page(safety.MESSAGE, post_id)
        if syncer.busy:
            return _source_page("正在同步小红书，请等这次同步结束再打开原帖。", post_id)
        if not row["source_id"]:
            return _source_page("这条记录没有笔记 ID，无法重新取得入口。", post_id)
        try:
            result = xhs_source.resolve_note(sync_runner.profile_dir(), row["source_id"], row["title"] or "")
        except xhs_source.SourceUnavailable as exc:
            result = {"ok": False, "reason": exc.reason}
        except Exception:
            result = {"ok": False, "reason": "打开原帖失败，请稍后再试。"}
        if result.get("ok"):
            if xhs_source.probe_open(result["url"], result.get("title") or ""):
                return RedirectResponse(result["url"], status_code=302)
            return _source_page(
                "已按笔记 ID 重新取得入口，并核对了笔记标题；但本机浏览器打开时小红书返回"
                "「安全限制 / 当前网络存在风险（300012）」，所以没有自动跳转。"
                "可以换一个可靠网络后点“重试”，或在小红书 App 里查看这篇笔记。",
                post_id, title=result.get("title") or "")
        return _source_page(result.get("reason") or "暂时无法打开原帖。", post_id)

    @app.api_route("/images/thumb-v1/{filename}", methods=["GET", "HEAD"], include_in_schema=False)
    def image_thumbnail(filename: str):
        try:
            path = thumbnails.ensure(filename)
        except FileNotFoundError:
            raise HTTPException(404, "找不到图片") from None
        except (OSError, ValueError, thumbnails.Image.DecompressionBombError):
            return RedirectResponse(f"/images/{filename}", headers={"Cache-Control": "no-store"})
        return FileResponse(path, media_type="image/webp", headers={"Cache-Control": thumbnails.CACHE_CONTROL})

    app.mount("/images", thumbnails.ImageFiles(directory=config.images_dir()), name="images")
    return app
