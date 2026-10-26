from functools import lru_cache
from pathlib import Path

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
    reranker = None
    if settings.reranker_provider != "rules":
        from resource_ai.rag.retrieval.cross_encoder import OnnxCrossEncoder
        from resource_ai.rag.retrieval.model_catalog import MODEL_CATALOG

        spec = MODEL_CATALOG[settings.reranker_provider]
        folder = settings.reranker_model_directory or spec["directory"]
        reranker = OnnxCrossEncoder(
            Path(folder),
            spec["weight"],
            model_id=f"{spec['repository']}@{spec['revision']}",
            expected_sha256=spec["sha256"],
        )
    embeddings = create_embedding_provider(
        settings.embedding_provider,
        settings.embedding_model,
        settings.embedding_dimension,
    )
    korean_tokenizer = None
    if settings.retrieval_korean_lexical:
        from resource_ai.rag.retrieval.korean_lexical import KoreanLegalTokenizer

        korean_tokenizer = KoreanLegalTokenizer()
    return EvidenceRepository(
        Database(
            settings.database_url, connect_timeout_seconds=settings.database_connect_timeout_seconds
        ),
        embeddings,
        candidate_multiplier=settings.hybrid_candidate_multiplier,
        rrf_k=settings.hybrid_rrf_k,
        candidate_minimum=settings.hybrid_candidate_minimum,
        reranker=reranker,
        article_diversity=settings.retrieval_article_diversity,
        korean_tokenizer=korean_tokenizer,
        legal_basis_enabled=settings.retrieval_legal_basis,
    )
