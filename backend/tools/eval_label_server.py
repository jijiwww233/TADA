#!/usr/bin/env python
r"""Standalone read-only label page server.

Never calls db.init_db(), never starts analysis/embedding workers and never writes the
library DB. It only serves /eval/label, /api/eval/library, /api/eval/tasks and the real
image files. Use a distinct port so it can run beside the product backend.

  set WOMINMIN_DATA_DIR=./data
  python tools/eval_label_server.py --port 8010
  open htt./data
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND.parent
sys.path.insert(0, str(BACKEND))

from fastapi import FastAPI  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

from app import config  # noqa: E402
from app.eval_api import eval_router  # noqa: E402


def create_label_app(tasks_path: Path | None = None) -> FastAPI:
    app = FastAPI(title="评测标注（只读）")
    app.include_router(eval_router(tasks_path=tasks_path or (PROJECT_ROOT / "eval" / "tasks.json")))
    # Read-only static images: no importer, no DB migration, no analysis queue.
    app.mount("/images", StaticFiles(directory=config.images_dir()), name="images")
    return app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8010)
    parser.add_argument("--tasks", default=str(PROJECT_ROOT / "eval" / "tasks.json"))
    args = parser.parse_args(argv)
    import uvicorn
    uvicorn.run(create_label_app(Path(args.tasks)), host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
