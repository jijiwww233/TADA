"""Tests for v4 migration and the post-level classification application service."""
from __future__ import annotations

import json
import sqlite3

import pytest

from app import db
from app.classification_service import (
    ClassificationService,
    ClassificationServiceError,
    build_post_document,
    classify_posts,
    clear_manual,
    effective_post_categories,
    filter_image_ids,
    load_automatic,
    load_manual,
    set_manual,
    visible_image_ids,
    visible_images,
)
from app.collection_classification import Assessment, Claim, Evidence


class FakeBackend:
    def __init__(self, categories=None, *, fail=False, version="fake-v1"):
        self.categories = categories or {}
        self.fail = fail
        self.classifier_version = version
        self.calls = 0
        self.documents = []

    def predict_batch(self, documents):
        self.documents.extend(documents)

    def classify(self, document):
        self.calls += 1
        if self.fail:
            raise RuntimeError("provider secret should not leak")
        title = document["post"]["title"]
        category = self.categories.get(title, "design_reference")
        if category == "uncertain":
            return Assessment()
        return Assessment((Claim(category, 0.9, (Evidence("post.title", title, "intent"),)),))


def seed(data_dir):
    db.init_db()
    conn = db.connect()
    posts = [
        (1, "建筑设计参考", "展板排版参考"),
        (2, "今天吃了火锅", "生活记录"),
        (3, "随手拍", "不确定内容"),
    ]
    for post_id, title, description in posts:
        conn.execute(
            "INSERT INTO posts (id, platform, source_id, title, description) VALUES (?, 'xhs', ?, ?, ?)",
            (post_id, f"work-{post_id}", title, description),
        )
    conn.execute("INSERT INTO images (id, post_id, position, sha256, file_path) VALUES "
                 "(1, 1, 0, ?, 'images/shared.jpg'), (2, 2, 0, ?, 'images/life.jpg')",
                 ("a" * 64, "b" * 64))
    conn.execute("INSERT INTO post_images (post_id, position, image_id, download_state) VALUES "
                 "(1, 0, 1, 'ok'), (2, 0, 1, 'ok'), (2, 1, 2, 'ok')")
    for image_id in (1, 2):
        conn.execute(
            "INSERT INTO analyses (image_id, status, subject, description, layout_json, style_json, visible_text) "
            "VALUES (?, 'done', ?, 'd', ?, ?, '文字')",
            (image_id, '["建筑"]', '{"columns":["single"]}', '{"tags":["极简"]}'),
        )
    conn.commit()
    conn.close()


def categories_by_title():
    return {"建筑设计参考": "design_reference",
            "今天吃了火锅": "life",
            "随手拍": "uncertain"}


def test_v3_to_v4_migration_preserves_data_and_creates_empty_tables(data_dir):
    conn = db.connect()
    conn.executescript(db.SCHEMA)
    for steps in db.MIGRATIONS[:3]:
        for sql in steps:
            conn.execute(sql)
    conn.execute("PRAGMA user_version = 3")
    conn.execute("INSERT INTO posts (id, platform, source_id, title) VALUES (1, 'xhs', 'x', 'old')")
    conn.execute("INSERT INTO images (id, post_id, position, sha256, file_path) VALUES (1, 1, 0, ?, 'images/x.jpg')",
                 ("c" * 64,))
    conn.commit()
    conn.close()

    db.init_db()
    conn = db.connect()
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    assert conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM images").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM post_classifications").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM post_classification_manual").fetchone()[0] == 0
    names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    assert "idx_post_classifications_category" in names
    assert "idx_post_classification_manual_category" in names
    conn.close()
    db.init_db()  # idempotent


def test_no_automatic_classification_at_startup(data_dir):
    seed(data_dir)
    conn = db.connect()
    assert conn.execute("SELECT COUNT(*) FROM post_classifications").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM post_classification_manual").fetchone()[0] == 0
    conn.close()


def test_manual_override_wins_and_automatic_never_touches_it(data_dir):
    seed(data_dir)
    conn = db.connect()
    service = ClassificationService(conn)
    backend = FakeBackend(categories_by_title())
    assert service.classify([1], backend).ok == 1
    assert effective_post_categories(conn, [1])[1] == {"category": "design_reference", "source": "automatic"}
    set_manual(conn, 1, "life", "我觉得这是生活")
    conn.commit()
    assert effective_post_categories(conn, [1])[1] == {"category": "life", "source": "manual"}

    backend2 = FakeBackend({"建筑设计参考": "art_reference"}, version="fake-v2")
    run = service.classify([1], backend2)
    assert run.ok == 1
    assert effective_post_categories(conn, [1])[1] == {"category": "life", "source": "manual"}
    assert load_manual(conn, [1])[1]["category"] == "life"
    assert load_automatic(conn, [1])[1]["category"] == "art_reference"
    conn.close()


