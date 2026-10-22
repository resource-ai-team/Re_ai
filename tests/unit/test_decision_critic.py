"""Synthetic submissions and faults; no legal/quote/carbon ground truth claims."""

from copy import deepcopy

import pytest

from resource_ai.agents.decision_critic import inspect_review
from resource_ai.agents.research import CandidateResearchService
from resource_ai.api.schemas.analysis import ResearchRequest, ScenarioComparisonRequest
from resource_ai.api.schemas.decision_review import CriticIssue
from resource_ai.calculators.scenarios import compare_scenarios
from tests.unit.test_candidate_research import FakeGraph, FakeRepository


@pytest.fixture
def research():
    return CandidateResearchService(FakeGraph(), FakeRepository()).run(
        ResearchRequest(lot={"material": "PP", "input_form": "scrap", "quantity_kg": 3000})
    )


def numeric_for(research):
    basis = {
        "quantity_kg": "3000",
        "as_of": "2026-10-22",
        "region": "KR",
        "assessment_year": 2026,
        "carbon_method": "SYNTHETIC-TEST-ONLY",
    }
    evidence = [
        {
            "evidence_id": kind,
            "source_url": "https://example.invalid/synthetic-only",
            "locator": "synthetic fixture",
            "kind": kind,
            "valid_from": "2020-01-01",
            "valid_until": "2030-01-01",
            "region": "KR",
            "assessment_year": 2026,
            "synthetic": True,
            "carbon_method": "SYNTHETIC-TEST-ONLY",
        }
        for kind in ("activity_record", "cost_factor", "carbon_factor")
    ]
    scenarios = []
    for index, candidate in enumerate(research.candidates):
        pid, packet = candidate.plan.plan_id, candidate.plan.research_packet
        stages = [s["stage_id"] for s in packet["stages"]]
        legs = [leg["leg_id"] for leg in packet["handoffs"]]
        lines = []
        for category, targets in [
            ("process", stages),
            ("transport", [pid + ":origin-transport", *legs]),
            ("residue", [pid]),
            ("revenue", [pid]),
        ]:
            for target in targets:
                for metric in ("cost",) if category == "revenue" else ("cost", "carbon"):
                    lines.append(
                        {
                            "line_id": f"{pid}:{len(lines)}",
                            "target_id": target,
                            "category": category,
                            "metric": metric,
                            "activity": "1",
                            "activity_unit": "kg",
                            "factor": str(index + 1),
                            "factor_unit": "KRW/kg" if metric == "cost" else "kgCO2e/kg",
                            "activity_evidence_id": "activity_record",
                            "factor_evidence_id": metric + "_factor",
                        }
                    )
        scenarios.append(
            {
                "candidate_id": pid,
                "basis": basis,
                "stage_ids": stages,
                "handoff_ids": legs,
                "lines": lines,
            }
        )
    return ScenarioComparisonRequest.model_validate(
        {"basis": basis, "mode": "simulation", "evidence": evidence, "scenarios": scenarios}
    )


def codes(findings):
    return {finding["code"] for finding in findings}


def test_correct_graph_binding_and_arithmetic_still_require_real_evidence(research):
    numeric = numeric_for(research)
    findings = inspect_review(research, numeric, compare_scenarios(numeric))
    assert not [f for f in findings if f["severity"] == "error"]
    assert {
        "facility_operations_unverified",
        "legal_applicability_unverified",
        "numeric_source_content_unverified",
        "simulation_not_operational_evidence",
    } <= codes(findings)
    for finding in findings:
        CriticIssue.model_validate(finding)
    assert {
        f["candidate_id"] for f in findings if f["code"] == "numeric_source_content_unverified"
    } == {c.plan.plan_id for c in research.candidates}


def test_missing_submission_preserves_every_candidate(research):
    findings = inspect_review(research, None, None)
    assert {f["candidate_id"] for f in findings if f["code"] == "numeric_scenario_missing"} == {
        c.plan.plan_id for c in research.candidates
    }
    assert not [f for f in findings if f["severity"] == "error"]


