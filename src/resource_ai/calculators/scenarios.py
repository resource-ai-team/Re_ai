"""Decimal arithmetic and Pareto layers on explicitly submitted scenario inputs."""

from decimal import Decimal, localcontext

from resource_ai.api.schemas.analysis import ScenarioComparisonRequest, ScenarioComparisonResponse

LIMITATIONS = [
    "제출된 활동량·단가·배출계수의 산술·형식·범위 검사입니다. 원문 내용의 진위·적용성 검증이 아닙니다.",
    "공정 ID·운송 구간은 제출자가 선언한 범위입니다. 이 API는 KG의 실제 경로·시설을 검증하지 않습니다.",
    "단계별 물질수지·수율·운송 경로·중복 계상은 별도 검수가 필요합니다. 도로거리·견적·LCI 값을 생성하지 않습니다.",
    "경계는 투입 폐기물부터 산출물 출하 지점까지의 공정·운송·잔재물입니다. 회피 배출·버진 대체 공제는 포함하지 않습니다.",
    "순비용은 처리·운송·잔재물 비용에서 매각 수익을 뺀 값입니다. 가격·배출의 불확실성 구간은 아직 지원하지 않습니다.",
    "Pareto 집합은 비용과 탄소 중 어느 하나를 악화하지 않고 더 나아질 수 없는 수치상 후보들입니다. 단일 순위·최종 추천이 아닙니다.",
    "법규·허가·규격·실제 인수는 이 비교에서 판정하지 않습니다. 최종 Critic·Router 통과를 대신하지 않습니다.",
]


def pareto_layers(points):
    """Minimize both metrics; exact ties remain on the same nondominated front."""
    pending = dict(points)
    layers = []
    while pending:
        front = sorted(
            identifier
            for identifier, value in pending.items()
            if not any(
                other[0] <= value[0]
                and other[1] <= value[1]
                and (other[0] < value[0] or other[1] < value[1])
                for oid, other in pending.items()
                if oid != identifier
            )
        )
        layers.append(front)
        for identifier in front:
            del pending[identifier]
    return layers


def compare_scenarios(request: ScenarioComparisonRequest) -> ScenarioComparisonResponse:
    sources = {s.evidence_id: s for s in request.evidence}
    duplicate_sources = len(sources) != len(request.evidence)
    candidate_ids = [s.candidate_id for s in request.scenarios]
    duplicate_candidates = len(candidate_ids) != len(set(candidate_ids))
    results, points = [], {}
    for scenario in request.scenarios:
        issues = []

        def issue(code, _issues=issues, **fields):
            _issues.append({"code": code, **fields})

        if duplicate_sources:
            issue("duplicate_evidence_id")
        if duplicate_candidates:
            issue("duplicate_candidate_id")
        if scenario.basis != request.basis:
            issue("comparison_basis_mismatch")
        stages, legs = set(scenario.stage_ids), set(scenario.handoff_ids)
        if len(stages) != len(scenario.stage_ids) or len(legs) != len(scenario.handoff_ids):
            issue("duplicate_target_id")
        origin = f"{scenario.candidate_id}:origin-transport"
        if stages & legs or scenario.candidate_id in stages | legs or origin in stages | legs:
            issue("ambiguous_target_id")
        if len(legs) != len(stages) - 1:
            issue("handoff_count_mismatch")
        expected = {
            (metric, category, target)
            for metric in ("cost", "carbon")
            for category, targets in (
                ("process", stages),
                ("transport", legs | {origin}),
                ("residue", {scenario.candidate_id}),
                ("revenue", {scenario.candidate_id} if metric == "cost" else set()),
            )
            for target in targets
        }
        covered, line_ids, calculations = set(), set(), []
        totals = {"cost": Decimal(0), "carbon": Decimal(0)}
        for line in scenario.lines:
            before = len(issues)
            if line.line_id in line_ids:
                issue("duplicate_line_id", line_id=line.line_id, metric=line.metric)
            line_ids.add(line.line_id)
            target = (line.metric, line.category, line.target_id)
            if target not in expected:
                issue("unexpected_component_target", line_id=line.line_id, metric=line.metric)
            wanted_unit = f"{'KRW' if line.metric == 'cost' else 'kgCO2e'}/{line.activity_unit}"
            if line.factor_unit != wanted_unit:
                issue("factor_unit_mismatch", line_id=line.line_id, metric=line.metric)
            for sid, kind in (
                (line.activity_evidence_id, "activity_record"),
                (line.factor_evidence_id, f"{line.metric}_factor"),
            ):
                source = sources.get(sid)
                if source is None:
                    issue("evidence_missing", evidence_id=sid, metric=line.metric)
                    continue
                if source.kind != kind:
                    issue("evidence_kind_mismatch", evidence_id=sid, metric=line.metric)
                if kind == "carbon_factor" and source.carbon_method != request.basis.carbon_method:
                    issue("carbon_method_mismatch", evidence_id=sid, metric=line.metric)
                if not source.valid_from <= request.basis.as_of <= source.valid_until:
                    issue("evidence_outside_validity", evidence_id=sid, metric=line.metric)
                if (
                    source.region != request.basis.region
                    or source.assessment_year != request.basis.assessment_year
                ):
                    issue("evidence_scope_mismatch", evidence_id=sid, metric=line.metric)
                if source.synthetic and request.mode != "simulation":
                    issue(
                        "synthetic_evidence_requires_simulation",
                        evidence_id=sid,
                        metric=line.metric,
                    )
            if len(issues) != before:
                continue
            covered.add(target)
            with localcontext() as context:
                context.prec = 80
                amount = line.activity * line.factor
                if line.category == "revenue":
                    amount = -amount
                totals[line.metric] += amount
            calculations.append(
                {
                    "line_id": line.line_id,
                    "target_id": line.target_id,
                    "category": line.category,
                    "metric": line.metric,
                    "activity": str(line.activity),
                    "activity_unit": line.activity_unit,
                    "factor": str(line.factor),
                    "factor_unit": line.factor_unit,
                    "amount": str(amount),
                    "evidence_ids": [line.activity_evidence_id, line.factor_evidence_id],
                }
            )
        for metric, category, target in sorted(expected - covered):
            issue("component_missing", metric=metric, category=category, target_id=target)
        complete = {m: not any(i.get("metric", m) == m for i in issues) for m in totals}
        comparable = not issues
        if comparable:
            points[scenario.candidate_id] = (totals["cost"], totals["carbon"])
        results.append(
            {
                "candidate_id": scenario.candidate_id,
                "declared_basis": scenario.basis.model_dump(mode="json"),
                "declared_stage_ids": scenario.stage_ids,
                "declared_handoff_ids": scenario.handoff_ids,
                "status": "comparable_under_declared_inputs" if comparable else "needs_data",
                "cost_total_krw": str(totals["cost"]) if complete["cost"] else None,
                "carbon_total_kg_co2e": str(totals["carbon"]) if complete["carbon"] else None,
                "known_cost_subtotal_krw": str(totals["cost"]),
                "known_carbon_subtotal_kg_co2e": str(totals["carbon"]),
                "calculations": calculations,
                "issues": issues,
                "evidence_content_audited": False,
                "constraint_feasible_verified": False,
                "recommended": False,
            }
        )
    return ScenarioComparisonResponse(
        basis=request.basis,
        mode=request.mode,
        evidence=request.evidence,
        results=results,
        pareto_fronts=pareto_layers(points),
        incomparable_ids=[r["candidate_id"] for r in results if r["status"] == "needs_data"],
        limitations=LIMITATIONS,
    )
