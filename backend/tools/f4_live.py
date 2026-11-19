"""Run the product search and recommendation routes against an isolated data copy.

Never starts sync/analysis workers, never writes the library DB. It only calls the
same HTTP handlers as the frontend and saves
their raw responses (with usage) so eval_f3.py --responses can score them.

  set WOMINMIN_DATA_DIR=<read-only data copy>
  python backend/tools/f4_live.py --out <new folder>/responses.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import dotenv_values
from fastapi.testclient import TestClient

from app.eval_tasks import validate

PRICES = {  # RMB per 1M tokens; only used for an estimate, never for billing
    "text_in": 0.2, "text_out": 0.8, "embed_in": 0.125,
}


def estimate_rmb(prompt_tokens: int | None, completion_tokens: int | None, embedding_tokens: int = 0) -> float | None:
    if prompt_tokens is None or completion_tokens is None:
        return None
    return (prompt_tokens * PRICES["text_in"] + completion_tokens * PRICES["text_out"]
            + embedding_tokens * PRICES["embed_in"]) / 1_000_000


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tasks", type=Path,
                        default=Path(__file__).resolve().parents[2] / "eval" / "tasks.json")
    parser.add_argument("--split", default="dev", choices=["dev", "test"])
    parser.add_argument("--final", action="store_true", help="only with this flag run the test split once")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--local", action="store_true", help="no key and no provider calls")
    parser.add_argument("--provider-env", type=Path, help="read provider configuration only into this process")
    args = parser.parse_args(argv)

    if args.split == "test" and not args.final:
        parser.error("test 必须加 --final，且只跑一次")
    if args.split == "dev" and args.final:
        parser.error("--final 只用于 test split")
    if args.out.exists():
        parser.error("输出已存在，请选择新文件")
    data = os.environ.get("WOMINMIN_DATA_DIR")
    if not data or not (Path(data) / "app.db").is_file():
        parser.error("WOMINMIN_DATA_DIR 必须指向已有数据库的独立副本")
    if args.local and args.provider_env:
        parser.error("--local 不读取服务商配置")
    os.environ.update(WOMINMIN_ALLOW_AUTO_SYNC="0", HF_HUB_OFFLINE="1",
                      TRANSFORMERS_OFFLINE="1", WOMINMIN_DEMO="1" if args.local else "0",
                      WOMINMIN_OFFLINE_DEMO="1" if args.local else "0")
    if args.local:
        os.environ["AI_API_KEY"] = ""
    elif args.provider_env:
        values = dotenv_values(args.provider_env, interpolate=False)
        for key in ("AI_BASE_URL", "AI_API_KEY", "VISION_MODEL", "VISION_MAX_SIDE",
                    "VISION_ENABLE_THINKING", "EMBEDDING_MODEL", "EMBEDDING_DIMENSIONS"):
            if values.get(key):
                os.environ[key] = values[key]
    from app import config
    from app.main import create_app

    if not args.local and not config.ai_settings()["api_key"]:
        parser.error("正式模式需要临时 API key")
    payload = validate(json.loads(args.tasks.read_text(encoding="utf-8-sig")))
    tasks = [t for t in payload["tasks"] if t["split"] == args.split]
    # Entering the TestClient context would start the app's lifespan workers.
    client = TestClient(create_app())

    args.out.parent.mkdir(parents=True, exist_ok=True)
    package = {"format": "f4-live-responses-v1", "split": args.split,
               "mode": "local" if args.local else "formal", "tasks_file": str(args.tasks), "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
               "responses": {}, "meta": []}
    totals = {"prompt_tokens": 0, "completion_tokens": 0, "requests": 0, "unknown": False}

    for index, task in enumerate(tasks, 1):
        started = time.perf_counter()
        error = None
        try:
            find = task["type"] == "find"
            path, field = ("/api/search", "query") if find else ("/api/recommendations/columns", "brief")
            response = client.post(path, json={field: task["query"]})
            if response.is_success:
                raw = response.json()
                if not args.local and raw.get("degraded"):
                    raw["evaluation_error"] = "formal_search_degraded"
            else:
                error = "offline_recommendation_unavailable" if args.local and not find else f"http_{response.status_code}"
                raw = {"status": "error", "evaluation_error": error}
            if args.local:
                raw["usage"] = {"requests": 0, "prompt_tokens": 0, "completion_tokens": 0}
        except Exception as exc:  # Never print an upstream body, URL or key.
            error = type(exc).__name__
            raw = {"status": "error", "evaluation_error": error}
        elapsed = round((time.perf_counter() - started) * 1000, 1)
        if not isinstance(raw, dict):
            raw = {"status": "error"}
        package["responses"][task["id"]] = raw
        usage = raw.get("usage") if isinstance(raw.get("usage"), dict) else {}
        prompt = usage.get("prompt_tokens")
        completion = usage.get("completion_tokens")
        requests = usage.get("reported_requests", usage.get("requests"))
        if prompt is None or completion is None:
            totals["unknown"] = True
        else:
            totals["prompt_tokens"] += int(prompt)
            totals["completion_tokens"] += int(completion)
        if requests is not None:
            totals["requests"] += int(requests)
        package["meta"].append({"id": task["id"], "type": task["type"], "query": task["query"],
                                "elapsed_ms": elapsed, "error": error,
                                "status": raw.get("status"),
                                "columns": {c.get("purpose"): {"status": c.get("status")}
                                            for c in raw.get("columns", []) if isinstance(c, dict)}})
        print(f"[{index}/{len(tasks)}] {task['type']} {task['query']!r} {elapsed}ms "
              f"status={raw.get('status')} error={error}", flush=True)
        args.out.write_text(json.dumps(package, ensure_ascii=False, indent=1), encoding="utf8")

    cost = None if totals["unknown"] else estimate_rmb(totals["prompt_tokens"], totals["completion_tokens"])
    summary = {"tasks": len(tasks), "requests": totals["requests"],
               "prompt_tokens": totals["prompt_tokens"], "completion_tokens": totals["completion_tokens"],
               "unknown_usage": totals["unknown"], "estimated_rmb": cost,
               "data_dir": str(config.data_dir()), "responses": str(args.out)}
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
