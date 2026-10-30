"""Synthetic intake specifications; real curated graph structure; no cloud providers."""

from copy import deepcopy
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from resource_ai.api.routes.workflow import get_case_service, get_case_store, identity
from resource_ai.main import app
from resource_ai.workflow import critic
from resource_ai.workflow.decisions import (
    build_conditions,
    condition_catalog,
    parse_limits,
    what_if,
)
from resource_ai.workflow.store import WorkflowError
from tests.unit.test_evidence_workflow import operate, prepared, source
from tests.unit.test_workflow_critic import Model
from tests.unit.test_workflow_critic import service as critic_service


def with_proofs(rule="함수율 <= 2 %", reading="함수율: 3 %"):
    service, state, plan, _draft = prepared()
    evidence = [
        {**source("limit"), "kind": "facility", "content": rule},
        {**source("reading"), "content": reading},
    ]
    state = operate(service, state, "evidence", {"evidence": evidence})
    c = next(
        c
        for c in condition_catalog(state)
        if c["candidate_id"] == plan["plan_id"] and c["field"] == "moisture_pct"
    )
    rule = {
        "condition_id": c["condition_id"],
        "facility_id": c["facility_id"],
        "operator": "le",
        "limit": "2",
        "unit": "%",
        "evidence_id": "limit",
        "applicability_confirmed": True,
    }
    reading = {
        "condition_id": c["condition_id"],
        "value": "3",
        "unit": "%",
        "evidence_id": "reading",
        "same_lot_and_stage_confirmed": True,
    }
    return service, state, plan, c, rule, reading


def test_sourced_condition_blocks_comparison_and_records_reason_without_rag_or_model_calls():
    service, state, plan, c, rule, reading = with_proofs()
    old = deepcopy(state)
    service.engine_reader = lambda _: pytest.fail("no repeated KG/RAG on condition review")
    state = service.operate(
        "tenant-a",
        state["case_id"],
        state["revision"],
        "review-condition-once",
        "conditions",
        {"rules": [rule], "readings": [reading]},
        actor="synthetic-reviewer",
    )
    result = next(r for r in state["evaluation"]["results"] if r["candidate_id"] == plan["plan_id"])
    actual = next(x for x in build_conditions(state) if x["condition_id"] == c["condition_id"])
    assert actual["status"] == "unmet" and not result["comparable"]
    assert plan["plan_id"] not in state["evaluation"]["pareto_for_review"]
    assert actual["requirement"]["confirmed_by"] == "synthetic-reviewer"
    assert not state["evaluation"]["dispatch_approved"]
    assert state["drafts"] == old["drafts"] and state["roads"] == old["roads"]
    change = state["last_decision_change"]
    assert (
        next(r for r in change["condition_changes"] if r["condition_id"] == c["condition_id"])[
            "before_status"
        ]
        == "unknown"
    )
    assert change["metric_changes"] and change["pareto_before"] != change["pareto_after"]
    assert "시설 조건 대조" in state["report_markdown"]
    assert state["evaluation"]["decision_support"]["first_actions"][0]["priority"] == 0


def test_hypothetical_met_condition_keeps_permit_hold_and_does_not_change_saved_revision_or_metrics():
    service, state, plan, c, rule, reading = with_proofs()
    state = operate(service, state, "conditions", {"rules": [rule], "readings": [reading]})
    saved = deepcopy(state)
    preview = what_if(
        state,
        {
            "expected_revision": state["revision"],
            "readings": [{"condition_id": c["condition_id"], "value": "1", "unit": "%"}],
        },
    )
    row = next(
        r
        for r in preview["decision_support"]["conditions"]
        if r["condition_id"] == c["condition_id"]
    )
    assert row["status"] == "met" and row["hypothetical"]
    assert row["observation"]["evidence_id"] is None
    assert (
        next(
            g
            for g in preview["decision_support"]["candidate_gates"]
            if g["candidate_id"] == plan["plan_id"]
        )["status"]
        == "hold"
    )
    assert (
        preview["scope"] == "hypothetical_not_saved_or_observed"
        and preview["cost_and_carbon"] is None
    )
    assert state == saved == service.store.get("tenant-a", state["case_id"])
    assert preview["changes"]["to_revision"] is None


