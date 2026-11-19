"""User-editable annotation overlay on top of AI analysis (schema v11).

One row per image, field by field: NULL means "no user override" and the caller falls
back to the current AI analysis; "" / [] mean the user explicitly cleared that field.
Omitted arguments to save() keep the stored value, so editing only tags never copies
the AI description into the overlay. This module never rewrites analyses or the
deterministic embedding text (text_hash stays valid); its search channel reads the
local SQLite database only and makes no paid embedding/vision call.
"""
import json
import re

from . import db

MAX_DESCRIPTION = 2000
MAX_TAGS = 20
MAX_TAG_LENGTH = 60
SPLIT_RE = re.compile(r"[\s\W_]+", re.UNICODE)
UNSET = object()  # public sentinel: "do not change this field"

_CJK_RANGES = (
    (0x3400, 0x4DBF),    # CJK Extension A
    (0x4E00, 0x9FFF),    # CJK Unified Ideographs
    (0xF900, 0xFAFF),    # compatibility ideographs
    (0x20000, 0x2A6DF),  # Extension B
    (0x2A700, 0x2B73F),
    (0x2B740, 0x2B81F),
    (0x2B820, 0x2CEAF),
    (0x2CEB0, 0x2EBEF),
    (0x30000, 0x3134F),
)


def _normalize_description(value) -> str:
    """A user description: stripped, may be empty (explicit clear), <=2000 chars."""
    if not isinstance(value, str):
        raise ValueError("description 必须是字符串或 null")
    value = value.strip()
    if len(value) > MAX_DESCRIPTION:
        raise ValueError(f"description 最多 {MAX_DESCRIPTION} 字")
    return value


def _normalize_tags(value) -> list[str]:
    """A user tag list: stripped, empty items dropped, deduped order kept, <=20 tags."""
    if not isinstance(value, list):
        raise ValueError("tags 必须是字符串数组或 null")
    tags: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str):
            raise ValueError("tags 每项必须是字符串")
        item = item.strip()
        if not item:
            continue
        if len(item) > MAX_TAG_LENGTH:
            raise ValueError(f"tags 每项最多 {MAX_TAG_LENGTH} 字")
        if item in seen:
            continue
        seen.add(item)
        tags.append(item)
    if len(tags) > MAX_TAGS:
        raise ValueError(f"tags 最多 {MAX_TAGS} 个")
    return tags


def _decode_tags(raw) -> list[str]:
    if raw is None:
        return []
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return []
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


def get(image_id: int) -> dict | None:
    """The user annotation for an image, or None when the AI content is in effect.

    description/tags None means that field has no user override (fall back to AI);
    "" / [] means the user explicitly cleared it.
    """
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT description, tags_json, edited_at FROM image_annotations WHERE image_id = ?",
            (image_id,)).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    tags = None if row["tags_json"] is None else _decode_tags(row["tags_json"])
    return {"description": row["description"], "tags": tags,
            "edited": True, "edited_at": row["edited_at"]}


def save(image_id: int, description=UNSET, tags=UNSET) -> dict | None:
    """Field-level upsert. Omitted fields are unchanged; None clears one field.

    Both fields end up None -> delete the row (the AI analysis is shown again).
    """
    description_given = description is not UNSET
    tags_given = tags is not UNSET
    if description_given and description is not None:
        description = _normalize_description(description)
    if tags_given and tags is not None:
        tags = _normalize_tags(tags)
    conn = db.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            if conn.execute("SELECT 1 FROM images WHERE id = ?", (image_id,)).fetchone() is None:
                raise KeyError(image_id)
            row = conn.execute(
                "SELECT description, tags_json FROM image_annotations WHERE image_id = ?",
                (image_id,)).fetchone()
            current_description = None if row is None else row["description"]
            current_tags = (None if row is None or row["tags_json"] is None
                            else _decode_tags(row["tags_json"]))
            final_description = description if description_given else current_description
            final_tags = tags if tags_given else current_tags
            has_edit = description_given or tags_given
            if has_edit and final_description is None and final_tags is None:
                conn.execute("DELETE FROM image_annotations WHERE image_id = ?", (image_id,))
            elif has_edit:
                conn.execute(
                    """INSERT INTO image_annotations (image_id, description, tags_json, edited_at, updated_at)
                       VALUES (?, ?, ?, datetime('now'), datetime('now'))
                       ON CONFLICT(image_id) DO UPDATE SET
                           description = excluded.description,
                           tags_json = excluded.tags_json,
                           edited_at = datetime('now'),
                           updated_at = datetime('now')""",
                    (image_id, final_description,
                     json.dumps(final_tags, ensure_ascii=False) if final_tags is not None else None))
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()
    return get(image_id)


