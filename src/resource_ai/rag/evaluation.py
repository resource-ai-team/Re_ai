from __future__ import annotations

"""검색 그룹별 기대 근거 순위를 이용한 RAG 회귀 평가."""

import json
from dataclasses import dataclass
from pathlib import Path

from resource_ai.evidence.models import SearchGroup, SearchResult


@dataclass(frozen=True)
class RetrievalCase:
    query: str
    expected_any: tuple[str, ...]
    search_group: SearchGroup = SearchGroup.LEGAL
    case_id: str | None = None


@dataclass(frozen=True)
class RetrievalEvaluation:
    query: str
    passed: bool
    matched: str | None
    result_headings: tuple[str, ...]
    first_relevant_rank: int | None = None
    reciprocal_rank: float = 0.0
    search_group: SearchGroup = SearchGroup.LEGAL


@dataclass(frozen=True)
class RetrievalSummary:
    total: int
    passed: int
    pass_rate: float
    recall_at_k: float
    mrr: float


@dataclass(frozen=True)
class RetrievalDataset:
    cases: tuple[RetrievalCase, ...]
    top_k: int
    minimum_recall_at_k: float
    minimum_mrr: float


def _metric(value: object, name: str) -> float:
    metric = float(value)
    if not 0 <= metric <= 1:
        raise ValueError(f"{name} must be between 0 and 1")
    return metric


def summarize_evaluations(evaluations: list[RetrievalEvaluation]) -> RetrievalSummary:
    """개별 평가 결과를 전체 통과율로 요약한다."""
    if not evaluations:
        raise ValueError("At least one evaluation is required")
    passed = sum(evaluation.passed for evaluation in evaluations)
    return RetrievalSummary(
        total=len(evaluations),
        passed=passed,
        pass_rate=passed / len(evaluations),
        recall_at_k=passed / len(evaluations),
        mrr=sum(evaluation.reciprocal_rank for evaluation in evaluations) / len(evaluations),
    )


def summarize_by_group(
    evaluations: list[RetrievalEvaluation],
) -> dict[SearchGroup, RetrievalSummary]:
    """한 검색 그룹의 저하가 전체 평균에 가려지지 않도록 그룹별 지표를 계산한다."""
    grouped: dict[SearchGroup, list[RetrievalEvaluation]] = {}
    for evaluation in evaluations:
        grouped.setdefault(evaluation.search_group, []).append(evaluation)
    return {group: summarize_evaluations(items) for group, items in grouped.items()}


def meets_thresholds(summary: RetrievalSummary, dataset: RetrievalDataset) -> bool:
    """Recall@K와 MRR이 모두 평가셋의 최소 기준을 충족하는지 확인한다."""
    return summary.recall_at_k >= dataset.minimum_recall_at_k and summary.mrr >= dataset.minimum_mrr


def load_retrieval_dataset(path: Path) -> RetrievalDataset:
    """버전 관리되는 JSON 평가셋과 품질 기준을 읽고 검증한다."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    cases = [
        RetrievalCase(
            query=item["query"],
            expected_any=tuple(item["expected_any"]),
            search_group=SearchGroup(item.get("search_group", SearchGroup.LEGAL)),
            case_id=item.get("id"),
        )
        for item in payload.get("cases", [])
    ]
    if not cases:
        raise ValueError("Evaluation file must contain at least one case")
    if any(not case.expected_any for case in cases):
        raise ValueError("Each evaluation case needs expected_any values")
    top_k = int(payload.get("top_k", 5))
    if top_k <= 0:
        raise ValueError("top_k must be positive")
    thresholds = payload.get("thresholds", {})
    return RetrievalDataset(
        cases=tuple(cases),
        top_k=top_k,
        minimum_recall_at_k=_metric(thresholds.get("recall_at_k", 1.0), "recall_at_k"),
        minimum_mrr=_metric(thresholds.get("mrr", 0.0), "mrr"),
    )


def load_retrieval_cases(path: Path) -> list[RetrievalCase]:
    """기존 호출자를 위해 평가 사례 목록만 반환한다."""
    return list(load_retrieval_dataset(path).cases)


def evaluate_case(case: RetrievalCase, results: list[SearchResult]) -> RetrievalEvaluation:
    """Top K 결과에 기대 근거 문자열이 하나 이상 포함됐는지 확인한다."""
    matched = None
    first_relevant_rank = None
    for rank, result in enumerate(results, start=1):
        result_text = f"{result.title}\n{result.heading}\n{result.content}\n{result.citation or ''}"
        matched = next(
            (expected for expected in case.expected_any if expected in result_text), None
        )
        if matched is not None:
            first_relevant_rank = rank
            break
    return RetrievalEvaluation(
        query=case.query,
        passed=matched is not None,
        matched=matched,
        result_headings=tuple(f"{result.title} / {result.heading}" for result in results),
        first_relevant_rank=first_relevant_rank,
        reciprocal_rank=0.0 if first_relevant_rank is None else 1 / first_relevant_rank,
        search_group=case.search_group,
    )
