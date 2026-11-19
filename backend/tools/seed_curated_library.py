"""Seed a new local library from the approved demo assets and reviewed archive images."""
import argparse
from contextlib import closing
from hashlib import sha256
from io import BytesIO
import json
import mimetypes
from pathlib import Path
import re
import sqlite3

from PIL import Image, ImageOps

from app import config, db

REVIEWED_ARCHIVE_IDS = (4, 9, 10, 150, 152, 153, 154)
EXCLUDED_ARCHIVE_IDS = {1, 3, 5, 186, 234, 265, 299, 353}
PROJECT_POSTS = (176, 181, 180, 172, 175, 174, 169)
SENSITIVE = re.compile(
    r"https?[:/]|www\.|@|(?:\d{1,3}\.){3}\d{1,3}|(?:\+?\d[\s-]*){9,}"
    r"|api.?key|token|密码|手机号|用户名|微信号|身份证|[A-Z]:\\", re.I
)
PLATFORMS = {"本地": "local", "站酷": "zcool", "小红书": "xhs", "Pinterest": "pinterest", "花瓣": "huaban"}


def safe_text(value):
    return value if isinstance(value, str) and value.strip() and not SENSITIVE.search(value) else None


def safe_json(raw):
    if not raw:
        return None
    value = json.loads(raw)
    if isinstance(value, list):
        return json.dumps([item for item in value if safe_text(item)], ensure_ascii=False)
    if isinstance(value, dict):
        for key in list(value):
            field = value[key]
            if isinstance(field, str) and safe_text(field):
                continue
            if isinstance(field, list):
                value[key] = [item for item in field if safe_text(item)]
            else:
                del value[key]
        return json.dumps(value, ensure_ascii=False)
    return None


def preserve_pixels_without_metadata(path):
    output = BytesIO()
    with Image.open(path) as image:
        if getattr(image, "n_frames", 1) != 1:
            raise ValueError("Review animated archive assets separately")
        clean = ImageOps.exif_transpose(image).convert("RGBA" if image.has_transparency_data else "RGB")
        profile = image.info.get("icc_profile")
        clean.info.clear()
        clean.save(output, format="PNG", icc_profile=profile)
        return output.getvalue(), clean.size


