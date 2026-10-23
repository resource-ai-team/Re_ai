"""Internal site-scoped investigation, without customer/permit/price inference."""

from typing import Any, Literal

from pydantic import Field, model_validator

from resource_ai.api.schemas.analysis import CandidateResearch, StrictModel
from resource_ai.api.schemas.facility_assessment import AssessedPlansRequest


class OriginSite(StrictModel):
    organisation_id: str = Field(min_length=1, max_length=160)
    site_id: str = Field(min_length=1, max_length=160)
    label: str = Field(min_length=1, max_length=200)
    pickup_address: str = Field(min_length=1, max_length=500)
    latitude: float = Field(ge=33, le=39, allow_inf_nan=False)
    longitude: float = Field(ge=124, le=132, allow_inf_nan=False)
    country: Literal["KR"] = "KR"


class SiteDiscoveryRequest(StrictModel):
    origin: OriginSite
    lot: AssessedPlansRequest
    max_plan_pages: int = Field(default=20, ge=1, le=20)
    facility_page_size: int = Field(default=25, ge=1, le=100)
    max_facility_pages: int = Field(default=2, ge=1, le=20)

    @model_validator(mode="after")
    def first_page(self):
        if self.lot.cursor is not None:
            raise ValueError("Start site investigation from the first KG page")
        return self


class SiteDiscoveryResponse(StrictModel):
    contract_version: Literal["site-facility-discovery-v1"] = "site-facility-discovery-v1"
    origin: OriginSite
    origin_basis: Literal["server_supplied_not_independently_verified"] = (
        "server_supplied_not_independently_verified"
    )
    context_sha256: str
    graph_trace: dict[str, Any]
    coverage: dict[str, Any]
    candidates: list[CandidateResearch]
    facility_searches: list[dict[str, Any]]
    legal_search_performed: Literal[False] = False
    generation_model_called: Literal[False] = False
    cost_carbon_evaluated: Literal[False] = False
    route: Literal["REVIEW"] = "REVIEW"
    final_recommendations: list[str] = Field(default_factory=list, max_length=0)
    dispatch_approved: Literal[False] = False
    limitations: list[str]
