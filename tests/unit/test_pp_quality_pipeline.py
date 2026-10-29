"""Synthetic A/B/C pipeline cases. Not industrial measurements or OCR accuracy scores."""

import base64
import hashlib
from copy import deepcopy
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from resource_ai.api.routes.workflow import get_case_service, get_case_store, identity
from resource_ai.document_processing.adapters import MockLayoutOCR
from resource_ai.document_processing.reports import extract_report
from resource_ai.integrations.recycling_prices import load_snapshot, select_price
from resource_ai.main import app
from resource_ai.material_quality.models import QualityClaim, QualityScenario, TargetSpecification
from resource_ai.material_quality.service import add_document_claims, assess, confirm
from resource_ai.vision.observations import MockPhotoObserver
from resource_ai.workflow.models import DocumentUpload
from resource_ai.workflow.store import WorkflowError
from tests.unit.test_evidence_workflow import operate, setup_case


def claim(field, value, *, sample="synthetic-sample-a", source="operator_reported", **kw):
    numeric = field in {"quantity_kg", "contamination", "moisture"}
    return {
        "evidence_id": "synthetic-" + field,
        "field": field,
        "source_type": source,
        "normalized_value": str(value) if numeric else None,
        "text_value": None if numeric else value,
        "raw_value": str(value),
        "unit": "kg" if field == "quantity_kg" else "wt%" if numeric else None,
        "basis": "wet_mass",
        "meaning": "lot_property",
        "sample_id": sample,
        "same_lot_confirmed": True,
        "synthetic": True,
        **kw,
    }


def target():
    return {
        "specification_id": "synthetic-buyer-v1",
        "name": "가상 구매자 목표",
        "version": "1",
        "jurisdiction": "KR",
        "forms": ["rigid_scrap"],
        "applies_to": "input_waste",
        "requirement_type": "scenario_target",
        "source": "synthetic fixture; not a domestic standard",
        "explicitly_selected": True,
        "applicability_confirmed": True,
        "limits": [{"field": "contamination", "max_value": "5"}],
    }


def prepared(*, contamination=True):
    service, state = setup_case(with_engine=False)
    claims = [
        claim("material", "PP"),
        claim("material_form", "rigid_scrap"),
        claim("quantity_kg", 1000),
    ]
    if contamination:
        claims.append(claim("contamination", 20))
    state = operate(
        service, state, "quality_inputs", {"sample_id": "synthetic-sample-a", "claims": claims}
    )
    state = operate(service, state, "quality_scenario", {"scenario": {"target": target()}})
    return service, state


def layout(unit="wt%", result="20", name="오염률", sample="synthetic-sample-a"):
    cells = []
    for row, values in enumerate(
        [
            ["시험항목", "단위", "시험방법", "시험결과"],
            [name, unit, "synthetic wet mass method", result],
        ]
    ):
        for col, text in enumerate(values):
            cells.append(
                {
                    "rowIndex": row,
                    "columnIndex": col,
                    "content": text,
                    "boundingRegions": [{"pageNumber": 1, "polygon": [1, 2, 3, 2, 3, 4, 1, 4]}],
                }
            )
    return {
        "status": "extracted",
        "pages": [{"page": 1, "text": f"PP 시험성적서\n시료 ID: {sample}\n습윤 질량 기준 오염률"}],
        "tables": [{"cells": cells}],
        "synthetic": True,
    }


def test_mock_ocr_retains_value_method_sample_and_position_without_azure_keys():
    extracted = MockLayoutOCR(layout()).extract(b"synthetic", "image/png")
    report = extract_report("synthetic-doc", extracted)
    c = report["claims"][0]
    assert c["normalized_value"] == "20" and c["sample_id"] == "synthetic-sample-a"
    assert c["bounding_region"] and c["test_method"]
    assert c["source_type"] == "document_extracted_unconfirmed"
    assert c["extraction_confidence"] is None and c["synthetic"]


