"""Persist -> investigate -> complete evidence -> recompute -> bounded repair -> map."""

import re
from copy import deepcopy
from datetime import UTC, datetime
from decimal import Decimal

from resource_ai.api.schemas.road_routing import RoadLeg, RoadPoint, RoadRoutesRequest
from resource_ai.document_processing.windows_ocr import WindowsLocalOCR
from resource_ai.knowledge_graph.ontology import digest, identity
from resource_ai.material_quality import service as quality_service
from resource_ai.workflow.calculation import (
    break_even,
    evaluate_candidate,
    issues_for_source,
    robust_pareto,
)
from resource_ai.workflow.decisions import apply_reviews, build_conditions, decision_support, delta
from resource_ai.workflow.documents import (
    decode_document,
    local_extraction,
    reviewable_fields,
    suggest_fields,
)
from resource_ai.workflow.models import CandidateDraft, CaseCreate, DocumentFactApply
from resource_ai.workflow.pp_study import evaluate_study, report_lines, save_study
from resource_ai.workflow.store import WorkflowError


def now():
    return datetime.now(UTC).isoformat()


def plans(state):
    return [
        c["plan"]
        for c in (state.get("engine") or {})
        .get("review", {})
        .get("research", {})
        .get("candidates", [])
    ]


def _question(code, target, text, *, candidate=None, field=None, priority=2, context=None):
    return {
        "question_id": identity("question", [code, target, candidate, context]),
        "code": code,
        "target_id": target,
        "candidate_id": candidate,
        "field": field,
        "priority": priority,
        "question": text,
        "status": "unanswered",
        "context": context,
    }


def allowed_facilities(state, plan, stage_id):
    stage = next(s for s in plan["research_packet"]["stages"] if s["stage_id"] == stage_id)
    result = set()
    facility = stage.get("facility")
    if facility:
        result.add(facility["key"])
    for search in (state.get("engine") or {}).get("site", {}).get("facility_searches", []):
        if any(
            ref.get("candidate_id", ref.get("plan_id")) == plan["plan_id"]
            and ref.get("stage_id") == stage_id
            for ref in search.get("candidate_stage_refs", [])
        ):
            result.update(str(r["listing_id"]) for r in search.get("registry_candidates", []))
    return result


def validate_draft(state, draft):
    plan = next((p for p in plans(state) if p["plan_id"] == draft.candidate_id), None)
    if plan is None:
        raise WorkflowError("candidate-not-in-current-graph", 422)
    stage_ids = {s["stage_id"] for s in plan["research_packet"]["stages"]}
    if len({f.stage_id for f in draft.facilities}) != len(draft.facilities):
        raise WorkflowError("duplicate-facility-stage", 422)
    for f in draft.facilities:
        if f.stage_id not in stage_ids or f.facility_id not in allowed_facilities(
            state, plan, f.stage_id
        ):
            raise WorkflowError("facility-not-linked-to-candidate-stage", 422)
        if f.evidence_id not in state["evidence"]:
            raise WorkflowError("facility-location-evidence-missing", 422)
    if any(
        t.from_stage_id not in stage_ids or t.evidence_id not in state["evidence"]
        for t in draft.tails
    ):
        raise WorkflowError("invalid-tail-transport-binding", 422)
    main_legs = {f"{plan['plan_id']}:origin-transport"} | {
        h["leg_id"] for h in plan["research_packet"]["handoffs"]
    }
    tail_ids = [t.leg_id for t in draft.tails]
    if len(set(tail_ids)) != len(tail_ids) or set(tail_ids) & main_legs:
        raise WorkflowError("duplicate-or-reserved-tail-leg", 422)
    return plan


def internal_leg_ids(plan, draft):
    selected = {f["stage_id"]: f for f in draft["facilities"]}
    result = set()
    for handoff in plan["research_packet"]["handoffs"]:
        a, b = selected.get(handoff.get("from_stage_id")), selected.get(handoff.get("to_stage_id"))
        if (
            handoff.get("kind") == "within_site_candidate"
            and a
            and b
            and a["facility_id"] == b["facility_id"]
            and a["gate"] == b["gate"]
        ):
            result.add(handoff["leg_id"])
    return result


