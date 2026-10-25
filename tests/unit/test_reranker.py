from dataclasses import replace
from datetime import date

import pytest

from resource_ai.evidence.models import SearchGroup, SearchResult
from resource_ai.rag.retrieval.reranker import rerank_legal_results, rerank_results


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
    future = replace(future, effective_from=date(2027, 1, 1))
    current = _result(2, "제25조", "https://www.law.go.kr/법령/폐기물관리법")

    results = rerank_legal_results(
        "폐기물관리법",
        [future, current],
        limit=2,
        as_of=date(2026, 10, 20),
    )

    assert results[0].chunk_id == 2


def _group_result(
    chunk_id: int,
    document_type: str,
    *,
    title: str,
    heading: str,
    content: str,
    source_url: str = "https://example.org/source",
    citation: str | None = None,
    page_no: int | None = None,
) -> SearchResult:
    return SearchResult(
        chunk_id=chunk_id,
        title=title,
        document_type=document_type,
        heading=heading,
        content=content,
        citation=citation,
        source_url=source_url,
        effective_from=None,
        score=0.02,
        retrieval_method="keyword+vector",
        version="v1",
        page_no=page_no,
        source_position=f"p.{page_no}" if page_no else None,
    )


def test_technical_reranker_does_not_apply_official_law_bonus() -> None:
    ordinary_host = _group_result(
        1,
        "TECHNICAL_LITERATURE",
        title="PET 재활용",
        heading="세척",
        content="세척 조건",
    )
    law_host = replace(
        ordinary_host,
        chunk_id=2,
        source_url="https://www.law.go.kr/technical",
    )

    results = rerank_results(
        "PET 세척",
        [law_host, ordinary_host],
        limit=2,
        search_group=SearchGroup.TECHNICAL,
    )

    assert results[0].chunk_id == 1


def test_technical_reranker_boosts_process_context_and_source_position() -> None:
    unrelated = _group_result(
        1,
        "TECHNICAL_LITERATURE",
        title="일반 보고서",
        heading="개요",
        content="일반적인 설명",
    )
    relevant = _group_result(
        2,
        "TECHNICAL_LITERATURE",
        title="PP 재활용 공정",
        heading="세척 온도 조건",
        content="PP 세척 온도는 공정 조건에 따라 관리한다.",
        page_no=3,
    )

    results = rerank_results(
        "PP 세척 온도",
        [unrelated, relevant],
        limit=2,
        search_group=SearchGroup.TECHNICAL,
    )

    assert results[0].chunk_id == 2


def test_company_reranker_boosts_test_item_and_source_position() -> None:
    unrelated = _group_result(
        1,
        "COMPANY_DOCUMENT",
        title="처리계획서",
        heading="일정",
        content="처리 일정",
    )
    relevant = _group_result(
        2,
        "COMPANY_DOCUMENT",
        title="성분분석서",
        heading="수분 시험 결과",
        content="수분 함량 5%",
        page_no=2,
    )

    results = rerank_results(
        "수분 시험",
        [unrelated, relevant],
        limit=2,
        search_group=SearchGroup.COMPANY,
    )

    assert results[0].chunk_id == 2


def test_reranker_rejects_result_outside_requested_group() -> None:
    law = _result(1, "제17조", "https://www.law.go.kr/법령/폐기물관리법")

    with pytest.raises(ValueError, match="outside TECHNICAL"):
        rerank_results(
            "세척",
            [law],
            limit=1,
            search_group=SearchGroup.TECHNICAL,
        )
