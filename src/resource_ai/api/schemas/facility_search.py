"""Facility discovery, not facility approval or automatic path insertion."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class FacilitySearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    transition_key: str = Field(pattern=r"^[a-z][a-z0-9-]{0,100}$")
    waste_code: str | None = Field(default=None, pattern=r"^\d{2}-\d{2}-\d{2}$")
    region: str | None = Field(default=None, min_length=1, max_length=160)
    origin_latitude: float | None = Field(default=None, ge=-90, le=90, allow_inf_nan=False)
    origin_longitude: float | None = Field(default=None, ge=-180, le=180, allow_inf_nan=False)
    sort_by: Literal["relevance", "straight_line_distance"] = "relevance"
    page_size: int = Field(default=25, ge=1, le=100)
    cursor: str | None = Field(default=None, min_length=1, max_length=2048)
    audience: Literal["internal", "external_model"] = "internal"

    @model_validator(mode="after")
    def private_constraints(self):
        if (self.origin_latitude is None) != (self.origin_longitude is None):
            raise ValueError("Both origin coordinates are required")
        if self.sort_by == "straight_line_distance" and self.origin_latitude is None:
            raise ValueError("Distance ordering requires origin coordinates")
        if self.audience == "external_model" and (
            self.region is not None or self.origin_latitude is not None
        ):
            raise ValueError("Use internal facility search for private location constraints")
        return self


class FacilitySearchResponse(BaseModel):
    contract_version: Literal["kg-facility-search-v1"] = "kg-facility-search-v1"
    status: Literal["candidates", "reference_only", "no_candidates"]
    backend: Literal["neo4j"] = "neo4j"
    transition_key: str
    material: str
    reference_bindings: list[dict[str, Any]]
    registry_candidates: list[dict[str, Any]]
    pagination: dict[str, Any]
    graph_trace: dict[str, Any]
    search_scope: dict[str, Any]
    limitations: list[str]
    dispatch_approved: Literal[False] = False
