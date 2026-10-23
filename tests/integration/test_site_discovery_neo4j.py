"""Actual isolated Neo4j plans/registry with synthetic source firms and origin."""

import math
import os

import pytest

from resource_ai.agents.site_discovery import SiteDiscoveryService
from resource_ai.api.schemas.facility_search import FacilitySearchRequest
from resource_ai.knowledge_graph import facility_registry as registry
from resource_ai.knowledge_graph.facility_search import FacilitySearch
from resource_ai.knowledge_graph.graph_store import GraphStoreError
from resource_ai.knowledge_graph.query import GraphQuery
from tests.integration.test_facility_search_neo4j import facility_graph  # noqa: F401
from tests.unit.test_site_discovery import request

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_NEO4J_INTEGRATION") != "true", reason="Real Neo4j required"
)


def test_actual_registry_and_paths_keep_distance_and_source_roles_separate(facility_graph):  # noqa: F811
    config, path, sha = facility_graph
    registry.import_registry(path, "national-json", sha, config=config)
    result = SiteDiscoveryService(GraphQuery(config)).run(request())
    assert len(result.candidates) == 3
    assert len(result.facility_searches) == 4
    assert result.coverage["all_facility_pages_read"]
    assert result.graph_trace["generation"]
    for row in result.facility_searches:
        assert row["graph_trace"]["generation"] == result.graph_trace["generation"]
        assert row["coverage"]["total_registry_candidates"] == 3
        distances = [
            record["research_packet"]["straight_line_distance_km"]
            for record in row["registry_candidates"]
        ]
        assert distances == sorted(
            distances, key=lambda value: math.inf if value is None else value
        )
        assert distances[-1] is None
        assert all(
            record["research_packet"]["stage_capability"] == "not_established"
            for record in row["registry_candidates"]
        )
        assert row["transport_cost_krw"] is None and not row["automatic_path_insertion"]
    assert not result.dispatch_approved and result.final_recommendations == []


def test_real_facility_cursor_cannot_reuse_another_pickup_point_or_order(facility_graph):  # noqa: F811
    config, path, sha = facility_graph
    registry.import_registry(path, "national-json", sha, config=config)
    original = FacilitySearchRequest(
        transition_key="pp-prepare",
        origin_latitude=37.2,
        origin_longitude=127.0,
        page_size=1,
        sort_by="straight_line_distance",
    )
    search = FacilitySearch(config)
    first = search.search(original)
    cursor = first.pagination["next_cursor"]
    assert cursor
    for changed in ({"origin_latitude": 37.3}, {"sort_by": "relevance"}):
        with pytest.raises(GraphStoreError) as caught:
            search.search(original.model_copy(update={**changed, "cursor": cursor}))
        assert caught.value.code == "invalid-cursor"
