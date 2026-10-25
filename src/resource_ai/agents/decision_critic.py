"""Deterministic review of route binding and submitted arithmetic, never approval.

This critic reports known contradictions separately from missing evidence. It
cannot verify a permit, contractual intake, source contents, or a legal opinion.
"""

from decimal import Decimal, InvalidOperation, localcontext
from itertools import pairwise

from resource_ai.api.schemas.analysis import (
    ResearchResponse,
    ScenarioComparisonRequest,
    ScenarioComparisonResponse,
)


def _number(value):
    try:
        number = Decimal(str(value))
        return number if number.is_finite() else None
    except (InvalidOperation, TypeError, ValueError):
        return None


def inspect_review(
    research: ResearchResponse,
    numeric: ScenarioComparisonRequest | None,
    comparison: ScenarioComparisonResponse | None,
) -> list[dict]:
    """Inspect the entire researched pool, including unavailable and unmet paths."""
    findings, seen = [], set()

    def add(
        code,
        stage,
        severity,
        message,
        action,
        *,
        candidate=None,
        target=None,
        target_type="global",
        evidence=(),
    ):
        evidence = sorted({eid for eid in evidence if isinstance(eid, str) and eid})
        identity = code, stage, candidate, target, target_type, tuple(evidence)
        if identity in seen:
            return
        seen.add(identity)
        findings.append(
            {
                "code": code,
                "stage": stage,
                "severity": severity,
                "candidate_id": candidate,
                "target_id": target,
                "target_type": target_type,
                "evidence_ids": evidence,
                "message": message,
                "next_action": action,
            }
        )

    candidates = {}
    targets = {}
    statute_versions = {}
    for reference in research.legal_baselines:
        for hit in reference.get("sources", []):
            if hit.get("version"):
                statute_versions.setdefault(hit["title"], set()).add(hit["version"])
    for search in research.legal_searches:
        for hit in search.results:
            if hit.get("document_type") == "LAW" and hit.get("version"):
                statute_versions.setdefault(hit["title"], set()).add(hit["version"])
    if any(len(versions) > 1 for versions in statute_versions.values()):
        add(
            "legal_source_version_changed",
            "validation",
            "error",
            "같은 법령의 서로 다른 버전이 이번 조사에 섞였습니다.",
            "현재 법령 인덱스에서 기본 조문과 벡터 검색 근거를 같은 버전으로 다시 조회하세요.",
        )
    if not research.candidates:
        add(
            "kg_no_candidates",
            "kg",
            "missing",
            "현재 조사 결과에 공정 후보가 없습니다.",
            "입력 소재·형태와 등록된 공정 범위를 확인하세요.",
        )
    if research.coverage.get("complete_within_curated_scope") is not True:
        add(
            "kg_scope_incomplete",
            "kg",
            "missing",
            "등록된 범위의 전체 후보 조회를 확인하지 못했습니다.",
            "조회 한도·누락 입력·공정 연결 공백을 확인하고 전체 후보를 다시 조회하세요.",
        )
    declared_count = research.coverage.get("collected_candidates")
    if declared_count is not None and declared_count != len(research.candidates):
        add(
            "kg_candidate_count_mismatch",
            "kg",
            "error",
            "후보 수와 조사 범위의 기록이 다릅니다.",
            "같은 실행·그래프 세대에서 전체 후보를 다시 수집하세요.",
        )
    if research.coverage.get("all_pages_read") is True and (
        research.coverage.get("enumerated_in_graph") is not None
        and research.coverage["enumerated_in_graph"] != len(research.candidates)
    ):
        add(
            "kg_enumeration_count_mismatch",
            "kg",
            "error",
            "전체 조회 표시와 실제 후보 건수가 일치하지 않습니다.",
            "페이지·후보 중복·그래프 버전과 조회 완료 기록을 확인하세요.",
        )

    for candidate in research.candidates:
        pid = candidate.plan.plan_id
        if pid in candidates:
            add(
                "kg_duplicate_candidate",
                "kg",
                "error",
                "같은 공정 후보가 중복되어 있습니다.",
                "중복 원인을 수정한 뒤 전체 후보를 다시 조회하세요.",
                candidate=pid,
                target=pid,
                target_type="candidate",
            )
        candidates[pid] = candidate
        packet = candidate.plan.research_packet
        if not isinstance(packet, dict):
            add(
                "kg_packet_missing",
                "kg",
                "missing",
                "공정 후보의 단계·인계 근거 묶음이 없습니다.",
                "원본 KG 조사 묶음을 다시 조회하세요.",
                candidate=pid,
                target=pid,
                target_type="candidate",
            )
            continue
        stages, legs = packet.get("stages", []), packet.get("handoffs", [])
        if (
            not isinstance(stages, list)
            or not stages
            or not all(isinstance(s, dict) for s in stages)
        ):
            add(
                "kg_stage_records_invalid",
                "kg",
                "error",
                "공정 단계 목록을 확인할 수 없습니다.",
                "KG 후보의 단계 구조를 수정하고 다시 조회하세요.",
                candidate=pid,
                target=pid,
                target_type="candidate",
            )
            continue
        if not isinstance(legs, list) or not all(isinstance(leg, dict) for leg in legs):
            add(
                "kg_handoff_records_invalid",
                "kg",
                "error",
                "공정 인계 구간 목록을 확인할 수 없습니다.",
                "KG 후보의 인계 구조를 수정하고 다시 조회하세요.",
                candidate=pid,
                target=pid,
                target_type="candidate",
            )
            continue
        stage_ids = [stage.get("stage_id") for stage in stages]
        leg_ids = [leg.get("leg_id") for leg in legs]
        if any(not isinstance(sid, str) or not sid for sid in stage_ids + leg_ids) or len(
            set(stage_ids + leg_ids)
        ) != len(stage_ids + leg_ids):
            add(
                "kg_target_ids_invalid",
                "kg",
                "error",
                "공정·인계 ID가 없거나 중복되어 있습니다.",
                "같은 후보의 단계·인계 ID를 고유하게 연결하세요.",
                candidate=pid,
                target=pid,
                target_type="candidate",
            )
        targets[pid] = stage_ids, leg_ids
        if len(legs) != len(stages) - 1:
            add(
                "kg_handoff_count_mismatch",
                "kg",
                "error",
                "공정 순서에 필요한 인계 구간 수가 다릅니다.",
                "연속한 모든 공정 사이의 인계 구간을 확인하세요.",
                candidate=pid,
                target=pid,
                target_type="candidate",
            )
        for index, leg in enumerate(legs):
            if index + 1 >= len(stages) or (
                leg.get("from_stage_id") != stage_ids[index]
                or leg.get("to_stage_id") != stage_ids[index + 1]
            ):
                add(
                    "kg_handoff_sequence_mismatch",
                    "kg",
                    "error",
                    "인계 구간이 공정 순서와 다르게 연결되어 있습니다.",
                    "이전 공정의 산출과 다음 공정의 투입 연결을 수정하세요.",
                    candidate=pid,
                    target=leg.get("leg_id"),
                    target_type="handoff",
                )
        for before, after in pairwise(stages):
            output = (before.get("output_state") or {}).get("key")
            incoming = (after.get("input_state") or {}).get("key")
            if output is not None and incoming is not None and output != incoming:
                add(
                    "kg_state_sequence_mismatch",
                    "kg",
                    "error",
                    "앞 공정의 산출 상태와 다음 공정의 투입 상태가 다릅니다.",
                    "누락된 전처리·상태 전이를 확인하고 경로를 다시 생성하세요.",
                    candidate=pid,
                    target=after.get("stage_id"),
                    target_type="stage",
                )
        sources = {
            s.get("key"): s.get("evidence_id")
            for s in packet.get("evidence", [])
            if isinstance(s, dict)
        }
        assessments = candidate.facility_assessment.get("stages", [])
        assessed_ids = [row.get("stage_id") for row in assessments if isinstance(row, dict)]
        if len(assessed_ids) != len(stage_ids) or set(assessed_ids) != set(stage_ids):
            add(
                "facility_stage_binding_mismatch",
                "validation",
                "error",
                "시설 검토 대상과 공정 단계 ID가 일치하지 않습니다.",
                "같은 후보의 모든 공정 단계에 시설 검토 결과를 다시 연결하세요.",
                candidate=pid,
                target=pid,
                target_type="candidate",
            )
        for index, stage in enumerate(stages):
            sid = stage.get("stage_id")
            if stage.get("facility") is None or stage.get("capability") is None:
                add(
                    "facility_binding_missing",
                    "validation",
                    "missing",
                    "이 공정의 실제 시설·수행 공정 연결 근거가 부족합니다.",
                    "사업장별 공정 수행·투입 규격·현행 허가 근거를 확보하세요.",
                    candidate=pid,
                    target=sid,
                    target_type="stage",
                )
            row = next(
                (
                    row
                    for row in assessments
                    if isinstance(row, dict) and row.get("stage_id") == sid
                ),
                {},
            )
            assessment = row.get("assessment")
            if not isinstance(assessment, dict):
                add(
                    "facility_assessment_missing",
                    "validation",
                    "missing",
                    "시설 공개 조건 대조가 없습니다.",
                    "해당 단계의 시설 조건과 확인할 항목을 조사하세요.",
                    candidate=pid,
                    target=sid,
                    target_type="stage",
                )
                continue
            if assessment.get("all_operational_conditions_verified") is not True:
                add(
                    "facility_operations_unverified",
                    "validation",
                    "missing",
                    "현행 허가·이번 로트 인수·여유 용량은 검증되지 않았습니다.",
                    "관할 허가 자료와 해당 로트의 서면 인수 조건을 확인하세요.",
                    candidate=pid,
                    target=sid,
                    target_type="stage",
                )
            for check in assessment.get("checks", []):
                if not isinstance(check, dict) or check.get("status") == "matched":
                    continue
                status = check.get("status")
                on_leg = check.get("code") == "handoff_specifications" and 0 < index <= len(legs)
                target = leg_ids[index - 1] if on_leg else sid
                add(
                    "facility_" + str(check.get("code", "condition")),
                    "validation",
                    "error" if status == "unmet" else "missing",
                    f"{check.get('label', '시설 조건')}: {'공개 조건과 입력이 다릅니다.' if status == 'unmet' else '추가 확인이 필요합니다.'}",
                    check.get("next_action") or "시설의 해당 조건과 실제 로트 자료를 확인하세요.",
                    candidate=pid,
                    target=target,
                    target_type="handoff" if on_leg else "stage",
                    evidence=[sources[s] for s in check.get("source_ids", []) if sources.get(s)],
                )
        legal = candidate.legal_graph_review
        baseline_refs = {r["id"]: r for r in research.legal_baselines}
        for task in candidate.legal_baseline_tasks:
            refs = [baseline_refs.get(key) for key in task["reference_ids"]]
            complete = bool(refs) and all(r is not None and r.get("complete") is True for r in refs)
            corrupt = any(
                {"ambiguous_current_version", "source_locator_mismatch"}.intersection(
                    r.get("source_checks", [])
                )
                for r in refs
                if r is not None
            )
            baseline_code = (
                "legal_baseline_applicability_unverified"
                if complete
                else "legal_baseline_incomplete"
            )
            if corrupt:
                baseline_code = "legal_baseline_integrity_error"
            labels = ", ".join(f"{r['law_title']} {r['article']}" for r in refs if r is not None)
            add(
                baseline_code,
                "validation",
                "error" if corrupt else "missing",
                f"기본 확인 조문 {'확보·적용성 미검수' if complete else '확보 미완료'}: {labels or task['topic']}",
                "허가·신고·자체 처리 등 해당 경로의 적용 요건·예외·위임 규정·사실관계를 원문에서 검수하세요.",
                candidate=pid,
                target=task["target_id"],
                target_type=task["target_type"],
                evidence=[
                    s["evidence_id"] for r in refs if r is not None for s in r.get("sources", [])
                ],
            )
        technical = {search.search_id: search for search in research.technical_searches}
        for task in candidate.technical_tasks:
            search = technical.get(task["search_id"])
            retrieved = search is not None and search.status == "retrieved" and search.results
            add(
                "technical_applicability_unverified" if retrieved else "technical_evidence_missing",
                "validation",
                "missing",
                "기술 근거를 찾았으나 이 로트·공정에 대한 적용성 검수는 남아 있습니다."
                if retrieved
                else "이 공정의 기술 근거 조사가 미완료입니다.",
                "원문에서 투입 성상·오염 허용치·전처리·수율을 확인하세요. 검색 한도·장애·자료 없음도 확인하세요.",
                candidate=pid,
                target=task["target_id"],
                target_type="stage",
                evidence=[r["evidence_id"] for r in search.results] if retrieved else (),
            )
        if not legal or legal.get("audit", {}).get("coverage_complete") is not True:
            add(
                "legal_applicability_unverified",
                "validation",
                "missing",
                "원문·요건·예외의 적용성 검증 범위가 부족합니다.",
                "기준일의 법령·예외·적용 사실과 누락된 판례·해석을 확인하세요.",
                candidate=pid,
                target=pid,
                target_type="candidate",
            )
        # Even model-audited article applicability is not an operational permit or intake clearance.
        add(
            "legal_operational_clearance_unverified",
            "validation",
            "missing",
            "법규 조사 요약은 시설의 현행 허가·실제 인수 확인을 대신하지 않습니다.",
            "공정·품목·사업장의 허가 범위와 실제 수탁 조건을 별도로 확인하세요.",
            candidate=pid,
            target=pid,
            target_type="candidate",
        )

    quantity = _number(research.input_context.get("quantity_kg"))
    if quantity is None or quantity <= 0:
        add(
            "lot_quantity_missing",
            "calculation",
            "missing",
            "비교할 원래 폐자원 물량이 확인되지 않았습니다.",
            "사용자가 확인한 같은 로트의 kg 물량을 입력하세요.",
        )
    if numeric is None:
        for pid in candidates:
            add(
                "numeric_scenario_missing",
                "calculation",
                "missing",
                "이 후보의 비용·탄소 계산 자료가 없습니다.",
                "검증한 후보·단계·인계 ID에 실제 견적·활동량·배출계수를 연결하세요.",
                candidate=pid,
                target=pid,
                target_type="candidate",
            )
        return findings

    if quantity is not None and quantity != numeric.basis.quantity_kg:
        add(
            "lot_quantity_mismatch",
            "calculation",
            "error",
            "수치 비교 물량이 원래 폐자원 물량과 다릅니다.",
            "원래 로트와 같은 물량·기능단위로 모든 후보를 다시 계산하세요.",
        )
    if numeric.mode == "simulation":
        add(
            "simulation_not_operational_evidence",
            "calculation",
            "missing",
            "가상 수치 시연은 실제 견적·LCI 검증이 아닙니다.",
            "실제 자료로 교체하고 원문·적용 범위를 검수하세요.",
        )
    scenarios, bound = {}, set()
    for scenario in numeric.scenarios:
        pid = scenario.candidate_id
        if pid in scenarios:
            add(
                "numeric_duplicate_candidate",
                "calculation",
                "error",
                "한 후보의 수치 시나리오가 중복되어 있습니다.",
                "후보별로 하나의 동일 기준 시나리오를 연결하세요.",
                candidate=pid,
                target=pid,
                target_type="candidate",
            )
        scenarios[pid] = scenario
        if pid not in candidates:
            add(
                "numeric_unknown_candidate",
                "calculation",
                "error",
                "현재 KG 조사에 없는 후보의 수치가 들어왔습니다.",
                "같은 실행에서 조회한 공정 후보에만 수치를 연결하세요.",
                candidate=pid,
                target=pid,
                target_type="candidate",
            )
            continue
        expected = targets.get(pid)
        if expected is None:
            continue
        stage_ids, leg_ids = expected
        valid = True
        for name, submitted, actual in (
            ("stage", scenario.stage_ids, stage_ids),
            ("handoff", scenario.handoff_ids, leg_ids),
        ):
            if submitted != actual:
                valid = False
                add(
                    "numeric_" + name + "_binding_mismatch",
                    "calculation",
                    "error",
                    "제출한 공정·인계 ID 또는 순서가 KG 경로와 다릅니다.",
                    "임의 단계를 만들거나 빼지 말고 현재 후보의 원래 순서·ID에 수치를 연결하세요.",
                    candidate=pid,
                    target=pid,
                    target_type="candidate",
                )
        if valid:
            bound.add(pid)
        add(
            "numeric_source_content_unverified",
            "calculation",
            "missing",
            "가격·활동량·계수 원문의 진위와 해당 공정 적용성은 검수하지 않았습니다.",
            "견적·LCI 원문의 수치·단위·시점·공정·사업장 적용 범위를 검수하세요.",
            candidate=pid,
            target=pid,
            target_type="candidate",
            evidence=[
                sid
                for line in scenario.lines
                for sid in (line.activity_evidence_id, line.factor_evidence_id)
            ],
        )
    for pid in candidates.keys() - scenarios.keys():
        add(
            "numeric_scenario_missing",
            "calculation",
            "missing",
            "전체 후보 중 이 경로의 수치 자료가 빠졌습니다.",
            "이 후보의 계산 자료를 채우거나 계산을 보류한 이유를 기록하세요.",
            candidate=pid,
            target=pid,
            target_type="candidate",
        )
    if comparison is None:
        add(
            "comparison_result_missing",
            "comparison",
            "missing",
            "제출 수치의 계산·비교 결과가 없습니다.",
            "KG 경로와 수치 연결을 확인한 뒤 같은 기준으로 계산하세요.",
        )
        return findings

    if comparison.basis != numeric.basis or comparison.mode != numeric.mode:
        add(
            "comparison_request_mismatch",
            "comparison",
            "error",
            "계산 결과의 비교 기준·모드가 요청과 다릅니다.",
            "같은 요청·물량·시점·시스템 경계로 계산 결과를 다시 생성하세요.",
        )
    result_ids, points = [], {}
    for row in comparison.results:
        pid = row.get("candidate_id")
        result_ids.append(pid)
        scenario = scenarios.get(pid)
        if pid not in bound:
            add(
                "comparison_candidate_unbound",
                "comparison",
                "error",
                "계산 결과가 실제 KG 경로에 올바르게 연결되지 않았습니다.",
                "잘못된 후보·단계·인계 연결을 수정하고 비교를 다시 실행하세요.",
                candidate=pid,
                target=pid,
                target_type="candidate",
            )
        if scenario is None:
            continue
        if (
            row.get("declared_stage_ids") != scenario.stage_ids
            or row.get("declared_handoff_ids") != scenario.handoff_ids
        ):
            add(
                "comparison_targets_changed",
                "comparison",
                "error",
                "계산 결과의 공정·인계 대상이 제출 시나리오와 다릅니다.",
                "같은 시나리오를 다시 계산하고 다른 실행 결과가 섞이지 않았는지 확인하세요.",
                candidate=pid,
                target=pid,
                target_type="candidate",
            )
        for issue in row.get("issues", []):
            code = str(issue.get("code", "unknown_calculation_issue"))
            missing = code in {"component_missing", "evidence_missing"}
            target = issue.get("target_id") or pid
            target_type = (
                "stage"
                if target in scenario.stage_ids
                else "handoff"
                if target in scenario.handoff_ids
                else "candidate"
            )
            add(
                "calculation_" + code,
                "calculation",
                "missing" if missing else "error",
                "계산에 필요한 수치·근거가 부족합니다."
                if missing
                else "계산의 단위·범위·대상·근거 조건이 일치하지 않습니다.",
                "누락된 원문 수치·근거를 채우세요."
                if missing
                else "해당 계산 항목과 근거의 단위·기간·대상을 수정하세요.",
                candidate=pid,
                target=target,
                target_type=target_type,
                evidence=[issue["evidence_id"]] if issue.get("evidence_id") else [],
            )
        _inspect_arithmetic(row, scenario, add)
        cost, carbon = _number(row.get("cost_total_krw")), _number(row.get("carbon_total_kg_co2e"))
        if row.get("status") == "comparable_under_declared_inputs":
            if row.get("issues") or cost is None or carbon is None or carbon < 0:
                add(
                    "comparison_values_invalid",
                    "comparison",
                    "error",
                    "비교 가능 표시와 총계·계산 문제가 일치하지 않습니다.",
                    "오류 없는 비용·탄소 총계만 비교하도록 다시 계산하세요.",
                    candidate=pid,
                    target=pid,
                    target_type="candidate",
                )
            else:
                points[pid] = cost, carbon
    if len(set(result_ids)) != len(result_ids):
        add(
            "comparison_duplicate_candidate",
            "comparison",
            "error",
            "계산 결과에 같은 후보가 중복되어 있습니다.",
            "후보별 계산 결과를 한 번씩 생성하세요.",
        )
    if set(result_ids) != set(scenarios):
        add(
            "comparison_candidate_set_mismatch",
            "comparison",
            "error",
            "수치 요청과 계산 결과의 후보 목록이 다릅니다.",
            "계산을 보류한 후보도 이유와 함께 빠짐없이 반환하세요.",
        )
    _inspect_pareto(comparison.pareto_fronts, points, bound, add)
    return findings


