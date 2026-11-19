"""Two XHS import entries that do not depend on the logged-in sync path.

1. captured(): data the user's own browser already loaded. No network request, no
   cookie, no image download: the note and its image order are stored with
   download_state='remote_only' so the UI can say "图片未下载" instead of pretending.
2. share links: anonymous page fetch, one request per note, random gap between
   requests, at most 20 per batch, dedupe by note id. Every request first passes the
   persistent XHS safety lock (manual cooldown or a tripped incident blocks it).

No captcha bypass and no signed private API replay. Notes are deduped by
(platform='xhs', source_id=note id) which the posts table already enforces.
"""
from __future__ import annotations

import json
import random
import re
import threading
import time
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urljoin, urlparse

import httpx

from . import db
from .sync import safety
from .sync.readers.xhs import page_kind, risk_text

PLATFORM = "xhs"
MAX_SHARE_BATCH = 20
MIN_DELAY_S = 0.8
MAX_DELAY_S = 2.0
NOTE_ID_RE = re.compile(r"(?:explore|discovery/item|note)/([0-9a-fA-F]{24})")
NOTE_ID_FULL = re.compile(r"^[0-9a-fA-F]{24}$")
ALLOWED_HOSTS = {"www.xiaohongshu.com", "xiaohongshu.com", "xhslink.com"}
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36")
FETCH_TIMEOUT = 20.0
MAX_REDIRECTS = 5  # share links redirect at most a few times (short link -> note page)


class ImportRejected(ValueError):
    """Bad input; the caller did not lose any local data."""


class SafetyBlocked(RuntimeError):
    """The local XHS cooldown lock refused a network request."""


# HTTP statuses that mean "access denied / rate limited / risk page", not "note gone".
RISK_STATUSES = {403, 429, 461, 471}


def _risk_response(url: str, html: str) -> bool:
    """Is this fetched page (or the url it landed on) an XHS risk/captcha page?"""
    text = html or ""
    return page_kind(url or "") == "captcha" or risk_text(text) or "暂时无法浏览" in text


def _trip_risk():
    """Persist the shared cooldown and stop the batch; the caller maps this to 409."""
    safety.trip("分享导入检测到风控响应")
    raise SafetyBlocked(safety.MESSAGE)


def _sanitized_link(url, note_id=None) -> str | None:
    """Canonical link without userinfo/query/fragment; note_url when the id is known.

    Failure details are returned by the API and replayed by /api/import/status, so the
    original share URL (short-lived xsec_token and other query params) must never be
    echoed back.
    """
    if isinstance(note_id, str) and NOTE_ID_FULL.match(note_id):
        return note_url(note_id)
    try:
        parsed = urlparse(url if isinstance(url, str) else "")
        host = (parsed.hostname or "").lower()
        path = parsed.path or "/"
    except ValueError:
        return None
    if not host:
        return None
    scheme = parsed.scheme if parsed.scheme in ("http", "https") else "https"
    return f"{scheme}://{host}{path}"


def _failure(index: int, url, error: str, note_id=None) -> dict:
    """One sanitized failure item: input position + note id / canonical link only.

    "url" is an alias of the same canonical (query/fragment-free) link: the existing
    import UI reads it, and it must never carry the original share URL either.
    """
    note_id = note_id if isinstance(note_id, str) and NOTE_ID_FULL.match(note_id) else None
    link = _sanitized_link(url, note_id)
    entry = {"input_index": index + 1, "note_id": note_id, "link": link, "error": error}
    if link is not None:
        entry["url"] = link
    return entry


def note_url(note_id: str) -> str:
    return f"https://www.xiaohongshu.com/explore/{note_id}"


def _validated_target(url):
    """Parse a URL that may be requested: allowlisted http(s) host, no userinfo/odd port.

    Raises ImportRejected instead of ever handing a non-allowlisted target to httpx.
    """
    if not isinstance(url, str):
        raise ImportRejected("链接格式错误")
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        port = parsed.port
    except ValueError:
        raise ImportRejected("链接格式错误") from None
    if parsed.scheme not in ("http", "https"):
        raise ImportRejected("只接受 http/https 链接")
    if parsed.username is not None or parsed.password is not None:
        raise ImportRejected("链接不能包含用户信息")
    if port not in (None, 80, 443):
        raise ImportRejected("链接端口异常")
    if host not in ALLOWED_HOSTS:
        raise ImportRejected("只接受 xiaohongshu.com / xhslink.com 的分享链接")
    return parsed


