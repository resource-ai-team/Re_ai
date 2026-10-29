"""Append-only claims, explicit sample binding and conservative adaptive output."""

from copy import deepcopy
from datetime import date
from decimal import Decimal

from resource_ai.calculators.mass_balance import drying_balance, forward_balance, minimum_removal
from resource_ai.knowledge_graph.ontology import digest
from resource_ai.material_quality.models import (
    QualityClaim,
    QualityConfirmation,
    QualityInputUpdate,
    QualityScenario,
    QualityScenarioUpdate,
    WasteProfile,
)
from resource_ai.workflow.store import WorkflowError

NUMERIC = {"quantity_kg": "kg", "contamination": "wt%", "moisture": "wt%"}


def add_inputs(state, payload, actor):
    update = QualityInputUpdate.model_validate({"expected_revision": 1, **payload})
    quality = state.setdefault("material_quality", {"claims": {}, "confirmations": []})
    old_sample = quality.get("sample_id")
    if old_sample and update.sample_id and old_sample != update.sample_id:
        raise WorkflowError("quality-primary-sample-change-requires-new-lot", 422)
    if update.sample_id:
        quality["sample_id"] = update.sample_id
    for claim in update.claims:
        claim.verification_status = {
            "operator_reported": "reported",
            "literature_assumption": "assumption",
        }.get(claim.source_type, "unconfirmed")
        if claim.source_type == "literature_assumption" and state["inputs"]["mode"] != "simulation":
            raise WorkflowError("quality-assumptions-require-simulation-mode", 422)
        append_claim(quality, claim, actor)


def append_claim(quality, claim, actor):
    record = claim.model_dump(mode="json")
    old = quality["claims"].get(claim.evidence_id)
    if old and old["claim"] != record:
        raise WorkflowError("quality-evidence-id-immutable-use-new-id", 409)
    if not old and len(quality["claims"]) >= 400:
        raise WorkflowError("quality-case-claim-limit", 422)
    quality["claims"][claim.evidence_id] = {"claim": record, "submitted_by": actor}


def add_document_claims(state, document_id, extraction, actor="ocr-adapter"):
    from resource_ai.document_processing.reports import extract_report

    report = extract_report(document_id, extraction)
    quality = state.setdefault("material_quality", {"claims": {}, "confirmations": []})
    for raw in report["claims"]:
        append_claim(quality, QualityClaim.model_validate(raw), actor)
    return report


def confirm(state, payload, actor, at):
    update = QualityConfirmation.model_validate({"expected_revision": 1, **payload})
    quality = state.setdefault("material_quality", {"claims": {}, "confirmations": []})
    if quality.get("sample_id") not in (None, update.sample_id):
        raise WorkflowError("quality-sample-mismatch", 422)
    selected = []
    for eid in update.evidence_ids:
        record = quality["claims"].get(eid)
        if not record:
            raise WorkflowError("quality-claim-not-in-case", 404)
        claim = QualityClaim.model_validate(record["claim"])
        if not claim.document_id or claim.source_type != "document_extracted_unconfirmed":
            raise WorkflowError("quality-confirmation-requires-unconfirmed-document-claim", 422)
        if claim.sample_id not in (None, update.sample_id):
            raise WorkflowError("quality-sample-mismatch", 422)
        selected.append(claim)
    quality["sample_id"] = update.sample_id
    for claim in selected:
        claim.source_type = "document_user_confirmed"
        claim.verification_status = "user_confirmed"
        claim.sample_id = update.sample_id
        claim.same_lot_confirmed = True
        quality["claims"][claim.evidence_id]["claim"] = claim.model_dump(mode="json")
    quality["confirmations"].append(
        {
            "evidence_ids": update.evidence_ids,
            "sample_id": update.sample_id,
            "by": actor,
            "at": at,
            "scope": "selected_transcription_and_lot_binding_not_scientific_or_legal_validity",
        }
    )


def set_scenario(state, payload, actor):
    update = QualityScenarioUpdate.model_validate({"expected_revision": 1, **payload})
    if update.scenario.explicit_assumptions and state["inputs"]["mode"] != "simulation":
        raise WorkflowError("quality-assumptions-require-simulation-mode", 422)
    quality = state.setdefault("material_quality", {"claims": {}, "confirmations": []})
    quality["scenario"] = update.scenario.model_dump(mode="json")
    quality["scenario_by"] = actor


