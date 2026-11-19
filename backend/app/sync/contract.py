"""Reader page contract (sync-reader/1, single-page form).

Adapted from spikes/sync_contract/docs/contract.md. Readers (one per platform) produce
pages; the main program only consumes this format and never platform-specific data.

Page:
  {contract_version: "sync-reader/1", platform, collection: {collection_key},
   page: {index, cursor_in?, cursor_out?, has_more}, items: [...], failures?: [...]}
Item:
  {work_key, title?, description?, author_name?, canonical_url?, media_type?,
   images: [{idx (from 1), url?, local_path?, sha256?, download_state?}], extra?}
Failure (reader could not fully fetch an item; additive field, not in the spike):
  {work_key, stage, error?, source_unavailable?}

An invalid item that still has a work_key becomes a failure (retryable later).
Anything without a work_key rejects the whole page, so the cursor cannot move past it.
"""
import re
from dataclasses import dataclass, field
from typing import Any

CONTRACT_VERSION = "sync-reader/1"
PLATFORMS = {"xhs", "huaban", "pinterest", "zcool"}  # posts.platform CHECK; sync platforms only (local is not synced)
STAGES = {"list", "detail", "image_download", "store", "contract"}
DOWNLOAD_STATES = {"ok", "pending", "failed", "remote_only"}
MEDIA_TYPES = {"image", "video", "mixed", "unknown"}
_SHA = re.compile(r"^[0-9a-f]{64}$")


class ContractError(ValueError):
    pass


@dataclass
class ImageRef:
    idx: int
    url: str | None = None
    local_path: str | None = None
    sha256: str | None = None
    download_state: str | None = None


@dataclass
class Item:
    work_key: str
    title: str | None = None
    description: str | None = None
    author_name: str | None = None
    canonical_url: str | None = None
    media_type: str = "image"
    images: list[ImageRef] = field(default_factory=list)
    extra: dict | None = None


@dataclass
class Failure:
    work_key: str
    stage: str
    error: str | None = None
    source_unavailable: bool = False


@dataclass
class Page:
    platform: str
    collection_key: str
    index: int
    cursor_in: str | None
    cursor_out: str | None
    has_more: bool
    items: list[Item]
    failures: list[Failure]
    collection_title: str | None = None
    collection_kind: str | None = None


def _opt_str(obj: dict, key: str) -> str | None:
    v = obj.get(key)
    if v is None:
        return None
    if not isinstance(v, str):
        raise ContractError(f"{key} 必须是字符串")
    return v


def _work_key(raw: Any) -> str | None:
    if isinstance(raw, dict):
        wk = raw.get("work_key")
        if isinstance(wk, str) and wk.strip():
            return wk
    return None


def _parse_image(raw: Any) -> ImageRef:
    if not isinstance(raw, dict):
        raise ContractError("图片必须是对象")
    idx = raw.get("idx")
    if not isinstance(idx, int) or isinstance(idx, bool) or idx < 1:
        raise ContractError("图片 idx 必须是 >=1 的整数")
    img = ImageRef(
        idx=idx,
        url=_opt_str(raw, "url"),
        local_path=_opt_str(raw, "local_path"),
        sha256=_opt_str(raw, "sha256"),
        download_state=_opt_str(raw, "download_state"),
    )
    if img.url and not img.url.startswith(("http://", "https://")):
        raise ContractError("图片 url 必须是 http(s)")
    if img.sha256 is not None and not _SHA.match(img.sha256):
        raise ContractError("sha256 必须是 64 位小写十六进制")
    if img.download_state is not None and img.download_state not in DOWNLOAD_STATES:
        raise ContractError(f"未知 download_state {img.download_state}")
    if not (img.url or img.local_path or img.sha256) and img.download_state != "failed":
        raise ContractError("图片至少要有 url / local_path / sha256 之一")
    return img


def _parse_item(raw: dict, work_key: str) -> Item:
    images_raw = raw.get("images")
    if not isinstance(images_raw, list):
        raise ContractError("images 必须是数组")
    images = [_parse_image(r) for r in images_raw]
    if len({i.idx for i in images}) != len(images):
        raise ContractError("同一作品内图片 idx 重复")
    media_type = raw.get("media_type") or "image"
    if media_type not in MEDIA_TYPES:
        raise ContractError(f"未知 media_type {media_type}")
    url = _opt_str(raw, "canonical_url")
    if url and not url.startswith(("http://", "https://")):
        raise ContractError("canonical_url 必须是 http(s)")
    extra = raw.get("extra")
    if extra is not None and not isinstance(extra, dict):
        raise ContractError("extra 必须是对象")
    return Item(
        work_key=work_key,
        title=_opt_str(raw, "title"),
        description=_opt_str(raw, "description"),
        author_name=_opt_str(raw, "author_name"),
        canonical_url=url,
        media_type=media_type,
        images=sorted(images, key=lambda i: i.idx),
        extra=extra,
    )


def parse_page(raw: Any) -> Page:
    """Raise ContractError if the page as a whole is unusable (cursor must not move)."""
    if not isinstance(raw, dict):
        raise ContractError("页面必须是对象")
    if raw.get("contract_version") != CONTRACT_VERSION:
        raise ContractError(f"contract_version 必须是 {CONTRACT_VERSION}")
    platform = raw.get("platform")
    if platform not in PLATFORMS:
        raise ContractError(f"不支持的平台 {platform!r}")
    collection = raw.get("collection")
    if not isinstance(collection, dict) or not isinstance(collection.get("collection_key"), str):
        raise ContractError("缺少 collection.collection_key")
    page = raw.get("page")
    if not isinstance(page, dict):
        raise ContractError("缺少 page")
    index = page.get("index")
    if not isinstance(index, int) or isinstance(index, bool) or index < 1:
        raise ContractError("page.index 必须是 >=1 的整数")
    if not isinstance(page.get("has_more"), bool):
        raise ContractError("page.has_more 必须显式给出布尔值")
    items_raw = raw.get("items")
    if not isinstance(items_raw, list):
        raise ContractError("items 必须是数组")
    failures_raw = raw.get("failures") or []
    if not isinstance(failures_raw, list):
        raise ContractError("failures 必须是数组")

    items: list[Item] = []
    failures: list[Failure] = []
    for n, r in enumerate(items_raw):
        wk = _work_key(r)
        if wk is None:
            # No retry handle: the whole page is rejected rather than skipping it silently.
            raise ContractError(f"items[{n}] 缺少 work_key，无法记录为可重试失败")
        try:
            items.append(_parse_item(r, wk))
        except ContractError as exc:
            failures.append(Failure(wk, "contract", str(exc)))
    for n, r in enumerate(failures_raw):
        wk = _work_key(r)
        if wk is None:
            raise ContractError(f"failures[{n}] 缺少 work_key，无法重试")
        stage = r.get("stage")
        if stage not in STAGES:
            raise ContractError(f"failures[{n}] 未知 stage {stage!r}")
        failures.append(Failure(wk, stage, _opt_str(r, "error"), bool(r.get("source_unavailable"))))

    return Page(
        platform=platform,
        collection_key=collection["collection_key"],
        index=index,
        cursor_in=_opt_str(page, "cursor_in"),
        cursor_out=_opt_str(page, "cursor_out"),
        has_more=page["has_more"],
        items=items,
        failures=failures,
        collection_title=_opt_str(collection, "title"),
        collection_kind=_opt_str(collection, "kind"),
    )
