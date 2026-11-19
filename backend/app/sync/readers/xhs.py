"""Xiaohongshu favorites reader -> sync-reader/1 pages. Reads only; never writes the DB.

Ported from the verified spike (xhs-sync-alt/scripts/xhs_sync_probe.py, a–e passed on a real
account), minus its own state/manifest/dedupe: storage, sha256 dedupe, post_images order,
sync_runs/items/cursors and analyses all stay with sync.engine / sync.store.

Rules carried over from the spike's real-account findings:
- Guests also get a web_session cookie: login is judged from the page (user.loggedIn,
  the viewer's own userId) on the favorites page itself. explore's loggedIn=false is ignored.
- The first navigation sometimes returns a ~40 byte blank document: retried, and if it
  persists it is a network error, never "no favorites".
- No signed private API is replayed: the list comes from the favorites page's own SSR data
  and the collect/page responses it fires while scrolling; details from the note page's
  own feed response, falling back to its SSR noteDetailMap entry for that exact note_id.
- One persistent context, two fixed pages (favorites list + detail), images via the
  context's request client. No page per note.

Paging: the captured list is cut into fixed pages of PAGE_SIZE; cursor_out is the page's last
note_id. A new process cannot jump to a cursor (no API replay): resuming scrolls the list
from the top until that note_id shows up, then continues after it. If the anchor is gone
(un-favorited) the list is walked again from the top and `restarted` is set.
has_more=false is only reported when the site's own list response says so; a list that stops
growing while the site still says has_more raises ReaderError instead (no fake full scan).
"""
import hashlib
import json
import re
import shutil
import time
from pathlib import Path
from typing import Callable, Protocol
from urllib.parse import urlparse

from ..engine import ReaderError
from ..metrics import timed
from .. import safety

PLATFORM = "xhs"
COLLECTION_KEY = "fav"
PAGE_SIZE = 10
MAX_NOTES = 1000          # hard cap per run
DETAIL_DELAY = 3.0        # seconds between detail pages (conservative, manual sync only)
FAVORITES_URL = "https://www.xiaohongshu.com/user/profile/{uid}?tab=fav&subTab=note"
EXPLORE_URL = "https://www.xiaohongshu.com/explore"
NOTE_URL = "https://www.xiaohongshu.com/explore/{note_id}"
COLLECT_API = "/api/sns/web/v2/note/collect/page"
FEED_API = "/api/sns/web/v1/feed"
USER_ME_API = "https://edith.xiaohongshu.com/api/sns/web/v2/user/me"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36")
_UID = re.compile(r"^[0-9a-f]{24}$")
_TOKEN = re.compile(r"(xsec_token=)[^&\s\"']+", re.I)


def clean(text: object, limit: int = 300) -> str:
    """Error text for sync_items / logs: never carries an xsec_token."""
    return _TOKEN.sub(r"\1***", str(text))[:limit]


def note_url(note_id: str) -> str:
    """canonical_url: the plain note link, without xsec_token."""
    return NOTE_URL.format(note_id=note_id)


# ---------------------------------------------------------------- extraction (offline-testable)

def unwrap(v):
    """Vue reactive wrappers in __INITIAL_STATE__ (_rawValue / _value / value)."""
    depth = 0
    while isinstance(v, dict) and depth < 8:
        depth += 1
        for key in ("_rawValue", "__v_raw", "_value", "value"):
            if key in v and len(v) <= 4:
                v = v[key]
                break
        else:
            break
    return v


def pick(obj, *keys, default=None):
    for k in keys:
        if isinstance(obj, dict) and obj.get(k) not in (None, ""):
            return obj[k]
    return default


def normalize_list_item(raw) -> dict | None:
    """Favorites entry (SSR or collect/page) -> {note_id, xsec_token, title, author, cover, type}."""
    if not isinstance(raw, dict):
        return None
    card = unwrap(raw.get("noteCard") or raw.get("note_card") or raw)
    if not isinstance(card, dict):
        card = raw
    note_id = pick(raw, "id", "noteId", "note_id") or pick(card, "noteId", "note_id", "id")
    if not note_id:
        return None
    token = pick(raw, "xsecToken", "xsec_token") or pick(card, "xsecToken", "xsec_token")
    user = unwrap(card.get("user") or {})
    cover = unwrap(card.get("cover") or {})
    cover_url = ""
    info_list = cover.get("infoList") or cover.get("info_list") or [] if isinstance(cover, dict) else []
    if isinstance(info_list, list):
        for it in info_list:
            it = unwrap(it) or {}
            cover_url = it.get("url") or it.get("urlDefault") or ""
            if cover_url:
                break
    if isinstance(cover, dict):
        cover_url = cover_url or cover.get("url") or cover.get("urlDefault") or ""
    return {
        "note_id": str(note_id),
        "xsec_token": str(token) if token else "",
        "title": str(pick(card, "displayTitle", "display_title", "title", default="") or ""),
        "author": str(pick(unwrap(user), "nickname", "nickName", "nick_name", "name", default="") or ""),
        "cover": str(cover_url or ""),
        "type": str(pick(card, "type", default="") or ""),
    }


