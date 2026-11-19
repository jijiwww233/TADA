"""HTTP boundary for watched import folders.

Only the folder list, pause/resume and the daily cap can be changed here. Imported
images go through the normal local import pipeline; this router never touches source
folders and never calls a paid service.
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, StrictBool, StrictInt, StrictStr

from . import config, watch_folders as store
from .watch_folders import DuplicateFolder, WatchFolders


class AddFolderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: StrictStr


class ToggleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: StrictBool


class FolderUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: StrictBool | None = None
    include_subfolders: StrictBool | None = None


class DailyLimitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    daily_limit: StrictInt


def watch_folder_router(service: WatchFolders, worker=None) -> APIRouter:
    router = APIRouter()

    @router.get("/api/watch-folders")
    def watch_status():
        return {**store.status(service),
                "analysis_configured": bool(worker and worker.state()["configured"])}

    @router.put("/api/watch-folders/settings")
    def watch_settings(request: DailyLimitRequest):
        try:
            value = store.set_daily_limit(request.daily_limit)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        return {"daily_limit": value}

    @router.put("/api/watch-folders/auto-analysis")
    def auto_analysis(request: ToggleRequest):
        if not config.auto_sync_allowed():
            raise HTTPException(403, "请在正式入口开启自动分析")
        if request.enabled and (worker is None or not worker.state()["configured"]):
            raise HTTPException(409, "请先配置 AI 分析服务")
        store.set_auto_analysis(request.enabled)
        if request.enabled and not worker.running:
            worker.start()
        return {"auto_analysis_enabled": request.enabled}

    @router.post("/api/watch-folders/scan-now")
    def scan_now():
        return service.scan_manual()

    @router.post("/api/watch-folders/analyze-now")
    def analyze_now():
        result = service.analyze_now()
        result["started"] = False
        if worker is not None:
            if worker.state()["configured"] and not worker.running:
                worker.start()
            result["started"] = bool(worker.running)
        return result

    @router.post("/api/watch-folders")
    def add_folder(request: AddFolderRequest):
        try:
            return service.add_folder(request.path)
        except DuplicateFolder as exc:
            raise HTTPException(409, str(exc)) from None
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None

    @router.put("/api/watch-folders/{folder_id}")
    def update_folder(folder_id: int, request: FolderUpdateRequest):
        changes = request.model_dump(exclude_none=True)
        if not changes:
            raise HTTPException(422, "请选择要更新的文件夹设置")
        try:
            return service.update_folder(folder_id, **changes)
        except KeyError:
            raise HTTPException(404, "文件夹不存在") from None

    @router.delete("/api/watch-folders/{folder_id}")
    def delete_folder(folder_id: int):
        try:
            service.remove_folder(folder_id)
        except KeyError:
            raise HTTPException(404, "文件夹不存在") from None
        return {"removed": True}

    return router
