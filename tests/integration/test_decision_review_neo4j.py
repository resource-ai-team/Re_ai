"""Real isolated Neo4j routes with synthetic lots/numbers and no legal model."""

import os

import pytest
from fastapi.testclient import TestClient

from resource_ai.agents.decision_review import DecisionReviewService
from resource_ai.agents.research import CandidateResearchService
from resource_ai.api.routes.analysis import get_research_service
from resource_ai.api.schemas.analysis import ResearchRequest
from resource_ai.api.schemas.decision_review import DecisionReviewRequest, ReviewTemplateRequest
from resource_ai.main import app
from scripts.evaluate_decision_review import synthetic_numeric
from tests.integration.test_analysis_neo4j import (
    EmptyLegalRepository,
    analysis_graph,  # noqa: F401 - shared isolated namespace fixture
)

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_NEO4J_INTEGRATION") != "true", reason="Real Neo4j required"
)


@pytest.fixture
def review_service(analysis_graph):  # noqa: F811 - pytest injects the imported fixture
    return DecisionReviewService(CandidateResearchService(analysis_graph, EmptyLegalRepository()))


def request():
    return ResearchRequest(
        lot={"material": "PP", "input_form": "scrap", "quantity_kg": 3000, "page_size": 1}
    )


def test_actual_graph_worksheet_and_missing_data_review(review_service):
    research = request()
    template = review_service.template(ReviewTemplateRequest(lot=research.lot))
    assert len(template["candidates"]) == 3
    assert template["coverage"]["complete_within_curated_scope"]
    assert template["graph_trace"]["generation"]
    assert template["graph_trace"]["reference_version"] == "planning-reference-v2"
    result = review_service.run(DecisionReviewRequest(research=research))
    assert result.route == "REVIEW"
    assert len(result.candidate_reviews) == 3
    assert any(marker["on_map"] for marker in result.map_markers)
    assert not result.numeric_review["included_candidate_ids"]
    assert not result.final_recommendations and not result.dispatch_approved


@pytest.mark.parametrize("fault", ["none", "stale_graph", "forged_stage"])
def test_actual_graph_numeric_binding_never_approves_dispatch(review_service, fault):
    research = request()
    template = review_service.template(ReviewTemplateRequest(lot=research.lot))
    numeric = synthetic_numeric(template)
    rejected = None
    if fault == "stale_graph":
        numeric["graph_trace"] = {"generation": "synthetic-stale"}
    if fault == "forged_stage":
        row = numeric["comparison"]["scenarios"][0]
        rejected = row["candidate_id"]
        old, row["stage_ids"][0] = row["stage_ids"][0], "synthetic-forged-stage"
        for line in row["lines"]:
            if line["target_id"] == old:
                line["target_id"], line["factor"] = "synthetic-forged-stage", "0"
    previous = dict(app.dependency_overrides)
    app.dependency_overrides[get_research_service] = lambda: review_service.research_service
    try:
        response = TestClient(app).post(
            "/analysis/review",
            json={"research": research.model_dump(mode="json"), "numeric": numeric},
        )
    finally:
        app.dependency_overrides = previous
    assert response.status_code == 200
    result = response.json()
    assert result["route"] == ("REVIEW" if fault == "none" else "REVISE")
    assert len(result["candidate_reviews"]) == 3
    assert (
        len(result["numeric_review"]["included_candidate_ids"])
        == {
            "none": 3,
            "stale_graph": 0,
            "forged_stage": 2,
        }[fault]
    )
    if rejected:
        assert rejected not in result["numeric_review"]["included_candidate_ids"]
        assert all(rejected not in front for front in result["numeric_review"]["pareto_fronts"])
    assert result["final_recommendations"] == [] and result["dispatch_approved"] is False
    assert not result["router"]["automatic_reexecution"]
