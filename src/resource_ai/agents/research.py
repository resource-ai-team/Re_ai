"""조회 한도를 지키며 KG 후보에 로컬 법률 근거를 연결한다.

LLM 호출, 법률 적용성 판정, 적합성 판정, 후보 순위 결정은 수행하지 않는다.
"""

from datetime import UTC, date, datetime
from urllib.parse import urlsplit
from uuid import uuid4

from resource_ai.api.schemas.analysis import (
    CandidateResearch,
    LegalSearch,
    ResearchRequest,
    ResearchResponse,
)
from resource_ai.knowledge_graph.graph_store import integrity
from resource_ai.knowledge_graph.ontology import digest, identity

LIMITATIONS = [
    "법령은 검색 후보입니다. 적용성·예외·원문 Auditor와 현행 허가·실제 인수 검증을 완료하지 않았습니다.",
    "KG·시설 공개 조건과 법령 근거를 연결한 조사 결과이며 최종 추천·발주·인수 승인이 아닙니다.",
    "조회 완료는 현재 KG 범위 안에서만 의미가 있습니다. 국내 전체 공정·시설을 포괄하지 않습니다.",
    "이 실행은 내부 KG와 로컬 RAG만 사용하며 외부 생성 모델에 문서·질문을 보내지 않습니다.",
    "검색 예산·자료 없음·검색 실패를 미확인으로 보존합니다. 법령이 없거나 적합하다는 뜻이 아닙니다.",
    "최종 Critic·자동 부분 재실행·최종 Top K·영구 실행 이력 저장은 후속 구현입니다.",
]


def legal_questions(material, stage):
    transition = stage["transition"]["label"]
    return [
        ("classification", f"{material} 폐기물 분류 사업장폐기물 지정폐기물"),
        ("process", f"{material} {transition} 재활용 유형 처리기준 재활용 기준 예외"),
        ("facility", f"폐기물처리업 허가 업종 영업 범위 수탁 확인 {material} {transition}"),
        ("handover", f"{material} 폐기물 위탁 수집 운반 인계 배출자 확인 책임"),
    ]


def retrieve(repository, query, top_k, *, technical=False):
    try:
        search = repository.search_technical if technical else repository.search
        hits = search(query, top_k)
        contexts = {}
        if not technical and hasattr(repository, "expand_legal_context"):
            try:
                contexts = repository.expand_legal_context(hits)
            except Exception:  # noqa: BLE001 - preserve retrieval, expose context failure separately.
                contexts = {}
        results = []
        for hit in hits:
            flags = []
            if technical and hit.document_type != "TECHNICAL_LITERATURE":
                raise ValueError("Unexpected technical search corpus")
            # 공식 출처 형식 확인만으로 해당 법령의 실제 적용성이 확정되지는 않는다.
            if not technical and urlsplit(hit.source_url).hostname not in {
                "www.law.go.kr",
                "law.go.kr",
            }:
                flags.append("official_origin_not_confirmed")
            if not technical and hit.document_type != "LAW":
                flags.append("not_a_statute")
            if not technical and hit.effective_from is None:
                flags.append("effective_date_unknown")
            elif not technical and hit.effective_from > date.today():
                flags.append("future_effective_date")
            results.append(
                {
                    "evidence_id": f"{'technical' if technical else 'legal'}-chunk:{hit.chunk_id}",
                    "title": hit.title,
                    "document_type": hit.document_type,
                    "heading": hit.heading,
                    "content": hit.content,
                    "citation": hit.citation,
                    "version": getattr(hit, "version", None),
                    "page_no": getattr(hit, "page_no", None),
                    "source_position": getattr(hit, "source_position", None),
                    "source_url": hit.source_url,
                    "effective_from": hit.effective_from.isoformat()
                    if hit.effective_from
                    else None,
                    "retrieval_method": hit.retrieval_method,
                    "reranker_score": getattr(hit, "reranker_score", None),
                    "reranker_model": getattr(hit, "reranker_model", None),
                    **(
                        {
                            "legal_context": contexts.get(
                                hit.chunk_id,
                                {
                                    "status": "unavailable",
                                    "primary": [],
                                    "linked": [],
                                    "source_checks": ["context_provider_unavailable"],
                                    "applicability": "not_audited",
                                },
                            )
                        }
                        if not technical
                        else {}
                    ),
                    "source_checks": flags,
                    "applicability": "not_audited",
                }
            )
        return "retrieved" if results else "no_hits", results
    except Exception:  # noqa: BLE001 - 검색 제공자 장애는 법률 판단이 아니라 명시적 공백이다.
        # DB·모델의 원본 예외에는 연결 문자열이나 사용자 내용이 포함될 수 있다.
        return "unavailable", []


