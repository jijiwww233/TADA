"""Embed every done analysis whose stored embedding is stale, through the same adapter
the analysis worker uses. Re-running is the recovery path: valid rows cost nothing.

Usage from backend/:
  python -m app.embedding.backfill --dry-run     # counts only, no network
  python -m app.embedding.backfill [--limit N]
"""
from __future__ import annotations

import argparse
import json
import math

from .. import db
from . import store
from .adapter import EmbeddingAdapter
from .client import MAX_BATCH


def plan(adapter: EmbeddingAdapter) -> dict:
    """What a backfill would do under the current config. No HTTP."""
    conn = db.connect()
    try:
        c = store.counts(conn, adapter.spec)
    finally:
        conn.close()
    pending = c.pop("pending")
    return {
        "configured": adapter.configured,
        "model": adapter.spec.model,
        "dimensions": adapter.spec.dimensions,
        "scheme": adapter.spec.scheme,
        **c,
        "estimated_requests": math.ceil(len(pending) / MAX_BATCH),
        "stale_ids": [p.image_id for p in pending],
    }


def run(adapter: EmbeddingAdapter, limit: int | None = None) -> dict:
    report = plan(adapter)
    ids = report["stale_ids"][:limit] if limit else report["stale_ids"]
    for image_id in ids:
        adapter.enqueue(image_id)
    return adapter.drain()


def _cli() -> None:
    parser = argparse.ArgumentParser(description="Build missing / stale combined_d_v1 embeddings.")
    parser.add_argument("--dry-run", action="store_true", help="only report, send nothing")
    parser.add_argument("--limit", type=int, default=None, help="embed at most N images")
    args = parser.parse_args()
    db.init_db()
    adapter = EmbeddingAdapter(client_factory=None if not args.dry_run else _no_client)
    if adapter.spec is None:
        print(json.dumps({"error": adapter.last_error}, ensure_ascii=False))
        return
    report = plan(adapter)
    print(json.dumps(report, ensure_ascii=False))
    if args.dry_run:
        return
    print(json.dumps(run(adapter, args.limit), ensure_ascii=False))
    adapter.shutdown()


def _no_client(_settings):
    raise RuntimeError("dry-run must not create an embedding client")


if __name__ == "__main__":
    _cli()