def _tokens(query: str) -> list[str]:
    """Casefolded whitespace/punctuation tokens of at least 2 characters."""
    return [token for token in SPLIT_RE.split(query) if len(token) >= 2]


def _is_cjk(char: str) -> bool:
    code = ord(char)
    return any(start <= code <= end for start, end in _CJK_RANGES)


def _bigrams(text: str) -> set[str]:
    """Adjacent-two-CJK-character bigrams; long unspaced Chinese queries need these."""
    return {first + second for first, second in zip(text, text[1:])
            if _is_cjk(first) and _is_cjk(second)}


def search_matches(query: str, limit: int = 20, *, manual_only: bool = False) -> list[dict]:
    """Local text channel matching annotations and available analysis, without embeddings.

    Tags score 3.0 + matched-tag count; a description-only hit scores 2.0.
    Matching keeps the equality/containment/token rules and adds CJK bigram overlap
    so a long Chinese query (no spaces) can still hit a shorter annotation.
    """
    if not isinstance(query, str):
        return []
    query = query.strip()
    if not query or isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
        return []
    needle = query.casefold()
    tokens = _tokens(needle)
    query_bigrams = _bigrams(needle)
    conn = db.connect()
    try:
        rows = conn.execute(
            """SELECT i.id AS image_id,
                      COALESCE(a.description, v.description) AS description,
                      COALESCE(a.tags_json, v.subject) AS tags_json
               FROM images i
               LEFT JOIN image_annotations a ON a.image_id = i.id
               LEFT JOIN analyses v ON v.image_id = i.id AND v.status = 'done' AND NOT ?
               WHERE a.image_id IS NOT NULL OR v.image_id IS NOT NULL""", (manual_only,)).fetchall()
    finally:
        conn.close()
    matches: list[dict] = []
    for row in rows:
        description = row["description"].casefold() if isinstance(row["description"], str) else ""
        tags = [tag.casefold() for tag in _decode_tags(row["tags_json"])]
        tag_hits = 0
        for tag in tags:
            if not tag:
                continue
            if (tag == needle or tag in needle
                    or any(token in tag for token in tokens)
                    or bool(query_bigrams & _bigrams(tag))):
                tag_hits += 1
        if tag_hits:
            score = 3.0 + tag_hits
        elif ((len(needle) >= 2 and needle in description)
              or any(token in description for token in tokens)
              or bool(query_bigrams & _bigrams(description))):
            score = 2.0
        else:
            continue
        matches.append({"image_id": row["image_id"], "score": score})
    matches.sort(key=lambda item: (-item["score"], item["image_id"]))
    return matches[:limit]


def post_search_matches(query: str) -> dict[int, float]:
    """Free literal matching on stored post title, body (including inline tags) and author."""
    needle = query.strip().casefold()
    terms = set(_tokens(needle)) | _bigrams(needle)
    if not terms:
        return {}
    conn = db.connect()
    try:
        posts = conn.execute("SELECT id, title, description, author_name FROM posts").fetchall()
        images = conn.execute(
            """SELECT post_id, image_id FROM post_images WHERE image_id IS NOT NULL
               UNION SELECT post_id, id FROM images"""
        ).fetchall()
    finally:
        conn.close()

    post_scores = {}
    for row in posts:
        fields = [(row[key] or "").casefold() for key in ("title", "description", "author_name")]
        if any(needle in field for field in fields):
            post_scores[row["id"]] = 1.0
            continue
        overlap = sum(any(term in field for field in fields) for term in terms)
        if overlap:
            post_scores[row["id"]] = 0.2 * (overlap / len(terms)) ** 2

    scores: dict[int, float] = {}
    for row in images:
        score = post_scores.get(row["post_id"])
        image_id = row["image_id"]
        if score is not None and score > scores.get(image_id, 0):
            scores[image_id] = score
    return scores
