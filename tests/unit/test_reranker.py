from datetime import date

from resource_ai.evidence.models import SearchResult
from resource_ai.rag.retrieval.reranker import rerank_legal_results


def _result(chunk_id: int, heading: str, source_url: str) -> SearchResult:
    return SearchResult(
        chunk_id=chunk_id,
        title="폐기물관리법",
        document_type="LAW",
        heading=heading,
        content="공식 원문",
        citation=f"폐기물관리법 {heading}",
        source_url=source_url,
        effective_from=date(2026, 1, 1),
        score=0.02,
        retrieval_method="keyword+vector",
    )


def test_reranker_boosts_official_exact_citation_match() -> None:
    unrelated = _result(1, "제25조", "https://example.com/law")
    exact = _result(2, "제17조", "https://www.law.go.kr/법령/폐기물관리법")

    results = rerank_legal_results(
        "폐기물관리법 제17조",
        [unrelated, exact],
        limit=2,
        as_of=date(2026, 10, 20),
    )

    assert results[0].chunk_id == 2
    assert results[0].retrieval_method.endswith("+reranked")


def test_reranker_penalizes_not_yet_effective_law() -> None:
    future = _result(1, "제17조", "https://www.law.go.kr/법령/폐기물관리법")
    future = SearchResult(**{**future.__dict__, "effective_from": date(2027, 1, 1)})
    current = _result(2, "제25조", "https://www.law.go.kr/법령/폐기물관리법")

    results = rerank_legal_results(
        "폐기물관리법",
        [future, current],
        limit=2,
        as_of=date(2026, 10, 20),
    )

    assert results[0].chunk_id == 2