@pytest.mark.parametrize("field", ["stage_ids", "handoff_ids"])
def test_process_and_handoff_order_must_equal_the_actual_kg_route(research, field):
    numeric = numeric_for(research)
    scenario = next(s for s in numeric.scenarios if len(getattr(s, field)) > 1)
    getattr(scenario, field).reverse()
    findings = inspect_review(research, numeric, compare_scenarios(numeric))
    kind = "stage" if field == "stage_ids" else "handoff"
    assert "numeric_" + kind + "_binding_mismatch" in codes(findings)


def test_same_length_fake_stage_is_not_an_actual_route(research):
    numeric = numeric_for(research)
    numeric.scenarios[0].stage_ids[0] = "synthetic-forged-stage"
    findings = inspect_review(research, numeric, compare_scenarios(numeric))
    assert "numeric_stage_binding_mismatch" in codes(findings)
    assert "comparison_candidate_unbound" in codes(findings)


def test_foreign_candidate_cannot_enter_bound_comparison(research):
    numeric = numeric_for(research)
    numeric.scenarios[0].candidate_id = "synthetic-unknown-candidate"
    findings = inspect_review(research, numeric, compare_scenarios(numeric))
    assert "numeric_unknown_candidate" in codes(findings)
    assert "comparison_candidate_unbound" in codes(findings)
    assert "numeric_scenario_missing" in codes(findings)


def test_duplicate_and_omitted_candidates_are_explicit(research):
    numeric = numeric_for(research)
    numeric.scenarios[1] = numeric.scenarios[0].model_copy(deep=True)
    findings = inspect_review(research, numeric, compare_scenarios(numeric))
    assert {
        "numeric_duplicate_candidate",
        "comparison_duplicate_candidate",
        "numeric_scenario_missing",
    } <= codes(findings)


def test_quantity_mismatch_does_not_become_a_cheaper_route(research):
    numeric = numeric_for(research)
    numeric.basis.quantity_kg *= 2
    for scenario in numeric.scenarios:
        scenario.basis = numeric.basis.model_copy()
    findings = inspect_review(research, numeric, compare_scenarios(numeric))
    assert "lot_quantity_mismatch" in codes(findings)


def test_unknown_lot_quantity_remains_missing(research):
    research.input_context["quantity_kg"] = None
    findings = inspect_review(research, None, None)
    assert "lot_quantity_missing" in codes(findings)
    assert next(f for f in findings if f["code"] == "lot_quantity_missing")["severity"] == "missing"


def test_incomplete_pagination_and_wrong_counts_are_separate(research):
    research.coverage["complete_within_curated_scope"] = False
    research.coverage["collected_candidates"] = 100
    findings = inspect_review(research, None, None)
    assert "kg_scope_incomplete" in codes(findings)
    assert "kg_candidate_count_mismatch" in codes(findings)


@pytest.mark.parametrize(
    "change,expected",
    [
        ("handoff", "kg_handoff_sequence_mismatch"),
        ("state", "kg_state_sequence_mismatch"),
        ("assessment", "facility_stage_binding_mismatch"),
    ],
)
def test_route_linkage_is_inspected_independently(research, change, expected):
    candidate = next(c for c in research.candidates if len(c.plan.research_packet["stages"]) > 1)
    if change == "handoff":
        candidate.plan.research_packet["handoffs"][0]["to_stage_id"] = "wrong-stage"
    elif change == "state":
        candidate.plan.research_packet["stages"][0]["output_state"]["key"] = "wrong-state"
    else:
        candidate.facility_assessment["stages"][0]["stage_id"] = "wrong-assessment-stage"
    assert expected in codes(inspect_review(research, None, None))


