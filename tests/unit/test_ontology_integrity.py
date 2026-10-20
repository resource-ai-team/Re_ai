"""Adversarial graph integrity regressions using small local reference fixtures."""

import json
import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from resource_ai.knowledge_graph import ontology as graph
from tests.unit.test_ontology_graph import extraction


def authored_report():
    """A synthetic route, never a claim about a real process or company."""
    evidence = ["E01"]
    return {
        "material": {"id": "pet"},
        "evidence": [
            {
                "id": "E01",
                "document_id": "company-profile:integrity-fixture",
                "title": "Synthetic provenance fixture",
                "locator": "Fixture section 1",
                "excerpt": "A synthetic authored route used only for integrity tests.",
                "limitations": ["Synthetic fixture, not actual acceptance."],
                "scope": "synthetic-business-note-not-current-acceptance",
                "model_transfer_allowed": False,
            }
        ],
        "process_graph": {
            "input_form": "bottle",
            "routes": [
                {
                    "id": "process-integrity-fixture",
                    "label": "Synthetic route",
                    "status": "conditional",
                    "input_conditions": ["Verify synthetic input."],
                    "output": "Unverified synthetic output",
                    "evidence_ids": evidence,
                    "nodes": [
                        {
                            "id": "fixture-n1",
                            "kind": "source",
                            "label": "Input",
                            "evidence_ids": evidence,
                        },
                        {
                            "id": "fixture-n2",
                            "kind": "sorting",
                            "label": "Check",
                            "evidence_ids": evidence,
                        },
                        {
                            "id": "fixture-n3",
                            "kind": "recycling",
                            "label": "Output",
                            "evidence_ids": evidence,
                        },
                    ],
                    "edges": [
                        {
                            "id": "fixture-e1",
                            "from": "fixture-n1",
                            "to": "fixture-n2",
                            "kind": "process",
                            "label": "First connection",
                            "evidence_ids": evidence,
                        },
                        {
                            "id": "fixture-e2",
                            "from": "fixture-n2",
                            "to": "fixture-n3",
                            "kind": "handover",
                            "label": "Second connection",
                            "evidence_ids": evidence,
                        },
                    ],
                    "risks": [
                        {
                            "id": "fixture-r1",
                            "title": "Synthetic verification question",
                            "target_type": "node",
                            "target_id": "fixture-n2",
                            "status": "needs-verification",
                            "severity": "medium",
                            "cause": "Synthetic missing information.",
                            "impact": "Synthetic uncertainty.",
                            "required_evidence": ["Synthetic measurement"],
                            "next_action": "Verify the fixture input.",
                            "evidence_ids": evidence,
                            "case_source_ids": [],
                        }
                    ],
                    "facility_candidate_ids": [],
                    "limitations": ["Test fixture only."],
                }
            ],
        },
    }


class OntologyIntegrityTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "integrity.sqlite"
        self.connection = sqlite3.connect(self.path)
        self.addCleanup(self.connection.close)
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.execute("""CREATE TABLE documents(
            id TEXT PRIMARY KEY, kind TEXT NOT NULL, source_id TEXT,
            title TEXT, text TEXT, metadata TEXT)""")
        self.connection.execute(
            "INSERT INTO documents VALUES(?,?,?,?,?,?)",
            (
                "unrelated-document",
                "fact_card",
                "fixture",
                "Preserve me",
                "Unrelated original content",
                '{"fixture":true}',
            ),
        )
        self.first = extraction(1)
        self.second = extraction(
            2, waste="paper waste", resource="recovered fibres", reference="10.1000/source.2"
        )
        self.first_document = self.add(self.first)
        self.add(self.second)
        self.build()
        graph.audit_graph(self.connection)

    def add(self, metadata):
        document_id = "fixture:" + metadata["candidate_id"]
        self.connection.execute(
            "INSERT INTO documents VALUES(?,?,?,?,?,?)",
            (
                document_id,
                "kg_candidate",
                "W2RKG-33679",
                "Synthetic source title",
                "Original synthetic source text",
                json.dumps(metadata, ensure_ascii=False),
            ),
        )
        self.connection.commit()
        return document_id

    def build(self):
        with (
            patch.object(graph, "_curated_reports", return_value=iter([authored_report()])),
            patch.object(graph, "_import_cards", return_value={}),
        ):
            return graph.build_graph(self.connection, include_curated=True)

    def documents(self):
        return self.connection.execute("SELECT * FROM documents ORDER BY id").fetchall()

    def occurrence(self, original_id):
        row = self.connection.execute(
            "SELECT id FROM kg_nodes WHERE json_extract(properties,'$.original_id')=?",
            (original_id,),
        ).fetchone()
        self.assertIsNotNone(row)
        return row[0]

    def reseal_digest(self):
        # The build-time audit runs before its digest is sealed. Recomputing the
        # digest isolates semantic validation from generic checksum rejection.
        self.connection.execute(
            "UPDATE kg_meta SET value=? WHERE key='graph_sha256'",
            (graph.graph_digest(self.connection),),
        )

    def retarget(self, relation, source, target):
        self.connection.execute(
            "UPDATE kg_edges SET id=?,target_id=? WHERE kind=? AND source_id=?",
            (graph.identity("edge", [relation, source, target]), target, relation, source),
        )

    def assert_audit_rejects(self):
        self.reseal_digest()
        with self.assertRaises((ValueError, sqlite3.Error)):
            graph.audit_graph(self.connection)

    def assert_stale_not_served(self):
        for name, read in (
            ("availability", lambda: graph.graph_available(self.connection)),
            ("candidates", lambda: graph.graph_candidates(self.connection, "pet")),
            (
                "inspection",
                lambda: graph.inspect_claim(self.connection, self.first["candidate_id"]),
            ),
        ):
            with self.subTest(reader=name):
                try:
                    result = read()
                except (ValueError, sqlite3.Error):
                    continue
                # A reader may explicitly report unavailable instead of raising.
                # It must not advertise a usable graph or return old claim data.
                self.assertFalse(result, "Stale graph was served by " + name)

    def test_unknown_graph_trigger_rebuild_rejects_before_deleting_source_documents(self):
        documents = self.documents()
        self.connection.execute("""CREATE TRIGGER unexpected_graph_cleanup
            BEFORE DELETE ON kg_nodes BEGIN DELETE FROM documents; END""")
        self.connection.commit()
        with self.assertRaises((ValueError, sqlite3.Error)):
            self.build()
        self.assertEqual(self.documents(), documents)
        self.assertEqual(
            self.connection.execute("SELECT COUNT(*) FROM kg_claim_records").fetchone()[0], 2
        )

    def test_external_cascading_graph_reference_rebuild_rejects_without_data_loss(self):
        # SQLite identifiers are case-insensitive, while foreign_key_list keeps
        # the spelling used in REFERENCES. All spellings must be protected.
        for referenced_table in ("kg_nodes", "KG_NODES", "Kg_Nodes"):
            with self.subTest(referenced_table=referenced_table):
                self.connection.execute("SAVEPOINT external_reference_case")
                try:
                    self.connection.execute(
                        """CREATE TABLE external_annotations(
                        id TEXT PRIMARY KEY,
                        node_id TEXT NOT NULL REFERENCES """
                        + referenced_table
                        + """(id) ON DELETE CASCADE,
                        note TEXT NOT NULL)"""
                    )
                    self.connection.execute(
                        "INSERT INTO external_annotations VALUES(?,?,?)",
                        (
                            "keep-external",
                            "claim:" + self.first["candidate_id"],
                            "External data must survive a rejected graph rebuild.",
                        ),
                    )
                    documents = self.documents()
                    external = self.connection.execute(
                        "SELECT * FROM external_annotations"
                    ).fetchall()
                    graph_before = graph.graph_digest(self.connection)
                    with self.assertRaises((ValueError, sqlite3.Error)):
                        self.build()
                    self.assertEqual(self.documents(), documents)
                    self.assertEqual(
                        self.connection.execute("SELECT * FROM external_annotations").fetchall(),
                        external,
                    )
                    self.assertEqual(graph.graph_digest(self.connection), graph_before)
                finally:
                    self.connection.execute("ROLLBACK TO external_reference_case")
                    self.connection.execute("RELEASE external_reference_case")

    def test_audit_rejects_risk_without_snapshot_parent(self):
        risk = self.occurrence("fixture-r1")
        self.connection.execute(
            "DELETE FROM kg_edges WHERE kind='HAS_RISK' AND target_id=?", (risk,)
        )
        self.assert_audit_rejects()

    def test_audit_rejects_wrong_risk_target_in_same_snapshot(self):
        risk = self.occurrence("fixture-r1")
        self.retarget("TARGETS", risk, self.occurrence("fixture-n3"))
        self.assert_audit_rejects()

    def test_audit_rejects_wrong_risk_target_type_in_same_snapshot(self):
        risk = self.occurrence("fixture-r1")
        self.retarget("TARGETS", risk, self.occurrence("fixture-e1"))
        self.assert_audit_rejects()

    def test_audit_rejects_wrong_link_endpoints_in_same_snapshot(self):
        for relation in ("FROM_STEP", "TO_STEP"):
            with self.subTest(relation=relation):
                self.connection.execute("SAVEPOINT incorrect_endpoint")
                try:
                    self.retarget(
                        relation, self.occurrence("fixture-e1"), self.occurrence("fixture-n3")
                    )
                    self.assert_audit_rejects()
                finally:
                    self.connection.execute("ROLLBACK TO incorrect_endpoint")
                    self.connection.execute("RELEASE incorrect_endpoint")

    def test_audit_rejects_evidence_swapped_between_source_claims(self):
        claims = ["claim:" + row["candidate_id"] for row in (self.first, self.second)]
        evidence = [
            self.connection.execute(
                "SELECT target_id FROM kg_edges WHERE kind='SUPPORTED_BY' AND source_id=?", (claim,)
            ).fetchone()[0]
            for claim in claims
        ]
        self.assertNotEqual(evidence[0], evidence[1])
        for claim, target in zip(claims, reversed(evidence)):
            self.retarget("SUPPORTED_BY", claim, target)
        self.assert_audit_rejects()

    def test_source_row_addition_invalidates_all_graph_readers(self):
        self.add(extraction(3, waste="PET bottle waste", resource="new unindexed output"))
        self.assert_stale_not_served()

    def test_source_row_deletion_invalidates_all_graph_readers(self):
        # Direct writers can bypass connection-local foreign keys. Reader-side
        # freshness checks must still reject the resulting stale source set.
        self.connection.execute("PRAGMA foreign_keys=OFF")
        self.connection.execute("DELETE FROM documents WHERE id=?", (self.first_document,))
        self.connection.commit()
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.assert_stale_not_served()

    def test_source_row_modification_invalidates_all_graph_readers(self):
        self.connection.execute(
            "UPDATE documents SET text=? WHERE id=?",
            ("Changed synthetic source text", self.first_document),
        )
        self.connection.commit()
        self.assert_stale_not_served()


if __name__ == "__main__":
    unittest.main()
