"""Synthetic coordinates and provider fixtures; never a live/billable map call."""

import logging
from copy import deepcopy

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from resource_ai.agents.road_discovery import enrich_road_distances
from resource_ai.agents.site_discovery import SiteDiscoveryService
from resource_ai.api.routes import analysis
from resource_ai.api.schemas.road_routing import (
    RoadLeg,
    RoadPoint,
    RoadRoutesRequest,
    SiteRoadOptions,
)
from resource_ai.core.config import Settings
from resource_ai.core.dependencies import get_graph_query
from resource_ai.integrations.road_routing import RoadRoutingService
from resource_ai.main import app
from tests.unit.test_site_discovery import Graph
from tests.unit.test_site_discovery import request as site_request


def config(provider="kakao", **changes):
    return Settings(
        _env_file=None,
        **{
            "road_routing_enabled": True,
            "road_routing_allow_external": True,
            "road_routing_provider": provider,
            "kakao_mobility_rest_api_key": "test-only-map-key",
            "naver_maps_api_key_id": "test-only-map-id",
            "naver_maps_api_key": "test-only-key",
            "road_routing_service_token": "test-only-backend-token",
            **changes,
        },
    )


def point(lat=37.2, lon=127.0):
    return RoadPoint(latitude=lat, longitude=lon)


def leg(name="origin-to-B", start=None, end=None):
    return RoadLeg(leg_id=name, origin=start or point(), destination=end or point(37.3, 127.1))


def kakao(distance=12500, seconds=900):
    return {
        "routes": [
            {
                "result_code": 0,
                "summary": {"distance": distance, "duration": seconds, "fare": {"taxi": 99000}},
                "sections": [{"roads": [{"vertexes": [127.0, 37.2, 127.1, 37.3]}]}],
            }
        ]
    }


def naver(distance=12500, milliseconds=900000):
    return {
        "code": 0,
        "route": {
            "traoptimal": [
                {
                    "summary": {"distance": distance, "duration": milliseconds, "taxiFare": 99000},
                    "path": [[127.0, 37.2], [127.1, 37.3]],
                }
            ]
        },
    }


def service(handler, provider="kakao", **changes):
    return RoadRoutingService(config(provider, **changes), transport=httpx.MockTransport(handler))


def test_oversized_provider_body_is_not_accepted_as_route():
    result = service(lambda r: httpx.Response(200, content=b" " * 4_000_001)).run(
        RoadRoutesRequest(legs=[leg()])
    )
    assert result.routes[0].status == "invalid_response"
    assert result.total_road_distance_km is None


def test_large_geometry_is_omitted_but_valid_distance_is_retained():
    response = naver()
    response["route"]["traoptimal"][0]["path"] = [[127.0, 37.2]] * 20_001
    result = service(lambda r: httpx.Response(200, json=response), "naver").run(
        RoadRoutesRequest(legs=[leg()], include_geometry=True)
    )
    assert result.routes[0].status == "found"
    assert result.routes[0].road_distance_km == 12.5
    assert result.routes[0].geometry is None
    assert result.routes[0].geometry_status == "too_large"


def test_malformed_geometry_never_becomes_a_valid_route():
    response = kakao()
    response["routes"][0]["sections"][0]["roads"][0]["vertexes"] = [127.0, 37.2, 128]
    result = service(lambda r: httpx.Response(200, json=response)).run(
        RoadRoutesRequest(legs=[leg()], include_geometry=True)
    )
    assert result.routes[0].status == "invalid_response"
    assert result.routes[0].road_distance_km is None


@pytest.mark.parametrize("provider", ["kakao", "naver"])
def test_provider_coordinate_order_units_geometry_and_no_business_fields(provider):
    seen = []

    def respond(req):
        seen.append(req)
        assert "name=" not in str(req.url)
        assert "company" not in str(req.url)
        if provider == "kakao":
            assert req.url.params["origin"] == "127.0,37.2"
            assert req.url.params["destination"] == "127.1,37.3"
            assert req.headers["Authorization"] == "KakaoAK test-only-map-key"
            assert req.url.params["summary"] == "false"
        else:
            assert req.url.params["start"] == "127.0,37.2"
            assert req.url.params["goal"] == "127.1,37.3"
            assert req.headers["x-ncp-apigw-api-key-id"] == "test-only-map-id"
        return httpx.Response(200, json=kakao() if provider == "kakao" else naver())

    result = service(respond, provider).run(RoadRoutesRequest(legs=[leg()], include_geometry=True))
    row = result.routes[0]
    assert row.status == "found" and row.distance_method == "road_api"
    assert row.road_distance_km == 12.5 and row.duration_seconds == 900
    assert row.geometry == [[127.0, 37.2], [127.1, 37.3]]
    assert row.transport_cost_krw is None  # taxi fare is never a freight quote
    assert row.queried_at and row.vehicle_scope.endswith("not_truck_clearance")
    assert result.total_road_distance_km == 12.5
    assert result.total_duration_seconds == 900 and len(seen) == 1


