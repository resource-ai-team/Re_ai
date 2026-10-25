"""Full review on real reference paths, synthetic lots and numeric source claims."""

import pytest
from fastapi.testclient import TestClient

from resource_ai.agents.decision_review import DecisionReviewService, input_hash
from resource_ai.agents.research import CandidateResearchService
from resource_ai.api.routes.analysis import get_research_service
from resource_ai.api.schemas.analysis import ResearchRequest
from resource_ai.api.schemas.decision_review import DecisionReviewRequest, ReviewTemplateRequest
from resource_ai.main import app
from tests.unit.test_candidate_research import FakeGraph, FakeRepository
from tests.unit.test_decision_critic import numeric_for


def setup():
    service = CandidateResearchService(FakeGraph(), FakeRepository())
    request = ResearchRequest(lot={"material": "PP", "input_form": "scrap", "quantity_kg": 3000})
    observed = service.run(request)
    numeric = numeric_for(observed)
    bound = {
        "input_sha256": input_hash(request.lot),
        "graph_trace": observed.graph_trace,
        "comparison": numeric.model_dump(mode="json"),
    }
    return service, request, bound


def review(service, request, bound=None):
    return DecisionReviewService(service).run(
        DecisionReviewRequest(research=request, numeric=bound)
    )


def test_template_only_reads_kg_without_retriever_or_generation_model():
    class NeverRetrieve:
        def search(self, *args):
            raise AssertionError("Template must not call RAG")

    service = DecisionReviewService(CandidateResearchService(FakeGraph(), NeverRetrieve()))
    result = service.template(ReviewTemplateRequest(lot={"material": "PP", "input_form": "scrap"}))
    assert len(result["candidates"]) == 3
    assert result["generation_model_called"] is False
    assert result["basis_template"]["quantity_kg"] is None
    assert result["coverage"]["complete_within_curated_scope"] is True
    for row in result["candidates"]:
        assert all(
            line["activity"] is None and line["factor"] is None for line in row["required_lines"]
        )
        assert any(
            line["target_id"] == row["candidate_id"] + ":origin-transport"
            for line in row["required_lines"]
        )
        assert {line["category"] for line in row["required_lines"]} == {
            "process",
            "transport",
            "residue",
            "revenue",
        }


def test_missing_data_is_review_not_infeasible_and_keeps_every_candidate():
    service, request, _ = setup()
    result = review(service, request)
    assert result.route == "REVIEW"
    assert len(result.candidate_reviews) == 3
    assert not result.numeric_review["pareto_fronts"]
    assert result.router["restart_from"] is None
    assert all(not m["confirmed_operational_incident"] for m in result.map_markers)
    assert any(m["on_map"] and m["target_type"] == "stage" for m in result.map_markers)


@pytest.mark.parametrize(
    "current,previous", [("a" * 64, None), (None, "a" * 64), ("a" * 64, "b" * 64)]
)
def test_changed_or_stripped_site_context_rejects_old_transport_numbers(current, previous):
    service, request, bound = setup()
    bound["site_context_sha256"] = previous
    result = DecisionReviewService(service).run(
        DecisionReviewRequest(
            research=request,
            numeric=bound,
            site_context_sha256=current,
        )
    )
    assert result.route == "REVISE" and not result.numeric_review["included_candidate_ids"]
    assert "numeric_site_changed" in {i.code for i in result.critic}


def test_matching_site_context_is_preserved_without_claiming_identity_verification():
    service, request, bound = setup()
    bound["site_context_sha256"] = "a" * 64
    result = DecisionReviewService(service).run(
        DecisionReviewRequest(
            research=request,
            numeric=bound,
            site_context_sha256="a" * 64,
        )
    )
    assert result.binding["submission_matches"] is True
    assert result.binding["origin_bound"] is True
    assert (
        result.binding["site_context_basis"] == "backend_supplied_digest_not_independently_verified"
    )
    assert not result.dispatch_approved


