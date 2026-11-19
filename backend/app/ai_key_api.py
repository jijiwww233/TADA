"""Verify an explicit Key and save it in the local, git-ignored AI configuration."""
import json
import re
from threading import Lock
from urllib.parse import urlsplit, urlunsplit

import httpx
from dotenv import set_key
from fastapi import APIRouter, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from . import config, demo_cache


def _masked(key: str) -> dict:
    return {"configured": bool(key), "last4": key[-4:] if len(key) > 4 else None}


def _models_url(base_url: str) -> tuple[str, bool]:
    base = urlsplit(base_url)
    if base.scheme != "https" or not base.hostname or base.username or base.password or base.query or base.fragment:
        raise ValueError("invalid provider URL")
    host = base.hostname
    path = base.path.rstrip("/")
    dashscope = path == "/compatible-mode/v1" and (
        host.endswith(".maas.aliyuncs.com")
        or host in {"dashscope.aliyuncs.com", "dashscope-intl.aliyuncs.com",
                    "dashscope-us.aliyuncs.com", "cn-hongkong.dashscope.aliyuncs.com"}
    )
    return urlunsplit(base._replace(path=("/api/v1" if dashscope else path) + "/models")), dashscope


def _verify_key(key: str) -> None:
    try:
        url, dashscope = _models_url(config.ai_settings()["base_url"])
    except ValueError:
        raise HTTPException(409, "请先配置可验证的 HTTPS 接口地址") from None
    try:
        with httpx.Client(timeout=8, follow_redirects=False) as client:
            response = client.get(url, headers={"Authorization": f"Bearer {key}"},
                                  params={"page_size": 1} if dashscope else None)
        if response.status_code in (401, 403):
            raise HTTPException(422, "服务商未接受这把钥匙")
        if not response.is_success:
            raise HTTPException(503, "服务商暂时无法验证钥匙")
        data = response.json()
        rows = (data.get("output") or {}).get("models") if dashscope else data.get("data")
        if not isinstance(rows, list) or data.get("success") is False:
            raise ValueError
    except (httpx.HTTPError, httpx.InvalidURL, ValueError, AttributeError, TypeError):
        raise HTTPException(503, "服务商暂时无法验证钥匙") from None


async def _read_key(request: Request) -> str:
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
        raise HTTPException(415, "请提交 JSON")
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > 4096:
            raise HTTPException(413, "钥匙输入过长")
        body.extend(chunk)
    try:
        data = json.loads(body)
        key = data.get("key")
        if set(data) == {"key"} and isinstance(key, str):
            key = key.strip()
            if re.fullmatch(r"[A-Za-z0-9._~+/=-]{8,512}", key):
                return key
    except (ValueError, AttributeError, TypeError):
        pass
    # Request validation must not reflect the submitted secret in a 422 response.
    raise HTTPException(422, "钥匙格式无效")


def ai_key_router() -> APIRouter:
    router = APIRouter(prefix="/api/settings/api-key")
    write_lock = Lock()
    pending_status: dict | None = None

    @router.get("")
    def status():
        return pending_status or _masked(config.ai_settings()["api_key"])

    def verify_and_save(key: str):
        nonlocal pending_status
        with write_lock:
            _verify_key(key)
            try:
                ok, _, _ = set_key(str(config.PROJECT_ROOT / ".env"), "AI_API_KEY", key, quote_mode="always")
                if not ok:
                    raise OSError
            except (OSError, ValueError):
                raise HTTPException(500, "无法写入本机 AI 配置") from None
            # Keep every running worker on the same configuration until restart.
            pending_status = {**_masked(key), "restart_required": True}
            return pending_status

    @router.put("")
    async def save(request: Request):
        if demo_cache.offline() or config.AI_KEY_FROM_ENV:
            raise HTTPException(409, "当前实例的钥匙由启动配置管理")
        key = await _read_key(request)
        return await run_in_threadpool(verify_and_save, key)

    return router