def normalize_image_list(note_card) -> list[dict]:
    """All images of a note in server order. index is the 0-based position in the note;
    an entry whose URL could not be found keeps its index with url="" (never dropped)."""
    if not isinstance(note_card, dict):
        return []
    raw_list = unwrap(note_card.get("imageList") or note_card.get("image_list") or []) or []
    images = []
    for idx, img in enumerate(raw_list):
        img = unwrap(img) or {}
        if not isinstance(img, dict):
            img = {}
        url = (img.get("urlDefault") or img.get("url_default")
               or img.get("urlPre") or img.get("url_pre") or img.get("url") or "")
        if not url:
            for it in img.get("infoList") or img.get("info_list") or []:
                it = unwrap(it) or {}
                url = it.get("url") or ""
                if url:
                    break
        images.append({"index": idx, "url": str(url),
                       "width": img.get("width") or 0, "height": img.get("height") or 0})
    return images


def normalize_note_detail(payload) -> dict | None:
    """/v1/feed response or an SSR noteDetailMap entry -> {note_id, card, images}."""
    if not isinstance(payload, dict):
        return None
    card = None
    items = payload.get("items")
    if isinstance(items, list) and items:
        first = unwrap(items[0]) or {}
        card = unwrap(first.get("note_card") or first.get("noteCard") or first)
    if card is None and isinstance(payload.get("note"), dict):
        card = unwrap(payload["note"])
    if card is None and isinstance(payload.get("noteCard"), dict):
        card = unwrap(payload["noteCard"])
    if not isinstance(card, dict):
        return None
    note_id = pick(card, "noteId", "note_id", "id")
    if not note_id:
        return None
    return {"note_id": str(note_id), "card": card, "images": normalize_image_list(card)}


def guess_ext(url: str, content_type: str = "") -> str:
    ct = (content_type or "").lower()
    for key, ext in (("png", ".png"), ("webp", ".webp"), ("gif", ".gif"), ("jpeg", ".jpg"), ("jpg", ".jpg")):
        if key in ct:
            return ext
    path = urlparse(url).path.lower()
    for ext in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
        if path.endswith(ext):
            return ".jpg" if ext == ".jpeg" else ext
    return ".jpg"


# Site error codes seen on the web API; anything else non-successful counts as network.
_LOGIN_CODES = {-100, -101, -104}
_RATE_CODES = {300013}
_BLOCK_CODES = {300011, 300012, 300015, 461, 471}


def risk_text(text):
    return bool(re.search(r'操作.{0,20}(不像|非正常|不是.{0,5}正常人)|安全警告|行为异常|当前网络.{0,10}风险|异常流量|访问异常|安全限制|请完成.{0,5}验证|滑动验证', str(text)))


def classify_api_error(http_status: int, body) -> str | None:
    """None if the response is a success; else needs_login / rate_limited / blocked / network."""
    if isinstance(body,dict) and risk_text(body.get("msg") or body.get("message") or ""):
        return "blocked"
    if http_status in (461, 471):
        return "blocked"
    if http_status == 429:
        return "rate_limited"
    if http_status in (401, 403):
        return "needs_login"
    if http_status != 200 or not isinstance(body, dict):
        return "network"
    if body.get("success") is True or body.get("code") == 0:
        return None
    code = body.get("code")
    if code in _LOGIN_CODES:
        return "needs_login"
    if code in _RATE_CODES:
        return "rate_limited"
    if code in _BLOCK_CODES:
        return "blocked"
    return "network"


def judge_identity(ssr: dict, me: dict | None) -> dict:
    """Who is logged in. Guests also get a 24-hex user id (and a web_session), so an id
    alone proves nothing: it counts only with SSR user.loggedIn === true, or from
    /user/me with an explicit guest === false. -> {logged_in, user_id ('' if unproven)}."""
    ssr_id = str((ssr or {}).get("userId") or "")
    if (ssr or {}).get("loggedIn") is True and _UID.match(ssr_id):
        return {"logged_in": True, "user_id": ssr_id}
    me = me or {}
    me_id = str(me.get("user_id") or "")
    if me.get("guest") is False and _UID.match(me_id):
        return {"logged_in": True, "user_id": me_id}
    return {"logged_in": (ssr or {}).get("loggedIn") is True, "user_id": ""}