@pytest.mark.parametrize("changed", ["amount", "known_cost_subtotal_krw", "cost_total_krw"])
def test_output_arithmetic_is_checked_against_original_lines(research, changed):
    numeric = numeric_for(research)
    comparison = compare_scenarios(numeric)
    if changed == "amount":
        comparison.results[0]["calculations"][0]["amount"] = "999999"
    else:
        comparison.results[0][changed] = "999999"
    assert "calculation_output_mismatch" in codes(inspect_review(research, numeric, comparison))


def test_calculation_missing_data_is_not_a_known_contradiction(research):
    numeric = numeric_for(research)
    numeric.scenarios[0].lines = [
        line for line in numeric.scenarios[0].lines if line.category != "transport"
    ]
    findings = inspect_review(research, numeric, compare_scenarios(numeric))
    missing = [f for f in findings if f["code"] == "calculation_component_missing"]
    assert missing and all(f["severity"] == "missing" for f in missing)


def test_unlike_units_are_a_known_calculation_error(research):
    numeric = numeric_for(research)
    numeric.scenarios[0].lines[0].factor_unit = "KRW/t"
    findings = inspect_review(research, numeric, compare_scenarios(numeric))
    assert any(
        f["code"] == "calculation_factor_unit_mismatch" and f["severity"] == "error"
        for f in findings
    )


def test_reversed_pareto_layers_violate_pairwise_dominance(research):
    numeric = numeric_for(research)
    comparison = compare_scenarios(numeric)
    assert len(comparison.pareto_fronts) > 1
    comparison.pareto_fronts.reverse()
    assert "pareto_dominance_mismatch" in codes(inspect_review(research, numeric, comparison))


@pytest.mark.parametrize("change", ["missing", "duplicate", "unknown"])
def test_pareto_cannot_hide_or_add_candidates(research, change):
    numeric = numeric_for(research)
    comparison = compare_scenarios(numeric)
    if change == "missing":
        comparison.pareto_fronts.pop()
    elif change == "duplicate":
        comparison.pareto_fronts.append(comparison.pareto_fronts[0])
    else:
        comparison.pareto_fronts.append(["synthetic-unknown"])
    assert "pareto_candidate_scope_invalid" in codes(inspect_review(research, numeric, comparison))


def test_equal_cost_carbon_points_must_stay_on_the_same_front(research):
    numeric = numeric_for(research)
    for scenario in numeric.scenarios:
        for line in scenario.lines:
            line.factor *= 0
    comparison = compare_scenarios(numeric)
    assert len(comparison.pareto_fronts) == 1
    first = comparison.pareto_fronts[0].pop()
    comparison.pareto_fronts.append([first])
    assert "pareto_dominance_mismatch" in codes(inspect_review(research, numeric, comparison))


def test_declaring_legal_and_numeric_success_does_not_establish_source_truth(research):
    numeric = numeric_for(research)
    numeric.mode = "submitted_evidence"
    for source in numeric.evidence:
        source.synthetic = False
    for candidate in research.candidates:
        candidate.legal_graph_review = {"audit": {"coverage_complete": True}}
    findings = inspect_review(research, numeric, compare_scenarios(numeric))
    assert "numeric_source_content_unverified" in codes(findings)
    assert "legal_operational_clearance_unverified" in codes(findings)
    assert "legal_applicability_unverified" not in codes(findings)


def test_critic_does_not_mutate_research_or_calculations(research):
    numeric = numeric_for(research)
    comparison = compare_scenarios(numeric)
    before = deepcopy((research.model_dump(), numeric.model_dump(), comparison.model_dump()))
    inspect_review(research, numeric, comparison)
    assert (research.model_dump(), numeric.model_dump(), comparison.model_dump()) == before


def test_handoff_risks_keep_the_actual_handoff_target(research):
    findings = inspect_review(research, None, None)
    issues = [
        f
        for f in findings
        if f["code"] == "facility_handoff_specifications" and f["target_type"] == "handoff"
    ]
    assert issues
    expected = {
        leg["leg_id"] for c in research.candidates for leg in c.plan.research_packet["handoffs"]
    }
    assert {f["target_id"] for f in issues} <= expected