def query_candidate_roads(state, plan, draft, service):
    selected = {f["stage_id"]: f for f in draft["facilities"]}
    stages = plan["research_packet"]["stages"]
    if any(s["stage_id"] not in selected for s in stages):
        return {
            "status": "missing_facility_selection",
            "measurements": {},
            "all_legs_routed": False,
        }
    for f in draft["facilities"]:
        if issues_for_source(f["evidence_id"], state["evidence"], state["inputs"]):
            return {"status": "unconfirmed_gate", "measurements": {}, "all_legs_routed": False}
    origin = state["inputs"]["engine_request"]["origin"]
    previous = RoadPoint(latitude=origin["latitude"], longitude=origin["longitude"])
    legs = []
    handoffs = plan["research_packet"]["handoffs"]
    internal = internal_leg_ids(plan, draft)
    for index, stage in enumerate(stages):
        gate = RoadPoint.model_validate(selected[stage["stage_id"]]["gate"])
        leg_id = (
            f"{plan['plan_id']}:origin-transport" if index == 0 else handoffs[index - 1]["leg_id"]
        )
        if leg_id not in internal:
            legs.append(RoadLeg(leg_id=leg_id, origin=previous, destination=gate))
        previous = gate
    # Per-candidate batching preserves one caller-controlled budget, not one budget per tail.
    for tail in draft["tails"]:
        if issues_for_source(tail["evidence_id"], state["evidence"], state["inputs"]):
            raise WorkflowError("tail-location-evidence-unconfirmed", 422)
        legs.append(
            RoadLeg(
                leg_id=tail["leg_id"],
                origin=RoadPoint.model_validate(selected[tail["from_stage_id"]]["gate"]),
                destination=RoadPoint.model_validate(tail["destination"]),
            )
        )
    batch = service.run(RoadRoutesRequest(legs=legs, mode="independent", include_geometry=True))
    return {
        "status": "internal_movements_unmeasured"
        if internal and batch.all_legs_routed
        else batch.status,
        "all_legs_routed": batch.all_legs_routed and not internal,
        "all_external_legs_routed": batch.all_legs_routed,
        "internal_leg_ids": sorted(internal),
        "measurements": {
            **{r.leg_id: r.model_dump(mode="json") for r in batch.routes},
            **{
                leg: {
                    "leg_id": leg,
                    "status": "internal_site_unmeasured",
                    "road_distance_km": None,
                    "scope": "internal_activity_needs_separate_evidence",
                }
                for leg in internal
            },
        },
        "total_road_distance_km": None if internal else batch.total_road_distance_km,
        "external_road_distance_km": batch.total_road_distance_km,
        "scope": "ordered_main_process_chain_and_explicit_branches_not_dispatch_clearance",
        "coverage": batch.coverage,
    }


