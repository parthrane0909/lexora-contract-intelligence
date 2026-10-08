"""Semantic intelligence layer (Phase 6): embeddings → Qdrant → retrieval.

Retrieval ONLY — no generation, no advice. The risk engine stays
authoritative for risk; similarity scores are similarity, nothing more.
"""
from app.modules.retrieval import semantic_matcher  # noqa: F401 (registers "semantic")
from app.modules.retrieval.indexer import (  # noqa: F401
    index_contract,
    index_policy,
    index_status,
    index_template,
    reindex_scope,
)
from app.modules.retrieval.providers import (  # noqa: F401
    EmbeddingUnavailableError,
    EmptyTextError,
    get_provider,
    reset_provider,
)
from app.modules.retrieval.search import precedent_for_clause, semantic_search  # noqa: F401
from app.modules.retrieval.vector_store import (  # noqa: F401
    InMemoryVectorStore,
    QdrantVectorStore,
    VectorStoreError,
)