def issues_for_claim(claim, sample):
    issues = []
    if claim.sample_id != sample or sample is None or not claim.same_lot_confirmed:
        issues.append("sample_or_lot_binding_unconfirmed")
    if claim.source_type in {"unknown", "document_extracted_unconfirmed"} or (
        claim.source_type == "photo_observed" and claim.field != "observed_impurities"
    ):
        issues.append("not_confirmed_measurement")
    if claim.field in NUMERIC:
        if claim.normalized_value is None or claim.unit != NUMERIC[claim.field]:
            issues.append("value_or_unit_missing_or_incompatible")
        if claim.meaning != "lot_property" or claim.qualifier != "exact":
            issues.append("not_exact_lot_property")
        if claim.field != "quantity_kg" and claim.basis != "wet_mass":
            issues.append("wet_mass_denominator_not_confirmed")
        if claim.normalized_value is not None and (
            claim.normalized_value <= 0
            if claim.field == "quantity_kg"
            else claim.normalized_value >= 100
        ):
            issues.append("outside_two_component_scope")
    elif not claim.text_value:
        issues.append("text_value_missing")
    return issues


def build_profile(state):
    quality = state.get("material_quality", {})
    sample = quality.get("sample_id")
    claims = [QualityClaim.model_validate(v["claim"]) for v in quality.get("claims", {}).values()]
    profile = WasteProfile(
        tenant_id=state.get("tenant_id", ""),
        lot_id=state.get("case_id", "pending"),
        sample_id=sample,
        measurements=claims,
        user_confirmations=quality.get("confirmations", []),
    )
    profile.input_sources = [
        {
            "document_id": d["document_id"],
            "input_kind": d.get("input_kind", "test_document"),
            "sha256": d["sha256"],
            "extraction_status": d["extraction"]["status"],
        }
        for d in state.get("documents", {}).values()
    ]
    validation, grouped = [], {}
    for claim in claims:
        issues = issues_for_claim(claim, sample)
        validation.append(
            {"evidence_id": claim.evidence_id, "issues": issues, "calculation_eligible": not issues}
        )
        profile.input_sources.append(
            {
                "evidence_id": claim.evidence_id,
                "source_type": claim.source_type,
                "document_id": claim.document_id,
                "synthetic": claim.synthetic,
            }
        )
        # Unconfirmed but scope-compatible OCR still participates in conflict detection.
        potential = [i for i in issues if i != "not_confirmed_measurement"]
        if claim.source_type == "document_extracted_unconfirmed":
            potential = [i for i in potential if i != "sample_or_lot_binding_unconfirmed"]
            if claim.sample_id != sample or sample is None:
                potential.append("different_sample")
        if not potential:
            grouped.setdefault(claim.field, []).append((claim, not issues))
    for field, rows in grouped.items():
        values = {c.normalized_value if field in NUMERIC else c.text_value for c, _ in rows}
        if len(values) > 1 and field != "observed_impurities":
            profile.conflicts.append(
                {
                    "field": field,
                    "evidence_ids": [c.evidence_id for c, _ in rows],
                    "values": sorted(str(v) for v in values),
                }
            )
            continue
        eligible = [c for c, ok in rows if ok]
        if not eligible:
            continue
        if field == "observed_impurities":
            profile.observed_impurities = sorted({c.text_value for c in eligible})
        elif field in NUMERIC:
            setattr(profile, field, eligible[0].normalized_value)
        elif field != "other_measurement":
            setattr(profile, field, eligible[0].text_value)
    profile.unknown_fields = [
        k
        for k in (
            "material",
            "material_form",
            "quantity_kg",
            "generation_process",
            "contamination",
            "moisture",
        )
        if getattr(profile, k) is None
    ]
    return profile, validation


def compare_quality_scenarios(state, scenarios):
    """Readonly batch assessment; no graph/provider calls or invented carbon points."""
    rows = []
    for scenario in scenarios:
        candidate = deepcopy(state)
        set_scenario(
            candidate, {"scenario": scenario.model_dump(mode="json")}, "readonly-comparison"
        )
        rows.append(assess(candidate))
    return {
        "revision": state["revision"],
        "results": rows,
        "pareto_fronts": [],
        "pareto_status": "pp_carbon_missing_use_existing_compare_scenarios_when_available",
        "recommended": False,
        "persisted": False,
    }


