"""서로 다른 검색 순위를 RRF로 결합하는 Hybrid Search 기능."""

from collections.abc import Sequence
from dataclasses import replace

from resource_ai.evidence.models import SearchResult


def reciprocal_rank_fusion(
    vector_results: Sequence[SearchResult],
    keyword_results: Sequence[SearchResult],
    *,
    limit: int,
    rrf_k: int = 60,
) -> list[SearchResult]:
    """척도가 다른 원점수를 직접 비교하지 않고 검색 순위만으로 후보를 결합한다."""
    if limit <= 0:
        raise ValueError("limit must be positive")
    if rrf_k < 0:
        raise ValueError("rrf_k must not be negative")

    records: dict[int, SearchResult] = {}
    scores: dict[int, float] = {}
    methods: dict[int, set[str]] = {}

    for method, results in (("vector", vector_results), ("keyword", keyword_results)):
        for rank, result in enumerate(results, start=1):
            records.setdefault(result.chunk_id, result)
            scores[result.chunk_id] = scores.get(result.chunk_id, 0.0) + 1.0 / (rrf_k + rank)
            methods.setdefault(result.chunk_id, set()).add(method)

    ranked_ids = sorted(scores, key=lambda chunk_id: (-scores[chunk_id], chunk_id))[:limit]
    return [
        replace(
            records[chunk_id],
            score=scores[chunk_id],
            retrieval_method="+".join(sorted(methods[chunk_id])),
        )
        for chunk_id in ranked_ids
    ]
