"""Compose existing bounded stages without promoting screening values to totals."""

from resource_ai.agents.decision_review import DecisionReviewService
from resource_ai.agents.kg_lci import KGPathLCIIntegrityError, KGPathLCIService
from resource_ai.agents.road_discovery import enrich_road_distances
from resource_ai.agents.site_discovery import SiteDiscoveryService
from resource_ai.api.schemas.analysis import ResearchRequest
from resource_ai.api.schemas.decision_review import DecisionReviewRequest
from resource_ai.api.schemas.kg_lci import KGPathLCIRequest
from resource_ai.api.schemas.site_discovery import SiteDiscoveryRequest
from resource_ai.api.schemas.site_review import SiteReviewResponse
from resource_ai.knowledge_graph.graph_store import integrity


class SiteReviewService:
    def __init__(self, graph, research_service, datasets, *, road_service=None):
        self.graph, self.research_service, self.datasets = graph, research_service, datasets
        self.road_service = road_service

    def run(self, request):
        site_request = SiteDiscoveryRequest.model_validate(
            request.model_dump(include=set(SiteDiscoveryRequest.model_fields))
        )
        site = SiteDiscoveryService(self.graph).run(site_request)
        if request.road_routing is not None:
            if self.road_service is None:
                raise integrity("도로 조회 서비스 설정을 확인하세요.")
            site = enrich_road_distances(site, request.road_routing, self.road_service)
        review = DecisionReviewService(self.research_service).run(
            DecisionReviewRequest(
                research=ResearchRequest(
                    lot=request.lot,
                    max_pages=request.max_plan_pages,
                    max_legal_queries=request.max_legal_queries,
                    legal_top_k=request.legal_top_k,
                    max_technical_queries=request.max_technical_queries,
                    technical_top_k=request.technical_top_k,
                    legal_graph=request.legal_graph,
                ),
                numeric=request.numeric,
                site_context_sha256=site.context_sha256,
            )
        )
        if review.research.graph_trace != site.graph_trace:
            raise KGPathLCIIntegrityError("Graph changed between site investigation and review")
        lci = (
            KGPathLCIService(self.graph, self.datasets).run(
                KGPathLCIRequest(
                    lot=request.lot,
                    max_pages=request.max_plan_pages,
                )
            )
            if request.include_lci
            else None
        )
        if lci is not None and lci.graph_trace != site.graph_trace:
            raise KGPathLCIIntegrityError("Graph changed between review and LCI screening")
        estimated = sum(r["carbon_kgco2e"] is not None for r in lci.results) if lci else 0
        lci_scope = (
            f"국가 LCI 예비 산정 경로 {estimated}개. 같은 경계로 검수되지 않아 비용·탄소 총계나 순위에 자동 합산하지 않았습니다."
            if lci is not None
            else "국가 LCI 조사는 이번 요청에서 생략했습니다."
        )
        report = review.report_markdown + (
            "\n\n## 사업장·시설 및 국가 LCI 조사 범위\n\n"
            f"- 공정별 시설 조사 {len(site.facility_searches)}개. 명부 일치는 수행·인수 확인이 아닙니다.\n"
            f"- 같은 사업장 맥락 확인값: `{site.context_sha256}` (백엔드 제출 사업장 기준).\n"
            f"- {lci_scope}\n"
            "- 지도 키·견적·현행 허가·인수 가능성·물질수지는 별도 확인 대상입니다.\n"
        )
        return SiteReviewResponse(
            site=site, review=review, lci=lci, route=review.route, report_markdown=report
        )
