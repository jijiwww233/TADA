"""Backend-served page for the 「能否使用」 evaluation (create columns only).

Reads eval/usability.json, writes it on each click; no model call, no DB write, no
product frontend build. Standalone server: backend/tools/usability_server.py.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict

from . import usability

PAGE = Path(__file__).with_name("usability.html")


class Vote(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task_id: str
    panel: str
    image_id: int
    usable: bool | None = None
    note: str | None = None


def usability_router(*, data_path: Path | None = None):
    router = APIRouter()
    path = data_path or usability.default_path()

    @router.get("/eval/usability", response_class=HTMLResponse)
    def page():
        return HTMLResponse(PAGE.read_text(encoding="utf-8"))

    @router.get("/api/usability/data")
    def data():
        try:
            payload = usability.read(path)
        except (OSError, ValueError):
            raise HTTPException(503, detail={"code": "usability_unreadable"}) from None
        return {**payload, "summary": usability.summary(payload)}

    @router.post("/api/usability/vote")
    def vote(request: Vote):
        try:
            payload = usability.read(path)
            usability.apply_vote(payload, task_id=request.task_id, panel=request.panel,
                                 image_id=request.image_id, usable=request.usable,
                                 note=request.note)
            usability.write(path, payload)
        except KeyError:
            raise HTTPException(404, detail={"code": "entry_not_found"}) from None
        except ValueError as exc:
            raise HTTPException(422, detail={"code": "invalid_vote", "message": str(exc)}) from None
        except OSError:
            raise HTTPException(503, detail={"code": "usability_write_failed"}) from None
        return {"ok": True, "summary": usability.summary(payload)}

    return router
