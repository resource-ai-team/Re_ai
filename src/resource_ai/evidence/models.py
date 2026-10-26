"""RAG 저장 문서 유형과 외부 검색 그룹의 공통 데이터 모델."""

from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from typing import Any


class CorpusType(StrEnum):
    """DB에 저장되는 세부 문서 유형."""

    LAW = "LAW"
    PRECEDENT = "PRECEDENT"
    TECHNICAL_LITERATURE = "TECHNICAL_LITERATURE"
    HISTORICAL_CASE = "HISTORICAL_CASE"
    COMPANY_DOCUMENT = "COMPANY_DOCUMENT"


SUPPORTED_CORPUS_TYPES = frozenset(CorpusType)


class SearchGroup(StrEnum):
    """API 사용자가 선택하는 상위 검색 범위."""

    LEGAL = "LEGAL"
    TECHNICAL = "TECHNICAL"
    COMPANY = "COMPANY"


SEARCH_GROUP_CORPUS_TYPES: dict[SearchGroup, tuple[CorpusType, ...]] = {
    SearchGroup.LEGAL: (CorpusType.LAW, CorpusType.PRECEDENT),
    SearchGroup.TECHNICAL: (CorpusType.TECHNICAL_LITERATURE,),
    SearchGroup.COMPANY: (CorpusType.COMPANY_DOCUMENT,),
}


def corpus_types_for_search_group(search_group: SearchGroup | str) -> tuple[str, ...]:
    """저장 형식을 바꾸지 않고 외부 검색 그룹을 세부 문서 유형으로 변환한다."""
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
        """기존 document_type JSON 계약을 유지하면서 코퍼스 이름을 제공한다."""
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
    version: str | None = None
    page_no: int | None = None
    source_position: str | None = None
    reranker_score: float | None = None
    reranker_model: str | None = None
    evidence_role: str = "retrieved"
    reference_sources: tuple[str, ...] = ()

    metadata: dict[str, Any] = field(default_factory=dict)


# 기존 법령 수집기와 API가 사용하던 이름을 유지해 이전 호출이 깨지지 않게 한다.
LegalSection = EvidenceSection
LegalDocument = EvidenceDocument
