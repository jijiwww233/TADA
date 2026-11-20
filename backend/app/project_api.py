"""Thin Day7 router; no recommendation persistence or model dependencies."""
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Path
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, model_validator

from .projects import PURPOSES
from .project_store import ProjectStore, ProjectNotFound, ImageNotFound, InvalidStoredPin, PersistenceError

Purpose = Literal["layout", "style", "palette", "subject"]
PositiveId = Annotated[int, Path(gt=0)]


class Command(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateProject(Command):
    name: StrictStr
    brief: StrictStr = ""


class UpdateProject(Command):
    """PATCH 项目：name / brief 都可选，但至少要有一个。name 去空格后必须非空。"""
    name: StrictStr | None = None
    brief: StrictStr | None = None

    @model_validator(mode="after")
    def at_least_one_field(self):
        if self.name is None and self.brief is None:
            raise ValueError("name or brief required")
        return self


class PinImage(Command):
    image_id: Annotated[StrictInt, Field(gt=0)]
    purposes: Annotated[list[Purpose], Field(min_length=1)]
    note: StrictStr | None = None


class MoveImage(Command):
    direction: Literal["previous", "next"]


def project_router(store=None):
    store = store or ProjectStore()
    router = APIRouter(prefix="/api/projects", tags=["projects"])

    def call(method, *args, **kwargs):
        try:
            return method(*args, **kwargs)
        except (ProjectNotFound, ImageNotFound) as exc:
            code = "project_not_found" if isinstance(exc, ProjectNotFound) else "image_not_found"
            raise HTTPException(404, detail={"code": code}) from None
        except InvalidStoredPin:
            raise HTTPException(409, detail={"code": "invalid_stored_purposes"}) from None
        except ValueError:
            raise HTTPException(422, detail={"code": "invalid_project_or_pin_command"}) from None
        except PersistenceError:
            raise HTTPException(503, detail={"code": "project_store_unavailable"}) from None

    @router.post("", status_code=201)
    def create(request: CreateProject):
        return call(store.create_project, request.name, request.brief)

    @router.get("")
    def list_projects():
        return {"items": call(store.list_projects), "allowed_purposes": list(PURPOSES)}

    @router.get("/{project_id}")
    def detail(project_id: PositiveId):
        return call(store.detail, project_id)

    @router.patch("/{project_id}")
    def update(project_id: PositiveId, request: UpdateProject):
        fields = {}
        if request.name is not None:
            fields["name"] = request.name
        if request.brief is not None:
            fields["brief"] = request.brief
        return call(store.update_project, project_id, **fields)

    @router.delete("/{project_id}")
    def delete(project_id: PositiveId):
        """软删除：只写 projects.deleted_at；pins/图片/分析都不动，恢复脚本清标记即全部回来。"""
        return call(store.soft_delete_project, project_id)

    @router.get("/{project_id}/pins")
    def pins(project_id: PositiveId):
        return call(store.list_pins, project_id)

    @router.post("/{project_id}/pins")
    def pin(project_id: PositiveId, request: PinImage):
        return call(store.pin_image, project_id, request.image_id, request.purposes, note=request.note)

    @router.delete("/{project_id}/pins/{image_id}/purposes/{purpose}")
    def remove(project_id: PositiveId, image_id: PositiveId, purpose: Purpose):
        return {"pin": call(store.remove_purpose, project_id, image_id, purpose)}

    @router.delete("/{project_id}/pins/{image_id}")
    def remove_image(project_id: PositiveId, image_id: PositiveId):
        return call(store.remove_image, project_id, image_id)

    @router.patch("/{project_id}/pins/{image_id}/position")
    def move_image(project_id: PositiveId, image_id: PositiveId, request: MoveImage):
        return call(store.move_image, project_id, image_id, -1 if request.direction == "previous" else 1)

    return router
