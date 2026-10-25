"""RAG 근거 문서·검색 결과의 공통 모델."""

from resource_ai.evidence.models import (
    CorpusType,
    EvidenceDocument,
    EvidenceSection,
    SearchGroup,
    SearchResult,
    corpus_types_for_search_group,
)

__all__ = [
    "CorpusType",
    "EvidenceDocument",
    "EvidenceSection",
    "SearchGroup",
    "SearchResult",
    "corpus_types_for_search_group",
]