def note_id_from_url(url: str) -> str | None:
    parsed = _validated_target(url)
    match = NOTE_ID_RE.search(parsed.path or "")
    return match.group(1) if match else None


@dataclass
class IngestResult:
    created: int = 0
    skipped: int = 0
    failed: list | None = None

    def as_dict(self) -> dict:
        return {"created": self.created, "skipped": self.skipped,
                "failed": list(self.failed or []), "total": self.created + self.skipped + len(self.failed or [])}


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


# Last outcome per entry, memory only: the UI shows "上次导入" after a reload, but a
# restart forgets it (no result data worth persisting, and no user library is touched).
_LAST_LOCK = threading.Lock()
_LAST: dict[str, dict | None] = {"captured": None, "share": None}


def _remember(mode: str, result: dict) -> dict:
    with _LAST_LOCK:
        _LAST[mode] = {**result, "at": _now()}
    return result


def last_results() -> dict:
    with _LAST_LOCK:
        return {mode: (dict(value) if value else None) for mode, value in _LAST.items()}


def status() -> dict:
    """Read-only view for the import UI: cooldown lock + last outcome per entry."""
    current = safety.state()
    blocked = bool(current.get("blocked"))
    return {
        "blocked": blocked,
        "manual_hold": bool(current.get("manual_hold")),
        "message": "小红书冷却中：联网导入暂停，两个入口暂时禁用" if blocked else None,
        "last": last_results(),
    }


def _text(value, limit: int) -> str:
    return (value if isinstance(value, str) else "").strip()[:limit]


def _clean_images(value) -> list[dict]:
    images = []
    if not isinstance(value, list):
        return images
    for item in value[:30]:
        if not isinstance(item, dict):
            continue
        url = item.get("remote_url") or item.get("url") or item.get("urlDefault") or item.get("urlPre")
        if not isinstance(url, str) or not url.startswith(("https://", "http://")):
            info = item.get("infoList")
            if isinstance(info, list) and info and isinstance(info[0], dict):
                url = info[0].get("url")
        if not isinstance(url, str) or not url.startswith(("https://", "http://")):
            continue
        width = item.get("width") if isinstance(item.get("width"), int) else None
        height = item.get("height") if isinstance(item.get("height"), int) else None
        images.append({"remote_url": url[:2048], "width": width, "height": height})
    return images


def _normalize_captured(raw: dict) -> dict | None:
    if not isinstance(raw, dict):
        return None
    note_id = raw.get("note_id") or raw.get("noteId") or raw.get("id")
    if not isinstance(note_id, str) or not NOTE_ID_FULL.match(note_id):
        return None
    images = _clean_images(raw.get("images") or raw.get("imageList") or raw.get("image_list"))
    return {
        "note_id": note_id,
        "url": note_url(note_id),
        "title": _text(raw.get("title"), 300),
        "description": _text(raw.get("description") or raw.get("desc"), 5000),
        "author": _text(raw.get("author") or raw.get("author_name"), 200),
        "is_video": bool(raw.get("is_video") or raw.get("isVideo") or raw.get("type") == "video"),
        "images": images,
        "source": _text(raw.get("source"), 30) or "capture",
    }


