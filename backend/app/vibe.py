"""Local-only similarity dimensions and project rediscovery."""
from datetime import date, timedelta

from . import palette
from .embedding import dimensions
from .embedding.search import EmbeddingItem, SearchHit, rank
from .embedding.search_application import hydrate_hits
from .embedding.service import EmbeddingSpec, blob_to_vector
from .embedding.store import stale_reason
from .project_store import ProjectStore
from .projects import encode_purposes, normalize_purposes
from .similarity import SimilarityUnavailable


SCHEMES = {"layout": "layout_v1", "subject": "subject_v1", "style": "style_v1"}
RESULT_LIMIT = 20
BATCH_SIZE = 5
SEEN_DAYS = 7
PREFERRED_AGE_DAYS = 30
NEGATIVE_WEIGHT = 0.35


def _vector_spec(row):
    return EmbeddingSpec(model=row["model"], dimensions=row["dimensions"],
                         scheme=row["dimension"], text_version=row["dimension"])


def _available_spec(conn, analysis, scheme):
    anchor = conn.execute(
        "SELECT * FROM embeddings WHERE image_id=? AND dimension=?", (analysis["image_id"], scheme)).fetchone()
    if not anchor or not anchor["dimensions"]:
        return None
    spec = _vector_spec(anchor)
    text = dimensions.build_text(scheme, dict(analysis))
    if not text or stale_reason(anchor, spec, dimensions.text_hash(scheme, text)) is not None:
        return None
    return spec


def _dimension_scores(conn, image_id, spec):
    vectors = dimensions.load_rows(conn, spec)
    query = next((row.vector for row in vectors if row.image_id == image_id), None)
    if query is None:
        return None
    items = (EmbeddingItem(row.image_id, blob_to_vector(row.vector, spec.dimensions)) for row in vectors)
    hits = rank(blob_to_vector(query, spec.dimensions), items, top_n=None)
    return {hit.image_id: hit.score for hit in hits}


def _colors(conn, image_id):
    return [dict(row) for row in conn.execute(
        "SELECT hex,ratio FROM colors WHERE image_id=? ORDER BY rank", (image_id,)) if palette.rgb(row["hex"])]


def _palette_scores(conn, anchor):
    swatches = {}
    for row in conn.execute("""SELECT c.image_id,c.hex,c.ratio FROM colors c
                               JOIN images i ON i.id=c.image_id AND i.deleted_at IS NULL
                               ORDER BY c.image_id,c.rank"""):
        swatches.setdefault(row["image_id"], []).append(dict(row))
    targets = [(palette.Target(row["hex"]), row["ratio"]) for row in anchor if row["ratio"] > 0]
    return {image_id: sum(palette.score(colors, (target,)) * share for target, share in targets)
            for image_id, colors in swatches.items()}


