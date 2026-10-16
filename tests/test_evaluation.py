import json
from datetime import date
from pathlib import Path

import pytest

from app.domain import SearchResult
from app.evaluation import RetrievalCase, evaluate_case, load_retrieval_cases


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


def test_load_retrieval_cases_requires_cases(tmp_path: Path) -> None:
    path = tmp_path / "cases.json"
    path.write_text(json.dumps({"cases": []}), encoding="utf-8")
    with pytest.raises(ValueError, match="at least one"):
        load_retrieval_cases(path)
