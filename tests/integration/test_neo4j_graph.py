import json
import os
import sqlite3
import unittest
from dataclasses import replace
from unittest.mock import patch
from uuid import uuid4

from resource_ai.knowledge_graph import graph_store as store
from resource_ai.knowledge_graph import ontology as graph
from tests.unit.test_ontology_graph import extraction


def fixture():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute(
        "CREATE TABLE documents(id TEXT PRIMARY KEY,kind TEXT,source_id TEXT,title TEXT,text TEXT,metadata TEXT)"
    )
    for number in [1, 2]:
        raw = extraction(number, process="mechanical recycling" if number == 1 else "hydrolysis")
        connection.execute(
            "INSERT INTO documents VALUES(?,?,?,?,?,?)",
            (
                raw["candidate_id"],
                "kg_candidate",
                "W2RKG-33679",
                "Fixture",
                "Public dataset fixture",
                json.dumps(raw),
            ),
        )
    connection.commit()
    graph.build_graph(connection, include_curated=False)
    return connection


@unittest.skipUnless(
    os.getenv("RUN_NEO4J_INTEGRATION") == "true", "Opt-in real Neo4j server required"
)
class RealNeo4jTests(unittest.TestCase):
    def setUp(self):
        self.connection = fixture()
        self.addCleanup(self.connection.close)
        self.config = replace(store.settings(), namespace="resource-test-" + uuid4().hex[:16])
        self.addCleanup(self.cleanup_namespace)
        self.result = store.import_graph(self.connection, config=self.config)

    def cleanup_namespace(self):
        # This random, per-test namespace is the only data this test created.
        with store.get_driver(self.config).session(database=self.config.database) as session:
            session.run(
                "MATCH (n:ReSourceNode {space:$space}) DETACH DELETE n", space=self.config.namespace
            ).consume()
            session.run(
                "MATCH (n:ReSourceDataset {space:$space}) DELETE n", space=self.config.namespace
            ).consume()
            session.run(
                "MATCH (n:ReSourceGraph {space:$space}) DELETE n", space=self.config.namespace
            ).consume()

    def test_real_bolt_roundtrip_exact_tuples_trace_and_idempotence(self):
        self.assertEqual(
            self.result["graph_sha256"], graph.audit_graph(self.connection)["graph_sha256"]
        )
        expected = graph.graph_candidates(self.connection, "pet")
        actual = store.candidates(self.connection, "pet", config=self.config)
        self.assertEqual([row["metadata"] for row in actual], [row["metadata"] for row in expected])
        self.assertTrue(all(row["graph_trace"]["backend"] == "neo4j" for row in actual))
        repeated = store.import_graph(self.connection, config=self.config)
        self.assertEqual(repeated["status"], "unchanged")
        self.assertEqual(repeated["generation"], self.result["generation"])

    def test_failed_new_generation_audit_keeps_previous_head(self):
        with (
            patch(
                "resource_ai.knowledge_graph.graph_store.audit_generation",
                side_effect=store.integrity(),
            ),
            self.assertRaises(store.GraphStoreError),
        ):
            store.import_graph(self.connection, config=self.config, rebuild=True)
        after = store.audit_generation(config=self.config)
        self.assertEqual(after["generation"], self.result["generation"])
        self.assertEqual(after["graph_sha256"], self.result["graph_sha256"])

    def test_swapped_evidence_is_rejected_from_actual_neo4j_edges(self):
        with store.get_driver(self.config).session(database=self.config.database) as session:
            session.run(
                """MATCH (a:ReSourceNode:TransformationClaim {space:$space,generation:$generation})-[r:SUPPORTED_BY]->(e)
                WITH a,r,e ORDER BY a.id WITH collect({a:a,r:r,e:e}) AS pair
                WITH pair[0] AS x,pair[1] AS y
                WITH x.a AS a,y.e AS e,x.r AS old
                CREATE (a)-[:SUPPORTED_BY {id:old.id,properties_json:old.properties_json}]->(e)
                DELETE old""",
                space=self.config.namespace,
                generation=self.result["generation"],
            ).consume()
        with self.assertRaises(store.GraphStoreError):
            store.audit_generation(config=self.config)

    def test_source_edit_stops_neo4j_lookup(self):
        self.connection.execute(
            "UPDATE documents SET text='Changed source' WHERE id=(SELECT min(id) FROM documents)"
        )
        with self.assertRaises(store.GraphStoreError) as error:
            store.candidates(self.connection, "pet", config=self.config)
        self.assertEqual(error.exception.code, "stale-source")

    def test_force_rebuild_never_overwrites_a_different_namespace_owner(self):
        with store.get_driver(self.config).session(database=self.config.database) as session:
            session.run(
                "MATCH (h:ReSourceGraph {space:$space}) SET h.owner=$owner",
                space=self.config.namespace,
                owner="different-owner",
            ).consume()
        with self.assertRaises(store.GraphStoreError):
            store.import_graph(self.connection, config=self.config, rebuild=True)
        with store.get_driver(self.config).session(database=self.config.database) as session:
            owner = session.run(
                "MATCH (h:ReSourceGraph {space:$space}) RETURN h.owner AS owner",
                space=self.config.namespace,
            ).single()["owner"]
        self.assertEqual(owner, "different-owner")


if __name__ == "__main__":
    unittest.main()
