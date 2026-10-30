"""Business failures found by the live HTTP/SQL scenario campaign (synthetic inputs)."""

from copy import deepcopy
from decimal import Decimal

import pytest

from resource_ai.material_quality.service import assess
from tests.unit.test_evidence_workflow import operate
from tests.unit.test_pp_quality_pipeline import claim, prepared, target
from tests.unit.test_site_discovery import request


def test_future_measurement_is_not_a_current_sample_reading():
    service, state = prepared(contamination=False)
    reading = claim("contamination", 20, measurement_date="2099-01-01")
    state = operate(service, state, "quality_inputs", {"claims": [reading]})
    value = assess(state)
    assert value["level"] == 1 and value["mass_balance"] is None


def test_metadata_only_target_cannot_supply_operational_numeric_limits():
    service, state = prepared()
    chosen = target()
    chosen["status"] = "metadata_only"
    state = operate(service, state, "quality_scenario", {"scenario": {"target": chosen}})
    value = assess(state)
    assert value["status"] == "REVIEW" and value["mass_balance"] is None


@pytest.mark.parametrize("initial", [0, 1])
def test_already_below_moisture_max_needs_no_water_removal(initial):
    service, state = prepared(contamination=False)
    state = operate(service, state, "quality_inputs", {"claims": [claim("moisture", initial)]})
    chosen = target()
    chosen["limits"] = [{"field": "moisture", "max_value": "5"}]
    state = operate(service, state, "quality_scenario", {"scenario": {"target": chosen}})
    value = assess(state)
    assert value["level"] == 2 and Decimal(value["drying_balance"]["water_removal_kg"]) == 0
    assert Decimal(value["drying_balance"]["final_wet_mass_kg"]) == 1000


@pytest.mark.parametrize("contaminant_removal", [0, 100])
def test_no_pp_recovery_keeps_mass_trace_but_does_not_make_pp_sales(contaminant_removal):
    service, state = prepared()
    scenario = {
        "target": target(),
        "explicit_assumptions": ["synthetic failed process"],
        "stages": [
            {
                "stage_id": "synthetic-damaged-process",
                "process": "sorting",
                "pp_loss_pct": "100",
                "contaminant_removal_pct": str(contaminant_removal),
                "evidence_id": "synthetic-performance",
            }
        ],
        "economics": {
            "preprocessing_krw": 100000,
            "transport_krw": 10000,
            "residue_krw_per_kg": 100,
            "sale_price_krw_per_kg": 400,
            "evidence_ids": ["synthetic-cost"],
        },
    }
    state = operate(service, state, "quality_scenario", {"scenario": scenario})
    value = assess(state)
    assert value["status"] == "REVIEW" and value["economics"] is None
    assert Decimal(value["forward_scenario"]["pp_kg"]) == 0
    assert value["forward_scenario"]["mass_conserved"]


def test_hypothetical_target_is_simulation_even_with_reported_input_values():
    _, state = prepared()
    state["inputs"]["mode"] = "research"
    for record in state["material_quality"]["claims"].values():
        record["claim"]["synthetic"] = False
    assert assess(state)["mode"] == "simulation"


@pytest.mark.parametrize("changed", ["quantity", "origin"])
def test_kg_binding_expires_when_engine_lot_or_origin_changes(changed):
    service, state = prepared()
    state = operate(
        service,
        state,
        "quality_research",
        {
            "origin": request().origin.model_dump(mode="json"),
            "max_plan_pages": 1,
            "max_legal_queries": 1,
            "max_technical_queries": 0,
        },
    )
    assert assess(state)["kg_candidates_loaded"]
    inputs = deepcopy(state["inputs"])
    if changed == "quantity":
        inputs["engine_request"]["lot"]["quantity_kg"] = 2000
    else:
        inputs["engine_request"]["origin"]["site_id"] = "synthetic-different-site"
    state = operate(service, state, "inputs", inputs)
    assert not assess(state)["kg_candidates_loaded"]
