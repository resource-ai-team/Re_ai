"""Read-only KG → local ILCD screening contract (not a final recommendation)."""

from typing import Any, Literal

from pydantic import Field, model_validator

from resource_ai.api.schemas.analysis import StrictModel
from resource_ai.api.schemas.facility_assessment import AssessedPlansRequest


class KGPathLCIRequest(StrictModel):
    # Using the existing contract retains KG generation and facility condition checks.
    lot: AssessedPlansRequest
    max_pages: int = Field(default=20, ge=1, le=20)

    @model_validator(mode="after")
    def internal_first_page_only(self):
        if self.lot.cursor is not None:
            raise ValueError("KG/LCI evaluation must start at the first candidate page.")
        return self


class KGPathLCIResponse(StrictModel):
    contract_version: Literal["kg-path-lci-screening-v1"] = "kg-path-lci-screening-v1"
    input_context: dict[str, Any]
    graph_trace: dict[str, Any]
    coverage: dict[str, Any]
    datasets_loaded: list[str]
    results: list[dict[str, Any]]
    next_actions: list[str]
    route: Literal["REVIEW"] = "REVIEW"
    final_recommendations: list[str] = Field(default_factory=list, max_length=0)
    dispatch_approved: Literal[False] = False
    limitations: list[str]
