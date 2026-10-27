"""Persist -> investigate -> complete evidence -> recompute -> bounded repair -> map."""

import re
from copy import deepcopy
from datetime import UTC, datetime
from decimal import Decimal

from resource_ai.api.schemas.road_routing import RoadLeg, RoadPoint, RoadRoutesRequest
from resource_ai.knowledge_graph.ontology import digest, identity
from resource_ai.workflow.calculation import (
    break_even,
    evaluate_candidate,
    issues_for_source,
    robust_pareto,
)
from resource_ai.workflow.documents import decode_document, local_extraction, suggest_fields
from resource_ai.workflow.models import CandidateDraft, CaseCreate
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


def _question(code, target, text, *, candidate=None, field=None, priority=2):
    return {
        "question_id": identity("question", [code, target, candidate]),
        "code": code,
        "target_id": target,
        "candidate_id": candidate,
        "field": field,
        "priority": priority,
        "question": text,
        "status": "unanswered",
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
    for index, stage in enumerate(stages):
        gate = RoadPoint.model_validate(selected[stage["stage_id"]]["gate"])
        leg_id = (
            f"{plan['plan_id']}:origin-transport" if index == 0 else handoffs[index - 1]["leg_id"]
        )
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
        "status": batch.status,
        "all_legs_routed": batch.all_legs_routed,
        "measurements": {r.leg_id: r.model_dump(mode="json") for r in batch.routes},
        "total_road_distance_km": batch.total_road_distance_km,
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
    existing = (state.get("engine") or {}).get("review", {}).get("map_markers", [])
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
                marker.get("next_action")
                or marker.get("message")
                or "연결된 근거를 확인해 주세요.",
                candidate=marker.get("candidate_id"),
                priority=0 if marker.get("severity") == "error" else 1,
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
        "route": "REVISE" if any(r["errors"] for r in results) else "REVIEW",
        "dispatch_approved": False,
        "expert_review_completed": False,
        "generated_at": now(),
        "evidence_sha256": digest(state["evidence"]),
    }
    state["status"] = (
        "needs_input" if not plans(state) else "review_ready" if front else "needs_evidence"
    )
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
    lines += ["", "## 제출 근거 목록"]
    lines += [
        f"- {s['evidence_id']}: {s['title']} / {s['locator']} / {'확인함' if s.get('user_confirmed') else '미확인'} / {'가상' if s.get('synthetic') else '제출 자료'}"
        for s in state["evidence"].values()
    ]
    if state.get("engine"):
        lines += ["", "## KG·법령·기술 조사 상세", state["engine"]["review"]["report_markdown"]]
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
    def __init__(self, store, engine_reader, *, road_service=None, critic=None, ocr=None):
        self.store, self.engine_reader = store, engine_reader
        self.road_service, self.critic, self.ocr = road_service, critic, ocr

    def create(self, tenant, request):
        state = {
            "contract_version": "evidence-workflow-v1",
            "inputs": request.model_dump(mode="json"),
            "evidence": {},
            "documents": {},
            "drafts": {},
            "roads": {},
            "engine": None,
            "events": [],
            "critic_runs": [],
            "created_at": now(),
        }
        return self.store.create(tenant, recompute(state))

    def operate(self, tenant, case_id, revision, key, action, payload, actor="backend"):
        op_id, state, replay = self.store.reserve(
            tenant, case_id, revision, key, {"action": action, "payload": payload}
        )
        if replay:
            return state
        try:
            state.pop("active_operation", None)
            state["events"].append(
                {"stage": action, "status": "started", "at": now(), "operation_id": op_id}
            )
            if action == "inputs":
                state["inputs"] = CaseCreate.model_validate(payload).model_dump(mode="json")
                self._refresh(state, force=True)
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
            recompute(state)
            state["events"].append(
                {"stage": action, "status": "completed", "at": now(), "operation_id": op_id}
            )
            return self.store.complete(tenant, op_id, state)
        except Exception:
            self.store.fail(tenant, case_id, op_id)
            raise

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
                    required = len(plan["research_packet"]["stages"]) + len(draft["tails"])
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
            self.ocr.extract(content, upload.media_type)
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
            "extraction": extraction,
            "suggested_fields": suggest_fields(extraction),
            "user_confirmed": False,
        }
