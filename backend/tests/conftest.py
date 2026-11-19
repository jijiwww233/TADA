import io
import sqlite3

import pytest
from PIL import Image

from asyncio_compat import asgi_backend_options


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("WOMINMIN_DATA_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture(scope="session")
def backend_options():
    """anyio options for TestClient; swaps the event loop only if loopback is blocked."""
    return asgi_backend_options()


def png(color) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), color).save(buf, "PNG")
    return buf.getvalue()


def counts(data_dir):
    """(posts, images rows, files in data/images)"""
    conn = sqlite3.connect(data_dir / "app.db")
    try:
        return (
            conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0],
            conn.execute("SELECT COUNT(*) FROM images").fetchone()[0],
            len(list((data_dir / "images").iterdir())),
        )
    finally:
        conn.close()


@pytest.fixture(autouse=True)
def isolated_safety(tmp_path, monkeypatch):
    monkeypatch.setenv("WOMINMIN_SAFETY_DIR", str(tmp_path / "safety"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local-app-data"))
