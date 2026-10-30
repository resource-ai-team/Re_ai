"""Internal contracts: an extraction is a claim, never an automatic measurement."""

from datetime import date
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import Field, FiniteFloat, StrictBool, model_validator

from resource_ai.api.schemas.analysis import StrictModel
from resource_ai.api.schemas.site_discovery import OriginSite
from resource_ai.workflow.models import Identifier, Number, Revision

Percent = Annotated[Decimal, Field(ge=0, le=100, allow_inf_nan=False)]
FieldName = Literal[
    "material",
    "material_form",
    "quantity_kg",
    "generation_process",
    "contamination",
    "moisture",
    "observed_impurities",
    "other_measurement",
]
SourceType = Literal[
    "document_extracted_unconfirmed",
    "document_user_confirmed",
    "operator_reported",
    "photo_observed",
    "literature_assumption",
    "unknown",
]


class QualityClaim(StrictModel):
    evidence_id: Identifier
    field: FieldName
    source_type: SourceType
    raw_value: str | None = Field(default=None, max_length=1000)
    normalized_value: Number | None = None
    text_value: str | None = Field(default=None, max_length=1000)
    unit: str | None = Field(default=None, max_length=40)
    basis: Literal["wet_mass", "dry_mass", "volume", "unknown"] = "unknown"
    meaning: Literal["lot_property", "migration", "mechanical", "unknown"] = "unknown"
    qualifier: Literal["exact", "lt", "gt", "not_detected", "unknown"] = "exact"
    test_method: str | None = Field(default=None, max_length=200)
    sample_id: Identifier | None = None
    measurement_date: date | None = None
    document_id: Identifier | None = None
    page: int | None = Field(default=None, ge=1, le=10000)
    bounding_region: list[FiniteFloat] = Field(default_factory=list, max_length=16)
    extraction_confidence: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    verification_status: Literal[
        "unconfirmed", "user_confirmed", "reported", "assumption", "unknown"
    ] = "unconfirmed"
    same_lot_confirmed: StrictBool = False
    assumptions: list[str] = Field(default_factory=list, max_length=10)
    synthetic: StrictBool = False

    @model_validator(mode="after")
    def observational_limits(self):
        if self.source_type == "photo_observed" and self.normalized_value is not None:
            raise ValueError("photos cannot supply quantitative lot measurements")
        if self.source_type == "literature_assumption" and not self.assumptions:
            raise ValueError("literature requires an explicit scenario assumption")
        return self


class QualityInputUpdate(Revision):
    sample_id: Identifier | None = None
    claims: list[QualityClaim] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def manual_sources_only(self):
        for claim in self.claims:
            if claim.source_type not in {
                "operator_reported",
                "photo_observed",
                "literature_assumption",
                "unknown",
            }:
                raise ValueError("document claims can only originate in the OCR adapter")
            if claim.document_id or claim.verification_status == "user_confirmed":
                raise ValueError("manual input cannot impersonate confirmed document evidence")
        return self


class QualityConfirmation(Revision):
    evidence_ids: list[Identifier] = Field(min_length=1, max_length=100)
    sample_id: Identifier
    same_lot_confirmed: Literal[True]


class SpecificationLimit(StrictModel):
    field: Literal["contamination", "moisture"]
    max_value: Annotated[Decimal, Field(ge=0, lt=100, allow_inf_nan=False)]
    unit: Literal["wt%"] = "wt%"
    basis: Literal["wet_mass"] = "wet_mass"


