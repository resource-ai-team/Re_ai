"""Synthetic arithmetic examples, not quotes/LCI/customer recommendation evidence."""

import json
from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from resource_ai.api.schemas.analysis import ScenarioComparisonRequest
from resource_ai.calculators.scenarios import compare_scenarios, pareto_layers
from resource_ai.main import app


@pytest.fixture
def payload():
    return json.loads(Path("data/config/scenario_comparison_demo.json").read_text(encoding="utf8"))


def compare(payload):
    return compare_scenarios(ScenarioComparisonRequest.model_validate(payload))


def test_reproducible_cost_carbon_and_pareto_without_invented_weights(payload):
    result = compare(payload)
    a, b, c = result.results
    assert Decimal(a["cost_total_krw"]) == Decimal(-210000)
    assert Decimal(a["carbon_total_kg_co2e"]) == Decimal(753)
    assert Decimal(b["cost_total_krw"]) == Decimal(90000)
    assert Decimal(b["carbon_total_kg_co2e"]) == Decimal(453)
    assert Decimal(c["carbon_total_kg_co2e"]) == Decimal(1053)
    assert result.pareto_fronts == [["simulation-A", "simulation-B"], ["simulation-C"]]
    assert all(not row["recommended"] for row in result.results)
    assert not result.final_recommendations and result.dispatch_approved is False
    assert all(not row["evidence_content_audited"] for row in result.results)
    assert all(line["evidence_ids"] for row in result.results for line in row["calculations"])
    assert result.evidence[0].synthetic is True


def test_missing_origin_transport_is_never_a_free_zero(payload):
    first = payload["scenarios"][0]
    first["lines"] = [l for l in first["lines"] if l["category"] != "transport"]
    result = compare(payload)
    assert result.results[0]["cost_total_krw"] is None
    assert result.results[0]["carbon_total_kg_co2e"] is None
    assert result.incomparable_ids == ["simulation-A"]
    assert "simulation-A" not in {i for front in result.pareto_fronts for i in front}


@pytest.mark.parametrize("part", ["process", "residue", "revenue"])
def test_all_required_categories_must_be_explicit_even_when_zero(payload, part):
    scenario = payload["scenarios"][0]
    scenario["lines"] = [l for l in scenario["lines"] if l["category"] != part]
    row = compare(payload).results[0]
    assert row["status"] == "needs_data"
    assert any(i["code"] == "component_missing" and i["category"] == part for i in row["issues"])


@pytest.mark.parametrize(
    "change,code",
    [
        ("quantity", "comparison_basis_mismatch"),
        ("date", "evidence_outside_validity"),
        ("region", "evidence_scope_mismatch"),
        ("year", "evidence_scope_mismatch"),
        ("method", "carbon_method_mismatch"),
        ("unit", "factor_unit_mismatch"),
        ("target", "unexpected_component_target"),
        ("evidence", "evidence_missing"),
        ("kind", "evidence_kind_mismatch"),
    ],
)
def test_scope_unit_date_and_target_errors_block_comparison(payload, change, code):
    if change == "quantity":
        payload["scenarios"][0]["basis"]["quantity_kg"] = "2999"
    elif change == "date":
        payload["evidence"][0]["valid_until"] = "2025-12-31"
    elif change == "region":
        payload["evidence"][0]["region"] = "EU"
    elif change == "year":
        payload["evidence"][0]["assessment_year"] = 2025
    elif change == "method":
        payload["evidence"][2]["carbon_method"] = "Different method"
    elif change == "unit":
        payload["scenarios"][0]["lines"][0]["factor_unit"] = "KRW/t"
    elif change == "target":
        payload["scenarios"][0]["lines"][0]["target_id"] = "another-candidate:stage:1"
    elif change == "evidence":
        payload["scenarios"][0]["lines"][0]["factor_evidence_id"] = "missing"
    else:
        payload["evidence"][0]["kind"] = "carbon_factor"
    result = compare(payload)
    assert result.results[0]["status"] == "needs_data"
    assert any(i["code"] == code for i in result.results[0]["issues"])


@pytest.mark.parametrize("kind", ["candidate", "source", "line"])
def test_duplicate_identifiers_cannot_hide_overwrites_or_double_count(payload, kind):
    if kind == "candidate":
        payload["scenarios"][1]["candidate_id"] = payload["scenarios"][0]["candidate_id"]
    elif kind == "source":
        payload["evidence"].append(deepcopy(payload["evidence"][0]))
    else:
        payload["scenarios"][0]["lines"].append(deepcopy(payload["scenarios"][0]["lines"][0]))
    result = compare(payload)
    assert result.results[0]["status"] == "needs_data"
    assert any(i["code"].startswith("duplicate_") for i in result.results[0]["issues"])


def test_synthetic_data_requires_explicit_simulation_mode(payload):
    payload["mode"] = "submitted_evidence"
    result = compare(payload)
    assert result.pareto_fronts == []
    assert len(result.incomparable_ids) == 3
    assert "synthetic_evidence_requires_simulation" in {
        i["code"] for i in result.results[0]["issues"]
    }


def test_evidenced_zero_is_accepted_and_revenue_does_not_become_carbon_credit(payload):
    for line in payload["scenarios"][0]["lines"]:
        line["factor"] = "0"
    result = compare(payload)
    assert Decimal(result.results[0]["cost_total_krw"]) == 0
    assert Decimal(result.results[0]["carbon_total_kg_co2e"]) == 0
    payload["scenarios"][0]["lines"][-1]["metric"] = "carbon"
    assert compare(payload).results[0]["status"] == "needs_data"


@pytest.mark.parametrize("bad", ["-1", "NaN", "Infinity", "1e100", "0.1234567890123456789"])
def test_invalid_or_excessive_precision_numeric_values_rejected(payload, bad):
    payload["scenarios"][0]["lines"][0]["activity"] = bad
    with pytest.raises(ValidationError):
        ScenarioComparisonRequest.model_validate(payload)


def test_exact_ties_and_genuine_tradeoff_not_forced_into_total_order():
    assert pareto_layers({"A": (1, 3), "B": (3, 1), "C": (1, 3), "D": (4, 4)}) == [
        ["A", "B", "C"],
        ["D"],
    ]


def test_multiple_stages_require_all_handoff_cost_and_carbon(payload):
    scenario = payload["scenarios"][0]
    scenario["stage_ids"].append("simulation-A:stage:2")
    scenario["handoff_ids"].append("simulation-A:leg:1")
    result = compare(payload)
    missing = {(i.get("category"), i.get("target_id")) for i in result.results[0]["issues"]}
    assert ("process", "simulation-A:stage:2") in missing
    assert ("transport", "simulation-A:leg:1") in missing


def test_api_preserves_evidence_and_never_returns_final_recommendations(payload):
    client = TestClient(app)
    response = client.post("/analysis/compare-scenarios", json=payload)
    assert response.status_code == 200
    assert len(response.json()["evidence"]) == 3
    assert response.json()["final_recommendations"] == []
    payload["basis"]["currency"] = "USD"
    assert client.post("/analysis/compare-scenarios", json=payload).status_code == 422
