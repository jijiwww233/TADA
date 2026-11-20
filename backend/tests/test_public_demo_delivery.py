"""公网演示版的传输层：gzip、静态缓存头、Vary 与 304 行为。"""
import gzip
import logging
from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from starlette.middleware.gzip import GZipMiddleware

from tools import public_demo


JS = (b"// gzip demo asset\n"
      b"console.log('hello from the public demo build');\n") * 60
HTML = (
    "<!doctype html>\n<html lang=\"zh-CN\"><head><meta charset=\"UTF-8\" />"
    "<title>gzip demo shell</title>"
    "<script type=\"module\" crossorigin src=\"/assets/app-abc123.js\"></script>"
    "</head><body><div id=\"root\"></div>"
    "<!-- shell padding: " + "x" * 400 + " -->"
    "</body></html>\n"
)


def png_bytes() -> bytes:
    buf = BytesIO()
    Image.new("RGB", (32, 32), "blue").save(buf, "PNG")
    return buf.getvalue()


PNG = png_bytes()
WOFF2 = bytes(range(256)) * 4


@pytest.fixture
def demo(data_dir, monkeypatch, backend_options):
    monkeypatch.setattr(public_demo, "configure_demo", lambda: None)
    monkeypatch.setenv("WOMINMIN_SETTINGS_FILE", str(data_dir / "settings.json"))
    monkeypatch.setenv("AI_API_KEY", "")
    monkeypatch.setenv("WOMINMIN_ALLOW_AUTO_SYNC", "0")
    monkeypatch.setenv("WOMINMIN_DESKTOP_LIBRARY", "1")
    site = data_dir / "site"
    (site / "assets").mkdir(parents=True)
    (site / "assets" / "app-abc123.js").write_bytes(JS)
    (site / "assets" / "photo-abc123.png").write_bytes(PNG)
    (site / "assets" / "font-abc123.woff2").write_bytes(WOFF2)
    (site / "index.html").write_bytes(HTML.encode("utf-8"))
    monkeypatch.setattr(public_demo, "SITE", site)
    with TestClient(public_demo.create_public_app(), backend_options=backend_options) as client:
        yield client


def wire_capture(demo, backend_options, path, headers):
    """按 ASGI 消息抓取真实线上字节，用于核对 Content-Length 与解压结果。"""
    captured = []

    async def observe(scope, receive, send):
        async def capture(message):
            captured.append(message)
            await send(message)

        await demo.app(scope, receive, capture)

    client = TestClient(observe, backend_options=backend_options)
    response = client.get(path, headers=headers)
    start = next(message for message in captured if message["type"] == "http.response.start")
    raw_headers = {key.decode().lower(): value.decode() for key, value in start["headers"]}
    body = b"".join(message.get("body", b"") for message in captured if message["type"] == "http.response.body")
    return response, raw_headers, body


def test_hashed_js_is_gzipped_with_correct_length(demo, backend_options):
    response, headers, wire = wire_capture(demo, backend_options, "/assets/app-abc123.js",
                                           {"accept-encoding": "gzip"})
    assert response.status_code == 200
    assert headers["content-encoding"] == "gzip"
    assert 0 < len(wire) < len(JS)
    assert gzip.decompress(wire) == JS
    assert int(headers["content-length"]) == len(wire)
    assert headers["vary"].lower() == "accept-encoding"
    assert headers["cache-control"] == public_demo.ASSET_CACHE_CONTROL


def test_identity_request_falls_back_to_uncompressed(demo):
    response = demo.get("/assets/app-abc123.js", headers={"accept-encoding": "identity"})
    assert response.status_code == 200
    assert "content-encoding" not in response.headers
    assert response.content == JS
    assert int(response.headers["content-length"]) == len(JS)
    assert response.headers["vary"].lower() == "accept-encoding"
    assert response.headers["cache-control"] == public_demo.ASSET_CACHE_CONTROL


def test_images_and_fonts_are_not_compressed(demo):
    for path, payload in (("/assets/photo-abc123.png", PNG), ("/assets/font-abc123.woff2", WOFF2)):
        response = demo.get(path, headers={"accept-encoding": "gzip"})
        assert response.status_code == 200
        assert "content-encoding" not in response.headers, path
        assert response.content == payload
        assert int(response.headers["content-length"]) == len(payload)
        assert "vary" not in response.headers
        assert response.headers["cache-control"] == public_demo.ASSET_CACHE_CONTROL


