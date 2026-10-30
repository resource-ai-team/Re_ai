"""Permanent business expectations from the actual HTTP scenario campaign.

The separate runner checks real TCP/PostgreSQL; these fast tests keep the decision
boundaries in normal CI without cloud calls or database services.
"""

import pytest
from pydantic import ValidationError

from resource_ai.material_quality.service import assess
from scripts.pp_stress_cases import catalog
from tests.unit.test_evidence_workflow import operate, setup_case


@pytest.mark.parametrize("case", catalog(), ids=lambda case: case["id"])
def test_business_case_decision_boundary(case):
    service, state = setup_case(mode=case.get("mode", "simulation"), with_engine=False)
    if case["claims"]:
        payload = {"sample_id": "synthetic-sample", "claims": case["claims"]}
        if case.get("input_http", 200) == 422:
            with pytest.raises(ValidationError):
                operate(service, state, "quality_inputs", payload)
            return
        state = operate(service, state, "quality_inputs", payload)
    if not case.get("omit_target"):
        payload = {"scenario": case["scenario"]}
        if case.get("scenario_http", 200) == 422:
            with pytest.raises(ValidationError):
                operate(service, state, "quality_scenario", payload)
            return
        state = operate(service, state, "quality_scenario", payload)
    result = assess(state)
    expected = case["expected"]
    assert result["level"] == expected["level"]
    for field in ("mass_balance", "drying_balance", "economics"):
        assert (result[field] is not None) == expected[field]
    if case.get("expect_mode"):
        assert result["mode"] == case["expect_mode"]
    assert not result["recommended"] and not result["dispatch_approved"]
    assert result["carbon_kgco2e"] is None