class CandidateResearchService:
    def __init__(self, graph, repository, *, legal_graph_factory=None):
        self.graph = graph
        self.repository = repository
        self.legal_graph_factory = legal_graph_factory

    def _baseline_research(self, candidates):
        from resource_ai.rag.legal_baselines import catalog, reference_ids, unavailable_references

        if not candidates:
            return []
        for candidate in candidates:
            packet = candidate.plan.research_packet
            for index, stage in enumerate(packet["stages"]):
                for topic in ("classification", "process", "facility", "handover"):
                    on_leg = topic == "handover" and index > 0
                    candidate.legal_baseline_tasks.append(
                        {
                            "target_id": packet["handoffs"][index - 1]["leg_id"]
                            if on_leg
                            else stage["stage_id"],
                            "target_type": "handoff" if on_leg else "stage",
                            "topic": topic,
                            "reference_ids": reference_ids(topic),
                        }
                    )
        try:
            references = self.repository.legal_baselines()
            ids = [r["id"] for r in references]
            expected = {r["id"] for r in catalog()[0]["references"]}
            if len(ids) != len(expected) or set(ids) != expected:
                raise ValueError("Incomplete legal baseline response")
            if any(
                r.get("complete") and (r.get("status") != "retrieved" or not r.get("sources"))
                for r in references
            ):
                raise ValueError("Invalid legal baseline completeness")
        except Exception:  # noqa: BLE001 - missing provider/DB is a visible review gap, no raw exception exposure.
            references = unavailable_references()
        states = {r["id"]: r["complete"] for r in references}
        for candidate in candidates:
            if any(
                not states[ref]
                for task in candidate.legal_baseline_tasks
                for ref in task["reference_ids"]
            ):
                candidate.blockers.append("legal_baseline_evidence_incomplete")
        return references

    def _technical_research(self, request, candidates, material):
        if request.max_technical_queries == 0:
            return []
        queries = {}
        for candidate in candidates:
            for stage in (candidate.plan.research_packet or {}).get("stages", []):
                transition = stage["transition"]["label"]
                query = f"{material} {transition} 재활용 공정 투입 성상 전처리 오염 수율 적용 조건"
                sid = identity("technical-search", query)
                queries.setdefault(sid, query)
                candidate.technical_tasks.append(
                    {
                        "target_id": stage["stage_id"],
                        "target_type": "stage",
                        "search_id": sid,
                        "topic": "process_conditions",
                    }
                )
        searches, failed = [], False
        for index, (sid, query) in enumerate(queries.items()):
            attempted = index < request.max_technical_queries and not failed
            if index >= request.max_technical_queries:
                status, results = "budget_exhausted", []
            elif failed:
                status, results = "unavailable", []
            else:
                status, results = retrieve(
                    self.repository, query, request.technical_top_k, technical=True
                )
                failed = status == "unavailable"
            searches.append(
                LegalSearch(
                    search_id=sid, query=query, status=status, results=results, attempted=attempted
                )
            )
        states = {s.search_id: s.status for s in searches}
        for candidate in candidates:
            candidate.blockers.extend(
                f"technical_search_{states[task['search_id']]}"
                for task in candidate.technical_tasks
                if states[task["search_id"]] != "retrieved"
            )
        return searches

    def _graph_legal_reviews(self, request, candidates):
        from resource_ai.legal_graph.models import LegalGraphError
        from resource_ai.legal_graph.service import configured_service

        options = request.legal_graph
        remaining, service, failure = options.max_model_calls, None, None
        try:
            service = (self.legal_graph_factory or configured_service)().pin()
        except Exception as exc:  # noqa: BLE001 - keep configuration/DB/provider details private
            failure = exc.code if isinstance(exc, LegalGraphError) else "legal-graph-unavailable"
        for candidate in candidates:
            if not candidate.plan.external_model_allowed or candidate.plan.withheld_evidence_ids:
                candidate.legal_graph_review = {
                    "status": "unavailable",
                    "error_code": "evidence-transfer-not-allowed",
                    "route": "REVIEW",
                    "dispatch_approved": False,
                }
                candidate.blockers.append("legal_graph_evidence_transfer_not_allowed")
                continue
            try:
                if failure or remaining <= 0:
                    raise LegalGraphError(
                        failure or "model-budget",
                        "법률 그래프 검증이 미완료되어 추가 확인이 필요합니다.",
                    )
                # Proposed KG routes are not observations or evidence of a facility permit.
                facts = {
                    **options.facts,
                    "proposed_route": candidate.plan.research_packet,
                    "submitted_lot": request.lot.model_dump(mode="json", exclude_none=True),
                }
                result = service.run(
                    options.query,
                    facts,
                    top_k=options.top_k,
                    max_model_calls=remaining,
                    max_articles=options.max_articles,
                )
                remaining -= result["model_calls"]
                candidate.legal_graph_review = result
                if result["audit"]["coverage_complete"]:
                    candidate.blockers.remove("legal_applicability_not_audited")
                else:
                    candidate.blockers.append("legal_graph_coverage_incomplete")
            except Exception as exc:  # noqa: BLE001 - failure is a review gap, never approval
                code = exc.code if isinstance(exc, LegalGraphError) else "legal-graph-unavailable"
                failure = code
                candidate.legal_graph_review = {
                    "status": "unavailable",
                    "error_code": code,
                    "route": "REVIEW",
                    "dispatch_approved": False,
                }
                candidate.blockers.append("legal_graph_" + code)
        return options.max_model_calls - remaining

    def collect(self, request: ResearchRequest):
        """Collect one stable process-KG snapshot without retrieval or model calls."""
        cursor, trace, seen, candidates = None, None, set(), []
        seen_cursors = set()
        pages, fully_enumerated, expected_count = 0, True, None
        last = None
        for _ in range(request.max_pages):
            last = self.graph.assessed_plans(request.lot.model_copy(update={"cursor": cursor}))
            planning = last.planning
            pages += 1
            if trace is None:
                trace = planning.graph_trace
                expected_count = planning.pagination.enumerated_plans
            elif trace != planning.graph_trace:
                raise integrity(
                    "조회 중 그래프 버전이 바뀌었습니다. 분석을 처음부터 다시 실행해 주세요."
                )
            if expected_count != planning.pagination.enumerated_plans:
                raise integrity("조회 중 후보 수가 바뀌었습니다.")
            fully_enumerated &= planning.search_scope.get("all_paths_enumerated", False)
            assessments = {row["plan_id"]: row for row in last.assessments}
            if len(assessments) != len(last.assessments) or set(assessments) != {
                p.plan_id for p in planning.items
            }:
                raise integrity("후보와 조건 대조 목록이 일치하지 않습니다.")
            for plan in planning.items:
                if plan.plan_id in seen or plan.research_packet is None:
                    raise integrity("후보가 중복되었거나 내부 조사 묶음이 누락되었습니다.")
                seen.add(plan.plan_id)
                assessment = assessments[plan.plan_id]
                expected_stages = {s["stage_id"] for s in plan.research_packet["stages"]}
                actual_stages = [s["stage_id"] for s in assessment["stages"]]
                if (
                    len(actual_stages) != len(expected_stages)
                    or set(actual_stages) != expected_stages
                ):
                    raise integrity("공정 단계와 조건 대조 대상이 일치하지 않습니다.")
                candidates.append(
                    CandidateResearch(
                        plan=plan,
                        facility_assessment=assessment,
                        legal_tasks=[],
                        status="unmet" if assessment["status"] == "unmet" else "needs_verification",
                        blockers=[
                            "legal_applicability_not_audited",
                            "operational_conditions_unverified",
                        ]
                        + (["facility_condition_unmet"] if assessment["status"] == "unmet" else []),
                    )
                )
            cursor = planning.pagination.next_cursor
            if bool(cursor) != planning.pagination.has_more:
                raise integrity("후보 조회 커서와 has_more가 일치하지 않습니다.")
            if not cursor:
                if len(seen) != expected_count:
                    raise integrity("전체 후보 건수와 수집한 후보 수가 일치하지 않습니다.")
                break
            if cursor in seen_cursors or not planning.items:
                raise integrity("후보 페이지가 진행하지 않습니다.")
            seen_cursors.add(cursor)

        return {
            "candidates": candidates,
            "last": last,
            "trace": trace or {},
            "pages": pages,
            "fully_enumerated": fully_enumerated,
            "expected_count": expected_count,
            "cursor": cursor,
        }

    def run(self, request: ResearchRequest) -> ResearchResponse:
        collection = self.collect(request)
        candidates, last, trace = collection["candidates"], collection["last"], collection["trace"]
        pages, fully_enumerated = collection["pages"], collection["fully_enumerated"]
        expected_count, cursor = collection["expected_count"], collection["cursor"]
        process_sequences = {
            tuple(stage["transition"]["key"] for stage in candidate.plan.research_packet["stages"])
            for candidate in candidates
        }
        # Build tasks before executing any retrieval, so budgets cannot hide work.
        queries = {}
        for candidate in candidates:
            if request.legal_graph is not None:
                continue
            packet = candidate.plan.research_packet
            for index, stage in enumerate(packet["stages"]):
                for topic, query in legal_questions(last.planning.normalized_material, stage):
                    sid = identity("legal-search", query)
                    queries.setdefault(sid, query)
                    on_handoff = topic == "handover" and index > 0
                    candidate.legal_tasks.append(
                        {
                            "target_id": packet["handoffs"][index - 1]["leg_id"]
                            if on_handoff
                            else stage["stage_id"],
                            "target_type": "handoff" if on_handoff else "stage",
                            "topic": topic,
                            "search_id": sid,
                        }
                    )

        searches, executed, provider_failed = [], 0, False
        for index, (sid, query) in enumerate(queries.items()):
            attempted = index < request.max_legal_queries and not provider_failed
            if index >= request.max_legal_queries:
                status, results = "budget_exhausted", []
            elif provider_failed:
                status, results = "unavailable", []
            else:
                executed += 1
                status, results = retrieve(self.repository, query, request.legal_top_k)
                provider_failed = status == "unavailable"
            searches.append(
                LegalSearch(
                    search_id=sid, query=query, status=status, results=results, attempted=attempted
                )
            )
        search_states = {s.search_id: s for s in searches}
        graph_calls = (
            self._graph_legal_reviews(request, candidates) if request.legal_graph is not None else 0
        )
        technical_searches = self._technical_research(
            request, candidates, last.planning.normalized_material
        )
        baselines = self._baseline_research(candidates)
        from resource_ai.rag.legal_baselines import catalog

        for candidate in candidates:
            for task in candidate.legal_tasks:
                search = search_states[task["search_id"]]
                if search.status != "retrieved":
                    candidate.blockers.append(f"legal_search_{search.status}")
                if any(r["source_checks"] for r in search.results):
                    candidate.blockers.append("legal_source_review_required")
            candidate.blockers = sorted(set(candidate.blockers))

        return ResearchResponse(
            run_id=str(uuid4()),
            request_sha256=digest(request.model_dump(mode="json")),
            generated_at=datetime.now(UTC).isoformat(),
            input_context=request.lot.model_dump(mode="json", exclude={"cursor"}),
            graph_trace=trace or {},
            coverage={
                "material_scope": last.planning.search_scope.get("material_scope", {}),
                "graph_connection_gaps": last.planning.search_scope.get("coverage_diagnostics", {}),
                "planning_status": last.planning.status,
                "missing_fields": last.planning.missing_fields,
                "pages_read": pages,
                "enumerated_in_graph": expected_count,
                "collected_candidates": len(candidates),
                "distinct_curated_process_sequences": len(process_sequences),
                "multiple_curated_process_sequences": len(process_sequences) >= 2,
                "all_pages_read": cursor is None,
                "all_paths_enumerated_within_curated_scope": fully_enumerated,
                "complete_within_curated_scope": cursor is None and fully_enumerated,
                "next_cursor": cursor,
                "legal_unique_queries": len(searches),
                "legal_executed_queries": executed,
                "legal_provider_failed": provider_failed,
                "legal_retrieval_complete": bool(searches)
                and all(s.status == "retrieved" for s in searches),
                "legal_audit_complete": False,
                "legal_method": "hierarchical_graph" if request.legal_graph else "hybrid_retrieval",
                "legal_graph_model_calls": graph_calls,
                "legal_graph_reviews": sum(c.legal_graph_review is not None for c in candidates),
                "technical_requested": request.max_technical_queries > 0,
                "technical_unique_queries": len(technical_searches),
                "technical_executed_queries": sum(s.attempted for s in technical_searches),
                "technical_retrieval_complete": bool(technical_searches)
                and all(s.status == "retrieved" for s in technical_searches),
                "technical_applicability_audited": False,
                "legal_baseline_catalog_sha256": catalog()[1],
                "legal_baseline_jurisdiction": "KR",
                "legal_baseline_expected": len(baselines),
                "legal_baseline_retrieved": sum(r["complete"] for r in baselines),
                "legal_baseline_retrieval_complete": bool(baselines)
                and all(r["complete"] for r in baselines),
                "legal_baseline_applicability_audited": False,
            },
            candidates=candidates,
            legal_searches=searches,
            technical_searches=technical_searches,
            legal_baselines=baselines,
            next_actions=[
                *(
                    [
                        "현재 근거가 연결된 공정 순서가 하나뿐입니다. 대안 공정·시설 근거를 보강한 뒤 경로 간 비교를 진행하세요."
                    ]
                    if len(process_sequences) == 1
                    else []
                ),
                *(
                    ["누락된 입력을 확인하세요: " + ", ".join(last.planning.missing_fields)]
                    if last.planning.missing_fields
                    else []
                ),
                *(
                    [
                        "전체 후보 조회를 완료하세요. max_pages 한도 또는 경로 탐색 범위를 확인하세요."
                    ]
                    if cursor or not fully_enumerated
                    else []
                ),
                "검색 원문·시행일·적용 사실·예외를 Auditor에서 검증하세요.",
                "시설별 현행 허가·해당 로트 규격·인수·수탁 가능 물량·공정 간 인계를 확인하세요.",
                "동일 경계의 실제 비용·탄소 자료를 확보한 뒤 계산·비교하세요.",
            ],
            limitations=(
                LIMITATIONS
                if request.legal_graph is None
                else [
                    "선택한 법률 그래프의 Researcher → Auditor → Adjudicator로 후보별 법규를 조사합니다.",
                    "전체 후보가 하나의 모델 호출 한도를 공유합니다. 한도·버전·요건 부족은 확인 필요로 남깁니다.",
                    "공정 후보는 실행 가설이며 시설 허가·인수·최종 추천이 아닙니다.",
                    "최종 Critic·비용·탄소 비교·Top K와의 전체 연결은 후속 작업입니다.",
                ]
            ),
        )
