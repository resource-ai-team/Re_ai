"""Actual Neo4j path/condition APIs; explicitly synthetic lot and legal retrieval."""

import os
import sqlite3
from dataclasses import replace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from resource_ai.agents.research import CandidateResearchService
from resource_ai.api.routes.analysis import get_research_service
from resource_ai.knowledge_graph import graph_store as store
from resource_ai.knowledge_graph.builder import build_stage
from resource_ai.knowledge_graph.query import GraphQuery
from resource_ai.main import app

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_NEO4J_INTEGRATION") != "true", reason="Real Neo4j required"
)


@pytest.fixture(scope="module")
def analysis_graph():
    config = replace(store.settings(), namespace="resource-analysis-test-" + uuid4().hex[:10])
    staging = sqlite3.connect(":memory:")
    build_stage(staging, [], include_curated=True)
    store.import_graph(staging, config=config)
    try:
        yield GraphQuery(config)
    finally:
        assert config.namespace.startswith("resource-analysis-test-")
        with store.get_driver(config).session(database=config.database) as session:
            session.run(
                "MATCH (n) WHERE n.space=$space DETACH DELETE n", space=config.namespace
            ).consume()
        staging.close()


class EmptyLegalRepository:
    def search(self, query, top_k):
        return []


@pytest.mark.parametrize("designated", [False, True])
def test_real_graph_three_pp_paths_paginate_without_hiding_unmet_paths(analysis_graph, designated):
    previous = dict(app.dependency_overrides)
    app.dependency_overrides[get_research_service] = lambda: CandidateResearchService(
        analysis_graph, EmptyLegalRepository()
    )
    try:
        response = TestClient(app).post(
            "/analysis/research",
            json={
                "lot": {
                    "material": "PP",
                    "input_form": "scrap",
                    "quantity_kg": 3000,
                    "page_size": 1,
                    "designated_waste": designated,
                },
            },
        )
    finally:
        app.dependency_overrides = previous
    assert response.status_code == 200
    data = response.json()
    assert data["coverage"]["pages_read"] == 3
    assert data["coverage"]["complete_within_curated_scope"] is True
    assert len(data["candidates"]) == 3
    assert data["graph_trace"]["reference_version"] == "planning-reference-v2"
    assert data["route"] == "REVIEW" and data["final_recommendations"] == []
    assert {s["status"] for s in data["legal_searches"]} == {"no_hits"}
    assert {c["status"] for c in data["candidates"]} == {
        "unmet" if designated else "needs_verification"
    }


def test_actual_graph_page_limit_does_not_claim_candidate_coverage(analysis_graph):
    from resource_ai.api.schemas.analysis import ResearchRequest

    data = CandidateResearchService(analysis_graph, EmptyLegalRepository()).run(
        ResearchRequest(lot={"material": "PP", "page_size": 1}, max_pages=1)
    )
    assert len(data.candidates) == 1
    # Missing form preserves six entry-form paths, unlike the scrap cases above.
    assert data.coverage["enumerated_in_graph"] == 6
    assert data.coverage["next_cursor"]
    assert data.coverage["complete_within_curated_scope"] is False
