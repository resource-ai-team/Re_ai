"""Synthetic origin/listings; actual curated path structure, no model or road API."""

from copy import deepcopy

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from resource_ai.agents.site_discovery import SiteDiscoveryService
from resource_ai.api.schemas.facility_search import FacilitySearchRequest, FacilitySearchResponse
from resource_ai.api.schemas.site_discovery import SiteDiscoveryRequest
from resource_ai.core.dependencies import get_graph_query
from resource_ai.knowledge_graph.facility_search import registry_matches
from resource_ai.knowledge_graph.graph_store import GraphStoreError
from resource_ai.main import app
from tests.unit.test_candidate_research import FakeGraph
from tests.unit.test_facility_search import normalized


def request(**kwargs):
    data = {
        "origin": {
            "organisation_id": "synthetic-org",
            "site_id": "synthetic-site",
            "label": "가상 사업장",
            "pickup_address": "가상 주소: 실제 SK 공장 아님",
            "latitude": 37.2,
            "longitude": 127.0,
        },
        "lot": {"material": "PP", "input_form": "scrap", "page_size": 1},
        "facility_page_size": 2,
        "max_facility_pages": 2,
    }
    return SiteDiscoveryRequest.model_validate({**data, **kwargs})


class Graph(FakeGraph):
    def __init__(self, fault=None):
        super().__init__()
        self.facility_calls = []
        self.fault = fault

    def assessed_plans(self, request):
        result = super().assessed_plans(request)
        result.planning.graph_trace["reference_sha256"] = "a" * 64
        return result

    def facility_candidates(self, request):
        self.facility_calls.append(request)
        assert request.audience == "internal"
        assert request.sort_by == "straight_line_distance"
        assert request.region is None  # nearby cross-border facilities must remain discoverable
        offset = int(request.cursor or 0)
        rows = [
            {"listing_id": str(i), "research_packet": {"straight_line_distance_km": i}}
            for i in range(3)
        ]
        selected = rows[offset : offset + request.page_size]
        more = offset + len(selected) < len(rows)
        trace = {
            "generation": "synthetic-generation",
            "reference_sha256": "a" * 64,
            "registry_generation": "synthetic-registry",
            "registry_source_sha256": "b" * 64,
            "codebook_sha256": "c" * 64,
        }
        response = FacilitySearchResponse(
            status="candidates",
            transition_key=request.transition_key,
            material="pp",
            reference_bindings=[],
            registry_candidates=selected,
            graph_trace=trace,
            pagination={
                "total_registry_candidates": 3,
                "returned_count": len(selected),
                "has_more": more,
                "next_cursor": str(offset + len(selected)) if more else None,
            },
            search_scope={"registry_imported": True},
            limitations=[],
        )
        if self.fault:
            self.fault(response, len(self.facility_calls))
        return response


def test_site_is_connected_to_every_unique_stage_once_without_retrieval():
    graph = Graph()
    result = SiteDiscoveryService(graph).run(request())
    assert len(result.candidates) == 3
    assert result.coverage["all_plan_pages_read"]
    assert result.coverage["unique_transitions_searched"] == 4
    assert len(graph.facility_calls) == 8  # two pages for each distinct transition
    assert sum(len(row["candidate_stage_refs"]) for row in result.facility_searches) == 8
    assert all(len(row["registry_candidates"]) == 3 for row in result.facility_searches)
    assert not result.legal_search_performed and not result.generation_model_called
    assert not result.cost_carbon_evaluated and not result.dispatch_approved
    assert result.route == "REVIEW" and result.final_recommendations == []
    assert all(row["road_distance_km"] is None for row in result.facility_searches)
    assert all(not row["automatic_path_insertion"] for row in result.facility_searches)


def test_partial_search_preserves_original_plans_and_unread_cursors():
    result = SiteDiscoveryService(Graph()).run(request(max_facility_pages=1))
    assert len(result.candidates) == 3
    assert not result.coverage["all_facility_pages_read"]
    assert all(row["coverage"]["next_cursor"] for row in result.facility_searches)
    assert all(
        row["coverage"]["collected_registry_candidates"] == 2 for row in result.facility_searches
    )
    assert all(
        row["coverage"]["total_registry_candidates"] == 3 for row in result.facility_searches
    )