def recompute(state):
    questions, results, markers = [], [], []
    if state["inputs"]["engine_request"] is None:
        questions.append(
            _question(
                "structured_site_and_lot_required",
                "input",
                "선택한 공장 수거 위치와 소재·형태·물량을 입력해 주세요.",
                field="engine_request",
                priority=0,
            )
        )
    elif not state["inputs"]["engine_request"]["lot"].get("quantity_kg"):
        questions.append(
            _question(
                "lot_quantity_missing",
                "input",
                "이번 로트의 kg 물량을 확인해 주세요.",
                field="quantity_kg",
                priority=0,
            )
        )
    if state["inputs"]["engine_request"] and state.get("engine") and not plans(state):
        questions.append(
            _question(
                "no_supported_graph_candidate",
                "input",
                "소재·형태와 KG 지원 범위를 확인해 주세요. 근거 없는 후보를 만들지 않았습니다.",
                priority=0,
            )
        )
    for plan in plans(state):
        pid = plan["plan_id"]
        draft = state["drafts"].get(pid, CandidateDraft(candidate_id=pid).model_dump(mode="json"))
        selected = {f["stage_id"]: f for f in draft["facilities"]}
        result = evaluate_candidate(
            plan,
            draft,
            state["evidence"],
            state["inputs"],
            (state["roads"].get(pid) or {}).get("measurements"),
        )
        if any(s["stage_id"] not in selected for s in plan["research_packet"]["stages"]):
            result["comparable"] = False
            result["missing"].append({"code": "facility_selection_missing", "target_id": pid})
        for facility in draft["facilities"]:
            for code in issues_for_source(
                facility["evidence_id"], state["evidence"], state["inputs"]
            ):
                result["comparable"] = False
                result["missing"].append({"code": code, "target_id": facility["stage_id"]})
        candidate = next(
            c
            for c in state["engine"]["review"]["research"]["candidates"]
            if c["plan"]["plan_id"] == pid
        )
        if candidate["status"] == "unmet":
            result["comparable"] = False
            result["suitability_blocked"] = True
        residue_stages = {
            m["stage_id"] for m in draft["masses"] if Decimal(m.get("residue_kg", "0")) > 0
        }
        residue_tails = {t["from_stage_id"] for t in draft["tails"] if t["purpose"] == "residue"}
        if residue_stages - residue_tails:
            result["comparable"] = False
            result["missing"].append({"code": "residue_transport_scope_missing", "target_id": pid})
        results.append(result)
        for s in plan["research_packet"]["stages"]:
            if s["stage_id"] not in selected:
                questions.append(
                    _question(
                        "facility_selection_missing",
                        s["stage_id"],
                        "공정을 수행할 조사 대상 시설과 실제 출입구 좌표의 근거를 선택해 주세요.",
                        candidate=pid,
                        priority=1,
                    )
                )
        # Keep all issues visible, but group numeric coverage questions for readability.
        for issue in [*result["errors"], *result["missing"]]:
            text = {
                "mass_balance_missing": "단계별 투입·산출·잔재물 물량과 근거를 입력해 주세요.",
                "mass_balance_mismatch": "앞 단계 산출량과 다음 단계 투입량·잔재물 수지를 확인해 주세요.",
                "mass_binding_target_mismatch": "발생지 물량·단계별 투입·산출·잔재물 중 이 구간이 실제로 옮기는 물량을 연결해 주세요.",
                "transport_payload_mass_mismatch": "1회 평균 실적재량 × 운행 횟수가 이 구간의 실제 이동 물량과 같은지 확인해 주세요.",
                "transport_trip_count_mismatch": "운송 활동량의 운행 횟수와 입력한 운행 횟수를 맞춰 주세요.",
                "transport_mass_missing": "이 구간에서 옮기는 단계별 물량의 근거를 먼저 입력해 주세요.",
                "bound_mass_missing": "계산에 연결한 단계별 물량이 비어 있습니다. 투입·산출·잔재물 근거를 먼저 입력해 주세요.",
                "full_component_missing": f"{ {'cost': '비용', 'carbon': '탄소'}.get(issue.get('metric'), '') } · { {'process': '공정', 'transport': '운송', 'residue': '잔재물 처리', 'revenue': '매각 수익'}.get(issue.get('category'), '') } 전체 범위와 근거가 필요합니다.",
                "reporting_factor_not_route_lca": "보고용 폐기물 계수 대신 해당 공정 경계의 LCI·활동량을 확인해 주세요.",
                "factor_transfer_explanation_required": "계수의 국가·기준연도가 이번 평가에 적용되는 이유를 기록해 주세요.",
                "factor_unit_mismatch": "원문의 활동 단위와 단가·배출계수 단위를 대조해 주세요.",
                "road_distance_unverified": "선택한 공정 순서의 실제 도로거리를 조회해 주세요.",
                "residue_transport_scope_missing": "잔재물이 어디로 이동하는지와 그 운송 범위를 확인해 주세요.",
                "carbon_boundary_and_allocation_required": "탄소 비교의 공통 경계와 재활용 편익 할당 방식을 확인해 주세요.",
            }.get(issue["code"], "관련 원문·단위·확인 상태를 보완해 주세요.")
            questions.append(
                _question(
                    issue["code"],
                    issue["target_id"],
                    text,
                    candidate=pid,
                    priority=0 if issue in result["errors"] else 2,
                    context={k: issue[k] for k in ("metric", "category") if k in issue} or None,
                )
            )
            markers.append(
                {
                    "marker_id": identity("marker", [pid, issue]),
                    "candidate_id": pid,
                    "target_id": issue["target_id"],
                    "code": issue["code"],
                    "symbol": "!" if issue in result["errors"] else "?",
                    "status": "conflict" if issue in result["errors"] else "unknown",
                    "message": text,
                }
            )
    condition_rows = build_conditions(state)
    support = decision_support(state, condition_rows, results)
    for result in results:
        if any(
            c["candidate_id"] == result["candidate_id"] and c["status"] == "unmet"
            for c in condition_rows
        ):
            result["comparable"] = False
            result["condition_blocked"] = True
    replaced = {
        (c["candidate_id"], c["target_id"], "facility_" + c["field"]) for c in condition_rows
    }
    existing = [
        m
        for m in (state.get("engine") or {}).get("review", {}).get("map_markers", [])
        if (m.get("candidate_id"), m.get("target_id"), m.get("code")) not in replaced
    ]
    for c in condition_rows:
        if c["status"] == "met":
            continue
        existing.append(
            {
                "marker_id": c["condition_id"],
                "condition_id": c["condition_id"],
                "candidate_id": c["candidate_id"],
                "target_id": c["target_id"],
                "code": "intake_condition_" + c["status"],
                "severity": "error" if c["status"] == "unmet" else "missing",
                "message": c["label"] + " · " + c["explanation"],
                "next_action": "확인한 시설 기준과 해당 단계 로트의 성적서를 대조하세요.",
                "origin": "code",
                "confirmed_operational_incident": False,
                "evidence_ids": [
                    r["evidence_id"]
                    for r in (c["requirement"], c["observation"])
                    if r and r.get("evidence_id")
                ],
            }
        )
    for run in state.get("critic_runs", []):
        if run.get("superseded_by_repair"):
            continue
        for finding in run.get("findings", []):
            existing = [
                *existing,
                {
                    "marker_id": identity("model-concern", finding),
                    "code": "model_concern_requires_verification",
                    "candidate_id": finding.get("candidate_id"),
                    "target_id": finding.get("target_id"),
                    "message": "GPT 검토 지적 · 확인 필요: " + finding["message"],
                    "next_action": finding["next_action"],
                    "severity": "missing",
                    "origin": "model",
                    "evidence_ids": finding.get("evidence_ids", []),
                },
            ]
    for marker in existing:
        questions.append(
            _question(
                marker.get("code", "engine_review_required"),
                marker.get("target_id") or "global",
                "\n".join(
                    dict.fromkeys(filter(None, [marker.get("message"), marker.get("next_action")]))
                )
                or "연결된 근거를 확인해 주세요.",
                candidate=marker.get("candidate_id"),
                priority=0 if marker.get("severity") == "error" else 1,
                context={
                    "marker_id": marker.get("marker_id") or identity("marker-question", marker)
                },
            )
        )
        markers.append(
            {
                **marker,
                "symbol": "!" if marker.get("severity") == "error" else "?",
                "status": "conflict" if marker.get("severity") == "error" else "unknown",
            }
        )
    front = robust_pareto(results)
    thresholds = []
    for plan in plans(state):
        row = next(r for r in results if r["candidate_id"] == plan["plan_id"])
        draft = state["drafts"].get(plan["plan_id"])
        if draft:
            for other in results:
                if other["candidate_id"] != row["candidate_id"]:
                    thresholds.extend(break_even(plan, draft, row, other))
    unique_questions = {q["question_id"]: q for q in questions}
    state["evaluation"] = {
        "decision_support": support,
        "results": results,
        "pareto_for_review": front,
        "break_even": thresholds[:100],
        "questions": sorted(
            unique_questions.values(), key=lambda q: (q["priority"], q["question_id"])
        ),
        "map": {
            "nodes": [
                {
                    "candidate_id": p["plan_id"],
                    "stage_id": s["stage_id"],
                    "label": s["transition"]["label"],
                    "facility": (s.get("facility") or {}).get("label"),
                }
                for p in plans(state)
                for s in p["research_packet"]["stages"]
            ],
            "markers": markers,
            "view": "process_concept_not_geographic_map",
        },
        "route": "REVISE"
        if any(r["errors"] for r in results) or any(c["status"] == "unmet" for c in condition_rows)
        else "REVIEW",
        "dispatch_approved": False,
        "expert_review_completed": False,
        "generated_at": now(),
        "evidence_sha256": digest(state["evidence"]),
    }
    state["status"] = (
        "needs_input" if not plans(state) else "review_ready" if front else "needs_evidence"
    )
    if state.get("pp_study"):
        state["evaluation"]["pp_study"] = evaluate_study(state)
        if not plans(state):
            state["status"] = "pp_study_" + state["evaluation"]["pp_study"]["status"]
    if state.get("material_quality"):
        state["evaluation"]["material_quality"] = quality_service.assess(state)
    state["report_markdown"] = report(state)
    return state


