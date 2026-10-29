"""Narrow PP study on the existing case/version/evidence pipeline.

Scenario assumptions are never promoted to measurements, facility acceptance or legal approval.
"""

from decimal import Decimal, localcontext
from typing import Annotated, Literal

from pydantic import Field, StrictBool, model_validator

from resource_ai.api.schemas.analysis import StrictModel
from resource_ai.calculators.mass_balance import drying_balance
from resource_ai.knowledge_graph.ontology import digest
from resource_ai.workflow.models import Number, Revision
from resource_ai.workflow.store import WorkflowError

PCT = Annotated[Decimal, Field(ge=0, lt=100, allow_inf_nan=False)]
SIGNED = Annotated[Decimal, Field(ge=-Decimal("1e15"), le=Decimal("1e15"), allow_inf_nan=False)]
ZERO = Decimal(0)
HUNDRED = Decimal(100)
COMPONENTS = {
    "pp": ("PP", None),
    "pe": ("경질 PE", Decimal("0.10")),
    "pet_ps": ("PET·PS 합계", Decimal("0.03")),
    "paper": ("종이·골판지", Decimal("0.02")),
    "other_rigid": ("기타 비PP 경질 플라스틱", Decimal("0.01")),
    "liquid_residue": ("액체·내용물 잔류", Decimal("0.02")),
    "aluminium": ("알루미늄 캔", Decimal("0.02")),
    "unknown": ("성분 미확인", None),
}
SPEC = {
    "id": "rema-pp-small-rigid-2023-10-18",
    "title": "ReMA/ISRI PP Small Rigid Plastic",
    "url": "https://www.isrispecs.org/orpheus_resource/pp-small-rigid-plastic/",
    "effective_on": "2023-10-18",
    "checked_on": "2026-10-29",
    "scope": "해외 사용 후 PP 압축품의 참조 조성 조건 일부; 국내 법규·전체 거래 규격·업체 인수 승인 아님",
    "total_contamination_max_pct": "15",
}
PAPER = {
    "id": "kasper-2025-dry-washing-section-4.1",
    "title": "Losses and emissions in polypropylene recycling from household packaging waste",
    "url": "https://publications.tno.nl/publication/34643453/tIU4Ehl9/kasper-2025-losses.pdf",
    "doi": "10.1016/j.wasman.2024.11.029",
    "locator": "Section 4.1, p.238, Fig.6",
    "scope": "네덜란드 사용 후 PP의 파쇄 후·세척 전 loop dryer 시험; 현재 로트의 처리 성능 예측 아님",
}


class Composition(StrictModel):
    pp: Number
    pe: Number = ZERO
    pet_ps: Number = ZERO
    paper: Number = ZERO
    other_rigid: Number = ZERO
    liquid_residue: Number = ZERO
    aluminium: Number = ZERO
    unknown: Number = ZERO


class Drying(StrictModel):
    initial_moisture_pct: PCT
    target_moisture_pct: PCT
    basis: Literal["wet_mass"] = "wet_mass"

    @model_validator(mode="after")
    def decreasing(self):
        if self.target_moisture_pct > self.initial_moisture_pct:
            raise ValueError("drying target exceeds starting moisture")
        return self


class Economics(StrictModel):
    # All costs are batch totals in KRW; product mass is explicitly declared, not inferred.
    saleable_product_kg: Annotated[Decimal, Field(gt=0, le=Decimal("1e12"))]
    preprocessing_cost_krw: Number
    other_cost_krw: Number
    baseline_net_cost_krw: SIGNED | None = None
    prices_krw_per_kg: list[Number] = Field(min_length=1, max_length=3)
    basis: Literal["independent_scenario_assumption"] = "independent_scenario_assumption"


class PPStudy(StrictModel):
    scenario: Literal["sorting", "drying", "published"]
    target_specification_id: Literal["rema-pp-small-rigid-2023-10-18"] | None = None
    feedstock: Literal["post_consumer_rigid_pp_packaging", "other", "unknown"]
    data_basis: Literal["scenario_assumption"] = "scenario_assumption"
    prohibited: Literal["absent", "present", "unknown"] = "unknown"
    oil_or_hazardous: Literal["absent", "present", "unknown"] = "unknown"
    food_contact_intended: StrictBool = False
    composition: Composition | None = None
    pp_loss_pct: PCT = ZERO
    drying: Drying | None = None
    economics: Economics | None = None

    @model_validator(mode="after")
    def scenario_fields(self):
        if (self.scenario == "sorting") != (self.composition is not None):
            raise ValueError("sorting requires composition and other scenarios must not mix it")
        if (self.scenario == "drying") != (self.drying is not None):
            raise ValueError("drying requires its own wet-basis balance")
        return self


class PPStudyUpdate(Revision):
    study: PPStudy