class TargetSpecification(StrictModel):
    specification_id: Identifier
    name: str = Field(min_length=1, max_length=200)
    version: str = Field(min_length=1, max_length=100)
    jurisdiction: str = Field(min_length=1, max_length=80)
    material: Literal["PP"] = "PP"
    forms: list[Literal["rigid_scrap", "rigid_part", "flake", "pellet", "bale"]] = Field(
        min_length=1, max_length=5
    )
    applies_to: Literal["input_waste", "recycled_moulding_material", "postconsumer_bale"]
    requirement_type: Literal[
        "buyer_requirement", "trade_guideline", "scenario_target", "standard_metadata"
    ]
    source: str = Field(min_length=1, max_length=1500)
    effective_on: date | None = None
    limits: list[SpecificationLimit] = Field(default_factory=list, max_length=2)
    explicitly_selected: Literal[True]
    applicability_confirmed: StrictBool = False
    status: Literal["user_declared", "reference_only", "metadata_only"] = "user_declared"

    @model_validator(mode="after")
    def no_invented_standards(self):
        if len({r.field for r in self.limits}) != len(self.limits):
            raise ValueError("duplicate target field")
        if self.requirement_type == "standard_metadata" and self.limits:
            raise ValueError("metadata-only standards cannot supply thresholds")
        rema = any(
            x in (self.specification_id + self.name + self.source).lower()
            for x in ("rema", "isri", "isrispecs")
        )
        if rema and (
            self.jurisdiction == "KR"
            or self.requirement_type != "trade_guideline"
            or self.applies_to != "postconsumer_bale"
        ):
            raise ValueError("ReMA is a foreign postconsumer trade reference")
        if "3843" in self.specification_id + self.name and self.limits:
            raise ValueError("KS M3843 full text and thresholds have not been acquired")
        return self


class PerformanceStage(StrictModel):
    stage_id: Identifier
    process: Literal["sorting", "shredding", "washing", "drying"]
    contaminant_removal_pct: Percent
    pp_loss_pct: Percent
    evidence_id: Identifier
    basis: Literal["stage_input_component_mass"] = "stage_input_component_mass"


class QualityEconomics(StrictModel):
    preprocessing_krw: Number
    transport_krw: Number
    residue_krw_per_kg: Number
    sale_price_krw_per_kg: Number
    evidence_ids: list[Identifier] = Field(min_length=1, max_length=20)
    prices_are_quotes: Literal[False] = False
    baseline_net_cost_krw: Decimal | None = Field(
        default=None, ge=-1e15, le=1e15, allow_inf_nan=False
    )


class QualityScenario(StrictModel):
    target: TargetSpecification
    stages: list[PerformanceStage] = Field(default_factory=list, max_length=4)
    overall_pp_loss_pct: Percent | None = None
    economics: QualityEconomics | None = None
    explicit_assumptions: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def loss_accounting(self):
        if self.stages and self.overall_pp_loss_pct is not None:
            raise ValueError("overall and overlapping stage losses cannot both be counted")
        if len({s.stage_id for s in self.stages}) != len(self.stages):
            raise ValueError("duplicate stage id")
        if (
            self.stages or self.economics or self.overall_pp_loss_pct is not None
        ) and not self.explicit_assumptions:
            raise ValueError(
                "performance and economic inputs require explicit scenario assumptions"
            )
        return self


class QualityScenarioUpdate(Revision):
    scenario: QualityScenario


class QualityScenarioComparison(Revision):
    scenarios: list[QualityScenario] = Field(min_length=1, max_length=5)


class QualityResearchUpdate(Revision):
    origin: OriginSite
    max_plan_pages: int = Field(default=1, ge=1, le=20)
    max_legal_queries: int = Field(default=2, ge=1, le=24)
    max_technical_queries: int = Field(default=2, ge=0, le=12)


class WasteProfile(StrictModel):
    tenant_id: str
    lot_id: str
    sample_id: str | None = None
    material: str | None = None
    material_form: str | None = None
    quantity_kg: Decimal | None = None
    generation_process: str | None = None
    composition: dict = Field(default_factory=dict)
    contamination: Decimal | None = None
    moisture: Decimal | None = None
    observed_impurities: list[str] = Field(default_factory=list)
    measurements: list[QualityClaim] = Field(default_factory=list)
    input_sources: list[dict] = Field(default_factory=list)
    unknown_fields: list[str] = Field(default_factory=list)
    conflicts: list[dict] = Field(default_factory=list)
    user_confirmations: list[dict] = Field(default_factory=list)
