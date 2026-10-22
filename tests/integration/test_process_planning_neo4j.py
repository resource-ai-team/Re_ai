"""Real Bolt coverage for typed process hypotheses, evidence and facility boundaries."""

import os
import sqlite3
from contextlib import contextmanager
from dataclasses import replace
from itertools import pairwise
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from resource_ai.api.schemas.kg_planning import PathPlanningRequest
from resource_ai.core.config import get_settings
from resource_ai.core.dependencies import get_graph_query
from resource_ai.knowledge_graph import graph_store as store
from resource_ai.knowledge_graph.builder import build_stage
from resource_ai.knowledge_graph.planning import ProcessPlanner, decode_cursor, encode_cursor
from resource_ai.knowledge_graph.planning_reference import node_id
from resource_ai.knowledge_graph.query import GraphQuery
from resource_ai.main import app

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_NEO4J_INTEGRATION") != "true", reason="Opt-in real Neo4j server required"
)


@contextmanager
def isolated_graph(*, include_curated=True):
    config = replace(store.settings(), namespace="resource-planning-test-" + uuid4().hex[:12])
    connection = sqlite3.connect(":memory:")
    try:
        # No literature rows: plans must come from imported typed transitions and bindings.
        build_stage(connection, [], include_curated=include_curated)
        imported = store.import_graph(connection, config=config)
        yield config, connection, imported
    finally:
        try:
            # Delete only the fresh namespace owned by this test, including old generations.
            with store.get_driver(config).session(database=config.database) as session:
                session.run(
                    "MATCH (n:ReSourceNode {space:$space}) DETACH DELETE n", space=config.namespace
                ).consume()
                session.run(
                    "MATCH (n:ReSourceDataset {space:$space}) DELETE n", space=config.namespace
                ).consume()
                session.run(
                    "MATCH (n:ReSourceGraph {space:$space}) DELETE n", space=config.namespace
                ).consume()
        finally:
            connection.close()


@pytest.fixture(scope="module")
def planning_graph():
    with isolated_graph() as imported:
        yield imported


@contextmanager
def graph_client(config):
    overrides = dict(app.dependency_overrides)
    app.dependency_overrides[get_graph_query] = lambda: GraphQuery(config)
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(overrides)


def all_pages(config, **context):
    planner, cursor, items, pages = ProcessPlanner(config), None, [], []
    for _ in range(200):
        page = planner.search(PathPlanningRequest(**context, cursor=cursor))
        items.extend(page.items)
        pages.append(page)
        cursor = page.pagination.next_cursor
        if cursor is None:
            return items, pages
    raise AssertionError("Planning pagination did not terminate")


def assert_unassessed(page):
    assert page.dispatch_approved is False
    assert page.search_scope["ranking_applied"] is False
    for item in page.items:
        assert item.state == "graph_compatible"
        assert item.dispatch_approved is False
        assert item.recommended is False
        packet = item.research_packet
        if packet is None:
            continue
        assert packet["input_state_specifications_verified"] is False
        assert packet["origin_transport"] == {"state": "not_assessed", "distance_km": None}
        assert packet["legal"]["state"] == "not_assessed"
        assert packet["cost"] == {"state": "not_evaluated", "total_krw": None}
        assert packet["carbon"] == {"state": "not_evaluated", "total_kg_co2e": None}
        assert {"cost", "carbon", "recommendation"} <= set(packet["not_established"])
        assert all(stage["suitability"] == "not_assessed" for stage in packet["stages"])
        assert all(check["state"] == "needs_verification" for check in packet["checks"])
        for stage in packet["stages"]:
            for field in (
                "current_permit_scope",
                "current_available_capacity_kg",
                "current_acceptance",
                "current_price_krw_per_kg",
            ):
                assert stage["facility"][field] is None
            assert all(
                metric["scope"] == "website-reported-design-capacity-not-current-available"
                for metric in stage["facility"]["reported_metrics"]
            )
        for leg in packet["handoffs"]:
            assert leg["handover_confirmed"] is False
            assert leg["distance_km"] is None
            assert leg["transport_cost_krw"] is None
            assert leg["transport_kg_co2e"] is None


