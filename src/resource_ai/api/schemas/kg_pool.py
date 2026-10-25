"""Additive, unscreened KG pool contract; never a suitability decision."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from resource_ai.api.schemas.numbers import NonBooleanFloat


class CandidatePoolRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    material: str | None = Field(default=None, min_length=1, max_length=160)
    input_form: str | None = Field(default=None, min_length=1, max_length=80)
    color: str | None = Field(default=None, min_length=1, max_length=80)
    contamination: str | None = Field(default=None, min_length=1, max_length=500)
    quantity_kg: NonBooleanFloat | None = Field(default=None, gt=0, le=1e12, allow_inf_nan=False)
    generation_process: str | None = Field(default=None, max_length=200)
    region: str | None = Field(default=None, max_length=200)
    current_treatment: str | None = Field(default=None, max_length=200)
    page_size: int = Field(default=25, ge=1, le=100)
    cursor: str | None = Field(default=None, min_length=1, max_length=2048)
    audience: Literal["internal", "external_model"] = "internal"


class PoolCandidate(BaseModel):
    candidate_id: str
    kind: Literal["process", "scoped_claim", "literature"]
    review_state: Literal["unassessed"] = "unassessed"
    # None means the content was withheld, not that the candidate was excluded.
    research_packet: dict[str, Any] | None
    external_model_allowed: bool
    withheld_evidence_ids: list[str] = Field(default_factory=list)
    dispatch_approved: Literal[False] = False


class PoolPagination(BaseModel):
    total_candidates: int = 0
    totals_by_kind: dict[str, int] = Field(default_factory=dict)
    returned_count: int = 0
    has_more: bool = False
    next_cursor: str | None = None
    # A single page is complete only when the pool fits into that first page.
    complete_in_this_response: bool = True


class CandidatePoolResponse(BaseModel):
    contract_version: Literal["kg-research-pool-v1"] = "kg-research-pool-v1"
    status: Literal["needs_input", "unsupported_material", "candidates", "no_candidates"]
    backend: Literal["neo4j"] = "neo4j"
    normalized_material: str | None = None
    input_context: dict[str, Any]
    missing_fields: list[str]
    items: list[PoolCandidate] = Field(default_factory=list)
    pagination: PoolPagination = Field(default_factory=PoolPagination)
    graph_trace: dict[str, Any] = Field(default_factory=dict)
    limitations: list[str]
    dispatch_approved: Literal[False] = False
