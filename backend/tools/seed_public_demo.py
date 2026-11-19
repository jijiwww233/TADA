"""Seed the public library with approved image files only."""
import argparse
from contextlib import closing
from io import BytesIO
import sqlite3

from PIL import Image, ImageOps

from tools.public_demo import ROOT, SAMPLES, configure_demo

FORMATS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".heic", ".heif"}


def public_bytes(path):
    data = path.read_bytes()
    if path.suffix.lower() in {".heic", ".heif"}:
        return data  # the importer converts HEIC without copying metadata
    with Image.open(BytesIO(data)) as picture:
        metadata = (picture.getexif() or picture.info.get("xmp")
                    or picture.info.get("comment") or getattr(picture, "text", None))
        if not metadata:
            return data
        if getattr(picture, "is_animated", False):
            raise ValueError("请先导出不带元数据的动图")
        displayed = ImageOps.exif_transpose(picture)
        mode = "RGBA" if displayed.mode == "P" or "transparency" in displayed.info else displayed.mode
        cleaned = Image.new(mode, displayed.size)
        cleaned.paste(displayed.convert(mode))
        output = BytesIO()
        cleaned.save(output, format=picture.format)
        return output.getvalue()


def local_library_images():
    root = (ROOT / "data" / "images").resolve()
    original = (ROOT / "data" / "app.db").resolve()
    with closing(sqlite3.connect(original.as_uri() + "?mode=ro", uri=True)) as conn:
        rows = conn.execute("SELECT file_path FROM images ORDER BY id").fetchall()
    candidates = [(ROOT / "data" / row[0]).resolve() for row in rows]
    if any(not path.is_relative_to(root) or not path.is_file() for path in candidates):
        raise ValueError("本机库有图片文件缺失或不在图库目录，已停止复制")
    return candidates


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--include-local-library", action="store_true")
    args = parser.parse_args()
    configure_demo()
    from app import db, importer

    db.init_db()
    sample_root = SAMPLES.resolve()
    samples = sorted(path for path in SAMPLES.rglob("*") if path.is_file()
                     and path.suffix.lower() in FORMATS and path.resolve().is_relative_to(sample_root))
    local = local_library_images() if args.include_local_library else []
    for label, paths in (("示例图", samples), ("本机图库", local)):
        totals = {"added": 0, "duplicates": 0, "failed": 0}
        for index, path in enumerate(paths, 1):
            try:
                data = public_bytes(path)
            except (OSError, ValueError):
                totals["failed"] += 1
                continue
            upload = importer.Upload(f"{label}-{index:03d}{path.suffix.lower()}", data)
            result = importer.import_images([upload])
            for key in totals:
                totals[key] += len(result[key])
        print(f"{label}：新增 {totals['added']} 张，重复 {totals['duplicates']} 张，失败 {totals['failed']} 张")
        if totals["failed"]:
            raise SystemExit("部分图片无法导入，请检查图片格式或元数据。")


if __name__ == "__main__":
    main()
