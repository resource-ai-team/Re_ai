"""Real Neo4j, synthetic graph/model. Never touches the active process/legal namespaces."""

import os
from uuid import uuid4

import pytest

from resource_ai.knowledge_graph.graph_store import get_driver, settings
from resource_ai.legal_graph.models import LegalGraphError
from resource_ai.legal_graph.service import LegalGraphService
from resource_ai.legal_graph.store import LegalGraphStore
from tests.legal_graph_fixtures import enriched

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_NEO4J_INTEGRATION") != "true", reason="Real Neo4j required"
)


@pytest.fixture
def graph_store():
    config = settings()
    namespace = "legal-graph-test-" + uuid4().hex[:12]
    store = LegalGraphStore(config, namespace=namespace)
    try:
        yield store
    finally:
        assert namespace.startswith("legal-graph-test-")
        with get_driver(config).session(database=config.database) as session:
            session.run(
                "MATCH (n) WHERE n.space=$s AND any(label IN labels(n) WHERE label STARTS WITH 'ReLegal') DETACH DELETE n",
                s=namespace,
            ).consume()


def test_real_neo4j_roundtrip_preserves_actual_edges_evidence_and_no_user_writes(graph_store):
    snapshot, embeddings, model = enriched()
    trace = graph_store.publish(snapshot)
    loaded, observed = graph_store.load()
    assert loaded.fingerprint() == snapshot.fingerprint() == observed["fingerprint"]
    assert trace["generation"] == observed["generation"]
    result = LegalGraphService(graph_store, embeddings, model).run(
        "가상 사업장 의뢰", {"permit_confirmed": True}
    )
    assert result["conclusion"]["status"] == "grounded_research_summary"
    assert graph_store.load()[1] == observed


@pytest.mark.parametrize("mutation", ["endpoint", "support", "record", "extra_edge"])
def test_tampered_real_projection_fails_closed(graph_store, mutation):
    snapshot, _, _ = enriched()
    trace = graph_store.publish(snapshot)
    mutations = {
        "endpoint": "MATCH (n:ReLegalNode {space:$s,generation:$g}) WHERE n.kind='Case' SET n.kind='Article'",
        "support": "MATCH (n:ReLegalNode {space:$s,generation:$g})-[r:RE_LEGAL_SUPPORT]->() WITH r LIMIT 1 DELETE r",
        "record": "MATCH (n:ReLegalEvidence {space:$s,generation:$g}) WITH n LIMIT 1 SET n.record_json='{}'",
        "extra_edge": "MATCH (n:ReLegalNode {space:$s,generation:$g}) WITH n LIMIT 1 CREATE (n)-[:UNEXPECTED]->(n)",
    }
    with get_driver(graph_store.config).session(database=graph_store.config.database) as session:
        session.run(mutations[mutation], s=graph_store.namespace, g=trace["generation"]).consume()
    with pytest.raises(LegalGraphError) as exc:
        graph_store.load()
    assert exc.value.code == "graph-integrity"
    # A fresh valid generation can recover without deleting evidence or touching the process KG.
    new = graph_store.publish(snapshot)
    assert new["generation"] != trace["generation"]
    assert graph_store.status()["analysis_ready"] is True


def test_failed_activation_rolls_back_new_generation_and_keeps_old_head(graph_store, monkeypatch):
    snapshot, _, _ = enriched()
    old = graph_store.publish(snapshot)
    original = graph_store._load

    def fail(*args):
        raise LegalGraphError("graph-integrity", "가상 검증 실패")

    monkeypatch.setattr(graph_store, "_load", fail)
    with pytest.raises(LegalGraphError):
        graph_store.publish(snapshot)
    monkeypatch.setattr(graph_store, "_load", original)
    assert graph_store.load()[1] == old
    with get_driver(graph_store.config).session(database=graph_store.config.database) as session:
        count = session.run(
            "MATCH (n:ReLegalSnapshot {space:$s}) RETURN count(n) AS n", s=graph_store.namespace
        ).single()["n"]
    assert count == 1
