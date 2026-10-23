from functools import lru_cache

from resource_ai.core.config import get_settings
from resource_ai.knowledge_graph.query import GraphQuery
from resource_ai.rag.retrieval.embeddings import create_embedding_provider
from resource_ai.rag.retrieval.vector_store import Database
from resource_ai.rag.service import EvidenceRepository


@lru_cache
def get_graph_query() -> GraphQuery:
    return GraphQuery()


@lru_cache
def get_repository() -> EvidenceRepository:
    settings = get_settings()
    embeddings = create_embedding_provider(
        settings.embedding_provider,
        settings.embedding_model,
        settings.embedding_dimension,
    )
    return EvidenceRepository(
        Database(
            settings.database_url, connect_timeout_seconds=settings.database_connect_timeout_seconds
        ),
        embeddings,
        candidate_multiplier=settings.hybrid_candidate_multiplier,
        rrf_k=settings.hybrid_rrf_k,
    )
