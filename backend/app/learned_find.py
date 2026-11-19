"""Read-only learned reranker for known visual concepts."""
from functools import lru_cache
import json
import re

from . import config
from .embedding.search import SearchHit
from .embedding.search_application import hydrate_hits


def clean_query(text):
    return re.sub(r"[\s，。！、？,!.?]+", "", text.casefold())


class LearnedFind:
    def __init__(self, payload):
        import numpy as np
        self.identity = payload["encoder"]
        self.mean = np.asarray(payload["mean"], dtype=float)
        self.weights = np.asarray(payload["weights"], dtype=float)
        self.bias = np.asarray(payload["bias"], dtype=float)
        self.floor = np.asarray(payload["floor"], dtype=float)
        self.reviewed = {int(image_id): digest for image_id, digest in payload.get("reviewed", {}).items()}
        self.aliases = {clean_query(alias): target for alias, target in payload["aliases"]}
        self.alias_vectors = np.asarray(payload["alias_vectors"], dtype=float)
        self.alias_targets = np.asarray([target for _, target in payload["aliases"]])

    def concept(self, query, encoder):
        key = clean_query(query)
        if not key or any(word in key for word in ("不要", "排除", "没有", "不含")):
            return None
        exact = self.aliases.get(key)
        if exact is not None:
            return exact
        if len(key) < 2:
            return None
        phrase = re.sub(r"^(?:我记得|我想找|有没有|找一张|想找|找一下)", "", key)
        phrase = re.sub(r"(?:的照片|的图片|的图)$", "", phrase)
        if phrase in self.aliases:
            return self.aliases[phrase]
        vector = encoder.encode_texts([query])[0]
        similarity = self.alias_vectors @ vector
        ordering = similarity.argsort()[::-1]
        first = ordering[0]
        target = self.alias_targets[first]
        runner_up = next((similarity[i] for i in ordering[1:] if self.alias_targets[i] != target), 0)
        if similarity[first] >= .78 and similarity[first] - runner_up >= .025:
            return target
        return None

    def rank(self, target, ids, matrix):
        prediction = (matrix - self.mean) @ self.weights[:, target] + self.bias[target]
        return [(int(ids[i]), float(prediction[i])) for i in prediction.argsort()[::-1]
                if prediction[i] >= self.floor[target]]


@lru_cache(maxsize=2)
def load_model(path, modified):
    return LearnedFind(json.loads(path.read_text(encoding="utf-8")))


def learned_hits(conn, query, similarity):
    model_path = config.data_dir() / "models" / "learned-find.json"
    try:
        model = load_model(model_path, model_path.stat().st_mtime_ns)
        if model.identity != similarity.encoder.identity:
            return [], set()
        target = model.concept(query, similarity.encoder)
        if target is None:
            return [], set()
        ids, matrix = similarity.index_vectors()
        if not len(ids):
            return [], set()
        ranked = model.rank(target, ids, matrix)
        reviewed = {row["id"] for row in conn.execute("SELECT id,sha256 FROM images")
                    if model.reviewed.get(row["id"]) == row["sha256"]}
        hits = hydrate_hits(conn, [SearchHit(image_id, score) for image_id, score in ranked if image_id in reviewed][:120])
        return hits, reviewed
    except (OSError, ValueError, KeyError, ImportError, RuntimeError):
        return [], set()
