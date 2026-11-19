"""Local image/folder import: file mtime as saved time + optional HEIC conversion.

No real folder watcher or platform request: tests use temp imports and Pillow bytes.
"""
import hashlib
import io
import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import db, importer
from app.main import create_app
from conftest import counts, png


def iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000.0).astimezone().isoformat(timespec="seconds")


def jpeg_bytes(color="blue") -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), color).save(buffer, format="JPEG", quality=92)
    return buffer.getvalue()


def fake_heic() -> bytes:
    """ISO-BMFF magic only; the converter is faked in the success-path test."""
    return b"\x00\x00\x00\x18ftypheic" + b"\x00" * 32


@pytest.fixture
def lib(data_dir):
    db.init_db()
    return data_dir


@pytest.fixture
def client(data_dir, backend_options):
    with TestClient(create_app(), backend_options=backend_options) as c:
        yield c


def upload(client, files, modified=None):
    data = {"modified": modified} if modified is not None else {}
    response = client.post(
        "/api/images/import",
        files=[("files", (name, content, "application/octet-stream")) for name, content in files],
        data=data,
    )
    assert response.status_code == 200, response.text
    return response


def rows(lib, sql, args=()):
    conn = sqlite3.connect(lib / "app.db")
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(sql, args).fetchall()
    finally:
        conn.close()


def test_file_mtime_becomes_image_and_post_created_at(lib):
    first = 1714546984000          # 2024-05-01T12:03:04+09:00 in this environment
    second = first + 3600 * 1000
    result = importer.import_images([
        importer.Upload("a.png", png("red"), modified_at=first / 1000.0),
        importer.Upload("b.png", png("blue"), modified_at=second / 1000.0),
    ])
    assert result["post_id"] is not None
    assert [a["created_at"] for a in result["added"]] == [iso(first), iso(second)]
    images = rows(lib, "SELECT original_name, created_at FROM images ORDER BY position")
    assert [i["original_name"] for i in images] == ["a.png", "b.png"]
    assert [i["created_at"] for i in images] == [iso(first), iso(second)]
    post = rows(lib, "SELECT created_at FROM posts WHERE platform = 'local'")[0]
    assert post["created_at"] == iso(first)  # posts.created_at = first file of the batch


def test_second_batch_mtime_and_duplicates_create_no_extra_post(lib):
    first = importer.import_images([importer.Upload("old.png", png("red"))])
    duplicate_ms = 1714546984000
    new_ms = duplicate_ms + 60 * 60 * 1000
    result = importer.import_images([
        importer.Upload("red-copy.png", png("red"), modified_at=duplicate_ms / 1000.0),
        importer.Upload("new.png", png("blue"), modified_at=new_ms / 1000.0),
        importer.Upload("notes.txt", b"not an image"),
    ])
    assert [a["original_name"] for a in result["added"]] == ["new.png"]
    assert result["duplicates"][0]["existing_image_id"] == first["added"][0]["id"]
    assert len(result["failed"]) == 1
    assert counts(lib) == (2, 2, 2)  # the duplicate wrote no row/file/post
    posts = rows(lib, "SELECT created_at FROM posts ORDER BY id")
    assert posts[0]["created_at"] != iso(new_ms)
    # "本批第一张" is red-copy.png even though it is a duplicate.
    assert posts[1]["created_at"] == iso(duplicate_ms)
    image = rows(lib, "SELECT file_path FROM images WHERE original_name = 'new.png'")[0]
    assert (lib / image["file_path"]).exists()


def test_heic_without_plugin_fails_with_actionable_reason(lib, monkeypatch):
    monkeypatch.setattr(importer, "_heic_opener", lambda: None)  # deterministic even if installed
    assert importer._is_heic(fake_heic())
    result = importer.import_images([importer.Upload("photo.heic", fake_heic())])
    assert result["added"] == [] and result["post_id"] is None
    reason = result["failed"][0]["reason"]
    assert "HEIC" in reason and "pillow-heif" in reason
    assert counts(lib) == (0, 0, 0)


