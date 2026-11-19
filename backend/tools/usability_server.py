#!/usr/bin/env python
r"""Standalone server for the 「能否使用」 page.

Reads eval/usability.json and the real image files; writes only eval/usability.json
when the user clicks. It never calls db.init_db(), never starts analysis/embedding
workers and never touches the library DB.

  set WOMINMIN_DATA_DIR=./data
  python backend/tools/usability_server.py --port 8114
  open htt./data
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from fastapi import FastAPI  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

from app import config  # noqa: E402
from app.usability_api import usability_router  # noqa: E402


def create_usability_app(data_path: Path | None = None) -> FastAPI:
    app = FastAPI(title="能否使用（只读图库）")
    app.include_router(usability_router(data_path=data_path))
    app.mount("/images", StaticFiles(directory=config.images_dir()), name="images")
    return app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8114)
    parser.add_argument("--data", default=None, help="eval/usability.json 路径")
    args = parser.parse_args(argv)
    import uvicorn
    uvicorn.run(create_usability_app(Path(args.data) if args.data else None),
                host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
