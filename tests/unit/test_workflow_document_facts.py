"""Selected document literals are input facts, not permits or facility approval."""

import base64
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from resource_ai.api.routes.workflow import get_case_service, get_case_store, identity
from resource_ai.main import app
from resource_ai.workflow.calculation import (
    evaluate_candidate,
    expected_targets,
    target_mass_binding,
)
from resource_ai.workflow.documents import present_documents, reviewable_fields
from resource_ai.workflow.models import DocumentFactApply, MetricLine
from resource_ai.workflow.service import recompute
from resource_ai.workflow.store import WorkflowError
from tests.unit.test_evidence_workflow import complete_draft, operate, prepared, setup_case


def upload(service, state, text="중량: 2 톤\n수분: 0.3 %\n오염도: 0.1 %\n매각단가: 200 원/kg"):
    state = operate(
        service,
        state,
        "document",
        {
            "expected_revision": state["revision"],
            "filename": "synthetic-lot.txt",
            "media_type": "text/plain",
            "base64_content": base64.b64encode(text.encode()).decode(),
            "use_azure_ocr": False,
        },
    )
    return state, next(iter(state["documents"].values()))


def selection(doc, *fields):
    return {
        "document_id": doc["document_id"],
        "same_lot_confirmed": True,
        "field_ids": [r["field_id"] for r in doc["review_fields"] if r["target_field"] in fields],
    }


def test_selected_literals_keep_quote_page_actor_and_conversion_and_invalidate_changed_drafts():
    service, state, _, _ = prepared()
    state, doc = upload(service, state)
    old = deepcopy(state)
    assert not state.get("field_provenance")
    updated = service.operate(
        "tenant-a",
        state["case_id"],
        state["revision"],
        "apply-facts-once",
        "apply_document_facts",
        selection(doc, "quantity_kg", "moisture_pct"),
        actor="synthetic-reviewer",
    )
    assert updated["inputs"]["engine_request"]["lot"]["quantity_kg"] == 2000
    assert updated["inputs"]["engine_request"]["lot"]["moisture_pct"] == 0.3
    assert updated["inputs"]["engine_request"]["lot"].get("contamination_pct") == old["inputs"][
        "engine_request"
    ]["lot"].get("contamination_pct")
    assert not updated["drafts"] and not updated["roads"]
    fact = updated["field_provenance"]["quantity_kg"]
    assert fact["quote"] == "중량: 2 톤" and fact["page"] == 1
    assert fact["conversion"] == "1 t = 1000 kg" and fact["confirmed_by"] == "synthetic-reviewer"
    source = updated["evidence"][fact["evidence_id"]]
    assert source["content"] == fact["quote"] and source["sha256"] == doc["sha256"]
    assert source["synthetic"] and not source["external_model_allowed"]
    assert "quantity_kg: 2000" in updated["report_markdown"]
    assert not updated["evaluation"]["dispatch_approved"]
    # A retry returns exactly the original operation result without re-running KG.
    replay = service.operate(
        "tenant-a",
        old["case_id"],
        old["revision"],
        "apply-facts-once",
        "apply_document_facts",
        selection(doc, "quantity_kg", "moisture_pct"),
        actor="synthetic-reviewer",
    )
    assert replay == updated
    assert old["drafts"] and not old.get("field_provenance")


@pytest.mark.parametrize(
    "variant,code",
    [
        ("unconfirmed", "document-lot-confirmation-required"),
        ("two_values", "choose-one-document-observation-per-field"),
        ("unknown", "document-field-selection-invalid"),
        ("price", "document-field-selection-invalid"),
        ("duplicate", "document-field-selection-invalid"),
        ("wrong_document", "document-not-in-case"),
    ],
)
def test_reject_ambiguous_or_unconfirmed_selections_without_mutating_case(variant, code):
    service, state = setup_case()
    state, doc = upload(service, state, "중량: 1 톤\n중량: 2 톤\n단가: 200 원/kg")
    payload = selection(doc, "quantity_kg")
    if variant != "two_values":
        payload["field_ids"] = payload["field_ids"][:1]
    if variant == "unconfirmed":
        payload["same_lot_confirmed"] = False
    if variant == "unknown":
        payload["field_ids"] = ["made-up-field"]
    if variant == "price":
        payload["field_ids"] = [doc["review_fields"][-1]["field_id"]]
    if variant == "duplicate":
        payload["field_ids"] *= 2
    if variant == "wrong_document":
        payload["document_id"] = "another-document"
    before = deepcopy(state["inputs"])
    with pytest.raises(WorkflowError, match=code):
        operate(service, state, "apply_document_facts", payload)
    assert service.store.get("tenant-a", state["case_id"])["inputs"] == before


