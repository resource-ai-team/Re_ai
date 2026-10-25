"""Road API measurements are separate from straight-line search and freight approval."""

from typing import Literal

from pydantic import Field, StrictBool, model_validator

from resource_ai.api.schemas.analysis import StrictModel


class RoadPoint(StrictModel):
    latitude: float = Field(ge=32, le=39.5, allow_inf_nan=False)
    longitude: float = Field(ge=124, le=132, allow_inf_nan=False)


class RoadLeg(StrictModel):
    leg_id: str = Field(min_length=1, max_length=160)
    origin: RoadPoint
    destination: RoadPoint


class RoadRoutesRequest(StrictModel):
    legs: list[RoadLeg] = Field(min_length=1, max_length=20)
    include_geometry: StrictBool = False
    mode: Literal["chain", "independent"] = "chain"

    @model_validator(mode="after")
    def distinct_ids(self):
        if len({leg.leg_id for leg in self.legs}) != len(self.legs):
            raise ValueError("leg_id must be unique within the request")
        if self.mode == "chain" and any(
            a.destination != b.origin for a, b in zip(self.legs, self.legs[1:])
        ):
            raise ValueError("chain legs must connect destination to next origin")
        return self


class SiteRoadOptions(StrictModel):
    max_destinations: int = Field(default=10, ge=1, le=20)
    include_geometry: StrictBool = False


class RoadMeasurement(StrictModel):
    leg_id: str
    status: Literal[
        "found",
        "disabled",
        "not_configured",
        "transfer_disabled",
        "budget_exhausted",
        "timeout",
        "quota_exceeded",
        "provider_auth_failed",
        "provider_error",
        "invalid_response",
        "no_route",
        "missing_coordinates",
        "not_selected",
    ]
    provider: Literal["none", "kakao", "naver"]
    distance_method: Literal["road_api"] = "road_api"
    road_distance_km: float | None = None
    duration_seconds: float | None = None
    geometry: list[list[float]] | None = None  # [longitude, latitude]
    geometry_status: Literal["not_requested", "available", "unavailable", "too_large"]
    queried_at: str | None = None
    route_option: str | None = None
    vehicle_scope: Literal["provider_default_car_not_truck_clearance"] = (
        "provider_default_car_not_truck_clearance"
    )
    transport_cost_krw: None = None


class RoadRoutesResponse(StrictModel):
    contract_version: Literal["road-routes-v1"] = "road-routes-v1"
    provider: Literal["none", "kakao", "naver"]
    mode: Literal["chain", "independent"]
    status: Literal["complete", "partial", "unavailable"]
    routes: list[RoadMeasurement]
    coverage: dict[str, int | bool | str]
    all_legs_routed: bool
    total_road_distance_km: float | None
    total_duration_seconds: float | None
    dispatch_approved: Literal[False] = False
    limitations: list[str]
