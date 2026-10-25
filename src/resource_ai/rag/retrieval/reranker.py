"""RRF로 결합한 검색 후보를 문서 그룹별 결정론적 규칙으로 재정렬한다."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import replace
from datetime import date
from urllib.parse import urlparse

from resource_ai.evidence.models import (
    CorpusType,
    SearchGroup,
    SearchResult,
    corpus_types_for_search_group,
)

_TERM_RE = re.compile(r"[0-9A-Za-z가-힣]+")


def _terms(value: str) -> set[str]:
    return {term.lower() for term in _TERM_RE.findall(value) if len(term) >= 2}


def _overlap_counts(query_terms: set[str], result: SearchResult) -> tuple[int, int, int]:
    title = len(query_terms & _terms(result.title))
    locator = len(query_terms & _terms(f"{result.heading} {result.citation or ''}"))
    content = len(query_terms & _terms(result.content))
    return title, locator, content


def _legal_bonus(result: SearchResult, overlaps: tuple[int, int, int], as_of: date) -> float:
    title, locator, content = overlaps
    hostname = (urlparse(result.source_url).hostname or "").lower()
    official_bonus = 0.001 if hostname in {"law.go.kr", "www.law.go.kr"} else 0.0
    effective_bonus = 0.0
    if result.document_type == CorpusType.LAW and result.effective_from is not None:
        effective_bonus = 0.0005 if result.effective_from <= as_of else -0.01
    return official_bonus + effective_bonus + title * 0.001 + locator * 0.002 + content * 0.0002


def _technical_bonus(result: SearchResult, overlaps: tuple[int, int, int]) -> float:
    title, locator, content = overlaps
    position_bonus = 0.0005 if result.source_position or result.page_no or result.citation else 0.0
    return position_bonus + title * 0.0015 + locator * 0.002 + content * 0.0005


def _company_bonus(result: SearchResult, overlaps: tuple[int, int, int]) -> float:
    title, locator, content = overlaps
    position_bonus = 0.0005 if result.source_position or result.page_no or result.citation else 0.0
    return position_bonus + title * 0.0015 + locator * 0.0025 + content * 0.0005


def _generic_bonus(overlaps: tuple[int, int, int]) -> float:
    title, locator, content = overlaps
    return title * 0.001 + locator * 0.001 + content * 0.0002


def rerank_results(
    query: str,
    results: Sequence[SearchResult],
    *,
    limit: int,
    search_group: SearchGroup | str | None = None,
    as_of: date | None = None,
) -> list[SearchResult]:
    """검색 그룹 밖 결과를 차단하고 문서 유형에 맞는 가산점으로 Top K를 만든다."""
    if limit <= 0:
        raise ValueError("limit must be positive")
    group = SearchGroup(search_group) if search_group is not None else None
    if group is not None:
        allowed_types = set(corpus_types_for_search_group(group))
        unexpected = sorted({result.document_type for result in results} - allowed_types)
        if unexpected:
            raise ValueError(
                f"Search results contain document types outside {group.value}: "
                f"{', '.join(unexpected)}"
            )

    as_of = as_of or date.today()
    query_terms = _terms(query)
    reranked: list[SearchResult] = []
    for result in results:
        overlaps = _overlap_counts(query_terms, result)
        if result.document_type in {CorpusType.LAW, CorpusType.PRECEDENT}:
            bonus = _legal_bonus(result, overlaps, as_of)
        elif result.document_type == CorpusType.TECHNICAL_LITERATURE:
            bonus = _technical_bonus(result, overlaps)
        elif result.document_type == CorpusType.COMPANY_DOCUMENT:
            bonus = _company_bonus(result, overlaps)
        else:
            bonus = _generic_bonus(overlaps)
        reranked.append(
            replace(
                result,
                score=result.score + bonus,
                retrieval_method=f"{result.retrieval_method}+reranked",
            )
        )

    return sorted(reranked, key=lambda result: (-result.score, result.chunk_id))[:limit]


def rerank_legal_results(
    query: str,
    results: Sequence[SearchResult],
    *,
    limit: int,
    as_of: date | None = None,
) -> list[SearchResult]:
    """기존 호출을 유지하면서 법률 그룹 재정렬 정책을 적용한다."""
    return rerank_results(
        query,
        results,
        limit=limit,
        search_group=SearchGroup.LEGAL,
        as_of=as_of,
    )