@pytest.mark.parametrize(
    "fault,code",
    [
        ("missing_confirmation", "condition-facility-applicability-required"),
        ("wrong_facility", "condition-facility-applicability-required"),
        ("wrong_threshold", "condition-literal-not-in-source"),
        ("wrong_operator", "condition-literal-not-in-source"),
        ("wrong_unit", "condition-unit-mismatch"),
        ("unconfirmed", "condition-evidence-unconfirmed-or-stale"),
        ("wrong_measurement", "condition-literal-not-in-source"),
        ("wrong_stage_confirmation", "condition-lot-and-stage-confirmation-required"),
        ("foreign_condition", "condition-not-in-current-graph"),
    ],
)
def test_unverified_mismatched_or_out_of_scope_values_are_not_promoted(fault, code):
    service, state, _, _, rule, reading = with_proofs()
    if fault == "missing_confirmation":
        rule["applicability_confirmed"] = False
    if fault == "wrong_facility":
        rule["facility_id"] = "another-facility"
    if fault == "wrong_threshold":
        rule["limit"] = "20"
    if fault == "wrong_operator":
        rule["operator"] = "ge"
    if fault == "wrong_unit":
        rule["unit"] = "kg"
    if fault == "unconfirmed":
        state = operate(
            service,
            state,
            "evidence",
            {
                "evidence": [
                    {**source("limit"), "content": "함수율 <= 2 %", "user_confirmed": False}
                ]
            },
        )
    if fault == "wrong_measurement":
        reading["value"] = "1"
    if fault == "wrong_stage_confirmation":
        reading["same_lot_and_stage_confirmed"] = False
    if fault == "foreign_condition":
        rule["condition_id"] = "invented-condition"
    before = deepcopy(service.store.get("tenant-a", state["case_id"]))
    with pytest.raises(WorkflowError, match=code):
        operate(service, state, "conditions", {"rules": [rule], "readings": [reading]})
    assert before == service.store.get("tenant-a", state["case_id"])


def test_changing_or_revoking_a_source_returns_to_unknown_not_pass_or_proven_mismatch():
    service, state, _, c, rule, reading = with_proofs()
    state = operate(service, state, "conditions", {"rules": [rule], "readings": [reading]})
    state = operate(
        service, state, "evidence", {"evidence": [{**source("limit"), "content": "함수율 <= 20 %"}]}
    )
    row = next(r for r in build_conditions(state) if r["condition_id"] == c["condition_id"])
    assert row["status"] == "unknown" and row["requirement"] is None
    assert "facility_limit_evidence" in row["missing"]


def test_missing_requirement_or_measurement_is_unknown_and_first_stage_fact_never_fills_later_stages():
    service, state, _, c, rule, reading = with_proofs()
    state = operate(service, state, "conditions", {"rules": [rule]})
    assert (
        next(r for r in build_conditions(state) if r["condition_id"] == c["condition_id"])["status"]
        == "unknown"
    )
    state = operate(service, state, "conditions", {"readings": [reading]})
    changed = deepcopy(state)
    changed["inputs"]["engine_request"]["lot"]["quantity_kg"] += 1
    assert (
        next(r for r in build_conditions(changed) if r["condition_id"] == c["condition_id"])[
            "observation"
        ]
        is None
    )


@pytest.mark.parametrize(
    "text,op,n",
    [
        ("함수율 2%이하", "le", "2"),
        ("수분 < 2 %", "lt", "2"),
        ("물량 >= 2 톤", "ge", "2000"),
        ("오염도 0.1 % 미만", "lt", "0.1"),
    ],
)
def test_literal_thresholds_keep_comparator_and_unit(text, op, n):
    field = (
        "quantity_kg"
        if "물량" in text
        else "contamination_pct"
        if "오염도" in text
        else "moisture_pct"
    )
    assert parse_limits(text, field)[0][:2] == (op, Decimal(n))


@pytest.mark.parametrize(
    "text",
    [
        "함수율 2 %",
        "함수율 약 2 % 이하",
        "함수율 <=2%~4%",
        "함수율 <=2% 이상",
        "함수율 <=200%",
        "함수율 <= 2 kg",
        "함수율 <= 2 tCO2",
    ],
)
def test_range_estimate_wrong_units_and_bare_values_do_not_become_limits(text):
    assert parse_limits(text, "moisture_pct") == []


def test_distinct_thresholds_or_readings_in_one_excerpt_need_disambiguation():
    for limit, measurement, code in [
        ("함수율 <=2%\n함수율 <=5%", "함수율:3%", "ambiguous-facility-limit-excerpt"),
        ("함수율 <=2%", "함수율:3%\n함수율:1%", "ambiguous-stage-measurement-excerpt"),
    ]:
        service, state, _, _, rule, reading = with_proofs(limit, measurement)
        with pytest.raises(WorkflowError, match=code):
            operate(service, state, "conditions", {"rules": [rule], "readings": [reading]})


def test_preview_contract_rejects_stale_revision_quantity_and_cross_tenant_access():
    service, state, _, c, rule, reading = with_proofs()
    state = operate(service, state, "conditions", {"rules": [rule], "readings": [reading]})
    with pytest.raises(WorkflowError, match="revision-conflict"):
        what_if(
            state,
            {
                "expected_revision": 1,
                "readings": [{"condition_id": c["condition_id"], "value": "1", "unit": "%"}],
            },
        )
    quantity = next(r for r in condition_catalog(state) if r["field"] == "quantity_kg")
    with pytest.raises(WorkflowError, match="quantity-simulation-needs"):
        what_if(
            state,
            {
                "expected_revision": state["revision"],
                "readings": [
                    {"condition_id": quantity["condition_id"], "value": "1000", "unit": "kg"}
                ],
            },
        )
    overrides = dict(app.dependency_overrides)
    try:
        app.dependency_overrides[get_case_store] = lambda: service.store
        app.dependency_overrides[get_case_service] = lambda: service
        app.dependency_overrides[identity] = lambda: "tenant-a"
        with TestClient(app) as client:
            path = f"/workflow/cases/{state['case_id']}/what-if"
            body = {
                "expected_revision": state["revision"],
                "readings": [{"condition_id": c["condition_id"], "value": "1", "unit": "%"}],
            }
            assert client.post(path, json=body).status_code == 200
            app.dependency_overrides[identity] = lambda: "tenant-b"
            assert client.post(path, json=body).status_code == 404
    finally:
        app.dependency_overrides = overrides


