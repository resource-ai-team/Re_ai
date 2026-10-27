"""Actual local Neo4j/RAG/Postgres; synthetic lots/documents. No model/map/OCR calls."""

import base64
import json
import os
from datetime import date
from pathlib import Path
from uuid import uuid4

from resource_ai.api.routes.workflow import get_case_service, get_case_store
from resource_ai.workflow.calculation import expected_targets
from resource_ai.workflow.models import CaseCreate
from resource_ai.workflow.service import plans


def main():
    os.environ["HF_HUB_OFFLINE"] = "1"
    service = get_case_service(get_case_store())
    rows = []
    for material, form in [("PET", "bottle"), ("aluminium", "can"), ("paper", "cardboard")]:
        tenant = "local-workflow-evaluation"
        inputs = CaseCreate(
            prompt=f"가상 {material} 로트의 공정 후보와 위험·확인 조건을 검토해 주세요.",
            mode="simulation",
            assessment_on=date.today(),
            engine_request={
                "origin": {
                    "organisation_id": "synthetic-qa",
                    "site_id": "synthetic-origin",
                    "label": "가상 QA 공장 · 실제 기업 아님",
                    "pickup_address": "합성 출발 좌표 · 현장 검증 없음",
                    "latitude": 37.2,
                    "longitude": 127.0,
                },
                "lot": {"material": material, "input_form": form, "quantity_kg": 1000},
                "max_plan_pages": 2,
                "max_legal_queries": 2,
                "max_technical_queries": 1,
                "include_lci": True,
            },
        )
        state = service.create(tenant, inputs)

        def step(action, payload, tenant=tenant):
            nonlocal state
            state = service.operate(
                tenant, state["case_id"], state["revision"], str(uuid4()), action, payload
            )
            return state

        step("run", {"max_repairs": 0})
        assert plans(state), material
        assert state["evaluation"]["questions"] and not state["evaluation"]["dispatch_approved"]
        text = f"Synthetic QA only. {material}: 1000 kg. 100000 KRW/t. Not a market quote."
        step(
            "document",
            {
                "expected_revision": state["revision"],
                "filename": f"synthetic-{material}.txt",
                "media_type": "text/plain",
                "base64_content": base64.b64encode(text.encode()).decode(),
                "use_azure_ocr": False,
            },
        )
        doc = next(iter(state["documents"].values()))
        assert not doc["user_confirmed"]
        step(
            "confirm_document",
            {
                "document_id": doc["document_id"],
                "text": text,
                "locator": "Synthetic QA line 1",
                "kind": "quote",
            },
        )
        assert not any(s.get("external_model_allowed") for s in state["evidence"].values())
        repair = None
        if material == "PET":
            plan = next(
                p
                for p in plans(state)
                if all(s.get("facility") for s in p["research_packet"]["stages"])
            )
            eid = "synthetic-numeric-mechanics"
            step(
                "evidence",
                {
                    "evidence": [
                        {
                            "evidence_id": eid,
                            "title": "가상 계산·환산 테스트. 현장 자료 아님",
                            "locator": "synthetic fixture 1",
                            "content": "Synthetic only: 1000 kg input/output each stage; 0 kg residue. Fixture quote 100000 KRW/t. All other rates are fictional. Not a market or LCA claim.",
                            "kind": "document",
                            "synthetic": True,
                            "user_confirmed": True,
                            "external_model_allowed": False,
                            "observed_on": str(date.today()),
                        }
                    ]
                },
            )
            stages = plan["research_packet"]["stages"]
            draft = {
                "candidate_id": plan["plan_id"],
                "facilities": [
                    {
                        "stage_id": s["stage_id"],
                        "facility_id": s["facility"]["key"],
                        "label": "가상 QA 선택 · 실인수 확인 아님",
                        "gate": {"latitude": 37.2, "longitude": 127.0},
                        "evidence_id": eid,
                    }
                    for s in stages
                ],
                "masses": [
                    {
                        "stage_id": s["stage_id"],
                        "input_kg": "1000",
                        "output_kg": "1000",
                        "residue_kg": "0",
                        "other_output_kg": "0",
                        "evidence_ids": [eid],
                    }
                    for s in stages
                ],
                "tails": [],
                "lines": [],
            }
            for index, (metric, category, target) in enumerate(sorted(expected_targets(plan))):
                basis = "output_product" if category == "revenue" else "input_waste"
                draft["lines"].append(
                    {
                        "line_id": f"fixture-{index}",
                        "target_id": target,
                        "metric": metric,
                        "category": category,
                        "component": "synthetic-full-scope",
                        "activity": {
                            "low": "0" if category == "residue" else "1000",
                            "unit": "kg",
                            "basis": basis,
                            "evidence_id": eid,
                        },
                        "factor": {
                            "low": "1",
                            "unit": ("KRW" if metric == "cost" else "kgCO2e") + "/kg",
                            "basis": basis,
                            "evidence_id": eid,
                        },
                        "mass_binding": {
                            "revenue": "stage_output",
                            "residue": "total_residue",
                            "process": "stage_input",
                            "transport": "lot_input",
                        }[category],
                        "mass_stage_id": stages[-1]["stage_id"] if category == "revenue" else None,
                        "scope": {
                            "region": "KR",
                            "reference_year": date.today().year,
                            "gwp": "IPCC_AR5",
                            "boundary": "fictional full-scope mechanics only",
                            "purpose": "cost" if metric == "cost" else category,
                            "system_boundary": "collection_to_recovered_output_and_residue",
                            "allocation": "cut_off_no_avoided_virgin_credit",
                        },
                        "covers_full_target": True,
                    }
                )
            wrong = next(l for l in draft["lines"] if l["metric"] == "cost")
            wrong["factor"].update(low="100000", unit="KRW/t")
            step("draft", {"draft": draft})
            step("run", {"max_repairs": 2})
            trace = state["last_repair_trace"]
            assert trace[0]["errors"] > trace[-1]["errors"]
            repair = {
                "before_errors": trace[0]["errors"],
                "after_errors": trace[-1]["errors"],
                "scope": "synthetic numbers on actual KG; no model recheck or site verification",
            }
        assert service.store.get(tenant, state["case_id"])["revision"] == state["revision"]
        rows.append(
            {
                "material": material,
                "case_id": state["case_id"],
                "candidates": len(plans(state)),
                "questions": len(state["evaluation"]["questions"]),
                "revision": state["revision"],
                "document_status": doc["extraction"]["status"],
                "model_calls": 0,
                "map_calls": 0,
                "paid_ocr_calls": 0,
                "dispatch_approved": False,
                "numeric_mechanics": repair,
            }
        )
    target = Path("data/local/workflow-scenarios.json")
    target.write_text(
        json.dumps(
            {
                "scope": "actual local KG/RAG/PG; synthetic lot/quote; not an accuracy benchmark",
                "runs": rows,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(json.dumps(rows, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