@pytest.mark.parametrize(
    "changes,status",
    [
        ({"road_routing_enabled": False}, "disabled"),
        ({"road_routing_provider": "none"}, "not_configured"),
        ({"kakao_mobility_rest_api_key": ""}, "not_configured"),
        ({"road_routing_allow_external": False}, "transfer_disabled"),
    ],
)
def test_inactive_or_missing_credentials_never_send_coordinates(changes, status):
    def forbidden(req):
        pytest.fail("No external call is permitted")

    result = service(forbidden, **changes).run(RoadRoutesRequest(legs=[leg()]))
    assert result.routes[0].status == status
    assert result.routes[0].road_distance_km is None
    assert result.total_road_distance_km is None and not result.all_legs_routed
    assert result.coverage["external_calls"] == 0


def test_multiple_process_legs_are_independent_directional_requests_and_sum_only_full_chain():
    requests = []

    def respond(req):
        requests.append(req)
        return httpx.Response(200, json=kakao(distance=1000 * len(requests)))

    payload = RoadRoutesRequest(
        legs=[leg("A-to-B"), leg("B-to-C", point(37.3, 127.1), point(37.4, 127.2))]
    )
    result = service(respond).run(payload)
    assert requests[1].url.params["origin"] == "127.1,37.3"
    assert requests[1].url.params["destination"] == "127.2,37.4"
    assert result.total_road_distance_km == 3 and result.all_legs_routed
    partial = service(respond, road_routing_max_calls=1).run(payload)
    assert partial.routes[1].status == "budget_exhausted"
    assert partial.total_road_distance_km is None  # never sum a partial route as complete


def test_disconnected_chain_rejected_and_independent_pair_queries_have_no_path_total():
    legs = [leg(), leg("other", point(37.4, 127.2), point(37.5, 127.3))]
    with pytest.raises(ValidationError, match="chain legs must connect"):
        RoadRoutesRequest(legs=legs)
    result = service(lambda r: httpx.Response(200, json=kakao())).run(
        RoadRoutesRequest(legs=legs, mode="independent")
    )
    assert result.all_legs_routed and result.total_road_distance_km is None


def test_duplicate_pair_reused_within_request_only_and_reverse_pair_not_collapsed():
    calls = []

    def respond(req):
        calls.append(req)
        return httpx.Response(200, json=kakao())

    payload = RoadRoutesRequest(
        legs=[leg(), leg("same"), leg("reverse", point(37.3, 127.1), point())], mode="independent"
    )
    s = service(respond)
    result = s.run(payload)
    assert len(calls) == 2
    assert result.coverage["request_local_reused_legs"] == 1
    assert [r.leg_id for r in result.routes] == ["origin-to-B", "same", "reverse"]
    s.run(payload)
    assert len(calls) == 4  # no cross-request business-data cache


@pytest.mark.parametrize(
    "status,expected",
    [
        (401, "provider_auth_failed"),
        (403, "provider_auth_failed"),
        (429, "quota_exceeded"),
        (503, "provider_error"),
        (302, "provider_error"),
    ],
)
def test_http_failures_are_explicit_without_raw_body_or_straight_line_substitution(
    status, expected
):
    calls = []

    def respond(req):
        calls.append(req)
        return httpx.Response(
            status, headers={"Location": "https://untrusted.example"}, text="private upstream body"
        )

    result = service(respond).run(
        RoadRoutesRequest(legs=[leg(), leg("other", end=point(37.4, 127.2))], mode="independent")
    )
    assert all(r.status == expected for r in result.routes)
    assert all(r.road_distance_km is None for r in result.routes)
    assert "private upstream" not in result.model_dump_json()
    if status in {401, 403, 429}:
        assert len(calls) == 1
    assert all(req.url.host == "apis-navi.kakaomobility.com" for req in calls)


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"routes": []},
        {"routes": [{"result_code": False}]},
        kakao(distance=-1),
        kakao(seconds=float("nan")),
    ],
)
def test_malformed_or_nonfinite_measurements_never_become_zero_or_found(body):
    import json

    result = service(lambda r: httpx.Response(200, text=json.dumps(body))).run(
        RoadRoutesRequest(legs=[leg()])
    )
    assert result.routes[0].status == "invalid_response"
    assert result.routes[0].road_distance_km is None


@pytest.mark.parametrize(
    "provider,http_status,body",
    [
        ("kakao", 200, {"routes": [{"result_code": 102}]}),
        ("naver", 400, {"code": 2}),
        ("naver", 400, {"code": 3}),
    ],
)
def test_documented_no_route_response_is_unknown_distance(provider, http_status, body):
    result = service(lambda r: httpx.Response(http_status, json=body), provider).run(
        RoadRoutesRequest(legs=[leg()])
    )
    assert result.routes[0].status == "no_route" and result.routes[0].road_distance_km is None


