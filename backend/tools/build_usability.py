#!/usr/bin/env python
"""Build eval/usability.json from a saved f4_live package (default: the one-shot test run).

No model calls: only saved responses + read-only image paths. Existing clicks are
merged back, so rebuilding after a page tweak does not lose the user's verdicts.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND.parent
sys.path.insert(0, str(BACKEND))

from app import config, usability  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--responses", type=Path,
                        default=PROJECT_ROOT.parent / "_f4_runs" / "final-test.json")
    parser.add_argument("--tasks", type=Path, default=PROJECT_ROOT / "eval" / "tasks.json")
    parser.add_argument("--out", type=Path, default=usability.default_path())
    parser.add_argument("--split", default="test")
    args = parser.parse_args(argv)

    raw = json.loads(args.responses.read_text(encoding="utf-8-sig"))
    responses = raw.get("responses", raw)
    tasks = json.loads(args.tasks.read_text(encoding="utf-8-sig"))["tasks"]
    selected = [t for t in tasks if t.get("type") == "create" and t.get("split") == args.split]
    payload = usability.build(responses, selected, config.db_path(), source={
        "split": args.split, "responses": str(args.responses),
        "created_at": raw.get("created_at") if isinstance(raw, dict) else None,
        "note": "显示的是冷冻配置一次性 test 运行的三栏结果；本页只收人工能否使用，不调参",
    })
    if args.out.exists():
        payload = usability.merge_votes(usability.read(args.out), payload)
    usability.write(args.out, payload)
    summary = usability.summary(payload)
    print(json.dumps({"tasks": [t["query"] for t in payload["tasks"]],
                      "summary": summary}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
