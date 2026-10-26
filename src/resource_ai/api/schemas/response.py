from datetime import date

from pydantic import BaseModel


class SearchItem(BaseModel):
    chunk_id: int
    title: str
    document_type: str
    heading: str
    content: str
    citation: str | None
    source_url: str
    effective_from: date | None
    score: float
    retrieval_method: str
    version: str | None = None
    page_no: int | None = None
    source_position: str | None = None
    reranker_score: float | None = None
    reranker_model: str | None = None
    evidence_role: str = "retrieved"
    reference_sources: tuple[str, ...] = ()
