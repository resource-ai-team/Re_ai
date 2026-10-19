from dataclasses import replace
from datetime import date

import pytest

from resource_ai.evidence.models import SearchResult
from resource_ai.rag.retrieval.retriever import reciprocal_rank_fusion


def _result(chunk_id: int, heading: str) -> SearchResult:
    return SearchResult(
        chunk_id=chunk_id,
        title="폐기물관리법",
        document_type="LAW",
        heading=heading,
        content="공식 원문",
        citation=heading,
        source_url="https://www.law.go.kr/법령/폐기물관리법",
        effective_from=date(2026, 1, 1),
        score=0.5,
    )


def test_rrf_prioritizes_candidate_found_by_both_retrievers() -> None:
    shared = _result(1, "제17조")
    vector_only = _result(2, "제25조")
    keyword_only = _result(3, "제48조")

    results = reciprocal_rank_fusion(
        [vector_only, shared],
        [keyword_only, replace(shared, score=0.9)],
        limit=3,
    )

    assert results[0].chunk_id == shared.chunk_id
    assert results[0].retrieval_method == "keyword+vector"


def test_rrf_rejects_invalid_limit() -> None:
    with pytest.raises(ValueError, match="limit"):
        reciprocal_rank_fusion([], [], limit=0)
