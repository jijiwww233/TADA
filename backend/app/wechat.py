"""Plaintext WeChat callback verification, XML, and image download."""
import asyncio
from hashlib import sha1
from hmac import compare_digest
import os
from time import time
from xml.etree import ElementTree as ET

import httpx
from fastapi import HTTPException, Request, Response

XML_LIMIT = 64 * 1024
IMAGE_ORIGINS = {
    (scheme, host, None)
    for scheme in ("http", "https")
    for host in ("mmbiz.qpic.cn", "mmbiz.qlogo.cn")
}


def verify_signature(signature: str, timestamp: str, nonce: str) -> None:
    token = os.getenv("WECHAT_TOKEN")
    if not token:
        raise HTTPException(503, "WeChat verification is not configured")
    signed = "".join(sorted((token, timestamp, nonce))).encode("utf-8")
    expected = sha1(signed).hexdigest()
    if not compare_digest(signature.encode("utf-8"), expected.encode("ascii")):
        raise HTTPException(403, "Invalid WeChat signature")


async def read_message(request: Request) -> ET.Element:
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > XML_LIMIT:
            raise HTTPException(413, "WeChat message is too large")
        body.extend(chunk)
    try:
        text = body.decode("utf-8")
        if "\x00" in text or "<!DOCTYPE" in text:
            raise ValueError
        message = ET.fromstring(text)
        if message.tag != "xml" or any(
            not message.findtext(field) for field in ("ToUserName", "FromUserName", "MsgType")
        ):
            raise ValueError
    except (UnicodeError, ET.ParseError, ValueError):
        raise HTTPException(400, "Invalid WeChat XML message") from None
    return message


def text_reply(message: ET.Element, content: str) -> Response:
    reply = ET.Element("xml")
    for name, value in (
        ("ToUserName", message.findtext("FromUserName")),
        ("FromUserName", message.findtext("ToUserName")),
        ("CreateTime", str(int(time()))),
        ("MsgType", "text"),
        ("Content", content),
    ):
        ET.SubElement(reply, name).text = value
    return Response(ET.tostring(reply, encoding="utf-8"), media_type="application/xml")


async def download_image(pic_url: str, size_limit: int) -> bytes:
    url = httpx.URL(pic_url)
    if url.userinfo or (url.scheme, url.host, url.port) not in IMAGE_ORIGINS:
        raise ValueError("Unexpected WeChat image origin")

    data = bytearray()
    async with asyncio.timeout(20):
        async with httpx.AsyncClient(
            follow_redirects=False, trust_env=False, headers={"Accept-Encoding": "identity"},
        ) as client:
            async with client.stream("GET", url) as response:
                response.raise_for_status()
                async for chunk in response.aiter_raw():
                    if len(data) + len(chunk) > size_limit:
                        raise ValueError("WeChat image exceeds the upload limit")
                    data.extend(chunk)
    return bytes(data)