@pytest.mark.parametrize(
    "text",
    [
        "중량: -2 t",
        "중량: 1,23 kg",
        "중량: 2 tCO2",
        "중량: 1~2 t",
        "중량: 1 t ~ 2 t",
        "중량: 2 톤 이하",
        "최대 중량: 2 t",
        "중량: 1 kg 또는 2 kg",
        "중량: 0 kg",
        "수분: 101 %",
        "중량: 99999999999999 t",
        "수분: " + "9" * 100 + "%",
        "중량: 0.1234567890123456789 kg",
    ],
)
def test_invalid_units_ranges_and_unrepresentable_precision_are_not_applied(text):
    assert not any(
        r["can_apply"] for r in reviewable_fields("test", {"pages": [{"page": 1, "text": text}]})
    )


def test_manual_input_site_or_material_edit_drops_stale_provenance_same_value_preserves_draft():
    service, state, _, _ = prepared()
    state, doc = upload(service, state, "중량: 1 톤")
    original_reader, reads = service.engine_reader, []

    def count_read(value):
        reads.append(value)
        return original_reader(value)

    service.engine_reader = count_read
    state = operate(service, state, "apply_document_facts", selection(doc, "quantity_kg"))
    assert state["drafts"] and state["field_provenance"]
    assert not reads
    inputs = deepcopy(state["inputs"])
    inputs["prompt"] += " · 설명만 수정"
    state = operate(service, state, "inputs", inputs)
    assert state["drafts"] and state["field_provenance"]
    assert not reads
    inputs["engine_request"]["lot"]["quantity_kg"] = 1200
    state = operate(service, state, "inputs", inputs)
    assert not state["drafts"] and not state["field_provenance"]
    assert len(reads) == 1
    state = operate(service, state, "apply_document_facts", selection(doc, "quantity_kg"))
    inputs = deepcopy(state["inputs"])
    inputs["engine_request"]["origin"]["site_id"] = "another-synthetic-site"
    state = operate(service, state, "inputs", inputs)
    assert not state["field_provenance"]


def test_old_saved_documents_are_enriched_on_read_without_rewriting_revision():
    service, state = setup_case()
    state, doc = upload(service, state)
    del state["documents"][doc["document_id"]]["review_fields"]
    result = present_documents(state)
    assert result["revision"] == state["revision"]
    assert result["documents"][doc["document_id"]]["review_fields"]
    assert "review_fields" not in state["documents"][doc["document_id"]]


def test_selected_field_retains_current_vs_previous_context_in_source():
    service, state = setup_case()
    state, doc = upload(service, state, "이번 로트 중량: 2 톤\n이전 로트 중량: 1 톤")
    current = doc["review_fields"][0]
    assert current["context_quote"] == "이번 로트 중량: 2 톤"
    state = operate(
        service,
        state,
        "apply_document_facts",
        {
            "document_id": doc["document_id"],
            "field_ids": [current["field_id"]],
            "same_lot_confirmed": True,
        },
    )
    record = state["field_provenance"]["quantity_kg"]
    assert state["evidence"][record["evidence_id"]]["content"] == "이번 로트 중량: 2 톤"
    assert record["quote"] == "중량: 2 톤"


def test_cost_and_carbon_missing_components_remain_separate_questions():
    _, state = setup_case()
    for candidate in state["engine"]["review"]["research"]["candidates"]:
        plan = candidate["plan"]
        actual = {
            (q["context"]["metric"], q["context"]["category"], q["target_id"])
            for q in state["evaluation"]["questions"]
            if q["candidate_id"] == plan["plan_id"] and q["code"] == "full_component_missing"
        }
        assert actual == expected_targets(plan)


def test_distinct_legal_requirements_with_same_code_are_not_lost_when_grouped():
    _, state = setup_case()
    plan = state["engine"]["review"]["research"]["candidates"][0]["plan"]
    target = plan["research_packet"]["stages"][0]["stage_id"]
    state["engine"]["review"]["map_markers"] = [
        {
            "code": "same-review-code",
            "target_id": target,
            "candidate_id": plan["plan_id"],
            "message": message,
            "next_action": "각 조문 원문 확인",
            "severity": "missing",
        }
        for message in ["가상 조문 A의 의무", "가상 조문 B의 예외"]
    ]
    recompute(state)
    qs = [q for q in state["evaluation"]["questions"] if q["code"] == "same-review-code"]
    assert len(qs) == 2
    assert any("가상 조문 A" in q["question"] for q in qs)
    assert any("가상 조문 B" in q["question"] for q in qs)


