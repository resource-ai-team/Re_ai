"""Bounded semantic-review contracts; a model cannot return approvals or routing."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class SemanticCriticOptions(Strict):
    max_findings: StrictInt = Field(default=12, ge=1, le=20)


class ModelCitation(Strict):
    ref_id: str = Field(pattern=r"^E\d{4}$")
    quote: str = Field(min_length=12, max_length=800)


class ModelConcern(Strict):
    stage: Literal["kg", "validation", "calculation", "comparison"]
    kind: Literal["potential_error", "missing_evidence"]
    candidate_id: str | None = Field(max_length=200)
    target_type: Literal["candidate", "stage", "handoff", "global"]
    target_id: str | None = Field(max_length=200)
    message: str = Field(min_length=1, max_length=1200)
    next_action: str = Field(min_length=1, max_length=600)
    citations: list[ModelCitation] = Field(min_length=1, max_length=4)


class SemanticCriticOutput(Strict):
    concerns: list[ModelConcern] = Field(max_length=20)


class SemanticCriticRun(Strict):
    contract_version: Literal["semantic-critic-v1"] = "semantic-critic-v1"
    status: Literal[
        "not_requested",
        "disabled",
        "external_transfer_disabled",
        "not_configured",
        "no_candidates",
        "context_budget_exceeded",
        "evidence_conflict",
        "unavailable",
        "invalid_output",
        "partial",
        "completed",
    ] = "not_requested"
    model: Literal["gpt-6-astra"] = "gpt-6-astra"
    reasoning_effort: Literal["high"] = "high"
    model_calls: int = Field(default=0, ge=0, le=1)
    model_identity_verified: bool = False
    input_sha256: str | None = None
    coverage: dict[str, Any] = Field(default_factory=dict)
    findings: list[dict[str, Any]] = Field(default_factory=list)
    rejected_findings: list[dict[str, Any]] = Field(default_factory=list)
    usage: dict[str, int] = Field(default_factory=dict)
    # Citation agreement does not establish the correctness of the inference.
    semantic_correctness_verified: Literal[False] = False
    dispatch_approved: Literal[False] = False
