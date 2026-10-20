"""Deterministic, open-world checks on audited Neo4j facility profiles."""

import base64
import binascii
import json

from resource_ai.api.schemas.facility_assessment import (
    AssessedPlansResponse,
    ConditionCheck,
    FacilityAssessmentResponse,
    FacilityConditionAssessment,
    LotFacts,
)
from resource_ai.api.schemas.kg_planning import PathPlanningRequest
from resource_ai.knowledge_graph import graph_store as store
from resource_ai.knowledge_graph import ontology as graph
from resource_ai.knowledge_graph.planning import ProcessPlanner, load_reference, normalize_form
from resource_ai.knowledge_graph.planning_reference import node_id
from resource_ai.knowledge_graph.pool import cursor_error, unknown
from resource_ai.knowledge_graph.query import resolve_material

LIMITATIONS = [
    "조건 대조는 공개 자료와 입력의 일치 여부입니다. 현행 허가·이번 로트 인수·법적 적합성 승인이 아닙니다.",
    "공개 소재·형태는 예시 목록입니다. 목록에 없다는 이유만으로 해당 시설 전체를 부적합 처리하지 않습니다.",
    "수분·오염·물량 등의 수치 한도가 공개되지 않아 기준을 만들거나 설계 용량과 로트 물량을 비교하지 않습니다.",
    "후속 단계의 상태는 KG가 예상한 상태이며 실측값이 아닙니다. 원래 로트의 형태·오염·수분을 그대로 복사하지 않습니다.",
    "경로를 탈락시키거나 최종 순위를 매기지 않습니다. 미충족·미확인 기록을 보존해 후속 검증으로 전달합니다.",
]
EXTRA_FIELDS = {"moisture_pct", "contamination_pct", "designated_waste"}
FOLLOW_UPS = {
    "moisture_pct": ("수분 기준", "시설의 수분 한도와 해당 단계 시료의 측정값 확보"),
    "contamination_pct": ("오염·순도 기준", "허용 오염물·함량과 성분 분석서 확보"),
    "color": ("색상·등급 조건", "검정색·첨가제·등급별 선별·투입 가능 여부 확인"),
    "quantity_kg": ("이번 물량·여유 용량", "이번 일정의 수탁 가능 물량을 시설에 확인"),
    "current_permit_scope": ("현행 허가 범위", "관할기관·허가증의 품목·재활용 유형·유효 상태 확인"),
    "current_acceptance": ("이번 로트 인수", "해당 로트의 규격·일정·서면 인수 조건 확인"),
    "handoff_specifications": ("공정 간 인계 규격", "앞 단계 산출 규격과 다음 단계 투입 규격 대조"),
}


