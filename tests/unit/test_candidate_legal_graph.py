"""Process KG -> legal graph integration; fixtures are not legal ground truth."""

from resource_ai.agents.research import CandidateResearchService
from resource_ai.api.schemas.analysis import ResearchRequest
from resource_ai.legal_graph.service import LegalGraphService
from tests.legal_graph_fixtures import MemoryStore, enriched
from tests.unit.test_candidate_research import FakeGraph


class NeverHybrid:
    def search(self, *args):
        raise AssertionError("Hierarchical mode must not silently become plain hybrid RAG")


def test_process_candidates_share_budget_and_keep_proposed_route_as_hypothesis():
    snapshot, embeddings, model = enriched()
    service = LegalGraphService(MemoryStore(snapshot), embeddings, model)
    result = CandidateResearchService(
        FakeGraph(), NeverHybrid(), legal_graph_factory=lambda: service
    ).run(
        ResearchRequest(
            lot={"material": "PP", "input_form": "scrap"},
            legal_graph={
                "query": "가상 위탁 검토",
                "facts": {"permit_confirmed": True},
                "max_model_calls": 9,
            },
        )
    )
    assert len(result.candidates) == 3
    assert result.coverage["legal_method"] == "hierarchical_graph"
    assert result.coverage["legal_graph_model_calls"] <= 9
    assert not result.legal_searches
    assert any(c.legal_graph_review.get("error_code") == "model-budget" for c in result.candidates)
    assert all(c.recommended is False for c in result.candidates)
    payload = next(payload for task, payload in model.calls if task == "plan_query")
    assert "proposed_route" in payload["facts"]


def test_model_disabled_remains_visible_per_candidate():
    from resource_ai.legal_graph.models import LegalGraphError

    def disabled():
        raise LegalGraphError("model-not-configured", "미연결")

    result = CandidateResearchService(FakeGraph(), NeverHybrid(), legal_graph_factory=disabled).run(
        ResearchRequest(lot={"material": "PP"}, legal_graph={"query": "가상 검토"})
    )
    assert all(
        c.legal_graph_review["error_code"] == "model-not-configured" for c in result.candidates
    )
    assert result.route == "REVIEW"
