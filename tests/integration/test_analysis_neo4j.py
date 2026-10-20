"""Actual Neo4j path/condition APIs; explicitly synthetic lot and legal retrieval."""

import os
import sqlite3
from dataclasses import replace
from datetime import date
from pathlib import Path
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


@pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_INTEGRATION") != "true", reason="Real PostgreSQL required"
)
def test_real_neo4j_to_real_postgres_retrieval_is_still_not_legal_approval(analysis_graph):
    """Real DB boundary, but synthetic statute and offline hash embeddings for CI."""
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import make_conninfo

    from resource_ai.api.schemas.analysis import ResearchRequest
    from resource_ai.core.config import get_settings
    from resource_ai.evidence.models import LegalDocument, LegalSection
    from resource_ai.rag.retrieval.embeddings import HashEmbeddingProvider
    from resource_ai.rag.retrieval.vector_store import Database
    from resource_ai.rag.service import LegalRepository

    database_url = get_settings().database_url
    schema = "re_ai_analysis_test_" + uuid4().hex
    # Install the extension in public, never in the schema later removed by this test.
    with psycopg.connect(database_url, connect_timeout=5) as connection:
        connection.execute("CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public")
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    try:
        isolated_url = make_conninfo(database_url, options=f"-c search_path={schema},public")
        repository = LegalRepository(Database(isolated_url), HashEmbeddingProvider())
        repository.initialize(Path("migrations"))
        repository.ingest(
            LegalDocument(
                source_id="synthetic-legal-wiring-only",
                document_type="LAW",
                title="가상 PP 폐기물 법령 테스트 자료: 실재 법규 아님",
                source_url="https://example.invalid/synthetic-statute-not-real",
                effective_from=date(2020, 1, 1),
                sections=[
                    LegalSection(
                        heading="가상 조문",
                        citation="가상 인용",
                        content="PP 폐기물 재활용 허가 위탁 수집 운반 확인. 테스트용 가상 문구이며 실재 법령이 아님.",
                    )
                ],
            )
        )
        result = CandidateResearchService(analysis_graph, repository).run(
            ResearchRequest(lot={"material": "PP", "input_form": "scrap", "page_size": 1})
        )
        assert len(result.candidates) == 3
        assert result.coverage["legal_retrieval_complete"] is True
        assert all(s.status == "retrieved" and s.results for s in result.legal_searches)
        assert all(
            s.results[0]["source_url"].endswith("synthetic-statute-not-real")
            for s in result.legal_searches
        )
        assert all("legal_source_review_required" in c.blockers for c in result.candidates)
        assert result.coverage["legal_audit_complete"] is False
        assert result.route == "REVIEW" and result.final_recommendations == []
    finally:
        assert schema.startswith("re_ai_analysis_test_") and len(schema) == 52
        with psycopg.connect(database_url, connect_timeout=5) as connection:
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