def test_failure_does_not_destroy_existing_automatic_or_manual(data_dir):
    seed(data_dir)
    conn = db.connect()
    service = ClassificationService(conn)
    service.classify([1], FakeBackend(categories_by_title()))
    set_manual(conn, 1, "life")
    conn.commit()
    failed = service.classify([1], FakeBackend(categories_by_title(), fail=True, version="fake-fail"))
    assert failed.backend_error == 1
    assert load_automatic(conn, [1])[1]["category"] == "design_reference"
    assert load_manual(conn, [1])[1]["category"] == "life"
    assert effective_post_categories(conn, [1])[1]["source"] == "manual"
    conn.close()


def test_shared_sha_has_source_scoped_categories_and_one_result(data_dir):
    seed(data_dir)
    conn = db.connect()
    service = ClassificationService(conn)
    # image 1 belongs to post 1 (design) and post 2 (life)
    service.classify([1, 2, 3], FakeBackend(categories_by_title()))
    conn.commit()
    library = visible_images(conn, "library")
    shared = next(row for row in library if row["image_id"] == 1)
    assert {s["post_id"] for s in shared["sources"]} == {1, 2}
    assert {s["effective_classification"]["category"] for s in shared["sources"]} == {
        "design_reference", "life"}
    design = visible_images(conn, "design_workspace")
    assert [row["image_id"] for row in design] == [1]
    assert {s["post_id"] for s in design[0]["sources"]} == {1}
    assert set(visible_image_ids(conn, "library")) == {1, 2}
    assert visible_image_ids(conn, "design_workspace") == [1]
    assert visible_image_ids(conn, "search") == [1]
    assert visible_image_ids(conn, "recommendation") == [1]
    assert filter_image_ids(conn, "design_workspace", [1, 2]) == {1}
    assert filter_image_ids(conn, "library", [1, 2]) == {1, 2}
    conn.close()


def test_life_and_uncertain_are_retained_not_deleted_by_filter(data_dir):
    seed(data_dir)
    conn = db.connect()
    service = ClassificationService(conn)
    service.classify([1, 2, 3], FakeBackend(categories_by_title()))
    conn.commit()
    assert load_automatic(conn, [2, 3])[2]["category"] == "life"
    assert load_automatic(conn, [2, 3])[3]["category"] == "uncertain"
    # filtering hides them from design surfaces but does not remove rows.
    assert 2 not in visible_image_ids(conn, "design_workspace")
    assert 2 in visible_image_ids(conn, "library")
    assert load_automatic(conn, [2, 3])[2]["category"] == "life"
    assert load_automatic(conn, [2, 3])[3]["category"] == "uncertain"
    conn.close()


def test_repeated_classification_is_idempotent_with_cache(data_dir):
    seed(data_dir)
    conn = db.connect()
    service = ClassificationService(conn)
    backend = FakeBackend(categories_by_title())
    first = service.classify([1, 2], backend)
    assert first.ok == 2
    calls = backend.calls
    second = service.classify([1, 2], backend)
    assert second.ok == 2
    assert second.updated == 0
    assert backend.calls == calls
    assert load_automatic(conn, [1, 2])[1]["category"] == "design_reference"
    conn.close()


def test_manual_clear_and_invalid_category(data_dir):
    seed(data_dir)
    conn = db.connect()
    set_manual(conn, 1, "uncertain", "待确认")
    conn.commit()
    assert effective_post_categories(conn, [1])[1] == {"category": "uncertain", "source": "manual"}
    clear_manual(conn, 1)
    conn.commit()
    assert effective_post_categories(conn, [1])[1] == {"category": "uncertain", "source": "automatic"}
    with pytest.raises(ValueError):
        set_manual(conn, 1, "not-a-category")
    with pytest.raises(ClassificationServiceError):
        set_manual(conn, 999, "life")
    conn.close()


def test_build_post_document_includes_done_analyses(data_dir):
    seed(data_dir)
    conn = db.connect()
    document = build_post_document(conn, 1)
    assert document["post"]["title"] == "建筑设计参考"
    assert len(document["images"]) == 1
    assert document["images"][0]["subject"] == ["建筑"]
    assert document["images"][0]["layout"] == {"columns": ["single"]}
    assert build_post_document(conn, 999) is None
    conn.close()