def test_live_coverage_api_reads_imported_reference_and_keeps_site_gaps(planning_graph):
    config, _, imported = planning_graph
    with graph_client(config) as client:
        response = client.get("/kg/planning-coverage")
    assert response.status_code == 200
    data = response.json()
    assert data["backend"] == "neo4j"
    assert data["graph_trace"]["generation"] == imported["generation"]
    assert data["counts"] == {
        "sources": 9,
        "states": 23,
        "transitions": 18,
        "organisations": 5,
        "facilities": 7,
        "capabilities": 18,
    }
    assert data["facilities_without_transition_keys"] == ["bgf-incheon"]
    assert data["dispatch_approved"] is False


def test_live_paper_path_uses_the_published_ten_step_order(planning_graph):
    config, _, _ = planning_graph
    with graph_client(config) as client:
        response = client.post(
            "/kg/assessed-plans", json={"material": "종이", "input_form": "cardboard"}
        )
    assert response.status_code == 200
    data = response.json()
    (plan,) = data["planning"]["items"]
    stages = plan["research_packet"]["stages"]
    assert len(stages) == 5
    assert [step for s in stages for step in s["transition"]["operation_steps"]] == [
        "해리",
        "정선",
        "농축",
        "고해",
        "지필형성",
        "압착탈수",
        "건조",
        "광택",
        "권취",
        "재단",
    ]
    assert {s["facility"]["key"] for s in stages} == {"hansol-damyang"}
    assert plan["recommended"] is False


def test_live_pe_grade_stays_unverified_in_paths_and_facility_risks(planning_graph):
    config, _, _ = planning_graph
    with graph_client(config) as client:
        response = client.post(
            "/kg/assessed-plans", json={"material": "HDPE", "input_form": "film"}
        )
    assert response.status_code == 200
    data = response.json()
    scope = data["planning"]["search_scope"]["material_scope"]
    assert scope["supplied_subtype"] == "hdpe"
    assert scope["subtype_applicability_verified"] is False
    for assessment in data["assessments"]:
        first = assessment["stages"][0]["assessment"]
        assert any(
            c["code"] == "material_subtype" and c["status"] == "needs_verification"
            for c in first["checks"]
        )


@pytest.mark.parametrize(
    ("material", "input_form", "goal", "transitions", "facilities", "handoffs", "bindings"),
    [
        (
            "PP",
            "scrap",
            "pp-compound",
            ["pp-prepare", "pp-clean", "pp-pelletise", "pp-compound"],
            ["jungang-gongju", "bgf-yesan", "bgf-yesan", "bgf-hwaseong"],
            ["external_handover_candidate", "within_site_candidate", "external_handover_candidate"],
            ["published_site_process", *["site_role_hypothesis"] * 3],
        ),
        (
            "PET",
            "bottle",
            "pet-pellet",
            ["pet-prepare", "pet-wash", "pet-finish", "pet-pelletise"],
            ["samyang-sihwa", "samyang-sihwa", "samyang-sihwa", "bgf-yesan"],
            ["within_site_candidate", "within_site_candidate", "external_handover_candidate"],
            [*["published_site_process"] * 3, "site_role_hypothesis"],
        ),
    ],
)
def test_real_graph_chains_preserve_state_facility_role_and_unconfirmed_handoffs(
    planning_graph, material, input_form, goal, transitions, facilities, handoffs, bindings
):
    config, _, imported = planning_graph
    page = ProcessPlanner(config).search(
        PathPlanningRequest(
            material=material, input_form=input_form, goal_state=goal, quantity_kg=3000
        )
    )
    assert page.status == "plans"
    assert page.graph_trace["generation"] == imported["generation"]
    assert page.pagination.enumerated_plans == 1
    (item,) = page.items
    packet = item.research_packet
    assert packet["sequence_basis"] == "explicit-curated-state-transitions"
    assert packet["input_form_assumed"] is False
    stages = packet["stages"]
    assert [s["transition"]["key"] for s in stages] == transitions
    assert [s["facility"]["key"] for s in stages] == facilities
    assert [s["facility_binding"] for s in stages] == bindings
    assert [leg["kind"] for leg in packet["handoffs"]] == handoffs
    for stage in stages:
        assert stage["capability"]["facility"] == stage["facility"]["key"]
        assert stage["capability"]["transition"] == stage["transition"]["key"]
        assert stage["facility"]["organisation"] == stage["organisation"]["key"]
        assert stage["transition"]["input_state"] == stage["input_state"]["key"]
        assert stage["transition"]["output_state"] == stage["output_state"]["key"]
    for before, after in pairwise(stages):
        assert before["output_state"] == after["input_state"]
    targets = {s["stage_id"] for s in stages} | {leg["leg_id"] for leg in packet["handoffs"]}
    assert all(check["target_id"] in targets for check in packet["checks"])
    evidence_keys = {e["key"] for e in packet["evidence"]}
    for stage in stages:
        for key in (
            "transition",
            "input_state",
            "output_state",
            "capability",
            "facility",
            "organisation",
        ):
            assert set(stage[key]["source_ids"]) <= evidence_keys
    assert all(set(check["source_ids"]) <= evidence_keys for check in packet["checks"])
    assert "bgf-incheon" not in facilities
    assert_unassessed(page)


