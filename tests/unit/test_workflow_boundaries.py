"""Failure injection checks. All prices, activity and locations here are synthetic."""

from copy import deepcopy
from decimal import Decimal
from types import SimpleNamespace

import pytest

from resource_ai.workflow.calculation import break_even, evaluate_candidate
from resource_ai.workflow.models import CandidateDraft, Range
from resource_ai.workflow.service import query_candidate_roads, validate_draft
from resource_ai.workflow.store import WorkflowError
from tests.unit.test_evidence_workflow import prepared


@pytest.fixture(scope="module")
def fixture_case():
    return prepared()[1:]


@pytest.mark.parametrize(
    "fault,expected",
    [
        ("boundary", "carbon_boundary_and_allocation_required"),
        ("mass", "bound_mass_mismatch"),
        ("revenue", "revenue_requires_final_output_mass"),
        ("included", "included_target_not_in_candidate"),
        ("expired", "expired_evidence"),
        ("future", "future_evidence"),
        ("unconfirmed", "evidence_unconfirmed"),
        ("duplicate", "unexpected_or_duplicate_component"),
        ("future_factor", "future_factor_reference"),
        ("full_and_part", "full_target_and_components_overlap"),
    ],
)
def test_faults_cannot_enter_complete_comparison(fixture_case, fault, expected):
    state, plan, original = deepcopy(fixture_case)
    draft = deepcopy(original)
    carbon = next(l for l in draft["lines"] if l["metric"] == "carbon")
    source = state["evidence"]["synthetic-evidence"]
    if fault == "boundary":
        carbon["scope"]["allocation"] = None
    elif fault == "mass":
        carbon["activity"].update(low="1100", high="1100")
    elif fault == "revenue":
        next(l for l in draft["lines"] if l["category"] == "revenue")["mass_binding"] = "lot_input"
    elif fault == "included":
        carbon["includes_targets"] = ["not-a-real-leg"]
    elif fault == "expired":
        source["valid_until"] = "2025-01-01"
    elif fault == "future":
        source["observed_on"] = "2027-01-01"
    elif fault == "unconfirmed":
        source["user_confirmed"] = False
    elif fault == "duplicate":
        draft["lines"].append(deepcopy(draft["lines"][0]))
    elif fault == "future_factor":
        carbon["scope"].update(reference_year=2027, transfer_explanation="synthetic reason")
    elif fault == "full_and_part":
        extra = deepcopy(carbon)
        extra.update(
            line_id="extra-component", component="extra-electricity", covers_full_target=False
        )
        draft["lines"].append(extra)
    result = evaluate_candidate(plan, draft, state["evidence"], state["inputs"])
    assert not result["comparable"]
    assert expected in {i["code"] for i in result["errors"] + result["missing"]}


def test_branch_legs_are_both_routed_and_costed(fixture_case):
    state, plan, draft = deepcopy(fixture_case)
    tail = {
        "leg_id": "residue-tail",
        "from_stage_id": plan["research_packet"]["stages"][0]["stage_id"],
        "purpose": "residue",
        "destination": {"latitude": 37.2, "longitude": 127.1},
        "evidence_id": "synthetic-evidence",
    }
    draft["tails"] = [tail]
    validate_draft(state, CandidateDraft.model_validate(draft))
    result = evaluate_candidate(plan, draft, state["evidence"], state["inputs"])
    assert not result["comparable"]
    assert {i["metric"] for i in result["missing"] if i["target_id"] == "residue-tail"} == {
        "cost",
        "carbon",
    }
    draft["tails"].append(tail)
    with pytest.raises(WorkflowError, match="duplicate-or-reserved-tail"):
        validate_draft(state, CandidateDraft.model_validate(draft))


def test_road_requests_follow_stages_and_branch_origins(fixture_case):
    state, plan, draft = deepcopy(fixture_case)
    for i, facility in enumerate(draft["facilities"]):
        facility["gate"] = {"latitude": 37.2 + i / 100, "longitude": 127.1 + i / 100}
    last = draft["facilities"][-1]
    draft["tails"] = [
        {
            "leg_id": "tail-qa",
            "from_stage_id": last["stage_id"],
            "purpose": "residue",
            "destination": {"latitude": 37.4, "longitude": 127.4},
            "evidence_id": "synthetic-evidence",
        }
    ]
    captured = []

    def run(request):
        captured.append(request)
        return SimpleNamespace(
            status="not_configured",
            all_legs_routed=False,
            routes=[],
            total_road_distance_km=None,
            coverage={"external_calls": 0},
        )

    result = query_candidate_roads(state, plan, draft, SimpleNamespace(run=run))
    legs = captured[0].legs
    assert len(legs) == len(draft["facilities"]) + 1
    for i in range(1, len(draft["facilities"])):
        assert legs[i].origin == legs[i - 1].destination
    assert legs[-1].origin.model_dump() == last["gate"]
    assert result["measurements"] == {} and not result["all_legs_routed"]


def test_measured_distance_and_payload_drive_transport_not_straight_line(fixture_case):
    state, plan, draft = deepcopy(fixture_case)
    line = next(
        l for l in draft["lines"] if l["category"] == "transport" and l["metric"] == "carbon"
    )
    line.update(mass_binding="none", trip_count=2, payload_kg_per_trip="500")
    line["activity"].update(low="20", high="20", unit="t_km", basis="activity")
    line["factor"].update(low="0.1", high="0.1", unit="kgCO2e/t_km", basis="activity")
    roads = {line["target_id"]: {"status": "found", "road_distance_km": 20}}
    good = evaluate_candidate(plan, draft, state["evidence"], state["inputs"], roads)
    assert good["totals"]["carbon"] is not None
    line["activity"].update(low="10", high="10")
    bad = evaluate_candidate(plan, draft, state["evidence"], state["inputs"], roads)
    assert any(i["code"] == "transport_activity_differs_from_measured_route" for i in bad["errors"])


def test_break_even_uses_existing_costs_not_an_invented_baseline(fixture_case):
    state, plan, draft = deepcopy(fixture_case)
    own = evaluate_candidate(plan, draft, state["evidence"], state["inputs"])
    other = deepcopy(own)
    other["candidate_id"] = "alternative"
    other["totals"]["cost"] = {k: str(Decimal(v) + 100) for k, v in own["totals"]["cost"].items()}
    thresholds = break_even(plan, draft, own, other)
    assert thresholds
    assert all(t["assumption"] == "other_inputs_fixed_no_probability_claim" for t in thresholds)
    missing = deepcopy(other)
    missing["comparable"] = False
    assert break_even(plan, draft, own, missing) == []


@pytest.mark.parametrize(
    "low,high", [("NaN", "1"), ("Infinity", "1"), ("-1", "1"), ("2", "1"), ("1e16", "1e16")]
)
def test_nonfinite_negative_and_reversed_ranges_are_rejected(low, high):
    with pytest.raises(ValueError):
        Range(low=low, high=high)