def report(state):
    evaluation = state["evaluation"]
    lines = [
        "# 폐자원 공정 후보 검토",
        "",
        f"- 상태: {state['status']}",
        "- 같은 입력 폐자원 기준의 조사입니다. 기존 처리 방식은 가정하지 않습니다.",
        "- 사용자 확인 자료에 근거한 비교이며, 현행 허가·인수·발주 승인이 아닙니다.",
        "",
    ]
    for row in evaluation["results"]:
        lines += [
            f"## 후보 {row['candidate_id']}",
            f"- 비용 범위: {row['totals']['cost'] or '전체 미확인'} KRW",
            f"- 탄소 범위: {row['totals']['carbon'] or '전체 미확인'} kgCO2e",
            f"- 오류 {len(row['errors'])}개 / 확인 항목 {len(row['missing'])}개",
        ]
        for calculation in row["calculations"]:
            lines.append(
                f"- 계산 {calculation['line_id']}: {calculation['amount']} ({calculation['metric']}); 근거 {', '.join(calculation['evidence_ids'])}"
            )
    lines += [
        "",
        "## 다음 확인",
        *[f"- {q['question']} ({q['target_id']})" for q in evaluation["questions"]],
    ]
    support = evaluation.get("decision_support", {})
    if support.get("conditions"):
        lines += [
            "",
            "## 시설 조건 대조",
            "제출한 수탁 기준과 해당 단계 로트 측정값의 대조입니다. 현행 허가·법규 적용·인수 승인은 별도 확인합니다.",
        ]
        for condition in support["conditions"]:
            lines.append(
                f"- {condition['stage_label']} / {condition['label']}: {condition['status']} — {condition['explanation']}"
            )
            for part in (condition["requirement"], condition["observation"]):
                if part:
                    lines.append(f"  - 근거 {part['evidence_id']}: {part['quote']}")
        lines += ["", "## 먼저 확인할 자료"]
        lines += [
            f"- {a['title']} / {a['owner']} / {a['required_document']} / {a['why']}"
            for a in support["first_actions"]
        ]
    if state.get("last_decision_change"):
        change = state["last_decision_change"]
        lines += ["", f"## 변경 기록 v{change['from_revision']} → v{change['to_revision']}"]
        lines += [
            f"- {c['label']}: {c['before_status']} → {c['after_status']} / {c['explanation']}"
            for c in change["condition_changes"]
        ]
    lines += ["", "## 제출 근거 목록"]
    lines += [
        f"- {s['evidence_id']}: {s['title']} / {s['locator']} / {'확인함' if s.get('user_confirmed') else '미확인'} / {'가상' if s.get('synthetic') else '제출 자료'}"
        for s in state["evidence"].values()
    ]
    if state.get("field_provenance"):
        lines += [
            "",
            "## 문서에서 선택해 입력한 값",
            "선택한 로트 항목만 사용자 확인했습니다. 문서 전체·허가·인수 승인을 뜻하지 않습니다.",
        ]
        for name, item in state["field_provenance"].items():
            lines.append(
                f"- {name}: {item['normalized_value']} {item['normalized_unit']} / 원문: {item['quote']} / p.{item['page']} / 환산: {item['conversion']} / 근거: {item['evidence_id']}"
            )
    if state.get("engine"):
        lines += ["", "## KG·법령·기술 조사 상세", state["engine"]["review"]["report_markdown"]]
    lines += report_lines(evaluation.get("pp_study"))
    if evaluation.get("material_quality"):
        quality = evaluation["material_quality"]
        lines += [
            "",
            "## PP 자료 기반 사전 평가",
            f"- Level {quality['level']} / {quality['status']} / {quality['mode']}",
        ]
        if quality["mass_balance"]:
            lines.append(
                "- 이론적 최소 오염물 제거량 kg: "
                + quality["mass_balance"]["minimum_contaminant_removal_kg"]
            )
        for conflict in quality["profile"]["conflicts"]:
            lines.append(
                f"- 충돌: {conflict['field']} / {conflict['values']} / 원문·시료 확인 필요"
            )
        lines += ["- " + q["question"] for q in quality["questions"]]
        lines += ["- 실제 시설 인수·허가·실행 승인 미확인. PP 탄소계수 미확보."]
    return "\n".join(lines)