def login_candidates(probe: dict) -> list[str]:
    """User ids that *might* be the logged-in account (still to be verified on the
    favorites page). Ids /user/me flags as guest are never candidates."""
    out = []
    ssr, me = probe.get("ssr") or {}, probe.get("me") or {}
    sid, mid = str(ssr.get("userId") or ""), str(me.get("user_id") or "")
    if ssr.get("loggedIn") is True and _UID.match(sid):
        out.append(sid)
    if _UID.match(mid) and me.get("guest") is not True and mid not in out:
        out.append(mid)
    nav = str(probe.get("nav") or "")
    if _UID.match(nav) and nav not in out:
        out.append(nav)
    return out


def describe_probe(probe: dict) -> str:
    """Diagnostic line without cookies/tokens: flags and id prefixes only."""
    ssr, me = probe.get("ssr") or {}, probe.get("me")
    sid = str(ssr.get("userId") or "")
    parts = [f"ssr.loggedIn={ssr.get('loggedIn')!r}", f"ssr.userId={sid[:8] or '-'}"]
    parts.append(f"nav={str(probe.get('nav') or '')[:8] or '-'}")
    if me is None:
        parts.append("me=unavailable")
    else:
        parts += [f"me.keys={sorted(me)[:12]}", f"me.guest={me.get('guest')!r}",
                  f"me.user_id={str(me.get('user_id') or '')[:8] or '-'}"]
    return " ".join(parts)


LOGIN_WINDOW = {"left": 80, "top": 60, "width": 1200, "height": 900}


def launch_args(visible: bool) -> list[str]:
    """Login: a normal on-screen window (explicit position, so the off-screen placement a
    sync run saved in the profile is not restored). Sync: headed but off-screen."""
    args = ["--disable-blink-features=AutomationControlled"]
    if visible:
        w = LOGIN_WINDOW
        return args + [f"--window-position={w['left']},{w['top']}", f"--window-size={w['width']},{w['height']}"]
    return args + ["--window-position=-32000,-32000", "--window-size=1000,800"]


def page_kind(url: str) -> str | None:
    """Where a navigation ended up: 'captcha' / 'login' / 'notfound' / None (normal)."""
    u = (url or "").lower()
    if "captcha" in u or "website-login/verify" in u or "/security" in u:
        return "captcha"
    if "/login" in u or "website-login" in u:
        return "login"
    if "/404" in u:
        return "notfound"
    return None


# ---------------------------------------------------------------- browser layer

class Browser(Protocol):
    def open(self) -> None: ...
    def close(self) -> None: ...
    def own_identity(self) -> dict: ...                  # {logged_in, user_id} from explore
    @timed("xhs", "list")
    def open_favorites(self, uid: str) -> str: ...      # ok / blank / login / captcha
    def viewer(self) -> dict: ...                        # {logged_in, user_id} on favorites page
    def list_snapshot(self) -> dict: ...                 # {notes, has_more, api_error}
    def scroll_list(self) -> None: ...
    def fetch_detail(self, note_id: str, token: str) -> dict: ...
    def download(self, url: str) -> tuple[bytes, str]: ...


_UNWRAP_JS = """
const unwrap = (o) => {
  let d = 0;
  while (o && typeof o === 'object' && d < 10) {
    d += 1;
    if ('_rawValue' in o && o._rawValue && typeof o._rawValue === 'object') { o = o._rawValue; continue; }
    if ('_value' in o && o._value && typeof o._value === 'object') { o = o._value; continue; }
    if ('__v_raw' in o && o.__v_raw && typeof o.__v_raw === 'object') { o = o.__v_raw; continue; }
    if ('value' in o && Object.keys(o).length <= 4 && o.value && typeof o.value === 'object') { o = o.value; continue; }
    break;
  }
  return o;
};
// Refs around primitives too (e.g. loggedIn: {_rawValue: true, ...}); unwrap only reaches objects.
const val = (o) => {
  let d = 0;
  while (o && typeof o === 'object' && d < 10) {
    d += 1;
    if ('_rawValue' in o) { o = o._rawValue; continue; }
    if ('_value' in o) { o = o._value; continue; }
    if ('value' in o && Object.keys(o).length <= 4) { o = o.value; continue; }
    break;
  }
  return o;
};
"""

# The *viewer* (who is logged in), not the profile owner shown on the page.
_VIEWER_JS = "() => { try {" + _UNWRAP_JS + """
  const u = (window.__INITIAL_STATE__ || {}).user || {};
  const ui = unwrap(u.userInfo) || {};
  return JSON.stringify({ loggedIn: val(u.loggedIn) === true,
                          userId: ui.userId || ui.user_id || '' });
} catch (e) { return ''; } }"""

