"""Join current KG stages to distance-ordered facility investigation pages.

This deliberately keeps registry leads out of validated process paths. Distances
are origin-to-listing straight lines, not the legs of a proposed transport route.
"""

from resource_ai.agents.research import CandidateResearchService
from resource_ai.api.schemas.analysis import ResearchRequest
from resource_ai.api.schemas.facility_search import FacilitySearchRequest
from resource_ai.api.schemas.site_discovery import SiteDiscoveryResponse
from resource_ai.knowledge_graph.graph_store import integrity
from resource_ai.knowledge_graph.ontology import digest

LIMITATIONS = [
    "사업장·주소·수거 좌표는 서버가 전달한 입력입니다. 회원 권한·사업장 소유·좌표 검수는 Re_server가 확인해야 합니다.",
    "공정별 명부 조사 대상이며 실제 수행 공정·현행 허가·이번 로트 인수·여유 용량이 검증된 후보가 아닙니다.",
    "거리는 배출 사업장부터 각 명부 좌표까지의 직선거리입니다. 도로거리·실제 운송 구간·전체 경로·운임·탄소가 아닙니다.",
    "기존 KG 시설 연결과 명부 조사 대상을 구분합니다. 명부를 원래 공정 경로에 자동 삽입하지 않습니다.",
    "소재군·설비 단어·미해석 코드 단서도 보존하므로 가까운 시설이 소재 등급·폐기물 분류에 적합하다는 뜻이 아닙니다.",
    "법령 검색·Auditor·생성 모델·유료 지도 API·가격 조사·최종 추천은 이번 호출에서 실행하지 않습니다.",
    "이 조회 해시는 조사 맥락 추적용입니다. 기존 수치 검토 API에 사업장/운송 가격 바인딩을 추가한 것은 아닙니다.",
]


class SiteDiscoveryService:
    def __init__(self, graph):
        self.graph = graph

    def run(self, request):
        collection = CandidateResearchService(self.graph, None).collect(
            ResearchRequest(lot=request.lot, max_pages=request.max_plan_pages)
        )
        if collection["candidates"] and not all(
            isinstance(collection["trace"].get(name), str) and collection["trace"][name]
            for name in ("generation", "reference_sha256")
        ):
            raise integrity("공정 경로의 세대·참조 자료 해시가 누락됐습니다.")
        stages = {}
        for candidate in collection["candidates"]:
            for stage in candidate.plan.research_packet["stages"]:
                key = stage["transition"]["key"]
                stages.setdefault(key, []).append(
                    {"candidate_id": candidate.plan.plan_id, "stage_id": stage["stage_id"]}
                )
        searches, shared_trace = [], None
        for key, refs in stages.items():
            items, seen, cursors = [], set(), set()
            cursor, first, count, pages = None, None, None, 0
            for _ in range(request.max_facility_pages):
                page = self.graph.facility_candidates(
                    FacilitySearchRequest(
                        transition_key=key,
                        origin_latitude=request.origin.latitude,
                        origin_longitude=request.origin.longitude,
                        sort_by="straight_line_distance",
                        page_size=request.facility_page_size,
                        cursor=cursor,
                        audience="internal",
                    )
                )
                trace = page.graph_trace
                if trace.get("generation") != collection["trace"].get("generation") or trace.get(
                    "reference_sha256"
                ) != collection["trace"].get("reference_sha256"):
                    raise integrity("공정 경로와 시설 조사의 KG 세대가 다릅니다. 다시 조회하세요.")
                pinned = {
                    name: trace.get(name)
                    for name in (
                        "generation",
                        "reference_sha256",
                        "registry_generation",
                        "registry_source_sha256",
                        "codebook_sha256",
                    )
                }
                if shared_trace is None:
                    shared_trace = pinned
                elif shared_trace != pinned:
                    raise integrity(
                        "조회 중 시설 명부·코드표 세대가 바뀌었습니다. 다시 조회하세요."
                    )
                total = page.pagination["total_registry_candidates"]
                ids = [row["listing_id"] for row in page.registry_candidates]
                if first is None:
                    first, count = page, total
                if (
                    page.transition_key != key
                    or not isinstance(total, int)
                    or isinstance(total, bool)
                    or total < 0
                    or total != count
                    or len(ids) != page.pagination["returned_count"]
                    or len(ids) != len(set(ids))
                    or seen.intersection(ids)
                ):
                    raise integrity("시설 조사 공정·건수·페이지 중복이 일치하지 않습니다.")
                seen.update(ids)
                items.extend(page.registry_candidates)
                pages += 1
                cursor = page.pagination["next_cursor"]
                if bool(cursor) != page.pagination["has_more"]:
                    raise integrity("시설 조사 커서와 has_more가 일치하지 않습니다.")
                if not cursor:
                    if len(items) != count:
                        raise integrity("시설 조사 전체 건수와 수집 목록이 일치하지 않습니다.")
                    break
                if cursor in cursors or not ids:
                    raise integrity("시설 조사 페이지가 진행하지 않습니다.")
                cursors.add(cursor)
            searches.append(
                {
                    "transition_key": key,
                    "candidate_stage_refs": refs,
                    "status": first.status,
                    "reference_bindings": first.reference_bindings,
                    "registry_candidates": items,
                    "graph_trace": first.graph_trace,
                    "search_scope": first.search_scope,
                    "coverage": {
                        "total_registry_candidates": count,
                        "collected_registry_candidates": len(items),
                        "pages_read": pages,
                        "complete_within_matching_registry": cursor is None,
                        "next_cursor": cursor,
                        "registry_imported": first.search_scope["registry_imported"],
                    },
                    "distance_scope": "origin_to_each_listing_straight_line_not_transport_legs",
                    "automatic_path_insertion": False,
                    "road_distance_km": None,
                    "transport_cost_krw": None,
                }
            )
        return SiteDiscoveryResponse(
            origin=request.origin,
            context_sha256=digest(
                {
                    "origin": request.origin.model_dump(mode="json"),
                    "lot": request.lot.model_dump(mode="json", exclude={"cursor", "page_size"}),
                    "trace": shared_trace or collection["trace"],
                }
            ),
            graph_trace=collection["trace"],
            coverage={
                "planning_status": collection["last"].planning.status,
                "collected_plans": len(collection["candidates"]),
                "enumerated_plans": collection["expected_count"],
                "all_plan_pages_read": collection["cursor"] is None,
                "all_paths_enumerated": collection["fully_enumerated"],
                "next_plan_cursor": collection["cursor"],
                "missing_lot_fields": collection["last"].planning.missing_fields,
                "unique_transitions_searched": len(searches),
                "all_facility_pages_read": bool(searches)
                and all(row["coverage"]["complete_within_matching_registry"] for row in searches),
                "nationwide_facility_coverage_verified": False,
            },
            candidates=collection["candidates"],
            facility_searches=searches,
            limitations=LIMITATIONS,
        )