def _inspect_arithmetic(row, scenario, add):
    """Check output products and subtotals independently of the calculation service."""
    pid = scenario.candidate_id
    lines = {line.line_id: line for line in scenario.lines}
    totals = {"cost": Decimal(0), "carbon": Decimal(0)}
    seen = set()
    bad = False
    for output in row.get("calculations", []):
        line_id = output.get("line_id")
        line = lines.get(line_id)
        if line is None or line_id in seen:
            bad = True
            continue
        seen.add(line_id)
        with localcontext() as context:
            context.prec = 80
            expected = line.activity * line.factor * (-1 if line.category == "revenue" else 1)
            totals[line.metric] += expected
        if (
            output.get("target_id") != line.target_id
            or output.get("metric") != line.metric
            or output.get("category") != line.category
            or output.get("activity_unit") != line.activity_unit
            or output.get("factor_unit") != line.factor_unit
            or _number(output.get("activity")) != line.activity
            or _number(output.get("factor")) != line.factor
            or _number(output.get("amount")) != expected
            or output.get("evidence_ids") != [line.activity_evidence_id, line.factor_evidence_id]
        ):
            bad = True
    for metric, field in (("cost", "cost_subtotal_krw"), ("carbon", "carbon_subtotal_kg_co2e")):
        if _number(row.get("known_" + field)) != totals[metric]:
            bad = True
        total = row.get("cost_total_krw" if metric == "cost" else "carbon_total_kg_co2e")
        if total is not None and _number(total) != totals[metric]:
            bad = True
    if row.get("status") == "comparable_under_declared_inputs" and seen != set(lines):
        bad = True
    if bad:
        add(
            "calculation_output_mismatch",
            "calculation",
            "error",
            "계산 항목의 곱셈·부호·근거·소계가 제출 수치와 일치하지 않습니다.",
            "다른 결과를 섞지 말고 원래 입력으로 계산을 다시 실행하세요.",
            candidate=pid,
            target=pid,
            target_type="candidate",
        )


