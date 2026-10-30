"""Independent arithmetic targets and scope barriers for the narrow PP prototype."""

from copy import deepcopy
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from resource_ai.api.routes.workflow import get_case_service, get_case_store, identity
from resource_ai.main import app
from resource_ai.workflow.pp_study import PPStudy, evaluate_study, save_study
from resource_ai.workflow.service import recompute
from resource_ai.workflow.store import WorkflowError
from tests.unit.test_evidence_workflow import operate, setup_case


def study(**changes):
    return {
        "scenario": "sorting",
        "target_specification_id": "rema-pp-small-rigid-2023-10-18",
        "feedstock": "post_consumer_rigid_pp_packaging",
        "prohibited": "absent",
        "oil_or_hazardous": "absent",
        "composition": {"pp": "800", "pe": "120", "pet_ps": "40", "paper": "40"},
        **changes,
    }


def evaluate(value):
    _, state = setup_case()
    save_study(state, {"study": value}, "synthetic-reviewer", "2026-10-29")
    return evaluate_study(state)


def near(value, expected):
    assert abs(Decimal(value) - Decimal(expected)) < Decimal("0.000000001")


def test_ideal_composition_example_reproduces_all_three_individual_targets():
    result = evaluate(study())
    target = result["sorting"]["ideal_target"]
    near(target["output_mass_upper_bound_kg"], "941.176470588235294")
    near(target["removal_lower_bound_kg"], "58.823529411764706")
    for field, expected in [
        ("pe", "25.88235294117647"),
        ("pet_ps", "11.76470588235294"),
        ("paper", "21.17647058823529"),
    ]:
        near(target["minimum_removal_kg"][field], expected)
    assert target["additional_removal_to_allocate_kg"] == "0"
    assert result["sorting"]["status"] == "composition_conditions_unmet"
    assert result["environment"]["carbon_kgco2e"] is None and not result["dispatch_approved"]


def test_individual_limit_can_bind_before_total_contamination_and_pp_loss_changes_bound():
    result = evaluate(study(composition={"pp": "900", "other_rigid": "100"}))
    near(result["sorting"]["ideal_target"]["output_mass_upper_bound_kg"], "909.090909090909091")
    clean = evaluate(study())
    loss = evaluate(study(pp_loss_pct="5"))
    target = loss["sorting"]["ideal_target"]
    assert Decimal(target["removal_lower_bound_kg"]) > Decimal(
        clean["sorting"]["ideal_target"]["removal_lower_bound_kg"]
    )
    assert Decimal(target["assumed_pp_loss_kg"]) == 40


def test_additional_total_removal_is_not_falsely_allocated_to_one_material():
    value = study(
        composition={
            "pp": "800",
            "pe": "100",
            "pet_ps": "30",
            "paper": "20",
            "other_rigid": "10",
            "liquid_residue": "20",
            "aluminium": "20",
        }
    )
    target = evaluate(value)["sorting"]["ideal_target"]
    assert Decimal(target["additional_removal_to_allocate_kg"]) > 0
    near(
        str(
            sum(map(Decimal, target["minimum_removal_kg"].values()))
            + Decimal(target["additional_removal_to_allocate_kg"])
        ),
        target["removal_lower_bound_kg"],
    )


@pytest.mark.parametrize(
    "change",
    [
        {"prohibited": "present"},
        {"oil_or_hazardous": "present"},
        {"food_contact_intended": True},
        {"feedstock": "other"},
    ],
)
def test_forbidden_or_excluded_scope_never_creates_an_automatic_improvement(change):
    result = evaluate(study(**change))
    assert result["status"] == "outside_supported_scope"
    assert result["sorting"] is None and result["economics"] is None
    assert not result["dispatch_approved"]


def test_unknown_scope_and_unidentified_composition_are_not_promoted_to_known():
    assert evaluate(study(prohibited="unknown"))["status"] == "scope_confirmation_needed"
    result = evaluate(study(composition={"pp": "900", "unknown": "100"}))
    assert result["sorting"]["status"] == "unknown_composition"
    assert result["sorting"]["ideal_target"] is None


def test_wet_basis_water_balance_is_not_a_washing_or_energy_prediction():
    result = evaluate(
        study(
            scenario="drying",
            composition=None,
            drying={"initial_moisture_pct": "12", "target_moisture_pct": "1"},
        )
    )
    near(result["drying"]["water_removal_kg"], "111.111111111111111")
    assert Decimal(result["drying"]["dry_solids_kg"]) == 880
    assert result["drying"]["energy_kwh"] is None and result["sorting"] is None


