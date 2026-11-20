"""Anonymous usage events and the private /stats page.

Events are deliberately small: a type, an optional page name and a JSON detail bag.
Image contents and free text other than the search query are never collected.  The
sender's IP is not persisted; the in-memory rate limiter only keeps device ids.
"""
from __future__ import annotations

from collections import Counter, defaultdict, deque
from contextlib import closing
from datetime import datetime
import html
import json
import re
import sqlite3
import threading
import time
from typing import Any

from . import db

# One device may send at most LIMIT events per WINDOW seconds.  Excess events are
# silently dropped by the request handler, as agreed with the client.
LIMIT = 120
WINDOW = 60
MAX_BATCH = 200
MAX_DETAIL = 2048
DEVICE_RE = re.compile(r"[A-Za-z0-9._-]{6,64}")

# Only these event types are stored; anything else is dropped without an error.
EVENT_TYPES = {
    "app_open",
    "page_view",
    "search",
    "search_open",
    "detail_open",
    "detail_swipe",
    "similar",
    "add_project",
    "color_find",
    "filter_open",
    "filter_change",
    "import_open",
    "import",
    "intro_step",
    "intro_skip",
    "intro_enter",
    "pending_keep",
    "pending_reject",
    "leave",
}

PAGE_LABELS = {
    "library": "素材库",
    "projects": "项目",
    "activity": "日历",
    "settings": "设置",
    "pending": "待确认",
    "intro": "引导页",
    "home": "首页",
}

TYPE_LABELS = {
    "app_open": "打开网站",
    "page_view": "切换页面",
    "search": "搜索",
    "search_open": "点开搜索结果",
    "detail_open": "打开详情",
    "detail_swipe": "详情左右滑",
    "similar": "找相似",
    "add_project": "加入项目",
    "color_find": "按颜色找",
    "filter_open": "打开筛选",
    "filter_change": "改筛选",
    "import_open": "打开导入面板",
    "import": "导入",
    "intro_step": "引导页到达",
    "intro_skip": "引导页跳过",
    "intro_enter": "进入素材库",
    "pending_keep": "待确认·保留",
    "pending_reject": "待确认·不是参考",
    "leave": "离开页面",
}

_import_labels = {"picker": "选图片", "shortcut": "快捷指令", "tutu": "图图", "link": "链接导入"}

_lock = threading.Lock()
_recent: dict[str, deque[float]] = {}


def _device_ok(device_id: Any) -> bool:
    return isinstance(device_id, str) and bool(DEVICE_RE.fullmatch(device_id))


def device_ok(device_id: Any) -> bool:
    """Public check shared with the stats API."""
    return _device_ok(device_id)


def _clean_detail(value: Any) -> dict[str, Any] | None:
    """Keep small primitives only; drop the bag when it is unreasonably large."""
    if not isinstance(value, dict):
        return None
    kept: dict[str, Any] = {}
    for key, item in list(value.items())[:20]:
        if not isinstance(key, str) or len(key) > 40:
            continue
        if isinstance(item, str):
            kept[key] = item[:80]
        elif isinstance(item, bool) or isinstance(item, (int, float)):
            kept[key] = item
    if not kept:
        return None
    raw = json.dumps(kept, ensure_ascii=False, separators=(",", ":"))
    return kept if len(raw) <= MAX_DETAIL else None


def _clean_event(event: Any) -> tuple[str, str | None, dict[str, Any] | None] | None:
    if not isinstance(event, dict):
        return None
    event_type = event.get("type")
    if event_type not in EVENT_TYPES:
        return None
    page = event.get("page")
    page = page[:40] if isinstance(page, str) and page.strip() else None
    return event_type, page, _clean_detail(event.get("detail"))


def _take_quota(device_id: str, count: int) -> int:
    """Reserve up to `count` slots for this device; return how many were allowed."""
    now = time.monotonic()
    with _lock:
        bucket = _recent.setdefault(device_id, deque())
        while bucket and now - bucket[0] >= WINDOW:
            bucket.popleft()
        allowed = max(0, LIMIT - len(bucket))
        allowed = min(allowed, count)
        bucket.extend([now] * allowed)
        return allowed