def assess_capability(value, capability, lot, *, projected=False):
    """Pure evaluator. Missing and non-exclusive examples can never imply a rejection."""
    transition = next(t for t in value.transitions if t.key == capability.transition)
    state = next(s for s in value.states if s.key == transition.input_state)
    profile = capability.input_profile
    basis = (
        "projected_state_not_measured" if projected else "user_supplied_not_independently_verified"
    )
    checks = []

    def add(
        code, label, status, observed=None, expected=None, *, scope, sources=(), reason, action
    ):
        checks.append(
            ConditionCheck(
                code=code,
                label=label,
                status=status,
                observed=observed,
                expected=expected,
                scope=scope,
                source_ids=list(sources),
                reason=reason,
                next_action=action,
            )
        )

    material = None if unknown(lot.material) else resolve_material(lot.material)
    match = material == state.material
    add(
        "transition_material",
        "선택한 공정의 소재",
        "needs_verification" if projected or material is None else "matched" if match else "unmet",
        lot.material,
        state.material,
        scope="this_modelled_transition_only_not_facility_wide_exclusion",
        sources=transition.source_ids,
        reason="KG의 예상 소재이며 해당 단계 실측 확인 필요"
        if projected
        else "선택한 공정의 소재와 입력을 대조; 다른 공정 가능성은 별도",
        action="해당 단계 소재 확인"
        if projected or material is None
        else "다른 소재의 공정 후보도 조회"
        if not match
        else "순도·혼합 여부 확인",
    )
    site = profile.evidence_scope == "site_publication"
    add(
        "site_process",
        "사업장 공정 근거",
        "matched" if site else "needs_verification",
        capability.basis,
        "published_site_process",
        scope="publication_not_current_operation",
        sources=profile.source_ids,
        reason="사업장 공정 공개 근거" if site else "기업 전체의 공정 소개로 사업장 연결은 가설",
        action="현행 가동·인수 조건 확인" if site else "해당 사업장의 소재별 설비·공정 근거 확보",
    )
    add(
        "material_example",
        "공개 소재 예시",
        "matched"
        if not projected and site and material in profile.material_examples
        else "needs_verification",
        lot.material,
        profile.material_examples,
        scope="non_exhaustive_examples_not_exclusive_intake_list",
        sources=profile.source_ids,
        reason="예시 일치만 확인하며 누락은 거절 근거가 아님",
        action="해당 로트의 조성·수지 등급과 수탁 규격 확인",
    )
    form = normalize_form(lot.input_form)
    add(
        "form_example",
        "공개 투입 형태 예시",
        "matched"
        if not projected and site and form is not None and form in profile.form_examples
        else "needs_verification",
        lot.input_form,
        profile.form_examples,
        scope="non_exhaustive_examples_not_exclusive_intake_list",
        sources=profile.source_ids,
        reason="형태 예시의 일치만 확인; 다른 형태나 후속 투입은 별도 확인",
        action="반입·인계 형태와 전처리 필요 여부 확인",
    )
    exclusion = profile.excludes_designated_waste is True
    add(
        "designated_waste",
        "공개된 지정폐기물 제외 정책",
        "needs_verification"
        if projected or not exclusion or lot.designated_waste is None
        else "unmet"
        if lot.designated_waste
        else "matched",
        lot.designated_waste,
        False if exclusion else None,
        scope="published_company_policy_not_legal_classification",
        sources=profile.exclusion_source_ids,
        reason="해당 사업장의 공개 취급 제외 정책과 사용자 입력 대조; 폐기물 법적 분류는 미검증"
        if exclusion
        else "확인한 자료에 명시적 제외 정책이 없음; 허용으로 추정하지 않음",
        action="분류 근거와 현행 허가를 확인하고 대체 시설 조사"
        if exclusion and lot.designated_waste is True and not projected
        else "지정폐기물 여부의 분류 근거·시설 허가 확인",
    )
    for field, (label, action) in FOLLOW_UPS.items():
        add(
            field,
            label,
            "needs_verification",
            getattr(lot, field, None),
            None,
            scope="unpublished_limit_or_unverified_operational_fact",
            reason="검수된 수치 한도 또는 현행 운영 근거 미확보",
            action=action,
        )
    status = (
        "unmet"
        if any(c.status == "unmet" for c in checks)
        else "needs_verification"
        if any(c.status == "needs_verification" for c in checks)
        else "conditions_met"
    )
    risks = [
        {
            "risk_id": graph.identity("facility-check", [capability.key, c.code]),
            "check_code": c.code,
            "label": c.label,
            "reason": c.reason,
            "kind": "documented_mismatch" if c.status == "unmet" else "unverified_condition",
            "status": c.status,
            "facility_key": capability.facility,
            "transition_key": transition.key,
            "source_ids": c.source_ids,
            "next_action": c.next_action,
            "scope": c.scope,
            "input_basis": basis,
            "condition_mismatch_detected": c.status == "unmet",
            "actual_incident_confirmed": False,
        }
        for c in checks
        if c.status != "matched"
    ]
    return FacilityConditionAssessment(
        capability_key=capability.key,
        facility_key=capability.facility,
        transition_key=transition.key,
        status=status,
        input_basis=basis,
        input_context=lot,
        checks=checks,
        risks=risks,
        published_profile=profile.model_dump(mode="json"),
    )


