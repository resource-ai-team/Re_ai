"""Bounded Kakao/Naver directions calls; no geocoding, freight quote or fallback."""

import logging
import math
import time
from datetime import UTC, datetime

import httpx

from resource_ai.api.schemas.road_routing import RoadMeasurement, RoadRoutesResponse

ENDPOINTS = {
    "kakao": "https://apis-navi.kakaomobility.com/v1/directions",
    "naver": "https://maps.apigw.ntruss.com/map-direction/v1/driving",
}
MAX_BODY_BYTES = 4_000_000
MAX_GEOMETRY_POINTS = 20_000
LIMITATIONS = [
    "지도 사업자의 자동차 도로 경로이며 폐기물 운반 허가·중량/높이/위험물 차량 통행을 보장하지 않습니다.",
    "사업장/시설 좌표와 수거 출입구 검수는 백엔드 책임이며 좌표만 지도 사업자에게 전송합니다.",
    "운송비·탄소·최종 시설 적합성을 계산하지 않습니다. 택시비·연료비를 화물 운임으로 사용하지 않습니다.",
    "교통과 조회 시점에 따라 경로가 달라집니다. 구간 시간 합은 독립 조회한 참고 합계이며 도착시각 예측이 아닙니다.",
    "조회 실패·키 미설정·호출 한도는 미확인으로 남기며 직선거리로 대체하지 않습니다.",
]


class _MapLogFilter(logging.Filter):
    """HTTPX INFO normally logs coordinate query strings; redact only map endpoints."""

    def filter(self, record):
        if any(url in record.getMessage() for url in ENDPOINTS.values()):
            record.msg = "Road routing HTTP request (URL and details withheld)"
            record.args = ()
        return True


def _redact_httpx_map_logs():
    logger = logging.getLogger("httpx")
    if not any(isinstance(f, _MapLogFilter) for f in logger.filters):
        logger.addFilter(_MapLogFilter())


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("Invalid provider number")
    if not math.isfinite(value) or value < 0 or value > 1e12:
        raise ValueError("Invalid provider number")
    return float(value)


def _geometry(points, wanted):
    if not wanted:
        return None, "not_requested"
    if not points:
        return None, "unavailable"
    if len(points) > MAX_GEOMETRY_POINTS:
        return None, "too_large"
    result = []
    for point in points:
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            raise ValueError("Invalid geometry")
        lon, lat = point
        if (
            isinstance(lon, bool)
            or isinstance(lat, bool)
            or not isinstance(lon, (int, float))
            or not isinstance(lat, (int, float))
            or not math.isfinite(lon)
            or not math.isfinite(lat)
            or not -180 <= lon <= 180
            or not -90 <= lat <= 90
        ):
            raise ValueError("Invalid geometry coordinate")
        if not result or result[-1] != [lon, lat]:
            result.append([lon, lat])
    return result, "available"


