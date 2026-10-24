from __future__ import annotations

"""기대 근거 포함 여부를 이용한 기본 검색 회귀 평가."""

import json
from dataclasses import dataclass
from pathlib import Path

from resource_ai.evidence.models import SearchResult


@dataclass(frozen=True)
class RetrievalCase:
    query: str
    expected_any: tuple[str, ...]


@dataclass(frozen=True)
class RetrievalEvaluation:
    query: str
    passed: bool
    matched: str | None
    result_headings: tuple[str, ...]


@dataclass(frozen=True)
class RetrievalSummary:
    total: int
    passed: int
    pass_rate: float


def summarize_evaluations(evaluations: list[RetrievalEvaluation]) -> RetrievalSummary:
    """개별 평가 결과를 전체 통과율로 요약한다."""
    if not evaluations:
        raise ValueError("At least one evaluation is required")
    passed = sum(evaluation.passed for evaluation in evaluations)
    return RetrievalSummary(
        total=len(evaluations),
        passed=passed,
        pass_rate=passed / len(evaluations),
    )


def load_retrieval_cases(path: Path) -> list[RetrievalCase]:
    """버전 관리되는 JSON 평가셋을 검색 평가 사례로 읽는다."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    cases = [
        RetrievalCase(item["query"], tuple(item["expected_any"]))
        for item in payload.get("cases", [])
    ]
    if not cases:
        raise ValueError("Evaluation file must contain at least one case")
    if any(not case.expected_any for case in cases):
        raise ValueError("Each evaluation case needs expected_any values")
    return cases


def evaluate_case(case: RetrievalCase, results: list[SearchResult]) -> RetrievalEvaluation:
    """Top K 결과에 기대 근거 문자열이 하나 이상 포함됐는지 확인한다."""
    result_text = "\n".join(
        f"{result.title}\n{result.heading}\n{result.content}\n{result.citation or ''}"
        for result in results
    )
    matched = next((expected for expected in case.expected_any if expected in result_text), None)
    return RetrievalEvaluation(
        query=case.query,
        passed=matched is not None,
        matched=matched,
        result_headings=tuple(f"{result.title} / {result.heading}" for result in results),
    )
