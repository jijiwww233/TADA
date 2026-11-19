from contextlib import closing
from datetime import datetime
from functools import partial
from hashlib import sha1, sha256
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from xml.etree import ElementTree as ET

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import config, db, wechat
from tools import public_demo


TOKEN = "local-wechat-test"
ACK = "收到啦，正在收进素材库"
GUIDE = "直接发图片给我，就会收进『图然想起』"
WELCOME = "嗨，我是图然想起！发张图片给我，我帮你收进素材库。"


@pytest.fixture
def demo(data_dir, monkeypatch, backend_options):
    monkeypatch.setattr(public_demo, "configure_demo", lambda: None)
    monkeypatch.setenv("WOMINMIN_SETTINGS_FILE", str(data_dir / "settings.json"))
    monkeypatch.setenv("AI_API_KEY", "")
    monkeypatch.setenv("WOMINMIN_ALLOW_AUTO_SYNC", "0")
    monkeypatch.setenv("WOMINMIN_DESKTOP_LIBRARY", "1")
    monkeypatch.setenv("WECHAT_TOKEN", TOKEN)
    monkeypatch.setenv("SHORTCUT_UPLOAD_TOKEN", "local-shortcut-test")
    site = data_dir / "site"
    (site / "assets").mkdir(parents=True)
    (site / "index.html").write_text("Demo", encoding="utf-8")
    monkeypatch.setattr(public_demo, "SITE", site)
    with TestClient(public_demo.create_public_app(), backend_options=backend_options) as client:
        yield client


@pytest.fixture
def image_source(monkeypatch):
    handler = partial(SimpleHTTPRequestHandler, directory=public_demo.SAMPLES / "show")
    with ThreadingHTTPServer(("127.0.0.1", 0), handler) as server:
        port = server.server_port
        monkeypatch.setattr(wechat, "IMAGE_ORIGINS", {("http", "127.0.0.1", port)})
        thread = Thread(target=server.serve_forever)
        thread.start()
        try:
            yield f"http://127.0.0.1:{port}"
        finally:
            server.shutdown()
            thread.join()


def test_demo_mobile_import_options(demo, data_dir, monkeypatch):
    monkeypatch.setattr(public_demo, "STORAGE", data_dir)
    monkeypatch.setenv("SHORTCUT_INSTALL_URL", "https://invalid.example/shortcuts/not-an-icloud-link")
    options = demo.get("/api/demo/import-options")
    assert options.json() == {"shortcut": None, "wechat": False}
    assert options.headers["cache-control"] == "private, no-store"
    assert demo.get("/api/demo/wechat-qr").status_code == 404

    link = "https://www.icloud.com/shortcuts/" + "a" * 32
    monkeypatch.setenv("SHORTCUT_INSTALL_URL", link)
    Image.new("RGB", (16, 16), "white").save(data_dir / "wechat-qr.png")
    options = demo.get("/api/demo/import-options").json()
    assert options["shortcut"]["url"] == link
    assert options["shortcut"]["qr"].startswith("data:image/png;base64,")
    assert options["wechat"] is True
    qr = demo.get("/api/demo/wechat-qr")
    assert qr.headers["cache-control"] == "private, no-store"
    assert qr.headers["content-type"] == "image/png"
    assert qr.content.startswith(b"\x89PNG")
    assert demo.get("/api/demo/unknown").status_code == 403
    assert demo.get("/guides/api-key.pdf").content.startswith(b"%PDF-")


def signed_query():
    timestamp, nonce = "1795000000", "12345"
    signature = sha1("".join(sorted((TOKEN, timestamp, nonce))).encode()).hexdigest()
    return dict(signature=signature, timestamp=timestamp, nonce=nonce)


def message(kind, **fields):
    xml = ET.Element("xml")
    for name, value in dict(ToUserName="test-account", FromUserName="test-user", MsgType=kind, **fields).items():
        ET.SubElement(xml, name).text = value
    return ET.tostring(xml, encoding="utf-8")


