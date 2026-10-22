"""Source-backed path hypotheses, before suitability validation and ranking."""

from typing import Any, Literal

from pydantic import BaseModel, Field

from resource_ai.api.schemas.kg_pool import CandidatePoolRequest


class PathPlanningRequest(CandidatePoolRequest):
    goal_state: str | None = Field(default=None, min_length=1, max_length=101)
    max_steps: int = Field(default=6, ge=1, le=8)


class ProcessPlan(BaseModel):
    plan_id: str
    state: Literal["graph_compatible"] = "graph_compatible"
    research_packet: dict[str, Any] | None
    external_model_allowed: bool
    withheld_evidence_ids: list[str] = Field(default_factory=list)
    recommended: Literal[False] = False
    dispatch_approved: Literal[False] = False


class PlanningPagination(BaseModel):
    enumerated_plans: int = 0
    returned_count: int = 0
    has_more: bool = False
    next_cursor: str | None = None
    complete_in_this_response: bool = True


class PathPlanningResponse(BaseModel):
    contract_version: Literal["kg-process-plans-v1"] = "kg-process-plans-v1"
    status: Literal["needs_input", "unsupported_material", "plans", "no_plans"]
    backend: Literal["neo4j"] = "neo4j"
    normalized_material: str | None = None
    input_context: dict[str, Any]
    missing_fields: list[str]
    items: list[ProcessPlan] = Field(default_factory=list)
    pagination: PlanningPagination = Field(default_factory=PlanningPagination)
    search_scope: dict[str, Any] = Field(default_factory=dict)
    graph_trace: dict[str, Any] = Field(default_factory=dict)
    limitations: list[str]
    dispatch_approved: Literal[False] = False
