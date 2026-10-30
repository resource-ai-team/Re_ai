from dataclasses import replace
from datetime import date

import pytest

from resource_ai.agents.decision_critic import inspect_review
from resource_ai.agents.decision_review import DecisionReviewService
from resource_ai.agents.research import CandidateResearchService
from resource_ai.api.schemas.analysis import ResearchRequest
from resource_ai.api.schemas.decision_review import DecisionReviewRequest
from resource_ai.rag.legal_baselines import catalog, fetch_baselines, reference_ids
from tests.unit.test_candidate_research import FakeGraph, FakeRepository


class DB:
    def __init__(self, rows):
        self.rows, self.calls = rows, []

    def connect(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, query, params):
        self.calls.append((query, params))
        return self

    def fetchall(self):
        return self.rows


def source_row(**changes):
    row = [
        "waste-business-permit",
        "law:001771:test-version",
        "폐기물관리법",
        "https://www.law.go.kr/법령/폐기물관리법",
        "test-version",
        date(2026, 1, 1),
        "2026-10-25T00:00:00Z",
        1,
        "제25조(폐기물처리업) ⑤항",
        "가상 원문: 업종 구분",
        "폐기물관리법 제25조 ⑤항",
        "폐기물처리업",
        1,
        1,
    ]
    for index, value in changes.items():
        row[int(index)] = value
    return tuple(row)


def test_exact_lookup_preserves_original_locator_version_and_quotes_in_one_snapshot():
    db = DB([source_row()])
    result = {r["id"]: r for r in fetch_baselines(db)}
    permit = result["waste-business-permit"]
    assert permit["status"] == "retrieved" and permit["complete"]
    hit = permit["sources"][0]
    assert hit["content"] == "가상 원문: 업종 구분" and hit["version"] == "test-version"
    assert hit["applicability"] == "not_audited" and len(hit["content_sha256"]) == 64
    assert result["waste-treatment-notification"]["status"] == "not_found"
    sql, _ = db.calls[0]
    assert len(db.calls) == 1 and "d.tenant_id IS NULL" in sql
    assert "d.document_type = 'LAW'" in sql and "d.is_current = true" in sql
    assert "d.effective_from <= CURRENT_DATE" in sql
    assert "c.metadata ->> 'article' = d.article" in sql


@pytest.mark.parametrize(
    ("changes", "flag"),
    [
        ({"10": "폐기물관리법 제25조의3 ①항"}, "source_locator_mismatch"),
        ({"11": "다른 조문 제목"}, "article_title_changed"),
        ({"12": 2}, "partial_article"),
        ({"13": 2}, "ambiguous_current_version"),
        ({"3": "https://law.go.kr.example.org/fake"}, "official_origin_unverified"),
        ({"5": None}, "source_date_or_version_unknown"),
    ],
)
def test_ambiguous_partial_mislabelled_or_unverified_sources_never_complete(changes, flag):
    permit = next(
        r
        for r in fetch_baselines(DB([source_row(**changes)]))
        if r["id"] == "waste-business-permit"
    )
    assert permit["status"] == "review_required" and not permit["complete"]
    assert flag in permit["source_checks"]


def test_catalog_separates_permit_notification_and_self_treatment_without_deciding_applicability():
    assert {
        "waste-business-permit",
        "waste-treatment-notification",
        "waste-facility-installation",
    } <= set(reference_ids("facility"))
    assert len(catalog()[1]) == 64 and len(catalog()[0]["references"]) == 10


class Repository(FakeRepository):
    def legal_baselines(self):
        return fetch_baselines(DB([source_row()]))


def test_primary_permit_source_is_attached_even_when_vector_budget_is_exhausted():
    result = CandidateResearchService(FakeGraph(), Repository()).run(
        ResearchRequest(lot={"material": "PP"}, max_legal_queries=1)
    )
    permit = next(r for r in result.legal_baselines if r["id"] == "waste-business-permit")
    assert permit["complete"]
    assert result.coverage["legal_executed_queries"] == 1
    assert all(c.legal_baseline_tasks for c in result.candidates)
    assert all("legal_baseline_evidence_incomplete" in c.blockers for c in result.candidates)
    findings = inspect_review(result, None, None)
    assert any(
        r["code"] == "legal_baseline_incomplete" and r["target_type"] == "stage" for r in findings
    )
    assert not result.coverage["legal_baseline_applicability_audited"]
    assert not result.dispatch_approved and not result.final_recommendations


def test_legacy_or_unavailable_repository_exposes_missing_baselines():
    result = CandidateResearchService(FakeGraph(), FakeRepository()).run(
        ResearchRequest(lot={"material": "PP"})
    )
    assert len(result.legal_baselines) == 10
    assert all(r["status"] == "unavailable" for r in result.legal_baselines)
    assert not result.coverage["legal_baseline_retrieval_complete"]


def test_confirmed_source_link_error_requests_revision_instead_of_approval():
    class CorruptRepository(FakeRepository):
        def legal_baselines(self):
            return fetch_baselines(DB([source_row(**{"10": "폐기물관리법 제25조의3 ①항"})]))

    result = DecisionReviewService(CandidateResearchService(FakeGraph(), CorruptRepository())).run(
        DecisionReviewRequest(research=ResearchRequest(lot={"material": "PP"}, max_legal_queries=1))
    )
    assert result.route == "REVISE" and result.router["restart_from"] == "validation"
    assert any(i.code == "legal_baseline_integrity_error" for i in result.critic)
    assert not result.dispatch_approved


def test_vector_results_from_a_different_statute_version_trigger_revision():
    class ChangedRepository(Repository):
        def search(self, query, top_k):
            return [
                replace(hit, title="폐기물관리법", version="older-version")
                for hit in super().search(query, top_k)
            ]

    result = DecisionReviewService(CandidateResearchService(FakeGraph(), ChangedRepository())).run(
        DecisionReviewRequest(research=ResearchRequest(lot={"material": "PP"}, max_legal_queries=1))
    )
    assert result.route == "REVISE"
    assert any(
        i.code == "legal_source_version_changed" and i.target_type == "global"
        for i in result.critic
    )
