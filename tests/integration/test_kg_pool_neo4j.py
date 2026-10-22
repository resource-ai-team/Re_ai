"""Real Bolt tests for recall, snapshot isolation, research packets and source boundaries."""

import os
import sqlite3
from dataclasses import replace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from resource_ai.api.schemas.kg_pool import CandidatePoolRequest
from resource_ai.core.dependencies import get_graph_query
from resource_ai.knowledge_graph import graph_store as store
from resource_ai.knowledge_graph import ontology as graph
from resource_ai.knowledge_graph.builder import build_stage
from resource_ai.knowledge_graph.dataset import allowed_cards
from resource_ai.knowledge_graph.pool import CandidatePool, decode_cursor, encode_cursor
from resource_ai.knowledge_graph.query import GraphQuery
from resource_ai.main import app
from tests.unit.test_ontology_graph import extraction

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_NEO4J_INTEGRATION") != "true", reason="Opt-in real Neo4j server required"
)


@pytest.fixture(scope="module")
def research_graph():
    connection = sqlite3.connect(":memory:")
    rows = [extraction(i) for i in range(1, 8)]
    rows.append(extraction(8, waste="PP injection scrap", process="sorting, extrusion"))
    build_stage(connection, rows, include_curated=True)
    config = replace(store.settings(), namespace="resource-pool-test-" + uuid4().hex[:12])
    imported = store.import_graph(connection, config=config)
    try:
        yield config, connection, imported
    finally:
        # Only this test's randomly generated namespace is removed.
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
        connection.close()


def all_pages(config, **context):
    pool, cursor, results, pages = CandidatePool(config), None, [], []
    for _ in range(200):
        page = pool.search(CandidatePoolRequest(**context, cursor=cursor))
        results.extend(page.items)
        pages.append(page)
        cursor = page.pagination.next_cursor
        if cursor is None:
            return results, pages
    raise AssertionError("Pagination did not terminate")


def test_every_candidate_is_reachable_once_and_counts_match_independent_stage(research_graph):
    config, connection, imported = research_graph
    items, pages = all_pages(config, material="PET", page_size=3)
    literature = {
        "claim:" + r["metadata"]["candidate_id"] for r in graph.graph_candidates(connection, "pet")
    }
    local = {
        r[0]
        for r in connection.execute(
            """SELECT e.source_id FROM kg_edges e JOIN kg_nodes n ON n.id=e.source_id
            WHERE e.kind='FOR_MATERIAL' AND e.target_id='material:pet'
            AND n.kind IN ('RouteSnapshot','ScopedClaim')"""
        )
    }
    background = {
        graph.identity("scoped-claim", key)
        for key, card in allowed_cards().items()
        if card.get("material") == "multiple"
    }
    expected = literature | local | background
    actual = [item.candidate_id for item in items]
    assert set(actual) == expected
    assert len(actual) == len(set(actual)) == pages[0].pagination.total_candidates
    assert len(pages) > 1
    assert all(p.graph_trace["generation"] == imported["generation"] for p in pages)
    assert all(p.graph_trace["top_k_applied"] is False for p in pages)
    assert all(p.pagination.complete_in_this_response is False for p in pages)
    assert pages[-1].pagination.has_more is False


def test_unknown_and_different_forms_are_preserved_without_becoming_suitable(research_graph):
    config, _, _ = research_graph
    unknown, _ = all_pages(config, material="PET", input_form=None)
    different, _ = all_pages(config, material="PET", input_form="not-a-stored-form")
    assert {i.candidate_id for i in unknown} == {i.candidate_id for i in different}
    process = [i for i in unknown if i.kind == "process"]
    # Five stored snapshots; not all routes have every possible input-form variant.
    assert len(process) == 5
    assert all(i.research_packet["form_comparison"] == "input_unknown" for i in process)
    assert any(
        i.research_packet["form_comparison"] == "different_label_requires_review"
        for i in different
        if i.kind == "process"
    )
    for item in different:
        assert item.review_state == "unassessed"
        assert item.research_packet["rule_decision"] is None
        assert item.dispatch_approved is False


def test_packets_keep_conditions_risk_targets_and_citations_in_the_same_snapshot(research_graph):
    config, _, _ = research_graph
    items, _ = all_pages(config, material="PET", input_form="bottle")
    for item in items:
        packet = item.research_packet
        evidence_ids = {e["evidence_id"] for e in packet["evidence"]}
        for condition in packet["conditions"]:
            assert set(condition["evidence_ids"]) <= evidence_ids
            assert condition["machine_rule_ready"] is False
            assert condition["state"] == "unassessed"
        if item.kind != "process":
            assert packet["process_graph"] is None
            assert packet["sequence_policy"] == "no-sequence-inference"
            continue
        process = packet["process_graph"]
        route = process["route"]
        targets = {s["id"] for s in route["nodes"]} | {e["id"] for e in route["edges"]}
        assert set(process["evidence_map"].values()) <= evidence_ids
        for risk in route["risks"]:
            assert risk["target_id"] in targets
            assert risk["target_id"] in process["occurrence_ids"]
            assert {process["evidence_map"][e] for e in risk["evidence_ids"]} <= evidence_ids