class FacilityAssessor:
    def __init__(self, config=None):
        self.config = config

    def assess(self, request):
        config = self.config or store.settings()

        def lookup(tx):
            generation, manifest = store._head(tx, config)
            value, fingerprint = load_reference(tx, config, generation, manifest)
            cap = next((c for c in value.capabilities if c.key == request.capability_key), None)
            if cap is None:
                raise store.GraphStoreError(
                    "invalid-capability", "적재된 시설–공정 연결 key를 사용해 주세요."
                )
            assessment = assess_capability(value, cap, request.lot)
            ids = sorted({s for c in assessment.checks for s in c.source_ids})
            return FacilityAssessmentResponse(
                assessment=assessment,
                evidence=[
                    {"evidence_id": node_id("Evidence", s.key), **s.model_dump(mode="json")}
                    for s in value.sources
                    if s.key in ids
                ],
                graph_trace={
                    "generation": generation,
                    "reference_sha256": fingerprint,
                    "reference_version": value.version,
                },
                limitations=LIMITATIONS,
            )

        try:
            return store._read(config, lookup)
        except (ValueError, KeyError, TypeError):
            raise store.integrity() from None

    def plans(self, request):
        config = self.config
        values = request.model_dump()
        binding = graph.digest(request.model_dump(exclude={"cursor", "page_size"}))
        inner = None
        if request.cursor:
            try:
                token = json.loads(
                    base64.b64decode(
                        request.cursor + "=" * (-len(request.cursor) % 4),
                        altchars=b"-_",
                        validate=True,
                    )
                )
                if (
                    not isinstance(token, dict)
                    or set(token) != {"v", "request", "inner"}
                    or token["v"] != "assessed-plans-v1"
                    or token["request"] != binding
                    or not isinstance(token["inner"], str)
                    or not token["inner"]
                ):
                    raise ValueError()
                inner = token["inner"]
            except (ValueError, TypeError, binascii.Error, UnicodeError):
                raise cursor_error() from None
        values["cursor"] = inner
        planning = ProcessPlanner(config).search(
            PathPlanningRequest.model_validate(
                {k: v for k, v in values.items() if k not in EXTRA_FIELDS}
            )
        )
        if not planning.items:
            return AssessedPlansResponse(planning=planning, assessments=[], limitations=LIMITATIONS)
        config = config or store.settings()
        lot = LotFacts.model_validate(request.model_dump(include=set(LotFacts.model_fields)))

        def lookup(tx):
            generation, manifest = store._head(tx, config)
            if generation != planning.graph_trace["generation"]:
                raise cursor_error("stale-cursor")
            value, fingerprint = load_reference(tx, config, generation, manifest)
            if fingerprint != planning.graph_trace["reference_sha256"]:
                raise cursor_error("stale-cursor")
            capabilities = {c.key: c for c in value.capabilities}
            results = []
            for item in planning.items:
                stages = []
                for index, stage in enumerate(item.research_packet["stages"]):
                    # Projected state is never promoted into measured lot data.
                    facts = lot if index == 0 else LotFacts()
                    cap = stage.get("capability")
                    if cap is None:
                        stages.append(
                            {
                                "transition_key": stage["transition"]["key"],
                                "stage_id": stage["stage_id"],
                                "status": "needs_verification",
                                "reason": "시설 연결 근거 없음",
                                "assessment": None,
                            }
                        )
                        continue
                    assessment = assess_capability(
                        value, capabilities[cap["key"]], facts, projected=index > 0
                    )
                    for risk in assessment.risks:
                        incoming = item.research_packet["handoffs"][index - 1] if index else None
                        on_handoff = (
                            risk["check_code"] == "handoff_specifications" and incoming is not None
                        )
                        risk["target_id"] = incoming["leg_id"] if on_handoff else stage["stage_id"]
                        risk["target_type"] = "handoff" if on_handoff else "stage"
                        risk["risk_id"] = graph.identity(
                            "plan-check", [risk["target_id"], risk["check_code"]]
                        )
                    stages.append(
                        {
                            "transition_key": assessment.transition_key,
                            "stage_id": stage["stage_id"],
                            "status": assessment.status,
                            "assessment": assessment.model_dump(mode="json"),
                        }
                    )
                blocked = any(s["status"] == "unmet" for s in stages)
                results.append(
                    {
                        "plan_id": item.plan_id,
                        "status": "unmet" if blocked else "needs_verification",
                        "stages": stages,
                        "recommended": False,
                        "dispatch_approved": False,
                    }
                )
            return results

        try:
            assessments = store._read(config, lookup)
        except (ValueError, KeyError, TypeError):
            raise store.integrity() from None
        if planning.pagination.next_cursor:
            token = {
                "v": "assessed-plans-v1",
                "request": binding,
                "inner": planning.pagination.next_cursor,
            }
            planning.pagination.next_cursor = (
                base64.urlsafe_b64encode(graph.canonical(token).encode()).decode().rstrip("=")
            )
        planning.search_scope["cursor_endpoint"] = "/kg/assessed-plans"
        return AssessedPlansResponse(
            planning=planning, assessments=assessments, limitations=LIMITATIONS
        )
