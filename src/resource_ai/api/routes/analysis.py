from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from resource_ai.agents.decision_review import DecisionReviewService
from resource_ai.agents.research import CandidateResearchService
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
from resource_ai.calculators.scenarios import compare_scenarios
from resource_ai.core.dependencies import get_graph_query, get_repository
from resource_ai.knowledge_graph.graph_store import GraphStoreError
from resource_ai.knowledge_graph.query import GraphQuery

router = APIRouter(prefix="/analysis", tags=["internal-analysis"])


def get_research_service(graph: Annotated[GraphQuery, Depends(get_graph_query)]):
    # Lazy retrieval avoids loading embeddings for an empty KG result. Initialization
    # failures become explicit per-query gaps, not a fabricated successful search.
    class LocalRepository:
        def search(self, query, top_k):
            return get_repository().search(query, top_k, corpus_types=["LAW"])

    return CandidateResearchService(graph, LocalRepository())


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
