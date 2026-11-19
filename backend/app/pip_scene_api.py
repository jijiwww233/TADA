"""Thin PiP scene router; validation lives in pip_scene.py, locking in the store."""
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Path
from pydantic import BaseModel, ConfigDict, Field, StrictInt

from .pip_scene_store import InvalidStoredScene, PipSceneStore, RevisionConflict
from .project_store import ImageNotFound, PersistenceError, ProjectNotFound

PositiveId = Annotated[int, Path(gt=0)]


class Command(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SaveScene(Command):
    revision: Annotated[StrictInt, Field(ge=0)]
    # Any: the scene contract is enforced by pip_scene.validate_scene, which lets
    # the route return {"code": "invalid_scene"} instead of pydantic's error list.
    scene: Any


def pip_scene_router(store=None):
    store = store or PipSceneStore()
    router = APIRouter(prefix="/api/projects", tags=["pip-scene"])

    def call(method, *args, **kwargs):
        try:
            return method(*args, **kwargs)
        except ProjectNotFound:
            raise HTTPException(404, detail={"code": "project_not_found"}) from None
        except ImageNotFound:
            raise HTTPException(404, detail={"code": "image_not_found"}) from None
        except RevisionConflict as exc:
            raise HTTPException(409, detail={"code": "revision_conflict",
                                             "current_revision": exc.current_revision,
                                             "current_scene": exc.current_scene}) from None
        except InvalidStoredScene:
            raise HTTPException(409, detail={"code": "invalid_stored_scene"}) from None
        except ValueError:
            raise HTTPException(422, detail={"code": "invalid_scene"}) from None
        except PersistenceError:
            raise HTTPException(503, detail={"code": "pip_scene_store_unavailable"}) from None

    @router.get("/{project_id}/pip-scene")
    def get_scene(project_id: PositiveId):
        return call(store.get_scene, project_id)

    @router.put("/{project_id}/pip-scene")
    def save_scene(project_id: PositiveId, request: SaveScene):
        return call(store.save_scene, project_id, request.revision, request.scene)

    return router