def test_pp_literature_is_not_promoted_to_a_verified_sequence(research_graph):
    config, _, _ = research_graph
    items, pages = all_pages(config, material="PP", color="검정", quantity_kg=3000)
    assert pages[0].pagination.totals_by_kind["process"] == 0
    literature = [i for i in items if i.kind == "literature"]
    assert len(literature) == 1
    packet = literature[0].research_packet
    assert packet["source_record"]["metadata"]["transforming_process"] == "sorting, extrusion"
    assert packet["process_graph"] is None
    assert "cost" in packet["not_established"]
    assert pages[0].input_context["current_treatment"] is None


def test_external_view_withholds_blocked_text_and_customer_context_without_losing_ids(
    research_graph,
):
    config, _, _ = research_graph
    context = {"material": "PET", "region": "private-factory", "quantity_kg": 1234}
    internal, _ = all_pages(config, **context)
    external, pages = all_pages(config, **context, audience="external_model")
    assert {i.candidate_id for i in internal} == {i.candidate_id for i in external}
    assert all(p.input_context == {} for p in pages)
    blocked = [i for i in external if not i.external_model_allowed]
    assert blocked
    assert all(i.research_packet is None and i.withheld_evidence_ids for i in blocked)
    assert all("private-factory" not in p.model_dump_json() for p in pages)
    for item in external:
        if item.research_packet:
            assert all(
                e["properties"]["model_transfer_allowed"] is True
                for e in item.research_packet["evidence"]
            )


def test_cursor_cannot_mix_context_audience_namespace_or_generation(research_graph):
    config, _, _ = research_graph
    pool = CandidatePool(config)
    page = pool.search(CandidatePoolRequest(material="PET", page_size=1))
    token = page.pagination.next_cursor
    assert token
    for changes in ({"material": "PP"}, {"color": "black"}, {"audience": "external_model"}):
        with pytest.raises(store.GraphStoreError) as error:
            pool.search(CandidatePoolRequest(**{"material": "PET", **changes}, cursor=token))
        assert error.value.code == "invalid-cursor"
    stale = {**decode_cursor(token), "generation": "previous-generation"}
    with pytest.raises(store.GraphStoreError) as error:
        pool.search(CandidatePoolRequest(material="PET", cursor=encode_cursor(stale)))
    assert error.value.code == "stale-cursor"
    # A page-size change is safe: the cursor records the last key, never an offset.
    next_page = pool.search(CandidatePoolRequest(material="PET", cursor=token, page_size=7))
    assert page.items[0].candidate_id not in {i.candidate_id for i in next_page.items}


def test_api_uses_actual_pool_and_preserves_legacy_contract(research_graph):
    config, _, _ = research_graph
    app.dependency_overrides[get_graph_query] = lambda: GraphQuery(config)
    try:
        client = TestClient(app)
        response = client.post("/kg/candidate-pool", json={"material": "PET", "page_size": 1})
        assert response.status_code == 200
        assert response.json()["pagination"]["has_more"] is True
        bad = client.post("/kg/candidate-pool", json={"material": "PET", "cursor": "%%%"})
        assert bad.status_code == 400
        legacy = client.post("/kg/candidates", json={"material": "PET", "top_k": 2})
        assert legacy.status_code == 200
        assert len(legacy.json()["literature_candidates"]) == 2
        assert len(legacy.json()["process_candidates"]) == 2
    finally:
        app.dependency_overrides.clear()


def test_cross_route_risk_edge_is_blocked_even_if_snapshot_payload_is_intact(research_graph):
    config, _, imported = research_graph
    with store.get_driver(config).session(database=config.database) as session:
        row = session.run(
            """MATCH (risk:ReSourceNode:Risk {space:$space,generation:$generation})
            -[original_edge:TARGETS]->(original:ReSourceNode)
            MATCH (other:ReSourceNode:StepOccurrence {space:$space,generation:$generation})
            WHERE other.route_snapshot_id<>risk.route_snapshot_id
            AND risk.route_snapshot_id IN [(r:ReSourceNode:RouteSnapshot)-[:FOR_MATERIAL]->
              (:ReSourceNode:Material {space:$space,generation:$generation,id:'material:pet'}) | r.id]
            RETURN risk.id AS risk,original.id AS original,other.id AS other,
                   properties(original_edge) AS edge_props LIMIT 1""",
            space=config.namespace,
            generation=imported["generation"],
        ).single()
        assert row
        values = dict(row)
        try:
            session.run(
                """MATCH (r:ReSourceNode {space:$space,generation:$generation,id:$risk})
                -[t:TARGETS]->(),(n:ReSourceNode {space:$space,generation:$generation,id:$other})
                DELETE t CREATE (r)-[restored:TARGETS]->(n) SET restored=$edge_props""",
                space=config.namespace,
                generation=imported["generation"],
                **values,
            ).consume()
            with pytest.raises(store.GraphStoreError) as error:
                all_pages(config, material="PET")
            assert error.value.code == "integrity"
        finally:
            session.run(
                """MATCH (r:ReSourceNode {space:$space,generation:$generation,id:$risk})
                -[t:TARGETS]->(),(n:ReSourceNode {space:$space,generation:$generation,id:$original})
                DELETE t CREATE (r)-[restored:TARGETS]->(n) SET restored=$edge_props""",
                space=config.namespace,
                generation=imported["generation"],
                **values,
            ).consume()
