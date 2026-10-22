"""Synthetic lots against actual Neo4j; no customer/field validation claims."""

import os
import sqlite3
from dataclasses import replace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from resource_ai.api.schemas.facility_assessment import (
    AssessedPlansRequest,
    FacilityAssessmentRequest,
    LotFacts,
)
from resource_ai.core.dependencies import get_graph_query
from resource_ai.knowledge_graph import graph_store as store
from resource_ai.knowledge_graph.builder import build_stage
from resource_ai.knowledge_graph.facility_assessment import FacilityAssessor
from resource_ai.knowledge_graph.query import GraphQuery
from resource_ai.main import app

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_NEO4J_INTEGRATION") != "true", reason="Real Neo4j required"
)


@pytest.fixture(scope="module")
def checked_graph():
    config = replace(store.settings(), namespace="resource-condition-test-" + uuid4().hex[:10])
    stage = sqlite3.connect(":memory:")
    build_stage(stage, [], include_curated=True)
    store.import_graph(stage, config=config)
    try:
        yield config, stage
    finally:
        assert config.namespace.startswith("resource-condition-test-")
        with store.get_driver(config).session(database=config.database) as session:
            session.run(
                "MATCH (n) WHERE n.space=$space DETACH DELETE n", space=config.namespace
            ).consume()
        stage.close()


def plans(config, **fields):
    return FacilityAssessor(config).plans(
        AssessedPlansRequest(material="PP", input_form="scrap", quantity_kg=3000, **fields)
    )


def test_profiles_are_loaded_from_audited_graph_and_have_traceable_sources(checked_graph):
    config, _ = checked_graph
    result = FacilityAssessor(config).assess(
        FacilityAssessmentRequest(
            capability_key="jungang-gongju--pp-prepare",
            lot=LotFacts(material="PP", designated_waste=True),
        )
    )
    assert result.assessment.status == "unmet"
    check = next(c for c in result.assessment.checks if c.code == "designated_waste")
    assert check.source_ids and check.scope == "published_company_policy_not_legal_classification"
    assert result.evidence and result.graph_trace["reference_version"] == "planning-reference-v2"


def test_pp_three_routes_preserved_and_original_lot_not_copied_downstream(checked_graph):
    config, _ = checked_graph
    result = plans(
        config, color="검정", moisture_pct=25, contamination_pct=12, designated_waste=False
    )
    assert len(result.planning.items) == len(result.assessments) == 3
    assert all(a["status"] == "needs_verification" for a in result.assessments)
    for item in result.assessments:
        plan = next(p for p in result.planning.items if p.plan_id == item["plan_id"])
        for i, stage in enumerate(item["stages"]):
            a = stage["assessment"]
            if i == 0:
                assert a["input_context"]["moisture_pct"] == 25
                assert a["input_context"]["designated_waste"] is False
            else:
                assert all(v is None for v in a["input_context"].values())
                assert a["input_basis"] == "projected_state_not_measured"
                assert (
                    next(c for c in a["checks"] if c["code"] == "transition_material")["status"]
                    == "needs_verification"
                )
            for risk in a["risks"]:
                expected = (
                    plan.research_packet["handoffs"][i - 1]["leg_id"]
                    if i and risk["check_code"] == "handoff_specifications"
                    else stage["stage_id"]
                )
                assert risk["target_id"] == expected
            assert a["dispatch_approved"] is False


def test_explicit_exclusion_marks_all_affected_paths_without_hiding_them(checked_graph):
    config, _ = checked_graph
    result = plans(config, designated_waste=True)
    assert len(result.assessments) == 3
    assert all(a["status"] == "unmet" for a in result.assessments)
    assert result.recommended is False


def test_assessment_cursor_covers_all_paths_and_binds_new_lot_facts(checked_graph):
    config, _ = checked_graph
    first = plans(config, page_size=1, moisture_pct=10)
    second = plans(
        config, page_size=1, moisture_pct=10, cursor=first.planning.pagination.next_cursor
    )
    third = plans(
        config, page_size=1, moisture_pct=10, cursor=second.planning.pagination.next_cursor
    )
    assert len({r.assessments[0]["plan_id"] for r in [first, second, third]}) == 3
    assert third.planning.pagination.next_cursor is None
    with pytest.raises(store.GraphStoreError) as caught:
        plans(config, page_size=1, moisture_pct=11, cursor=first.planning.pagination.next_cursor)
    assert caught.value.code == "invalid-cursor"


def test_generation_change_invalidates_assessment_cursor(checked_graph):
    config, stage = checked_graph
    cursor = plans(config, page_size=1).planning.pagination.next_cursor
    store.import_graph(stage, config=config, rebuild=True)
    with pytest.raises(store.GraphStoreError) as caught:
        plans(config, page_size=1, cursor=cursor)
    assert caught.value.code == "stale-cursor"


def test_condition_profile_tampering_fails_closed_and_owned_reimport_repairs(checked_graph):
    config, stage = checked_graph
    with store.get_driver(config).session(database=config.database) as session:
        generation = store._read(config, lambda tx: store._head(tx, config))[0]
        session.run(
            "MATCH (n:ReSourceNode {space:$space,generation:$generation,kind:'FacilityCapability'}) SET n.properties_json='{}'",
            space=config.namespace,
            generation=generation,
        ).consume()
    with pytest.raises(store.GraphStoreError):
        plans(config)
    store.import_graph(stage, config=config, rebuild=True)
    assert len(plans(config).assessments) == 3


def test_api_contract_invalid_requests_and_empty_material(checked_graph):
    config, _ = checked_graph
    previous = dict(app.dependency_overrides)
    app.dependency_overrides[get_graph_query] = lambda: GraphQuery(config)
    try:
        c = TestClient(app)
        assert (
            c.post("/kg/assessed-plans", json={"material": "PP", "input_form": "scrap"}).status_code
            == 200
        )
        assert (
            c.post(
                "/kg/assessed-plans", json={"material": "PP", "audience": "external_model"}
            ).status_code
            == 422
        )
        assert (
            c.post(
                "/kg/assessed-plans", json={"material": "PP", "designated_waste": "false"}
            ).status_code
            == 422
        )
        assert (
            c.post("/kg/assessed-plans", json={"material": "PP", "cursor": "%%%"}).status_code
            == 400
        )
        empty = c.post("/kg/assessed-plans", json={}).json()
        assert empty["planning"]["status"] == "needs_input" and empty["assessments"] == []
        assert (
            c.post(
                "/kg/facility-assessment", json={"capability_key": "missing", "lot": {}}
            ).status_code
            == 400
        )
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)
