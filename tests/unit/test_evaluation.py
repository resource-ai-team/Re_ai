import json
from dataclasses import replace
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
    required_cases_pass,
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


def test_locator_scope_does_not_count_body_cross_reference_or_a_different_article():
    case = RetrievalCase(
        "처리업 허가", ("폐기물관리법 제25조",), match_scope="locator", required=True
    )
    wrong = replace(_result("폐기물관리법 제25조의3"), content="폐기물관리법 제25조를 참고")
    assert not evaluate_case(case, [wrong]).passed
    assert not evaluate_case(case, [replace(wrong, citation="폐기물관리법 제17조")]).passed
    assert evaluate_case(case, [_result("폐기물관리법 제25조 ⑤항")]).passed


def test_required_case_failure_cannot_be_hidden_by_average_score():
    good = evaluate_case(RetrievalCase("q", ("제17조",)), [_result("제17조")])
    bad = evaluate_case(RetrievalCase("q", ("제25조",), required=True), [_result("제17조")])
    assert summarize_evaluations([good] * 9 + [bad]).recall_at_k == 0.9
    assert not required_cases_pass([good] * 9 + [bad])


def test_wrong_corpus_cannot_make_a_quality_probe_pass():
    case = RetrievalCase("q", ("제17조",))
    value = evaluate_case(case, [replace(_result("제17조"), document_type="COMPANY_DOCUMENT")])
    assert not value.passed and value.group_violations == ("COMPANY_DOCUMENT",)
    assert value.result_headings == ("[withheld: unexpected corpus]",)


@pytest.mark.parametrize(
    "change",
    [
        {"top_k": True},
        {"top_k": 1.5},
        {"thresholds": {"recall_at_k": True}},
        {"cases": [{"query": "q", "expected_any": "제17조"}]},
    ],
)
def test_malformed_quality_dataset_is_rejected(tmp_path, change):
    p = tmp_path / "bad.json"
    p.write_text(
        json.dumps({"cases": [{"query": "q", "expected_any": ["제17조"]}], **change}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        load_retrieval_dataset(p)