def _inspect_pareto(fronts, points, bound, add):
    """Validate each nondominated layer by pairwise dominance, without calling the optimizer."""
    flattened = [pid for front in fronts for pid in front]
    if (
        len(set(flattened)) != len(flattened)
        or set(flattened) != set(points)
        or not set(flattened) <= bound
        or any(not front for front in fronts)
    ):
        add(
            "pareto_candidate_scope_invalid",
            "comparison",
            "error",
            "Pareto 후보에 중복·누락 또는 KG에 연결되지 않은 경로가 있습니다.",
            "올바른 KG 경로와 오류 없는 총계를 가진 전체 후보로 비교를 다시 실행하세요.",
        )
        return

    def dominates(left, right):
        return (
            left[0] <= right[0]
            and left[1] <= right[1]
            and (left[0] < right[0] or left[1] < right[1])
        )

    pending = set(points)
    for front in fronts:
        current = set(front)
        expected = {
            pid
            for pid in pending
            if not any(other != pid and dominates(points[other], points[pid]) for other in pending)
        }
        if current != expected:
            add(
                "pareto_dominance_mismatch",
                "comparison",
                "error",
                "비용·탄소의 지배 관계와 Pareto 층이 일치하지 않습니다.",
                "전체 후보의 비용·탄소 총계를 다시 확인하고 Pareto 층을 재계산하세요.",
            )
            return
        pending -= current
