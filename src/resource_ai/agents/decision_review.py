"""Join real KG candidates to submitted arithmetic and expose review gaps.

This bounded stage does not grant PASS, rank legal feasibility, fetch prices,
or automatically rerun upstream stages. Code checks always run; optional semantic
review adds citation-checked concerns without establishing source truth.
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
    "Critic은 코드 검사와 선택 AI 반박의 인용 대조를 제공합니다. 모델 반박의 정확도·전문가 법규 판단·가격 조사는 별도 검증이 필요합니다.",
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
    def __init__(self, research_service, *, semantic_service=None):
        self.research_service = research_service
        self.semantic_service = semantic_service

    def set_semantic_service(self, service):
        self.semantic_service = service
        return self

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
            "site_context_sha256": request.site_context_sha256,
            "site_context_basis": "backend_supplied_digest_not_independently_verified",
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
            "site_context_sha256": request.site_context_sha256,
            "origin_bound": request.site_context_sha256 is not None,
            "site_context_basis": "backend_supplied_digest_not_independently_verified",
            "scope": "same_lot_graph_and_backend_site_context"
            if request.site_context_sha256
            else "same_lot_and_current_process_graph_origin_not_bound",
        }
        numeric = request.numeric.comparison if request.numeric else None
        raw_comparison = compare_scenarios(numeric) if numeric is not None else None
        issues = inspect_review(research, numeric, raw_comparison)
        if request.numeric is not None:
            binding["submission_matches"] = True
            if request.numeric.site_context_sha256 != request.site_context_sha256:
                binding["submission_matches"] = False
                issues.append(
                    issue(
                        "numeric_site_changed",
                        "kg",
                        "수치 양식을 만든 사업장 맥락과 현재 사업장 맥락이 다릅니다.",
                        "현재 site-discovery의 context_sha256으로 양식·운송·견적을 다시 확인하세요.",
                    )
                )
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
        response = DecisionReviewResponse(
            run_id=str(uuid4()),
            research=research,
            binding=binding,
            numeric_review=numeric_review,
            critic=issues,
            router=router,
            candidate_reviews=candidate_reviews,
            map_markers=markers,
            map_marker_groups=self._marker_groups(markers),
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
        return self.enrich_semantic(response, request.semantic_critic, numeric=numeric)

    def enrich_semantic(self, review, options, *, numeric=None, extra=None):
        if options is None:
            return review
        from resource_ai.agents.semantic_critic import get_semantic_critic_service

        issues = [i.model_dump(mode="json") for i in review.critic]
        service = self.semantic_service or get_semantic_critic_service()
        result = service.run(
            review.research, review.numeric_review, numeric, issues, options, extra=extra
        )
        for finding in result.findings:
            issues.append(
                {
                    "code": "semantic_" + finding["kind"],
                    "stage": finding["stage"],
                    "severity": "missing",
                    "candidate_id": finding["candidate_id"],
                    "target_type": finding["target_type"],
                    "target_id": finding["target_id"],
                    "evidence_ids": sorted({c["evidence_id"] for c in finding["citations"]}),
                    "message": "AI 추가 검토 제안: " + finding["message"],
                    "next_action": finding["next_action"],
                    "origin": "model_concern",
                    "semantic_finding_id": finding["finding_id"],
                }
            )
        if result.status != "completed":
            issues.append(
                {
                    "code": "semantic_critic_incomplete",
                    "stage": "validation",
                    "severity": "missing",
                    "candidate_id": None,
                    "target_type": "global",
                    "target_id": None,
                    "evidence_ids": [],
                    "message": f"요청한 AI 반박 검토가 완전하지 않습니다 ({result.status}).",
                    "next_action": "모델 설정·입력 한도·제외 자료·기각된 인용을 확인하세요.",
                }
            )
        router = route_issues(issues)
        markers = self._markers(review.research, issues)
        candidates = []
        for previous in review.candidate_reviews:
            scoped = [i for i in issues if i["candidate_id"] in {None, previous["candidate_id"]}]
            candidates.append(
                {
                    **previous,
                    "issue_codes": sorted({i["code"] for i in scoped}),
                    "next_actions": list(dict.fromkeys(i["next_action"] for i in scoped)),
                }
            )
        statuses = [
            {**s, "status": "code_checks_and_model_concerns_" + result.status}
            if s["stage"] == "critic"
            else {**s, "status": router["route"]}
            if s["stage"] == "router"
            else s
            for s in review.stage_statuses
        ]
        report = self._report(review.research, review.numeric_review, issues, router)
        report += (
            "\n\n## AI 반박 검토\n\n"
            f"- 실행 상태: `{result.status}` · 모델 호출 시도 {result.model_calls}회\n"
            f"- 인용 대조를 통과한 제안 {len(result.findings)}개. 의미적 정확도 검증·적법 판정이 아닙니다.\n"
            "- 모델 제안은 확인 사항으로 추가하며 기존 오류·계산 결과를 지우지 않습니다.\n"
        )
        return DecisionReviewResponse.model_validate(
            {
                **review.model_dump(mode="json"),
                "critic": issues,
                "semantic_critic": result,
                "candidate_reviews": candidates,
                "map_markers": markers,
                "map_marker_groups": self._marker_groups(markers),
                "router": router,
                "route": router["route"],
                "stage_statuses": statuses,
                "report_markdown": report,
                "limitations": [
                    *review.limitations,
                    "AI 반박은 인용 일치 검사를 거친 제안입니다. 의미적·법적 정확도는 별도 검증이 필요합니다.",
                ],
            }
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
    def _marker_groups(markers):
        grouped = {}
        for marker in markers:
            if not marker["on_map"]:
                continue
            key = (marker["candidate_id"], marker["target_type"], marker["target_id"])
            group = grouped.setdefault(
                key,
                {
                    "group_id": identity("map-review-group", list(key)),
                    "candidate_id": key[0],
                    "target_type": key[1],
                    "target_id": key[2],
                    "issue_count": 0,
                    "error_count": 0,
                    "confirmation_count": 0,
                    "marker_ids": [],
                    "issue_codes": [],
                    "evidence_ids": [],
                    "confirmed_operational_incident": False,
                },
            )
            group["issue_count"] += 1
            group["error_count"] += marker["severity"] == "error"
            group["confirmation_count"] += marker["severity"] != "error"
            group["marker_ids"].append(marker["marker_id"])
            group["issue_codes"].append(marker["code"])
            group["evidence_ids"].extend(marker["evidence_ids"])
        for group in grouped.values():
            group["issue_codes"] = sorted(set(group["issue_codes"]))
            group["evidence_ids"] = sorted(set(group["evidence_ids"]))
            group["severity"] = "error" if group["error_count"] else "needs_verification"
        return list(grouped.values())

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
                "origin": row.get("origin", "code"),
                "semantic_finding_id": row.get("semantic_finding_id"),
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