class VibeService:
    def __init__(self, clip, projects=None, today=date.today):
        self.clip = clip
        self.projects = projects or ProjectStore()
        self.today = today

    def similar(self, image_id, dimension):
        with self.projects.transaction() as conn:
            source = conn.execute(
                "SELECT sha256 FROM images WHERE id=? AND deleted_at IS NULL", (image_id,)).fetchone()
            if source is None:
                raise KeyError(image_id)
            excluded = {row[0] for row in conn.execute(
                "SELECT id FROM images WHERE sha256=? UNION SELECT image_id FROM vibe_rejected",
                (source["sha256"],))}
            done = conn.execute(
                "SELECT * FROM analyses WHERE image_id=? AND status='done'", (image_id,)).fetchone()
            colors = _colors(conn, image_id)
            available = ["combined"]
            if colors:
                available.append("palette")
            scores = None
            if done:
                for mode, scheme in SCHEMES.items():
                    spec = _available_spec(conn, done, scheme)
                    if spec is not None:
                        available.append(mode)
                        if mode == dimension:
                            scores = _dimension_scores(conn, image_id, spec)
            if dimension == "palette" and colors:
                scores = _palette_scores(conn, colors)
            if dimension != "combined" and scores is not None:
                hits = [SearchHit(candidate, value) for candidate, value in
                        sorted(scores.items(), key=lambda item: (-item[1], item[0]))
                        if candidate not in excluded][:RESULT_LIMIT]
                return {"status": "ok" if hits else "index_empty", "items": hydrate_hits(conn, hits),
                        "available_dimensions": available}
        if dimension != "combined":
            raise ValueError("这张还没分析，或缺少该维度的向量")
        rejected = excluded - {image_id}
        options = {"exclude_ids": rejected} if rejected else {}
        return {**self.clip.search(image_id=image_id, **options), "available_dimensions": available}

    def upload_options(self):
        with self.projects.transaction() as conn:
            rejected = {row[0] for row in conn.execute("SELECT DISTINCT image_id FROM vibe_rejected")}
        return {"exclude_ids": rejected} if rejected else {}

    @staticmethod
    def _hidden(conn, project_id):
        return {row[0] for row in conn.execute(
            """SELECT p.image_id FROM pins p JOIN images i ON i.id=p.image_id AND i.deleted_at IS NULL
               WHERE p.project_id=?
               UNION SELECT image_id FROM vibe_rejected WHERE project_id=?""",
            (project_id, project_id))}

    @staticmethod
    def _batch(conn, project_id, day):
        return [row[0] for row in conn.execute(
            "SELECT image_id FROM vibe_batches WHERE project_id=? AND day=? ORDER BY position",
            (project_id, day))]

    def _visible_batch(self, conn, project_id, batch):
        hidden = self._hidden(conn, project_id)
        return self.projects._images(conn, [image for image in batch if image not in hidden])

    def resurface(self, project_id):
        today = self.today()
        day = today.isoformat()
        with self.projects.transaction() as conn:
            brief = self.projects._project(conn, project_id)["brief"].strip()
            if not brief:
                return []
            batch = self._batch(conn, project_id, day)
            if batch:
                return self._visible_batch(conn, project_id, batch)
            rejected = {row[0] for row in conn.execute(
                "SELECT image_id FROM vibe_rejected WHERE project_id=?", (project_id,))}
        try:
            scores = self.clip.search_text(brief)
            penalties = self.clip.rejection_scores(rejected) if rejected else {}
        except (SimilarityUnavailable, ValueError):
            return []
        if not scores:
            return []
        with self.projects.transaction(write=True) as conn:
            if self.projects._project(conn, project_id)["brief"].strip() != brief:
                return []
            batch = self._batch(conn, project_id, day)
            if batch:
                return self._visible_batch(conn, project_id, batch)
            hidden = self._hidden(conn, project_id)
            hidden.update(row[0] for row in conn.execute(
                "SELECT image_id FROM vibe_batches WHERE project_id=? AND day>=?",
                (project_id, (today - timedelta(days=SEEN_DAYS)).isoformat())))
            old = {row[0] for row in conn.execute(
                "SELECT id FROM images WHERE deleted_at IS NULL AND julianday(created_at)<=julianday(?)",
                ((today - timedelta(days=PREFERRED_AGE_DAYS)).isoformat(),))}
            ranked = sorted(
                ((image, score) for image, score in scores if image not in hidden and score > 0),
                key=lambda item: (item[0] not in old,
                                  -item[1] + NEGATIVE_WEIGHT * penalties.get(item[0], 0), item[0]))
            batch = [image for image, _ in ranked[:BATCH_SIZE]]
            conn.executemany("INSERT INTO vibe_batches VALUES(?,?,?,?)",
                             ((project_id, day, index, image) for index, image in enumerate(batch)))
            return self.projects._images(conn, batch)

    def reject(self, project_id, image_id):
        with self.projects.transaction(write=True) as conn:
            self.projects._project(conn, project_id)
            self.projects._image(conn, image_id)
            conn.execute("INSERT OR IGNORE INTO vibe_rejected VALUES(?,?)", (project_id, image_id))

    def pin_batch(self, project_id, image_ids, purpose):
        purposes = normalize_purposes((purpose,))
        with self.projects.transaction(write=True) as conn:
            self.projects._project(conn, project_id)
            inserted = 0
            for image_id in dict.fromkeys(image_ids):
                self.projects._image(conn, image_id)
                inserted += conn.execute(
                    """INSERT INTO pins(project_id,image_id,purpose,note) VALUES(?,?,?,'')
                       ON CONFLICT(project_id,image_id) DO NOTHING""",
                    (project_id, image_id, encode_purposes(purposes))).rowcount
            return inserted
