"""Internal research and arithmetic contracts; neither grants operational approval."""

from datetime import date
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator

from resource_ai.api.schemas.facility_assessment import AssessedPlansRequest
from resource_ai.api.schemas.kg_planning import ProcessPlan


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ResearchRequest(StrictModel):
    lot: AssessedPlansRequest
    max_pages: int = Field(default=20, ge=1, le=20)
    max_legal_queries: int = Field(default=24, ge=1, le=100)
    legal_top_k: int = Field(default=3, ge=1, le=5)

    @model_validator(mode="after")
    def start_at_first_page(self):
        if self.lot.cursor is not None:
            raise ValueError("분석은 첫 페이지부터 시작합니다. lot.cursor를 비워 주세요.")
        return self


class LegalSearch(StrictModel):
    search_id: str
    query: str
    status: Literal["retrieved", "no_hits", "unavailable", "budget_exhausted"]
    results: list[dict[str, Any]] = Field(default_factory=list)
    applicability: Literal["not_audited"] = "not_audited"


class CandidateResearch(StrictModel):
    plan: ProcessPlan
    facility_assessment: dict[str, Any]
    legal_tasks: list[dict[str, str]]
    blockers: list[str]
    status: Literal["unmet", "needs_verification"]
    recommended: Literal[False] = False


class ResearchResponse(StrictModel):
    contract_version: Literal["candidate-research-v1"] = "candidate-research-v1"
    run_id: str
    request_sha256: str
    generated_at: str
    input_context: dict[str, Any]
    graph_trace: dict[str, Any]
    coverage: dict[str, Any]
    candidates: list[CandidateResearch]
    legal_searches: list[LegalSearch]
    next_actions: list[str]
    route: Literal["REVIEW"] = "REVIEW"
    final_recommendations: list[str] = Field(default_factory=list, max_length=0)
    limitations: list[str]
    dispatch_approved: Literal[False] = False


class ComparisonBasis(StrictModel):
    quantity_kg: Decimal = Field(
        gt=0, le=Decimal("1e12"), max_digits=30, decimal_places=12, allow_inf_nan=False
    )
    as_of: date
    region: str = Field(min_length=1, max_length=80)
    assessment_year: int = Field(ge=1900, le=2200)
    carbon_method: str = Field(min_length=1, max_length=100)
    functional_unit: Literal["management_of_same_input_waste_mass"] = (
        "management_of_same_input_waste_mass"
    )
    boundary: Literal["input_waste_to_output_gate_including_transport_and_residue"] = (
        "input_waste_to_output_gate_including_transport_and_residue"
    )
    allocation: Literal["cut_off_no_avoided_virgin_credit"] = "cut_off_no_avoided_virgin_credit"
    currency: Literal["KRW"] = "KRW"


class NumericEvidence(StrictModel):
    evidence_id: str = Field(min_length=1, max_length=160)
    source_url: str = Field(pattern=r"^https?://[^\s]+$", max_length=1500)
    locator: str = Field(min_length=1, max_length=500)
    kind: Literal["activity_record", "cost_factor", "carbon_factor"]
    valid_from: date
    valid_until: date
    region: str = Field(min_length=1, max_length=80)
    assessment_year: int = Field(ge=1900, le=2200)
    synthetic: StrictBool = False
    carbon_method: str | None = Field(default=None, min_length=1, max_length=100)

    @model_validator(mode="after")
    def ordered_dates(self):
        if self.valid_from > self.valid_until:
            raise ValueError("근거의 시작일은 종료일보다 늦을 수 없습니다.")
        return self


class CalculationLine(StrictModel):
    line_id: str = Field(min_length=1, max_length=160)
    target_id: str = Field(min_length=1, max_length=200)
    metric: Literal["cost", "carbon"]
    category: Literal["process", "transport", "residue", "revenue"]
    activity: Decimal = Field(
        ge=0, le=Decimal("1e15"), max_digits=30, decimal_places=12, allow_inf_nan=False
    )
    activity_unit: Literal["kg", "t", "t_km", "kWh", "trip", "batch"]
    factor: Decimal = Field(
        ge=0, le=Decimal("1e15"), max_digits=30, decimal_places=12, allow_inf_nan=False
    )
    factor_unit: str = Field(min_length=1, max_length=40)
    activity_evidence_id: str = Field(min_length=1, max_length=160)
    factor_evidence_id: str = Field(min_length=1, max_length=160)


class NumericScenario(StrictModel):
    candidate_id: str = Field(min_length=1, max_length=160)
    basis: ComparisonBasis
    stage_ids: list[Annotated[str, Field(min_length=1, max_length=200)]] = Field(
        min_length=1, max_length=8
    )
    handoff_ids: list[Annotated[str, Field(min_length=1, max_length=200)]] = Field(
        default_factory=list, max_length=7
    )
    lines: list[CalculationLine] = Field(default_factory=list, max_length=250)


class ScenarioComparisonRequest(StrictModel):
    basis: ComparisonBasis
    mode: Literal["submitted_evidence", "simulation"] = "submitted_evidence"
    evidence: list[NumericEvidence] = Field(default_factory=list, max_length=1000)
    scenarios: list[NumericScenario] = Field(min_length=1, max_length=20)


class ScenarioComparisonResponse(StrictModel):
    contract_version: Literal["scenario-comparison-v1"] = "scenario-comparison-v1"
    basis: ComparisonBasis
    mode: str
    evidence: list[NumericEvidence]
    results: list[dict[str, Any]]
    pareto_fronts: list[list[str]]
    incomparable_ids: list[str]
    decision_scope: Literal["arithmetic_on_submitted_scenarios_only"] = (
        "arithmetic_on_submitted_scenarios_only"
    )
    final_recommendations: list[str] = Field(default_factory=list, max_length=0)
    limitations: list[str]
    dispatch_approved: Literal[False] = False
