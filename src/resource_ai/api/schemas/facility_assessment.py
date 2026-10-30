"""Internal documentary checks. No legal, intake or dispatch approval."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool

from resource_ai.api.schemas.kg_planning import PathPlanningRequest, PathPlanningResponse
from resource_ai.api.schemas.numbers import NonBooleanFloat


class LotFacts(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    material: str | None = Field(default=None, min_length=1, max_length=160)
    input_form: str | None = Field(default=None, min_length=1, max_length=80)
    quantity_kg: NonBooleanFloat | None = Field(default=None, gt=0, le=1e12, allow_inf_nan=False)
    color: str | None = Field(default=None, min_length=1, max_length=80)
    contamination: str | None = Field(default=None, min_length=1, max_length=500)
    moisture_pct: NonBooleanFloat | None = Field(default=None, ge=0, le=100, allow_inf_nan=False)
    contamination_pct: NonBooleanFloat | None = Field(
        default=None, ge=0, le=100, allow_inf_nan=False
    )
    designated_waste: StrictBool | None = None


class FacilityAssessmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    capability_key: str = Field(pattern=r"^[a-z][a-z0-9-]{0,100}$")
    lot: LotFacts
    audience: Literal["internal"] = "internal"


class ConditionCheck(BaseModel):
    code: str
    label: str
    status: Literal["matched", "unmet", "needs_verification"]
    observed: str | float | bool | None = None
    expected: str | list[str] | bool | None = None
    scope: str
    source_ids: list[str] = Field(default_factory=list)
    reason: str
    next_action: str


class FacilityConditionAssessment(BaseModel):
    capability_key: str
    facility_key: str
    transition_key: str
    status: Literal["unmet", "needs_verification", "conditions_met"]
    input_basis: Literal["user_supplied_not_independently_verified", "projected_state_not_measured"]
    input_context: LotFacts
    checks: list[ConditionCheck]
    risks: list[dict[str, Any]]
    published_profile: dict[str, Any]
    all_operational_conditions_verified: Literal[False] = False
    dispatch_approved: Literal[False] = False


class FacilityAssessmentResponse(BaseModel):
    contract_version: Literal["kg-facility-assessment-v1"] = "kg-facility-assessment-v1"
    assessment: FacilityConditionAssessment
    evidence: list[dict[str, Any]]
    graph_trace: dict[str, Any]
    limitations: list[str]
    dispatch_approved: Literal[False] = False


class AssessedPlansRequest(PathPlanningRequest):
    audience: Literal["internal"] = "internal"
    moisture_pct: NonBooleanFloat | None = Field(default=None, ge=0, le=100, allow_inf_nan=False)
    contamination_pct: NonBooleanFloat | None = Field(
        default=None, ge=0, le=100, allow_inf_nan=False
    )
    designated_waste: StrictBool | None = None


class AssessedPlansResponse(BaseModel):
    contract_version: Literal["kg-assessed-plans-v1"] = "kg-assessed-plans-v1"
    planning: PathPlanningResponse
    assessments: list[dict[str, Any]]
    limitations: list[str]
    recommended: Literal[False] = False
    dispatch_approved: Literal[False] = False
