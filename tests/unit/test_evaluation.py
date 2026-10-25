import json
from datetime import date
from pathlib import Path

import pytest

from resource_ai.evidence.models import SearchGroup, SearchResult
from resource_ai.rag.evaluation import (
    RetrievalCase,
    evaluate_case,
    load_retrieval_cases,
    load_retrieval_dataset,
    meets_thresholds,
    summarize_by_group,
    summarize_evaluations,
)


def _result(heading: str) -> SearchResult:
    return SearchResult(
        chunk_id=1,
        title="폐기물관리법",
        document_type="LAW",
        heading=heading,
        content="공식 원문",
        citation=heading,
        source_url="https://www.law.go.kr/",
        effective_from=date(2026, 1, 1),
        score=0.9,
    )


def test_evaluate_case_matches_expected_evidence() -> None:
    evaluation = evaluate_case(
        RetrievalCase("위탁 의무", ("제17조", "제48조")),
        [_result("제17조 사업장폐기물배출자의 의무")],
    )
    assert evaluation.passed is True
    assert evaluation.matched == "제17조"
    assert evaluation.first_relevant_rank == 1
    assert evaluation.reciprocal_rank == 1.0


def test_evaluate_case_reports_first_relevant_rank() -> None:
    evaluation = evaluate_case(
        RetrievalCase("허가 의무", ("제25조",)),
        [_result("제48조"), _result("제25조")],
    )

    assert evaluation.first_relevant_rank == 2
    assert evaluation.reciprocal_rank == 0.5


def test_load_retrieval_cases_requires_cases(tmp_path: Path) -> None:
    path = tmp_path / "cases.json"
    path.write_text(json.dumps({"cases": []}), encoding="utf-8")
    with pytest.raises(ValueError, match="at least one"):
        load_retrieval_cases(path)


def test_summarize_evaluations_reports_pass_rate() -> None:
    passed = evaluate_case(RetrievalCase("위탁", ("제17조",)), [_result("제17조")])
    failed = evaluate_case(RetrievalCase("허가", ("제25조",)), [_result("제48조")])

    summary = summarize_evaluations([passed, failed])

    assert summary.total == 2
    assert summary.passed == 1
    assert summary.pass_rate == 0.5
    assert summary.recall_at_k == 0.5
    assert summary.mrr == 0.5


def test_load_retrieval_dataset_reads_group_and_thresholds(tmp_path: Path) -> None:
    path = tmp_path / "cases.json"
    path.write_text(
        json.dumps(
            {
                "top_k": 3,
                "thresholds": {"recall_at_k": 0.8, "mrr": 0.6},
                "cases": [
                    {
                        "id": "technical-001",
                        "search_group": "TECHNICAL",
                        "query": "코팅 종이",
                        "expected_any": ["코팅 종이는 맞는 제지 공정을 찾아야 함"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    dataset = load_retrieval_dataset(path)

    assert dataset.top_k == 3
    assert dataset.minimum_recall_at_k == 0.8
    assert dataset.minimum_mrr == 0.6
    assert dataset.cases[0].search_group == "TECHNICAL"


def test_group_summary_and_thresholds_detect_a_weak_group(tmp_path: Path) -> None:
    path = tmp_path / "cases.json"
    path.write_text(
        json.dumps(
            {
                "thresholds": {"recall_at_k": 0.8, "mrr": 0.6},
                "cases": [{"query": "q", "expected_any": ["e"]}],
            }
        ),
        encoding="utf-8",
    )
    dataset = load_retrieval_dataset(path)
    legal = evaluate_case(RetrievalCase("법률", ("제17조",)), [_result("제17조")])
    technical = evaluate_case(
        RetrievalCase("기술", ("기대 근거",), search_group=SearchGroup.TECHNICAL),
        [_result("다른 근거")],
    )

    summaries = summarize_by_group([legal, technical])

    assert meets_thresholds(summaries[SearchGroup.LEGAL], dataset) is True
    assert meets_thresholds(summaries[SearchGroup.TECHNICAL], dataset) is False
