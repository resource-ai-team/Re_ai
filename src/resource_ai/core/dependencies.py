from functools import lru_cache

from resource_ai.core.config import get_settings
from resource_ai.rag.retrieval.embeddings import create_embedding_provider
from resource_ai.rag.retrieval.vector_store import Database
from resource_ai.rag.service import LegalRepository


@lru_cache
def get_repository() -> LegalRepository:
    settings = get_settings()
    embeddings = create_embedding_provider(
        settings.embedding_provider,
        settings.embedding_model,
        settings.embedding_dimension,
    )
    return LegalRepository(
        Database(settings.database_url),
        embeddings,
        candidate_multiplier=settings.hybrid_candidate_multiplier,
        rrf_k=settings.hybrid_rrf_k,
    )