def test_known_aluminium_form_mismatch_has_no_fallback_plan(planning_graph):
    config, _, _ = planning_graph
    mismatch = ProcessPlanner(config).search(
        PathPlanningRequest(material="aluminium", input_form="scrap")
    )
    assert mismatch.status == "no_plans"
    assert mismatch.items == []
    assert mismatch.pagination.enumerated_plans == 0
    assert mismatch.search_scope["normalized_form"] == "scrap"
    assert mismatch.search_scope["entry_state_keys"] == []
    assert mismatch.search_scope["entry_state_not_covered"] is True
    assert_unassessed(mismatch)
    matched = ProcessPlanner(config).search(
        PathPlanningRequest(material="aluminium", input_form="캔")
    )
    assert matched.status == "plans"
    assert matched.search_scope["entry_state_keys"] == ["al-used-can"]
    assert matched.items[0].research_packet["stages"][-1]["output_state"]["key"] == "al-sheet"
    assert_unassessed(matched)


def test_all_pages_enumerate_each_imported_pp_path_once_without_top_k(planning_graph):
    config, _, imported = planning_graph
    items, pages = all_pages(config, material="PP", input_form="scrap", page_size=1)
    sequences = [tuple(s["transition"]["key"] for s in i.research_packet["stages"]) for i in items]
    assert set(sequences) == {
        ("pp-prepare",),
        ("pp-prepare", "pp-clean", "pp-pelletise"),
        ("pp-prepare", "pp-clean", "pp-pelletise", "pp-compound"),
    }
    assert len(items) == len({i.plan_id for i in items}) == 3
    assert len(pages) == 3
    assert all(p.pagination.enumerated_plans == 3 for p in pages)
    assert all(p.pagination.returned_count == 1 for p in pages)
    assert all(p.pagination.complete_in_this_response is False for p in pages)
    assert all(p.graph_trace["generation"] == imported["generation"] for p in pages)
    assert all(p.search_scope["all_paths_enumerated"] is True for p in pages)
    assert all(p.search_scope["budget_exhausted"] is False for p in pages)
    assert pages[-1].pagination.has_more is False
    for page in pages:
        assert_unassessed(page)
    one_page = ProcessPlanner(config).search(
        PathPlanningRequest(material="PP", input_form="scrap", page_size=100)
    )
    assert [item.plan_id for item in one_page.items] == [item.plan_id for item in items]
    assert one_page.pagination.complete_in_this_response is True


@pytest.mark.parametrize(
    "changes",
    [
        {"material": "PET"},
        {"input_form": "pellet"},
        {"color": "black"},
        {"quantity_kg": 100},
        {"region": "private-location"},
        {"goal_state": "pp-compound"},
        {"max_steps": 3},
        {"audience": "external_model"},
    ],
)
def test_cursor_rejects_changes_to_every_planning_context(planning_graph, changes):
    config, _, _ = planning_graph
    context = {"material": "PP", "input_form": "scrap"}
    planner = ProcessPlanner(config)
    first = planner.search(PathPlanningRequest(**context, page_size=1))
    token = first.pagination.next_cursor
    assert token
    with pytest.raises(store.GraphStoreError) as error:
        planner.search(PathPlanningRequest(**{**context, **changes}, cursor=token))
    assert error.value.code == "invalid-cursor"