def test_kakao_http_400_quota_code_stops_further_calls():
    result = service(lambda r: httpx.Response(400, json={"code": -10})).run(
        RoadRoutesRequest(legs=[leg(), leg("other", end=point(37.4, 127.2))], mode="independent")
    )
    assert result.coverage["external_calls"] == 1
    assert all(r.status == "quota_exceeded" for r in result.routes)


def test_timeout_redacts_exception_and_no_implicit_retry():
    def timeout(req):
        raise httpx.ReadTimeout("private coordinate / key data", request=req)

    result = service(timeout).run(RoadRoutesRequest(legs=[leg()]))
    assert result.routes[0].status == "timeout"
    assert "private coordinate" not in result.model_dump_json()
    assert result.coverage["external_calls"] == 1


def test_map_http_info_log_does_not_contain_coordinates_or_keys(caplog):
    with caplog.at_level(logging.INFO, logger="httpx"):
        service(lambda r: httpx.Response(200, json=kakao())).run(RoadRoutesRequest(legs=[leg()]))
    assert "127.0" not in caplog.text and "37.2" not in caplog.text
    assert "test-only-map-key" not in caplog.text
    assert "details withheld" in caplog.text


def test_call_deadline_prevents_next_request():
    ticks = iter([0.0, 21.0, 21.0])
    s = RoadRoutingService(
        config(),
        transport=httpx.MockTransport(lambda r: pytest.fail("deadline expired")),
        clock=lambda: next(ticks),
    )
    result = s.run(RoadRoutesRequest(legs=[leg()]))
    assert result.coverage["external_calls"] == 0
    assert result.routes[0].status == "budget_exhausted"


def test_site_subset_road_sort_and_missing_location_are_not_nationwide_road_ranking():
    def add_points(page, _):
        for row in page.registry_candidates:
            i = int(row["listing_id"])
            row["research_packet"]["coordinates"] = [37.21 + i * 0.01, 127.0] if i < 2 else None

    calls = []

    def respond(req):
        calls.append(req)
        distance = 30000 if req.url.params["destination"].endswith(",37.21") else 10000
        return httpx.Response(200, json=kakao(distance=distance))

    result = SiteDiscoveryService(Graph(add_points)).run(site_request())
    enriched = enrich_road_distances(result, SiteRoadOptions(), service(respond))
    assert len(calls) == 2  # same listings across four process searches deduplicated
    assert not enriched.road_routing["coverage"]["global_road_nearest_verified"]
    for search in enriched.facility_searches:
        items = search["registry_candidates"]
        assert [r["listing_id"] for r in items] == ["1", "0", "2"]
        assert items[-1]["research_packet"]["road_distance_km"] is None
        assert items[-1]["research_packet"]["road_route"]["status"] == "missing_coordinates"
        assert not search["automatic_path_insertion"]
    assert enriched.route == "REVIEW" and not enriched.dispatch_approved


def test_http_requires_separate_backend_service_auth_before_any_external_call(monkeypatch):
    calls = []
    s = service(lambda r: calls.append(r) or httpx.Response(200, json=kakao()))
    previous = deepcopy(app.dependency_overrides)
    app.dependency_overrides[analysis.get_road_routing_service] = lambda: s
    monkeypatch.setattr(analysis, "get_road_routing_service", lambda: s)
    app.dependency_overrides[get_graph_query] = lambda: Graph()
    client = TestClient(app)
    payload = RoadRoutesRequest(legs=[leg()]).model_dump(mode="json")
    try:
        assert client.post("/analysis/road-routes", json=payload).status_code == 401
        assert (
            client.post(
                "/analysis/road-routes", json=payload, headers={"Authorization": "Bearer wrong"}
            ).status_code
            == 401
        )
        site = site_request(road_routing={}).model_dump(mode="json")
        assert client.post("/analysis/site-discovery", json=site).status_code == 401
        assert calls == []
        response = client.post(
            "/analysis/road-routes",
            json=payload,
            headers={"Authorization": "Bearer test-only-backend-token"},
        )
        assert response.status_code == 200 and response.json()["routes"][0]["status"] == "found"
        s.config = config(road_routing_service_token="")
        assert client.post("/analysis/road-routes", json=payload).status_code == 503
    finally:
        app.dependency_overrides = previous


def test_unconfigured_http_diagnostic_does_not_make_a_model_or_map_call():
    s = service(lambda r: pytest.fail("No map call"), road_routing_enabled=False)
    previous = deepcopy(app.dependency_overrides)
    app.dependency_overrides[analysis.get_road_routing_service] = lambda: s
    try:
        response = TestClient(app).post(
            "/analysis/road-routes", json=RoadRoutesRequest(legs=[leg()]).model_dump(mode="json")
        )
        assert response.status_code == 200
        assert response.json()["routes"][0]["status"] == "disabled"
    finally:
        app.dependency_overrides = previous