def test_apply_facts_api_uses_server_actor_and_tenant_and_rejects_client_value_override():
    service, state = setup_case()
    state, doc = upload(service, state)
    overrides = dict(app.dependency_overrides)
    try:
        app.dependency_overrides[get_case_service] = lambda: service
        app.dependency_overrides[get_case_store] = lambda: service.store
        app.dependency_overrides[identity] = lambda: "tenant-a"
        with TestClient(app) as client:
            path = f"/workflow/cases/{state['case_id']}/documents/apply-facts"
            payload = {**selection(doc, "quantity_kg"), "expected_revision": state["revision"]}
            bad = client.post(
                path,
                json={**payload, "normalized_value": "0"},
                headers={"Idempotency-Key": "bad-facts-override"},
            )
            assert bad.status_code == 422
            good = client.post(
                path,
                json=payload,
                headers={
                    "Idempotency-Key": "good-facts-selection",
                    "X-Workflow-Actor": "server-user",
                },
            )
            assert good.status_code == 200, good.text
            assert good.json()["field_provenance"]["quantity_kg"]["confirmed_by"] == "server-user"
            app.dependency_overrides[identity] = lambda: "tenant-b"
            assert client.get(f"/workflow/cases/{state['case_id']}").status_code == 404
    finally:
        app.dependency_overrides = overrides
    with pytest.raises(ValidationError):
        DocumentFactApply(
            expected_revision=1, document_id="x", field_ids=["x"], same_lot_confirmed="true"
        )


def test_handoff_and_tail_bindings_use_stage_material_not_original_lot():
    _, state, plan, draft = prepared()
    plan = deepcopy(plan)
    extra = deepcopy(plan["research_packet"]["stages"][0])
    extra["stage_id"] += "-synthetic-downstream"
    plan["research_packet"]["stages"].append(extra)
    plan["research_packet"]["handoffs"].append({"leg_id": "synthetic-handoff"})
    draft = complete_draft(state, plan)
    stages = plan["research_packet"]["stages"]
    assert len(stages) > 1
    first, second = stages[0]["stage_id"], stages[1]["stage_id"]
    leg = plan["research_packet"]["handoffs"][0]["leg_id"]
    assert target_mass_binding(plan, "transport", leg) == ("stage_input", second)
    assert target_mass_binding(
        plan,
        "transport",
        "residue-leg",
        [{"leg_id": "residue-leg", "purpose": "residue", "from_stage_id": first}],
    ) == ("stage_residue", first)
    # Coherent 1t -> 800kg + 200kg; later stages keep 800kg, all synthetic.
    for i, row in enumerate(draft["masses"]):
        row.update(
            input_kg="1000" if i == 0 else "800",
            output_kg="800",
            residue_kg="200" if i == 0 else "0",
        )
    for line in draft["lines"]:
        binding, stage = target_mass_binding(plan, line["category"], line["target_id"])
        line["activity"].update(
            low="200"
            if binding == "total_residue"
            else "1000"
            if binding == "lot_input" or (binding == "stage_input" and stage == first)
            else "800",
            high=None,
        )
    line = next(l for l in draft["lines"] if l["target_id"] == leg and l["metric"] == "carbon")
    line.update(mass_binding="none", mass_stage_id=None, trip_count=2, payload_kg_per_trip="400")
    line["activity"].update(low="16", high="16", unit="t_km", basis="activity")
    line["factor"].update(low="0.1", high="0.1", unit="kgCO2e/t_km", basis="activity")
    roads = {leg: {"status": "found", "road_distance_km": 20}}
    row = evaluate_candidate(plan, draft, state["evidence"], state["inputs"], roads)
    assert not any(e["code"].startswith("transport_") for e in row["errors"])
    line.update(payload_kg_per_trip="500")
    line["activity"].update(low="20", high="20")
    row = evaluate_candidate(plan, draft, state["evidence"], state["inputs"], roads)
    assert "transport_payload_mass_mismatch" in {e["code"] for e in row["errors"]}
    line.update(mass_binding="lot_input", trip_count=1)
    line["activity"].update(low="1000", high="1000", unit="kg", basis="input_waste")
    line["factor"].update(unit="kgCO2e/kg", basis="input_waste")
    row = evaluate_candidate(plan, draft, state["evidence"], state["inputs"])
    assert "mass_binding_target_mismatch" in {e["code"] for e in row["errors"]}


def test_trip_count_is_integer_and_trip_activity_matches_it():
    _, state, plan, draft = prepared()
    line = next(l for l in draft["lines"] if l["category"] == "transport")
    with pytest.raises(ValidationError):
        MetricLine.model_validate({**line, "trip_count": True})
    line.update(mass_binding="none", mass_stage_id=None, trip_count=2)
    line["activity"].update(low="1", high="1", unit="trip", basis="activity")
    line["factor"].update(
        unit="KRW/trip" if line["metric"] == "cost" else "kgCO2e/trip", basis="activity"
    )
    row = evaluate_candidate(plan, draft, state["evidence"], state["inputs"])
    assert "transport_trip_count_mismatch" in {e["code"] for e in row["errors"]}


def test_absent_stage_mass_is_unknown_instead_of_a_proven_numeric_conflict():
    _, state, plan, draft = prepared()
    draft["masses"] = []
    row = evaluate_candidate(plan, draft, state["evidence"], state["inputs"])
    assert "bound_mass_missing" in {m["code"] for m in row["missing"]}
    assert "bound_mass_mismatch" not in {m["code"] for m in row["errors"]}