@pytest.mark.parametrize(
    "unit,name,result",
    [("mg/L", "용출 납", "1"), ("mg/L", "용출 납", "불검출"), ("wt%", "오염률", "<0.1")],
)
def test_migration_nd_and_detection_limits_are_not_exact_contamination(unit, name, result):
    _, state = prepared(contamination=False)
    report = add_document_claims(state, "synthetic-doc", layout(unit, result, name))
    confirm(
        state,
        {
            "evidence_ids": [report["claims"][0]["evidence_id"]],
            "sample_id": "synthetic-sample-a",
            "same_lot_confirmed": True,
        },
        "qa",
        "2026-10-29",
    )
    r = assess(state)
    assert r["profile"]["contamination"] is None and r["mass_balance"] is None
    if result == "불검출":
        assert report["claims"][0]["normalized_value"] is None


def test_different_samples_are_kept_separate():
    _, state = prepared(contamination=False)
    add_document_claims(state, "synthetic-doc", layout(sample="other-sample"))
    assert assess(state)["mass_balance"] is None
    with pytest.raises(WorkflowError, match="quality-sample-mismatch"):
        confirm(
            state,
            {
                "evidence_ids": [next(iter(state["material_quality"]["claims"]))],
                "sample_id": "other-sample",
                "same_lot_confirmed": True,
            },
            "qa",
            "now",
        )


def test_ocr_is_not_promoted_until_selected_claim_confirmation_scenario_a():
    _, state = prepared(contamination=False)
    report = add_document_claims(state, "synthetic-doc", layout())
    assert assess(state)["level"] == 1
    confirm(
        state,
        {
            "evidence_ids": [report["claims"][0]["evidence_id"]],
            "sample_id": "synthetic-sample-a",
            "same_lot_confirmed": True,
        },
        "qa",
        "2026-10-29",
    )
    r = assess(state)
    assert r["level"] == 2 and r["mode"] == "simulation"
    assert not r["recommended"] and not r["legal_approved"]


def test_manual_only_without_document_starts_level_one_scenario_b():
    _, state = prepared(contamination=False)
    r = assess(state)
    assert r["level"] == 1 and r["process_candidates"]
    assert r["mass_balance"] is None and r["carbon_kgco2e"] is None


def test_photo_cannot_supply_mass_fraction():
    with pytest.raises(ValidationError, match="photos cannot"):
        QualityClaim.model_validate(claim("contamination", 20, source="photo_observed"))
    assert MockPhotoObserver().observe(b"synthetic", "image/png")["quantitative_measurements"] == []


def test_explicit_assumption_is_conditional_not_independent_measurement():
    service, state = prepared(contamination=False)
    c = claim(
        "contamination",
        20,
        source="literature_assumption",
        assumptions=["synthetic sensitivity input; not a domestic plant rate"],
    )
    state = operate(service, state, "quality_inputs", {"claims": [c]})
    r = assess(state)
    assert r["level"] == 2 and r["status"] == "CONDITIONAL" and r["mode"] == "simulation"


def test_minimum_removal_mass_conservation():
    _, state = prepared()
    balance = assess(state)["mass_balance"]
    assert abs(
        Decimal(balance["minimum_contaminant_removal_kg"])
        - Decimal("157.8947368421052631578947368421052631579")
    ) < Decimal("1e-25")
    assert abs(
        Decimal(balance["output_mass_upper_bound_kg"]) + Decimal(balance["residue_kg"]) - 1000
    ) < Decimal("1e-25")


def test_overall_and_stage_tno_losses_must_not_overlap():
    with pytest.raises(ValidationError, match="overlapping stage losses"):
        QualityScenario.model_validate(
            {
                "target": target(),
                "overall_pp_loss_pct": 15,
                "stages": [
                    {
                        "stage_id": "dry",
                        "process": "drying",
                        "contaminant_removal_pct": 10,
                        "pp_loss_pct": 5,
                        "evidence_id": "synthetic",
                    }
                ],
            }
        )


def test_foreign_guideline_cannot_be_domestic_standard_and_ks_limits_not_invented():
    t = target()
    t.update(
        specification_id="rema-pp",
        requirement_type="trade_guideline",
        applies_to="postconsumer_bale",
    )
    with pytest.raises(ValidationError, match="foreign postconsumer"):
        TargetSpecification.model_validate(t)
    t = target()
    t["name"] = "KS M 3843"
    with pytest.raises(ValidationError, match="not been acquired"):
        TargetSpecification.model_validate(t)