def import_library(archive, public):
    manifest = json.loads((public / "lab-assets/show/show.json").read_text(encoding="utf-8"))
    archive_files = {p.name: p for p in (archive.parent / "images").rglob("*") if p.is_file()}
    with closing(sqlite3.connect(archive.resolve().as_uri() + "?mode=ro&immutable=1", uri=True)) as source:
        source.row_factory = sqlite3.Row
        records = {row["id"]: row for row in source.execute(
            "SELECT i.*, p.platform FROM images i JOIN posts p ON p.id=i.post_id"
        )}
        by_digest = {row["sha256"]: row["id"] for row in records.values()}
        post_members = {}
        groups = manifest["libraryProjects"]
        if len(groups) != len(PROJECT_POSTS):
            raise ValueError("Approved groups have changed")
        for group, old_post in zip(groups, PROJECT_POSTS):
            source_ids = list(dict.fromkeys(row[0] for row in source.execute(
                "SELECT image_id FROM post_images WHERE post_id=? AND image_id IS NOT NULL ORDER BY position",
                (old_post,),
            )))
            for position, (demo_id, original_id) in enumerate(zip(group["imageIds"], source_ids, strict=True)):
                post_members[demo_id] = (group["name"], position, original_id)

        db.init_db()
        with closing(db.connect()) as target, target:
            if any(target.execute("SELECT EXISTS(SELECT 1 FROM images), EXISTS(SELECT 1 FROM projects)").fetchone()):
                raise ValueError("Seed requires an empty target library")
            image_map = {}
            imported_source_ids = set()
            post_map = {}
            restored = 0

            def save(payload, size, extension, platform, group, pos, original_id, palette):
                nonlocal restored
                if original_id in EXCLUDED_ARCHIVE_IDS:
                    raise ValueError("An excluded image is in the approved set")
                title = group
                if group is not None and group in post_map:
                    post_id = post_map[group]
                else:
                    post_id = target.execute("INSERT INTO posts(platform,title) VALUES (?,?)", (platform, title)).lastrowid
                    if group is not None:
                        post_map[group] = post_id
                digest = sha256(payload).hexdigest()
                filename = f"{digest}{extension}"
                file_path = f"images/{filename}"
                image_id = target.execute(
                    """INSERT INTO images(post_id,position,sha256,file_path,width,height,mime,original_name)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (post_id, pos, digest, file_path, *size, mimetypes.guess_type(filename)[0], None),
                ).lastrowid
                target.execute("INSERT INTO post_images(post_id,position,image_id) VALUES (?,?,?)", (post_id, pos, image_id))
                (config.images_dir() / filename).write_bytes(payload)
                state = source.execute(
                    "SELECT description,subject,layout_json,style_json FROM analyses WHERE image_id=? AND status='done'",
                    (original_id,),
                ).fetchone()
                if state:
                    target.execute(
                        """INSERT INTO analyses(image_id,status,description,subject,layout_json,style_json)
                           VALUES (?,'done',?,?,?,?)""",
                        (image_id, safe_text(state["description"]), safe_json(state["subject"]),
                         safe_json(state["layout_json"]), safe_json(state["style_json"])),
                    )
                    restored += 1
                for field, col in (("description", "description"), ("tags_json", "tags_json")):
                    if original_id is None or not source.execute(
                        "SELECT 1 FROM sqlite_master WHERE name='image_annotations'"
                    ).fetchone():
                        continue
                    annotation = source.execute(f"SELECT {col} FROM image_annotations WHERE image_id=?", (original_id,)).fetchone()
                    value = (safe_json if field == "tags_json" else safe_text)(annotation[0]) if annotation else None
                    if value:
                        target.execute(
                            f"""INSERT INTO image_annotations(image_id,{field}) VALUES (?,?)
                                ON CONFLICT(image_id) DO UPDATE SET {field}=excluded.{field}""", (image_id, value)
                        )
                colors = source.execute("SELECT rank,hex,ratio FROM colors WHERE image_id=? ORDER BY rank", (original_id,)).fetchall()
                if not colors:
                    colors = [(rank, item["hex"], item["ratio"]) for rank, item in enumerate(palette)]
                target.executemany("INSERT INTO colors(image_id,rank,hex,ratio) VALUES (?,?,?,?)",
                                   [(image_id, rank, color, ratio) for rank, color, ratio in colors])
                target.execute("INSERT INTO image_reference_labels(image_id,manual_category,confirmed) VALUES (?,'visual_ref',1)", (image_id,))
                imported_source_ids.add(original_id)
                return image_id

            rows = [r for r in manifest["items"] if not r["face"]] + manifest["libraryItems"] + manifest["projectItems"]
            # Write the grouped material first and the gallery picks afterwards.
            rows.sort(key=lambda r: (r["id"] not in post_members, r["src"].startswith("/lab-assets/show/")))
            for row in rows:
                path = public / row["src"].lstrip("/")
                payload = path.read_bytes()
                group, pos, original_id = post_members.get(
                    row["id"], (None, 0, by_digest.get(sha256(payload).hexdigest()))
                )
                platform = records[original_id]["platform"] if original_id is not None else row.get("platform") or PLATFORMS[row["source"]]
                image_map[row["id"]] = save(payload, (row["w"], row["h"]), path.suffix, platform,
                                            group, pos, original_id, row["palette"])
            for original_id in REVIEWED_ARCHIVE_IDS:
                if original_id in imported_source_ids:
                    continue
                record = records[original_id]
                path = archive_files[Path(record["file_path"]).name]
                if sha256(path.read_bytes()).hexdigest() != record["sha256"]:
                    raise ValueError("Source image checksum mismatch")
                payload, size = preserve_pixels_without_metadata(path)
                save(payload, size, ".png", record["platform"], None, 0, original_id, [])
            for group in groups:
                project_id = target.execute("INSERT INTO projects(name) VALUES (?)", (group["name"],)).lastrowid
                target.executemany("INSERT INTO pins(project_id,image_id) VALUES (?,?)",
                                   [(project_id, image_map[item_id]) for item_id in group["imageIds"]])
            print(f"Library images: {target.execute('SELECT count(*) FROM images').fetchone()[0]}, restored analyses: {restored}, projects: {len(groups)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("public", type=Path)
    args = parser.parse_args()
    import_library(args.archive, args.public)