def test_bound_synthetic_comparison_is_still_not_operational_recommendation():
    service, request, bound = setup()
    result = review(service, request, bound)
    assert result.route == "REVIEW"
    assert len(result.numeric_review["included_candidate_ids"]) == 3
    assert result.numeric_review["pareto_fronts"]
    assert result.final_recommendations == [] and result.dispatch_approved is False
    assert not result.router["pass_gate_enabled"]
    assert not result.numeric_review["evidence_content_audited"]
    assert "최종 추천·허가·시설 인수 승인이 아닙니다" in result.report_markdown
    assert "가상 수치 시뮬레이션" in result.report_markdown
    assert "순비용 (KRW)" in result.report_markdown
    assert "탄소 (kgCO2e)" in result.report_markdown


@pytest.mark.parametrize("field", ["input_sha256", "graph_trace"])
def test_old_lot_or_graph_version_never_reuses_numeric_results(field):
    service, request, bound = setup()
    if field == "input_sha256":
        bound[field] = "b" * 64
    else:
        bound[field] = {"generation": "different-generation"}
    result = review(service, request, bound)
    assert result.route == "REVISE"
    assert result.router["restart_from"] == "kg"
    assert not result.numeric_review["included_candidate_ids"]
    assert not result.numeric_review["pareto_fronts"]
    assert result.router["invalidate_on_revision"] == [
        "kg",
        "validation",
        "calculation",
        "comparison",
        "critic",
        "router",
        "output",
    ]
    assert result.router["automatic_reexecution"] is False


def test_same_plan_ids_do_not_let_another_color_lot_reuse_old_prices():
    service, request, bound = setup()
    request.lot.color = "different-color"
    result = review(service, request, bound)
    assert result.route == "REVISE"
    assert any(i.code == "numeric_input_changed" for i in result.critic)
    assert not result.numeric_review["included_candidate_ids"]


def test_forged_candidate_is_removed_while_other_bound_numeric_candidates_survive():
    service, request, bound = setup()
    forged = bound["comparison"]["scenarios"][0]
    forged["candidate_id"] = "forged-not-in-kg"
    result = review(service, request, bound)
    assert result.route == "REVISE"
    assert forged["candidate_id"] not in result.numeric_review["included_candidate_ids"]
    assert len(result.numeric_review["included_candidate_ids"]) == 2
    assert "비교 제외·확인 필요 | 보류 | 보류" in result.report_markdown
    assert all(
        forged["candidate_id"] not in front for front in result.numeric_review["pareto_fronts"]
    )
    assert len(result.candidate_reviews) == 3


def test_forged_stage_and_its_cheap_line_cannot_win_comparison():
    service, request, bound = setup()
    scenario = bound["comparison"]["scenarios"][0]
    old = scenario["stage_ids"][0]
    scenario["stage_ids"][0] = "made-up-stage"
    for line in scenario["lines"]:
        if line["target_id"] == old:
            line["target_id"], line["factor"] = "made-up-stage", "0"
    result = review(service, request, bound)
    assert result.route == "REVISE"
    assert scenario["candidate_id"] not in result.numeric_review["included_candidate_ids"]
    assert len(result.numeric_review["included_candidate_ids"]) == 2


@pytest.mark.parametrize("quantity", [None, 3001])
def test_missing_or_different_original_quantity_blocks_numeric_comparison(quantity):
    service, request, bound = setup()
    request.lot.quantity_kg = quantity
    bound["input_sha256"] = input_hash(request.lot)  # matching hash alone cannot prove the basis
    result = review(service, request, bound)
    assert result.route == ("REVIEW" if quantity is None else "REVISE")
    assert not result.numeric_review["included_candidate_ids"]


def test_partial_numbers_keep_matching_route_but_never_appear_as_comparable():
    service, request, bound = setup()
    for scenario in bound["comparison"]["scenarios"]:
        scenario["lines"] = []
    result = review(service, request, bound)
    assert result.route == "REVIEW"
    assert all(row["kg_binding"] == "matched" for row in result.numeric_review["results"])
    assert not result.numeric_review["included_candidate_ids"]
    assert all(row["cost_total_krw"] is None for row in result.numeric_review["results"])


