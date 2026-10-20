"""Real registry generations and process-scoped facility discovery, with synthetic firms."""

import hashlib
import json
import os
import sqlite3
from dataclasses import replace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from resource_ai.api.schemas.facility_search import FacilitySearchRequest
from resource_ai.api.schemas.kg_planning import PathPlanningRequest
from resource_ai.core.dependencies import get_graph_query
from resource_ai.knowledge_graph import facility_registry as registry
from resource_ai.knowledge_graph import graph_store as store
from resource_ai.knowledge_graph.builder import build_stage
from resource_ai.knowledge_graph.facility_search import FacilitySearch
from resource_ai.knowledge_graph.planning import ProcessPlanner
from resource_ai.knowledge_graph.query import GraphQuery
from resource_ai.main import app
from tests.unit.test_facility_search import national_row

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_NEO4J_INTEGRATION") != "true", reason="Real Neo4j required"
)


@pytest.fixture(scope="module")
def facility_graph(tmp_path_factory):
    config = replace(store.settings(), namespace="resource-facility-test-" + uuid4().hex[:10])
    path = tmp_path_factory.mktemp("registry") / "synthetic-national.json"
    rows = [
        national_row(FLCT_NM="가상 PP 시설"),
        national_row(
            FLCT_NM="가상 범용 시설",
            TRET_WAS_INF="폐합성수지",
            LCTN_ROAD_NM_ADDR="",
            LAT="",
            LOT="",
        ),
        national_row(
            FLCT_NM="가상 코드 시설",
            TRET_WAS_INF="51-03-01",
            LCTN_ROAD_NM_ADDR="부산 가상길",
            CRTR_YMD="2023-07-14",
        ),
        national_row(FLCT_NM="가상 운반사", BUS_NM="폐기물수집및운반업"),
        national_row(FLCT_NM="가상 종이 시설", TRET_WAS_INF="폐지"),
    ]
    path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf8")
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    stage = sqlite3.connect(":memory:")
    build_stage(stage, [], include_curated=True)
    store.import_graph(stage, config=config)
    try:
        yield config, path, sha
    finally:
        assert config.namespace.startswith("resource-facility-test-")
        with store.get_driver(config).session(database=config.database) as session:
            session.run(
                "MATCH (n) WHERE n.space=$space DETACH DELETE n", space=config.namespace
            ).consume()
        stage.close()


def lookup(config, **kwargs):
    return FacilitySearch(config).search(
        FacilitySearchRequest(transition_key="pp-prepare", **kwargs)
    )


def test_01_missing_registry_keeps_existing_sourced_bindings(facility_graph):
    config, _, _ = facility_graph
    response = lookup(config)
    assert response.status == "reference_only"
    assert response.registry_candidates == []
    assert (
        response.reference_bindings[0]["research_packet"]["assessment"]["published_site_process"]
        == "source_support"
    )
    assert (
        response.reference_bindings[0]["research_packet"]["assessment"]["current_acceptance"]
        == "needs_verification"
    )


def test_02_import_and_lookup_preserve_roles_ambiguity_and_unknowns(facility_graph):
    config, path, sha = facility_graph
    report = registry.import_registry(path, "national-json", sha, config=config)
    response = lookup(config, page_size=100)
    assert report["record_count"] == response.search_scope["registry_record_count"] == 5
    assert len(response.registry_candidates) == 3
    assert response.search_scope["filter_counts"]["non_recycling_registration"] == 1
    assert response.search_scope["filter_counts"]["no_material_or_code_hit"] == 1
    match_types = [i["research_packet"]["material_match"] for i in response.registry_candidates]
    assert match_types[0] == "material_label_only"
    # Text/category-code hints share a bucket; their source-derived IDs break ties.
    assert set(match_types[1:]) == {
        "broad_category_only",
        "code_category_only",
    }
    assert response.search_scope["automatic_path_insertion"] is False
    assert response.search_scope["match_counts"] == {
        "material_label_only": 1,
        "broad_category_only": 1,
        "code_category_only": 1,
    }
    for item in response.registry_candidates:
        packet = item["research_packet"]
        assert packet["source_sha256"] == sha
        assert packet["stage_capability"] == "not_established"
        assert packet["current_acceptance"] is packet["current_permit_scope"] is None
        assert packet["road_distance_km"] is packet["transport_cost_krw"] is None
        assert packet["process_capability_verified"] is False


def test_03_lookup_does_not_insert_registry_companies_into_existing_plans(facility_graph):
    config, _, _ = facility_graph
    page = ProcessPlanner(config).search(PathPlanningRequest(material="PP", input_form="scrap"))
    assert len(page.items) == 3
    for item in page.items:
        for stage in item.research_packet["stages"]:
            assert not stage["facility"]["label"].startswith("가상")
            assert stage["facility_assessment"]["ready_for_dispatch"] is False
            assert stage["facility_search_request"]["transition_key"] == stage["transition"]["key"]


def test_03b_every_pp_plan_stage_can_request_facility_research_without_approval(facility_graph):
    config, _, _ = facility_graph
    page = ProcessPlanner(config).search(PathPlanningRequest(material="PP", input_form="scrap"))
    requests = {
        stage["transition"]["key"]: stage["facility_search_request"]
        for item in page.items
        for stage in item.research_packet["stages"]
    }
    assert set(requests) == {"pp-prepare", "pp-clean", "pp-pelletise", "pp-compound"}
    for key, payload in requests.items():
        result = FacilitySearch(config).search(FacilitySearchRequest.model_validate(payload))
        assert result.transition_key == key and result.material == "pp"
        assert result.pagination["total_registry_candidates"] == 3
        assert result.dispatch_approved is False


