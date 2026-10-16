from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from resource_ai.api.schemas.response import SearchItem
from resource_ai.core.config import get_settings
from resource_ai.core.dependencies import get_repository
from resource_ai.rag.service import LegalRepository

router = APIRouter(prefix="/rag", tags=["rag"])


@router.get("/search", response_model=list[SearchItem])
def search(
    q: Annotated[str, Query(min_length=2)],
    repository: Annotated[LegalRepository, Depends(get_repository)],
    top_k: Annotated[int | None, Query(ge=1, le=20)] = None,
) -> list[SearchItem]:
    try:
        results = repository.search(q, top_k or get_settings().search_top_k)
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Retrieval service is unavailable") from exc
    return [
        SearchItem(
            chunk_id=item.chunk_id,
            title=item.title,
            document_type=item.document_type,
            heading=item.heading,
            content=item.content,
            citation=item.citation,
            source_url=item.source_url,
            effective_from=item.effective_from,
            score=item.score,
        )
        for item in results
    ]
