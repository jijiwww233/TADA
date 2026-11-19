import io
import sqlite3
import time

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import db, importer
from app.main import create_app
from app.similarity import ImageSimilarity, LocalClipEncoder, SCHEME, SimilarityUnavailable


def image(color):
    out = io.BytesIO()
    Image.new('RGB', (16, 16), color).save(out, 'PNG')
    return out.getvalue()


class Encoder:
    identity = 'fake-clip-512-v1'
    def __init__(self): self.calls = 0
    def encode_bytes(self, data):
        self.calls += 1
        with Image.open(io.BytesIO(data)) as im:
            r, g, b = im.getpixel((0, 0))
        return [r + 1, g + 1, b + 1] + [0] * 509
    def encode_paths(self, paths): return [self.encode_bytes(p.read_bytes()) for p in paths]


@pytest.fixture
def indexed(data_dir):
    db.init_db()
    uploads = [importer.Upload(str(i), image((255, i, 0))) for i in range(25)]
    ids = [r['id'] for r in importer.import_images(uploads)['added']]
    encoder = Encoder()
    service = ImageSimilarity(encoder=encoder, interval=.02)
    while service.index_pending(): pass
    return service, encoder, ids


def test_similarity_top20_excludes_source_and_is_independent_from_analysis(indexed):
    service, encoder, ids = indexed
    result = service.search(image_id=ids[0])
    assert len(result['items']) == 20
    assert ids[0] not in [r['image_id'] for r in result['items']]
    assert result['items'][0]['image_id'] == ids[1]
    assert encoder.calls == 25  # cached query vector, no re-encode
    conn = db.connect()
    assert conn.execute("SELECT count(*) FROM analyses WHERE status='pending'").fetchone()[0] == 25
    assert conn.execute('SELECT count(*) FROM embeddings WHERE dimension=?', (SCHEME,)).fetchone()[0] == 25
    assert service.index_pending() == 0
    conn.close()


def test_upload_is_ephemeral_dedupes_query_and_never_imports(indexed, data_dir):
    service, encoder, ids = indexed
    before = sorted(p.name for p in (data_dir/'images').iterdir())
    result = service.search(data=image((255, 0, 0)))
    assert len(result['items']) == 20 and ids[0] not in [r['image_id'] for r in result['items']]
    assert before == sorted(p.name for p in (data_dir/'images').iterdir())
    with pytest.raises(ValueError): service.search(data=b'not an image')
    with pytest.raises(KeyError): service.search(image_id=9999)


def test_background_discovers_new_committed_images_and_restarts(indexed):
    service, encoder, ids = indexed
    service.start()
    try:
        new = importer.import_images([importer.Upload('new', image('blue'))])['added'][0]['id']
        deadline = time.monotonic() + 3
        while service.status()['pending'] and time.monotonic() < deadline: time.sleep(.02)
        assert service.status()['indexed'] == 26
    finally: service.shutdown()
    again = ImageSimilarity(encoder=encoder)
    assert again.index_pending() == 0 and again.search(image_id=new)['items']


def test_changed_model_or_corrupt_blob_rebuilds_only_image_scheme(indexed):
    service, encoder, ids = indexed
    conn = db.connect()
    conn.execute("UPDATE embeddings SET vector=? WHERE image_id=?", (b'broken', ids[0]))
    conn.execute("INSERT INTO embeddings(image_id,dimension,vector) VALUES (?,?,?)", (ids[0], 'keep-me', b'text'))
    conn.commit();conn.close()
    assert service.index_pending() == 1
    encoder.identity = 'new-model'
    while service.index_pending(): pass
    conn = db.connect()
    assert conn.execute("SELECT vector FROM embeddings WHERE dimension='keep-me'").fetchone()[0] == b'text'
    assert service.status()['indexed'] == 25
    conn.close()


def test_missing_model_is_clear_and_never_downloads(data_dir):
    encoder = LocalClipEncoder(data_dir/'absent')
    with pytest.raises(SimilarityUnavailable, match='未安装'): encoder.identity
    assert not (data_dir/'absent').exists()


def test_api_existing_upload_invalid_and_status(indexed, backend_options):
    service, encoder, ids = indexed
    with TestClient(create_app(image_similarity=service), backend_options=backend_options) as client:
        assert client.get('/api/similar/status').json()['indexed'] == 25
        assert len(client.get(f'/api/images/{ids[0]}/similar').json()['items']) == 20
        got = client.post('/api/search/image', files={'file': ('x.png', image('blue'), 'image/png')})
        assert got.status_code == 200 and len(got.json()['items']) == 20
        assert client.post('/api/search/image', files={'file': ('x', b'bad')}).status_code == 422
        assert client.get('/api/images/99999/similar').status_code == 404


def test_corrupt_file_does_not_block_other_new_images(indexed, data_dir):
    service, encoder, ids = indexed
    added = importer.import_images([importer.Upload('a', image('pink')), importer.Upload('b', image('cyan'))])['added']
    conn = db.connect()
    path = conn.execute('SELECT file_path FROM images WHERE id=?', (added[0]['id'],)).fetchone()[0];conn.close()
    (data_dir/path).write_bytes(b'broken')
    assert service.index_pending() == 2
    assert service.status()['failed'] == 1 and service.status()['indexed'] == 26
    assert service.index_pending() == 0
