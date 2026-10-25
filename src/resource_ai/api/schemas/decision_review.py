"""KG-bound review contracts. No caller-supplied audit verdict or operating approval."""

from typing import Any, Literal

from pydantic import Field, model_validator

from resource_ai.api.schemas.analysis import (
    ResearchRequest,
    ResearchResponse,
    ScenarioComparisonRequest,
    StrictModel,
)
from resource_ai.api.schemas.facility_assessment import AssessedPlansRequest


class ReviewTemplateRequest(StrictModel):
    lot: AssessedPlansRequest
    site_context_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    max_pages: int = Field(default=20, ge=1, le=20)

    @model_validator(mode="after")
    def first_page(self):
        if self.lot.cursor is not None:
            raise ValueError("양식은 전체 조회의 첫 페이지에서 만듭니다. lot.cursor를 비워 주세요.")
        return self


class NumericBinding(StrictModel):
    input_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    graph_trace: dict[str, str] = Field(min_length=1, max_length=20)
    comparison: ScenarioComparisonRequest
    site_context_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


class DecisionReviewRequest(StrictModel):
    research: ResearchRequest
    numeric: NumericBinding | None = None
    site_context_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


class CriticIssue(StrictModel):
    code: str
    stage: Literal["kg", "validation", "calculation", "comparison"]
    severity: Literal["error", "missing"]
    candidate_id: str | None = None
    target_id: str | None = None
    target_type: Literal["candidate", "stage", "handoff", "global"]
    evidence_ids: list[str] = Field(default_factory=list)
    message: str
    next_action: str


class DecisionReviewResponse(StrictModel):
    contract_version: Literal["grounded-decision-review-v1"] = "grounded-decision-review-v1"
    run_id: str
    research: ResearchResponse
    binding: dict[str, Any]
    numeric_review: dict[str, Any]
    critic: list[CriticIssue]
    router: dict[str, Any]
    candidate_reviews: list[dict[str, Any]]
    map_markers: list[dict[str, Any]]
    map_marker_groups: list[dict[str, Any]] = Field(default_factory=list)
    stage_statuses: list[dict[str, str]]
    report_markdown: str
    route: Literal["REVIEW", "REVISE"]
    final_recommendations: list[str] = Field(default_factory=list, max_length=0)
    dispatch_approved: Literal[False] = False
    limitations: list[str]