def post(demo, payload, params=None):
    return demo.post("/api/wechat", params=params or signed_query(), content=payload,
                     headers={"Content-Type": "text/xml"})


def test_wechat_image_import(demo, image_source):
    today = datetime.now().date().isoformat()
    with closing(db.connect()) as conn:
        conn.execute("INSERT INTO public_upload_days(day, count) VALUES (?, 199)", (today,))
    path = public_demo.SAMPLES / "show" / "sample-01.jpg"
    first = message("image", PicUrl=f"{image_source}/{path.name}", MsgId="1")
    response = post(demo, first)
    assert response.status_code == 200
    reply = ET.fromstring(response.content)
    assert reply.findtext("Content") == ACK
    assert reply.findtext("ToUserName") == "test-user"
    assert reply.findtext("FromUserName") == "test-account"
    assert reply.findtext("MsgType") == "text"
    assert post(demo, first).status_code == 200

    other = public_demo.SAMPLES / "show" / "sample-02.jpg"
    assert post(demo, message("image", PicUrl=f"{image_source}/{other.name}", MsgId="2")).status_code == 200
    for endpoint, headers in (
        ("/api/images/import", {}),
        ("/api/shortcut/upload", {"Authorization": "Bearer local-shortcut-test"}),
    ):
        assert demo.post(endpoint, files={"files": (other.name, other.read_bytes())}, headers=headers).status_code == 429

    with closing(db.connect()) as conn:
        images = conn.execute("SELECT sha256, file_path FROM images").fetchall()
        assert len(images) == 1
        assert images[0]["sha256"] == sha256(path.read_bytes()).hexdigest()
        assert (config.data_dir() / images[0]["file_path"]).read_bytes() == path.read_bytes()
        assert conn.execute("SELECT count FROM public_upload_days WHERE day = ?", (today,)).fetchone()[0] == 200


def test_wechat_signatures_and_xml(demo):
    echo = " 001+%&微信 "
    assert demo.get("/api/wechat", params=signed_query() | {"echostr": echo}).content == echo.encode()
    for field in signed_query():
        assert post(demo, b"invalid xml", signed_query() | {field: "0"}).status_code == 403
    for payload, expected in (
        (message("text"), GUIDE),
        (message("voice"), GUIDE),
        (message("event", Event="subscribe"), WELCOME),
        (message("event", Event="CLICK"), GUIDE),
    ):
        response = post(demo, payload)
        assert response.status_code == 200
        assert ET.fromstring(response.content).findtext("Content") == expected
    assert post(demo, b"<xml>").status_code == 400
    doctype = b"<!DOCTYPE xml>" + message("text")
    assert post(demo, doctype).status_code == 400
    assert post(demo, doctype.decode().encode("utf-16-le")).status_code == 400
    assert post(demo, b"x" * (wechat.XML_LIMIT + 1)).status_code == 413
    assert demo.post("/api/wechat/", params=signed_query(), content=message("text")).status_code == 403


def test_wechat_sends_reply_before_download(demo, monkeypatch, backend_options):
    reply_sent = False
    download_started_after_reply = False

    async def observe_reply(scope, receive, send):
        async def observe_send(event):
            nonlocal reply_sent
            await send(event)
            if event["type"] == "http.response.body" and event.get("body"):
                reply_sent = True

        await demo.app(scope, receive, observe_send)

    async def download(url, size_limit):
        nonlocal download_started_after_reply
        download_started_after_reply = reply_sent
        return (public_demo.SAMPLES / "show" / "sample-01.jpg").read_bytes()

    monkeypatch.setattr(wechat, "download_image", download)
    client = TestClient(observe_reply, backend_options=backend_options)
    response = post(client, message("image", PicUrl="https://mmbiz.qpic.cn/example"))
    assert response.status_code == 200
    assert ET.fromstring(response.content).findtext("Content") == ACK
    assert download_started_after_reply
