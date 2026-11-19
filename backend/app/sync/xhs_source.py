"""Resolve a usable XHS note entry by re-reading the logged-in favorites list.

Why: posts.url stores the token-free canonical link; XHS web shows "当前笔记暂时无法
浏览" for it. The favorites list API returns a short-lived xsec_token per note, so we
scan the list again, fetch the detail with that token and verify the title.

The token only lives in this process' memory. It is never written to the database,
logs or docs; the only place it appears is the redirect URL sent to the browser that
asked to open the note.
"""
from __future__ import annotations

import re
from pathlib import Path

from .readers import xhs
from . import safety

MAX_SCAN_NOTES = 400
_WS = re.compile(r"\s+")


class SourceUnavailable(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _norm(value: str | None) -> str:
    text = _WS.sub("", value or "")
    return text.rstrip("…....")


def _same_title(actual: str | None, expected: str | None) -> bool:
    a, b = _norm(actual), _norm(expected)
    if not b:
        return True
    return bool(a) and (a == b or a.startswith(b) or b.startswith(a))


def classify_probe(title: str | None, body: str | None, url: str | None) -> str:
    """Pure classification of a probed page (no I/O, no cooldown side effect).

    'blocked' = XHS risk / captcha / 安全限制 page, 'blank' = unusable empty page,
    'normal' = a page that can be matched against the expected title.
    """
    text = f"{title or ''}\n{body or ''}"
    if xhs.page_kind(url or "") == "captcha" or xhs.risk_text(text) or "暂时无法浏览" in text:
        return "blocked"
    if not (body or "").strip():
        return "blank"
    return "normal"


def probe_open(url: str, expected_title: str = "", timeout_s: float = 20.0) -> bool:
    """Open the resolved URL in a clean browser once and decide whether it is usable.

    A risk/captcha page now trips the shared cooldown (so the UI retry cannot probe
    again immediately); ordinary network failures, timeouts and blank pages return
    False without tripping it, so a transient problem does not pause all syncing.
    """
    if safety.state()["blocked"]: return False
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return True  # cannot probe: keep the old redirect behaviour
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel="chrome", headless=True)
            try:
                page = browser.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=int(timeout_s * 1000))
                page.wait_for_timeout(5000)
                page_url = page.url
                body = page.inner_text("body")
                title = page.title()
            finally:
                browser.close()
    except Exception:
        return False
    kind = classify_probe(title, body, page_url)
    if kind == "blocked":
        safety.trip("原帖探测遇到安全限制")
        return False
    if kind == "blank":
        return False
    expected = _norm(expected_title)
    return bool(expected) and expected in _norm(f"{title}\n{body}")


def resolve_note(profile_dir: Path, note_id: str, expected_title: str = "",
                 max_notes: int = MAX_SCAN_NOTES) -> dict:
    """-> {"ok": True, "url", "title"} or {"ok": False, "reason"}.

    The URL contains a token; callers must not log it or persist it.
    """
    if safety.state()["blocked"]: return {"ok":False,"reason":safety.MESSAGE}
    browser = xhs.XhsBrowser(Path(profile_dir), visible=False)
    try:
        try:
            browser.open()
        except Exception as exc:
            raise SourceUnavailable(f"无法打开同步浏览器：{type(exc).__name__}") from exc
        uid = xhs.read_account(Path(profile_dir))
        if not uid:
            uid = (browser.own_identity() or {}).get("user_id") or ""
        if not uid:
            return {"ok": False, "reason": "登录状态不可用，请先登录小红书"}
        state = browser.open_favorites(uid)
        if state == "login":
            return {"ok": False, "reason": "登录已失效，请重新登录小红书"}
        if state == "captcha":
            return {"ok": False, "reason": "小红书要求安全验证，请稍后再试"}
        if state != "ok":
            return {"ok": False, "reason": "收藏页暂时无法打开，请稍后再试"}
        token = ""
        while True:
            snap = browser.list_snapshot()
            if snap.get("api_error"):
                return {"ok": False, "reason": "收藏列表暂时不可用，请稍后再试"}
            for note in snap.get("notes") or []:
                if note.get("note_id") == note_id:
                    token = str(note.get("xsec_token") or "")
                    break
            if token:
                break
            if snap.get("has_more") is False or len(snap.get("notes") or []) >= max_notes:
                break
            browser.scroll_list()
        if not token:
            return {"ok": False, "reason": "当前账号的收藏列表里找不到这篇笔记（可能已取消收藏）"}
        detail = browser.fetch_detail(note_id, token)
        if detail.get("status") == "login":
            return {"ok": False, "reason": "登录已失效，请重新登录小红书"}
        if detail.get("status") == "captcha":
            return {"ok": False, "reason": "小红书要求安全验证，请稍后再试"}
        if detail.get("status") != "ok":
            return {"ok": False, "reason": "笔记详情暂时无法读取，请稍后再试"}
        card = detail.get("card") or {}
        title = str(xhs.pick(card, "title", "displayTitle", default="") or "")
        if not _same_title(title, expected_title):
            return {"ok": False, "reason": "笔记标题与库里记录不一致，已停止打开（避免打开错帖）"}
        url = xhs.note_url(note_id) + f"?xsec_token={token}&xsec_source=pc_collect"
        return {"ok": True, "url": url, "title": title}
    finally:
        try:
            browser.close()
        except Exception:
            pass