def test_critic_receives_only_permitted_condition_fields_without_actor_or_private_prompt(
    monkeypatch,
):
    svc, state, _, c, rule, reading = with_proofs()
    state = svc.operate(
        "tenant-a",
        state["case_id"],
        state["revision"],
        "private-review-actor",
        "conditions",
        {"rules": [rule], "readings": [reading]},
        actor="PRIVATE_ACTOR",
    )
    state["inputs"]["prompt"] = "PRIVATE_PROMPT"
    for eid in ("limit", "reading"):
        state["evidence"][eid]["external_model_allowed"] = True
    model = Model({"concerns": []})
    monkeypatch.setattr(critic, "_last_started", 0)
    result = critic.WorkflowCritic(critic_service(model), clock=lambda: 1000)(state)
    assert result["model_calls"] == 1
    row = next(c for c in model.payload["candidates"] if c["declared_conditions"])[
        "declared_conditions"
    ][0]
    assert row["status"] == "unmet" and row["target_id"] == c["target_id"]
    assert "PRIVATE_ACTOR" not in str(model.payload) and "PRIVATE_PROMPT" not in str(model.payload)
    state["evidence"]["reading"]["external_model_allowed"] = False
    model = Model({"concerns": []})
    monkeypatch.setattr(critic, "_last_started", 0)
    critic.WorkflowCritic(critic_service(model), clock=lambda: 1000)(state)
    assert all(not c["declared_conditions"] for c in model.payload["candidates"])


def test_same_site_internal_movement_never_gets_zero_road_distance_or_a_paid_road_query():
    from types import SimpleNamespace

    from resource_ai.workflow.service import query_candidate_roads
    from tests.unit.test_evidence_workflow import complete_draft

    _, state, plan, _ = prepared()
    plan = deepcopy(plan)
    first = plan["research_packet"]["stages"][0]
    second = deepcopy(first)
    second["stage_id"] += "-second"
    plan["research_packet"]["stages"].append(second)
    plan["research_packet"]["handoffs"].append(
        {
            "leg_id": "internal-leg",
            "from_stage_id": first["stage_id"],
            "to_stage_id": second["stage_id"],
            "kind": "within_site_candidate",
        }
    )
    draft = complete_draft(state, plan)
    seen = []

    def run(request):
        seen.extend(request.legs)
        return SimpleNamespace(
            status="found",
            all_legs_routed=True,
            routes=[],
            total_road_distance_km=12,
            coverage={"external_calls": 0},
        )

    result = query_candidate_roads(state, plan, draft, SimpleNamespace(run=run))
    assert len(seen) == 1 and seen[0].leg_id.endswith(":origin-transport")
    assert result["measurements"]["internal-leg"]["road_distance_km"] is None
    assert not result["all_legs_routed"] and result["all_external_legs_routed"]


def test_origin_lot_moisture_is_not_reused_after_a_processing_step():
    _, state, _, _, _, _ = with_proofs()
    candidate = state["engine"]["review"]["research"]["candidates"][0]
    stage = deepcopy(candidate["plan"]["research_packet"]["stages"][0])
    stage["stage_id"] += "-downstream"
    candidate["plan"]["research_packet"]["stages"].append(stage)
    check = deepcopy(candidate["facility_assessment"]["stages"][0])
    check["stage_id"] = stage["stage_id"]
    candidate["facility_assessment"]["stages"].append(check)
    state["inputs"]["engine_request"]["lot"]["moisture_pct"] = 3
    state["field_provenance"]["moisture_pct"] = {
        "normalized_value": "3",
        "evidence_id": "reading",
        "quote": "함수율: 3 %",
    }
    rows = [
        c
        for c in build_conditions(state)
        if c["candidate_id"] == candidate["plan"]["plan_id"] and c["field"] == "moisture_pct"
    ]
    assert rows[0]["observation"]["value"] == "3"
    assert rows[1]["observation"] is None


def test_a_numeric_limit_is_not_silently_reused_after_the_material_form_changes():
    service, state, _, c, rule, reading = with_proofs()
    state = operate(service, state, "conditions", {"rules": [rule], "readings": [reading]})
    assert (
        next(r for r in build_conditions(state) if r["condition_id"] == c["condition_id"])["status"]
        == "unmet"
    )
    state["inputs"]["engine_request"]["lot"]["input_form"] = "film"
    row = next(r for r in build_conditions(state) if r["condition_id"] == c["condition_id"])
    assert row["requirement"] is None and row["observation"] is None and row["status"] == "unknown"