def repair_units(state):
    patches = []
    for draft in state["drafts"].values():
        for line in draft["lines"]:
            expected = f"{'KRW' if line['metric'] == 'cost' else 'kgCO2e'}/kg"
            raw = line["factor"]
            if line["activity"]["unit"] != "kg" or raw["unit"] != expected.replace("/kg", "/t"):
                continue
            source = state["evidence"].get(raw["evidence_id"], {})
            text = " ".join(source.get("content", "").split()).replace(",", "")
            if issues_for_source(raw["evidence_id"], state["evidence"], state["inputs"]):
                continue
            low, high = (re.escape(str(raw[k])) for k in ("low", "high"))
            values = (
                low
                if Decimal(raw["low"]) == Decimal(raw["high"])
                else low + r"\s*(?:~|–|-|to)\s*" + high
            )
            claim = r"(?<![\d.])" + values + r"\s*" + re.escape(raw["unit"]) + r"(?![A-Za-z0-9])"
            if not text or re.search(claim, text) is None:
                continue
            before = deepcopy(raw)
            raw.update(
                low=str(Decimal(raw["low"]) / 1000),
                high=str(Decimal(raw["high"]) / 1000),
                unit=expected,
            )
            patches.append(
                {
                    "candidate_id": draft["candidate_id"],
                    "line_id": line["line_id"],
                    "before": before,
                    "after": deepcopy(raw),
                    "rule": "1t=1000kg; factor /1000",
                    "evidence_id": raw["evidence_id"],
                }
            )
    return patches


