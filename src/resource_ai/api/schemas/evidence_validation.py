"""KG reference와 RAG 근거를 연결하는 보수적 검증 계약."""

from typing import Any, Literal

from pydantic import Field, model_validator

from resource_ai.api.schemas.analysis import StrictModel
from resource_ai.api.schemas.kg_pool import CandidatePoolRequest

EvidenceStatus = Literal["SUPPORTED", "CONFLICT", "SCOPE_MISMATCH", "UNKNOWN"]
Route = Literal["PASS", "REVISE", "REVIEW"]


class ReferenceValidationRequest(StrictModel):
    query: CandidatePoolRequest
    max_pages: int = Field(default=20, ge=1, le=20)
    max_reference_queries: int = Field(default=24, ge=1, le=100)
    technical_top_k: int = Field(default=3, ge=1, le=5)
    max_critic_retries: int = Field(default=1, ge=0, le=2)

    @model_validator(mode="after")
    def start_with_internal_first_page(self):
        if self.query.cursor is not None:
            raise ValueError("근거 검증은 첫 페이지부터 시작합니다.")
        if self.query.audience != "internal":
            raise ValueError("근거 검증에는 internal KG 조사 묶음이 필요합니다.")
        return self


class RagEvidenceHit(StrictModel):
    chunk_id: int
    title: str
    source_url: str
    citation: str | None = None
    source_position: str | None = None
    retrieval_method: str


class ReferenceSearch(StrictModel):
    search_id: str
    kg_evidence_id: str
    query: str
    attempt: int = Field(ge=0, le=2)
    status: Literal["retrieved", "no_hits", "unavailable", "budget_exhausted"]
    results: list[RagEvidenceHit] = Field(default_factory=list)


class EvidenceAssessment(StrictModel):
    kg_evidence_id: str
    reference: str | None = None
    doi_candidates: list[str] = Field(default_factory=list)
    kg_source_url: str | None = None
    locator: str | None = None
    rag_chunk_ids: list[int] = Field(default_factory=list)
    rag_source_urls: list[str] = Field(default_factory=list)
    citations: list[str] = Field(default_factory=list)
    status: EvidenceStatus = "UNKNOWN"
    reasons: list[str] = Field(default_factory=list)
    search_ids: list[str] = Field(default_factory=list)


class CandidateEvidenceValidation(StrictModel):
    candidate_id: str
    candidate_kind: Literal["process", "scoped_claim", "literature"]
    evidence: list[EvidenceAssessment] = Field(default_factory=list)
    status: EvidenceStatus
    route: Route
    blockers: list[str] = Field(default_factory=list)


class ReferenceValidationResponse(StrictModel):
    contract_version: Literal["kg-rag-evidence-validation-v1"] = "kg-rag-evidence-validation-v1"
    graph_trace: dict[str, Any] = Field(default_factory=dict)
    coverage: dict[str, Any] = Field(default_factory=dict)
    candidates: list[CandidateEvidenceValidation] = Field(default_factory=list)
    searches: list[ReferenceSearch] = Field(default_factory=list)
    route: Route
    limitations: list[str] = Field(default_factory=list)
    dispatch_approved: Literal[False] = False