def test_04_region_and_straight_line_distance_preserve_missing_location(facility_graph):
    config, _, _ = facility_graph
    response = lookup(config, region="충남", origin_latitude=36.7, origin_longitude=126.8)
    packets = [r["research_packet"] for r in response.registry_candidates]
    assert len(packets) == 2
    assert packets[0]["straight_line_distance_km"] == 0.0
    assert packets[0]["road_distance_km"] is None
    assert packets[1]["region_match"] == "unknown"
    assert packets[1]["straight_line_distance_km"] is None


def test_05_code_lookup_is_exact_without_claiming_polymer_compatibility(facility_graph):
    config, _, _ = facility_graph
    response = lookup(config, waste_code="51-03-01")
    assert len(response.registry_candidates) == 1
    assert (
        response.registry_candidates[0]["research_packet"]["material_match"]
        == "requested_code_not_material_validated"
    )


def test_06_cursor_pages_cover_every_listing_once_and_bind_query(facility_graph):
    config, _, _ = facility_graph
    first = lookup(config, page_size=1)
    second = lookup(config, page_size=1, cursor=first.pagination["next_cursor"])
    third = lookup(config, page_size=1, cursor=second.pagination["next_cursor"])
    ids = [p.registry_candidates[0]["listing_id"] for p in [first, second, third]]
    assert len(set(ids)) == 3 and third.pagination["next_cursor"] is None
    with pytest.raises(store.GraphStoreError) as caught:
        lookup(config, region="다른 지역", cursor=first.pagination["next_cursor"])
    assert caught.value.code == "invalid-cursor"


def test_07_external_registry_content_is_withheld_but_reference_evidence_remains(facility_graph):
    config, _, _ = facility_graph
    result = lookup(config, audience="external_model", page_size=100)
    assert all(i["research_packet"] is None and i["withheld"] for i in result.registry_candidates)
    assert result.reference_bindings[0]["research_packet"] is not None
    assert "가상 PP 시설" not in result.model_dump_json()


def test_08_changed_registry_generation_invalidates_old_cursor(facility_graph):
    config, path, sha = facility_graph
    cursor = lookup(config, page_size=1).pagination["next_cursor"]
    registry.import_registry(path, "national-json", sha, config=config)
    with pytest.raises(store.GraphStoreError) as caught:
        lookup(config, page_size=1, cursor=cursor)
    assert caught.value.code == "stale-cursor"


def test_08b_changed_code_dictionary_invalidates_old_cursor(facility_graph, monkeypatch):
    from resource_ai.knowledge_graph import facility_search

    config, _, _ = facility_graph
    cursor = lookup(config, page_size=1).pagination["next_cursor"]
    book = facility_search.load_codebook()
    monkeypatch.setattr(facility_search, "load_codebook", lambda: {**book, "sha256": "b" * 64})
    with pytest.raises(store.GraphStoreError) as caught:
        lookup(config, page_size=1, cursor=cursor)
    assert caught.value.code == "stale-cursor"


@pytest.mark.parametrize("corruption", ["record_json", "projection", "missing_record"])
def test_09_registry_tampering_is_blocked_and_owned_import_can_repair_it(
    facility_graph, corruption
):
    config, path, sha = facility_graph
    active = store._read(config, lambda tx: registry.read_registry(tx, config))
    listing = active["records"][0]["listing_id"]
    with store.get_driver(config).session(database=config.database) as session:
        selector = "MATCH (n:ReSourceFacilityListing {space:$space,generation:$generation,listing_id:$listing}) "
        params = {"space": config.namespace, "generation": active["generation"], "listing": listing}
        if corruption == "record_json":
            session.run(selector + "SET n.record_json='{}'", **params).consume()
        elif corruption == "projection":
            session.run(selector + "SET n.owner='foreign'", **params).consume()
        else:
            session.run(selector + "DELETE n", **params).consume()
    with pytest.raises(store.GraphStoreError) as caught:
        lookup(config)
    assert caught.value.code == "registry-integrity"
    registry.import_registry(path, "national-json", sha, config=config)
    assert len(lookup(config).registry_candidates) == 3


def test_10_failed_new_generation_never_replaces_active_registry(facility_graph, monkeypatch):
    config, path, sha = facility_graph
    original = lookup(config).graph_trace["registry_generation"]
    read = registry.read_registry

    def fail_staging(tx, cfg, generation=None):
        if generation:
            raise registry.registry_error()
        return read(tx, cfg, generation)

    monkeypatch.setattr(registry, "read_registry", fail_staging)
    with pytest.raises(store.GraphStoreError):
        registry.import_registry(path, "national-json", sha, config=config)
    assert lookup(config).graph_trace["registry_generation"] == original


def test_11_real_api_contract_and_errors(facility_graph):
    config, _, _ = facility_graph
    previous = dict(app.dependency_overrides)
    app.dependency_overrides[get_graph_query] = lambda: GraphQuery(config)
    try:
        client = TestClient(app)
        response = client.post("/kg/facility-candidates", json={"transition_key": "pp-prepare"})
        assert response.status_code == 200
        assert response.json()["dispatch_approved"] is False
        assert (
            client.post(
                "/kg/facility-candidates", json={"transition_key": "not-imported"}
            ).status_code
            == 400
        )
        assert (
            client.post(
                "/kg/facility-candidates", json={"transition_key": "pp-prepare", "cursor": "%%%"}
            ).status_code
            == 400
        )
        assert (
            client.post(
                "/kg/facility-candidates",
                json={
                    "transition_key": "pp-prepare",
                    "audience": "external_model",
                    "region": "private",
                },
            ).status_code
            == 422
        )
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)
