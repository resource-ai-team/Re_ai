from dataclasses import dataclass, field
from datetime import date
from typing import Any


@dataclass(frozen=True)
class LegalSection:
    heading: str
    content: str
    citation: str | None = None
    page_no: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LegalDocument:
    source_id: str
    document_type: str
    title: str
    source_url: str
    sections: list[LegalSection]
    source_org: str | None = None
    document_no: str | None = None
    effective_from: date | None = None
    version: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SearchResult:
    chunk_id: int
    title: str
    document_type: str
    heading: str
    content: str
    citation: str | None
    source_url: str
    effective_from: date | None
    score: float
    retrieval_method: str = "unknown"