def test_spa_shell_is_gzipped_but_never_long_cached(demo):
    response = demo.get("/main?data=live", headers={"accept-encoding": "gzip"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["content-encoding"] == "gzip"
    assert response.content == HTML.encode()
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["vary"].lower() == "accept-encoding"


def test_brand_and_sample_assets_use_medium_cache(demo):
    brand = demo.get("/brand/pinterest.svg", headers={"accept-encoding": "gzip"})
    assert brand.status_code == 200
    assert brand.headers["content-encoding"] == "gzip"
    assert brand.headers["cache-control"] == public_demo.BRAND_CACHE_CONTROL

    sample = demo.get("/lab-assets/show/sample-01.jpg", headers={"accept-encoding": "gzip"})
    assert sample.status_code == 200
    assert "content-encoding" not in sample.headers
    assert sample.headers["cache-control"] == public_demo.BRAND_CACHE_CONTROL


def test_missing_asset_is_not_cached(demo):
    response = demo.get("/assets/missing-abc123.js", headers={"accept-encoding": "gzip"})
    assert response.status_code == 404
    assert "cache-control" not in response.headers


def test_304_keeps_cache_and_vary_headers(demo):
    first = demo.get("/assets/app-abc123.js", headers={"accept-encoding": "gzip"})
    etag = first.headers["etag"]
    second = demo.get("/assets/app-abc123.js",
                      headers={"accept-encoding": "gzip", "if-none-match": etag})
    assert second.status_code == 304
    assert second.headers["cache-control"] == public_demo.ASSET_CACHE_CONTROL
    assert second.headers["vary"].lower() == "accept-encoding"
    assert "content-encoding" not in second.headers


def test_precompressed_response_is_not_compressed_again(backend_options):
    payload = gzip.compress(b"already compressed body " * 200)

    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": [
            (b"content-type", b"text/javascript"),
            (b"content-encoding", b"gzip"),
            (b"content-length", str(len(payload)).encode()),
        ]})
        await send({"type": "http.response.body", "body": payload})

    middleware = GZipMiddleware(app, minimum_size=500, compresslevel=6,
                                exclude_content_types=public_demo.GZIP_EXCLUDE_CONTENT_TYPES)
    response = TestClient(middleware, backend_options=backend_options).get(
        "/x", headers={"accept-encoding": "gzip"})
    assert response.headers.get_list("content-encoding") == ["gzip"]
    assert int(response.headers["content-length"]) == len(payload)


def test_upload_failure_is_logged_with_status_and_message(demo, caplog):
    """P0-3：导入被拒绝时日志要能看到 status + message，不能静默。"""
    with caplog.at_level(logging.WARNING, logger="tools.public_demo"):
        response = demo.post("/api/images/import", content=b"",
                             headers={"content-type": "text/plain"})
    assert response.status_code == 415
    assert response.json()["detail"] == "请用表单上传图片，字段名填 files"
    assert any("演示版导入被拒绝" in message and "415" in message
               for message in caplog.messages), caplog.messages


def test_successful_upload_is_logged_with_counts(demo, caplog):
    with caplog.at_level(logging.INFO, logger="tools.public_demo"):
        response = demo.post("/api/images/import",
                             files=[("files", ("a.png", PNG, "image/png"))])
    assert response.status_code == 200, response.text
    assert len(response.json()["added"]) == 1
    assert any("演示版导入完成" in message and "新增 1 张" in message
               for message in caplog.messages), caplog.messages


def test_negotiates_content_encoding_classifies_types():
    assert public_demo.negotiates_content_encoding("text/javascript; charset=utf-8")
    assert public_demo.negotiates_content_encoding("text/css")
    assert public_demo.negotiates_content_encoding("text/html; charset=utf-8")
    assert public_demo.negotiates_content_encoding("application/json")
    assert public_demo.negotiates_content_encoding("image/svg+xml")
    assert not public_demo.negotiates_content_encoding("image/png")
    assert not public_demo.negotiates_content_encoding("image/jpeg")
    assert not public_demo.negotiates_content_encoding("image/webp")
    assert not public_demo.negotiates_content_encoding("application/octet-stream")
    assert not public_demo.negotiates_content_encoding("text/event-stream")
