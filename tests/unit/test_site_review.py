import pytest
from fastapi.testclient import TestClient

from resource_ai.agents.kg_lci import KGPathLCIIntegrityError
from resource_ai.agents.research import CandidateResearchService
from resource_ai.agents.site_review import SiteReviewService
from resource_ai.api.routes.analysis import get_research_service, get_site_review_lci_datasets
from resource_ai.api.schemas.site_review import SiteReviewRequest
from resource_ai.core.dependencies import get_graph_query
from resource_ai.main import app
from tests.unit.test_candidate_research import FakeRepository
from tests.unit.test_site_discovery import Graph, request


def scenario(**changes):
    return SiteReviewRequest.model_validate(
        {**request().model_dump(), "max_legal_queries": 1, "max_technical_queries": 0, **changes}
    )


def test_site_evidence_risks_and_lci_remain_one_snapshot_without_invented_totals():
    graph = Graph()
    result = SiteReviewService(graph, CandidateResearchService(graph, FakeRepository()), []).run(
        scenario()
    )
    assert (
        len(result.site.candidates)
        == len(result.review.candidate_reviews)
        == len(result.lci.results)
        == 3
    )
    assert result.review.binding["site_context_sha256"] == result.site.context_sha256
    assert result.lci.graph_trace == result.site.graph_trace == result.review.research.graph_trace
    assert all(r["carbon_kgco2e"] is None for r in result.lci.results)
    assert result.review.map_markers and result.report_markdown
    assert (
        result.route == "REVIEW"
        and not result.final_recommendations
        and not result.dispatch_approved
    )


def test_optional_lci_does_not_invent_a_zero_estimate():
    graph = Graph()
    result = SiteReviewService(graph, CandidateResearchService(graph, FakeRepository()), []).run(
        scenario(include_lci=False)
    )
    assert result.lci is None


def test_generation_change_between_composed_steps_rejects_mixed_results():
    class OtherGraph(Graph):
        def assessed_plans(self, request):
            value = super().assessed_plans(request)
            value.planning.graph_trace["generation"] = "different-generation"
            return value

    with pytest.raises(KGPathLCIIntegrityError):
        SiteReviewService(
            Graph(), CandidateResearchService(OtherGraph(), FakeRepository()), []
        ).run(scenario(include_lci=False))


def test_composed_api_uses_existing_dependencies_and_source_contracts():
    graph = Graph()
    previous = dict(app.dependency_overrides)
    app.dependency_overrides[get_graph_query] = lambda: graph
    app.dependency_overrides[get_research_service] = lambda: CandidateResearchService(
        graph, FakeRepository()
    )
    app.dependency_overrides[get_site_review_lci_datasets] = lambda: []  # noqa: PIE807 - FastAPI inspects callable parameters.
    try:
        response = TestClient(app).post(
            "/analysis/site-review", json=scenario().model_dump(mode="json")
        )
    finally:
        app.dependency_overrides = previous
    assert response.status_code == 200
    assert response.json()["contract_version"] == "site-grounded-review-v1"


def test_lci_optout_does_not_open_optional_local_files(monkeypatch):
    def unavailable():
        raise AssertionError("optional dataset loader must not be called")

    monkeypatch.setattr("resource_ai.api.routes.analysis.get_lci_datasets", unavailable)
    assert get_site_review_lci_datasets(scenario(include_lci=False)) == []