# The sidebar "我" link points to the logged-in user's own profile (guests have none).
_NAV_ME_JS = r"""() => {
  for (const a of document.querySelectorAll('a[href*="/user/profile/"]')) {
    if ((a.textContent || '').trim() === '我') {
      const m = a.getAttribute('href').match(/\/user\/profile\/([0-9a-f]{24})/);
      if (m) return m[1];
    }
  }
  return '';
}"""

_SSR_FAVS_JS = "() => { try {" + _UNWRAP_JS + """
  const st = window.__INITIAL_STATE__;
  if (!st || !st.user) return '';
  const u = st.user;
  const notes = unwrap(u.notes);
  const active = unwrap(u.activeTab) || {};
  const idx = typeof val(active.index) === 'number' ? val(active.index) : 1;
  let list = Array.isArray(notes) ? unwrap(notes[idx]) : null;
  if (!Array.isArray(list)) {
    list = (Array.isArray(notes) ? notes : []).map(unwrap).find(
      row => Array.isArray(row) && row.some(x => x && (x.noteCard || x.note_card))) || [];
  }
  const queries = unwrap(u.noteQueries);
  const q = Array.isArray(queries) ? unwrap(queries[idx]) : null;
  const hasMore = q && typeof val(q.hasMore) === 'boolean' ? val(q.hasMore) : null;
  return JSON.stringify({ list, hasMore });
} catch (e) { return ''; } }"""

_SSR_DETAIL_JS = """(noteId) => { try {
  const st = window.__INITIAL_STATE__;
  const m = st && st.note && st.note.noteDetailMap;
  if (!m || !m[noteId]) return '';
  const entry = m[noteId];
  const note = entry.note || entry;
  return note ? JSON.stringify({ note }) : '';
} catch (e) { return ''; } }"""


