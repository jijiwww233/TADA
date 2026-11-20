"""Import a picture from a copied share link ("装进收藏" from the phone app).

Two sources:
- 小红书 (www.xiaohongshu.com/discovery/item/...): follow the share short link, read
  ``window.__INITIAL_STATE__`` from the page and take every image in ``imageList`` plus
  the note title.  The whole note becomes one post.
- Everything else: read the page's ``og:image`` and import that single image.  The post
  platform is the bare domain (e.g. ``dribbble.com``), appended to posts.platform.

Only http(s) links to public hosts are fetched.  The caller (public_demo) enforces the
token, the daily quota and the analysis queue; this module is pure fetch/parse/download.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from html import unescape
from ipaddress import ip_address
import json
import re
import socket
from urllib.parse import urljoin, urlparse, urlunparse

import requests

from . import db

MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1"
)
DESKTOP_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
HTML_LIMIT = 4 * 1024 * 1024
IMAGE_LIMIT = 20 * 1024 * 1024
MAX_IMAGES = 18
TIMEOUT = 20
REDIRECTS = 6

NO_IMAGE_MESSAGE = "这个链接暂时拿不到图，截个图发给我吧"
BAD_LINK_MESSAGE = "链接格式不对，复制小红书或网页的分享链接再试"
BLOCKED_MESSAGE = "这个链接打不开，可能有登录或地区限制；截个图发给我吧"
XHS_EMPTY_MESSAGE = "这条笔记暂时打不开，稍后再试，或者截个图发给我"


class LinkError(Exception):
    """Carries a message that is safe to show to the user."""


@dataclass
class LinkTarget:
    platform: str
    source_id: str
    original_url: str
    canonical_url: str
    title: str
    description: str = ""
    images: list[str] = field(default_factory=list)
    is_video: bool = False


@dataclass
class PreparedLink:
    target: LinkTarget
    uploads: list[tuple[str, bytes]]


def _is_public_host(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    for info in infos:
        try:
            address = ip_address(info[4][0])
        except ValueError:
            return False
        if not address.is_global:
            return False
    return True


def _checked_url(url: str) -> str:
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise LinkError(BAD_LINK_MESSAGE)
    if not _is_public_host(parsed.hostname):
        raise LinkError(BAD_LINK_MESSAGE)
    return urlunparse(parsed._replace(fragment=""))


@dataclass
class _Fetched:
    url: str
    content: bytes
    content_type: str

    def text(self) -> str:
        return self.content.decode("utf-8", "replace")


def _get(session: requests.Session, url: str, *, headers: dict, limit: int) -> _Fetched:
    """GET with a small manual redirect loop so every hop is checked for SSRF."""
    for _ in range(REDIRECTS):
        resp = session.get(url, headers=headers, timeout=TIMEOUT, allow_redirects=False, stream=True)
        if resp.is_redirect and resp.headers.get("location"):
            url = urljoin(url, resp.headers["location"])
            _checked_url(url)
            resp.close()
            continue
        if resp.status_code >= 400:
            resp.close()
            raise LinkError(BLOCKED_MESSAGE)
        data = bytearray()
        for chunk in resp.iter_content(64 * 1024):
            data.extend(chunk)
            if len(data) > limit:
                resp.close()
                raise LinkError(NO_IMAGE_MESSAGE)
        final_url = str(resp.url)
        content_type = resp.headers.get("content-type", "")
        resp.close()
        return _Fetched(final_url, bytes(data), content_type)
    raise LinkError(BLOCKED_MESSAGE)


def _platform_for(host: str) -> str:
    """posts.platform for this host: the four known keys, or the bare domain."""
    host = host.lower().lstrip(".")
    if host.endswith("xiaohongshu.com") or host.endswith("xhslink.cn") or host.endswith("xhslink.com"):
        return "xhs"
    if host.startswith("www."):
        host = host[4:]
    if host == "pinterest.com" or host.endswith(".pinterest.com") or host == "pin.it":
        return "pinterest"
    if host == "zcool.com.cn" or host.endswith(".zcool.com.cn"):
        return "zcool"
    if host == "huaban.com" or host.endswith(".huaban.com"):
        return "huaban"
    return host[:63]


def _canonical(url: str) -> str:
    parsed = urlparse(url)
    path = parsed.path.rstrip("/") or "/"
    return urlunparse((parsed.scheme, parsed.netloc.lower(), path, "", "", ""))


def _json_after(html: str, marker: str):
    position = html.find(marker)
    if position < 0:
        return None
    brace = html.find("{", position)
    if brace < 0:
        return None
    raw = html[brace:]
    raw = re.sub(r"\bundefined\b", "null", raw)
    try:
        return json.JSONDecoder().raw_decode(raw)[0]
    except ValueError:
        return None


def _walk_notes(value, out: list, depth: int = 0) -> None:
    if depth > 12:
        return
    if isinstance(value, dict):
        images = value.get("imageList")
        if isinstance(images, list) and images and any(
                key in value for key in ("title", "desc", "noteId", "note_id")):
            out.append(value)
        for item in value.values():
            _walk_notes(item, out, depth + 1)
    elif isinstance(value, list):
        for item in value[:100]:
            _walk_notes(item, out, depth + 1)


def _note_images(note: dict) -> list[str]:
    urls: list[str] = []
    for item in note.get("imageList") or []:
        if not isinstance(item, dict):
            continue
        scenes = {str(info.get("imageScene") or ""): info.get("url")
                  for info in item.get("infoList") or []
                  if isinstance(info, dict) and info.get("url")}
        chosen = (scenes.get("H5_DTL") or scenes.get("WB_DFT")
                  or item.get("urlDefault") or item.get("url") or scenes.get("H5_PRV"))
        if chosen and chosen not in urls:
            urls.append(chosen)
    return urls


def _note_id(url: str, note: dict | None) -> str:
    if note:
        value = note.get("noteId") or note.get("note_id")
        if value:
            return str(value)
    match = re.search(r"/(?:discovery/item|explore)/([0-9a-zA-Z]+)", url)
    return match.group(1) if match else ""


def _meta_content(html: str, *names: str) -> str:
    for name in names:
        pattern = (r'<meta[^>]+(?:property|name)=["\']' + re.escape(name)
                   + r'["\'][^>]*content=["\']([^"\']+)["\']')
        match = re.search(pattern, html, re.I)
        if match:
            return unescape(match.group(1)).strip()
        pattern = (r'<meta[^>]+content=["\']([^"\']+)["\'][^>]*(?:property|name)=["\']'
                   + re.escape(name) + r'["\']')
        match = re.search(pattern, html, re.I)
        if match:
            return unescape(match.group(1)).strip()
    return ""


def inspect_link(raw_url: str) -> LinkTarget:
    """Follow the share link and return everything needed before downloading."""
    source_url = _checked_url(raw_url)
    host = urlparse(source_url).hostname or ""
    platform = _platform_for(host)
    headers = {
        "User-Agent": MOBILE_UA if platform == "xhs" else DESKTOP_UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9",
    }
    session = requests.Session()
    try:
        fetched = _get(session, source_url, headers=headers, limit=HTML_LIMIT)
    except requests.RequestException:
        raise LinkError(BLOCKED_MESSAGE) from None
    html = fetched.text()
    final_url = fetched.url
    platform = _platform_for(urlparse(final_url).hostname or host)

    if platform == "xhs":
        state = _json_after(html, "window.__INITIAL_STATE__")
        candidates: list[dict] = []
        if state:
            _walk_notes(state, candidates)
        note = max(candidates, key=lambda item: len(item.get("imageList") or []), default=None) if candidates else None
        images = _note_images(note) if note else []
        if not images:
            raise LinkError(XHS_EMPTY_MESSAGE)
        title = ""
        if note:
            title = str(note.get("title") or "").strip()
            if not title:
                title = (str(note.get("desc") or "").strip().splitlines() or [""])[0].strip()
        if not title:
            title = _meta_content(html, "og:title") or ""
        description = str(note.get("desc") or "").strip() if note else ""
        note_id = _note_id(final_url, note)
        return LinkTarget(
            platform="xhs",
            source_id=note_id or _canonical(final_url),
            original_url=source_url,
            canonical_url=_canonical(final_url),
            title=title[:80] or "小红书收藏",
            description=description[:400],
            images=images[:MAX_IMAGES],
            is_video=str((note or {}).get("type") or "") == "video",
        )

    image = _meta_content(html, "og:image", "og:image:url", "twitter:image")
    if not image:
        raise LinkError(NO_IMAGE_MESSAGE)
    title = _meta_content(html, "og:title", "twitter:title")
    if not title:
        match = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
        title = unescape(match.group(1)).strip() if match else ""
    description = _meta_content(html, "og:description", "description")
    resolved = urljoin(final_url, image)
    _checked_url(resolved)
    return LinkTarget(
        platform=platform,
        source_id=_canonical(final_url),
        original_url=source_url,
        canonical_url=_canonical(final_url),
        title=title[:80] or platform,
        description=description[:400],
        images=[resolved],
    )


def find_existing(target: LinkTarget) -> dict | None:
    """A previously imported copy of the same note/page, if any."""
    conn = db.connect()
    try:
        row = conn.execute(
            """SELECT p.id, p.title,
                      (SELECT COUNT(*) FROM post_images pi WHERE pi.post_id = p.id) AS image_count
               FROM posts p
               WHERE (p.platform = ? AND p.source_id = ?) OR p.url = ?
               ORDER BY p.id LIMIT 1""",
            (target.platform, target.source_id, target.original_url),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    return {"post_id": row["id"], "title": row["title"] or target.title,
            "count": row["image_count"]}


def download_images(target: LinkTarget) -> list[tuple[str, bytes]]:
    """Download every image in the target, in page order. One bad image is skipped."""
    session = requests.Session()
    headers = {
        "User-Agent": MOBILE_UA if target.platform == "xhs" else DESKTOP_UA,
        "Referer": target.canonical_url,
        "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
    }
    uploads: list[tuple[str, bytes]] = []
    for index, url in enumerate(target.images[:MAX_IMAGES], 1):
        try:
            fetched = _get(session, url, headers=headers, limit=IMAGE_LIMIT)
        except (LinkError, requests.RequestException):
            continue
        if not fetched.content:
            continue
        extension = "jpg"
        content_type = fetched.content_type.lower()
        if "png" in content_type:
            extension = "png"
        elif "webp" in content_type:
            extension = "webp"
        uploads.append((f"{target.platform}-{index:02d}.{extension}", fetched.content))
    if not uploads:
        raise LinkError(NO_IMAGE_MESSAGE)
    return uploads
