import asyncio
from base64 import b64encode
from contextlib import asynccontextmanager, closing
from datetime import datetime
import hmac
from io import BytesIO
import logging
from mimetypes import guess_type
import os
from pathlib import Path
import re

from dotenv import dotenv_values
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import MutableHeaders, UploadFile
from starlette.formparsers import MultiPartException, MultiPartParser
from starlette.middleware.gzip import DEFAULT_EXCLUDED_CONTENT_TYPES, GZipMiddleware

ROOT = Path(__file__).resolve().parents[2]
STORAGE = ROOT / ".run" / "public-demo"
SITE = ROOT / ".run" / "public-site"
# 2.0 验收快照：构建时 base=/v2/，这里只读，永远不要替换（见 docs/v2-snapshot.md）。
SITE_V2 = ROOT / ".run" / "public-site-v2"
DOWNLOADS = ROOT / ".run" / "public-downloads"
SAMPLES = ROOT / "samples"
LOGGER = logging.getLogger(__name__)
FILE_LIMIT = 20 * 1024 * 1024
BODY_LIMIT = 8 * FILE_LIMIT + 1024 * 1024
DAILY_LIMIT = 200
UNAVAILABLE = "演示版不提供此功能"

# 演示版静态缓存：Vite 产物文件名带内容哈希，可以放心长缓存；入口 HTML 必须回源校验，
# 否则发布新版后浏览器还在跑旧壳。品牌图和介绍样例没有哈希，只给中等缓存。
ASSET_CACHE_CONTROL = "public, max-age=31536000, immutable"
BRAND_CACHE_CONTROL = "public, max-age=86400"
SHELL_CACHE_CONTROL = "no-cache"

# GZip 默认排除图片 / 音视频 / woff 字体 / zip / gRPC / SSE；本演示的 woff2 由
# mimetypes 推断成 application/octet-stream，本身已压缩，再压一次没有收益。
GZIP_EXCLUDE_CONTENT_TYPES = DEFAULT_EXCLUDED_CONTENT_TYPES + ("application/octet-stream",)


def negotiates_content_encoding(content_type: str) -> bool:
    """该 Content-Type 是否会随 Accept-Encoding 变化（与 GZip 的可压缩类型对齐）。"""
    media_type = content_type.partition(";")[0].strip().lower()
    if media_type in {"text/event-stream", "application/octet-stream"}:
        return False
    return media_type.startswith("text/") or media_type in {
        "application/json",
        "application/javascript",
        "application/xml",
        "application/xhtml+xml",
        "application/manifest+json",
        "image/svg+xml",
    }


