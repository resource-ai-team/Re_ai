"""Curated reference + synthetic lots/retrieval; no legal correctness claim."""

from datetime import date

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from resource_ai.agents.research import CandidateResearchService
from resource_ai.api.routes.analysis import get_research_service
from resource_ai.api.schemas.analysis import ResearchRequest
from resource_ai.api.schemas.facility_assessment import AssessedPlansResponse, LotFacts
from resource_ai.api.schemas.kg_planning import PathPlanningResponse, PlanningPagination
from resource_ai.evidence.models import SearchResult
from resource_ai.knowledge_graph.facility_assessment import assess_capability
from resource_ai.knowledge_graph.graph_store import GraphStoreError
from resource_ai.knowledge_graph.planning import enumerate_paths, make_plan
from resource_ai.knowledge_graph.planning_reference import reference
from resource_ai.main import app


class FakeGraph:
    def __init__(self, change=None):
        self.calls = []
        self.change = change

    def assessed_plans(self, request):
        self.calls.append(request)
        value = reference()
        paths, _ = enumerate_paths(value, "pp", "scrap", None, 6)
        all_plans = [
            make_plan(value, p, audience="internal", assumed_form=False) for p in paths.paths
        ]
        offset = int(request.cursor or 0)
        selected = all_plans[offset : offset + request.page_size]
        end = offset + len(selected)
        assessments = []
        for plan in selected:
            stages = []
            for index, stage in enumerate(plan.research_packet["stages"]):
                cap = next(c for c in value.capabilities if c.key == stage["capability"]["key"])
                facts = (
                    LotFacts(material="PP", designated_waste=request.designated_waste)
                    if index == 0
                    else LotFacts()
                )
                assessment = assess_capability(value, cap, facts, projected=index > 0)
                stages.append(
                    {"stage_id": stage["stage_id"], "assessment": assessment.model_dump()}
                )
            assessments.append(
                {
                    "plan_id": plan.plan_id,
                    "status": "unmet"
                    if any(s["assessment"]["status"] == "unmet" for s in stages)
                    else "needs_verification",
                    "stages": stages,
                }
            )
        response = AssessedPlansResponse(
            planning=PathPlanningResponse(
                status="plans",
                normalized_material="pp",
                input_context={},
                missing_fields=[],
                items=selected,
                pagination=PlanningPagination(
                    enumerated_plans=len(all_plans),
                    returned_count=len(selected),
                    has_more=end < len(all_plans),
                    next_cursor=str(end) if end < len(all_plans) else None,
                ),
                graph_trace={"generation": "synthetic-generation"},
                search_scope={"all_paths_enumerated": True},
                limitations=[],
            ),
            assessments=assessments,
            limitations=[],
        )
        if self.change:
            self.change(response, len(self.calls))
        return response


class FakeRepository:
    def __init__(self, mode="normal"):
        self.calls = []
        self.mode = mode

    def search(self, query, top_k):
        self.calls.append((query, top_k))
        if self.mode == "error":
            raise RuntimeError("sensitive-connection-string-test")
        if self.mode == "empty":
            return []
        return [
            SearchResult(
                chunk_id=1,
                title="가상 법령 검색 응답",
                document_type="LAW",
                heading="가상 조문",
                content="테스트용 가상 조문. 실재 법률 주장이 아님.",
                citation="가상 조문",
                source_url="https://example.invalid/test"
                if self.mode == "flagged"
                else "https://www.law.go.kr/test-fixture-not-real",
                effective_from=date(2099, 1, 1) if self.mode == "flagged" else date(2020, 1, 1),
                score=0.5,
            )
        ]


def run(graph=None, repository=None, **fields):
    request = ResearchRequest(
        lot={"material": "PP", "input_form": "scrap", "page_size": 1}, **fields
    )
    return CandidateResearchService(graph or FakeGraph(), repository or FakeRepository()).run(
        request
    )


def test_all_pages_all_tasks_and_deduplicated_legal_searches():
    graph, repo = FakeGraph(), FakeRepository()
    result = run(graph, repo)
    assert len(result.candidates) == len(graph.calls) == 3
    assert result.coverage["complete_within_curated_scope"] is True
    assert result.coverage["legal_retrieval_complete"] is True
    assert len(repo.calls) == len(result.legal_searches)
    assert len(repo.calls) == len({q for q, _ in repo.calls})
    for candidate in result.candidates:
        packet = candidate.plan.research_packet
        assert len(candidate.legal_tasks) == 4 * len(packet["stages"])
        stage_ids = {s["stage_id"] for s in packet["stages"]}
        leg_ids = {h["leg_id"] for h in packet["handoffs"]}
        assert {t["target_id"] for t in candidate.legal_tasks} == stage_ids | leg_ids
        assert candidate.recommended is False
    assert result.route == "REVIEW" and not result.final_recommendations
    assert result.coverage["legal_audit_complete"] is False


def test_budget_never_hides_tasks_or_claims_complete_retrieval():
    repo = FakeRepository()
    result = run(repository=repo, max_legal_queries=1)
    assert len(result.candidates) == 3 and len(repo.calls) == 1
    assert any(s.status == "budget_exhausted" for s in result.legal_searches)
    assert all(c.legal_tasks for c in result.candidates)
    assert any("legal_search_budget_exhausted" in c.blockers for c in result.candidates)
    assert not result.coverage["legal_retrieval_complete"]