def test_cursor_preserves_namespace_and_allows_page_size_changes(planning_graph):
    config, _, _ = planning_graph
    planner = ProcessPlanner(config)
    context = {"material": "PP", "input_form": "scrap"}
    first = planner.search(PathPlanningRequest(**context, page_size=1))
    token = first.pagination.next_cursor
    assert token
    other_space = {**decode_cursor(token), "namespace": config.namespace + "-other"}
    with pytest.raises(store.GraphStoreError) as error:
        planner.search(PathPlanningRequest(**context, cursor=encode_cursor(other_space)))
    assert error.value.code == "invalid-cursor"
    remaining = planner.search(PathPlanningRequest(**context, cursor=token, page_size=100))
    assert remaining.pagination.has_more is False
    assert len(remaining.items) == 2
    assert first.items[0].plan_id not in {item.plan_id for item in remaining.items}


def test_external_model_view_keeps_plan_ids_but_excludes_private_input(planning_graph):
    config, _, _ = planning_graph
    context = {
        "material": "PP",
        "input_form": "scrap",
        "region": "private-factory-location-19582",
        "generation_process": "private-production-process-48219",
        "current_treatment": "private-current-treatment-74621",
        "quantity_kg": 1234,
        "page_size": 1,
    }
    internal, _ = all_pages(config, **context)
    external, pages = all_pages(config, **context, audience="external_model")
    assert [item.plan_id for item in internal] == [item.plan_id for item in external]
    assert all(page.input_context == {} for page in pages)
    for page in pages:
        serialized = page.model_dump_json()
        for key in ("region", "generation_process", "current_treatment"):
            assert context[key] not in serialized
        assert_unassessed(page)
    for item in external:
        if item.research_packet is None:
            assert item.external_model_allowed is False
            assert item.withheld_evidence_ids
        else:
            assert item.external_model_allowed is True
            assert all(
                e["model_transfer_allowed"] is True for e in item.research_packet["evidence"]
            )


def test_real_generation_replacement_invalidates_service_and_api_cursor():
    with isolated_graph() as (config, connection, imported):
        planner = ProcessPlanner(config)
        context = {"material": "PP", "input_form": "scrap", "page_size": 1}
        first = planner.search(PathPlanningRequest(**context))
        token = first.pagination.next_cursor
        assert token
        replaced = store.import_graph(connection, config=config, rebuild=True)
        assert replaced["generation"] != imported["generation"]
        with pytest.raises(store.GraphStoreError) as error:
            planner.search(PathPlanningRequest(**context, cursor=token))
        assert error.value.code == "stale-cursor"
        with graph_client(config) as client:
            stale = client.post("/kg/process-plans", json={**context, "cursor": token})
            assert stale.status_code == 409
            assert stale.json()["detail"]["code"] == "stale-cursor"
            fresh = client.post("/kg/process-plans", json=context)
            assert fresh.status_code == 200
            assert fresh.json()["graph_trace"]["generation"] == replaced["generation"]


def test_import_without_curated_corpus_fails_closed_in_service_and_api():
    with isolated_graph(include_curated=False) as (config, _, _):
        with pytest.raises(store.GraphStoreError) as error:
            ProcessPlanner(config).search(PathPlanningRequest(material="PP"))
        assert error.value.code == "planning-not-imported"
        with graph_client(config) as client:
            response = client.post("/kg/process-plans", json={"material": "PP"})
            assert response.status_code == 503
            assert response.json()["detail"]["code"] == "planning-not-imported"


