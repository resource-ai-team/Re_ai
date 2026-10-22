"""Join real KG candidates to submitted arithmetic and expose review gaps.

This bounded stage does not grant PASS, rank legal feasibility, fetch prices,
or automatically rerun upstream stages. Its critic is a deterministic integrity
check, not a model-based assessment of source truth.
"""

from collections import Counter
from uuid import uuid4

from resource_ai.api.schemas.analysis import ResearchRequest
from resource_ai.api.schemas.decision_review import DecisionReviewResponse
from resource_ai.calculators.scenarios import compare_scenarios
from resource_ai.knowledge_graph.ontology import digest, identity

STAGES = ["kg", "validation", "calculation", "comparison", "critic", "router", "output"]
LIMITATIONS = [
    "실제 KG 경로와 제출한 수치의 연결·산술·비교 범위를 확인합니다. 가격·LCI 원문의 진위와 적용성을 확정하지 않습니다.",
    "법률 검증의 모델 응답·시설 공개 조건은 현행 허가·로트 인수·처리 용량의 확인을 대신하지 않습니다.",
    "수치상 Pareto는 조사 결과입니다. 최종 Top K·추천·발주·운송 승인이 아니며 PASS를 반환하지 않습니다.",
    "Critic은 ID·범위·산술·비교 구조를 검사합니다. 의미적 반박·전문가 법규 판단·가격 조사는 별도 작업입니다.",
    "REVISE는 재검토할 단계와 무효화 범위를 제시합니다. 자동 부분 재실행·영구 실행 이력·3D 화면 연동은 아직 없습니다.",
]


def input_hash(lot):
    # IDs are shared by distinct lots: bind every business input separately.
    return digest(lot.model_dump(mode="json", exclude={"cursor", "page_size", "audience"}))


def components(plan):
    packet = plan.research_packet
    stages = [stage["stage_id"] for stage in packet["stages"]]
    handoffs = [leg["leg_id"] for leg in packet["handoffs"]]
    return [
        {"metric": metric, "category": category, "target_id": target}
        for metric in ("cost", "carbon")
        for category, targets in (
            ("process", stages),
            ("transport", [f"{plan.plan_id}:origin-transport", *handoffs]),
            ("residue", [plan.plan_id]),
            ("revenue", [plan.plan_id] if metric == "cost" else []),
        )
        for target in targets
    ]


def issue(code, stage, message, next_action, *, candidate_id=None):
    return {
        "code": code,
        "stage": stage,
        "severity": "error",
        "candidate_id": candidate_id,
        "target_id": candidate_id,
        "target_type": "candidate" if candidate_id else "global",
        "evidence_ids": [],
        "message": message,
        "next_action": next_action,
    }


def route_issues(issues):
    errors = [item for item in issues if item["severity"] == "error"]
    earliest = min((STAGES.index(item["stage"]) for item in errors), default=None)
    return {
        "route": "REVISE" if errors else "REVIEW",
        "rules_version": "decision-router-v1",
        "reason": "확인된 연결·계산 오류를 수정하세요."
        if errors
        else "핵심 근거의 확인·검수가 남아 있습니다.",
        "restart_from": STAGES[earliest] if earliest is not None else None,
        "invalidate_on_revision": STAGES[earliest:] if earliest is not None else [],
        "automatic_reexecution": False,
        "requires_human_data": any(i["severity"] == "missing" for i in issues),
        "pass_gate_enabled": False,
    }


