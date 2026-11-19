"""给已有图片补短标题：用千问文字模型，从分析描述重新生成 12 字以内的完整短语。

只写 analyses.title；有人工标题（image_titles_manual）的图一律跳过。
默认只看不改（打印「现在的标题 → 新标题」），加 --apply 才落库。

用法（在 backend/ 下）：
    python -m scripts.backfill_titles --data-dir ../.run/public-demo-titles --limit 10
    python -m scripts.backfill_titles --data-dir ../.run/public-demo-titles --apply
"""
from __future__ import annotations

import argparse
import os
import sys
import time


def current_caption(description: str | None, tags: list[str] | None) -> str:
    """和前端 frontend/src/main/caption.ts 的 shortTitle 同一套规则，用来显示「现在卡片上的标题」。"""
    import re

    list_ = [tag for tag in (tags or []) if tag]
    classifier = re.compile(r"^(?:一|两|几)[张幅份个只位件组款](?=\S)")
    phrase = (description or "").strip()
    phrase = re.sub(r"^(?:这[是张幅个]|图中|画面(?:中|里|中央|主体)?)[，、：:\s]*", "", phrase)
    phrase = classifier.sub("", phrase)
    phrase = re.sub(r"^(?:是|为|展示|呈现|描绘|可以看到|主要是)(?:了|着)?\s*", "", phrase)
    phrase = classifier.sub("", phrase)
    phrase = re.split(r"[，。；：！？、,.!?;:\n]|(?:上|中|里)?(?:呈现|描绘|展示|包含|采用|使用|形成|构成|位于|坐在|出现在)|(?:在)(?=[^\s，。]{2,})",
                      phrase, maxsplit=1)[0]
    phrase = phrase.strip()
    phrase = re.sub(r"[的和与及]$", "", phrase)
    if not phrase or re.match(r"^(?:无法|不明|未知|没有|未见|图中|画面)", phrase):
        return " · ".join(list_[:2])
    return _cut(phrase, 10)


def _cut(text: str, length: int) -> str:
    chars = list(text)
    prefix = "".join(chars[:length]).rstrip()
    if not re_end_letter(prefix) or not re_start_letter("".join(chars[length:])):
        return prefix
    import re

    trimmed = re.sub(r"[A-Za-z][A-Za-z'’-]*$", "", prefix).rstrip()
    return trimmed or prefix


def re_end_letter(text: str) -> bool:
    import re

    return bool(re.search(r"[A-Za-z]$", text))


def re_start_letter(text: str) -> bool:
    import re

    return bool(re.match(r"[A-Za-z]", text))


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="用文字模型给图片补短标题（默认 dry-run）")
    parser.add_argument("--data-dir", help="数据目录（默认用 WOMINMIN_DATA_DIR 或 ../data）")
    parser.add_argument("--limit", type=int, default=0, help="最多处理多少张（0 = 全部）")
    parser.add_argument("--apply", action="store_true", help="真的写库；不加只打印")
    parser.add_argument("--force", action="store_true", help="已经有自动标题的也重新生成")
    parser.add_argument("--model", help="覆盖 TITLE_MODEL")
    parser.add_argument("--sleep", type=float, default=0.0, help="每次请求之间等几秒（默认 0）")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    if args.data_dir:
        os.environ["WOMINMIN_DATA_DIR"] = os.path.abspath(args.data_dir)

    # app.* 必须在上面的环境变量之后导入：config.data_dir() 是调用时读环境变量的。
    from app import db, titles

    settings = titles.TextSettings.from_env()
    if args.model:
        settings = titles.TextSettings(settings.base_url, settings.api_key, args.model.strip())
    if not settings.base_url or not settings.api_key:
        print("没有配置文字模型：AI_BASE_URL / AI_API_KEY 为空，什么都没做。", file=sys.stderr)
        return 2

    print("数据目录：" + str(db.config.data_dir()))
    print("模型：" + settings.model + ("（只打印，不写库）" if not args.apply else "（会写库）"))

    db.init_db()  # 会把数据副本升到 v14（首次还会在同目录留一份 app.db.bak-* 备份）
    conn = db.connect()
    try:
        rows = conn.execute(
            """SELECT i.id AS image_id, i.post_id, a.description, a.title AS auto_title,
                      m.title AS manual_title, p.title AS post_title
               FROM images i
               JOIN analyses a ON a.image_id = i.id
               LEFT JOIN image_titles_manual m ON m.image_id = i.id
               LEFT JOIN posts p ON p.id = i.post_id
               WHERE a.status = 'done' AND a.description IS NOT NULL AND TRIM(a.description) <> ''
               ORDER BY i.post_id DESC, i.position ASC, i.id ASC""").fetchall()
    finally:
        conn.close()

    picked, skipped = [], {"manual": 0, "done": 0}
    for row in rows:
        if row["manual_title"]:
            skipped["manual"] += 1
            continue
        if row["auto_title"] and not args.force:
            skipped["done"] += 1
            continue
        if args.limit and len(picked) >= args.limit:
            break
        picked.append(row)

    print("待处理 " + str(len(picked)) + " 张；跳过人工标题 " + str(skipped["manual"])
          + " 张、已有自动标题 " + str(skipped["done"]) + " 张")
    if not picked:
        return 0

    import httpx

    done = failed = 0
    with httpx.Client(timeout=settings.timeout) as client:
        for index, row in enumerate(picked, 1):
            description = row["description"] or ""
            try:
                title = titles.generate(description, settings=settings, client=client)
            except titles.TitleError as exc:
                failed += 1
                print(f"{index:>3}. 图 {row['image_id']:>4} 生成失败：{exc}")
                continue
            done += 1
            before = row["manual_title"] or row["auto_title"] or current_caption(description, None) or (
                row["post_title"] or "")
            print(f"{index:>3}. 图 {row['image_id']:>4} | 原标题：{before or '（空）'} | 新标题：{title}")
            print("     描述：" + description[:60].replace("\n", " "))
            if args.apply:
                titles.store_generated(row["image_id"], title, model=settings.model)
            if args.sleep:
                time.sleep(args.sleep)

    print("完成：成功 " + str(done) + " 张，失败 " + str(failed) + " 张"
          + ("" if args.apply else "（dry-run，没有写库）"))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
