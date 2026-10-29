from fastapi import APIRouter, HTTPException

from resource_ai.api.schemas.legal_graph import LegalGraphRequest
from resource_ai.core.config import get_settings
from resource_ai.knowledge_graph.graph_store import GraphStoreError
from resource_ai.legal_graph.models import LegalGraphError
from resource_ai.legal_graph.service import configured_service
from resource_ai.legal_graph.store import LegalGraphStore

router = APIRouter(prefix="/legal-graph", tags=["internal-legal-graph"])


def get_legal_service():
    return configured_service()


def graph_status():
    config = get_settings()
    return LegalGraphStore(namespace=config.legal_graph_namespace).status()


def failure(exc):
    status = 422 if exc.code == "invalid-input" else 503
    return HTTPException(status_code=status, detail={"code": exc.code, "message": str(exc)})


@router.get("/status")
def status():
    try:
        return graph_status()
    except (LegalGraphError, GraphStoreError) as exc:
        raise failure(exc) from exc


@router.post("/analyze")
def analyze(request: LegalGraphRequest):
    # Construct inside the handler so configuration failures receive a stable JSON error.
    try:
        return get_legal_service().run(**request.model_dump())
    except (LegalGraphError, GraphStoreError) as exc:
        raise failure(exc) from exc