class DecisionReviewService:
    def __init__(self, research_service):
        self.research_service = research_service

    def template(self, request):
        # collect() deliberately does not call RAG or any generation model.
        collected = self.research_service.collect(
            ResearchRequest(lot=request.lot, max_pages=request.max_pages)
        )
        rows = []
        for candidate in collected["candidates"]:
            plan = candidate.plan
            packet = plan.research_packet
            rows.append(
                {
                    "candidate_id": plan.plan_id,
                    "stage_ids": [s["stage_id"] for s in packet["stages"]],
                    "handoff_ids": [leg["leg_id"] for leg in packet["handoffs"]],
                    "stages": [
                        {
                            "stage_id": s["stage_id"],
                            "process": s["transition"]["label"],
                            "facility": (s.get("facility") or {}).get("label"),
                            "intake_verified": False,
                        }
                        for s in packet["stages"]
                    ],
                    "required_lines": [
                        {
                            **entry,
                            "line_id": identity("numeric-line", [plan.plan_id, entry]),
                            "activity": None,
                            "activity_unit": None,
                            "factor": None,
                            "factor_unit": None,
                            "activity_evidence_id": None,
                            "factor_evidence_id": None,
                        }
                        for entry in components(plan)
                    ],
                }
            )
        return {
            "contract_version": "grounded-review-template-v1",
            "input_sha256": input_hash(request.lot),
            "graph_trace": collected["trace"],
            "candidates": rows,
            "coverage": {
                "collected_candidates": len(rows),
                "enumerated_in_graph": collected["expected_count"],
                "complete_within_curated_scope": collected["cursor"] is None
                and collected["fully_enumerated"],
                "next_cursor": collected["cursor"],
                "missing_fields": collected["last"].planning.missing_fields,
            },
            "basis_template": {
                "quantity_kg": str(request.lot.quantity_kg)
                if request.lot.quantity_kg is not None
                else None,
                "region": request.lot.region,
                "as_of": None,
                "assessment_year": None,
                "carbon_method": None,
            },
            "instructions": [
                "원문에서 확인한 활동량·단가·배출계수와 출처를 입력하세요. 빈 값을 0으로 채우지 마세요.",
                "처음 운송·중간 인계·잔재물·매각 수익도 포함합니다. 0이라는 값에도 근거가 필요합니다.",
                "input_sha256·graph_trace를 수치와 함께 제출하세요. 로트·그래프가 바뀌면 재검토가 필요합니다.",
            ],
            "generation_model_called": False,
            "dispatch_approved": False,
        }

    def run(self, request):
        from resource_ai.agents.decision_critic import inspect_review

        research = self.research_service.run(request.research)
        binding = {
            "input_sha256": input_hash(request.research.lot),
            "graph_trace": research.graph_trace,
            "submission_matches": None,
            "scope": "same_lot_and_current_process_graph",
        }
        numeric = request.numeric.comparison if request.numeric else None
        raw_comparison = compare_scenarios(numeric) if numeric is not None else None
        issues = inspect_review(research, numeric, raw_comparison)
        if request.numeric is not None:
            binding["submission_matches"] = True
            if request.numeric.input_sha256 != binding["input_sha256"]:
                binding["submission_matches"] = False
                issues.append(
                    issue(
                        "numeric_input_changed",
                        "kg",
                        "수치 양식을 만든 입력과 현재 로트가 다릅니다.",
                        "현재 입력으로 양식을 다시 만들고 물량·성상·조건과 수치를 대조하세요.",
                    )
                )
            if request.numeric.graph_trace != research.graph_trace:
                binding["submission_matches"] = False
                issues.append(
                    issue(
                        "numeric_graph_changed",
                        "kg",
                        "수치 양식의 공정 그래프 버전이 현재 조회와 다릅니다.",
                        "최신 후보·단계·시설·인계 구간에 기존 수치를 다시 대조하세요.",
                    )
                )
            legal_options = request.research.legal_graph
            if (
                legal_options
                and legal_options.facts.get("as_of")
                and str(legal_options.facts["as_of"]) != numeric.basis.as_of.isoformat()
            ):
                binding["submission_matches"] = False
                issues.append(
                    issue(
                        "legal_numeric_date_mismatch",
                        "validation",
                        "법규와 수치 비교의 기준일이 다릅니다.",
                        "같은 기준일의 법규·견적·LCI 자료로 다시 검토하세요.",
                    )
                )
        matched_ids = self._grounded_numeric_ids(research, numeric, binding)
        comparable_ids = (
            {
                row["candidate_id"]
                for row in raw_comparison.results
                if row["status"] == "comparable_under_declared_inputs"
            }
            if raw_comparison
            else set()
        )
        valid_ids = {
            pid
            for pid in matched_ids & comparable_ids
            if not any(
                (i["severity"] == "error" and i["candidate_id"] == pid)
                or (
                    i["severity"] == "error"
                    and i["candidate_id"] is None
                    and i["stage"] != "comparison"
                )
                or i["code"] == "lot_quantity_missing"
                for i in issues
            )
        }
        compared = None
        if valid_ids:
            selected = [
                scenario for scenario in numeric.scenarios if scenario.candidate_id in valid_ids
            ]
            compared = compare_scenarios(numeric.model_copy(update={"scenarios": selected}))
            # Check the actual filtered comparison too; raw rejected candidates never enter it.
            known = {digest(row) for row in issues}
            for row in inspect_review(
                research, numeric.model_copy(update={"scenarios": selected}), compared
            ):
                if row["stage"] == "comparison" and digest(row) not in known:
                    issues.append(row)
                    known.add(digest(row))
            if any(
                i["severity"] == "error"
                and i["stage"] == "comparison"
                and i["code"]
                not in {"pareto_candidate_scope_invalid", "comparison_candidate_unbound"}
                for i in issues
            ):
                valid_ids, compared = set(), None
        raw_results = raw_comparison.results if raw_comparison else []
        numeric_review = {
            "submitted": numeric is not None,
            "mode": numeric.mode if numeric else None,
            "scope": "numeric_comparison_of_kg_bound_candidates_not_recommendation",
            "results": [
                {
                    **row,
                    "kg_binding": "matched"
                    if row["candidate_id"] in matched_ids
                    else "not_verified",
                    "included_in_numeric_comparison": row["candidate_id"] in valid_ids,
                }
                for row in raw_results
            ],
            "pareto_fronts": compared.pareto_fronts if compared else [],
            "included_candidate_ids": sorted(valid_ids),
            "excluded_submission_ids": sorted(
                {s.candidate_id for s in numeric.scenarios} - valid_ids
            )
            if numeric
            else [],
            "unsubmitted_candidate_ids": sorted(
                {c.plan.plan_id for c in research.candidates}
                - {s.candidate_id for s in numeric.scenarios}
            )
            if numeric
            else [c.plan.plan_id for c in research.candidates],
            "evidence_content_audited": False,
            "constraint_feasible_verified": False,
        }
        router = route_issues(issues)
        candidate_reviews = []
        for candidate in research.candidates:
            pid = candidate.plan.plan_id
            scoped = [i for i in issues if i["candidate_id"] in {None, pid}]
            candidate_reviews.append(
                {
                    "candidate_id": pid,
                    "status": "unmet" if candidate.status == "unmet" else "needs_verification",
                    "included_in_numeric_comparison": pid in valid_ids,
                    "issue_codes": sorted({i["code"] for i in scoped}),
                    "next_actions": list(dict.fromkeys(i["next_action"] for i in scoped)),
                    "recommended": False,
                }
            )
        markers = self._markers(research, issues)
        return DecisionReviewResponse(
            run_id=str(uuid4()),
            research=research,
            binding=binding,
            numeric_review=numeric_review,
            critic=issues,
            router=router,
            candidate_reviews=candidate_reviews,
            map_markers=markers,
            stage_statuses=[
                {
                    "stage": "kg",
                    "status": "collected"
                    if research.coverage["complete_within_curated_scope"]
                    else "incomplete",
                },
                {"stage": "validation", "status": "review_required"},
                {
                    "stage": "calculation",
                    "status": "numeric_inputs_checked" if raw_comparison else "missing_data",
                },
                {
                    "stage": "comparison",
                    "status": "numeric_only" if valid_ids else "no_kg_bound_comparison",
                },
                {"stage": "critic", "status": "deterministic_integrity_checks_completed"},
                {"stage": "router", "status": router["route"]},
                {"stage": "output", "status": "review_report_only"},
            ],
            report_markdown=self._report(research, numeric_review, issues, router),
            route=router["route"],
            limitations=LIMITATIONS,
        )

    @staticmethod
    def _grounded_numeric_ids(research, numeric, binding):
        if numeric is None or binding["submission_matches"] is not True:
            return set()
        plans = {c.plan.plan_id: c.plan for c in research.candidates}
        counts = Counter(s.candidate_id for s in numeric.scenarios)
        result = set()
        for scenario in numeric.scenarios:
            plan = plans.get(scenario.candidate_id)
            if plan is None or counts[scenario.candidate_id] != 1:
                continue
            packet = plan.research_packet
            if scenario.stage_ids != [
                s["stage_id"] for s in packet["stages"]
            ] or scenario.handoff_ids != [leg["leg_id"] for leg in packet["handoffs"]]:
                continue
            result.add(plan.plan_id)
        return result

    @staticmethod
    def _markers(research, issues):
        targets = {}
        for candidate in research.candidates:
            pid, packet = candidate.plan.plan_id, candidate.plan.research_packet
            targets[pid] = {
                pid,
                f"{pid}:origin-transport",
                *(s["stage_id"] for s in packet["stages"]),
                *(leg["leg_id"] for leg in packet["handoffs"]),
            }
        return [
            {
                "marker_id": identity("review-marker", [index, row]),
                **row,
                "on_map": row["candidate_id"] in targets
                and row["target_id"] in targets.get(row["candidate_id"], set()),
                "meaning": "수정할 입력·계산 오류"
                if row["severity"] == "error"
                else "확인할 조건·자료",
                "confirmed_operational_incident": False,
            }
            for index, row in enumerate(issues)
        ]

    @staticmethod
    def _report(research, numeric, issues, router):
        def cell(value):
            return str(value).replace("\r", " ").replace("\n", " ").replace("|", "\\|")

        lines = [
            "# 폐자원 경로 검토 보고서",
            "",
            f"판정: **{router['route']}** — {router['reason']}",
            "",
            f"조사 후보 {len(research.candidates)}개 · 수치 비교에 연결한 후보 {len(numeric['included_candidate_ids'])}개",
            "",
            "이 결과는 최종 추천·허가·시설 인수 승인이 아닙니다.",
            "",
        ]
        if numeric["submitted"]:
            lines.extend(
                [
                    "## 제출 수치 검토",
                    "",
                    "**가상 수치 시뮬레이션입니다. 실제 견적·LCI 검증이 아닙니다.**"
                    if numeric["mode"] == "simulation"
                    else "제출 수치의 원문 내용·공정 적용성은 별도 검수가 필요합니다.",
                    "",
                    "| 후보 ID | 수치 비교 | 순비용 (KRW) | 탄소 (kgCO2e) |",
                    "| --- | --- | --- | --- |",
                ]
            )
            for row in numeric["results"]:
                included = row["included_in_numeric_comparison"]
                status = "KG 연결·수치 조건 확인" if included else "비교 제외·확인 필요"
                cost = row["cost_total_krw"] if included else "보류"
                carbon = row["carbon_total_kg_co2e"] if included else "보류"
                lines.append(
                    f"| {cell(row['candidate_id'])} | {status} | {cell(cost)} | {cell(carbon)} |"
                )
            lines.extend(
                [
                    "",
                    "수치상 Pareto는 실행 가능성 검증이나 최종 순위가 아닙니다. 미제출 후보도 아래 확인 사항에 남깁니다.",
                    "",
                ]
            )
        lines.extend(["## 후보별 확인할 일", ""])
        for candidate in research.candidates:
            pid = candidate.plan.plan_id
            labels = " → ".join(
                s["transition"]["label"] for s in candidate.plan.research_packet["stages"]
            )
            lines.extend([f"### {labels}", "", f"후보 ID: `{pid}`", ""])
            relevant = [i for i in issues if i["candidate_id"] in {None, pid}]
            for text in dict.fromkeys(
                f"{i['message']} 다음 행동: {i['next_action']}" for i in relevant
            ):
                lines.append("- " + text)
            lines.append("")
        if not research.candidates:
            lines.append(
                "현재 입력과 등록 KG 범위에서 조회된 후보가 없습니다. 소재·형태·탐색 범위를 확인하세요."
            )
        if router["restart_from"]:
            lines.extend(
                [
                    "## 수정 후 다시 검토할 범위",
                    "",
                    " → ".join(router["invalidate_on_revision"]),
                    "",
                    "재실행은 자동 수행하지 않습니다. 오류 수정과 필요한 자료를 확보한 뒤 다시 요청하세요.",
                ]
            )
        return "\n".join(lines)