def test_price_scope_mismatch_empty_csv_and_missing_value(tmp_path):
    raw = "시점,구분,수도권,강원,충북,충남,전북,전남,경북,경남,데이터 기준일\n2026-10-10,압축-PP(잡색),400,,,,,,,,2026-10-26\n".encode()
    path = tmp_path / "synthetic.csv"
    path.write_bytes(raw)
    manifest = {"sha256": hashlib.sha256(raw).hexdigest(), "encoding": "utf-8", "period": "2026-10"}
    snapshot = load_snapshot(path, manifest)
    assert (
        select_price(snapshot, period="2026-09", region="수도권", item="압축-PP(잡색)")[
            "price_krw_per_kg"
        ]
        is None
    )
    assert (
        select_price(snapshot, period="2026-10", region="전국", item="압축-PP(잡색)")["status"]
        == "period_region_item_mismatch"
    )
    assert (
        select_price(snapshot, period="2026-10", region="수도권", item="펠렛-PP(잡색)")[
            "price_krw_per_kg"
        ]
        is None
    )
    assert (
        select_price(snapshot, period="2026-10", region="강원", item="압축-PP(잡색)")["status"]
        == "not_published"
    )
    assert load_snapshot(tmp_path / "missing.csv", manifest)["records"] == []


def test_conflicting_ocr_twenty_operator_ten_stays_review_scenario_c():
    service, state = prepared(contamination=False)
    content = "PP 시험성적서\n시료 ID: synthetic-sample-a\n습윤 질량 기준\n오염률 20 wt%".encode()
    state = operate(
        service,
        state,
        "document",
        {
            "expected_revision": state["revision"],
            "filename": "synthetic.txt",
            "media_type": "text/plain",
            "base64_content": base64.b64encode(content).decode(),
        },
    )
    state = operate(service, state, "quality_inputs", {"claims": [claim("contamination", 10)]})
    r = assess(state)
    assert r["status"] == "REVIEW" and r["mass_balance"] is None
    assert r["profile"]["conflicts"][0]["values"] == ["10", "20"]


def test_quality_endpoints_tenant_revision_and_authorization():
    service, state = prepared()
    app.dependency_overrides[get_case_store] = lambda: service.store
    app.dependency_overrides[get_case_service] = lambda: service
    app.dependency_overrides[identity] = lambda: "tenant-a"
    try:
        with TestClient(app) as client:
            path = f"/workflow/cases/{state['case_id']}"
            r = client.get(path + "/waste-profile")
            assert r.status_code == 200 and r.json()["tenant_id"] == "tenant-a"
            assert client.get(path + "/pp-assessment").json()["level"] == 2
            app.dependency_overrides[identity] = lambda: "tenant-b"
            assert client.get(path + "/waste-profile").status_code == 404
            assert (
                client.put(
                    path + "/quality-inputs",
                    headers={"Idempotency-Key": "synthetic-other-tenant"},
                    json={
                        "expected_revision": state["revision"],
                        "claims": [claim("moisture", 10)],
                    },
                ).status_code
                == 404
            )
    finally:
        app.dependency_overrides.clear()


def test_insufficient_data_never_forces_recommendation_or_default_target():
    _, state = setup_case(with_engine=False)
    r = assess(state)
    assert r["level"] == 0 and r["target"] is None and not r["recommended"]
    assert r["mass_balance"] is None


def test_stage_forward_economics_level_three_remains_simulation():
    service, state = prepared()
    scenario = {
        "target": target(),
        "explicit_assumptions": ["synthetic process and economic values"],
        "stages": [
            {
                "stage_id": "sorting",
                "process": "sorting",
                "contaminant_removal_pct": 90,
                "pp_loss_pct": 2,
                "evidence_id": "synthetic-performance",
            }
        ],
        "economics": {
            "preprocessing_krw": 100000,
            "transport_krw": 10000,
            "residue_krw_per_kg": 100,
            "sale_price_krw_per_kg": 400,
            "evidence_ids": ["synthetic-economics"],
        },
    }
    state = operate(service, state, "quality_scenario", {"scenario": scenario})
    r = assess(state)
    assert r["level"] == 3 and r["mode"] == "simulation"
    assert r["forward_scenario"]["mass_conserved"]
    assert not r["pareto_eligible"] and r["carbon_kgco2e"] is None
    assert not r["facility_acceptance_verified"]


