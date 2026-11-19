"""Optional phone upload page and Android relay endpoint for a trusted LAN."""
import argparse
import hmac
import ipaddress
from pathlib import Path
import secrets
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile

from app import db, importer

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".heic", ".heif", ".tif", ".tiff"}
MAX_FILE = 20 * 1024 * 1024
MAX_REQUEST = 40 * 1024 * 1024
PAGE = """<!doctype html>
<html lang="zh-CN">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>我明明收藏过 · 手机导入</title>
<style>
  body { max-width: 32rem; margin: 12vh auto; padding: 0 20px; background: #f8f6f1;
    color: #29221c; font: 17px/1.6 Georgia, "Songti SC", serif; }
  input, button { display: block; margin: 16px 0; font: inherit; max-width: 100%; }
  button { min-height: 44px; padding: 0 18px; border: 1px solid #53483e;
    border-radius: 8px; color: inherit; background: #eee7dc; }
</style>
<main>
<h1>导入截图或照片</h1>
<form>
  <label>从相册选图<input type="file" name="files" accept="image/*,.heic,.heif" multiple required></label>
  <button type="submit">传到素材库</button>
</form>
<p role="status"></p>
</main>
<script>
const token = decodeURIComponent(location.hash.slice(1));
if (token) history.replaceState(null, '', location.pathname);
const form = document.querySelector('form');
const status = document.querySelector('[role="status"]');
const button = form.querySelector('button');
if (!token) {
  button.disabled = true;
  status.textContent = '请打开电脑上生成的手机导入链接。';
}
form.addEventListener('submit', async event => {
  event.preventDefault();
  button.disabled = true;
  status.textContent = '正在收藏…';
  try {
    const response = await fetch('/api/images/import', {
      method: 'POST', headers: { Authorization: 'Bearer ' + token }, body: new FormData(form)
    });
    if (response.status === 401) throw new Error('链接已失效，请从电脑重新获取。');
    if (response.status === 413) throw new Error('这批图片太大，请分批导入。');
    if (!response.ok) throw new Error('导入失败，请重试。');
    const data = await response.json();
    status.textContent = [
      data.added.length && `已收藏 ${data.added.length} 张`,
      data.duplicates.length && `${data.duplicates.length} 张已在库中`,
      data.failed.length && `${data.failed.length} 张无法导入`,
    ].filter(Boolean).join(' · ');
    form.reset();
  } catch (error) {
    status.textContent = error.message;
  } finally {
    button.disabled = false;
  }
});
</script>
</html>"""


def create_bridge(token: str) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/")
    def upload_page():
        return HTMLResponse(PAGE, headers={
            "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer",
            "Content-Security-Policy": "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; frame-ancestors 'none'; form-action 'self'",
        })

    @app.post("/api/images/import")
    async def upload(request: Request):
        incoming = request.headers.get("authorization", "").encode("utf-8")
        if not hmac.compare_digest(incoming, ("Bearer " + token).encode("ascii")):
            raise HTTPException(401, "配对已失效")
        length = request.headers.get("content-length", "")
        if not length.isascii() or not length.isdecimal() or len(length) > 8:
            raise HTTPException(411, "需要有效的 Content-Length")
        if int(length) > MAX_REQUEST:
            raise HTTPException(413, "单次最多上传 40 MB")
        if not request.headers.get("content-type", "").lower().startswith("multipart/form-data;"):
            raise HTTPException(415, "需要 multipart/form-data")
        async with request.form(max_files=8, max_fields=0) as form:
            files = form.getlist("files")
            if not files or not all(isinstance(file, UploadFile) for file in files):
                raise HTTPException(422, "请选择图片")
            candidates = []
            for index, file in enumerate(files, 1):
                if file.size is None or file.size > MAX_FILE:
                    raise HTTPException(413, "单张图片最大 20 MB")
                suffix = Path(file.filename or "").suffix.lower()
                if suffix not in IMAGE_EXTENSIONS:
                    raise HTTPException(415, "图片格式不支持")
                candidates.append(importer.Upload(f"mobile-{index}{suffix}", await file.read()))
        return await run_in_threadpool(importer.import_images, candidates)

    return app


def is_lan_address(address):
    return any(address in ipaddress.ip_network(cidr) for cidr in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))


if __name__ == "__main__":
    import uvicorn

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", type=ipaddress.IPv4Address, required=True)
    parser.add_argument("--port", type=int, default=8101)
    args = parser.parse_args()
    if not args.host.is_loopback and not is_lan_address(args.host):
        parser.error("Use the PC's private Wi-Fi IPv4 address")
    db.init_db()
    token = secrets.token_urlsafe(24)
    print(f"手机导入链接 http://{args.host}:{args.port}/#{quote(token)}", flush=True)
    uvicorn.run(create_bridge(token), host=str(args.host), port=args.port, access_log=False)
