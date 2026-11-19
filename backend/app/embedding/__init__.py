"""Single combined embedding (combined_d_v1) for image semantic search.

Public surface:
- text.build_combined_d_v1 / text_hash / coarse_color_name / excerpt_visible_text
- client.EmbeddingSettings(Config) / build_embedding_components / EmbeddingClient / EmbeddingError
- service.EmbeddingSpec / needs_rebuild / prepare_embedding / vector_to_blob
- search.rank / cosine_similarity / search_text / EmbeddingItem / SearchHit
- search_service.SearchService / SearchRow (Top20, stable numeric image_id)
- index_service.EmbeddingIndexService / IndexTask / IndexedEmbedding / IndexReport
- repository read/write helpers for the existing embeddings table
"""
from .client import (DEFAULT_DIMENSIONS, DEFAULT_MODEL, MAX_BATCH, SCOPE_GLOBAL,
                     SCOPE_INPUT, SCOPE_UNKNOWN, EmbeddingClient, EmbeddingConfig,
                     EmbeddingError, EmbeddingSettings, build_embedding_components)
from .evaluation import EvalQuery, evaluate, load_queries
from .index_service import (BATCH_REQUEST_BUDGET, EmbeddingIndexService, IndexFailure,
                            IndexReport, IndexTask, IndexedEmbedding, image_lock)
from .repository import (RepositoryError, load_embeddings, load_search_rows,
                         upsert_embedding)
from .search import EmbeddingItem, SearchHit, cosine_similarity, rank, search_text
from .search_service import SearchRow, SearchService
from .service import (EmbeddingRecord, EmbeddingSpec, blob_to_vector, build_text,
                      make_spec, needs_rebuild, prepare_embedding, vector_to_blob)
from .text import (TEXT_VERSION, build_combined_d_v1, coarse_color_name,
                   excerpt_visible_text, text_hash)

__all__ = [
    "DEFAULT_DIMENSIONS", "DEFAULT_MODEL", "MAX_BATCH",
    "SCOPE_GLOBAL", "SCOPE_INPUT", "SCOPE_UNKNOWN",
    "EmbeddingClient", "EmbeddingConfig", "EmbeddingError", "EmbeddingSettings",
    "build_embedding_components",
    "EmbeddingItem", "SearchHit", "cosine_similarity", "rank", "search_text",
    "EmbeddingRecord", "EmbeddingSpec", "blob_to_vector", "build_text",
    "make_spec", "needs_rebuild", "prepare_embedding", "vector_to_blob",
    "TEXT_VERSION", "build_combined_d_v1", "coarse_color_name",
    "excerpt_visible_text", "text_hash",
    "SearchService", "SearchRow", "EmbeddingIndexService", "IndexTask",
    "IndexedEmbedding", "IndexFailure", "IndexReport", "image_lock",
    "BATCH_REQUEST_BUDGET",
    "EvalQuery", "evaluate", "load_queries",
    "RepositoryError", "load_embeddings", "load_search_rows", "upsert_embedding",
]
