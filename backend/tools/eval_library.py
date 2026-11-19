#!/usr/bin/env python
"""Generate docs/library_overview.md from the real library, read-only.

Usage from backend/:
  python tools/eval_library.py [--db PATH] [--out PATH]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND.parent
sys.path.insert(0, str(BACKEND))

from app import config  # noqa: E402
from app.eval_api import load_library  # noqa: E402


def render(items: list[dict]) -> str:
    lines = [
        "# 图库概览（done 图片）",
        "",
        f"- 数据源：真实本地库只读读取",
        f"- done 图片：{len(items)} 张",
        "- 用途：评测标注时按 image_id 查找；不含 pending / failed。",
        "",
        "| image_id | 来源平台 | 主题 subject | 简述 | 主色（前 5） |",
        "|---:|---|---|---|---|",
    ]
    for item in items:
        subject = "、".join(item.get("subject") or []) or "（空）"
        brief = (item.get("description") or item.get("post_title") or "").replace("|", "\\|").replace("\n", " ")
        if len(brief) > 120:
            brief = brief[:117] + "…"
        colors = " ".join(f"{c.get('hex')}({c.get('ratio')})" for c in (item.get("colors") or []))
        lines.append(f"| {item['image_id']} | {', '.join(item.get('platforms', [])) or '未知'} | {subject.replace('|','\\|')} | {brief} | {colors} |")
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(config.db_path()))
    parser.add_argument("--out", default=str(PROJECT_ROOT / "docs" / "library_overview.md"))
    args = parser.parse_args(argv)
    items = load_library(Path(args.db))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(items), encoding="utf-8")
    print(f"wrote {out} ({len(items)} done images)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