class RoadRoutingService:
    def __init__(self, config, *, transport=None, clock=time.monotonic):
        self.config = config
        self.provider = config.road_routing_provider
        self.transport = transport
        self.clock = clock

    def unavailable_status(self):
        c = self.config
        if not c.road_routing_enabled:
            return "disabled"
        if self.provider == "none":
            return "not_configured"
        configured = (
            bool(c.kakao_mobility_rest_api_key.get_secret_value())
            if self.provider == "kakao"
            else bool(c.naver_maps_api_key_id.get_secret_value())
            and bool(c.naver_maps_api_key.get_secret_value())
        )
        if not configured:
            return "not_configured"
        if not c.road_routing_allow_external:
            return "transfer_disabled"
        return None

    def unavailable_measurement(self, leg_id, status, *, wanted=False):
        return RoadMeasurement(
            leg_id=leg_id,
            status=status,
            provider=self.provider,
            geometry_status="unavailable" if wanted else "not_requested",
        )

    def _request(self, leg, wanted):
        # Coordinate order is provider X,Y = longitude,latitude. No company/site/name fields.
        start = f"{leg.origin.longitude},{leg.origin.latitude}"
        end = f"{leg.destination.longitude},{leg.destination.latitude}"
        c = self.config
        if self.provider == "kakao":
            return (
                {
                    "origin": start,
                    "destination": end,
                    "priority": "RECOMMEND",
                    "summary": "false" if wanted else "true",
                    "alternatives": "false",
                },
                {"Authorization": "KakaoAK " + c.kakao_mobility_rest_api_key.get_secret_value()},
                "RECOMMEND",
            )
        return (
            {"start": start, "goal": end, "option": "traoptimal"},
            {
                "x-ncp-apigw-api-key-id": c.naver_maps_api_key_id.get_secret_value(),
                "x-ncp-apigw-api-key": c.naver_maps_api_key.get_secret_value(),
            },
            "traoptimal",
        )

    def _parse(self, data, wanted):
        if self.provider == "kakao":
            routes = data["routes"]
            if not isinstance(routes, list) or not routes:
                raise ValueError("Missing route status")
            if any(type(r.get("result_code")) is not int for r in routes):
                raise ValueError("Invalid route status")
            route = next((r for r in routes if r["result_code"] == 0), None)
            if route is None:
                codes = {1, 101, 102, 103, 104, 105, 106, 107}
                return None if all(r["result_code"] in codes for r in routes) else "provider_error"
            summary = route["summary"]
            seconds = _number(summary["duration"])
            points = []
            if wanted:
                for section in route.get("sections", []):
                    for road in section.get("roads", []):
                        values = road["vertexes"]
                        if not isinstance(values, list) or len(values) % 2:
                            raise ValueError("Invalid vertex pairs")
                        points.extend(zip(values[::2], values[1::2]))
        else:
            code = data["code"]
            if type(code) is not int:
                raise ValueError("Invalid route code")
            if code != 0:
                return None if code in {2, 3, 4} else "provider_error"
            route = data["route"]["traoptimal"][0]
            summary = route["summary"]
            seconds = _number(summary["duration"]) / 1000.0
            points = route.get("path", []) if wanted else []
        geometry, geometry_status = _geometry(points, wanted)
        return {
            "road_distance_km": _number(summary["distance"]) / 1000.0,
            "duration_seconds": seconds,
            "geometry": geometry,
            "geometry_status": geometry_status,
        }

    def _one(self, client, leg, wanted, timeout):
        params, headers, option = self._request(leg, wanted)
        stamp = datetime.now(UTC).isoformat()
        try:
            with client.stream(
                "GET", ENDPOINTS[self.provider], params=params, headers=headers, timeout=timeout
            ) as response:
                if response.status_code in {401, 403}:
                    status, result = "provider_auth_failed", None
                elif response.status_code == 429:
                    status, result = "quota_exceeded", None
                elif response.status_code not in {200, 400}:
                    status, result = "provider_error", None
                else:
                    body = bytearray()
                    for chunk in response.iter_bytes(chunk_size=65536):
                        body.extend(chunk)
                        if len(body) > MAX_BODY_BYTES:
                            raise ValueError("Provider response too large")
                    import json

                    data = json.loads(body)
                    if response.status_code == 400:
                        code = data.get("code")
                        if self.provider == "kakao" and code == -10:
                            parsed = "quota_exceeded"
                        elif self.provider == "naver" and type(code) is int and code in {2, 3, 4}:
                            parsed = None
                        else:
                            parsed = "provider_error"
                    else:
                        parsed = self._parse(data, wanted)
                    if parsed is None:
                        status, result = "no_route", None
                    elif isinstance(parsed, str):
                        status, result = parsed, None
                    else:
                        status, result = "found", parsed
        except httpx.TimeoutException:
            status, result = "timeout", None
        except httpx.HTTPError:
            status, result = "provider_error", None
        except (ValueError, KeyError, IndexError, TypeError, AttributeError):
            status, result = "invalid_response", None
        measurement = self.unavailable_measurement(leg.leg_id, status, wanted=wanted)
        return measurement.model_copy(
            update={
                "queried_at": stamp,
                "route_option": option,
                **(result or {}),
            }
        )

    def run(self, request):
        unavailable = self.unavailable_status()
        routes, cache, calls, reused = [], {}, 0, 0
        deadline = self.clock() + self.config.road_routing_total_timeout_seconds
        _redact_httpx_map_logs()
        with httpx.Client(
            transport=self.transport, follow_redirects=False, trust_env=False
        ) as client:
            for leg in request.legs:
                pair = (
                    leg.origin.latitude,
                    leg.origin.longitude,
                    leg.destination.latitude,
                    leg.destination.longitude,
                )
                if unavailable:
                    row = self.unavailable_measurement(
                        leg.leg_id, unavailable, wanted=request.include_geometry
                    )
                elif pair in cache:
                    reused += 1
                    row = cache[pair].model_copy(update={"leg_id": leg.leg_id})
                elif calls >= self.config.road_routing_max_calls or self.clock() >= deadline:
                    row = self.unavailable_measurement(
                        leg.leg_id, "budget_exhausted", wanted=request.include_geometry
                    )
                else:
                    calls += 1
                    row = self._one(
                        client,
                        leg,
                        request.include_geometry,
                        min(
                            self.config.road_routing_timeout_seconds,
                            max(0.001, deadline - self.clock()),
                        ),
                    )
                    cache[pair] = row
                    if row.status in {"quota_exceeded", "provider_auth_failed"}:
                        unavailable = row.status
                routes.append(row)
        found = sum(row.status == "found" for row in routes)
        complete = found == len(routes)
        chain_complete = complete and request.mode == "chain"
        return RoadRoutesResponse(
            provider=self.provider,
            mode=request.mode,
            status="complete" if complete else "partial" if found else "unavailable",
            routes=routes,
            all_legs_routed=complete,
            total_road_distance_km=round(sum(r.road_distance_km for r in routes), 6)
            if chain_complete
            else None,
            total_duration_seconds=round(sum(r.duration_seconds for r in routes), 3)
            if chain_complete
            else None,
            coverage={
                "requested_legs": len(routes),
                "routed_legs": found,
                "external_calls": calls,
                "request_local_reused_legs": reused,
                "full_freight_route_validated": False,
            },
            limitations=LIMITATIONS,
        )


def get_road_routing_service():
    from resource_ai.core.config import get_settings

    return RoadRoutingService(get_settings())
