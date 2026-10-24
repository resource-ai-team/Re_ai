"""RRF로 결합한 법률 검색 후보를 결정론적 규칙으로 재정렬한다."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import replace
from datetime import date
from urllib.parse import urlparse

from resource_ai.evidence.models import SearchResult

_TERM_RE = re.compile(r"[0-9A-Za-z가-힣]+")


def _terms(value: str) -> set[str]:
    return {term.lower() for term in _TERM_RE.findall(value) if len(term) >= 2}


def rerank_legal_results(
    query: str,
    results: Sequence[SearchResult],
    *,
    limit: int,
    as_of: date | None = None,
) -> list[SearchResult]:
    """Hybrid 관련성 점수를 유지하면서 법률 근거 특성에 작은 가산점을 적용한다."""
    if limit <= 0:
        raise ValueError("limit must be positive")
    as_of = as_of or date.today()
    query_terms = _terms(query)
    reranked: list[SearchResult] = []

    for result in results:
        title_overlap = len(query_terms & _terms(result.title))
        evidence_overlap = len(query_terms & _terms(f"{result.heading} {result.citation or ''}"))
        hostname = (urlparse(result.source_url).hostname or "").lower()
        official_bonus = 0.001 if hostname == "www.law.go.kr" else 0.0
        effective_bonus = (
            0.0005 if result.effective_from is None or result.effective_from <= as_of else -0.01
        )
        score = (
            result.score
            + official_bonus
            + effective_bonus
            + title_overlap * 0.001
            + evidence_overlap * 0.002
        )
        reranked.append(
            replace(
                result,
                score=score,
                retrieval_method=f"{result.retrieval_method}+reranked",
            )
        )

    return sorted(reranked, key=lambda result: (-result.score, result.chunk_id))[:limit]