def test_heic_conversion_success_keeps_name_and_dedupes_by_converted_sha(lib, monkeypatch):
    converted = jpeg_bytes()

    def fake_convert(data):
        return converted, None

    monkeypatch.setattr(importer, "_convert_heic", fake_convert)
    result = importer.import_images([
        importer.Upload("photo.heic", fake_heic()),
        importer.Upload("photo-copy.heic", fake_heic()),
    ])
    assert len(result["added"]) == 1 and len(result["duplicates"]) == 1
    added = result["added"][0]
    assert added["original_name"] == "photo.heic"
    assert added["url"].endswith(".jpg")
    assert added["sha256"] == hashlib.sha256(converted).hexdigest()
    image = rows(lib, "SELECT file_path, mime, original_name, sha256 FROM images")[0]
    assert (image["mime"], image["original_name"]) == ("image/jpeg", "photo.heic")
    assert image["sha256"] == hashlib.sha256(converted).hexdigest()
    stored = (lib / image["file_path"]).read_bytes()
    assert stored == converted and stored[:2] == b"\xff\xd8"  # JPEG bytes stored, not HEIC


def test_real_heic_fixture_is_converted_to_jpeg_and_deduped(client, data_dir):
    """The 627-byte fixture is a real HEIC (brand heic); pillow-heif is now installed."""
    heic = (Path(__file__).parent / "fixtures" / "sample.heic").read_bytes()
    response = upload(client, [("sample.heic", heic)], modified=json.dumps([1714546984000]))
    body = response.json()
    assert len(body["added"]) == 1 and body["duplicates"] == [] and body["failed"] == []
    added = body["added"][0]
    assert added["original_name"] == "sample.heic" and added["url"].endswith(".jpg")

    image = rows(data_dir, "SELECT file_path, mime, width, height, original_name FROM images")[0]
    assert (image["mime"], image["width"], image["height"], image["original_name"]) == (
        "image/jpeg", 32, 24, "sample.heic")
    assert image["file_path"].endswith(".jpg")
    stored = (data_dir / image["file_path"]).read_bytes()
    assert stored[:3] == b"\xff\xd8\xff"  # JPEG SOI + APP0, not the HEIC bytes
    assert stored != heic

    posts_before = rows(data_dir, "SELECT COUNT(*) FROM posts")[0][0]
    again = upload(client, [("sample-copy.heic", heic)]).json()
    assert again["added"] == [] and len(again["duplicates"]) == 1 and again["post_id"] is None
    assert rows(data_dir, "SELECT COUNT(*) FROM posts")[0][0] == posts_before


def test_http_modified_json_sets_mtime(client, data_dir):
    ms = 1714546984000
    response = upload(client, [("a.png", png("red"))], modified=json.dumps([ms]))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["added"][0]["created_at"] == iso(ms)
    stored = rows(data_dir, "SELECT i.created_at AS image_created, p.created_at AS post_created "
                            "FROM images i JOIN posts p ON p.id = i.post_id")[0]
    assert stored["image_created"] == iso(ms)
    assert stored["post_created"] == iso(ms)


@pytest.mark.parametrize("modified", ["not-json", "{", json.dumps([1, 2, 3]), json.dumps([])])
def test_http_bad_modified_json_never_500(client, data_dir, modified):
    response = upload(client, [("a.png", png("red"))], modified=modified)
    assert response.status_code == 200, response.text
    created = response.json()["added"][0]["created_at"]
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", created)


def test_http_modified_length_mismatch_and_negative_use_import_time(client, data_dir):
    response = upload(client, [("a.png", png("red")), ("b.png", png("blue"))],
                      modified=json.dumps([1714546984000]))
    assert response.status_code == 200, response.text
    assert len(response.json()["added"]) == 2
    for item in response.json()["added"]:
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", item["created_at"])
    negative = upload(client, [("c.png", png("green"))], modified=json.dumps([-5]))
    assert negative.status_code == 200, negative.text
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}",
                        negative.json()["added"][0]["created_at"])