@pytest.mark.parametrize(
    ("relation", "source_kind", "source_key", "target_kind", "original_key", "wrong_key"),
    [
        (
            "AT_FACILITY",
            "FacilityCapability",
            "bgf-yesan--pp-pelletise",
            "Facility",
            "bgf-yesan",
            "bgf-incheon",
        ),
        (
            "FOR_TRANSITION",
            "FacilityCapability",
            "samyang-sihwa--pet-prepare",
            "ProcessTransition",
            "pet-prepare",
            "pet-pelletise",
        ),
        (
            "SUPPORTED_BY",
            "ProcessTransition",
            "pet-prepare",
            "Evidence",
            "samyang-pet",
            "bgf-recycling",
        ),
        (
            "FROM_SOURCE",
            "Evidence",
            "samyang-pet",
            "Source",
            "samyang-pet",
            "bgf-recycling",
        ),
    ],
)
def test_actual_capability_and_evidence_bindings_are_checked_even_with_intact_json(
    planning_graph, relation, source_kind, source_key, target_kind, original_key, wrong_key
):
    config, _, imported = planning_graph
    params = {
        "space": config.namespace,
        "generation": imported["generation"],
        "source": node_id(source_kind, source_key),
        "original": node_id(target_kind, original_key),
        "wrong": node_id(target_kind, wrong_key),
    }
    with store.get_driver(config).session(database=config.database) as session:
        edge = session.run(
            f"""MATCH (s:ReSourceNode {{space:$space,generation:$generation,id:$source}})
            -[r:{relation}]->(t:ReSourceNode {{space:$space,generation:$generation,id:$original}})
            RETURN properties(r) AS properties""",
            **params,
        ).single()
        assert edge
        params["edge_properties"] = dict(edge["properties"])
        try:
            session.run(
                f"""MATCH (s:ReSourceNode {{space:$space,generation:$generation,id:$source}})
                -[r:{relation}]->(t:ReSourceNode {{space:$space,generation:$generation,id:$original}}),
                (wrong:ReSourceNode {{space:$space,generation:$generation,id:$wrong}})
                DELETE r CREATE (s)-[replacement:{relation}]->(wrong)
                SET replacement=$edge_properties""",
                **params,
            ).consume()
            with pytest.raises(store.GraphStoreError) as error:
                ProcessPlanner(config).search(PathPlanningRequest(material="PET"))
            assert error.value.code == "integrity"
        finally:
            session.run(
                f"""MATCH (s:ReSourceNode {{space:$space,generation:$generation,id:$source}})
                -[r:{relation}]->(wrong:ReSourceNode {{space:$space,generation:$generation,id:$wrong}}),
                (original:ReSourceNode {{space:$space,generation:$generation,id:$original}})
                DELETE r CREATE (s)-[replacement:{relation}]->(original)
                SET replacement=$edge_properties""",
                **params,
            ).consume()
    assert ProcessPlanner(config).search(PathPlanningRequest(material="PET")).status == "plans"


@pytest.mark.parametrize(
    ("relation", "source_kind", "source_key", "target"),
    [
        (
            "SUPPORTED_BY",
            "ProcessTransition",
            "pet-prepare",
            node_id("Evidence", "samyang-pet"),
        ),
        (
            "AT_FACILITY",
            "FacilityCapability",
            "samyang-sihwa--pet-prepare",
            node_id("Facility", "samyang-sihwa"),
        ),
        ("FOR_MATERIAL", "ProcessTransition", "pet-prepare", "material:pet"),
    ],
)
def test_matching_endpoint_properties_cannot_substitute_an_unlabelled_decoy_node(
    planning_graph, relation, source_kind, source_key, target
):
    config, _, imported = planning_graph
    params = {
        "space": config.namespace,
        "generation": imported["generation"],
        "source": node_id(source_kind, source_key),
        "target": target,
    }
    with store.get_driver(config).session(database=config.database) as session:
        original = session.run(
            f"""MATCH (s:ReSourceNode {{space:$space,generation:$generation,id:$source}})
            -[r:{relation}]->(t:ReSourceNode {{space:$space,generation:$generation,id:$target}})
            RETURN properties(r) AS edge,properties(t) AS target""",
            **params,
        ).single()
        assert original
        params["edge_properties"] = dict(original["edge"])
        params["target_properties"] = dict(original["target"])
        # An unlabeled node is outside the ReSourceNode uniqueness constraint.
        # Its full property map intentionally matches the real imported node.
        decoy = session.run(
            f"""MATCH (s:ReSourceNode {{space:$space,generation:$generation,id:$source}})
            -[r:{relation}]->(:ReSourceNode {{space:$space,generation:$generation,id:$target}})
            CREATE (decoy) SET decoy=$target_properties
            CREATE (s)-[replacement:{relation}]->(decoy)
            SET replacement=$edge_properties DELETE r
            RETURN elementId(decoy) AS id,labels(decoy) AS labels,properties(decoy) AS fields""",
            **params,
        ).single()
        assert decoy
        params["decoy_id"] = decoy["id"]
        try:
            assert decoy["labels"] == []
            assert dict(decoy["fields"]) == params["target_properties"]
            with pytest.raises(store.GraphStoreError) as error:
                ProcessPlanner(config).search(PathPlanningRequest(material="PET"))
            assert error.value.code == "integrity"
        finally:
            session.run(
                f"""MATCH (s:ReSourceNode {{space:$space,generation:$generation,id:$source}})
                -[replacement:{relation}]->(decoy),
                (original:ReSourceNode {{space:$space,generation:$generation,id:$target}})
                WHERE elementId(decoy)=$decoy_id
                DELETE replacement CREATE (s)-[restored:{relation}]->(original)
                SET restored=$edge_properties DETACH DELETE decoy""",
                **params,
            ).consume()
    assert ProcessPlanner(config).search(PathPlanningRequest(material="PET")).status == "plans"


