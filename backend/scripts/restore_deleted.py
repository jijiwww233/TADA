#!/usr/bin/env python
"""恢复被软删除的图片 / 项目（只清 deleted_at，不动文件和其他记录）。

软删除只写 images.deleted_at / projects.deleted_at；图片文件、pins、分析、向量全都保留。
本脚本把这些标记清回 NULL，列表/详情/搜索/相似立刻重新出现（服务不需要重启）。

用法（在 backend/ 下）：
    python -m scripts.restore_deleted --list                          # 看有哪些被删的
    python -m scripts.restore_deleted --all                           # 恢复全部图片 + 项目
    python -m scripts.restore_deleted --images                        # 只恢复全部图片
    python -m scripts.restore_deleted --projects                      # 只恢复全部项目
    python -m scripts.restore_deleted --ids 12,34                     # 恢复指定 id（两张表都查）
    python -m scripts.restore_deleted --ids 12 --images               # 指定 id 只恢复图片
    python -m scripts.restore_deleted --list --projects               # 只看被删项目

指定数据目录（不会碰默认库）：
    python -m scripts.restore_deleted --data-dir ../.run/public-demo --list
"""
from __future__ import annotations

import argparse
import os
import sys


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="恢复软删除的图片 / 项目（清掉 deleted_at，可重复执行）")
    parser.add_argument("--data-dir", help="数据目录（默认 WOMINMIN_DATA_DIR 或 ../data）")
    parser.add_argument("--list", action="store_true", help="只列出当前被软删除的条目，不改库")
    parser.add_argument("--all", action="store_true", help="恢复全部（默认图片 + 项目）")
    parser.add_argument("--images", action="store_true", help="只处理图片")
    parser.add_argument("--projects", action="store_true", help="只处理项目")
    parser.add_argument("--ids", help="逗号分隔的 id，例如 12,34（默认图片和项目都查）")
    return parser.parse_args(argv)


def _parse_ids(raw: str) -> list[int]:
    values = []
    for part in raw.replace("，", ",").split(","):
        part = part.strip()
        if not part:
            continue
        if not part.isdigit() or int(part) <= 0:
            raise SystemExit(f"--ids 里有无效 id：{part!r}（只接受正整数）")
        values.append(int(part))
    if not values:
        raise SystemExit("--ids 不能为空")
    return list(dict.fromkeys(values))


def _tables(args) -> list[str]:
    """要处理的表；--images/--projects 圈定范围，都没给时两张表都算。"""
    tables = []
    if args.images:
        tables.append("images")
    if args.projects:
        tables.append("projects")
    return tables or ["images", "projects"]


def _where(tables: list[str], ids: list[int] | None):
    """每张表的 WHERE 子句和参数；ids 存在时按 id 收窄。"""
    if ids:
        marks = ",".join("?" for _ in ids)
        return [f"deleted_at IS NOT NULL AND id IN ({marks})" for _ in tables], list(ids)
    return ["deleted_at IS NOT NULL"] * len(tables), []


def list_deleted(conn, tables: list[str], ids: list[int] | None) -> int:
    wheres, params = _where(tables, ids)
    total = 0
    for table, where in zip(tables, wheres):
        if table == "images":
            rows = conn.execute(
                f"""SELECT id, original_name, file_path, created_at, deleted_at
                    FROM images WHERE {where} ORDER BY deleted_at, id""", params).fetchall()
            print(f"被软删除的图片：{len(rows)} 张")
            for row in rows:
                name = row["original_name"] or ""
                print(f"  图片 #{row['id']:<5} {name:<24} {row['file_path']:<40} "
                      f"创建 {row['created_at']}｜删除 {row['deleted_at']}")
            total += len(rows)
        else:
            rows = conn.execute(
                f"""SELECT id, name, created_at, deleted_at FROM projects
                    WHERE {where} ORDER BY deleted_at, id""", params).fetchall()
            print(f"被软删除的项目：{len(rows)} 个")
            for row in rows:
                print(f"  项目 #{row['id']:<5} {row['name']:<24} "
                      f"创建 {row['created_at']}｜删除 {row['deleted_at']}")
            total += len(rows)
    return total


def restore(conn, tables: list[str], ids: list[int] | None) -> int:
    wheres, params = _where(tables, ids)
    restored = 0
    for table, where in zip(tables, wheres):
        before = conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}", params).fetchone()[0]
        if not before:
            print(f"{table}: 没有需要恢复的行")
            continue
        conn.execute(f"UPDATE {table} SET deleted_at = NULL WHERE {where}", params)
        restored += before
        label = "图片" if table == "images" else "项目"
        print(f"{label}: 恢复 {before} 个（{table}.deleted_at 已清空）")
    return restored


def main(argv=None) -> int:
    args = parse_args(argv)
    if args.all and args.ids:
        raise SystemExit("--all 和 --ids 不能同时用：要么全部，要么指定 id。")
    if args.data_dir:
        os.environ["WOMINMIN_DATA_DIR"] = os.path.abspath(args.data_dir)

    # app.* 必须在设置 WOMINMIN_DATA_DIR 之后导入：config.data_dir() 是调用时读环境变量。
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from app import db

    print("数据目录：" + str(db.config.data_dir()))
    db.init_db()  # 老库直接升到当前版本（首次会留 app.db.bak-* 备份），幂等
    tables = _tables(args)
    ids = _parse_ids(args.ids) if args.ids else None
    restoring = bool(args.all or args.images or args.projects or args.ids)

    conn = db.connect()
    try:
        if args.list or not restoring:
            total = list_deleted(conn, tables, ids)
            if not restoring:
                if total:
                    print("用 --all / --images / --projects / --ids 恢复；加 --list 只查看。")
                else:
                    print("没有软删除的条目。")
            return 0
        conn.execute("BEGIN IMMEDIATE")
        try:
            restored = restore(conn, tables, ids)
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()
    print(f"完成：共恢复 {restored} 个；文件和分析记录都没有改动。")
    return 0 if restored else 1


if __name__ == "__main__":
    raise SystemExit(main())
