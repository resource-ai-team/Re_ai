from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from typing import Any


class CorpusType(StrEnum):
    LAW = "LAW"
    PRECEDENT = "PRECEDENT"
    TECHNICAL_LITERATURE = "TECHNICAL_LITERATURE"
    HISTORICAL_CASE = "HISTORICAL_CASE"
    COMPANY_DOCUMENT = "COMPANY_DOCUMENT"


SUPPORTED_CORPUS_TYPES = frozenset(CorpusType)


class SearchGroup(StrEnum):
    LEGAL = "LEGAL"
    TECHNICAL = "TECHNICAL"
    COMPANY = "COMPANY"


SEARCH_GROUP_CORPUS_TYPES: dict[SearchGroup, tuple[CorpusType, ...]] = {
    SearchGroup.LEGAL: (CorpusType.LAW, CorpusType.PRECEDENT),
    SearchGroup.TECHNICAL: (CorpusType.TECHNICAL_LITERATURE,),
    SearchGroup.COMPANY: (CorpusType.COMPANY_DOCUMENT,),
}


def corpus_types_for_search_group(search_group: SearchGroup | str) -> tuple[str, ...]:
    """Resolve the public search group without changing stored document types."""
    group = SearchGroup(search_group)
    return tuple(corpus_type.value for corpus_type in SEARCH_GROUP_CORPUS_TYPES[group])


@dataclass(frozen=True)
class EvidenceSection:
    heading: str
    content: str
    citation: str | None = None
    page_no: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EvidenceDocument:
    source_id: str
    document_type: str
    title: str
    source_url: str
    sections: list[EvidenceSection]
    source_org: str | None = None
    document_no: str | None = None
    effective_from: date | None = None
    version: str | None = None
    tenant_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def corpus_type(self) -> str:
        """Expose the domain name while preserving the document_type JSON contract."""
        return self.document_type


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


# Backward-compatible names for the existing legal collector and API contracts.
LegalSection = EvidenceSection
LegalDocument = EvidenceDocument