def lot_scope(state):
    request = state["inputs"].get("engine_request") or {}
    return digest({"origin": request.get("origin"), "lot": request.get("lot")})


def save_study(state, payload, actor, at):
    study = PPStudy.model_validate(payload["study"])
    lot = (state["inputs"].get("engine_request") or {}).get("lot") or {}
    if lot.get("material") != "PP":
        raise WorkflowError("pp-study-requires-pp-lot", 422)
    if study.composition and sum(study.composition.model_dump().values()) != Decimal(
        str(lot["quantity_kg"])
    ):
        raise WorkflowError("pp-composition-mass-must-equal-lot", 422)
    if study.economics and study.economics.saleable_product_kg > Decimal(str(lot["quantity_kg"])):
        raise WorkflowError("pp-saleable-mass-exceeds-case-batch", 422)
    state["pp_study"] = {
        "input": study.model_dump(mode="json"),
        "lot_scope": lot_scope(state),
        "saved_by": actor,
        "saved_at": at,
    }


def sorting(study, mass):
    x = study.composition.model_dump()
    contamination = mass - x["pp"]
    checks = [
        {
            "id": "total_contamination",
            "label": "총 비PP 조성",
            "actual_pct": str(contamination / mass * HUNDRED),
            "max_pct": "15",
            "status": "met" if contamination <= mass * Decimal("0.15") else "unmet",
        }
    ]
    for key, (label, cap) in COMPONENTS.items():
        if cap is not None:
            checks.append(
                {
                    "id": key,
                    "label": label,
                    "actual_pct": str(x[key] / mass * HUNDRED),
                    "max_pct": str(cap * HUNDRED),
                    "status": "met" if x[key] <= cap * mass else "unmet",
                }
            )
    if x["unknown"]:
        return {"checks": checks, "status": "unknown_composition", "ideal_target": None}
    p = x["pp"] * (1 - study.pp_loss_pct / HUNDRED)
    if p <= 0:
        return {"checks": checks, "status": "no_retained_pp", "ideal_target": None}
    # Maximise final mass at fixed retained PP. Active caps only grow as mass decreases.
    caps = {k: cap for k, (_label, cap) in COMPONENTS.items() if cap is not None}
    final = min(mass - (x["pp"] - p), p / Decimal("0.85"))
    for _ in range(len(caps) + 2):
        active = {k for k, cap in caps.items() if x[k] > cap * final}
        capacity = (p + sum((x[k] for k in caps if k not in active), ZERO)) / (
            1 - sum((caps[k] for k in active), ZERO)
        )
        new = min(final, capacity)
        if new == final:
            break
        final = new
    minimum = {k: max(ZERO, x[k] - cap * final) for k, cap in caps.items()}
    non_pp_removal = mass - (x["pp"] - p) - final
    additional = max(ZERO, non_pp_removal - sum(minimum.values(), ZERO))
    return {
        "status": "composition_conditions_met"
        if all(c["status"] == "met" for c in checks)
        else "composition_conditions_unmet",
        "checks": checks,
        "ideal_target": {
            "output_mass_upper_bound_kg": str(final),
            "removal_lower_bound_kg": str(mass - final),
            "assumed_pp_loss_kg": str(x["pp"] - p),
            "retained_pp_kg": str(p),
            "minimum_removal_kg": {k: str(v) for k, v in minimum.items()},
            "additional_removal_to_allocate_kg": str(additional),
            "scope": "이상적 선택 제거·가정 PP 손실에서의 조성 목표; 설비 성능·실제 수율 예측 아님",
        },
    }


def drying(study, mass):
    values = study.drying
    balance = drying_balance(mass, values.initial_moisture_pct, values.target_moisture_pct)
    return {
        **balance,
        "basis": "wet_mass",
        "scope": "물·고형물만 존재하고 고형물 손실이 없는 가정; 에너지·시간·기름 제거·제품 품질은 계산하지 않음",
        "energy_kwh": None,
    }


def economics(values):
    if values is None:
        return None
    total = values.preprocessing_cost_krw + values.other_cost_krw
    baseline = values.baseline_net_cost_krw
    return {
        "scope": "별도로 가정한 판매 가능 제품량·비용의 민감도; 선별·건조 결과에서 판매량을 자동 추정하지 않음",
        "saleable_product_kg": str(values.saleable_product_kg),
        "rows": [
            {
                "price_krw_per_kg": str(price),
                "revenue_krw": str(values.saleable_product_kg * price),
                "net_cost_krw": str(total - values.saleable_product_kg * price),
                "benefit_vs_baseline_krw": None
                if baseline is None
                else str(baseline + values.saleable_product_kg * price - total),
                "preprocessing_cost_ceiling_krw": None
                if baseline is None
                else str(baseline + values.saleable_product_kg * price - values.other_cost_krw),
            }
            for price in values.prices_krw_per_kg
        ],
        "break_even_price_krw_per_kg": None
        if baseline is None
        else str((total - baseline) / values.saleable_product_kg),
        "baseline_provided": baseline is not None,
    }


