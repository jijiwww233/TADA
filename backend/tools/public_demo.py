import asyncio
from base64 import b64encode
from contextlib import asynccontextmanager, closing
from datetime import datetime
import hmac
from io import BytesIO
import logging
import os
from pathlib import Path
import re

from dotenv import dotenv_values
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.formparsers import MultiPartException, MultiPartParser

ROOT = Path(__file__).resolve().parents[2]
STORAGE = ROOT / ".run" / "public-demo"
SITE = ROOT / ".run" / "public-site"
SAMPLES = ROOT / "samples"
LOGGER = logging.getLogger(__name__)
FILE_LIMIT = 20 * 1024 * 1024
BODY_LIMIT = 8 * FILE_LIMIT + 1024 * 1024
DAILY_LIMIT = 200
UNAVAILABLE = "演示版不提供此功能"

READ_API = re.compile(
    r"/api/(?:health|wechat|demo/(?:import-options|wechat-qr)|images(?:/\d+(?:/similar)?)?|stats/(?:library|platforms|daily)"
    r"|projects(?:/\d+(?:/pins|/pip-scene)?)?|reference/review|reference-labels"
    r"|analysis/status|similar/status|import/status)"
)
POST_API = {
    "/api/wechat",
    "/api/search", "/api/recommendations", "/api/recommendations/columns",
    "/api/recommendations/more-like", "/api/recommendations/stream",
}
PROJECT_WRITES = {
    "POST": re.compile(r"/api/projects(?:/\d+/pins)?"),
    "PATCH": re.compile(r"/api/projects/\d+(?:/pins/\d+/position)?"),
    "DELETE": re.compile(r"/api/projects/\d+/pins/\d+(?:/purposes/(?:layout|style|palette|subject))?"),
}


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
    from app import analysis, config, db, importer, wechat
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

    def import_public(uploads):
        today = datetime.now().date().isoformat()

        def queue_with_quota(conn, image_id):
            cursor = conn.execute("""
                INSERT INTO public_upload_days(day, count) VALUES (?, 1)
                ON CONFLICT(day) DO UPDATE SET count = count + 1 WHERE count < ?
            """, (today, DAILY_LIMIT))
            if not cursor.rowcount:
                raise UploadError(429, "演示库今天已收满 200 张，请明天再试")
            analysis.ensure_analysis(conn, image_id)

        return importer.import_images(uploads, on_analysis=queue_with_quota)

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
            if shortcut:
                return PlainTextResponse(exc.message, status_code=exc.status)
            return JSONResponse({"detail": exc.message}, status_code=exc.status)
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
        if path == "/api/images/import" and method == "POST":
            return await receive_upload(request, shortcut=False)
        if path == "/api" or path.startswith("/api/"):
            project_write = PROJECT_WRITES.get(method)
            permitted = (
                (method == "GET" and READ_API.fullmatch(path))
                or (method == "POST" and path in POST_API)
                or (project_write and project_write.fullmatch(path))
            )
            if not permitted:
                return JSONResponse({"detail": UNAVAILABLE}, status_code=403)
        if path in {"/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"}:
            return JSONResponse({"detail": UNAVAILABLE}, status_code=403)
        return await call_next(request)

    app.mount("/assets", StaticFiles(directory=SITE / "assets"), name="public-built-assets")
    app.mount("/brand", StaticFiles(directory=ROOT / "frontend" / "public" / "brand"), name="public-brand")
    app.mount("/lab-assets/show", StaticFiles(directory=SAMPLES / "show"), name="public-intro-samples")

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

    @app.get("/")
    def root():
        return RedirectResponse("/main?data=live")

    @app.get("/{path:path}")
    def spa(path: str):
        return FileResponse(SITE / "index.html")

    return app
