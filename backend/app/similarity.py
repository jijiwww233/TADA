"""Local Chinese-CLIP image-to-image search. No cloud calls or analysis dependency.

The background worker discovers committed images, so all import/sync paths share
one incremental index. Source images are never modified; upload queries stay in RAM.
"""
import hashlib
import io
import math
import os
import struct
import sqlite3
import threading
from pathlib import Path

from . import config
from .embedding.search import SearchHit
from .embedding.search_application import hydrate_hits, readonly_connection
from .importer import MAX_BYTES, Upload, _check
from .sync.metrics import measure

SCHEME = 'clip_image_v1'
DIMENSIONS = 512


class SimilarityUnavailable(RuntimeError):
    pass


class LocalClipEncoder:
    """Lazy CPU-only model. Never download at app startup or at query time."""
    def __init__(self, model_dir=None):
        self.model_dir = Path(model_dir or os.getenv('CHINESE_CLIP_MODEL_DIR') or
                              config.data_dir() / 'models' / 'chinese-clip-base')
        self.lock = threading.Lock()
        self._identity = None
        self.model = None

    @property
    def identity(self):
        if self._identity is None:
            weight = self.model_dir / 'pytorch_model.bin'
            if not all((self.model_dir / f).is_file() for f in
                       ('config.json', 'preprocessor_config.json', 'vocab.txt', 'pytorch_model.bin')):
                raise SimilarityUnavailable('本地图片模型未安装，请按 docs/image_similarity.md 配置后重启。')
            digest = hashlib.sha256()
            with weight.open('rb') as f:
                for chunk in iter(lambda: f.read(1024 * 1024), b''):
                    digest.update(chunk)
            for name in ('config.json', 'preprocessor_config.json', 'vocab.txt'):
                digest.update((self.model_dir / name).read_bytes())
            self._identity = 'OFA-Sys/chinese-clip-vit-base-patch16:' + digest.hexdigest() + ':pil-slow-v1'
        return self._identity

    def _load(self):
        self.identity
        if self.model is not None:
            return
        try:
            import torch
            from transformers import ChineseCLIPModel, ChineseCLIPProcessor
            torch.set_num_threads(4)
            self.model = ChineseCLIPModel.from_pretrained(self.model_dir, local_files_only=True).eval().to('cpu')
            self.processor = ChineseCLIPProcessor.from_pretrained(
                self.model_dir, local_files_only=True, use_fast=False)
        except ImportError:
            raise SimilarityUnavailable('本地图片模型依赖未安装，请安装 requirements-clip.txt 后重启。') from None

    def encode(self, images):
        from PIL import ImageOps
        with self.lock:
            self._load()
            import torch
            images = [ImageOps.exif_transpose(im).convert('RGB') for im in images]
            with torch.inference_mode():
                values = self.model.get_image_features(**self.processor(images=images, return_tensors='pt'))
                return (values / values.norm(dim=-1, keepdim=True)).cpu().tolist()

    def warmup(self):
        self.encode_texts(["预热"])

    def encode_texts(self, texts):
        """Chinese-CLIP text tower (local CPU); used by the find-route fusion."""
        with self.lock:
            self._load()
            import torch
            with torch.inference_mode():
                values = self.model.get_text_features(
                    **self.processor(text=list(texts), return_tensors='pt', padding=True))
                return (values / values.norm(dim=-1, keepdim=True)).cpu().tolist()

    def encode_paths(self, paths):
        from PIL import Image
        images = []
        try:
            for path in paths:
                images.append(Image.open(path))
            return self.encode(images)
        finally:
            for im in images:
                im.close()

    def encode_bytes(self, data):
        from PIL import Image
        with Image.open(io.BytesIO(data)) as im:
            return self.encode([im])[0]


def vector(blob):
    try:
        result = struct.unpack('<' + 'f' * DIMENSIONS, blob)
        if all(math.isfinite(x) for x in result) and sum(x*x for x in result) > 1e-12:
            return result
    except (TypeError, struct.error):
        pass
    return None


def normalized(values):
    if len(values) != DIMENSIONS or not all(math.isfinite(x) for x in values):
        raise ValueError('图片向量无效')
    length = math.sqrt(sum(x*x for x in values))
    if length <= 1e-12:
        raise ValueError('图片向量为空')
    return [x / length for x in values]


