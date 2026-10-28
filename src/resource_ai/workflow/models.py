"""Explicit case facts, document references and component-level numeric inputs."""

from datetime import date
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import Field, StrictBool, StrictInt, model_validator

from resource_ai.api.schemas.analysis import StrictModel
from resource_ai.api.schemas.road_routing import RoadPoint
from resource_ai.api.schemas.site_review import SiteReviewRequest

Identifier = Annotated[str, Field(pattern=r"^[A-Za-z0-9_.:-]{1,200}$")]
Number = Annotated[Decimal, Field(ge=0, le=Decimal("1e15"), allow_inf_nan=False)]
Unit = Literal["kg", "t", "kWh", "MJ", "L", "m3", "km", "t_km", "trip", "batch"]


class Source(StrictModel):
    evidence_id: Identifier
    title: str = Field(min_length=1, max_length=300)
    locator: str = Field(min_length=1, max_length=500)
    content: str = Field(default="", max_length=30000)
    document_id: Identifier | None = None
    url: str | None = Field(default=None, pattern=r"^https://[^\s]+$", max_length=1500)
    sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    kind: Literal["document", "quote", "activity", "factor", "facility", "road"]
    synthetic: StrictBool = False
    user_confirmed: StrictBool = False
    external_model_allowed: StrictBool = False
    observed_on: date | None = None
    valid_until: date | None = None


class Range(StrictModel):
    low: Number
    high: Number | None = None

    @model_validator(mode="after")
    def ordered(self):
        if self.high is None:
            self.high = self.low
        if self.high < self.low:
            raise ValueError("high must be >= low")
        return self


class Observation(Range):
    unit: str = Field(min_length=1, max_length=45)
    evidence_id: Identifier
    basis: Literal["input_waste", "output_product", "activity"] = "activity"


class FactorScope(StrictModel):
    region: str = Field(default="KR", min_length=1, max_length=80)
    reference_year: int = Field(ge=1900, le=2200)
    gwp: str | None = Field(default=None, max_length=100)
    boundary: str = Field(min_length=1, max_length=200)
    purpose: Literal["process", "transport", "residue", "electricity", "reporting_only", "cost"]
    transfer_explanation: str | None = Field(default=None, min_length=1, max_length=500)
    transport_included: StrictBool = False
    system_boundary: Literal["collection_to_recovered_output_and_residue"] | None = None
    allocation: Literal["cut_off_no_avoided_virgin_credit"] | None = None


class MetricLine(StrictModel):
    line_id: Identifier
    target_id: Identifier
    metric: Literal["cost", "carbon"]
    category: Literal["process", "transport", "residue", "revenue"]
    component: str = Field(min_length=1, max_length=80)
    activity: Observation
    factor: Observation
    scope: FactorScope
    covers_full_target: StrictBool = False
    includes_targets: list[Identifier] = Field(default_factory=list, max_length=20)
    # Activity scaling is explicit, not inferred from a coincidental equal number.
    mass_binding: Literal[
        "none", "lot_input", "stage_input", "stage_output", "stage_residue", "total_residue"
    ] = "none"
    payload_kg_per_trip: Number | None = None
    mass_stage_id: Identifier | None = None
    trip_count: StrictInt = Field(default=1, ge=1, le=100000)


class StageMass(StrictModel):
    stage_id: Identifier
    input_kg: Number
    output_kg: Number
    residue_kg: Number
    other_output_kg: Number = Decimal(0)
    evidence_ids: list[Identifier] = Field(min_length=1, max_length=10)


class FacilitySelection(StrictModel):
    stage_id: Identifier
    facility_id: Identifier
    label: str = Field(min_length=1, max_length=200)
    gate: RoadPoint
    evidence_id: Identifier


class TailLeg(StrictModel):
    leg_id: Identifier
    from_stage_id: Identifier
    purpose: Literal["residue", "output"]
    destination: RoadPoint
    evidence_id: Identifier


class CandidateDraft(StrictModel):
    candidate_id: Identifier
    facilities: list[FacilitySelection] = Field(default_factory=list, max_length=8)
    tails: list[TailLeg] = Field(default_factory=list, max_length=10)
    masses: list[StageMass] = Field(default_factory=list, max_length=8)
    lines: list[MetricLine] = Field(default_factory=list, max_length=200)


class CaseCreate(StrictModel):
    prompt: str = Field(min_length=1, max_length=12000)
    engine_request: SiteReviewRequest | None = None
    mode: Literal["research", "simulation"] = "research"
    assessment_on: date
    carbon_method: str = Field(default="IPCC_AR5", min_length=1, max_length=100)

    @model_validator(mode="after")
    def controlled_external_steps(self):
        if self.engine_request and any(
            (
                self.engine_request.numeric,
                self.engine_request.semantic_critic,
                self.engine_request.road_routing,
                self.engine_request.legal_graph,
            )
        ):
            raise ValueError("Use workflow operations for numeric, road and critic steps")
        return self


class Revision(StrictModel):
    expected_revision: int = Field(ge=1)


class InputUpdate(Revision):
    inputs: CaseCreate


class EvidenceUpdate(Revision):
    evidence: list[Source] = Field(min_length=1, max_length=100)


class DraftUpdate(Revision):
    draft: CandidateDraft


class RunOptions(Revision):
    query_roads: StrictBool = False
    run_critic: StrictBool = False
    max_repairs: int = Field(default=2, ge=0, le=2)
    max_model_calls: int = Field(default=1, ge=0, le=2)


class DocumentUpload(Revision):
    filename: str = Field(min_length=1, max_length=200)
    media_type: Literal["application/pdf", "image/png", "image/jpeg", "text/plain"]
    base64_content: str = Field(min_length=1, max_length=8_000_000)
    use_azure_ocr: StrictBool = False


class ExtractionConfirmation(Revision):
    document_id: Identifier
    text: str = Field(min_length=1, max_length=30000)
    locator: str = Field(min_length=1, max_length=500)
    kind: Literal["document", "quote", "activity", "factor", "facility"] = "document"


class DocumentFactApply(Revision):
    document_id: Identifier
    field_ids: list[Identifier] = Field(min_length=1, max_length=3)
    same_lot_confirmed: StrictBool = False