@pytest.mark.parametrize(
    ("kind", "key", "field", "tampered"),
    [
        ("FacilityCapability", "bgf-yesan--pp-pelletise", "dispatch_approved", True),
        ("Evidence", "samyang-pet", "model_transfer_allowed", False),
        ("Source", "samyang-pet", "source_key", "planning:bgf-recycling"),
    ],
)
def test_scalar_projection_tampering_fails_closed_without_changing_record_json(
    planning_graph, kind, key, field, tampered
):
    config, _, imported = planning_graph
    params = {
        "space": config.namespace,
        "generation": imported["generation"],
        "id": node_id(kind, key),
    }
    with store.get_driver(config).session(database=config.database) as session:
        row = session.run(
            """MATCH (n:ReSourceNode {space:$space,generation:$generation,id:$id})
            RETURN properties(n) AS properties""",
            **params,
        ).single()
        assert row
        original = dict(row["properties"])
        try:
            changed = session.run(
                f"""MATCH (n:ReSourceNode {{space:$space,generation:$generation,id:$id}})
                SET n.{field}=$tampered RETURN n.properties_json AS record_json""",
                **params,
                tampered=tampered,
            ).single()
            assert changed["record_json"] == original["properties_json"]
            with pytest.raises(store.GraphStoreError) as error:
                ProcessPlanner(config).search(PathPlanningRequest(material="PET"))
            assert error.value.code == "integrity"
        finally:
            session.run(
                """MATCH (n:ReSourceNode {space:$space,generation:$generation,id:$id})
                SET n=$properties""",
                **params,
                properties=original,
            ).consume()
    assert ProcessPlanner(config).search(PathPlanningRequest(material="PET")).status == "plans"


def test_api_calls_real_planner_and_returns_public_request_errors(planning_graph):
    config, _, imported = planning_graph
    with graph_client(config) as client:
        response = client.post(
            "/kg/process-plans", json={"material": "PP", "input_form": "scrap", "page_size": 1}
        )
        assert response.status_code == 200
        body = response.json()
        assert body["contract_version"] == "kg-process-plans-v1"
        assert body["graph_trace"]["generation"] == imported["generation"]
        assert body["pagination"]["has_more"] is True
        assert body["dispatch_approved"] is False
        assert body["items"][0]["recommended"] is False
        for goal in ("nonexistent-goal", "pet-pellet"):
            invalid_goal = client.post(
                "/kg/process-plans", json={"material": "PP", "goal_state": goal}
            )
            assert invalid_goal.status_code == 400
            assert invalid_goal.json()["detail"]["code"] == "invalid-goal"
        invalid_cursor = client.post("/kg/process-plans", json={"material": "PP", "cursor": "%%%"})
        assert invalid_cursor.status_code == 400
        assert invalid_cursor.json()["detail"]["code"] == "invalid-cursor"


def test_api_missing_configuration_returns_503(monkeypatch):
    # Exercise real Settings validation without exposing or changing saved credentials.
    monkeypatch.setenv("NEO4J_PASSWORD", "")
    get_settings.cache_clear()
    try:
        with graph_client(None) as client:
            response = client.post("/kg/process-plans", json={"material": "PP"})
        assert response.status_code == 503
        assert response.json()["detail"]["code"] == "not-configured"
    finally:
        get_settings.cache_clear()
