"""The replay must repair only declared structural/unit faults and then stop."""

from copy import deepcopy

import pytest

from resource_ai.agents.decision_review import DecisionReviewService
from resource_ai.agents.research import CandidateResearchService
from resource_ai.api.schemas.analysis import ResearchRequest
from resource_ai.api.schemas.decision_review import ReviewTemplateRequest
from scripts.replay_critic_revision import build_replay, normalize_declared_cost_units
from tests.unit.test_candidate_research import FakeGraph, FakeRepository


def setup():
    class CountingResearch(CandidateResearchService):
        query_count = 0

        def run(self, request):
            self.query_count += 1
            return super().run(request)

    service = CountingResearch(FakeGraph(), FakeRepository())
    request = ResearchRequest(
        lot={"material": "PP", "input_form": "scrap", "quantity_kg": 3000, "page_size": 3},
        max_legal_queries=1,
    )
    template = DecisionReviewService(service).template(ReviewTemplateRequest(lot=request.lot))
    return service, request, template


def test_actual_critic_drives_fresh_query_unit_conversion_and_human_stop():
    service, request, template = setup()
    trace = build_replay(service, request, template)
    assert trace["all_passed"]
    assert service.query_count == 2  # First research + real refresh, not cached restoration.
    assert [r["route"] for r in trace["rounds"]] == ["REVISE", "REVISE", "REVIEW"]
    assert trace["rounds"][0]["router"]["restart_from"] == "kg"
    assert trace["rounds"][1]["router"]["restart_from"] == "calculation"
    assert trace["rounds"][2]["router"]["restart_from"] is None
    assert "kg_handoff_count_mismatch" in trace["rounds"][1]["resolved_error_codes"]
    assert "calculation_factor_unit_mismatch" in trace["rounds"][2]["resolved_error_codes"]
    patch = trace["actions"][1]["patches"][0]
    assert patch["before"]["factor"] == "20000" and patch["after"]["factor"] == "20"
    assert patch["after"]["activity"] == patch["before"]["activity"] == "3000"
    assert trace["stop_reason"] == "human_verification_required"
    assert trace["rounds"][-1]["missing_count"] > 0
    assert not trace["rounds"][-1]["dispatch_approved"]
    assert all(not r["router"]["automatic_reexecution"] for r in trace["rounds"])


@pytest.mark.parametrize("max_repairs", [True, -1, 3, 1.5])
def test_revision_budget_rejected_before_any_query(max_repairs):
    with pytest.raises(ValueError, match="at most two"):
        build_replay(None, None, None, max_repairs=max_repairs)


def test_zero_repair_budget_preserves_fault_and_stops():
    service, request, template = setup()
    trace = build_replay(service, request, template, max_repairs=0)
    assert len(trace["rounds"]) == 1 and trace["actions"] == []
    assert trace["stop_reason"] == "repair_limit" and trace["rounds"][0]["errors"]
    assert service.query_count == 1


def test_demo_rejects_a_different_lot_instead_of_mislabeling_it_as_three_tonnes():
    service, request, template = setup()
    request.lot.quantity_kg = 4000
    with pytest.raises(ValueError, match="PP scrap, 3000 kg"):
        build_replay(service, request, template)
    assert service.query_count == 0


def test_graph_change_does_not_rebind_old_numbers_or_make_a_fake_success():
    service, request, template = setup()

    class ChangingResearch:
        def run(self, request):
            value = service.run(request)
            if service.query_count > 1:
                value.graph_trace["generation"] = "new-generation"
            return value

    trace = build_replay(ChangingResearch(), request, template)
    assert trace["stop_reason"] == "graph_changed_requires_new_numeric_binding"
    assert trace["actions"] == [] and not trace["all_passed"]


def test_conversion_preserves_input_and_evidence_and_rejects_unknown_units():
    source = {
        "comparison": {
            "evidence": [{"evidence_id": "quote", "kind": "cost_factor"}],
            "scenarios": [
                {
                    "candidate_id": "A",
                    "lines": [
                        {
                            "line_id": "line",
                            "metric": "cost",
                            "activity": "3000",
                            "activity_unit": "kg",
                            "factor": "20000",
                            "factor_unit": "KRW/t",
                            "factor_evidence_id": "quote",
                        }
                    ],
                }
            ],
        }
    }
    original = deepcopy(source)
    converted, patches = normalize_declared_cost_units(source)
    assert source == original
    assert converted["comparison"]["scenarios"][0]["lines"][0]["factor"] == "20"
    assert patches[0]["evidence_id"] == "quote" and not patches[0]["source_content_verified"]
    source["comparison"]["scenarios"][0]["lines"][0]["factor_unit"] = "unknown-price-unit"
    with pytest.raises(ValueError, match="Unsupported"):
        normalize_declared_cost_units(source)
