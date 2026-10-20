import json
from copy import deepcopy
from pathlib import Path

import pytest
from pydantic import ValidationError

from resource_ai.api.schemas.facility_assessment import AssessedPlansRequest, LotFacts
from resource_ai.knowledge_graph.facility_assessment import assess_capability
from resource_ai.knowledge_graph.planning_reference import PlanningReference, reference

CASES = json.loads(Path("data/config/facility_assessment_eval.json").read_text(encoding="utf8"))[
    "cases"
]


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["id"])
def test_documentary_expectations_without_operating_approval(case):
    value = reference()
    cap = next(c for c in value.capabilities if c.key == case["capability"])
    assessment = assess_capability(value, cap, LotFacts.model_validate(case["lot"]))
    assert assessment.status == case["status"]
    checks = {c.code: c for c in assessment.checks}
    assert {k: checks[k].status for k in case["checks"]} == case["checks"]
    assert assessment.dispatch_approved is False
    assert assessment.all_operational_conditions_verified is False
    assert all(r["actual_incident_confirmed"] is False for r in assessment.risks)


def test_zero_values_and_descriptions_are_retained_without_numeric_limits():
    value = reference()
    cap = value.capabilities[0]
    lot = LotFacts(
        material="PET", moisture_pct=0, contamination_pct=0, contamination="가상 시료 설명"
    )
    result = assess_capability(value, cap, lot)
    check = next(c for c in result.checks if c.code == "moisture_pct")
    assert check.observed == 0 and check.expected is None
    assert result.input_context.contamination == "가상 시료 설명"


@pytest.mark.parametrize(
    "change", ["unlinked_source", "false_site_scope", "missing_exclusion_source", "wrong_material"]
)
def test_profile_linkage_and_scope_cannot_be_forged(change):
    payload = deepcopy(reference().model_dump(mode="json"))
    cap = next(c for c in payload["capabilities"] if c["key"] == "jungang-gongju--pp-prepare")
    profile = cap["input_profile"]
    if change == "unlinked_source":
        profile["source_ids"] = ["samyang-pet"]
    elif change == "false_site_scope":
        profile["evidence_scope"] = "organisation_only_not_site_verified"
    elif change == "missing_exclusion_source":
        profile["exclusion_source_ids"] = []
    else:
        profile["material_examples"] = ["pet"]
    with pytest.raises(ValidationError):
        PlanningReference.model_validate(payload)


@pytest.mark.parametrize(
    "fields",
    [
        {"designated_waste": "false"},
        {"designated_waste": 1},
        {"moisture_pct": -1},
        {"contamination_pct": 101},
        {"moisture_pct": float("nan")},
        {"current_acceptance": True},
    ],
)
def test_input_cannot_invent_or_coerce_operational_facts(fields):
    with pytest.raises(ValidationError):
        LotFacts.model_validate(fields)


def test_private_assessment_cannot_be_requested_as_external_model_view():
    with pytest.raises(ValidationError):
        AssessedPlansRequest(material="PP", audience="external_model")
