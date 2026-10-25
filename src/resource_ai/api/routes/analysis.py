from secrets import compare_digest
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException

from resource_ai.agents.decision_review import DecisionReviewService
from resource_ai.agents.research import CandidateResearchService
from resource_ai.agents.site_discovery import SiteDiscoveryService
from resource_ai.api.routes.kg_lci import get_lci_datasets
from resource_ai.api.schemas.analysis import (
    ResearchRequest,
    ResearchResponse,
    ScenarioComparisonRequest,
    ScenarioComparisonResponse,
)
from resource_ai.api.schemas.decision_review import (
    DecisionReviewRequest,
    DecisionReviewResponse,
    ReviewTemplateRequest,
)
from resource_ai.api.schemas.road_routing import RoadRoutesRequest, RoadRoutesResponse
from resource_ai.api.schemas.site_discovery import SiteDiscoveryRequest, SiteDiscoveryResponse
from resource_ai.api.schemas.site_review import SiteReviewRequest, SiteReviewResponse
from resource_ai.calculators.scenarios import compare_scenarios
from resource_ai.core.dependencies import get_graph_query, get_repository
from resource_ai.evidence.models import SearchGroup
from resource_ai.integrations.road_routing import get_road_routing_service
from resource_ai.knowledge_graph.graph_store import GraphStoreError
from resource_ai.knowledge_graph.query import GraphQuery

router = APIRouter(prefix="/analysis", tags=["internal-analysis"])


def require_road_service_auth(service, authorization):
    # Disabled/unconfigured diagnostics do not trigger any external calls.
    if service.unavailable_status() is not None:
        return
    token = service.config.road_routing_service_token.get_secret_value()
    if not token:
        raise HTTPException(status_code=503, detail={"code": "routing-service-auth-not-configured"})
    expected = "Bearer " + token
    if not authorization or not compare_digest(authorization.encode(), expected.encode()):
        raise HTTPException(status_code=401, detail={"code": "routing-service-auth-required"})


@router.post("/road-routes", response_model=RoadRoutesResponse)
def road_routes(
    request: RoadRoutesRequest,
    service: Annotated[object, Depends(get_road_routing_service)],
    authorization: Annotated[str | None, Header()] = None,
):
    require_road_service_auth(service, authorization)
    return service.run(request)


@router.post("/site-discovery", response_model=SiteDiscoveryResponse)
def site_discovery(
    request: SiteDiscoveryRequest,
    graph: Annotated[GraphQuery, Depends(get_graph_query)],
    authorization: Annotated[str | None, Header()] = None,
):
    road_service = get_road_routing_service() if request.road_routing is not None else None
    if road_service is not None:
        require_road_service_auth(road_service, authorization)
    try:
        result = SiteDiscoveryService(graph).run(request)
        if road_service is not None:
            from resource_ai.agents.road_discovery import enrich_road_distances

            result = enrich_road_distances(result, request.road_routing, road_service)
        return result
    except GraphStoreError as exc:
        status = {"invalid-goal": 400, "invalid-transition": 400, "stale-cursor": 409}.get(
            exc.code, 503
        )
        raise HTTPException(
            status_code=status, detail={"code": exc.code, "message": str(exc)}
        ) from None


def get_research_service(graph: Annotated[GraphQuery, Depends(get_graph_query)]):
    # KG 후보가 없을 때 불필요하게 임베딩 모델을 올리지 않도록 검색 저장소를 지연 생성한다.
    # 초기화 실패는 질의별 검색 실패로 남기며, 근거를 찾지 못한 정상 결과로 바꾸지 않는다.
    class LocalRepository:
        def search(self, query, top_k):
            return get_repository().search(query, top_k, search_group=SearchGroup.LEGAL)

        def search_technical(self, query, top_k):
            return get_repository().search(query, top_k, search_group=SearchGroup.TECHNICAL)

    return CandidateResearchService(graph, LocalRepository())


def get_site_review_lci_datasets(request: SiteReviewRequest):
    return get_lci_datasets() if request.include_lci else []


@router.post("/site-review", response_model=SiteReviewResponse)
def site_review(
    request: SiteReviewRequest,
    graph: Annotated[GraphQuery, Depends(get_graph_query)],
    research_service: Annotated[CandidateResearchService, Depends(get_research_service)],
    datasets: Annotated[list, Depends(get_site_review_lci_datasets)],
    authorization: Annotated[str | None, Header()] = None,
):
    from resource_ai.agents.kg_lci import KGPathLCIIntegrityError
    from resource_ai.agents.site_review import SiteReviewService

    road_service = get_road_routing_service() if request.road_routing is not None else None
    if road_service is not None:
        require_road_service_auth(road_service, authorization)
    try:
        return SiteReviewService(graph, research_service, datasets, road_service=road_service).run(
            request
        )
    except (GraphStoreError, KGPathLCIIntegrityError) as exc:
        code = getattr(exc, "code", "site-review-generation-changed")
        status = {
            "invalid-goal": 400,
            "stale-cursor": 409,
            "site-review-generation-changed": 409,
        }.get(code, 503)
        raise HTTPException(
            status_code=status, detail={"code": code, "message": str(exc)}
        ) from None


@router.post("/research", response_model=ResearchResponse)
def research(
    request: ResearchRequest,
    service: Annotated[CandidateResearchService, Depends(get_research_service)],
):
    try:
        return service.run(request)
    except GraphStoreError as exc:
        status = {"invalid-goal": 400, "invalid-cursor": 400, "stale-cursor": 409}.get(
            exc.code, 503
        )
        raise HTTPException(
            status_code=status, detail={"code": exc.code, "message": str(exc)}
        ) from exc


@router.post("/compare-scenarios", response_model=ScenarioComparisonResponse)
def compare(request: ScenarioComparisonRequest):
    return compare_scenarios(request)


@router.post("/review-template")
def review_template(
    request: ReviewTemplateRequest,
    service: Annotated[CandidateResearchService, Depends(get_research_service)],
):
    try:
        return DecisionReviewService(service).template(request)
    except GraphStoreError as exc:
        raise HTTPException(
            status_code={"invalid-goal": 400, "stale-cursor": 409}.get(exc.code, 503),
            detail={"code": exc.code, "message": str(exc)},
        ) from exc


@router.post("/review", response_model=DecisionReviewResponse)
def review(
    request: DecisionReviewRequest,
    service: Annotated[CandidateResearchService, Depends(get_research_service)],
):
    try:
        return DecisionReviewService(service).run(request)
    except GraphStoreError as exc:
        raise HTTPException(
            status_code={"invalid-goal": 400, "stale-cursor": 409}.get(exc.code, 503),
            detail={"code": exc.code, "message": str(exc)},
        ) from exc
