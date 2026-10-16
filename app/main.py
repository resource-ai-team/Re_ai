from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel

from app.config import get_settings
from app.dependencies import get_repository
from app.repository import LegalRepository

app = FastAPI(title="Re:Source Legal RAG", version="0.1.0")


class SearchItem(BaseModel):
    chunk_id: int
    title: str
    document_type: str
    heading: str
    content: str
    citation: str | None
    source_url: str
    effective_from: str | None
    score: float


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/search", response_model=list[SearchItem])
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
            effective_from=item.effective_from.isoformat() if item.effective_from else None,
            score=item.score,
        )
        for item in results
    ]