def evaluate_study(state):
    record = state.get("pp_study")
    if not record:
        return None
    common = {
        "version": "pp-preprocessing-study-v1",
        "data_basis": "scenario_assumption",
        "sources": [SPEC, PAPER],
        "dispatch_approved": False,
        "environment": {"status": "missing_matched_activity_and_factors", "carbon_kgco2e": None},
        "model_calls": 0,
    }
    if record["lot_scope"] != lot_scope(state):
        return {
            **common,
            "status": "stale_inputs",
            "next_actions": ["로트·발생지 변경 후 PP 검토 가정을 다시 확인하세요."],
        }
    study = PPStudy.model_validate(record["input"])
    lot = state["inputs"]["engine_request"]["lot"]
    mass = Decimal(str(lot["quantity_kg"]))
    outside = (
        study.feedstock != "post_consumer_rigid_pp_packaging"
        or study.prohibited == "present"
        or study.oil_or_hazardous == "present"
        or study.food_contact_intended
        or lot.get("input_form") not in {"bale", "scrap", "flake", "bottle"}
    )
    missing_scope = study.prohibited == "unknown" or study.oil_or_hazardous == "unknown"
    output = {
        **common,
        "status": "outside_supported_scope"
        if outside
        else "scope_confirmation_needed"
        if missing_scope
        else "scenario_review",
        "scenario": study.scenario,
        "input_mass_kg": str(mass),
        "sorting": None,
        "drying": None,
        "economics": None,
        "published_cases": [
            {
                "rpm": 700,
                "moisture_pct": "2.3",
                "surface_contamination_pct": "5.1",
                "target_recovery_pct": "94",
            },
            {
                "rpm": 1100,
                "moisture_pct": "0.7",
                "surface_contamination_pct": "2.0",
                "target_recovery_pct": "87",
            },
        ],
        "published_conclusion": "해당 취성 PP의 세척 전 loop dryer 오염 제거는 손실 증가로 저자가 부적합하다고 판단함. 현재 원료의 처리 조건·회수율로 적용하지 않음.",
        "next_actions": [
            "해외 참조 조성 규격과 국내 업체의 실제 수탁 규격을 구분해 확인하세요.",
            "선별 성능·PP 손실·처리 후 시료를 시험해 이론 목표의 달성 여부를 확인하세요.",
            "같은 제품 형태의 견적·운송·잔재물·폐수·검사 비용을 확인하세요.",
        ],
        "checks": [
            {"code": "no_legal_or_acceptance_approval", "status": "hold"},
            {"code": "literature_not_transferred_to_current_lot", "status": "checked"},
            {"code": "no_avoided_carbon_credit_without_data", "status": "checked"},
        ],
    }
    if outside:
        output["next_actions"] = [
            "범위 밖 원료·금지 오염·식품접촉 용도는 별도 전문가 검토가 필요합니다. 자동 개선안은 제시하지 않습니다."
        ]
        return output
    if study.scenario == "sorting" and study.target_specification_id is None:
        output["status"] = "target_selection_required"
        output["next_actions"] = [
            "선별 조성 계산에는 해외 거래 규격의 명시적 선택이 필요합니다. 국내 제조업 PP는 WasteProfile과 별도 목표 규격 API를 사용하세요."
        ]
        return output
    with localcontext() as context:
        context.prec = 36
        if study.scenario == "sorting":
            output["sorting"] = sorting(study, mass)
        if study.scenario == "drying":
            output["drying"] = drying(study, mass)
        output["economics"] = economics(study.economics)
    return output


def report_lines(result):
    if not result:
        return []
    lines = [
        "",
        "## PP 전처리 검토 — 별도 시나리오",
        "- 입력은 가정이며 현장 검증·인수 승인이 아닙니다.",
        "- 상태: " + result["status"],
    ]
    if (result.get("sorting") or {}).get("ideal_target"):
        target = result["sorting"]["ideal_target"]
        lines.append(
            f"- 이상적 제거량 하한: {target['removal_lower_bound_kg']} kg / 산출 질량 상한: {target['output_mass_upper_bound_kg']} kg"
        )
    if result.get("drying"):
        lines.append("- 가정상 제거할 물: " + result["drying"]["water_removal_kg"] + " kg")
    if result.get("economics"):
        lines.append(
            "- 별도 경제성 가정의 손익분기 가격: "
            + str(result["economics"]["break_even_price_krw_per_kg"])
            + " KRW/kg"
        )
    lines += ["- 탄소: 동일 조건의 전체 자료 미확보로 계산하지 않음"]
    lines += ["- " + text for text in result.get("next_actions", [])]
    lines += ["- 근거: " + s["title"] + " " + s["url"] for s in result["sources"]]
    return lines
