"""Actual local KG/RAG/Postgres, explicitly fictional intake limits and measurements."""

import json
import os
from copy import deepcopy
from datetime import date
from pathlib import Path
from uuid import uuid4

from resource_ai.api.routes.workflow import get_case_service, get_case_store
from resource_ai.workflow.decisions import condition_catalog, what_if
from resource_ai.workflow.models import CaseCreate, Source


def main():
    os.environ["HF_HUB_OFFLINE"] = "1"
    tenant = "decision-evaluation-20261029"
    service = get_case_service(get_case_store())
    state = service.create(
        tenant,
        CaseCreate(
            prompt="가상 PET 로트의 공정 후보·조건을 검토해 주세요. 실제 기업·측정·거래 자료가 아닙니다.",
            mode="simulation",
            assessment_on=date(2026, 10, 29),
            engine_request={
                "origin": {
                    "organisation_id": "synthetic-evaluation",
                    "site_id": "synthetic-origin",
                    "label": "가상 검증용 공장",
                    "pickup_address": "합성 위치 · 실측 없음",
                    "latitude": 37.2,
                    "longitude": 127.0,
                },
                "lot": {"material": "PET", "input_form": "bottle", "quantity_kg": 1000},
                "max_plan_pages": 2,
                "max_legal_queries": 2,
                "max_technical_queries": 1,
                "include_lci": True,
            },
        ),
    )

    def step(action, payload):
        nonlocal state
        state = service.operate(
            tenant,
            state["case_id"],
            state["revision"],
            str(uuid4()),
            action,
            payload,
            actor="synthetic-evaluator",
        )
        return state

    step("run", {"max_repairs": 0})
    row = next(
        c
        for c in condition_catalog(state)
        if c["field"] == "moisture_pct" and c["stage_index"] == 0 and c["facility_id"]
    )

    def evidence(eid, text, kind):
        return Source(
            evidence_id=eid,
            title="가상 조건 검증 자료 · 실제 수탁·시험 기준 아님",
            locator="synthetic fixture line 1",
            content=text,
            kind=kind,
            synthetic=True,
            user_confirmed=True,
            external_model_allowed=False,
            observed_on=date(2026, 10, 29),
        ).model_dump(mode="json")

    step(
        "evidence",
        {
            "evidence": [
                evidence("demo-limit", "함수율 <= 2 %", "facility"),
                evidence("demo-reading", "함수율: 3 %", "activity"),
            ]
        },
    )
    rule = {
        "condition_id": row["condition_id"],
        "facility_id": row["facility_id"],
        "operator": "le",
        "limit": "2",
        "unit": "%",
        "evidence_id": "demo-limit",
        "applicability_confirmed": True,
    }
    reading = {
        "condition_id": row["condition_id"],
        "value": "3",
        "unit": "%",
        "evidence_id": "demo-reading",
        "same_lot_and_stage_confirmed": True,
    }
    step("conditions", {"rules": [rule], "readings": [reading]})
    actual = next(
        c
        for c in state["evaluation"]["decision_support"]["conditions"]
        if c["condition_id"] == row["condition_id"]
    )
    assert actual["status"] == "unmet"
    saved = deepcopy(state)
    saved_persisted = service.store.get(tenant, state["case_id"])
    saved_history = service.store.history(tenant, state["case_id"])
    preview = what_if(
        state,
        {
            "expected_revision": state["revision"],
            "readings": [{"condition_id": row["condition_id"], "value": "1", "unit": "%"}],
        },
    )
    assumed = next(
        c
        for c in preview["decision_support"]["conditions"]
        if c["condition_id"] == row["condition_id"]
    )
    assert assumed["status"] == "met" and preview["cost_and_carbon"] is None
    assert state == saved
    assert service.store.get(tenant, state["case_id"]) == saved_persisted
    assert service.store.history(tenant, state["case_id"]) == saved_history
    assert all(not g["dispatch_approved"] for g in preview["decision_support"]["candidate_gates"])
    step("evidence", {"evidence": [evidence("demo-reading", "함수율: 1 %", "activity")]})
    assert (
        next(
            c
            for c in state["evaluation"]["decision_support"]["conditions"]
            if c["condition_id"] == row["condition_id"]
        )["status"]
        == "unknown"
    )
    reading["value"] = "1"
    step("conditions", {"readings": [reading]})
    final = next(
        c
        for c in state["evaluation"]["decision_support"]["conditions"]
        if c["condition_id"] == row["condition_id"]
    )
    assert final["status"] == "met"
    assert (
        next(
            g
            for g in state["evaluation"]["decision_support"]["candidate_gates"]
            if g["candidate_id"] == row["candidate_id"]
        )["status"]
        == "hold"
    )
    output = {
        "date": "2026-10-29",
        "scope": "actual local graph/retrieval/database; synthetic lot, limits, measurements; not a customer or accuracy benchmark",
        "case_id": state["case_id"],
        "candidate_count": len(state["engine"]["review"]["research"]["candidates"]),
        "trace": [
            "missing_evidence",
            "unmet_at_3_percent_vs_2_percent_limit",
            "hypothetical_met_at_1_percent_no_save",
            "changed_source_invalidates_old_reading",
            "confirmed_new_reading_met_but_legal_acceptance_hold",
        ],
        "final_revision": state["revision"],
        "preview_cost_and_carbon": None,
        "preview_saved": False,
        "dispatch_approved": False,
        "paid_model_calls": 0,
        "map_calls": 0,
        "paid_ocr_calls": 0,
        "last_change": state["last_decision_change"],
    }
    path = Path("data/local/decision-scenarios-2026-10-29.json")
    path.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {k: v for k, v in output.items() if k != "last_change"}, ensure_ascii=False, indent=2
        )
    )


if __name__ == "__main__":
    main()
