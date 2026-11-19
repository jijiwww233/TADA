#!/usr/bin/env python
"""Offline search evaluation on a real collection.

Usage (from backend/):
  python tools/eval_search.py --queries tools/search_queries.example.json
  python tools/eval_search.py --db ../data/app.db --queries my_queries.json --json out/eval.json

It reads only the current valid vectors:
  embeddings.dimension='combined_d_v1', model/dimensions match the configured
  embedding model, and text_hash matches the freshly built text for that image.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app import config  # noqa: E402
from app.embedding import (  # noqa: E402
    EmbeddingClient,
    EmbeddingSettings,
    EmbeddingSpec,
    SearchService,
    evaluate,
    load_queries,
)
from app.embedding.repository import RepositoryError, load_search_rows  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="离线评测 combined_d_v1 检索")
    parser.add_argument("--db", default=str(config.data_dir() / "app.db"))
    parser.add_argument("--queries", required=True)
    parser.add_argument("--top", type=int, default=5, help="报告 TopN（默认 5）")
    parser.add_argument("--top-n", type=int, default=20, help="每次搜索取回数量（默认 20）")
    parser.add_argument("--json", default="", help="把完整结果写到该 JSON 文件")
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.is_file():
        print(f"数据库不存在: {db_path}", file=sys.stderr)
        return 2

    settings = EmbeddingSettings.from_env()
    spec = EmbeddingSpec.from_settings(settings)
    # Evaluation is also read-only; never create/migrate the user's DB.
    conn = sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    try:
        rows = load_search_rows(conn, spec)
    except RepositoryError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    finally:
        conn.close()

    if not rows:
        print("没有可评测的当前向量（检查迁移是否应用、索引是否已构建）。", file=sys.stderr)
        return 2

    queries = load_queries(args.queries)
    client = EmbeddingClient(settings)
    try:
        service = SearchService(client, spec)
        result = evaluate(lambda query, limit: service.search(query, rows, top_n=limit),
                          queries, top_n=args.top, rank_depth=args.top_n)
    finally:
        client.close()

    metrics = result["metrics"]
    print(f"model={spec.model} dimensions={spec.dimensions} scheme={spec.scheme} "
          f"vectors={len(rows)} queries={metrics['queries']}")
    print(f"Top1 {metrics['top1']}/{metrics['queries']}  "
          f"Top3 {metrics['top3']}/{metrics['queries']}  "
          f"Top5 {metrics['top5']}/{metrics['queries']}  MRR={metrics['mrr']}")
    print("-" * 78)
    for row in result["per_query"]:
        rank = row["rank"] if row["rank"] is not None else "miss"
        top1 = row["top"][0] if row["top"] else None
        top_text = (f"{top1['image_id']} ({top1['score']:.3f})" if top1 else "-")
        print(f"{row['id']:>4} rank={str(rank):>4}  top1={top_text:<22} {row['query']}")
    if result["failures"]:
        print("-" * 78)
        print(f"未进 Top{args.top} 的查询: {len(result['failures'])}")
        for failure in result["failures"]:
            print(f"  {failure['id']} rank={failure['rank']} expected={failure['expected']} "
                  f"query={failure['query']}")

    if args.json:
        out = Path(args.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({
            "model": spec.model,
            "dimensions": spec.dimensions,
            "scheme": spec.scheme,
            "vectors": len(rows),
            "result": result,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"结果已写入 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