class XhsBrowser:
    """Playwright persistent context on the local Chrome. `visible` for QR login;
    sync runs headed but moved off-screen (headless was unstable in the spike)."""

    def __init__(self, profile_dir: Path, visible: bool = False, log: Callable[[str], None] = print):
        self.profile_dir = Path(profile_dir)
        self.visible = visible
        self.log = log
        self._pw = self.context = self.list_page = self.detail_page = None
        self._ssr: dict = {"list": [], "hasMore": None}
        self._xhr_pages: list[list[dict]] = []
        self._xhr_has_more: bool | None = None
        self._api_error: str | None = None
        self._closed = False

    # -- lifecycle
    @safety.network
    def open(self) -> None:
        from playwright.sync_api import sync_playwright
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        args = launch_args(self.visible)
        kwargs = dict(user_data_dir=str(self.profile_dir), headless=False, args=args,
                      viewport={"width": 1366, "height": 900}, locale="zh-CN")
        self._pw = sync_playwright().start()
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
        # A restored about:blank page sometimes stays blank; use fresh pages.
        for old in list(self.context.pages):
            try:
                old.close()
            except Exception:
                pass
        self._closed = False
        self.context.on("close", lambda *_: setattr(self, "_closed", True))
        self.list_page = self.context.new_page()
        self.list_page.on("close", lambda *_: setattr(self, "_closed", True))
        self.list_page.on("response", self._on_list_response)
        self.detail_page = None  # created on first detail, then reused

    def is_closed(self) -> bool:
        """The user closed the window (or the browser went away)."""
        return self._closed or self.list_page is None or self.list_page.is_closed()

    def close(self) -> None:
        try:
            if self.context:
                self.context.close()
        finally:
            if self._pw:
                self._pw.stop()
            self._pw = self.context = None

    # -- helpers
    @safety.network
    def _goto_ready(self, page, url: str, tries: int = 3, min_len: int = 5000) -> bool:
        for i in range(1, tries + 1):
            safety.require()
            try:
                response = page.goto(url, wait_until="domcontentloaded", timeout=60000)
                if response is not None and response.status >= 400 and response.status != 404:
                    safety.trip('页面HTTP异常')
                    raise ReaderError('blocked',safety.MESSAGE)
                page.wait_for_timeout(3000)
                if page_kind(page.url) == 'captcha':
                    safety.trip('页面要求安全验证')
                    raise ReaderError('blocked',safety.MESSAGE)
                if page_kind(page.url) in ("login", "notfound"):
                    return True  # a real page, the caller inspects page.url
                html = page.content()
                visible = page.inner_text('body') if hasattr(page, 'inner_text') else html
                if risk_text(visible) or page_kind(page.url) == 'captcha':
                    safety.trip('页面安全警告或验证码')
                    raise ReaderError('blocked', safety.MESSAGE)
                if len(html) >= min_len and page.title():
                    return True
                safety.trip("空白或不完整页面")
                raise ReaderError("network", safety.MESSAGE)
            except ReaderError:
                raise
            except Exception as exc:
                safety.trip("页面访问异常")
                raise ReaderError("network", safety.MESSAGE) from None

        return False

    def _eval_json(self, page, js: str, arg=None) -> dict:
        try:
            raw = page.evaluate(js, arg) if arg is not None else page.evaluate(js)
            return json.loads(raw) if raw else {}
        except Exception:
            return {}

    @safety.network
    def _user_me(self) -> dict | None:
        try:
            resp=self.context.request.get(USER_ME_API,timeout=15000,headers={
                'User-Agent':UA,'Referer':'https://www.xiaohongshu.com/',
                'Origin':'https://www.xiaohongshu.com'})
            if resp.status in (401,403): raise ReaderError('needs_login','登录状态失效')
            if resp.status != 200:
                safety.trip('身份接口异常响应')
                raise ReaderError('blocked',safety.MESSAGE)
            body=resp.json()
            if not isinstance(body,dict): raise ValueError('unexpected shape')
            return body.get('data')or{}
        except ReaderError: raise
        except Exception:
            safety.trip('身份接口异常')
            raise ReaderError('network',safety.MESSAGE) from None

    def _identity(self, page) -> dict:
        ssr = self._eval_json(page, _VIEWER_JS)
        ident = judge_identity(ssr, None)
        if not ident["user_id"]:
            ident = judge_identity(ssr, self._user_me())
        return ident

    # -- login / identity
    def own_identity(self) -> dict:
        page = self.list_page
        self._goto_ready(page, EXPLORE_URL)
        ident = {}
        for _ in range(3):  # SSR may hydrate late
            ident = self._identity(page)
            if _UID.match(ident.get("user_id") or ""):
                return ident
            page.wait_for_timeout(2500)
        return ident

    # -- login window (visible only; runner.run_login drives the wait loop)
    def _show_window(self) -> None:
        """Force a normal, on-screen, front window even if the profile remembers an
        off-screen or minimized placement."""
        page = self.list_page
        try:
            cdp = self.context.new_cdp_session(page)
            wid = cdp.send("Browser.getWindowForTarget")["windowId"]
            cdp.send("Browser.setWindowBounds", {"windowId": wid, "bounds": {"windowState": "normal"}})
            cdp.send("Browser.setWindowBounds", {"windowId": wid, "bounds": dict(LOGIN_WINDOW)})
            cdp.detach()
        except Exception as exc:
            self.log(f"调整登录窗口位置失败（窗口仍以可见参数启动）：{clean(exc, 120)}")
        try:
            page.bring_to_front()
        except Exception:
            pass

    def show_login(self) -> None:
        self._show_window()
        self._goto_ready(self.list_page, EXPLORE_URL)
        self._show_window()

    def login_probe(self) -> dict:
        """Raw identity signals on the current page, for runner.run_login.
        -> {"ssr": {loggedIn, userId}, "me": /user/me data or None}"""
        nav = ""
        try:
            nav = self.list_page.evaluate(_NAV_ME_JS) or ""
        except Exception:
            pass
        return {"ssr": self._eval_json(self.list_page, _VIEWER_JS), "me": self._user_me(), "nav": nav}

    def verify_account(self, uid: str) -> dict:
        """Open uid's favorites page in the login window: it is that account only if the
        page says loggedIn and the viewer is uid (the spike found this page reliable,
        unlike explore). Goes back to explore on failure so the QR code stays usable."""
        state = self.open_favorites(uid)
        ssr = self._eval_json(self.list_page, _VIEWER_JS) if state == "ok" else {}
        viewer = str(ssr.get("userId") or "")
        ok = state == "ok" and ssr.get("loggedIn") is True and viewer in (uid, "")
        if not ok:
            self._goto_ready(self.list_page, EXPLORE_URL)
        return {"ok": ok, "state": state, "logged_in": ssr.get("loggedIn") is True,
                "viewer": "match" if viewer == uid else ("none" if not viewer else "other")}

    def pause(self, seconds: float) -> None:
        """Wait while still dispatching browser events (e.g. the window being closed)."""
        self.list_page.wait_for_timeout(seconds * 1000)

    # -- favorites list
    def _on_list_response(self, resp) -> None:
        if COLLECT_API not in resp.url:
            return
        try:
            body = resp.json()
        except Exception:
            body = None
        err = classify_api_error(resp.status, body)
        if err:
            if err in {"network","blocked","rate_limited"}: safety.trip("收藏接口异常响应")
            self._api_error = err
            return
        data = body.get("data") or {}
        notes = [n for n in (normalize_list_item(x) for x in data.get("notes") or data.get("note_list") or []) if n]
        self._xhr_pages.append(notes)
        if isinstance(data.get("has_more"), bool):
            self._xhr_has_more = data["has_more"]

    @timed("xhs", "list")
    @safety.network
    def open_favorites(self, uid: str) -> str:
        self._xhr_pages, self._xhr_has_more, self._api_error = [], None, None
        page = self.list_page
        if not self._goto_ready(page, FAVORITES_URL.format(uid=uid)):
            return "blank"
        kind = page_kind(page.url)
        if kind in ("captcha", "login"):
            return kind
        page.wait_for_timeout(2000)
        ssr = self._eval_json(page, _SSR_FAVS_JS)
        self._ssr = {"list": [n for n in (normalize_list_item(r) for r in ssr.get("list") or []) if n],
                     "hasMore": ssr.get("hasMore")}
        return "ok"

    def viewer(self) -> dict:
        ident = {}
        for _ in range(3):
            ident = self._identity(self.list_page)
            if ident.get("logged_in"):
                return ident
            self.list_page.wait_for_timeout(2500)
        return ident

    @timed("xhs", "list")
    def list_snapshot(self) -> dict:
        notes, seen = [], set()
        for n in [*self._ssr["list"], *(x for p in self._xhr_pages for x in p)]:
            if n["note_id"] not in seen:
                seen.add(n["note_id"])
                notes.append(n)
        has_more = self._xhr_has_more if self._xhr_pages else self._ssr.get("hasMore")
        return {"notes": notes, "has_more": has_more, "api_error": self._api_error}

    @timed("xhs", "list")
    @safety.network
    def scroll_list(self) -> None:
        page = self.list_page
        try:
            page.bring_to_front()  # background tabs may not fire the infinite-scroll observer
        except Exception:
            pass
        page.mouse.wheel(0, 2400)
        page.wait_for_timeout(1800)

    # -- detail + images
    @timed("xhs", "detail")
    @safety.network
    def fetch_detail(self, note_id: str, token: str) -> dict:
        if self.detail_page is None:
            self.detail_page = self.context.new_page()
        page = self.detail_page
        try:
            page.bring_to_front()
        except Exception:
            pass
        url = note_url(note_id) + (f"?xsec_token={token}&xsec_source=pc_collect" if token else "")
        payloads: list = []

        def on_response(resp):
            if FEED_API in resp.url:
                try:
                    payloads.append(resp.json())
                except Exception:
                    pass

        page.on("response", on_response)
        try:
            if not self._goto_ready(page, url, tries=2):
                return {"status": "failed", "error": "详情页空白/加载失败（已重试）"}
            deadline = time.time() + 20
            while True:
                kind = page_kind(page.url)
                if kind == "captcha": safety.trip("详情页安全验证")
                if kind in ("captcha", "login"):
                    return {"status": kind}
                if kind == "notfound":
                    return {"status": "failed", "error": "详情页跳转到 404（原因未知，不判定为删除）"}
                if any((normalize_note_detail((p or {}).get("data")) or {}).get("note_id") == note_id
                       for p in payloads):
                    break
                if self._eval_json(page, _SSR_DETAIL_JS, note_id) or time.time() > deadline:
                    break
                page.wait_for_timeout(500)
        finally:
            page.remove_listener("response", on_response)

        seen_ids = []
        for p in payloads:
            if classify_api_error(200,p) in ('blocked','rate_limited','network'):
                safety.trip('详情接口异常响应')
                raise ReaderError('blocked',safety.MESSAGE)
            if classify_api_error(200, p) in ("needs_login",):
                return {"status": "login"}
            d = normalize_note_detail((p or {}).get("data"))
            if d:
                seen_ids.append(d["note_id"])
                if d["note_id"] == note_id:
                    return {"status": "ok", "card": d["card"], "images": d["images"]}
        d = normalize_note_detail(self._eval_json(page, _SSR_DETAIL_JS, note_id))
        if d and d["note_id"] == note_id:
            return {"status": "ok", "card": d["card"], "images": d["images"]}
        if seen_ids or d:
            return {"status": "failed", "error": "详情 note_id 与请求不一致，已丢弃（防串帖）"}
        return {"status": "failed", "error": "未拿到详情数据"}

    @timed("xhs", "download")
    @safety.network
    def download(self, url: str) -> tuple[bytes, str]:
        try:
            resp=self.context.request.get(url,timeout=60000,headers={
                'Referer':'https://www.xiaohongshu.com/',
                'Accept':'image/webp,image/jpeg,image/png,image/*;q=0.8'})
            ctype=resp.headers.get('content-type','')
            if resp.status!=200 or 'image'not in ctype.lower():
                safety.trip('图片接口异常响应')
                raise ReaderError('blocked',safety.MESSAGE)
            return resp.body(),ctype
        except ReaderError: raise
        except Exception:
            safety.trip('图片接口异常')
            raise ReaderError('network',safety.MESSAGE) from None


