"""RAG 근거 검색 HTTP API."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from resource_ai.api.schemas.response import SearchItem
from resource_ai.core.config import get_settings
from resource_ai.core.dependencies import get_repository
from resource_ai.evidence.models import SearchGroup
from resource_ai.rag.service import LegalRepository

router = APIRouter(prefix="/rag", tags=["rag"])


@router.get("/search", response_model=list[SearchItem])
def search(
    q: Annotated[str, Query(min_length=2)],
    repository: Annotated[LegalRepository, Depends(get_repository)],
    top_k: Annotated[int | None, Query(ge=1, le=20)] = None,
    search_group: Annotated[SearchGroup | None, Query()] = None,
    tenant_id: Annotated[str | None, Query(min_length=1)] = None,
) -> list[SearchItem]:
    """선택한 검색 그룹의 근거를 조회하고 내부 결과를 API 응답으로 변환한다."""
    # 기업 문서는 공개 코퍼스와 달리 반드시 인증된 기업 범위로 제한해야 한다.
    if search_group == SearchGroup.COMPANY and not tenant_id:
        raise HTTPException(status_code=400, detail="tenant_id is required for COMPANY search")
    try:
        results = repository.search(
            q,
            top_k or get_settings().search_top_k,
            search_group=search_group,
            tenant_id=tenant_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        # DB·임베딩 장애를 단순히 검색 결과가 없는 상태로 숨기지 않는다.
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
            retrieval_method=item.retrieval_method,
        )
        for item in results
    ]
