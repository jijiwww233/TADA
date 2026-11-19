"""短标题（v14）：12 个字以内、能独立成立的一句中文，不要半句话。

评审看到的问题：卡片标题是从描述里截出来的碎片（「插」「关于」「“MONSTER」），
所以标题改成先整句写、再单独存：
- 新图分析：视觉分析的同一次回答里多要一个 title 字段（vision.PROMPT），收到后走 normalize()；
- 已有图：scripts/backfill_titles.py 用千问文字模型（OpenAI 兼容 /chat/completions）重新生成，
  文本经过同一套校验。

生成结果写 analyses.title。人工标题单独存 image_titles_manual，自动生成的任何路径都不覆盖它
（和 post_classification_manual 同一个做法）。读取时人工优先。
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

import httpx

from . import config, db

MAX_CHARS = 12          # 生成标题的上限：12 个字
MAX_MANUAL_CHARS = 40   # 人工标题宽松一些，但仍是短标题
DEFAULT_MODEL = "qwen3.7-flash"

# 半句话的常见尾巴：这些字结尾说明话没说完
BAD_END = "的与和及在了是把被对就还也都很更最又而并且或但为从向"
# 描述性的开头，不适合当标题
BAD_HEAD = ("这是", "图中", "画面", "图为", "本图", "该图")
# 模型爱加的前缀与包裹符号
_PREFIX = re.compile(r"^\s*(?:短标题|标题)\s*[:：]\s*")
_WRAPPERS = [("「", "」"), ("『", "』"), ("“", "”"), ("‘", "’"), ('"', '"'), ("'", "'"), ("《", "》")]
_TRAILING = "。，、；：！？,.;:!?…"
_CJK = re.compile(r"[\u3400-\u9fff\uf900-\ufaff]")


class TitleError(Exception):
    """生成失败。消息只写原因，不含模型原文（可能带描述内容）。"""


@dataclass(frozen=True)
class TextSettings:
    base_url: str
    api_key: str = field(repr=False)
    model: str = DEFAULT_MODEL
    timeout: float = 30.0

    @classmethod
    def from_env(cls):
        ai = config.ai_settings()
        model = (ai.get("title_model") or os.environ.get("TITLE_MODEL") or DEFAULT_MODEL).strip()
        return cls(ai["base_url"], ai["api_key"], model)


def configured() -> bool:
    """有没有可用的文字模型端点（没有就跳过生成，不报错）。"""
    ai = config.ai_settings()
    return bool(ai["base_url"]) and bool(ai["api_key"])


def _strip(raw: str) -> str:
    text = raw.strip()
    text = _PREFIX.sub("", text)
    for left, right in _WRAPPERS:
        if len(text) >= 2 and text.startswith(left) and text.endswith(right):
            text = text[1:-1].strip()
    text = text.splitlines()[0].strip() if text else ""
    return text.rstrip(_TRAILING).strip()


def _length(text: str) -> int:
    return len(text.replace(" ", ""))


def validate(raw: str) -> str:
    """校验并返回标题；不合规抛 ValueError（消息是给人看的短句）。"""
    if not isinstance(raw, str):
        raise ValueError("标题应为字符串")
    text = raw.strip()
    # 截断记号在去尾标点之前判断：模型常把「…」当结尾，那说明它自己知道话没说完
    if "\n" in text or "…" in text or "..." in text or "--" in text:
        raise ValueError("标题含截断或换行")
    text = _strip(text)
    if not text:
        raise ValueError("标题为空")
    if _length(text) > MAX_CHARS:
        raise ValueError(f"标题超过 {MAX_CHARS} 字（{_length(text)} 字）")
    if _length(text) < 2:
        raise ValueError("标题太短")
    if not _CJK.search(text):
        raise ValueError("标题里没有中文")
    if text.startswith(BAD_HEAD):
        raise ValueError("标题是描述性开头，不像标题")
    if text[-1] in BAD_END:
        raise ValueError("标题以连接词收尾，是半句话")
    return text


def normalize(raw) -> str | None:
    """宽松版：接受模型返回的 JSON、字符串或 {"title": ...}；不合规返回 None。"""
    if isinstance(raw, dict):
        raw = raw.get("title")
    if isinstance(raw, str):
        try:
            return validate(raw)
        except ValueError:
            return None
    return None


SYSTEM_PROMPT = (
    "你是中文短标题编辑。用户给你一段图片的中文描述，你只回一个短标题，不要解释、不要引号、不要句号。"
    f"要求：{MAX_CHARS} 个汉字以内；是能独立成立的完整说法，不要半句话；"
    "不要以「的、与、和、及、在、了、是、把、被、对、为、从、向」结尾；"
    "不要用「这是」「图中」「画面」开头；不要照抄描述里的长句，也不要加书名号。"
)


def _request_text(description: str, previous: str | None) -> str:
    text = "图片描述：\n" + description.strip()[:800] + "\n\n只回标题本身（" + str(MAX_CHARS) + " 字以内）。"
    if previous:
        text += "\n上一次写的是「" + previous + "」，不合规，请更短、更完整。"
    return text


def generate(description: str, *, settings: TextSettings | None = None,
             client: httpx.Client | None = None) -> str:
    """用文字模型生成一个合规短标题；最多两次请求，仍不合规就抛 TitleError。"""
    description = (description or "").strip()
    if not description:
        raise TitleError("没有描述，无法生成标题")
    settings = settings or TextSettings.from_env()
    if not settings.base_url or not settings.api_key:
        raise TitleError("没有配置文字模型（AI_BASE_URL / AI_API_KEY）")
    owned = client is None
    client = client or httpx.Client(timeout=settings.timeout)
    previous = None
    try:
        for _ in range(2):
            payload = {
                "model": settings.model,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": _request_text(description, previous)},
                ],
                "temperature": 0.3,
                "enable_thinking": False,
            }
            response = client.post(settings.base_url + "/chat/completions", json=payload,
                                   headers={"Authorization": "Bearer " + settings.api_key})
            if response.status_code >= 400:
                raise TitleError(f"文字模型返回 HTTP {response.status_code}")
            try:
                content = response.json()["choices"][0]["message"]["content"]
            except (KeyError, IndexError, TypeError, ValueError):
                raise TitleError("文字模型返回的不是 chat/completions 结构") from None
            title = normalize(content)
            if title:
                return title
            previous = (content or "").strip()[:40] or None
        raise TitleError("文字模型两次都没有给出合规的短标题")
    finally:
        if owned:
            client.close()


# ---- 存储：人工标题优先，自动生成只写 analyses.title ----

def manual_title(image_id: int) -> str | None:
    conn = db.connect()
    try:
        row = conn.execute("SELECT title FROM image_titles_manual WHERE image_id = ?",
                           (image_id,)).fetchone()
    finally:
        conn.close()
    return row["title"] if row else None


def set_manual(image_id: int, title: str | None) -> str | None:
    """写/清人工标题（None 表示恢复自动标题）。返回存下的人工标题。"""
    conn = db.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            if conn.execute("SELECT 1 FROM images WHERE id = ?", (image_id,)).fetchone() is None:
                raise KeyError(image_id)
            if title is None:
                conn.execute("DELETE FROM image_titles_manual WHERE image_id = ?", (image_id,))
            else:
                value = validate_manual(title)
                conn.execute(
                    """INSERT INTO image_titles_manual (image_id, title, updated_at)
                       VALUES (?, ?, datetime('now'))
                       ON CONFLICT(image_id) DO UPDATE SET
                           title = excluded.title, updated_at = datetime('now')""",
                    (image_id, value))
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()
    return manual_title(image_id)


def validate_manual(raw: str) -> str:
    if not isinstance(raw, str):
        raise ValueError("标题应为字符串")
    text = raw.strip()
    if not text:
        raise ValueError("标题为空")
    if len(text) > MAX_MANUAL_CHARS:
        raise ValueError(f"标题最多 {MAX_MANUAL_CHARS} 字")
    return text


def store_generated(image_id: int, title: str, *, model: str | None = None) -> bool:
    """把自动标题写进 analyses.title；有人工标题时不动（返回 False）。"""
    conn = db.connect()
    try:
        if conn.execute("SELECT 1 FROM image_titles_manual WHERE image_id = ?",
                        (image_id,)).fetchone() is not None:
            return False
        cur = conn.execute("UPDATE analyses SET title = ? WHERE image_id = ?", (title, image_id))
        return cur.rowcount == 1
    finally:
        conn.close()


def effective_titles(image_ids=None) -> dict[int, str]:
    """人工优先，其次自动；只返回有值的。"""
    conn = db.connect()
    try:
        rows = conn.execute(
            """SELECT i.id AS image_id,
                      COALESCE(m.title, a.title) AS title
               FROM images i
               LEFT JOIN analyses a ON a.image_id = i.id
               LEFT JOIN image_titles_manual m ON m.image_id = i.id
               WHERE COALESCE(m.title, a.title) IS NOT NULL""").fetchall()
    finally:
        conn.close()
    wanted = None if image_ids is None else set(image_ids)
    return {row["image_id"]: row["title"] for row in rows
            if wanted is None or row["image_id"] in wanted}


def effective_title(image_id: int) -> str | None:
    return effective_titles([image_id]).get(image_id)