def assess(state):
    profile, validation = build_profile(state)
    quality = state.get("material_quality", {})
    raw_scenario = quality.get("scenario")
    scenario = QualityScenario.model_validate(raw_scenario) if raw_scenario else None
    mode = (
        "simulation"
        if state["inputs"]["mode"] == "simulation"
        or any(
            c.synthetic or c.source_type == "literature_assumption" for c in profile.measurements
        )
        else "reported_evidence"
    )
    issues = [{"code": "conflicting_claims", **c} for c in profile.conflicts]
    level = 0
    if profile.material == "PP" and profile.material_form in {
        "rigid_scrap",
        "rigid_part",
        "flake",
        "pellet",
        "bale",
    }:
        level = 1
    elif profile.material:
        issues.append({"code": "not_confirmed_rigid_pp_scope"})
    result = {
        "version": "pp-quality-v1",
        "level": level,
        "mode": mode,
        "profile": profile.model_dump(mode="json"),
        "claim_validation": validation,
        "evidence_summary": {
            kind: sum(c.source_type == kind for c in profile.measurements)
            for kind in (
                "document_extracted_unconfirmed",
                "document_user_confirmed",
                "operator_reported",
                "photo_observed",
                "literature_assumption",
                "unknown",
            )
        },
        "status": "REVIEW",
        "issues": issues,
        "mass_balance": None,
        "drying_balance": None,
        "forward_scenario": None,
        "economics": None,
        "pareto_eligible": False,
        "carbon_kgco2e": None,
        "carbon_status": "pp_compatible_lci_not_acquired",
        "recommended": False,
        "dispatch_approved": False,
        "legal_approved": False,
        "facility_acceptance_verified": False,
        "target": scenario.target.model_dump(mode="json") if scenario else None,
        "process_candidates": ["sorting", "shredding", "washing", "drying"] if level else [],
        "process_candidates_scope": "PP preprocessing concepts; actual KG/facility lookup is a separate existing run",
        "kg_candidates_loaded": False,
        "kg_integration_status": "requires_location_and_explicit_lot_mapping_to_existing_research_api",
        "questions": [],
        "used_evidence_ids": [],
        "limitations": [
            "이론적·사용자 선언 조건의 검토이며 실측 성능·시설 인수·법적 승인이 아닙니다.",
            "총 비PP 질량의 두 성분 모델이며 개별 오염물·유해성·식품접촉 안전성을 판정하지 않습니다.",
        ],
    }
    binding = quality.get("research_binding", {})
    result["kg_candidates_loaded"] = bool(state.get("engine")) and binding.get(
        "profile_sha256"
    ) == digest(profile.model_dump(mode="json"))
    if result["kg_candidates_loaded"]:
        result["kg_integration_status"] = "existing_research_executed_on_explicit_profile_mapping"
    for field in profile.unknown_fields:
        if not level and field not in {"material", "material_form"}:
            continue
        if field == "moisture" and not (
            scenario and any(r.field == "moisture" for r in scenario.target.limits)
        ):
            continue
        result["questions"].append(
            {
                "field": field,
                "question": {
                    "material": "소재가 PP인지 확인할 자료가 있나요?",
                    "material_form": "경질 부품·스크랩·플레이크 중 어떤 형태인가요?",
                    "quantity_kg": "이 시료와 연결된 로트 물량은 몇 kg인가요?",
                    "generation_process": "어떤 제조 공정에서 발생했나요?",
                    "contamination": "동일 시료의 습윤 질량 기준 총 비PP 함량(wt%)을 측정했나요? 없으면 명시적 가상 시나리오만 가능합니다.",
                    "moisture": "습윤 질량 기준 수분 측정값이 있나요? 건조 분석에 필요합니다.",
                }[field],
            }
        )
    if not scenario and level:
        result["questions"].append(
            {
                "field": "target",
                "question": "국내 구매자 요구사항 또는 명시적 가상 목표를 선택해 주세요. 기본 오염률 한도는 없습니다.",
            }
        )
    if not level or profile.conflicts or not scenario:
        return result
    target = scenario.target
    if target.effective_on and target.effective_on > date.fromisoformat(
        state["inputs"]["assessment_on"]
    ):
        issues.append({"code": "target_not_effective_on_assessment_date"})
        return result
    if (
        not target.applicability_confirmed
        or profile.material_form not in target.forms
        or target.applies_to != "input_waste"
    ):
        issues.append({"code": "target_applicability_not_confirmed_for_input_lot"})
        return result
    contamination_limit = next((r for r in target.limits if r.field == "contamination"), None)
    if (
        contamination_limit
        and profile.contamination is not None
        and profile.moisture is not None
        and profile.moisture > profile.contamination
    ):
        issues.append({"code": "total_non_pp_below_water_fraction_clarify_denominator_and_scope"})
        return result
    moisture_limit = next((r for r in target.limits if r.field == "moisture"), None)
    if moisture_limit and profile.quantity_kg is not None and profile.moisture is not None:
        if moisture_limit.max_value > profile.moisture:
            issues.append({"code": "moisture_target_above_initial_not_a_drying_removal"})
        else:
            result["drying_balance"] = drying_balance(
                profile.quantity_kg, profile.moisture, moisture_limit.max_value
            )
            result["level"], result["status"] = 2, "CONDITIONAL"
    if scenario.overall_pp_loss_pct == 100:
        issues.append({"code": "no_retained_pp_at_full_loss"})
        return result
    if not contamination_limit or profile.quantity_kg is None or profile.contamination is None:
        if contamination_limit or not result["drying_balance"]:
            issues.append({"code": "insufficient_compatible_mass_balance_inputs"})
        return result
    result["level"] = 2
    result["used_evidence_ids"] = [
        c.evidence_id
        for c in profile.measurements
        if c.field in {"quantity_kg", "contamination"}
        and not issues_for_claim(c, profile.sample_id)
    ]
    result["mass_balance"] = minimum_removal(
        profile.quantity_kg,
        profile.contamination,
        contamination_limit.max_value,
        scenario.overall_pp_loss_pct or Decimal(0),
    )
    result["status"] = "CONDITIONAL"
    result["reliability"] = "user_confirmed_or_reported_not_independently_validated"
    if scenario.stages:
        forward = forward_balance(profile.quantity_kg, profile.contamination, scenario.stages)
        if forward["output_contamination_pct"] is None:
            issues.append({"code": "no_output_material"})
            return result
        forward["declared_contamination_target_met"] = (
            Decimal(forward["output_contamination_pct"]) <= contamination_limit.max_value
        )
        forward["other_specification_items_verified"] = False
        result["forward_scenario"] = forward
        if scenario.economics:
            econ = scenario.economics
            output, residue = Decimal(forward["output_kg"]), Decimal(forward["residue_kg"])
            other = econ.transport_krw + residue * econ.residue_krw_per_kg
            revenue = output * econ.sale_price_krw_per_kg
            net = econ.preprocessing_krw + other - revenue
            result["level"] = 3
            result["economics"] = {
                "scope": "declared_scenario_only_not_supplier_quote",
                "net_cost_krw": str(net),
                "revenue_krw": str(revenue),
                "residue_cost_krw": str(residue * econ.residue_krw_per_kg),
                "baseline_delta_krw": str(net - econ.baseline_net_cost_krw)
                if econ.baseline_net_cost_krw is not None
                else None,
                "breakeven_preprocessing_total_krw": str(revenue - other),
                "evidence_ids": econ.evidence_ids,
                "unverified_scenario_inputs": True,
                "sales_revenue_conditional_on_buyer_acceptance": True,
            }
    # Existing two-objective comparator requires compatible carbon; never fabricate it.
    return result


def to_research_request(state, origin, **budgets):
    """Explicitly map compatible fields; never fill origin, contamination or moisture."""
    from resource_ai.api.schemas.site_review import SiteReviewRequest

    profile, _ = build_profile(state)
    forms = {
        "rigid_scrap": "scrap",
        "rigid_part": "scrap",
        "flake": "flake",
        "pellet": "pellet",
        "bale": "bale",
    }
    if (
        profile.conflicts
        or profile.material != "PP"
        or profile.quantity_kg is None
        or profile.material_form not in forms
    ):
        raise WorkflowError("quality-research-requires-unambiguous-pp-form-and-quantity", 422)
    lot = {
        "material": "PP",
        "input_form": forms[profile.material_form],
        "quantity_kg": str(profile.quantity_kg),
    }
    for field, target in (("contamination", "contamination_pct"), ("moisture", "moisture_pct")):
        if getattr(profile, field) is not None:
            lot[target] = str(getattr(profile, field))
    return SiteReviewRequest(origin=origin, lot=lot, include_lci=False, **budgets), digest(
        profile.model_dump(mode="json")
    )
