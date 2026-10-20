from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from resource_ai.api.schemas.graph import CandidateRequest, CandidateResponse
from resource_ai.core.dependencies import get_graph_query
from resource_ai.knowledge_graph.graph_store import GraphStoreError
from resource_ai.knowledge_graph.ontology import ontology
from resource_ai.knowledge_graph.query import GraphQuery

router = APIRouter(prefix="/kg", tags=["knowledge-graph"])


@router.get("/ontology")
def get_ontology() -> dict:
    return ontology()


@router.post("/candidates", response_model=CandidateResponse)
def candidates(request: CandidateRequest, query: Annotated[GraphQuery, Depends(get_graph_query)]):
    try:
        return query.search(request)
    except GraphStoreError as error:
        raise HTTPException(
            status_code=503, detail={"code": error.code, "message": str(error)}
        ) from None
