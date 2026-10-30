"""Additive KG contract for Re_server and the future orchestrator."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from resource_ai.api.schemas.numbers import NonBooleanFloat


class CandidateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    material: str | None = Field(default=None, min_length=1, max_length=160)
    input_form: str | None = Field(default=None, min_length=1, max_length=80)
    color: str | None = Field(default=None, min_length=1, max_length=80)
    contamination: str | None = Field(default=None, min_length=1, max_length=500)
    quantity_kg: NonBooleanFloat | None = Field(default=None, gt=0, le=1e12, allow_inf_nan=False)
    generation_process: str | None = Field(default=None, max_length=200)
    region: str | None = Field(default=None, max_length=200)
    priority: Literal["cost", "carbon", "management"] | None = None
    current_treatment: str | None = Field(default=None, max_length=200)
    top_k: int = Field(default=20, ge=1, le=200)


class CandidateResponse(BaseModel):
    status: Literal["needs_input", "unsupported_material", "candidates", "no_candidates"]
    backend: Literal["neo4j"] = "neo4j"
    normalized_material: str | None = None
    input_context: dict[str, Any]
    missing_fields: list[str]
    literature_candidates: list[dict[str, Any]] = Field(default_factory=list)
    process_candidates: list[dict[str, Any]] = Field(default_factory=list)
    scoped_claims: list[dict[str, Any]] = Field(default_factory=list)
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    graph_trace: dict[str, Any] = Field(default_factory=dict)
    limitations: list[str]
    dispatch_approved: Literal[False] = False