class CaseService:
    def __init__(
        self, store, engine_reader, *, road_service=None, critic=None, ocr=None, local_ocr=None
    ):
        self.store, self.engine_reader = store, engine_reader
        self.road_service, self.critic, self.ocr = road_service, critic, ocr
        self.local_ocr = local_ocr or WindowsLocalOCR()

    def create(self, tenant, request):
        state = {
            "tenant_id": tenant,
            "contract_version": "evidence-workflow-v1",
            "inputs": request.model_dump(mode="json"),
            "evidence": {},
            "documents": {},
            "drafts": {},
            "roads": {},
            "engine": None,
            "events": [],
            "critic_runs": [],
            "field_provenance": {},
            "condition_reviews": {"rules": {}, "readings": {}},
            "created_at": now(),
        }
        return self.store.create(tenant, recompute(state))

    def operate(self, tenant, case_id, revision, key, action, payload, actor="backend"):
        op_id, state, replay = self.store.reserve(
            tenant, case_id, revision, key, {"action": action, "payload": payload}
        )
        if replay:
            return state
        state["tenant_id"] = tenant
        if (
            state.get("evaluation", {}).get("decision_support", {}).get("version")
            != "condition-decision-v2"
        ):
            recompute(state)  # Derive the old-version baseline without retrieval or a write.
        before = deepcopy(state)
        try:
            state.pop("active_operation", None)
            state["events"].append(
                {"stage": action, "status": "started", "at": now(), "operation_id": op_id}
            )
            if action == "inputs":
                previous_inputs = state["inputs"]
                state["inputs"] = CaseCreate.model_validate(payload).model_dump(mode="json")
                self._prune_field_provenance(state)
                old_request = previous_inputs.get("engine_request") or {}
                new_request = state["inputs"].get("engine_request") or {}
                if (
                    previous_inputs["mode"] != state["inputs"]["mode"]
                    or old_request.get("origin") != new_request.get("origin")
                    or any(
                        (old_request.get("lot") or {}).get(k)
                        != (new_request.get("lot") or {}).get(k)
                        for k in ("material", "input_form")
                    )
                ):
                    state["field_provenance"] = {}
                changed = (
                    previous_inputs["engine_request"] != state["inputs"]["engine_request"]
                    or previous_inputs["mode"] != state["inputs"]["mode"]
                )
                if changed or not state.get("engine"):
                    self._refresh(state, force=changed)
                state["critic_runs"] = []
            elif action == "evidence":
                for source in payload["evidence"]:
                    if (
                        source.get("document_id")
                        and source["document_id"] not in state["documents"]
                    ):
                        raise WorkflowError("document-not-in-case", 422)
                    state["evidence"][source["evidence_id"]] = {
                        **source,
                        "confirmed_by": actor if source["user_confirmed"] else None,
                        "content_sha256": digest(source.get("content", "")),
                    }
                    for pid, draft in state["drafts"].items():
                        if any(
                            f["evidence_id"] == source["evidence_id"]
                            for f in [*draft["facilities"], *draft["tails"]]
                        ):
                            state["roads"].pop(pid, None)
                state["critic_runs"] = []  # old judgments cannot silently apply to new evidence.
                self._prune_field_provenance(state)
            elif action == "draft":
                draft = CandidateDraft.model_validate(payload["draft"])
                validate_draft(state, draft)
                previous = state["drafts"].get(draft.candidate_id)
                state["drafts"][draft.candidate_id] = draft.model_dump(mode="json")
                if (
                    previous is None
                    or previous["facilities"] != state["drafts"][draft.candidate_id]["facilities"]
                    or previous["tails"] != state["drafts"][draft.candidate_id]["tails"]
                ):
                    state["roads"].pop(draft.candidate_id, None)
                state["critic_runs"] = []
            elif action == "document":
                self._document(tenant, state, payload)
            elif action == "apply_document_facts":
                self._apply_document_facts(state, payload, actor)
            elif action == "conditions":
                apply_reviews(state, payload, actor, now())
            elif action == "pp_study":
                save_study(state, payload, actor, now())
            elif action == "quality_inputs":
                quality_service.add_inputs(state, payload, actor)
            elif action == "quality_confirmations":
                quality_service.confirm(state, payload, actor, now())
            elif action == "quality_scenario":
                quality_service.set_scenario(state, payload, actor)
            elif action == "quality_research":
                request, profile_hash = quality_service.to_research_request(state, **payload)
                state["inputs"]["engine_request"] = request.model_dump(mode="json")
                self._refresh(state, force=True)
                state["material_quality"]["research_binding"] = {
                    "profile_sha256": profile_hash,
                    "engine_request_sha256": digest(state["inputs"]["engine_request"]),
                    "origin": payload["origin"],
                }
            elif action == "confirm_document":
                document = state["documents"].get(payload["document_id"])
                if document is None:
                    raise WorkflowError("document-not-in-case", 404)
                eid = "document:" + payload["document_id"]
                document["user_confirmed"] = True
                document["confirmation_scope"] = "selected_text_only_not_entire_document"
                state["evidence"][eid] = {
                    "evidence_id": eid,
                    "title": document["filename"],
                    "locator": payload["locator"],
                    "content": payload["text"],
                    "document_id": payload["document_id"],
                    "sha256": document["sha256"],
                    "kind": payload["kind"],
                    "user_confirmed": True,
                    "synthetic": state["inputs"]["mode"] == "simulation",
                    "confirmed_by": actor,
                    "observed_on": state["inputs"]["assessment_on"],
                    "valid_until": None,
                    "external_model_allowed": False,
                }
            elif action == "run":
                self._run(state, payload)
            else:
                raise WorkflowError("unknown-operation", 422)
            if action in {
                "inputs",
                "evidence",
                "draft",
                "apply_document_facts",
                "confirm_document",
                "conditions",
                "pp_study",
                "quality_inputs",
                "quality_confirmations",
                "quality_scenario",
            }:
                state["critic_runs"] = []
                state["model_recheck_pending"] = True
            recompute(state)
            state["last_decision_change"] = delta(before, state)
            state["report_markdown"] = report(state)
            state["events"].append(
                {"stage": action, "status": "completed", "at": now(), "operation_id": op_id}
            )
            return self.store.complete(tenant, op_id, state)
        except Exception:
            self.store.fail(tenant, case_id, op_id)
            raise

    @staticmethod
    def _prune_field_provenance(state):
        lot = (state["inputs"].get("engine_request") or {}).get("lot", {})
        for field, record in list(state.get("field_provenance", {}).items()):
            current = lot.get(field)
            evidence = state["evidence"].get(record["evidence_id"], {})
            if (
                current is None
                or Decimal(str(current)) != Decimal(record["normalized_value"])
                or not evidence.get("user_confirmed")
                or evidence.get("content") != record.get("context_quote", record["quote"])
                or evidence.get("sha256") != record["document_sha256"]
            ):
                del state["field_provenance"][field]

    def _apply_document_facts(self, state, payload, actor):
        selection = DocumentFactApply.model_validate(
            {**payload, "expected_revision": state["revision"]}
        )
        if not selection.same_lot_confirmed:
            raise WorkflowError("document-lot-confirmation-required", 422)
        document = state["documents"].get(selection.document_id)
        if document is None:
            raise WorkflowError("document-not-in-case", 404)
        if state["inputs"]["engine_request"] is None:
            raise WorkflowError("origin-and-lot-required-before-document-facts", 422)
        fields = {
            r["field_id"]: r
            for r in reviewable_fields(selection.document_id, document["extraction"])
        }
        if len(set(selection.field_ids)) != len(selection.field_ids) or any(
            k not in fields or not fields[k]["can_apply"] for k in selection.field_ids
        ):
            raise WorkflowError("document-field-selection-invalid", 422)
        chosen = [fields[k] for k in selection.field_ids]
        if len({r["target_field"] for r in chosen}) != len(chosen):
            raise WorkflowError("choose-one-document-observation-per-field", 422)
        previous = deepcopy(state["inputs"])
        updated = deepcopy(previous)
        for item in chosen:
            updated["engine_request"]["lot"][item["target_field"]] = item["normalized_value"]
        state["inputs"] = CaseCreate.model_validate(updated).model_dump(mode="json")
        for item in chosen:
            eid = "field:" + item["field_id"]
            state["evidence"][eid] = {
                "evidence_id": eid,
                "title": document["filename"] + " · " + item["field"],
                "locator": f"p.{item['page']} chars {item['context_start']}:{item['context_end']}; value {item['start']}:{item['end']}",
                "content": item["context_quote"],
                "document_id": selection.document_id,
                "sha256": document["sha256"],
                "kind": "activity",
                "user_confirmed": True,
                "synthetic": state["inputs"]["mode"] == "simulation",
                "external_model_allowed": False,
                "confirmed_by": actor,
                "observed_on": None,
                "valid_until": None,
            }
            state.setdefault("field_provenance", {})[item["target_field"]] = {
                **item,
                "document_id": selection.document_id,
                "evidence_id": eid,
                "confirmed_by": actor,
                "confirmed_at": now(),
                "document_sha256": document["sha256"],
                "user_confirmed": True,
            }
        document["user_confirmed"] = True
        document["confirmation_scope"] = "selected_fields_only_not_entire_document"
        if previous["engine_request"] != state["inputs"]["engine_request"] or not state.get(
            "engine"
        ):
            self._refresh(state, force=True)
        state["critic_runs"] = []
        state["events"].append(
            {
                "stage": "document_facts",
                "at": now(),
                "status": "completed",
                "document_id": selection.document_id,
                "fields": [
                    {
                        "field": r["target_field"],
                        "before": previous["engine_request"]["lot"].get(r["target_field"]),
                        "after": state["inputs"]["engine_request"]["lot"].get(r["target_field"]),
                        "evidence_id": "field:" + r["field_id"],
                    }
                    for r in chosen
                ],
            }
        )

    def _refresh(self, state, *, force=False):
        request = CaseCreate.model_validate(state["inputs"]).engine_request
        if request is None:
            state.update(engine=None, drafts={}, roads={}, critic_runs=[])
            return
        value = self.engine_reader(request)
        value = value.model_dump(mode="json") if hasattr(value, "model_dump") else value
        previous = state.get("engine")
        if force or (previous and previous["site"]["graph_trace"] != value["site"]["graph_trace"]):
            state.update(drafts={}, roads={}, critic_runs=[])
            state["events"].append(
                {
                    "stage": "invalidate",
                    "status": "completed",
                    "at": now(),
                    "reason": "input_or_graph_changed",
                    "invalidated": ["facility_selection", "calculation", "comparison", "critic"],
                }
            )
        state["engine"] = value

    def _run(self, state, options):
        self._refresh(state)
        state["critic_runs"] = []
        state["model_recheck_pending"] = state.get("model_recheck_pending", False)
        if options.get("query_roads"):
            if self.road_service is None:
                raise WorkflowError("road-service-not-configured", 503)
            remaining = self.road_service.config.road_routing_max_calls
            for plan in plans(state):
                draft = state["drafts"].get(plan["plan_id"])
                if draft:
                    required = (
                        len(plan["research_packet"]["stages"])
                        - len(internal_leg_ids(plan, draft))
                        + len(draft["tails"])
                    )
                    if required > remaining:
                        state["roads"][plan["plan_id"]] = {
                            "status": "budget_exhausted",
                            "measurements": {},
                            "all_legs_routed": False,
                        }
                        continue
                    state["roads"][plan["plan_id"]] = query_candidate_roads(
                        state, plan, draft, self.road_service
                    )
                    remaining -= int(
                        state["roads"][plan["plan_id"]].get("coverage", {}).get("external_calls", 0)
                    )
        recompute(state)
        trace = [
            {
                "phase": "before",
                "errors": sum(len(r["errors"]) for r in state["evaluation"]["results"]),
            }
        ]
        call_budget = options.get("max_model_calls", 1)
        if options.get("run_critic") and self.critic and call_budget:
            state["critic_runs"].append({**self.critic(state), "phase": "before_repair"})
            if state["critic_runs"][-1].get("status") == "completed":
                state["model_recheck_pending"] = False
            call_budget -= state["critic_runs"][-1].get("model_calls", 0)
        for index in range(options.get("max_repairs", 2)):
            patches = repair_units(state)
            if not patches:
                break
            recompute(state)
            trace.append(
                {
                    "phase": "repair",
                    "round": index + 1,
                    "patches": patches,
                    "errors": sum(len(r["errors"]) for r in state["evaluation"]["results"]),
                }
            )
        if len(trace) > 1:
            for critic in state["critic_runs"]:
                critic["superseded_by_repair"] = True
            if options.get("run_critic") and self.critic and call_budget:
                state["critic_runs"].append({**self.critic(state), "phase": "after_repair"})
            state["model_recheck_pending"] = not any(
                r.get("phase") == "after_repair" and r.get("status") == "completed"
                for r in state["critic_runs"]
            )
        state["last_repair_trace"] = trace

    def _document(self, tenant, state, payload):
        from resource_ai.workflow.models import DocumentUpload

        upload = DocumentUpload.model_validate(payload)
        if upload.input_kind == "field_photo" and upload.use_azure_ocr:
            raise WorkflowError("field-photo-is-not-a-test-document", 422)
        content, checksum = decode_document(upload)
        doc_id = checksum[:32]
        if doc_id in state["documents"] and upload.use_azure_ocr:
            raise WorkflowError("document-already-present-no-automatic-ocr-resubmission", 409)
        if (
            upload.use_azure_ocr
            and self.ocr
            and self.ocr.config.workflow_ocr_enabled
            and self.ocr.config.workflow_ocr_allow_external
            and not self.store.claim_ocr(tenant, state["case_id"], checksum)
        ):
            raise WorkflowError("ocr-already-claimed-check-provider-before-resubmission", 409)
        extraction = (
            {"status": "photo_observation_unavailable", "provider": "none", "pages": []}
            if upload.input_kind == "field_photo"
            else self.local_ocr.extract(content, upload.media_type)
            if upload.use_local_image_ocr
            else self.ocr.extract(content, upload.media_type)
            if upload.use_azure_ocr and self.ocr
            else local_extraction(content, upload.media_type)
        )
        self.store.put_document(
            tenant, state["case_id"], doc_id, upload.media_type, content, checksum
        )
        state["documents"][doc_id] = {
            "document_id": doc_id,
            "sha256": checksum,
            "filename": upload.filename.replace("\\", "/").rsplit("/", 1)[-1],
            "media_type": upload.media_type,
            "input_kind": upload.input_kind,
            "extraction": extraction,
            "suggested_fields": suggest_fields(extraction),
            "review_fields": reviewable_fields(doc_id, extraction),
            "user_confirmed": False,
        }
        state["documents"][doc_id]["structured_report"] = quality_service.add_document_claims(
            state, doc_id, extraction
        )
