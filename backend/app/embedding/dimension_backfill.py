"""Build missing / stale per-dimension embeddings (subject / layout / style / description)
from existing done analyses. Writes only embeddings rows of those schemes; never touches
analyses, colors or combined_d_v1. Re-running is the recovery path: valid rows cost nothing.

Usage from backend/:
  python -m app.embedding.dimension_backfill --dry-run   # counts + request estimate, no network
  python -m app.embedding.dimension_backfill [--scheme layout_v1 ...]
"""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
import time
from typing import Callable

from .. import db
from . import dimensions, store
from .client import MAX_BATCH, STOP_KINDS, EmbeddingClient, EmbeddingError, EmbeddingSettings


def plan(conn: sqlite3.Connection, settings: EmbeddingSettings, schemes=dimensions.SCHEMES) -> dict:
    out = {"model": settings.model, "dimensions": settings.dimensions, "schemes": {}}
    for scheme in schemes:
        c = dimensions.counts(conn, dimensions.spec_for(settings, scheme))
        c.pop("pending")
        c["estimated_requests"] = math.ceil(c["stale"] / MAX_BATCH)
        out["schemes"][scheme] = c
    out["estimated_requests"] = sum(s["estimated_requests"] for s in out["schemes"].values())
    out["texts"] = sum(s["stale"] for s in out["schemes"].values())
    out["chars"] = sum(s["chars"] for s in out["schemes"].values())
    return out


def run(conn: sqlite3.Connection, settings: EmbeddingSettings, schemes=dimensions.SCHEMES,
        client_factory: Callable = EmbeddingClient) -> dict:
    """Embed in batches of MAX_BATCH; each batch commits on its own. A STOP_KINDS error
    (config/auth/model) ends the run; other batch errors are recorded and skipped."""
    report = {"embedded": {}, "errors": [], "requests": 0, "seconds": 0.0}
    started = time.perf_counter()
    with client_factory(settings) as client:
        for scheme in schemes:
            spec = dimensions.spec_for(settings, scheme)
            pending, _ = dimensions.load_pending(conn, spec)
            report["embedded"][scheme] = 0
            for start in range(0, len(pending), MAX_BATCH):
                batch = pending[start:start + MAX_BATCH]
                try:
                    vectors = client.embed([p.text for p in batch])
                except EmbeddingError as exc:
                    report["errors"].append({"scheme": scheme, "ids": [p.image_id for p in batch],
                                             "kind": exc.kind, "message": exc.message})
                    if exc.kind in STOP_KINDS:
                        report["stopped"] = exc.kind
                        report["requests"] = getattr(client, "request_count", None)
                        report["seconds"] = round(time.perf_counter() - started, 2)
                        return report
                    continue
                store.upsert_batch(conn, spec, batch, vectors)
                report["embedded"][scheme] += len(batch)
        report["requests"] = getattr(client, "request_count", None)
    report["seconds"] = round(time.perf_counter() - started, 2)
    return report


def _cli() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="only report, send nothing")
    parser.add_argument("--scheme", action="append", choices=dimensions.SCHEMES)
    args = parser.parse_args()
    schemes = tuple(args.scheme or dimensions.SCHEMES)
    settings = EmbeddingSettings.from_env()
    conn = db.connect()
    try:
        print(json.dumps(plan(conn, settings, schemes), ensure_ascii=False))
        if not args.dry_run:
            print(json.dumps(run(conn, settings, schemes), ensure_ascii=False))
    finally:
        conn.close()


if __name__ == "__main__":
    _cli()
