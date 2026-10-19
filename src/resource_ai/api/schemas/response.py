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
