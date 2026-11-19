"""Huaban (花瓣) reader -> sync-reader/1 pages. Reads only; never writes the DB.

Carried over from the verified spike:
- Huaban must run in a real headed Chrome with a persistent profile; headless / plain
  HTTP gets HTTP 405. The browser below is always headed; visible=False only moves it
  off-screen for normal syncs. The login window is visible.
- The page's own v3 JSON is used via fetch() inside the authenticated page (cookies and
  same-origin are the browser's); no signed private API is replayed by Python.
- "My boards" needs joined=1 + urlname + boards:BOARD fields; the default
  "未整理的采集" board only appears in that form.
- Login is judged from the /follow page (urlname in HTML). The old /v3/user route is
  NOT used: it can report a wrong/guest identity.
- A blank page, a non-JSON body or a response without the expected key is a network /
  anomaly error, never "empty collection".
- Paging is strict: boards use max=<last board_id>, pins use max=<last pin_id>, newest
  first. cursor_out is the last raw pin_id on the page, so a new process can resume
  directly with &max=<cursor>. If the API ever fails to advance, the reader raises
  instead of looping forever.

Dedupe / cursors / failures / posts are the main sync engine + store's job. This reader
only emits one page at a time and never keeps its own state.json.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import time
from pathlib import Path
from typing import Callable, Protocol
from urllib.parse import quote, urlparse

from ..contract import CONTRACT_VERSION
from ..engine import ReaderError
from ..metrics import timed

PLATFORM = "huaban"
COLLECTION_PREFIX = "board:"
PAGE_SIZE = 50
BOARDS_PAGE_SIZE = 30
MAX_BOARD_PAGES = 20
MAX_PINS_PER_PAGE = 100
DEFAULT_DELAY = 0.4

PIN_URL = "https://huaban.com/pins/{pin_id}"
IMG_HOST = "https://gd-hbimg.huaban.com"
FOLLOW_URL = "https://huaban.com/follow"
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36")

_URLNAME = re.compile(r'"urlname"\s*:\s*"([^"\\]+)"')
_AUTH_KEY = re.compile(r"([?&]auth_key=)[^&\s\"']+", re.I)
_BOARD_COLLECTION = re.compile(r"^board:(\d+)$")
MIN_HTML = 2000


def clean(text: object, limit: int = 300) -> str:
    """Redact signed image auth keys from errors/log text."""
    return _AUTH_KEY.sub(r"\1***", str(text))[:limit] if text is not None else ""


def pin_url(pin_id: object) -> str:
    return PIN_URL.format(pin_id=pin_id)


def board_collection_key(board_id: object) -> str:
    return COLLECTION_PREFIX + str(board_id)


def parse_collection_key(collection_key: str) -> str:
    match = _BOARD_COLLECTION.match(collection_key or "")
    if not match:
        raise ValueError(f"unknown collection {collection_key!r}（应为 board:<board_id>）")
    return match.group(1)


def boards_path(urlname: str, cursor: str | None = None, limit: int = BOARDS_PAGE_SIZE) -> str:
    """The exact form the web app uses; joined=1/urlname are required for my boards."""
    path = f"/v3/{quote(urlname, safe='')}/boards?limit={int(limit)}"
    if cursor:
        path += f"&max={quote(str(cursor), safe='')}"
    fields = quote("boards:BOARD|user,total,page_num,page_size", safe="")
    return path + f"&fields={fields}&joined=1&urlname={quote(urlname, safe='')}"


def pins_path(board_id: str, cursor: str | None = None, limit: int = PAGE_SIZE) -> str:
    path = (f"/v3/boards/{quote(str(board_id), safe='')}/pins?limit={int(limit)}"
            f"&fields={quote('pins:PIN|board:BOARD_DETAIL', safe='')}")
    if cursor:
        path += f"&max={quote(str(cursor), safe='')}"
    return path


def image_url(pin: dict) -> str:
    """Prefer the anonymous origin URL by key; signed file.url is only a fallback."""
    file = pin.get("file") or {}
    key = file.get("key") or ""
    if key:
        return IMG_HOST + "/" + str(key)
    url = file.get("url") or ""
    return str(url)


def guess_ext(url: str, content_type: str = "") -> str:
    ctype = (content_type or "").lower()
    for needle, ext in (("jpeg", ".jpg"), ("jpg", ".jpg"), ("png", ".png"),
                        ("webp", ".webp"), ("gif", ".gif"), ("avif", ".avif")):
        if needle in ctype:
            return ext
    suffix = Path(urlparse(url).path).suffix.lower()
    return suffix if suffix in {".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif"} else ".jpg"


def _first(value: object) -> str:
    return str(value or "").strip()


def normalize_pin(pin: dict) -> dict:
    """Huaban pin v3 JSON -> contract fields. Raises ValueError if pin_id is missing."""
    pin_id = pin.get("pin_id")
    if pin_id in (None, ""):
        raise ValueError("pin 缺少 pin_id")
    user = pin.get("user") or {}
    author = (_first(user.get("username")) or _first(user.get("urlname"))
              or _first(user.get("nickname")) or None)
    raw = _first(pin.get("raw_text"))
    title = _first(pin.get("title")) or raw[:120] or None
    description = _first(pin.get("description"))
    if not description and raw and raw != title:
        description = raw
    file = pin.get("file") or {}
    key = _first(file.get("key"))
    media = _first(pin.get("media_type"))
    if media == "video":
        media_type = "video"
    elif media in ("", "image", "photo"):
        media_type = "image"
    else:
        media_type = "unknown"
    return {
        "work_key": str(pin_id),
        "title": title,
        "description": description,
        "author_name": author,
        "canonical_url": pin_url(pin_id),
        "media_type": media_type,
        "image_url": image_url(pin),
        "board_id": str(pin.get("board_id") or "") or None,
        "source_url": _first(pin.get("link")) or None,
        "file_key": key or None,
        "created_at": pin.get("created_at"),
    }


def _has_more(payload: dict, items: list, limit: int) -> bool:
    if isinstance(payload.get("has_more"), bool):
        return payload["has_more"]
    return len(items) >= limit


def _next_cursor(payload: dict, items: list, key: str) -> str | None:
    explicit = payload.get("next_cursor") or payload.get("max")
    if explicit not in (None, ""):
        return str(explicit)
    if items and isinstance(items[-1], dict) and items[-1].get(key) not in (None, ""):
        return str(items[-1][key])
    return None


class Browser(Protocol):
    """Injected browser. Tests use a fake; the real one is HuabanBrowser below."""

    def open(self) -> None: ...
    def close(self) -> None: ...
    def check_login(self) -> dict: ...                              # {state, urlname?}
    def list_boards(self, urlname: str, cursor: str | None, limit: int) -> dict: ...
    def list_pins(self, board_id: str, cursor: str | None, limit: int) -> dict: ...
    def download(self, url: str) -> tuple[bytes, str]: ...


class HuabanReader:
    """engine.Reader for one Huaban board collection. Use as a context manager."""

    platform = PLATFORM

    def __init__(self, browser: Browser, staging_dir: Path, *,
                 is_complete: Callable[[str], bool] = lambda key: False,
                 page_size: int = PAGE_SIZE, delay: float = DEFAULT_DELAY,
                 sleep: Callable[[float], None] = time.sleep):
        self.browser = browser
        self.staging = Path(staging_dir)
        self.is_complete = is_complete
        self.page_size = min(int(page_size), MAX_PINS_PER_PAGE)
        self.delay, self.sleep = delay, sleep
        self.urlname = ""

    @property
    def account_key(self) -> str:
        """Huaban urlname of the logged-in account."""
        return self.urlname

    def __enter__(self) -> "HuabanReader":
        shutil.rmtree(self.staging, ignore_errors=True)
        self.staging.mkdir(parents=True, exist_ok=True)
        self.browser.open()
        return self

    def __exit__(self, *exc) -> None:
        try:
            self.browser.close()
        finally:
            shutil.rmtree(self.staging, ignore_errors=True)

    # -- auth
    def check_auth(self) -> str:
        """'ok' | 'needs_login' | 'blocked' | 'rate_limited'. Network raises."""
        try:
            result = self.browser.check_login()
        except ReaderError:
            raise
        except Exception as exc:
            raise ReaderError("network", clean(f"check_auth: {type(exc).__name__}: {exc}")) from exc
        state = (result or {}).get("state")
        if state == "ok":
            urlname = _first((result or {}).get("urlname"))
            if not urlname:
                raise ReaderError("network", "登录页显示已登录但拿不到 urlname，不当作有效登录")
            self.urlname = urlname
            return "ok"
        if state in ("needs_login", "blocked", "rate_limited"):
            return state
        if state in ("blank", "anomaly"):
            raise ReaderError("network", "花瓣页面/接口异常（空白或非 JSON），不当作空收藏")
        raise ReaderError("network", f"未知登录状态 {state!r}，不当作空收藏")

    # -- my boards (not a sync collection; helper for the caller)
    def list_boards(self) -> list[dict]:
        if not self.urlname:
            state = self.check_auth()
            if state != "ok":
                raise ReaderError(state, "未登录，无法读取我的画板")
        boards: list[dict] = []
        seen: set[str] = set()
        cursor: str | None = None
        for _page in range(MAX_BOARD_PAGES):
            try:
                payload = self.browser.list_boards(self.urlname, cursor, BOARDS_PAGE_SIZE)
            except ReaderError:
                raise
            except Exception as exc:
                raise ReaderError("network", clean(f"list_boards: {type(exc).__name__}: {exc}")) from exc
            raw = payload.get("boards")
            if not isinstance(raw, list):
                raise ReaderError("network", "画板接口缺少 boards（不当作空画板列表）")
            for board in raw:
                if not isinstance(board, dict) or board.get("board_id") in (None, ""):
                    raise ReaderError("network", "画板数据缺少 board_id")
                board_id = str(board["board_id"])
                if board_id in seen:
                    continue
                seen.add(board_id)
                boards.append({
                    "collection_key": board_collection_key(board_id),
                    "board_id": board_id,
                    "title": _first(board.get("title")) or None,
                    "pin_count": board.get("pin_count"),
                    "is_private": bool(board.get("is_private")),
                    "urlname": _first(board.get("urlname")) or self.urlname,
                })
            has_more = _has_more(payload, raw, BOARDS_PAGE_SIZE)
            if not has_more:
                break
            nxt = _next_cursor(payload, raw, "board_id")
            if not nxt or nxt == cursor:
                raise ReaderError("network", "画板分页没有前进（不当作已到末尾）")
            cursor = nxt
        else:
            raise ReaderError("limit", f"画板超过 {MAX_BOARD_PAGES} 页")
        return boards

    # -- one sync-reader/1 page
    def fetch_page(self, collection_key: str, cursor: str | None, page_index: int) -> dict:
        board_id = parse_collection_key(collection_key)
        try:
            return self._fetch_page(board_id, collection_key, cursor, page_index)
        except ReaderError:
            raise
        except Exception as exc:
            raise ReaderError("network", clean(f"{type(exc).__name__}: {exc}")) from exc

    def _fetch_page(self, board_id: str, collection_key: str, cursor: str | None,
                    page_index: int) -> dict:
        try:
            payload = self.browser.list_pins(board_id, cursor, self.page_size)
        except ReaderError:
            raise
        except Exception as exc:
            raise ReaderError("network", clean(f"list_pins: {type(exc).__name__}: {exc}")) from exc
        raw = payload.get("pins")
        if not isinstance(raw, list):
            raise ReaderError("network", "采集接口缺少 pins（不当作空画板）")
        has_more = _has_more(payload, raw, self.page_size)
        last_raw_id = str(raw[-1]["pin_id"]) if raw and isinstance(raw[-1], dict) and raw[-1].get("pin_id") not in (None, "") else None
        if has_more and not last_raw_id:
            raise ReaderError("network", "采集分页没有可续读的 cursor（不当作已到末尾）")
        if cursor is not None and last_raw_id is not None and str(cursor).isdigit() and last_raw_id.isdigit():
            if int(last_raw_id) >= int(cursor):
                raise ReaderError("network", "采集分页没有前进（max= 未被接受）")

        items: list[dict] = []
        failures: list[dict] = []
        seen: set[str] = set()
        for pin in raw:
            if not isinstance(pin, dict):
                raise ReaderError("network", "pin 数据不是对象")
            pin_id = pin.get("pin_id")
            if pin_id in (None, ""):
                raise ReaderError("network", "pin 缺少 pin_id（无法记录可重试工作）")
            work_key = str(pin_id)
            if work_key in seen:
                continue
            seen.add(work_key)
            if self.is_complete(work_key):
                items.append(self._fast_item(pin, work_key))
                continue
            item, item_failures = self._full_item(pin, work_key)
            items.append(item)
            failures.extend(item_failures)
            if self.delay:
                self.sleep(self.delay)

        cursor_out = last_raw_id or cursor
        return {
            "contract_version": CONTRACT_VERSION,
            "platform": PLATFORM,
            "collection": {"collection_key": collection_key},
            "page": {
                "index": page_index,
                "cursor_in": cursor,
                "cursor_out": cursor_out,
                "has_more": has_more,
            },
            "items": items,
            "failures": failures,
        }

    def _fast_item(self, pin: dict, work_key: str) -> dict:
        """Already complete locally: refresh identity only; no download, no fields overwrite."""
        try:
            meta = normalize_pin(pin)
        except ValueError:
            meta = {"author_name": None, "canonical_url": pin_url(work_key), "media_type": "unknown"}
        return {
            "work_key": work_key,
            "author_name": meta.get("author_name"),
            "canonical_url": meta.get("canonical_url") or pin_url(work_key),
            "media_type": "unknown",
            "images": [],
        }

    def _full_item(self, pin: dict, work_key: str) -> tuple[dict, list[dict]]:
        meta = normalize_pin(pin)
        failures: list[dict] = []
        images: list[dict] = []
        url = meta["image_url"]
        if not url:
            failures.append({"work_key": work_key, "stage": "detail",
                             "error": "采集没有图片地址"})
        else:
            ref, error = self._download(work_key, 1, url)
            images.append(ref)
            if error:
                failures.append({"work_key": work_key, "stage": "image_download",
                                 "error": error})
        return {
            "work_key": work_key,
            "title": meta["title"],
            "description": meta["description"],
            "author_name": meta["author_name"],
            "canonical_url": meta["canonical_url"],
            "media_type": meta["media_type"],
            "images": images,
            "extra": {
                "board_id": meta["board_id"],
                "source_url": meta["source_url"],
                "file_key": meta["file_key"],
                "created_at": meta["created_at"],
            },
        }, failures

    def _download(self, work_key: str, idx: int, url: str) -> tuple[dict, str | None]:
        """Failed downloads keep idx + url (download_state=failed); the slot is not dropped."""
        try:
            data, content_type = self.browser.download(url)
            if not data:
                raise ValueError("空响应")
        except Exception as exc:
            return {"idx": idx, "url": url, "download_state": "failed"}, clean(exc, 160)
        suffix = guess_ext(url, content_type)
        path = self.staging / f"{work_key}_{idx:02d}{suffix}"
        try:
            path.write_bytes(data)
        except Exception as exc:
            return {"idx": idx, "url": url, "download_state": "failed"}, clean(exc, 160)
        return {
            "idx": idx,
            "url": url,
            "local_path": str(path),
            "sha256": hashlib.sha256(data).hexdigest(),
            "download_state": "ok",
        }, None


class HuabanBrowser:
    """Playwright persistent Chrome for Huaban. Always headed; visible for QR login.

    One page only: login, API fetches and downloads all reuse it. No page-per-pin and
    no about:blank popups, so a run cannot open an unbounded number of tabs.
    """

    def __init__(self, profile_dir: Path, visible: bool = False,
                 log: Callable[[str], None] = print):
        self.profile_dir = Path(profile_dir)
        self.visible = visible
        self.log = log
        self._pw = self.context = self.page = None
        self._closed = False

    def open(self) -> None:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise ReaderError("config", "花瓣 reader 需要 playwright：pip install playwright") from exc
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self._pw = sync_playwright().start()
        args = ["--disable-blink-features=AutomationControlled"]
        if not self.visible:
            args += ["--window-position=-32000,-32000", "--window-size=1366,900"]
        kwargs = dict(user_data_dir=str(self.profile_dir), headless=False, args=args,
                      viewport={"width": 1366, "height": 900}, locale="zh-CN",
                      user_agent=USER_AGENT)
        try:
            try:
                self.context = self._pw.chromium.launch_persistent_context(channel="chrome", **kwargs)
            except Exception:
                self.context = self._pw.chromium.launch_persistent_context(**kwargs)
        except Exception:
            self._pw.stop()
            self._pw = None
            raise
        self.context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")
        for old in list(self.context.pages):
            try:
                old.close()
            except Exception:
                pass
        self.context.on("close", lambda *_: setattr(self, "_closed", True))
        self.page = self.context.new_page()
        self.page.on("close", lambda *_: setattr(self, "_closed", True))

    def close(self) -> None:
        try:
            if self.context:
                self.context.close()
        finally:
            if self._pw:
                self._pw.stop()
            self._pw = self.context = self.page = None

    def is_closed(self) -> bool:
        return self._closed or self.page is None or self.page.is_closed()

    # -- navigation / login
    def _goto(self, url: str, tries: int = 3) -> tuple[int, str]:
        for attempt in range(1, tries + 1):
            try:
                response = self.page.goto(url, wait_until="domcontentloaded", timeout=60000)
                self.page.wait_for_timeout(2500)
                status = response.status if response else 0
                html = self.page.content()
                if status in (405, 429):
                    return status, html
                if len(html) >= MIN_HTML and self.page.title():
                    return status, html
                self.log(f"花瓣页面未完成加载（{attempt}/{tries}，{len(html)} 字节），重试")
            except Exception as exc:
                self.log(f"打开花瓣失败（{attempt}/{tries}）：{clean(exc, 120)}")
            self.page.wait_for_timeout(1200 * attempt)
        try:
            return 0, self.page.content()
        except Exception:
            return 0, ""

    @timed("huaban", "auth")
    def check_login(self) -> dict:
        if self.is_closed():
            raise ReaderError("needs_login", "浏览器已关闭")
        status, html = self._goto(FOLLOW_URL)
        if status == 405:
            raise ReaderError("blocked", "花瓣拦截了当前浏览器（HTTP 405，需要真实有头 Chrome）")
        if status == 429:
            raise ReaderError("rate_limited", "花瓣限流（HTTP 429）")
        if status in (401, 403):
            return {"state": "needs_login"}
        if status >= 500:
            raise ReaderError("network", f"花瓣 /follow HTTP {status}")
        if not html or len(html) < 200:
            raise ReaderError("network", "花瓣 /follow 是空白页（重试后仍空白），不当作未登录")
        match = _URLNAME.search(html)
        if match:
            return {"state": "ok", "urlname": match.group(1)}
        url = self.page.url or ""
        lowered = html.lower()
        if "/login" in url or any(mark in lowered for mark in ("扫码登录", "登录花瓣", "login-form", "login_form")):
            return {"state": "needs_login"}
        if any(mark in lowered for mark in ("captcha", "安全验证", "访问异常", "访问受限")):
            return {"state": "blocked"}
        if len(html) < MIN_HTML:
            raise ReaderError("network", "花瓣 /follow 内容不完整（空白/风控页），不当作空收藏")
        return {"state": "needs_login"}

    # -- page-context JSON
    _FETCH_JS = """async (path) => {
      try {
        const r = await fetch(path, {credentials: 'include',
          headers: {'Accept': 'application/json'}});
        const text = await r.text();
        return JSON.stringify({status: r.status, ct: r.headers.get('content-type') || '',
                               body: text});
      } catch (e) {
        return JSON.stringify({status: 0, ct: '', body: '', error: String(e)});
      }
    }"""

    def _fetch_json(self, path: str) -> dict:
        if self.is_closed():
            raise ReaderError("network", "浏览器已关闭")
        if urlparse(self.page.url or "").netloc != "huaban.com":
            self._goto(FOLLOW_URL)
            if urlparse(self.page.url or "").netloc != "huaban.com":
                raise ReaderError("network", "页面不在 huaban.com，无法发起同源接口请求")
        try:
            raw = self.page.evaluate(self._FETCH_JS, path)
            pack = json.loads(raw) if raw else {}
        except Exception as exc:
            raise ReaderError("network", clean(f"花瓣接口调用失败: {type(exc).__name__}: {exc}")) from exc
        status = int(pack.get("status") or 0)
        body = pack.get("body") or ""
        if status == 0:
            raise ReaderError("network", clean(f"花瓣接口网络失败: {pack.get('error')}"))
        if status == 429:
            raise ReaderError("rate_limited", "花瓣接口限流（HTTP 429）")
        if status in (401, 403):
            lowered = body.lower()
            if "login" in lowered or "登录" in body:
                raise ReaderError("needs_login", f"花瓣接口需要登录（HTTP {status}）")
            raise ReaderError("blocked", f"花瓣接口拒绝访问（HTTP {status}）")
        if status == 405:
            raise ReaderError("blocked", "花瓣接口 HTTP 405（无头/纯 HTTP 被拦截）")
        if status != 200:
            raise ReaderError("network", f"花瓣接口 HTTP {status}: {clean(body, 160)}")
        try:
            data = json.loads(body)
        except ValueError as exc:
            lowered = body.lower()
            if "login" in lowered or "登录" in body:
                raise ReaderError("needs_login", "花瓣接口返回登录页（需要重新登录）") from exc
            raise ReaderError("network", "花瓣接口返回非 JSON（不当作空列表）") from exc
        if not isinstance(data, dict):
            raise ReaderError("network", "花瓣接口返回结构异常（不当作空列表）")
        return data

    # -- API wrappers
    @timed("huaban", "list")
    def list_boards(self, urlname: str, cursor: str | None, limit: int) -> dict:
        data = self._fetch_json(boards_path(urlname, cursor, limit))
        raw = data.get("boards")
        if not isinstance(raw, list):
            raise ReaderError("network", "花瓣画板接口缺少 boards（不当作空画板列表）")
        has_more = _has_more(data, raw, limit)
        return {"boards": raw, "has_more": has_more,
                "next_cursor": _next_cursor(data, raw, "board_id")}

    @timed("huaban", "list")
    def list_pins(self, board_id: str, cursor: str | None, limit: int) -> dict:
        data = self._fetch_json(pins_path(board_id, cursor, limit))
        raw = data.get("pins")
        if not isinstance(raw, list):
            raise ReaderError("network", "花瓣采集接口缺少 pins（不当作空画板）")
        has_more = _has_more(data, raw, limit)
        return {"pins": raw, "has_more": has_more,
                "next_cursor": _next_cursor(data, raw, "pin_id")}

    @timed("huaban", "download")
    def download(self, url: str) -> tuple[bytes, str]:
        if self.is_closed():
            raise ReaderError("network", "浏览器已关闭")
        try:
            response = self.context.request.get(
                url, headers={"Referer": "https://huaban.com/", "User-Agent": USER_AGENT},
                timeout=60000)
        except Exception as exc:
            raise ReaderError("image_download", clean(f"下载图片失败: {exc}")) from exc
        if not response.ok:
            raise ReaderError("image_download", f"下载图片 HTTP {response.status}")
        data = response.body()
        if not data:
            raise ReaderError("image_download", "下载图片响应为空")
        return data, response.headers.get("content-type", "")
