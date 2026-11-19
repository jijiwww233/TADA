from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .project_store import ImageNotFound, ProjectNotFound

Dimension = Literal["combined", "palette", "layout", "subject", "style"]
PositiveId = Annotated[int, Field(strict=True, gt=0)]


class BatchPins(BaseModel):
    image_ids: list[PositiveId] = Field(min_length=1, max_length=20)
    purpose: Literal["layout", "style", "palette", "subject"]


def vibe_router(service):
    router = APIRouter(prefix="/api/projects")

    @router.get("/{project_id}/vibe")
    def resurface(project_id: int):
        try:
            return {"items": service.resurface(project_id)}
        except ProjectNotFound:
            raise HTTPException(404, "项目不存在") from None

    @router.post("/{project_id}/vibe/reject/{image_id}")
    def reject(project_id: int, image_id: int):
        try:
            service.reject(project_id, image_id)
        except (ProjectNotFound, ImageNotFound):
            raise HTTPException(404, "项目或图片不存在") from None
        return {"rejected": True}

    @router.post("/{project_id}/vibe/pins")
    def pin_batch(project_id: int, request: BatchPins):
        try:
            return {"pinned": service.pin_batch(project_id, request.image_ids, request.purpose)}
        except (ProjectNotFound, ImageNotFound):
            raise HTTPException(404, "项目或图片不存在") from None

    return router