@pytest.mark.parametrize(
    "field,value", [("site_id", "other-site"), ("latitude", 37.3), ("organisation_id", "other-org")]
)
def test_another_origin_changes_the_investigation_context_hash(field, value):
    original = request()
    changed = request(origin={**original.origin.model_dump(), field: value})
    service = SiteDiscoveryService(Graph())
    assert service.run(original).context_sha256 != service.run(changed).context_sha256


@pytest.mark.parametrize(
    "field",
    [
        "generation",
        "reference_sha256",
        "registry_generation",
        "registry_source_sha256",
        "codebook_sha256",
    ],
)
def test_new_process_or_registry_versions_are_never_mixed(field):
    def corrupt(page, call):
        if call == 2:
            page.graph_trace[field] = "changed"

    with pytest.raises(GraphStoreError):
        SiteDiscoveryService(Graph(corrupt)).run(request())


@pytest.mark.parametrize("fault", ["count", "duplicate", "cursor", "process", "empty"])
def test_inconsistent_facility_pages_fail_closed(fault):
    def corrupt(page, call):
        if call != 2:
            return
        if fault == "count":
            page.pagination["total_registry_candidates"] += 1
        elif fault == "duplicate":
            page.registry_candidates[0]["listing_id"] = "0"
        elif fault == "cursor":
            page.pagination["has_more"] = True
        elif fault == "process":
            page.transition_key = "another-process"
        elif fault == "empty":
            page.registry_candidates = []
            page.pagination["returned_count"] = 0

    with pytest.raises(GraphStoreError):
        SiteDiscoveryService(Graph(corrupt)).run(request())


def test_distance_order_is_applied_before_pagination_and_missing_points_stay_last():
    rows = [
        normalized(1, LAT="38.0", LOT="127.0", FLCT_EQU_NM="분쇄시설"),
        normalized(2, LAT="37.2", LOT="127.0", FLCT_EQU_NM=""),
        normalized(3, LAT="", LOT="", FLCT_EQU_NM="분쇄시설"),
    ]
    common = {"transition_key": "pp-prepare", "origin_latitude": 37.2, "origin_longitude": 127.0}
    default, _ = registry_matches(rows, "pp", FacilitySearchRequest(**common))
    nearest, _ = registry_matches(
        rows, "pp", FacilitySearchRequest(**common, sort_by="straight_line_distance")
    )
    assert default[0][0]["source_row"] == 1
    assert [row[0]["source_row"] for row in nearest] == [2, 1, 3]
    assert nearest[-1][2] is None
    assert not nearest[0][0]["process_capability_verified"]


def test_distance_sort_requires_origin_and_coordinates_cannot_be_swapped():
    with pytest.raises(ValidationError):
        FacilitySearchRequest(transition_key="pp-prepare", sort_by="straight_line_distance")
    payload = request().model_dump(mode="json")
    payload["origin"]["latitude"], payload["origin"]["longitude"] = 127, 37
    with pytest.raises(ValidationError):
        SiteDiscoveryRequest.model_validate(payload)


def test_http_contract_is_additive_and_never_calls_a_generation_model():
    previous = deepcopy(app.dependency_overrides)
    app.dependency_overrides[get_graph_query] = lambda: Graph()
    try:
        client = TestClient(app)
        response = client.post("/analysis/site-discovery", json=request().model_dump(mode="json"))
        assert response.status_code == 200
        assert response.json()["contract_version"] == "site-facility-discovery-v1"
        assert response.json()["origin_basis"] == "server_supplied_not_independently_verified"
        assert (
            client.post("/analysis/site-discovery", json={"lot": {"material": "PP"}}).status_code
            == 422
        )
        payload = request().model_dump(mode="json")
        payload["legal_graph"] = {"query": "must not call a model"}
        assert client.post("/analysis/site-discovery", json=payload).status_code == 422
    finally:
        app.dependency_overrides = previous