def test_ordinary_source_claims_do_not_open_pass_even_with_complete_numbers():
    service, request, bound = setup()
    bound["comparison"]["mode"] = "submitted_evidence"
    for source in bound["comparison"]["evidence"]:
        source["synthetic"] = False
    result = review(service, request, bound)
    assert result.route == "REVIEW"
    assert result.numeric_review["included_candidate_ids"]
    assert not result.final_recommendations and not result.dispatch_approved


def test_review_api_and_template_preserve_legacy_research_contract():
    service, request, bound = setup()
    previous = dict(app.dependency_overrides)
    app.dependency_overrides[get_research_service] = lambda: service
    try:
        client = TestClient(app)
        response = client.post(
            "/analysis/review-template", json={"lot": request.lot.model_dump(mode="json")}
        )
        assert response.status_code == 200
        assert response.json()["generation_model_called"] is False
        response = client.post(
            "/analysis/review", json={"research": request.model_dump(mode="json"), "numeric": bound}
        )
        assert response.status_code == 200
        assert response.json()["route"] == "REVIEW"
        legacy = client.post("/analysis/research", json=request.model_dump(mode="json"))
        assert legacy.status_code == 200
        assert legacy.json()["contract_version"] == "candidate-research-v1"
    finally:
        app.dependency_overrides = previous


def test_template_rejects_mid_page_cursor_and_model_options():
    client = TestClient(app)
    assert (
        client.post(
            "/analysis/review-template", json={"lot": {"material": "PP", "cursor": "old"}}
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/analysis/review-template",
            json={"lot": {"material": "PP"}, "legal_graph": {"query": "not allowed"}},
        ).status_code
        == 422
    )


def test_source_transfer_restriction_blocks_only_affected_candidate():

    class RestrictedGraph(FakeGraph):
        def assessed_plans(self, request):
            result = super().assessed_plans(request)
            if result.planning.items:
                result.planning.items[0].external_model_allowed = False
                result.planning.items[0].withheld_evidence_ids = ["restricted-source"]
            return result

    class SpyLegal:
        def __init__(self):
            self.calls = []

        def pin(self):
            return self

        def run(self, *args, **kwargs):
            self.calls.append(args)
            return {"model_calls": 1, "audit": {"coverage_complete": False}}

    spy = SpyLegal()
    service = CandidateResearchService(
        RestrictedGraph(), FakeRepository(), legal_graph_factory=lambda: spy
    )
    response = service.run(
        ResearchRequest(
            lot={"material": "PP", "page_size": 50}, legal_graph={"query": "가상 법규 확인"}
        )
    )
    assert (
        response.candidates[0].legal_graph_review["error_code"] == "evidence-transfer-not-allowed"
    )
    assert len(spy.calls) == len(response.candidates) - 1
    assert all("restricted-source" not in str(call) for call in spy.calls)


def test_grouped_map_badges_preserve_issues_and_keep_unconfirmed_conditions_distinct():
    markers = [
        {
            "on_map": True,
            "candidate_id": "p",
            "target_type": "stage",
            "target_id": "s",
            "marker_id": "m1",
            "code": "missing_evidence",
            "severity": "missing",
            "evidence_ids": ["e1"],
        },
        {
            "on_map": True,
            "candidate_id": "p",
            "target_type": "stage",
            "target_id": "s",
            "marker_id": "m2",
            "code": "bad_unit",
            "severity": "error",
            "evidence_ids": ["e1", "e2"],
        },
        {
            "on_map": False,
            "candidate_id": None,
            "target_type": "global",
            "target_id": None,
            "marker_id": "m3",
            "code": "global_gap",
            "severity": "missing",
            "evidence_ids": [],
        },
    ]
    groups = DecisionReviewService._marker_groups(markers)
    assert len(groups) == 1 and groups[0]["issue_count"] == 2
    assert groups[0]["error_count"] == 1 and groups[0]["confirmation_count"] == 1
    assert groups[0]["evidence_ids"] == ["e1", "e2"]
    assert groups[0]["marker_ids"] == ["m1", "m2"]
    assert not groups[0]["confirmed_operational_incident"]