def ingest_notes(notes: list[dict], *, source: str) -> IngestResult:
    """Insert captured/imported notes without downloading any image bytes."""
    result = IngestResult(failed=[])
    db.init_db()
    conn = db.connect()
    try:
        with conn:
            for raw in notes:
                note = _normalize_captured(raw)
                if note is None:
                    result.failed.append({"note_id": None, "error": "note id 无效"})
                    continue
                existing = conn.execute(
                    "SELECT id FROM posts WHERE platform = ? AND source_id = ?",
                    (PLATFORM, note["note_id"])).fetchone()
                if existing is not None:
                    result.skipped += 1
                    continue
                post_id = conn.execute(
                    """INSERT INTO posts (platform, source_id, url, title, description,
                                          is_video, author_name, fav_state, source_state,
                                          miss_streak, last_seen_at, extra_json)
                       VALUES (?,?,?,?,?,?,?,'unverified','ok',0,?,?)""",
                    (PLATFORM, note["note_id"], note["url"], note["title"], note["description"],
                     int(note["is_video"]), note["author"], _now(),
                     json.dumps({"import": source}, ensure_ascii=False))).lastrowid
                for position, image in enumerate(note["images"]):
                    conn.execute(
                        """INSERT INTO post_images (post_id, position, image_id, remote_url, download_state)
                           VALUES (?,?,NULL,?,'remote_only')""",
                        (post_id, position, image["remote_url"]))
                result.created += 1
    finally:
        conn.close()
    return result


def import_captured(payload: dict) -> dict:
    """Browser plugin payload: notes already present in the page. Never fetches."""
    if not isinstance(payload, dict):
        raise ImportRejected("请求格式错误")
    notes = payload.get("notes") or payload.get("posts") or payload.get("items") or []
    if not isinstance(notes, list) or not notes:
        raise ImportRejected("没有可导入的收藏数据")
    if len(notes) > 200:
        raise ImportRejected("一次最多导入 200 条")
    result = ingest_notes(notes, source="capture")
    return _remember("captured", {"mode": "captured", **result.as_dict()})


def _decode_initial_state(html: str) -> dict:
    """Parse window.__INITIAL_STATE__ without eval; replace bare undefined with null."""
    marker = "window.__INITIAL_STATE__"
    at = html.find(marker)
    if at < 0:
        raise ImportRejected("页面里没有 __INITIAL_STATE__")
    eq = html.find("=", at + len(marker))
    if eq < 0:
        raise ImportRejected("页面数据格式无法解析")
    start = html.find("{", eq)
    if start < 0:
        raise ImportRejected("页面数据格式无法解析")
    depth, in_string, escape = 0, False, False
    out = []
    index = start
    while index < len(html):
        char = html[index]
        if in_string:
            out.append(char)
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
        else:
            if char == '"':
                in_string = True
                out.append(char)
            elif char == "{":
                depth += 1
                out.append(char)
            elif char == "}":
                depth -= 1
                out.append(char)
                if depth == 0:
                    break
            elif html.startswith("undefined", index):
                before = html[index - 1] if index else " "
                after = html[index + 9] if index + 9 < len(html) else " "
                if not (before.isalnum() or before == "_") and not (after.isalnum() or after == "_"):
                    out.append("null")
                    index += 8
                else:
                    out.append(char)
            else:
                out.append(char)
        index += 1
    try:
        return json.loads("".join(out))
    except (TypeError, ValueError) as exc:
        raise ImportRejected("页面数据解析失败") from exc


def _find_note(state: dict, note_id: str) -> dict:
    note = state.get("note") if isinstance(state, dict) else None
    mapping = (note or {}).get("noteDetailMap") if isinstance(note, dict) else None
    if not isinstance(mapping, dict):
        mapping = state.get("noteDetailMap") if isinstance(state, dict) else None
    if isinstance(mapping, dict):
        entry = mapping.get(note_id)
        if isinstance(entry, dict):
            inner = entry.get("note")
            if isinstance(inner, dict):
                return inner
            return entry
    raise ImportRejected("页面里找不到这条笔记")


def normalize_ssr_note(state: dict, note_id: str) -> dict:
    note = _find_note(state, note_id)
    images = note.get("imageList") or note.get("image_list") or []
    normalized = {
        "note_id": note.get("noteId") or note.get("note_id") or note_id,
        "title": note.get("title") or "",
        "description": note.get("desc") or note.get("description") or "",
        "author": ((note.get("user") or {}).get("nickname") if isinstance(note.get("user"), dict) else "") or "",
        "is_video": note.get("type") == "video" or bool(note.get("video")),
        "images": _clean_images(images),
    }
    return normalized


