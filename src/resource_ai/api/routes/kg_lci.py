"""Read-only endpoint: live KG candidates → locally licensed ILCD estimates."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Annotated
from zipfile import BadZipFile

from fastapi import APIRouter, Depends, HTTPException

from resource_ai.agents.kg_lci import KGPathLCIIntegrityError, KGPathLCIService
from resource_ai.api.schemas.kg_lci import KGPathLCIRequest, KGPathLCIResponse
from resource_ai.calculators.lci import load_local_datasets
from resource_ai.core.dependencies import get_graph_query
from resource_ai.knowledge_graph.graph_store import GraphStoreError
from resource_ai.knowledge_graph.query import GraphQuery

router = APIRouter(prefix="/analysis", tags=["internal-analysis"])


@lru_cache(maxsize=1)
def get_lci_datasets():
    """Server-owned licensed ZIPs only; refresh by restarting the worker."""
    folder = Path(os.environ.get("RESOURCE_LCI_DIR", "data/local/lci"))
    try:
        return load_local_datasets(folder)
    except (BadZipFile, OSError, ValueError) as exc:
        # Do not reveal server-local absolute paths and raw XML in HTTP errors.
        raise HTTPException(
            status_code=503,
            detail={
                "code": "lci-source-invalid",
                "message": "Configured LCI sources are unreadable",
            },
        ) from exc


@router.post("/kg-lci", response_model=KGPathLCIResponse)
def kg_lci(
    request: KGPathLCIRequest,
    graph: Annotated[GraphQuery, Depends(get_graph_query)],
    datasets: Annotated[list, Depends(get_lci_datasets)],
):
    try:
        return KGPathLCIService(graph, datasets).run(request)
    except GraphStoreError as exc:
        status = {"invalid-goal": 400, "invalid-cursor": 400, "stale-cursor": 409}.get(
            exc.code, 503
        )
        raise HTTPException(
            status_code=status, detail={"code": exc.code, "message": str(exc)}
        ) from None
    except KGPathLCIIntegrityError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "kg-lci-integrity", "message": str(exc)},
        ) from None