class VaryAcceptEncodingMiddleware:
    """GZip 只给真正压缩过的响应加 Vary；这次用明文传的可压缩响应在这里补上。"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_vary(message):
            if message["type"] == "http.response.start" and message["status"] in (200, 206):
                headers = MutableHeaders(scope=message)
                vary = ", ".join(headers.getlist("vary")).lower()
                if ("content-encoding" not in headers
                        and "accept-encoding" not in vary
                        and negotiates_content_encoding(headers.get("content-type", ""))):
                    headers.add_vary_header("Accept-Encoding")
            await send(message)

        await self.app(scope, receive, send_with_vary)


class CachedStaticFiles(StaticFiles):
    """给静态文件响应加 Cache-Control；404 之类的错误响应不加，免得缺文件被缓存一年。"""

    def __init__(self, *, directory, cache_control):
        super().__init__(directory=directory)
        self.cache_control = cache_control

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        if response.status_code in (200, 206, 304):
            response.headers.setdefault("cache-control", self.cache_control)
        if response.status_code == 304:
            # 304 没有 Content-Type（NotModifiedResponse 只保留少数头），按扩展名推断。
            if negotiates_content_encoding(guess_type(path)[0] or ""):
                response.headers.setdefault("vary", "Accept-Encoding")
        return response


READ_API = re.compile(
    r"/api/(?:health|wechat|demo/(?:import-options|wechat-qr)|images(?:/\d+(?:/similar)?)?|stats/(?:library|platforms|daily|device)"
    r"|projects(?:/\d+(?:/pins|/pip-scene)?)?|posts/\d+/source|reference/review|reference-labels"
    r"|analysis/status|similar/status|import/status)"
)
POST_API = {
    "/api/wechat",
    "/api/events",
    "/api/stats/devices",
    "/api/search", "/api/recommendations", "/api/recommendations/columns",
    "/api/recommendations/more-like", "/api/recommendations/stream",
}
PROJECT_WRITES = {
    "POST": re.compile(r"/api/projects(?:/\d+/pins)?"),
    "PATCH": re.compile(r"/api/projects/\d+(?:/pins/\d+/position)?"),
    # 软删除也放行：项目改名/删除、图片删除只写 deleted_at，可由恢复脚本还原。
    "DELETE": re.compile(r"/api/projects/\d+(?:/pins/\d+(?:/purposes/(?:layout|style|palette|subject))?)?"
                         r"|/api/images/\d+"),
}

# 演示版允许的普通整理操作：改用户标签、对待确认图片点「保留 / 不是参考」。
USER_WRITES = {
    "PUT": re.compile(r"/api/images/\d+/annotation"),
    "POST": re.compile(r"/api/reference/decisions(?:/[^/]+/undo)?"),
}


def request_allowed(method: str, path: str) -> bool:
    """Demo boundary: read APIs plus the small set of normal organizing writes."""
    if method == "GET" and READ_API.fullmatch(path):
        return True
    if method == "POST" and path in POST_API:
        return True
    pattern = PROJECT_WRITES.get(method)
    if pattern and pattern.fullmatch(path):
        return True
    pattern = USER_WRITES.get(method)
    return bool(pattern and pattern.fullmatch(path))


def configure_demo() -> None:
    if not STORAGE.resolve().is_relative_to((ROOT / ".run").resolve()) or STORAGE.is_symlink():
        raise RuntimeError("演示数据目录必须位于项目的 .run/public-demo")
    dot_env = dotenv_values(ROOT / ".env")
    model_dir = os.getenv("CHINESE_CLIP_MODEL_DIR") or dot_env.get("CHINESE_CLIP_MODEL_DIR")
    os.environ.update({
        "WOMINMIN_DATA_DIR": str(STORAGE),
        "WOMINMIN_SETTINGS_FILE": str(STORAGE / "settings.json"),
        "WOMINMIN_SAFETY_DIR": str(STORAGE / "safety"),
        "WOMINMIN_REFERENCE_CALIBRATION": str(STORAGE / "reference-calibration.json"),
        "WOMINMIN_DEMO_CACHE": str(STORAGE / "offline-cache.json"),
        "WOMINMIN_OFFLINE_DEMO": "0",
        "WOMINMIN_DEMO": "0",
        "WOMINMIN_REFERENCE_CANDIDATE": "0",
        "WOMINMIN_DESKTOP_LIBRARY": "1",
        "WOMINMIN_ALLOW_AUTO_SYNC": "0",
        "ANALYSIS_AUTO_START": "1",
        "CHINESE_CLIP_MODEL_DIR": str(model_dir or ROOT / "data/models/chinese-clip-base"),
    })


class UploadError(MultiPartException):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


async def read_shortcut_image(request: Request, importer, content_type: str):
    size_error = "文件太大，单张最多 20MB"
    size = request.headers.get("content-length")
    if size is not None and (not size.isdecimal() or int(size) > FILE_LIMIT):
        raise UploadError(413, size_error)
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > FILE_LIMIT:
            raise UploadError(413, size_error)
        data.extend(chunk)
    extension = next((ext for ext, mime in importer.FORMATS.values() if mime == content_type), "img")
    filename = f"shortcut-{datetime.now():%Y%m%d-%H%M%S-%f}.{extension}"
    return [importer.Upload(filename, bytes(data))]


async def read_form(request: Request, importer):
    if not request.headers.get("content-type", "").lower().startswith("multipart/form-data"):
        raise UploadError(415, "请用表单上传图片，字段名填 files")
    size = request.headers.get("content-length")
    if size is not None and (not size.isdecimal() or int(size) > BODY_LIMIT):
        raise UploadError(413, "上传内容太大，一次最多 8 张，单张最多 20MB")

    async def bounded_stream():
        remaining = BODY_LIMIT
        async for chunk in request.stream():
            remaining -= len(chunk)
            if remaining < 0:
                raise UploadError(413, "上传内容太大，一次最多 8 张，单张最多 20MB")
            yield chunk

    try:
        parser = MultiPartParser(request.headers, bounded_stream(), max_files=8, max_fields=1)
        form = await parser.parse()
        try:
            files = form.getlist("files")
            if not (1 <= len(files) <= 8) or any(not isinstance(file, UploadFile) for file in files):
                raise UploadError(400, "请选择 1～8 张图片，字段名填 files")
            if any(file.size is None or file.size > FILE_LIMIT for file in files):
                raise UploadError(413, "文件太大，单张最多 20MB")
            modified = form.get("modified")
            try:
                times = importer.parse_modified(modified, len(files)) if isinstance(modified, str) else [None] * len(files)
            except HTTPException as exc:
                raise UploadError(exc.status_code, str(exc.detail)) from None
            return [importer.Upload(Path(file.filename or "图片").name, await file.read(), stamp)
                    for file, stamp in zip(files, times)]
        finally:
            await form.close()
    except UploadError:
        raise
    except (MultiPartException, ValueError) as exc:
        if "Too many files" in str(getattr(exc, "message", "")):
            raise UploadError(400, "一次最多上传 8 张图片") from None
        raise UploadError(400, "上传表单有误，请用 files 字段添加图片") from None


def create_public_app():
    configure_demo()
    from app import analysis, config, db, importer, link_import, usage_events, wechat
    from app.embedding import dimensions
    from app.embedding.adapter import EmbeddingAdapter
    from app.main import create_app

    if not (SITE / "index.html").is_file():
        raise RuntimeError("找不到公网演示网页，请运行 start-public-demo.ps1")
    embedder = EmbeddingAdapter(dimension_schemes=dimensions.SCHEMES)
    options = {"concurrency": config.analysis_concurrency()} if hasattr(config, "analysis_concurrency") else {}
    # Images in this library are explicitly selected for the demo. Process all of them.
    worker = analysis.Worker(on_done=embedder.enqueue, **options)
    app = create_app(worker=worker, embedder=embedder)
    with closing(db.connect()) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS public_upload_days (day TEXT PRIMARY KEY, count INTEGER NOT NULL)")

    original_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def start_public_queue(application):
        async with original_lifespan(application):
            if worker.state()["configured"] and not worker.running:
                worker.start()
            yield

    app.router.lifespan_context = start_public_queue

    def import_public(uploads, post=None):
        today = datetime.now().date().isoformat()

        def queue_with_quota(conn, image_id):
            cursor = conn.execute("""
                INSERT INTO public_upload_days(day, count) VALUES (?, 1)
                ON CONFLICT(day) DO UPDATE SET count = count + 1 WHERE count < ?
            """, (today, DAILY_LIMIT))
            if not cursor.rowcount:
                raise UploadError(429, "演示库今天已收满 200 张，请明天再试")
            analysis.ensure_analysis(conn, image_id)

        return importer.import_images(uploads, on_analysis=queue_with_quota, post=post)

    upload_slots = asyncio.Semaphore(2)

    async def receive_upload(request: Request, shortcut: bool):
        try:
            async with upload_slots:
                content_type = request.headers.get("content-type", "").partition(";")[0].strip().lower()
                if shortcut and content_type.startswith("image/"):
                    uploads = await read_shortcut_image(request, importer, content_type)
                else:
                    uploads = await read_form(request, importer)
                result = await run_in_threadpool(import_public, uploads)
        except UploadError as exc:
            # 以前这里静默返回，线上失败时日志里什么都看不到；status + message 足够定位。
            LOGGER.warning("演示版导入被拒绝：状态 %s，%s", exc.status, exc.message)
            if shortcut:
                return PlainTextResponse(exc.message, status_code=exc.status)
            return JSONResponse({"detail": exc.message}, status_code=exc.status)
        LOGGER.info("演示版导入完成：新增 %d 张，重复 %d 张，失败 %d 张",
                    len(result["added"]), len(result["duplicates"]), len(result["failed"]))
        if not shortcut:
            return JSONResponse(result)
        added, dupes, failed = (len(result[k]) for k in ("added", "duplicates", "failed"))
        if not added and not dupes:
            return PlainTextResponse(result["failed"][0]["reason"] if failed else "没有可收藏的图片", status_code=400)
        if dupes == 1 and not added and not failed:
            return PlainTextResponse("这张已经在库里了")
        message = [f"已收藏 {added} 张"] if added else []
        if dupes:
            message.append(f"{dupes} 张已经在库里了")
        if failed:
            message.append(f"{failed} 张未能导入")
        return PlainTextResponse("，".join(message))

    def import_link(url: str) -> dict:
        """跟随分享链接 → 下载图片 → 作为一个帖子入库（与图片导入共用 200 张/天上限）。"""
        target = link_import.inspect_link(url)
        existing = link_import.find_existing(target)
        if existing:
            return {"ok": True, "title": existing["title"], "count": existing["count"],
                    "post_id": existing["post_id"], "message": "这条之前收过啦"}
        uploads = [importer.Upload(name, data) for name, data in link_import.download_images(target)]
        result = import_public(
            uploads,
            post={
                "platform": target.platform,
                "source_id": target.source_id,
                "url": target.original_url,
                "title": target.title,
                "description": target.description,
            },
        )
        added = len(result["added"])
        if not added:
            return {"ok": False, "title": target.title, "count": 0, "post_id": None,
                    "message": "这几张图之前已经收过了，在素材库里搜搜看"}
        usage_events.record("server-link", [{"type": "import", "page": "server",
                                            "detail": {"method": "link", "count": added}}])
        return {"ok": True, "title": target.title, "count": added, "post_id": result["post_id"]}

    async def import_wechat_image(pic_url: str):
        try:
            async with upload_slots:
                data = await wechat.download_image(pic_url, FILE_LIMIT)
                result = await run_in_threadpool(import_public, [importer.Upload("微信图片", data)])
            if result["failed"]:
                LOGGER.warning("微信图片未能通过导入校验")
        except UploadError as exc:
            LOGGER.warning("微信图片未导入（状态 %s）", exc.status)
        except Exception as exc:
            LOGGER.warning("微信图片后台处理失败（%s）", type(exc).__name__)

    @app.post("/api/wechat")
    async def receive_wechat(
        request: Request, background_tasks: BackgroundTasks,
        signature: str, timestamp: str, nonce: str,
    ):
        wechat.verify_signature(signature, timestamp, nonce)
        message = await wechat.read_message(request)
        message_type = message.findtext("MsgType")
        if message_type == "image":
            background_tasks.add_task(import_wechat_image, message.findtext("PicUrl", "").strip())
            return wechat.text_reply(message, "收到啦，正在收进素材库")
        if message_type == "event" and message.findtext("Event") == "subscribe":
            return wechat.text_reply(message, "嗨，我是图然想起！发张图片给我，我帮你收进素材库。")
        return wechat.text_reply(message, "直接发图片给我，就会收进『图然想起』")

    @app.post("/api/tutu/link")
    async def receive_link(request: Request):
        length = request.headers.get("content-length")
        if length is not None and (not length.isdecimal() or int(length) > 16 * 1024):
            return JSONResponse({"ok": False, "message": "请求体太大"}, status_code=413)
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"ok": False, "message": "请用 JSON 发送：{\"url\": \"分享链接\"}"},
                                status_code=400)
        url = body.get("url") if isinstance(body, dict) else None
        if not isinstance(url, str) or not url.strip():
            return JSONResponse({"ok": False, "message": "没收到链接"}, status_code=400)
        async with upload_slots:
            try:
                result = await run_in_threadpool(import_link, url.strip())
            except link_import.LinkError as exc:
                return JSONResponse({"ok": False, "message": str(exc)})
            except UploadError as exc:
                return JSONResponse({"ok": False, "message": exc.message})
            except Exception:
                LOGGER.exception("链接导入失败")
                return JSONResponse({"ok": False, "message": "导入没成功，稍后再试"})
        return JSONResponse(result)

    # minimum_size=500：更小的响应压完反而更大；compresslevel=6：体积与 CPU 的平衡点。
    # 排除项见 GZIP_EXCLUDE_CONTENT_TYPES（图片 / 音视频 / 字体 / zip / SSE / octet-stream）。
    app.add_middleware(
        GZipMiddleware,
        minimum_size=500,
        compresslevel=6,
        exclude_content_types=GZIP_EXCLUDE_CONTENT_TYPES,
    )

    @app.middleware("http")
    async def public_boundary(request: Request, call_next):
        path, method = request.url.path, request.method
        if path == "/api/shortcut/upload" and method == "POST":
            token = os.getenv("SHORTCUT_UPLOAD_TOKEN", "").strip()
            if not token:
                return PlainTextResponse("演示版还没配置上传码", status_code=503)
            header = request.headers.get("authorization", "")
            scheme, _, offered = header.partition(" ")
            if scheme.lower() != "bearer" or not hmac.compare_digest(offered.encode(), token.encode()):
                return PlainTextResponse("上传码不对", status_code=401)
            return await receive_upload(request, shortcut=True)
        if path == "/api/tutu/link" and method == "POST":
            token = os.getenv("SHORTCUT_UPLOAD_TOKEN", "").strip()
            if not token:
                return JSONResponse({"ok": False, "message": "演示版还没配置上传码"}, status_code=503)
            header = request.headers.get("authorization", "")
            scheme, _, offered = header.partition(" ")
            if scheme.lower() != "bearer" or not hmac.compare_digest(offered.encode(), token.encode()):
                return JSONResponse({"ok": False, "message": "上传码不对"}, status_code=401)
            return await call_next(request)
        if path == "/api/images/import" and method == "POST":
            return await receive_upload(request, shortcut=False)
        if path == "/api" or path.startswith("/api/"):
            if not request_allowed(method, path):
                return JSONResponse({"detail": UNAVAILABLE}, status_code=403)
        if path in {"/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"}:
            return JSONResponse({"detail": UNAVAILABLE}, status_code=403)
        return await call_next(request)

    app.mount("/assets", CachedStaticFiles(directory=SITE / "assets", cache_control=ASSET_CACHE_CONTROL),
              name="public-built-assets")
    (SITE_V2 / "assets").mkdir(parents=True, exist_ok=True)
    app.mount("/v2/assets", CachedStaticFiles(directory=SITE_V2 / "assets", cache_control=ASSET_CACHE_CONTROL),
              name="public-v2-assets")
    app.mount("/brand", CachedStaticFiles(directory=ROOT / "frontend" / "public" / "brand",
                                          cache_control=BRAND_CACHE_CONTROL), name="public-brand")
    app.mount("/lab-assets/show", CachedStaticFiles(directory=SAMPLES / "show",
                                                    cache_control=BRAND_CACHE_CONTROL),
              name="public-show-samples")
    app.mount("/intro-samples", CachedStaticFiles(directory=ROOT / "frontend/public/intro-samples",
                                                   cache_control=BRAND_CACHE_CONTROL),
              name="public-intro-samples")

    # 中间件顺序（Starlette 中后加的在更外层）：Vary → public_boundary → GZip → 路由。
    # GZip 必须在 public_boundary 内层：BaseHTTPMiddleware 会把响应体重新拆成分块，
    # GZip 若在它外层就永远走流式分支，Content-Length 只能删掉；放在内层则
    # 单块响应（JSON / HTML / 小 CSS）压缩后仍能给出准确的 Content-Length。
    # 谁能访问仍由 public_boundary 决定，安全边界不经过压缩层。
    app.add_middleware(VaryAcceptEncodingMiddleware)

    private_headers = {"Cache-Control": "private, no-store"}

    @app.get("/api/demo/import-options")
    def import_options():
        install_url = (os.getenv("SHORTCUT_INSTALL_URL") or dotenv_values(ROOT / ".env").get("SHORTCUT_INSTALL_URL") or "").strip()
        shortcut = None
        if re.fullmatch(r"https://(?:www\.)?icloud\.com/shortcuts/[a-fA-F0-9]{32}", install_url):
            import qrcode

            png = BytesIO()
            qrcode.make(install_url).save(png)
            shortcut = {"url": install_url, "qr": "data:image/png;base64," + b64encode(png.getvalue()).decode("ascii")}
        qr = STORAGE / "wechat-qr.png"
        return JSONResponse({"shortcut": shortcut, "wechat": qr.is_file() and not qr.is_symlink()}, headers=private_headers)

    @app.get("/api/demo/wechat-qr")
    def wechat_qr():
        qr = STORAGE / "wechat-qr.png"
        if not qr.is_file() or qr.is_symlink():
            return JSONResponse({"detail": "未配置测试号二维码"}, status_code=404, headers=private_headers)
        return FileResponse(qr, media_type="image/png", headers=private_headers)

    @app.get("/guides/api-key.pdf")
    def api_key_guide():
        return FileResponse(ROOT / "frontend/public/guides/api-key.pdf", media_type="application/pdf")

    @app.get("/favicon.svg")
    def favicon():
        return FileResponse(ROOT / "frontend/public/favicon.svg")

    # 安卓安装包单独放一个目录，不跟前端产物一起被替换；文件本身不进 git（.run/ 已忽略）。
    @app.get("/downloads/tutu.apk")
    def tutu_apk():
        apk = DOWNLOADS / "tutu.apk"
        if not apk.is_file() or apk.is_symlink():
            return JSONResponse({"detail": "安装包还没上传"}, status_code=404)
        return FileResponse(apk, media_type="application/vnd.android.package-archive",
                            filename="tutu.apk", headers={"Cache-Control": "no-cache"})

    # 匿名使用记录：一批事件一次请求，超量/非法事件静默丢弃，永远不返回错误给页面。
    @app.post("/api/events")
    async def receive_events(request: Request):
        length = request.headers.get("content-length")
        if length is not None and (not length.isdecimal() or int(length) > 256 * 1024):
            return JSONResponse({"ok": True, "accepted": 0})
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"ok": True, "accepted": 0})
        if not isinstance(body, dict):
            return JSONResponse({"ok": True, "accepted": 0})
        accepted = await run_in_threadpool(usage_events.record, body.get("device_id"), body.get("events"))
        return JSONResponse({"ok": True, "accepted": accepted})

    def stats_key_ok(key: str) -> bool:
        expected = (os.getenv("STATS_KEY") or dotenv_values(ROOT / ".env").get("STATS_KEY") or "").strip()
        return bool(expected) and hmac.compare_digest(str(key).encode(), expected.encode())

    @app.get("/stats")
    def stats_page(key: str = ""):
        if not stats_key_ok(key):
            return PlainTextResponse("Not Found", status_code=404)
        return HTMLResponse(usage_events.render_stats(), headers={"Cache-Control": "private, no-store"})

    # 统计页的「这台设备是我」：只接受带正确 STATS_KEY 的请求，错误一律 404。
    @app.get("/api/stats/device")
    def stats_device_state(key: str = "", device_id: str = ""):
        if not stats_key_ok(key):
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        return {"excluded": usage_events.is_excluded(device_id)}

    @app.post("/api/stats/devices")
    async def stats_device_toggle(request: Request):
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"ok": False}, status_code=400)
        if not isinstance(body, dict) or not stats_key_ok(str(body.get("key") or "")):
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        device_id = body.get("device_id")
        action = body.get("action")
        if not usage_events.device_ok(device_id) or action not in {"exclude", "include"}:
            return JSONResponse({"ok": False}, status_code=400)
        usage_events.set_excluded(device_id, action == "exclude")
        return {"ok": True, "excluded": action == "exclude"}

    # 2.0 验收快照：页面路由都回快照自己的 index；/api、/images 仍共用主站，不另起数据。
    def v2_shell():
        index = SITE_V2 / "index.html"
        if not index.is_file():
            return PlainTextResponse("2.0 快照还没构建", status_code=404)
        return FileResponse(index, headers={"Cache-Control": SHELL_CACHE_CONTROL})

    @app.get("/v2")
    @app.get("/v2/")
    def v2_index():
        return v2_shell()

    @app.get("/v2/{path:path}")
    def v2_page(path: str):
        # 静态文件（favicon/二维码等）直接给文件；其余（/v2/main、/v2/intro…）给 SPA 外壳
        target = SITE_V2 / path
        if path and ".." not in path.split("/") and target.is_file() and not target.is_symlink():
            return FileResponse(target)
        return v2_shell()

    @app.get("/")
    def root():
        return RedirectResponse("/main?data=live")

    @app.get("/{path:path}")
    def spa(path: str):
        return FileResponse(SITE / "index.html", headers={"Cache-Control": SHELL_CACHE_CONTROL})

    return app