def _http_fetch(url: str, *, client_factory=None) -> tuple[str, str]:
    """Anonymous fetch with manual, validated redirects (review finding 9).

    Automatic follow_redirects=True could be sent to an arbitrary host returned by
    a short link and never re-checked the local cooldown. Every hop is validated
    against ALLOWED_HOSTS, userinfo/ports are rejected and safety.require() runs
    before each request; callers still hold the batch-level lock.
    """
    factory = client_factory or httpx.Client
    with factory(follow_redirects=False, timeout=FETCH_TIMEOUT,
                 headers={"User-Agent": UA}) as client:
        current = url
        for _hop in range(MAX_REDIRECTS + 1):
            _validated_target(current)
            safety.require()
            try:
                response = client.get(current)
            except (httpx.InvalidURL, httpx.UnsupportedProtocol):
                # httpx builds the next request even with follow_redirects=False, so a
                # malformed Location (e.g. javascript:) surfaces here; never retry it.
                raise ImportRejected("重定向目标无效") from None
            if response.is_redirect:
                location = response.headers.get("location")
                if not location:
                    raise ImportRejected("重定向缺少目标地址")
                current = urljoin(str(response.url), location)
                continue
            response.raise_for_status()
            _validated_target(str(response.url))
            return str(response.url), response.text
    raise ImportRejected(f"重定向超过 {MAX_REDIRECTS} 次")


class ShareImporter:
    def __init__(self, fetcher: Callable[[str], tuple[str, str]] | None = None,
                 sleep: Callable[[float], None] = time.sleep,
                 rng: random.Random | None = None):
        self.fetcher = fetcher or _http_fetch
        self.sleep = sleep
        self.rng = rng or random.Random()

    def import_urls(self, urls) -> dict:
        if not isinstance(urls, list) or not urls:
            raise ImportRejected("没有可导入的分享链接")
        if len(urls) > MAX_SHARE_BATCH:
            raise ImportRejected(f"每批最多 {MAX_SHARE_BATCH} 条")
        try:
            safety.require()
        except safety.CoolingDown as exc:
            raise SafetyBlocked(str(exc)) from None
        created, skipped, failed, seen = 0, 0, [], set()
        for index, url in enumerate(urls):
            if not isinstance(url, str):
                failed.append(_failure(index, url, "链接格式错误"))
                continue
            if index:
                self.sleep(self.rng.uniform(MIN_DELAY_S, MAX_DELAY_S))
            direct_id = None
            try:
                direct_id = note_id_from_url(url)
                resolved_url = url
                html = ""
                if direct_id is None:  # xhslink short link: resolve first, still one request
                    safety.require()
                    resolved_url, html = self.fetcher(url)
                    final = _validated_target(resolved_url)
                    if _risk_response(resolved_url, html):
                        _trip_risk()
                    match = NOTE_ID_RE.search(final.path or "")
                    direct_id = match.group(1) if match else None
                if direct_id is None or not NOTE_ID_FULL.match(direct_id):
                    failed.append(_failure(index, url, "无法解析笔记编号"))
                    continue
                if direct_id in seen:
                    skipped += 1
                    continue
                seen.add(direct_id)
                if not html:
                    safety.require()
                    resolved_url, html = self.fetcher(url)
                    _validated_target(resolved_url)
                    if _risk_response(resolved_url, html):
                        _trip_risk()
                note = normalize_ssr_note(_decode_initial_state(html), direct_id)
                result = ingest_notes([{**note, "source": "share"}], source="share")
                created += result.created
                skipped += result.skipped
                failed.extend(_failure(index, url, item.get("error") or "导入失败", item.get("note_id"))
                              for item in result.failed)
            except SafetyBlocked:
                raise
            except safety.CoolingDown as exc:
                raise SafetyBlocked(str(exc)) from None
            except ImportRejected as exc:
                failed.append(_failure(index, url, str(exc), direct_id))
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in RISK_STATUSES:
                    _trip_risk()
                failed.append(_failure(index, url, "网络请求失败", direct_id))
            except httpx.HTTPError:
                failed.append(_failure(index, url, "网络请求失败", direct_id))
            except Exception:
                failed.append(_failure(index, url, "导入失败", direct_id))
        return _remember("share", {"mode": "share", "created": created, "skipped": skipped,
                               "failed": failed, "total": created + skipped + len(failed)})