def test_independent_economic_example_and_missing_baseline():
    money = {
        "saleable_product_kg": "800",
        "preprocessing_cost_krw": "500000",
        "other_cost_krw": "200000",
        "baseline_net_cost_krw": "250000",
        "prices_krw_per_kg": ["400", "600", "800"],
    }
    result = evaluate(study(economics=money))["economics"]
    assert Decimal(result["break_even_price_krw_per_kg"]) == Decimal("562.5")
    assert [Decimal(r["benefit_vs_baseline_krw"]) for r in result["rows"]] == [
        -130000,
        30000,
        190000,
    ]
    assert [Decimal(r["preprocessing_cost_ceiling_krw"]) for r in result["rows"]] == [
        370000,
        530000,
        690000,
    ]
    without = evaluate(study(economics={**money, "baseline_net_cost_krw": None}))["economics"]
    assert without["break_even_price_krw_per_kg"] is None
    assert all(row["benefit_vs_baseline_krw"] is None for row in without["rows"])
    assert all(row["preprocessing_cost_ceiling_krw"] is None for row in without["rows"])


@pytest.mark.parametrize(
    "change",
    [
        {"data_basis": "measurement"},
        {"composition": {"pp": "NaN"}},
        {"pp_loss_pct": "100"},
        {"drying": {"initial_moisture_pct": "12", "target_moisture_pct": "1"}},
        {
            "scenario": "drying",
            "composition": None,
            "drying": {"initial_moisture_pct": "1", "target_moisture_pct": "12"},
        },
    ],
)
def test_invalid_units_scenario_mix_and_unverified_measurement_claims_are_rejected(change):
    with pytest.raises(ValidationError):
        PPStudy.model_validate(study(**change))


def test_reference_preserves_negative_result_and_does_not_scale_literature_yield_to_this_lot():
    result = evaluate(study(scenario="published", composition=None))
    assert result["published_cases"][0]["target_recovery_pct"] == "94"
    assert result["published_cases"][1]["target_recovery_pct"] == "87"
    assert "부적합" in result["published_conclusion"]
    assert all("output_kg" not in row for row in result["published_cases"])


def test_pp_film_is_outside_rigid_packaging_even_when_the_declared_composition_is_pure_pp():
    _, state = setup_case()
    state["inputs"]["engine_request"]["lot"]["input_form"] = "film"
    save_study(state, {"study": study(composition={"pp": "1000"})}, "qa", "2026-10-29")
    result = evaluate_study(state)
    assert result["status"] == "outside_supported_scope" and result["sorting"] is None


def test_save_uses_existing_versions_idempotency_actor_and_no_kg_rag_refresh():
    service, state = setup_case()
    service.engine_reader = lambda _: pytest.fail("PP computation must not silently repeat KG/RAG")
    before = deepcopy(state)
    payload = {"study": study()}
    updated = service.operate(
        "tenant-a",
        state["case_id"],
        state["revision"],
        "pp-save-once",
        "pp_study",
        payload,
        actor="pp-reviewer",
    )
    assert updated["revision"] == state["revision"] + 1
    assert updated["pp_study"]["saved_by"] == "pp-reviewer"
    assert updated["engine"] == before["engine"] and updated["drafts"] == before["drafts"]
    assert "PP 전처리 검토" in updated["report_markdown"]
    assert (
        service.operate(
            "tenant-a", state["case_id"], state["revision"], "pp-save-once", "pp_study", payload
        )
        == updated
    )
    updated["inputs"]["engine_request"]["lot"]["input_form"] = "film"
    assert recompute(updated)["evaluation"]["pp_study"]["status"] == "stale_inputs"


def test_mass_mismatch_and_non_pp_case_are_rejected_without_changing_saved_state():
    service, state = setup_case()
    with pytest.raises(WorkflowError, match="pp-composition-mass-must-equal-lot"):
        operate(service, state, "pp_study", {"study": study(composition={"pp": "800"})})
    state["inputs"]["engine_request"]["lot"]["material"] = "PET"
    with pytest.raises(WorkflowError, match="pp-study-requires-pp-lot"):
        save_study(state, {"study": study()}, "qa", "2026-10-29")


def test_api_preserves_tenant_boundary_and_existing_idempotency_contract():
    service, state = setup_case()
    previous = app.dependency_overrides.copy()
    app.dependency_overrides[get_case_service] = lambda: service
    app.dependency_overrides[get_case_store] = lambda: service.store
    app.dependency_overrides[identity] = lambda: "tenant-a"
    try:
        with TestClient(app) as client:
            response = client.put(
                f"/workflow/cases/{state['case_id']}/pp-study",
                json={"expected_revision": state["revision"], "study": study()},
                headers={"Idempotency-Key": "pp-api-test-once", "X-Workflow-Actor": "pp-qa"},
            )
            assert response.status_code == 200, response.text
            assert response.json()["pp_study"]["saved_by"] == "pp-qa"
            app.dependency_overrides[identity] = lambda: "tenant-b"
            assert client.get(f"/workflow/cases/{state['case_id']}").status_code == 404
    finally:
        app.dependency_overrides = previous