class ImageSimilarity:
    def __init__(self, database=config.db_path, encoder=None, interval=5):
        self.database = database
        self.encoder = encoder or LocalClipEncoder()
        self.interval = interval
        self.stop = threading.Event()
        self.thread = None
        self.index_lock = threading.Lock()
        self.error = None
        self._cache_lock = threading.RLock()
        self._watch = None
        self._database_key = None
        self._stamp = None
        self._snapshot = None
        self.warming = False
        self.warmed = False
        self.warmup_ms = None
        self.failures = {}  # bad files do not starve later images; retry after restart/file change

    def _rows(self):
        with readonly_connection(self.database()) as conn:
            return [dict(r) for r in conn.execute('''SELECT i.id,i.file_path,i.sha256,
                e.model,e.dimensions,e.text_hash,e.vector FROM images i
                LEFT JOIN embeddings e ON e.image_id=i.id AND e.dimension=? ORDER BY i.id''', (SCHEME,))]

    def _cache(self):
        """Immutable normalised matrix; refresh only after a committed DB/model change.
        A persistent read-only connection supplies PRAGMA data_version. New connections
        would always report the same version and cannot detect external commits.
        """
        with self._cache_lock:
            identity = self.encoder.identity
            try:
                import numpy as np
            except ImportError:
                raise SimilarityUnavailable('请安装本地图片模型依赖 requirements-clip.txt。') from None
            key = self.database().resolve()
            if self._database_key != key:
                if self._watch is not None: self._watch.close()
                self._watch = sqlite3.connect(key.as_uri()+'?mode=ro', uri=True,check_same_thread=False)
                self._watch.execute('PRAGMA query_only=ON')
                self._database_key=key; self._stamp=None
            version = self._watch.execute('PRAGMA data_version').fetchone()[0]
            stamp = (key,version,identity)
            if self._stamp == stamp and self._snapshot is not None:
                return self._snapshot
            rows=self._rows()
            valid=[row for row in rows if self._valid(row,identity)]
            ids=np.array([row['id'] for row in valid],dtype=np.int64)
            # float64 matches the previous Python-double cosine route; no quantisation.
            matrix=np.array([vector(row['vector']) for row in valid],dtype=np.float64).reshape((-1,DIMENSIONS))
            if len(matrix): matrix /= np.linalg.norm(matrix,axis=1,keepdims=True)
            matrix.setflags(write=False); ids.setflags(write=False)
            snapshot={'ids':ids,'matrix':matrix,'valid':valid,'rows':rows,
                      'by_id':{row['id']:row for row in rows},
                      'positions':{int(id):i for i,id in enumerate(ids)}}
            self._snapshot=snapshot; self._stamp=stamp
            return snapshot

    def warmup(self):
        import time
        self.warming=True;started=time.perf_counter()
        try:
            preload=getattr(self.encoder,'warmup',None)
            if preload: preload()
            self._cache()
            self.warmed=True;self.error=None
        finally:
            self.warming=False;self.warmup_ms=round((time.perf_counter()-started)*1000,2)

    def _valid(self, row, identity):
        return (row['model'] == identity and row['dimensions'] == DIMENSIONS
                and row['text_hash'] == row['sha256'] and vector(row['vector']) is not None)

    def status(self):
        try:
            identity = self.encoder.identity
            rows = self._rows()
            indexed = sum(bool(self._valid(r, identity)) for r in rows)
            return {'available': self.error is None, 'indexed': indexed, 'total': len(rows),
                    'pending': len(rows)-indexed, 'failed': len(self.failures), 'error': self.error,
                    'warming':self.warming,'warmed':self.warmed,'warmup_ms':self.warmup_ms}
        except SimilarityUnavailable as exc:
            return {'available': False, 'indexed': 0, 'error': str(exc)}

    def index_pending(self, limit=8):
        # One batch at a time across background work and query-side index repair.
        with self.index_lock:
            identity = self.encoder.identity
            rows = [r for r in self._rows() if not self._valid(r, identity)
                    and self.failures.get(r['id']) != r['sha256']][:limit]
            if not rows:
                return 0
            paths = [self.database().parent / r['file_path'] for r in rows]
            try:
                with measure("local_clip", "vector", len(paths)):
                    encoded = self.encoder.encode_paths(paths)
                if len(encoded) != len(rows):
                    raise ValueError('图片向量批次数量不符')
                pairs = [(r, normalized(v)) for r, v in zip(rows, encoded)]
            except SimilarityUnavailable:
                raise
            except Exception:
                # Isolate a broken image so one bad download cannot block the batch.
                pairs = []
                for row, path in zip(rows, paths):
                    try:
                        pairs.append((row, normalized(self.encoder.encode_paths([path])[0])))
                    except Exception:
                        self.failures[row['id']] = row['sha256']
            conn = sqlite3.connect(self.database(), timeout=30)
            try:
                with conn:
                    for row, values in pairs:
                        # Source changed/deleted while encoding: don't attach stale vectors.
                        conn.execute('''INSERT INTO embeddings
                            (image_id,dimension,model,vector,dimensions,text_hash)
                            SELECT id,?,?,?,?,sha256 FROM images WHERE id=? AND sha256=?
                            ON CONFLICT(image_id,dimension) DO UPDATE SET model=excluded.model,
                            vector=excluded.vector,dimensions=excluded.dimensions,text_hash=excluded.text_hash''',
                            (SCHEME, identity, struct.pack('<'+'f'*DIMENSIONS, *values), DIMENSIONS,
                             row['id'], row['sha256']))
            finally:
                conn.close()
            return len(rows)

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.stop.clear()
        self.warming=True
        def loop():
            try:
                self.warmup()
            except SimilarityUnavailable as exc:
                self.error=str(exc);return
            except Exception:
                self.error='本地图片模型预热失败，请检查权重/依赖并重启。';return
            while not self.stop.is_set():
                try:
                    count = self.index_pending()
                    self.error = None
                except SimilarityUnavailable as exc:
                    self.error = str(exc)
                    return
                except Exception:
                    self.error = '本地图片索引暂不可用，请重试或重启。'
                    count = 0
                if not count:
                    self.stop.wait(self.interval)
        self.thread = threading.Thread(target=loop, name='local-clip-index', daemon=True)
        self.thread.start()

    def shutdown(self):
        self.stop.set()
        if self.thread:
            self.thread.join()  # wait for encoding/writes before closing our metadata connection
        with self._cache_lock:
            if self._watch is not None:
                self._watch.close();self._watch=None;self._database_key=None;self._stamp=None

    def valid_ids(self):
        return set(map(int,self._cache()['ids']))

    def search_text(self, text, top_n=None):
        if not isinstance(text,str) or not text.strip(): return []
        import numpy as np
        cached=self._cache()
        # Exactly one text-tower encoding per query; all image vectors stay resident.
        query=np.asarray(normalized(self.encoder.encode_texts([text.strip()])[0]),dtype=np.float64)
        scores=cached['matrix'] @ query
        order=np.lexsort((cached['ids'],-scores))
        if top_n is not None: order=order[:top_n]
        return [(int(cached['ids'][i]),float(scores[i])) for i in order]

    def index_vectors(self):
        """Return the immutable image index for local training and reranking."""
        snapshot = self._cache()
        return snapshot["ids"], snapshot["matrix"]

    def text_evidence(self, prompts):
        """Return raw per-prompt cosine evidence for every indexed image."""
        import numpy as np
        cached = self._cache()
        if not len(cached["ids"]):
            return {}
        vectors = np.asarray([normalized(vector) for vector in self.encoder.encode_texts(prompts)], dtype=np.float64)
        scores = cached["matrix"] @ vectors.T
        return {int(image_id): row.tolist() for image_id, row in zip(cached["ids"], scores)}

    def rejection_scores(self, image_ids):
        """Maximum positive local CLIP similarity to the project's rejected images."""
        import numpy as np
        cached = self._cache()
        strongest = np.zeros(len(cached["ids"]))
        for image_id in image_ids:
            position = cached["positions"].get(image_id)
            if position is not None:
                np.maximum(strongest, cached["matrix"] @ cached["matrix"][position], out=strongest)
        return dict(zip(map(int, cached["ids"]), map(float, strongest)))

    def search(self, *, image_id=None, data=None, exclude_ids=()):
        cached=self._cache()
        rows=cached["rows"]
        if image_id is not None:
            source = next((r for r in rows if r['id'] == image_id), None)
            if source is None:
                raise KeyError(image_id)
            sha = source['sha256']
            query = (cached['matrix'][cached['positions'][image_id]].tolist() if image_id in cached['positions'] else
                     self.encoder.encode_paths([self.database().parent / source['file_path']])[0])
        else:
            check = _check(Upload('query', data or b''))
            if isinstance(check, str):
                raise ValueError(check)
            sha = check.sha256
            query = self.encoder.encode_bytes(check.data)
        query = normalized(query)
        import numpy as np
        scores=cached['matrix'] @ np.asarray(query,dtype=np.float64)
        order=np.lexsort((cached['ids'],-scores))
        hits=[SearchHit(int(cached['ids'][i]),float(scores[i]),None) for i in order
              if cached['valid'][i]['sha256']!=sha and int(cached['ids'][i]) not in exclude_ids][:20]
        with readonly_connection(self.database()) as conn:
            items = hydrate_hits(conn, hits[:20])
        return {'status': 'ok' if hits else 'index_empty', 'items': items,
                'indexed': len(cached['ids']),
                'total': len(rows), 'query_image_id': image_id}
