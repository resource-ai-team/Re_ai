from resource_ai.rag.retrieval.embeddings import HashEmbeddingProvider


def test_hash_embedding_is_deterministic_and_normalized() -> None:
    provider = HashEmbeddingProvider(dimension=32)
    first = provider.embed_query("폐기물 처리 허가")
    second = provider.embed_query("폐기물 처리 허가")
    assert first == second
    assert round(sum(value * value for value in first), 6) == 1.0