def test_field_photo_and_document_upload_cannot_share_ocr_mode():
    with pytest.raises(ValidationError, match="field photos"):
        DocumentUpload(
            expected_revision=1,
            filename="synthetic.png",
            media_type="image/png",
            base64_content=base64.b64encode(b"synthetic").decode(),
            input_kind="field_photo",
            use_azure_ocr=True,
        )


def test_existing_document_service_populates_structured_claims():
    service, state = setup_case(with_engine=False)
    content = "PP 시험성적서\n시료 ID: synthetic-sample-a\n습윤 질량 기준\n오염률 20 wt%".encode()
    state = operate(
        service,
        state,
        "document",
        {
            "expected_revision": state["revision"],
            "filename": "synthetic.txt",
            "media_type": "text/plain",
            "base64_content": base64.b64encode(content).decode(),
        },
    )
    doc = next(iter(state["documents"].values()))
    assert doc["structured_report"]["claims"]
    assert state["evaluation"]["material_quality"]["level"] == 0


def test_manual_cannot_impersonate_ocr_and_evidence_values_are_immutable():
    service, state = prepared()
    with pytest.raises(ValidationError):
        operate(
            service,
            state,
            "quality_inputs",
            {"claims": [claim("moisture", 10, source="document_user_confirmed")]},
        )
    with pytest.raises(WorkflowError, match="quality-evidence-id-immutable"):
        operate(
            service,
            state,
            "quality_inputs",
            {"claims": [claim("contamination", 10)]},
            key="synthetic-immutable-check",
        )


def test_mock_does_not_need_model_or_cloud_secrets(monkeypatch):
    monkeypatch.delenv("WORKFLOW_OCR_API_KEY", raising=False)
    assert (
        extract_report("synthetic-doc", MockLayoutOCR(layout()).extract(b"synthetic", "image/png"))[
            "rows"
        ][0]["normalized_value"]
        == "20"
    )


def test_quality_mapping_reuses_existing_engine_and_invalidates_on_new_claim():
    from tests.unit.test_site_discovery import request

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
    assert state["engine"] and assess(state)["kg_candidates_loaded"]
    assert state["inputs"]["engine_request"]["lot"]["moisture_pct"] is None
    state = operate(service, state, "quality_inputs", {"claims": [claim("moisture", 10)]})
    assert not assess(state)["kg_candidates_loaded"]


def test_readonly_multiple_scenarios_do_not_overwrite_profile_or_invent_pareto():
    from resource_ai.material_quality.service import compare_quality_scenarios

    _, state = prepared()
    before = deepcopy(state)
    a = QualityScenario.model_validate({"target": target()})
    b = a.model_copy(deep=True)
    b.target.limits[0].max_value = Decimal(10)
    result = compare_quality_scenarios(state, [a, b])
    assert state == before and len(result["results"]) == 2
    assert result["results"][0]["mass_balance"] != result["results"][1]["mass_balance"]
    assert not result["persisted"] and result["pareto_fronts"] == []


def test_wet_basis_drying_can_start_without_contamination_and_never_claim_purity():
    service, state = prepared(contamination=False)
    state = operate(service, state, "quality_inputs", {"claims": [claim("moisture", 12)]})
    t = target()
    t["limits"] = [{"field": "moisture", "max_value": "1"}]
    state = operate(service, state, "quality_scenario", {"scenario": {"target": t}})
    r = assess(state)
    assert r["level"] == 2 and r["mass_balance"] is None
    assert abs(
        Decimal(r["drying_balance"]["water_removal_kg"])
        - Decimal("111.1111111111111111111111111111111111111")
    ) < Decimal("1e-25")
    assert not r["recommended"]


def test_multi_sample_document_does_not_assign_first_sample_to_every_table():
    value = layout()
    value["pages"][0]["text"] += "\n시료 ID: synthetic-sample-b"
    r = extract_report("synthetic-multiple-samples", value)
    assert r["metadata"]["sample_id"] is None
    assert len(r["metadata"]["sample_ids"]) == 2
    assert all(c["sample_id"] is None for c in r["claims"])


def test_total_non_pp_partition_cannot_be_smaller_than_water_fraction():
    service, state = prepared()
    state = operate(service, state, "quality_inputs", {"claims": [claim("moisture", 30)]})
    r = assess(state)
    assert r["status"] == "REVIEW" and r["mass_balance"] is None
    assert any(i["code"].startswith("total_non_pp_below_water") for i in r["issues"])
