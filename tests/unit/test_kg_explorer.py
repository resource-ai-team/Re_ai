import gzip
import json
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from resource_ai.knowledge_graph import graph_store as store
from scripts.kg_explorer.export_graph import build_snapshot, export_snapshot
from scripts.kg_explorer.server import create_app


def records():
    nodes = [
        {"id": "waste:test", "kind": "WasteDescription", "label": "PP scrap"},
        {"id": "material:pp", "kind": "Material", "label": "PP"},
    ]
    edges = [{"source": "waste:test", "target": "material:pp", "kind": "MENTIONS_MATERIAL"}]
    manifest = {
        "node_counts": {"WasteDescription": 1, "Material": 1},
        "edge_counts": {"MENTIONS_MATERIAL": 1},
        "meta": {"graph_sha256": "fixture-hash"},
    }
    return nodes, edges, manifest


def snapshot():
    nodes, edges, manifest = records()
    return build_snapshot("generation-one", manifest, nodes, edges, "test-space")


@pytest.fixture
def client(tmp_path):
    data = snapshot()
    raw = json.dumps(data).encode()
    (tmp_path / "graph.json").write_bytes(raw)
    (tmp_path / "graph.json.gz").write_bytes(gzip.compress(raw))
    (tmp_path / "manifest.json").write_text(json.dumps(data["meta"]), encoding="utf-8")
    with TestClient(create_app(tmp_path)) as test_client:
        yield test_client


def test_complete_export_preserves_direction_degrees_and_aggregate_counts():
    data = snapshot()
    assert data["meta"]["node_count"] == len(data["nodes"]) == 2
    assert data["meta"]["edge_count"] == len(data["edges"]) == 1
    assert [node[3] for node in data["nodes"]] == [1, 1]
    a, b, r = data["edges"][0]
    assert [data["nodes"][a][0], data["nodes"][b][0]] == ["waste:test", "material:pp"]
    assert data["relations"][r]["id"] == "MENTIONS_MATERIAL"
    assert data["aggregates"] == [[data["nodes"][a][2], data["nodes"][b][2], r, 1]]


@pytest.mark.parametrize("damage", ["counts", "duplicate", "endpoint", "direction", "empty"])
def test_export_rejects_inconsistent_data(damage):
    nodes, edges, manifest = deepcopy(records())
    if damage == "counts":
        manifest["node_counts"]["Material"] += 1
    elif damage == "duplicate":
        nodes[1]["id"] = nodes[0]["id"]
    elif damage == "endpoint":
        edges[0]["target"] = "missing"
    elif damage == "direction":
        edges[0]["source"], edges[0]["target"] = edges[0]["target"], edges[0]["source"]
    else:
        nodes.clear()
    with pytest.raises(ValueError):
        build_snapshot("g", manifest, nodes, edges, "test-space")


def test_export_reads_head_nodes_and_edges_in_one_read_transaction(tmp_path):
    nodes, edges, manifest = records()
    queries = []

    class Transaction:
        def run(self, query, **params):
            assert params == {"space": "test-space", "generation": "generation-one"}
            queries.append(query)
            return nodes if "ORDER BY n.kind" in query else edges

    def read(config, work):
        assert config.namespace == "test-space"
        return work(Transaction())

    with (
        patch.object(store, "settings", return_value=SimpleNamespace(namespace="test-space")),
        patch.object(store, "_head", return_value=("generation-one", manifest)) as head,
        patch.object(store, "_read", side_effect=read) as transaction,
    ):
        result = export_snapshot(tmp_path)
    transaction.assert_called_once()
    head.assert_called_once()
    assert len(queries) == 2 and all("LIMIT" not in q and "CREATE" not in q for q in queries)
    raw = (tmp_path / "graph.json").read_bytes()
    assert raw == gzip.decompress((tmp_path / "graph.json.gz").read_bytes())
    assert json.loads(raw)["meta"] == result


def test_graph_is_available_without_neo4j_and_only_ui_assets_are_served(client):
    with patch.object(store, "_read", side_effect=AssertionError("No DB connection expected")):
        assert client.get("/api/health").json()["generation"] == "generation-one"
        assert client.get("/data/graph").json() == client.get("/graph.json").json()
        assert "지식그래프" in client.get("/").text
        for path in ["/server.py", "/export_graph.py", "/.env", "/manifest.json"]:
            assert client.get(path).status_code == 404
        assert client.post("/api/node", json={"id": "material:pp"}).status_code == 405


def test_missing_export_reports_actionable_error(tmp_path):
    with TestClient(create_app(tmp_path)) as empty:
        assert empty.get("/api/health").status_code == 503
        assert empty.get("/data/graph").status_code == 503
        assert "내보내기" in empty.get("/api/health").json()["detail"]


def test_node_read_pins_namespace_generation_and_parameterizes_user_input(client):
    node_id = "' MATCH (n) DETACH DELETE n //"

    class Transaction:
        def run(self, query, **params):
            assert node_id not in query
            assert params == {
                "id": node_id,
                "space": "test-space",
                "generation": "generation-one",
            }
            return SimpleNamespace(
                single=lambda: {
                    "id": node_id,
                    "kind": "Evidence",
                    "label": "Literal ID",
                    "properties": '{"validation":"unverified-extraction"}',
                }
            )

    with (
        patch.object(store, "settings", return_value=object()),
        patch.object(store, "_read", side_effect=lambda config, work: work(Transaction())),
    ):
        response = client.get("/api/node", params={"id": node_id, "generation": "generation-one"})
    assert response.status_code == 200
    assert response.json()["properties"]["validation"] == "unverified-extraction"
    assert response.json()["read_only"] is True


def test_changed_generation_cannot_return_properties_for_another_snapshot(client):
    with patch.object(
        store, "_read", side_effect=AssertionError("Must not query another snapshot")
    ):
        response = client.get("/api/node", params={"id": "material:pp", "generation": "old"})
    assert response.status_code == 409


def test_configuration_failure_is_sanitized_and_does_not_break_saved_graph(client):
    with patch.object(store, "settings", side_effect=store.GraphStoreError("config", "secret")):
        response = client.get("/api/node", params={"id": "material:pp"})
        assert response.status_code == 503 and "secret" not in response.text
        assert client.get("/data/graph").status_code == 200


def test_missing_node_is_404_and_ids_are_bounded(client):
    with (
        patch.object(store, "settings", return_value=object()),
        patch.object(store, "_read", return_value=None),
    ):
        assert client.get("/api/node", params={"id": "missing"}).status_code == 404
    for node_id in ["", "x" * 301]:
        assert client.get("/api/node", params={"id": node_id}).status_code == 422