# ---------------------------------------------------------------- reader

def read_account(profile_dir: Path) -> str:
    try:
        uid = json.loads((Path(profile_dir) / "account.json").read_text(encoding="utf-8")).get("user_id", "")
    except (OSError, ValueError, AttributeError):
        return ""
    return uid if _UID.match(uid or "") else ""


def write_account(profile_dir: Path, uid: str) -> None:
    """Only the public user id (to build the favorites URL). No cookies or tokens."""
    Path(profile_dir).mkdir(parents=True, exist_ok=True)
    (Path(profile_dir) / "account.json").write_text(json.dumps({"user_id": uid}), encoding="utf-8")


class XhsReader:
    """engine.Reader for xhs favorites. Use as a context manager: one browser per run."""

    platform = PLATFORM

    def __init__(self, browser: Browser, profile_dir: Path, staging_dir: Path, *,
                 is_complete: Callable[[str], bool] = lambda key: False,
                 delay: float = DETAIL_DELAY, sleep: Callable[[float], None] = time.sleep,
                 max_notes: int = MAX_NOTES):
        self.browser = browser
        self.profile_dir = Path(profile_dir)
        self.staging = Path(staging_dir)
        self.is_complete = is_complete
        self.delay, self.sleep, self.max_notes = delay, sleep, max_notes
        self.uid = ""
        self.restarted = False      # resume anchor was gone; walked again from the top
        self.details_fetched = 0
        self._first_index: int | None = None
        self._base = 0

    @property
    def account_key(self) -> str:
        """The logged-in user id; cursors/favorites are scoped by it."""
        return self.uid

    def __enter__(self):
        shutil.rmtree(self.staging, ignore_errors=True)
        self.staging.mkdir(parents=True, exist_ok=True)
        self.browser.open()
        return self

    def __exit__(self, *exc):
        try:
            self.browser.close()
        finally:
            shutil.rmtree(self.staging, ignore_errors=True)

    # -- auth
    def check_auth(self) -> str:
        try:
            return self._check_auth()
        except ReaderError:
            raise
        except Exception as exc:
            raise ReaderError("network", clean(f"check_auth: {type(exc).__name__}: {exc}"))

    def _check_auth(self) -> str:
        uid = read_account(self.profile_dir)
        if not uid:
            uid = self.browser.own_identity().get("user_id") or ""
            if not _UID.match(uid):
                return "needs_login"
            write_account(self.profile_dir, uid)
        state = self.browser.open_favorites(uid)
        if state == "captcha":
            return "blocked"
        if state == "login":
            return "needs_login"
        if state != "ok":
            raise ReaderError("network", "收藏页多次空白（已重试），不当作收藏为空")
        viewer = self.browser.viewer()
        viewer_id = viewer.get("user_id") or ""
        if viewer_id and viewer_id != uid:
            raise ReaderError("needs_login", "当前登录账号与记录的账号不一致")
        if viewer_id == uid or viewer.get("logged_in"):
            self.uid = uid
            return "ok"
        return "needs_login"

    # -- list
    def _ensure(self, n: int) -> dict:
        """Scroll until at least n notes are captured or the site says the list ended."""
        stagnant, last = 0, -1
        while True:
            snap = self.browser.list_snapshot()
            if snap.get("api_error"):
                raise ReaderError(snap["api_error"], "收藏列表接口返回异常")
            count = len(snap["notes"])
            if count >= n or snap.get("has_more") is False:
                return snap
            if count >= self.max_notes:
                raise ReaderError("limit", f"单次最多读取 {self.max_notes} 条收藏")
            stagnant = stagnant + 1 if count == last else 0
            if stagnant >= 3:
                raise ReaderError("network", "收藏列表停止增长但网站未表示已到末尾")
            last = count
            self.browser.scroll_list()

    def _locate(self, cursor: str) -> int:
        n = PAGE_SIZE
        while True:
            snap = self._ensure(n)
            ids = [x["note_id"] for x in snap["notes"]]
            if cursor in ids:
                return ids.index(cursor) + 1
            if snap.get("has_more") is False and len(ids) < n:
                self.restarted = True  # anchor gone: walk from the top (complete posts are skipped)
                return 0
            n = len(ids) + PAGE_SIZE

    # -- items
    def _fast_item(self, note: dict) -> dict:
        """Already complete locally: refresh seen-state only. None / 'unknown' / images=[]
        keep every stored field and post_images slot (store.py)."""
        return {"work_key": note["note_id"], "author_name": note.get("author") or None,
                "canonical_url": note_url(note["note_id"]), "media_type": "unknown", "images": []}

    def _download(self, note_id: str, idx: int, url: str) -> tuple[dict, str | None]:
        """Failed downloads keep idx + url (download_state=failed); store keeps the slot
        and records the image_download failure."""
        try:
            data, ctype = self.browser.download(url)
        except (ReaderError, safety.CoolingDown):
            raise
        except Exception as exc:
            return {"idx": idx, "url": url, "download_state": "failed"}, clean(exc, 120)
        path = self.staging / f"{note_id}_{idx:02d}{guess_ext(url, ctype)}"
        path.write_bytes(data)
        return {"idx": idx, "url": url, "local_path": str(path),
                "sha256": hashlib.sha256(data).hexdigest(), "download_state": "ok"}, None

    def _full_item(self, note: dict, items: list, failures: list) -> None:
        nid = note["note_id"]
        detail = self.browser.fetch_detail(nid, note.get("xsec_token") or "")
        self.details_fetched += 1
        status = detail.get("status")
        if status == "login":
            raise ReaderError("needs_login", "打开笔记详情时跳到登录页")
        if status == "captcha":
            raise ReaderError("blocked", "打开笔记详情时出现安全验证")
        if status != "ok":
            failures.append({"work_key": nid, "stage": "detail", "error": clean(detail.get("error"))})
            return
        card = detail["card"]
        ntype = str(pick(card, "type", default="") or note.get("type") or "")
        is_video = ntype == "video"
        raw_images = detail["images"]
        if is_video:  # cover only; the video itself is never fetched
            raw_images = raw_images[:1] or ([{"index": 0, "url": note["cover"]}] if note.get("cover") else [])
        images, missing = [], []
        for img in raw_images:
            idx = img["index"] + 1
            if not img["url"]:
                missing.append(idx)
                images.append({"idx": idx, "download_state": "failed"})
                continue
            ref, _err = self._download(nid, idx, img["url"])
            images.append(ref)
        user = unwrap(card.get("user") or {})
        items.append({
            "work_key": nid,
            "title": str(pick(card, "title", "displayTitle", default="") or note.get("title") or "") or None,
            "description": str(pick(card, "desc", default="") or "") or None,
            "author_name": str(pick(user, "nickname", "nickName", default="") or note.get("author") or "") or None,
            "canonical_url": note_url(nid),
            "media_type": "video" if is_video else "image",
            "images": images,
            "extra": {"type": ntype, "time": pick(card, "time", default=None)},
        })
        if missing:
            failures.append({"work_key": nid, "stage": "detail",
                             "error": f"图片 #{','.join(map(str, missing))} 没有地址"})
        if not raw_images:
            failures.append({"work_key": nid, "stage": "detail", "error": "详情里没有图片"})

    # -- page
    def fetch_page(self, collection_key: str, cursor: str | None, page_index: int) -> dict:
        if collection_key != COLLECTION_KEY:
            raise ValueError(f"unknown collection {collection_key}")
        try:
            return self._fetch_page(cursor, page_index)
        except ReaderError:
            raise
        except Exception as exc:  # browser died, navigation crashed ...
            raise ReaderError("network", clean(f"{type(exc).__name__}: {exc}"))

    def _fetch_page(self, cursor: str | None, page_index: int) -> dict:
        if self._first_index is None:
            self._first_index = page_index
            self._base = self._locate(cursor) if cursor else 0
        start = self._base + (page_index - self._first_index) * PAGE_SIZE
        snap = self._ensure(start + PAGE_SIZE + 1)  # one extra to know has_more
        notes = snap["notes"]
        chunk = notes[start:start + PAGE_SIZE]
        has_more = len(notes) > start + PAGE_SIZE

        items, failures = [], []
        for note in chunk:
            if self.is_complete(note["note_id"]):
                items.append(self._fast_item(note))
                continue
            self._full_item(note, items, failures)
            self.sleep(self.delay)
        prev = notes[start - 1]["note_id"] if start > 0 and start - 1 < len(notes) else None
        return {
            "contract_version": "sync-reader/1", "platform": PLATFORM,
            "collection": {"collection_key": COLLECTION_KEY},
            "page": {"index": page_index, "cursor_in": prev,
                     "cursor_out": chunk[-1]["note_id"] if chunk else cursor,
                     "has_more": has_more},
            "items": items, "failures": failures,
        }