def test_unavailable_provider_is_not_retried_for_every_query_in_one_run():
    repo = FakeRepository("error")
    result = run(repository=repo)
    assert len(repo.calls) == result.coverage["legal_executed_queries"] == 1
    assert result.coverage["legal_provider_failed"] is True
    assert sum(s.attempted for s in result.legal_searches) == 1
    assert len(result.legal_searches) > 1
    assert {s.status for s in result.legal_searches} == {"unavailable"}


def test_page_limit_keeps_cursor_and_incomplete_scope():
    result = run(max_pages=1)
    assert len(result.candidates) == 1
    assert result.coverage["next_cursor"] == "1"
    assert result.coverage["enumerated_in_graph"] == 3
    assert not result.coverage["complete_within_curated_scope"]


@pytest.mark.parametrize("mode,state", [("empty", "no_hits"), ("error", "unavailable")])
def test_no_hits_and_errors_are_gaps_not_permission_or_rejection(mode, state):
    result = run(repository=FakeRepository(mode))
    assert {s.status for s in result.legal_searches} == {state}
    assert {c.status for c in result.candidates} == {"needs_verification"}
    assert "sensitive-connection-string-test" not in result.model_dump_json()
    assert not result.coverage["legal_retrieval_complete"]


def test_future_and_unofficial_evidence_is_kept_but_flagged():
    result = run(repository=FakeRepository("flagged"))
    assert all(s.results for s in result.legal_searches)
    flags = result.legal_searches[0].results[0]["source_checks"]
    assert flags == ["official_origin_not_confirmed", "future_effective_date"]
    assert all("legal_source_review_required" in c.blockers for c in result.candidates)
    assert result.route == "REVIEW"


@pytest.mark.parametrize("kind", ["generation", "counts", "assessment", "stage_target", "loop"])
def test_inconsistent_kg_snapshots_fail_closed(kind):
    def change(response, call):
        if kind == "generation" and call == 2:
            response.planning.graph_trace["generation"] = "changed"
        elif kind == "counts" and call == 2:
            response.planning.pagination.enumerated_plans += 1
        elif kind == "assessment":
            response.assessments = []
        elif kind == "stage_target":
            response.assessments[0]["stages"][0]["stage_id"] = "other-plan:stage:1"
        elif kind == "loop" and call == 2:
            response.planning.pagination.next_cursor = "1"

    with pytest.raises(GraphStoreError):
        run(FakeGraph(change))


def test_unmet_paths_are_retained_in_full_research():
    result = CandidateResearchService(FakeGraph(), FakeRepository()).run(
        ResearchRequest(lot={"material": "PP", "page_size": 1, "designated_waste": True})
    )
    assert len(result.candidates) == 3
    assert {c.status for c in result.candidates} == {"unmet"}
    assert all(c.legal_tasks for c in result.candidates)


def test_empty_graph_does_not_initialize_or_call_repository():
    class Empty:
        def assessed_plans(self, request):
            return AssessedPlansResponse(
                planning=PathPlanningResponse(
                    status="needs_input",
                    input_context={},
                    missing_fields=["material"],
                    limitations=[],
                ),
                assessments=[],
                limitations=[],
            )

    repo = FakeRepository()
    result = run(Empty(), repo)
    assert not repo.calls and not result.candidates
    assert result.coverage["missing_fields"] == ["material"]
    assert result.coverage["legal_retrieval_complete"] is False


@pytest.mark.parametrize("lot", [{"cursor": "1"}, {"audience": "external_model"}])
def test_analysis_cannot_start_mid_pool_or_export_internal_evidence(lot):
    with pytest.raises(ValidationError):
        ResearchRequest(lot=lot)


def test_research_api_returns_candidate_trace_and_review_status():
    previous = dict(app.dependency_overrides)
    app.dependency_overrides[get_research_service] = lambda: CandidateResearchService(
        FakeGraph(), FakeRepository()
    )
    try:
        client = TestClient(app)
        response = client.post(
            "/analysis/research", json={"lot": {"material": "PP", "page_size": 1}}
        )
        assert response.status_code == 200
        assert len(response.json()["candidates"]) == 3
        assert response.json()["route"] == "REVIEW"
        assert (
            client.post("/analysis/research", json={"lot": {}, "max_pages": 21}).status_code == 422
        )
    finally:
        app.dependency_overrides = previous


@pytest.mark.parametrize(
    "code,expected", [("invalid-goal", 400), ("stale-cursor", 409), ("integrity", 503)]
)
def test_research_api_preserves_input_conflict_and_backend_failure_status(code, expected):
    class FailingService:
        def run(self, request):
            raise GraphStoreError(code, "safe diagnostic")

    previous = dict(app.dependency_overrides)
    app.dependency_overrides[get_research_service] = FailingService
    try:
        response = TestClient(app).post("/analysis/research", json={"lot": {"material": "PP"}})
        assert response.status_code == expected
        assert response.json()["detail"]["code"] == code
    finally:
        app.dependency_overrides = previous
