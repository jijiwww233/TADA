"""HTTP endpoint tests, in-process via TestClient (no server, no real socket).

See asyncio_compat.py for how this works where 127.0.0.1 is blocked.
"""
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from conftest import counts, png


@pytest.fixture
def client(data_dir, backend_options):
    with TestClient(create_app(), backend_options=backend_options) as c:
        yield c


def upload(client, files):
    resp = client.post(
        "/api/images/import",
        files=[("files", (name, data, "application/octet-stream")) for name, data in files],
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_health(client):
    assert client.get("/api/health").json() == {"ok": True}


def test_upload_dedupe_and_list(client, data_dir):
    red, blue = png("red"), png("blue")
    r1 = upload(client, [("a.png", red), ("b.png", blue)])
    assert len(r1["added"]) == 2
    r2 = upload(client, [("a-copy.png", red), ("c.txt", b"nope")])
    assert not r2["added"] and len(r2["duplicates"]) == 1 and len(r2["failed"]) == 1
    assert counts(data_dir) == (1, 2, 2)
    assert [i["original_name"] for i in client.get("/api/images").json()] == ["a.png", "b.png"]


def test_image_file_is_served(client):
    r = upload(client, [("a.png", png("red"))])
    resp = client.get(r["added"][0]["url"])
    assert resp.status_code == 200 and resp.content == png("red")
