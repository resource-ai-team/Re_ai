import re
import sqlite3
from dataclasses import replace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from neo4j.exceptions import ServiceUnavailable

from resource_ai.agents.tools.graph_tool import find_process_candidates
from resource_ai.api.schemas.graph import CandidateRequest
from resource_ai.core.config import Settings
from resource_ai.core.dependencies import get_graph_query
from resource_ai.knowledge_graph import graph_store as store
from resource_ai.knowledge_graph import ontology
from resource_ai.knowledge_graph.builder import build_stage
from resource_ai.knowledge_graph.dataset import DATASET_SHA256, read_candidates
from resource_ai.knowledge_graph.query import GraphQuery, resolve_material
from resource_ai.main import app
from tests.unit.test_ontology_graph import extraction


def test_unknown_inputs_are_preserved_without_inventing_composition_or_current_route():
    with patch.object(store, "_read", side_effect=AssertionError("No DB query")):
        result = GraphQuery().search(CandidateRequest())
        assert result.status == "needs_input"
        assert result.input_context["current_treatment"] is None
        assert result.input_context["material"] is None
        assert "contamination" in result.missing_fields
        assert result.dispatch_approved is False
        assert (
            GraphQuery().search(CandidateRequest(material="PETroleum")).status
            == "unsupported_material"
        )


def test_pp_pe_aliases_are_search_families_and_do_not_match_pet_as_pe():
    assert resolve_material(" 폴리프로필렌 ") == "pp"
    assert resolve_material("HDPE") == "pe"
    assert resolve_material("PP 스크랩") is None
    patterns = ontology.ontology()["materials"]
    assert re.search(patterns["pp"]["search_pattern"], "PP injection scrap", re.IGNORECASE)
    assert re.search(patterns["pe"]["search_pattern"], "LDPE film", re.IGNORECASE)
    assert not re.search(
        patterns["pe"]["search_pattern"], "polyethylene terephthalate bottles", re.IGNORECASE
    )
    assert not re.search(
        patterns["pe"]["search_pattern"], "poly(ethylene) terephthalate bottles", re.IGNORECASE
    )


def test_builder_rejects_nonempty_service_database_and_indexes_pp_separately():
    with sqlite3.connect(":memory:") as connection:
        build_stage(
            connection,
            [extraction(1, waste="PP scrap"), extraction(2, waste="HDPE flakes"), extraction(3)],
            include_curated=False,
        )
        assert len(ontology.graph_candidates(connection, "pp")) == 1
        assert len(ontology.graph_candidates(connection, "pe")) == 1
        with pytest.raises(ValueError, match="fresh"):
            build_stage(connection, [])


def test_pinned_dataset_has_every_row_and_preserves_missing_fields():
    rows = read_candidates()
    assert len(rows) == 33679
    assert len({row["candidate_id"] for row in rows}) == 33679
    assert sum(not r["transforming_process"].strip() for r in rows) == 7
    assert sum(not r["reference"].strip() for r in rows) == 686
    assert len(DATASET_SHA256) == 64


def test_settings_hide_password_and_reject_uri_embedded_credentials():
    configured = Settings(neo4j_password="unit-secret")
    assert "unit-secret" not in repr(configured)
    config = store.Settings("bolt://127.0.0.1:7687", "neo4j", "unit-secret")
    assert "unit-secret" not in repr(config)
    for uri in (
        "https://example.com",
        "bolt://user:secret@example.com",
        "bolt://example.com:bad",
        "bolt://example.com/path",
    ):
        with pytest.raises(store.GraphStoreError):
            replace(config, uri=uri)


def test_network_failure_never_leaks_driver_secrets():
    config = store.Settings("bolt://127.0.0.1:7687", "neo4j", "unit-secret")
    with patch.object(store, "get_driver") as driver:
        driver.return_value.session.return_value.__enter__.return_value.execute_read.side_effect = (
            ServiceUnavailable("PASSWORD=unit-secret")
        )
        with pytest.raises(store.GraphStoreError) as error:
            store._read(config, lambda tx: None)
    assert "unit-secret" not in str(error.value)


def test_api_and_agent_use_same_query_and_do_not_fallback_when_neo4j_fails():
    class Unavailable:
        def search(self, request):
            raise store.GraphStoreError("unavailable", "Neo4j 연결을 확인해 주세요.")

    app.dependency_overrides[get_graph_query] = Unavailable
    try:
        client = TestClient(app)
        response = client.post("/kg/candidates", json={"material": "PP"})
        assert response.status_code == 503
        assert response.json()["detail"]["code"] == "unavailable"
        assert client.get("/kg/ontology").json()["inference_policy"]["dispatch_approved"] is False
        for payload in (
            {"quantity_kg": -1},
            {"top_k": 201},
            {"material": ""},
            {"cypher": "MATCH (n)"},
        ):
            assert client.post("/kg/candidates", json=payload).status_code == 422
    finally:
        app.dependency_overrides.clear()
    with patch("resource_ai.agents.tools.graph_tool.get_graph_query", return_value=GraphQuery()):
        assert find_process_candidates()["status"] == "needs_input"