def record(device_id: Any, events: Any) -> int:
    """Store the allowed events; always returns a count and never raises to the caller."""
    if not _device_ok(device_id) or not isinstance(events, list) or not events:
        return 0
    cleaned = [item for item in (_clean_event(event) for event in events[:MAX_BATCH]) if item]
    if not cleaned:
        return 0
    allowed = _take_quota(device_id, len(cleaned))
    if allowed <= 0:
        return 0
    rows = [(device_id, event_type, page,
             json.dumps(detail, ensure_ascii=False, separators=(",", ":")) if detail else None)
            for event_type, page, detail in cleaned[:allowed]]
    try:
        with closing(db.connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.executemany(
                    "INSERT INTO usage_events (device_id, event_type, page, detail) VALUES (?, ?, ?, ?)",
                    rows,
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
    except sqlite3.Error:
        return 0
    return len(rows)


# ---------------------------------------------------------------------------
# Devices excluded from stats ("this is me")


def excluded_devices() -> set[str]:
    with closing(db.connect()) as conn:
        return {row[0] for row in conn.execute("SELECT device_id FROM usage_excluded_devices")}


def is_excluded(device_id: str) -> bool:
    if not _device_ok(device_id):
        return False
    with closing(db.connect()) as conn:
        return bool(conn.execute(
            "SELECT 1 FROM usage_excluded_devices WHERE device_id = ?", (device_id,)).fetchone())


def set_excluded(device_id: str, excluded: bool) -> bool:
    """Remember / forget one device; returns the resulting state (False on bad input)."""
    if not _device_ok(device_id):
        return False
    with closing(db.connect()) as conn:
        if excluded:
            conn.execute(
                "INSERT INTO usage_excluded_devices (device_id) VALUES (?) ON CONFLICT(device_id) DO NOTHING",
                (device_id,))
        else:
            conn.execute("DELETE FROM usage_excluded_devices WHERE device_id = ?", (device_id,))
    return excluded


def _my_devices() -> list[sqlite3.Row]:
    with closing(db.connect()) as conn:
        return conn.execute(
            """SELECT d.device_id,
                      datetime(d.created_at, 'localtime') AS marked_at,
                      datetime(MAX(e.created_at), 'localtime') AS last_at
               FROM usage_excluded_devices d
               LEFT JOIN usage_events e ON e.device_id = d.device_id
               GROUP BY d.device_id ORDER BY d.created_at DESC"""
        ).fetchall()


# ---------------------------------------------------------------------------
# /stats rendering


def _load_events(days: int) -> list[sqlite3.Row]:
    with closing(db.connect()) as conn:
        return conn.execute(
            """SELECT device_id, event_type, page, detail,
                      datetime(created_at, 'localtime') AS at
               FROM usage_events
               WHERE datetime(created_at) >= datetime('now', ?)
               ORDER BY id""",
            (f"-{days} days",),
        ).fetchall()


def _load_recent(limit: int = 50) -> list[sqlite3.Row]:
    with closing(db.connect()) as conn:
        return conn.execute(
            """SELECT device_id, event_type, page, detail,
                      datetime(created_at, 'localtime') AS at
               FROM usage_events ORDER BY id DESC LIMIT ?""",
            (limit,),
        ).fetchall()


def _detail(row: sqlite3.Row) -> dict[str, Any]:
    try:
        value = json.loads(row["detail"] or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _page_name(page: str | None) -> str:
    if not page:
        return "—"
    if page.startswith("intro"):
        return "引导页"
    return PAGE_LABELS.get(page, page)


def _type_name(event_type: str) -> str:
    return TYPE_LABELS.get(event_type, event_type)


def _summary(row: sqlite3.Row) -> str:
    detail = _detail(row)
    event_type = row["event_type"]
    if event_type in {"search", "search_open"}:
        parts = [f"「{detail.get('q', '')}」"]
        if event_type == "search":
            parts.append(f"{detail.get('mode', '')}")
            parts.append(f"像 {detail.get('strong', 0)} · 不太像 {detail.get('weak', 0)}")
            if detail.get("none"):
                parts.append("没找到很像的")
        return " · ".join(part for part in parts if part)
    if event_type == "import":
        return _import_labels.get(str(detail.get("method")), str(detail.get("method", "")))
    if event_type == "filter_change":
        return f"{detail.get('kind', '')}: {detail.get('value', '')}"
    if event_type.startswith("intro"):
        page = detail.get("page")
        return f"第 {page} 页" if isinstance(page, int) else ""
    if event_type == "leave":
        dwell = detail.get("dwell")
        return f"停留 {dwell} 秒" if isinstance(dwell, (int, float)) else ""
    if event_type == "detail_swipe":
        return "上一张" if detail.get("dir") == -1 else "下一张"
    return ""


def _bar(value: int, total: int) -> str:
    width = 0 if total <= 0 else max(2, round(value / total * 100))
    return f'<span class="bar"><i style="width:{width}%"></i></span>'


def _table(headers: list[str], rows: list[list[str]], empty: str = "还没有记录") -> str:
    if not rows:
        return f'<p class="empty">{html.escape(empty)}</p>'
    head = "".join(f"<th>{html.escape(cell)}</th>" for cell in headers)
    body = "".join("<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>" for row in rows)
    return f'<div class="scroll"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def render_stats() -> str:
    excluded = excluded_devices()
    events = [row for row in _load_events(7) if row["device_id"] not in excluded]
    today = datetime.now().date().isoformat()
    today_events = [row for row in events if (row["at"] or "")[:10] == today]
    recent = [row for row in _load_recent(200) if row["device_id"] not in excluded][:50]
    mine = [row for row in _my_devices()]

    def visits(rows: list[sqlite3.Row]) -> tuple[int, int]:
        opens = sum(1 for row in rows if row["event_type"] == "app_open")
        if opens == 0:
            opens = sum(1 for row in rows if row["event_type"] == "page_view")
        return len({row["device_id"] for row in rows}), opens

    today_people, today_opens = visits(today_events)
    week_people, week_opens = visits(events)

    searches = [row for row in events if row["event_type"] == "search"]
    opens_by_query: Counter[str] = Counter(
        str(_detail(row).get("q", "")) for row in events if row["event_type"] == "search_open"
    )
    search_agg: dict[str, dict[str, float]] = {}
    for row in searches:
        detail = _detail(row)
        q = str(detail.get("q") or "").strip()[:80]
        if not q:
            continue
        item = search_agg.setdefault(q, {"n": 0, "results": 0, "none": 0})
        item["n"] += 1
        item["results"] += int(detail.get("strong") or 0) + int(detail.get("weak") or 0)
        if detail.get("none"):
            item["none"] += 1
    top_search = sorted(search_agg.items(), key=lambda pair: -pair[1]["n"])[:20]
    search_rows = [
        [html.escape(q), str(int(item["n"])), f'{item["results"] / item["n"]:.1f}',
         str(opens_by_query.get(q, 0))]
        for q, item in top_search
    ]
    no_result = sorted(((q, int(item["none"])) for q, item in search_agg.items() if item["none"]),
                       key=lambda pair: -pair[1])[:20]
    no_result_rows = [[html.escape(q), str(n)] for q, n in no_result]

    page_counts = Counter(_page_name(row["page"]) for row in events if row["event_type"] == "page_view")
    max_page = max(page_counts.values(), default=0)
    page_rows = [[html.escape(name), str(count), _bar(count, max_page)]
                 for name, count in page_counts.most_common()]

    exits = Counter(_page_name(row["page"]) for row in events if row["event_type"] == "leave")
    if not exits:
        last_by_device: dict[tuple[str, str], sqlite3.Row] = {}
        for row in events:
            last_by_device[(row["device_id"], (row["at"] or "")[:10])] = row
        exits = Counter(_page_name(row["page"]) for row in last_by_device.values())
    max_exit = max(exits.values(), default=0)
    exit_rows = [[html.escape(name), str(count), _bar(count, max_exit)]
                 for name, count in exits.most_common(10)]

    imports = Counter(str(_detail(row).get("method") or "unknown")
                      for row in events if row["event_type"] == "import")
    import_rows = [[html.escape(_import_labels.get(key, "未标记")), str(count)]
                   for key, count in imports.most_common()]

    intro_pages: dict[int, set[str]] = defaultdict(set)
    for row in events:
        if row["event_type"] != "intro_step":
            continue
        page = _detail(row).get("page")
        if isinstance(page, int):
            intro_pages[page].add(row["device_id"])
    max_intro = max((len(v) for v in intro_pages.values()), default=0)
    intro_rows = [[f"第 {page} 页", str(len(devices)), _bar(len(devices), max_intro)]
                  for page, devices in sorted(intro_pages.items())]
    intro_skip = sum(1 for row in events if row["event_type"] == "intro_skip")
    intro_enter = sum(1 for row in events if row["event_type"] == "intro_enter")

    timeline: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for row in recent:
        timeline[row["device_id"]].append(row)

    timeline_html = []
    for device, rows in timeline.items():
        items = []
        for row in reversed(rows):
            summary = _summary(row)
            detail = f'<span class="ev-detail">{html.escape(summary)}</span>' if summary else ""
            items.append(
                f'<li><time>{html.escape((row["at"] or "")[11:19])}</time>'
                f'<span class="ev-type">{html.escape(_type_name(row["event_type"]))}</span>'
                f'<span class="ev-page">{html.escape(_page_name(row["page"]))}</span>{detail}</li>'
            )
        timeline_html.append(
            f'<section class="device"><h3>设备 {html.escape(device[:8])}</h3><ol>{"".join(items)}</ol></section>'
        )
    if not timeline_html:
        timeline_html.append('<p class="empty">还没有事件</p>')

    mine_rows = "".join(
        f'<tr><td>{html.escape(row["device_id"][:6])}…</td>'
        f'<td>{html.escape(row["marked_at"] or "—")}</td>'
        f'<td>{html.escape(row["last_at"] or "还没有记录")}</td>'
        f'<td><button type="button" class="me-cancel" data-cancel-device="{html.escape(row["device_id"])}">取消</button></td></tr>'
        for row in mine
    )
    mine_html = ('<div class="scroll"><table><thead><tr><th>设备</th><th>标记时间</th>'
                 '<th>最近访问</th><th></th></tr></thead><tbody>' + mine_rows +
                 '</tbody></table></div>') if mine_rows else '<p class="empty">还没有标记「我的设备」</p>'

    script = """<script>
(function () {
  var key = new URLSearchParams(location.search).get('key') || '';
  var DEVICE = 'tada-device-id', OFF = 'tada-telemetry-off';
  var statusEl = document.getElementById('me-state');
  var button = document.getElementById('me-flag');
  var device = '';
  try { device = localStorage.getItem(DEVICE) || ''; } catch (e) {}
  function show(excluded) {
    statusEl.textContent = excluded ? '这台设备：已排除' : '这台设备：正在记录';
    statusEl.className = 'me-state' + (excluded ? ' is-off' : '');
    button.disabled = !!excluded;
  }
  if (!device) {
    statusEl.textContent = '这台浏览器还没有设备编号';
    button.disabled = true;
  } else {
    fetch('/api/stats/device?key=' + encodeURIComponent(key) + '&device_id=' + encodeURIComponent(device))
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) { if (data) show(!!data.excluded); else statusEl.textContent = '状态读取失败'; })
      .catch(function () { statusEl.textContent = '状态读取失败'; });
    button.onclick = function () {
      button.disabled = true;
      fetch('/api/stats/devices', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ key: key, device_id: device, action: 'exclude' })
      }).then(function (r) { return r.ok ? r.json() : null; }).then(function (data) {
        if (data && data.ok) { try { localStorage.setItem(OFF, '1'); } catch (e) {} show(true); }
        else { button.disabled = false; statusEl.textContent = '没登记上，稍后再试'; }
      }).catch(function () { button.disabled = false; statusEl.textContent = '没登记上，稍后再试'; });
    };
  }
  document.querySelectorAll('button[data-cancel-device]').forEach(function (el) {
    el.onclick = function () {
      el.disabled = true;
      fetch('/api/stats/devices', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ key: key, device_id: el.getAttribute('data-cancel-device'), action: 'include' })
      }).then(function (r) { return r.ok ? r.json() : null; }).then(function (data) {
        if (data && data.ok) location.reload(); else el.disabled = false;
      }).catch(function () { el.disabled = false; });
    };
  });
})();
</script>"""

    generated = datetime.now().strftime("%Y-%m-%d %H:%M")
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>TADA 使用记录</title>
<style>
  :root {{ --paper:#faf8f2; --card:#fffdf8; --ink:#2f2a24; --muted:#7c7064; --line:#e7dfd1; --clay:#b4552d; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; padding:24px 16px 64px; background:var(--paper); color:var(--ink);
         font:15px/1.7 -apple-system,BlinkMacSystemFont,"PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif; }}
  main {{ max-width:860px; margin:0 auto; }}
  h1 {{ font-family:Georgia,"Songti SC","Noto Serif SC",serif; font-weight:500; font-size:30px; margin:0 0 4px; letter-spacing:.02em; }}
  .sub {{ color:var(--muted); font-size:13px; margin:0 0 28px; }}
  h2 {{ font-family:Georgia,"Songti SC","Noto Serif SC",serif; font-weight:500; font-size:20px; margin:36px 0 12px;
        padding-bottom:8px; border-bottom:1px solid var(--line); }}
  .cards {{ display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:12px; }}
  .card {{ background:var(--card); border:1px solid var(--line); border-radius:12px; padding:14px 16px; }}
  .card b {{ display:block; font-family:Georgia,"Songti SC",serif; font-size:30px; font-weight:500; line-height:1.2; }}
  .card span {{ color:var(--muted); font-size:13px; }}
  .scroll {{ overflow-x:auto; }}
  table {{ width:100%; border-collapse:collapse; background:var(--card); border:1px solid var(--line);
           border-radius:12px; overflow:hidden; font-size:14px; }}
  th,td {{ text-align:left; padding:9px 12px; border-bottom:1px solid var(--line); white-space:nowrap; }}
  th {{ color:var(--muted); font-weight:500; font-size:13px; }}
  tr:last-child td {{ border-bottom:0; }}
  .bar {{ display:block; width:120px; height:6px; border-radius:99px; background:#efe8db; overflow:hidden; }}
  .bar i {{ display:block; height:100%; background:var(--clay); border-radius:99px; }}
  .empty {{ color:var(--muted); font-size:14px; }}
  .note {{ color:var(--muted); font-size:13px; }}
  .device {{ background:var(--card); border:1px solid var(--line); border-radius:12px; padding:12px 16px; margin:12px 0; }}
  .device h3 {{ margin:0 0 8px; font-size:14px; font-weight:500; color:var(--muted); }}
  .device ol {{ margin:0; padding-left:0; list-style:none; }}
  .device li {{ display:flex; flex-wrap:wrap; gap:4px 10px; padding:6px 0; border-top:1px dashed var(--line); font-size:13px; }}
  .device li:first-child {{ border-top:0; }}
  time {{ color:var(--muted); font-variant-numeric:tabular-nums; }}
  .ev-type {{ font-weight:500; }}
  .ev-page {{ color:var(--muted); }}
  .ev-detail {{ flex-basis:100%; color:var(--muted); }}
  .me-bar {{ display:flex; flex-wrap:wrap; align-items:center; gap:10px; margin:0 0 8px; }}
  .me-btn, .me-cancel {{ font:inherit; font-size:13px; padding:7px 12px; border-radius:999px;
    border:1px solid var(--line); background:var(--card); color:var(--ink); cursor:pointer; }}
  .me-btn:disabled {{ opacity:.45; cursor:default; }}
  .me-cancel {{ padding:4px 10px; }}
  .me-state {{ color:var(--muted); font-size:13px; }}
  .me-state.is-off {{ color:var(--clay); }}
  @media (max-width:520px) {{
    body {{ padding:18px 12px 48px; }}
    h1 {{ font-size:24px; }}
    .cards {{ gap:8px; }}
    .card b {{ font-size:24px; }}
    .bar {{ width:80px; }}
  }}
</style>
</head>
<body>
<main>
  <h1>TADA 使用记录</h1>
  <p class="sub">匿名统计 · 生成于 {generated} · 不记录 IP 和图片内容</p>

  <div class="me-bar">
    <button type="button" id="me-flag" class="me-btn">这台设备是我，别记了</button>
    <span id="me-state" class="me-state">正在确认…</span>
  </div>

  <h2>概览</h2>
  <div class="cards">
    <div class="card"><b>{today_people}</b><span>今天访问人数</span></div>
    <div class="card"><b>{today_opens}</b><span>今天访问次数</span></div>
    <div class="card"><b>{week_people}</b><span>近 7 天访问人数</span></div>
    <div class="card"><b>{week_opens}</b><span>近 7 天访问次数</span></div>
  </div>

  <h2>最常搜的词</h2>
  {_table(["词", "搜索次数", "平均出几张", "点开结果"], search_rows)}

  <h2>搜了但没找到很像的</h2>
  {_table(["词", "次数"], no_result_rows, "还没有「没找到很像」的记录")}

  <h2>各页面访问次数</h2>
  {_table(["页面", "次数", ""], page_rows)}

  <h2>大家最常在哪个页面离开</h2>
  {_table(["页面", "次数", ""], exit_rows)}

  <h2>导入方式</h2>
  {_table(["方式", "次数"], import_rows)}

  <h2>引导页每一页的到达人数</h2>
  {_table(["页面", "人数", ""], intro_rows)}
  <p class="note">跳过 {intro_skip} 次 · 点「进入素材库」{intro_enter} 次</p>

  <h2>最近 50 条事件</h2>
  {"".join(timeline_html)}

  <h2>我的设备</h2>
  {mine_html}
  <p class="note">这些设备的事件不计入上面的任何数字。</p>
</main>
{script}
</body>
</html>
"""
