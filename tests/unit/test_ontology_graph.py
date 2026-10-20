"""Small persisted source-claim fixtures; no models, downloads, or full corpus."""

import json
import sqlite3
import unittest
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory

from resource_ai.knowledge_graph import ontology as graph


def extraction(
    number,
    *,
    waste="PET bottle waste",
    process="mechanical recycling",
    resource="recovered PET flakes",
    reference="10.1000/source.1",
):
    return {
        "candidate_id": f"W2R-{number:05d}-{number:020x}",
        "source_id": "W2RKG-33679",
        "upstream_row_1_based": number,
        "waste": waste,
        "transforming_process": process,
        "transformed_resource": resource,
        "reference": reference,
        "material_family_candidates": ["plastic"],
        "doi_search_candidates": [reference] if reference else [],
        "validation_status": "automatically_extracted_unverified",
        "flags": ["aggregated_string_do_not_pair_components"] if "," in process else [],
    }


class OntologyGraphTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "reference.sqlite"
        self.connection = self.connect()
        self.addCleanup(lambda: self.connection.close())
        self.connection.execute("""CREATE TABLE documents(
            id TEXT PRIMARY KEY, kind TEXT NOT NULL, source_id TEXT,
            title TEXT, text TEXT, metadata TEXT)""")
        self.connection.execute(
            "INSERT INTO documents VALUES(?,?,?,?,?,?)",
            (
                "keep-me",
                "fact_card",
                "fixture-source",
                "Preserve this title",
                "Unrelated reference text",
                '{"fixture":true}',
            ),
        )
        self.connection.commit()

    def connect(self):
        connection = sqlite3.connect(self.path)
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def add(self, metadata, *, document_id=None):
        document_id = document_id or "fixture:" + metadata["candidate_id"]
        self.connection.execute(
            "INSERT INTO documents VALUES(?,?,?,?,?,?)",
            (
                document_id,
                "kg_candidate",
                "W2RKG-33679",
                "Original display title",
                "Original document text",
                json.dumps(metadata, ensure_ascii=False),
            ),
        )
        self.connection.commit()
        return document_id

    def build(self):
        return graph.build_graph(self.connection, include_curated=False)

    def graph_snapshot(self):
        return {
            table: sorted(self.connection.execute("SELECT * FROM " + table).fetchall())
            for table in (
                "kg_nodes",
                "kg_edges",
                "kg_aliases",
                "kg_claim_records",
                "kg_classes",
                "kg_relations",
                "kg_meta",
            )
        }

    def documents_snapshot(self):
        return self.connection.execute("SELECT * FROM documents ORDER BY id").fetchall()

    def restore_edge_update_trigger(self):
        statement = next(
            sql
            for sql in graph._schema_statements()
            if sql.startswith("CREATE TRIGGER IF NOT EXISTS kg_edge_types_update ")
        )
        self.connection.execute(statement)
        self.connection.commit()

    def test_persists_after_reopen_and_rebuild_is_deterministic(self):
        original = extraction(1)
        self.add(original)
        self.add(extraction(2, waste="paper waste", resource="recovered fibres"))
        documents = self.documents_snapshot()
        initial = self.build()
        persisted = self.graph_snapshot()
        self.assertEqual(initial["source_claim_count"], 2)
        self.assertFalse(initial["components_split"])
        self.assertFalse(initial["field_accuracy_validated"])
        self.assertFalse(initial["dispatch_approved"])
        self.assertEqual(documents, self.documents_snapshot())

        self.connection.close()
        self.connection = self.connect()
        self.assertEqual(graph.audit_graph(self.connection), initial)
        self.assertEqual(
            graph.inspect_claim(self.connection, original["candidate_id"])["raw_record"], original
        )
        self.assertEqual(persisted, self.graph_snapshot())
        self.assertEqual(self.build(), initial)
        self.assertEqual(persisted, self.graph_snapshot())
        self.assertEqual(documents, self.documents_snapshot())

    def test_raw_aggregated_tuple_and_unverified_provenance_round_trip(self):
        original = extraction(
            1,
            waste="PET bottles, multilayer labels",
            process="  shredding, co-pyrolysis, hydrolysis  ",
            resource="thermal stability",
            reference="10.1000/first_chunk21, 10.1000/second",
        )
        original["doi_search_candidates"] = ["10.1000/first", "10.1000/second"]
        original["flags"].append("upstream_chunk_marker_retained_in_raw")
        self.add(original)
        self.build()
        claim = graph.inspect_claim(self.connection, original["candidate_id"])
        self.assertEqual(claim["raw_record"], original)
        self.assertFalse(claim["dispatch_approved"])
        self.assertEqual(claim["claim_id"], "claim:" + original["candidate_id"])
        process = [node for node in claim["nodes"] if node["kind"] == "ProcessDescription"]
        self.assertEqual(len(process), 1)
        self.assertEqual(process[0]["properties"]["raw_labels"], [original["transforming_process"]])
        self.assertTrue(process[0]["properties"]["opaque"])
        self.assertFalse(process[0]["properties"]["sequence_verified"])
        output = next(node for node in claim["nodes"] if node["kind"] == "OutputDescription")
        self.assertEqual(output["properties"]["raw_labels"], ["thermal stability"])
        self.assertFalse(output["properties"]["product_verified"])
        evidence = next(node for node in claim["nodes"] if node["kind"] == "Evidence")
        self.assertEqual(evidence["properties"]["reference_raw"], original["reference"])
        self.assertEqual(
            evidence["properties"]["doi_candidates"], original["doi_search_candidates"]
        )
        self.assertFalse(evidence["properties"]["article_support_verified"])
        source = next(node for node in claim["nodes"] if node["kind"] == "Source")
        self.assertEqual(source["properties"]["source_key"], "W2RKG-33679")
        self.assertIn("Apache-2.0", source["properties"]["metadata"]["license_scope"])
        self.assertFalse(source["properties"]["metadata"]["article_support_verified"])
        self.assertEqual(graph.graph_candidates(self.connection, "pet")[0]["metadata"], original)

    def test_blank_process_and_reference_remain_missing_without_placeholder_nodes(self):
        originals = [
            extraction(1, process="", reference="", resource="first result"),
            extraction(
                2, waste="paper waste", process=" \t ", reference="", resource="second result"
            ),
        ]
        for original in originals:
            original["flags"] = ["missing_process", "missing_reference"]
            self.add(original)
        audit = self.build()
        self.assertEqual(audit["missing_process_preserved"], 2)
        self.assertEqual(audit["missing_reference_preserved"], 2)
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM kg_nodes WHERE kind='ProcessDescription'"
            ).fetchone()[0],
            0,
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM kg_edges WHERE kind='PROCESS_LABEL'"
            ).fetchone()[0],
            0,
        )
        for original in originals:
            claim = graph.inspect_claim(self.connection, original["candidate_id"])
            self.assertEqual(claim["raw_record"], original)
            self.assertFalse(any(edge["kind"] == "PROCESS_LABEL" for edge in claim["edges"]))
            evidence = next(node for node in claim["nodes"] if node["kind"] == "Evidence")
            self.assertEqual(evidence["properties"]["reference_raw"], "")
            self.assertEqual(evidence["properties"]["doi_candidates"], [])

    def test_shared_process_descriptor_does_not_cross_join_claim_outputs(self):
        first = extraction(
            1, waste="PET feedstock A", process="shared process", resource="output B"
        )
        second = extraction(
            2, waste="paper feedstock C", process="shared process", resource="output D"
        )
        self.add(first)
        self.add(second)
        self.build()
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM kg_nodes WHERE kind='ProcessDescription'"
            ).fetchone()[0],
            1,
        )
        for original, other in ((first, second), (second, first)):
            claim = graph.inspect_claim(self.connection, original["candidate_id"])
            outputs = [
                node["properties"]["raw_labels"]
                for node in claim["nodes"]
                if node["kind"] == "OutputDescription"
            ]
            self.assertEqual(outputs, [[original["transformed_resource"]]])
            self.assertNotIn(
                "claim:" + other["candidate_id"], {node["id"] for node in claim["nodes"]}
            )
            field_edges = [
                edge
                for edge in claim["edges"]
                if edge["kind"] in {"INPUT_LABEL", "PROCESS_LABEL", "OUTPUT_LABEL"}
            ]
            self.assertEqual(len(field_edges), 3)
            self.assertEqual({edge["source_id"] for edge in field_edges}, {claim["claim_id"]})
        self.assertEqual(
            [row["metadata"] for row in graph.graph_candidates(self.connection, "pet")], [first]
        )
        self.assertEqual(
            [row["metadata"] for row in graph.graph_candidates(self.connection, "paper")], [second]
        )

    def test_duplicate_or_conflicting_candidate_ids_roll_back_only_graph_build(self):
        original = extraction(1)
        self.add(original)
        self.build()
        before = self.graph_snapshot()
        for conflict in (False, True):
            with self.subTest(conflicting_tuple=conflict):
                duplicate = deepcopy(original)
                if conflict:
                    duplicate["transformed_resource"] = "conflicting output"
                duplicate_document = self.add(duplicate, document_id="second-document")
                documents = self.documents_snapshot()
                with self.assertRaises(ValueError):
                    self.build()
                self.assertEqual(self.graph_snapshot(), before)
                self.assertEqual(self.documents_snapshot(), documents)
                self.assertIsNotNone(
                    self.connection.execute(
                        "SELECT id FROM documents WHERE id=?", (duplicate_document,)
                    ).fetchone()
                )
                self.connection.execute("DELETE FROM documents WHERE id=?", (duplicate_document,))
                self.connection.commit()
        self.assertEqual(graph.audit_graph(self.connection)["source_claim_count"], 1)

    def test_absent_tuple_field_rejects_without_changing_previous_graph(self):
        self.add(extraction(1))
        self.build()
        before = self.graph_snapshot()
        malformed = extraction(2)
        del malformed["transforming_process"]
        self.add(malformed)
        documents = self.documents_snapshot()
        with self.assertRaises(ValueError):
            self.build()
        self.assertEqual(self.graph_snapshot(), before)
        self.assertEqual(self.documents_snapshot(), documents)

    def test_exact_material_aliases_and_lexical_hints_exclude_petroleum(self):
        pet = extraction(1)
        self.add(pet)
        self.add(extraction(2, waste="petroleum sludge", resource="fuel"))
        self.add(extraction(3, waste="carpet fabric waste", resource="fibres"))
        self.build()
        for alias in ("pet", "PET", "  pet  ", "polyethylene terephthalate", "페트"):
            with self.subTest(alias=alias):
                rows = graph.graph_candidates(self.connection, alias)
                self.assertEqual(
                    [row["metadata"]["candidate_id"] for row in rows], [pet["candidate_id"]]
                )
        for unknown in ("PETroleum", "PET bottle", "plastic"):
            with self.subTest(unknown=unknown):
                self.assertEqual(graph.graph_candidates(self.connection, unknown), [])

    def test_graph_lookup_returns_original_document_with_resolvable_trace(self):
        original = extraction(1)
        document_id = self.add(original, document_id="original-document-id")
        self.build()
        rows = graph.graph_candidates(self.connection, "pet")
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(
            {key: row[key] for key in ("id", "kind", "source_id", "title", "text", "metadata")},
            {
                "id": document_id,
                "kind": "kg_candidate",
                "source_id": "W2RKG-33679",
                "title": "Original display title",
                "text": "Original document text",
                "metadata": original,
            },
        )
        trace = row["graph_trace"]
        self.assertEqual(trace["claim_id"], "claim:" + original["candidate_id"])
        self.assertEqual(trace["material_node_id"], "material:pet")
        self.assertFalse(trace["dispatch_approved"])
        self.assertIsNotNone(
            self.connection.execute(
                "SELECT id FROM kg_nodes WHERE id=?", (trace["claim_id"],)
            ).fetchone()
        )
        dangling = self.connection.execute("""SELECT COUNT(*) FROM kg_edges e
            LEFT JOIN kg_nodes s ON s.id=e.source_id LEFT JOIN kg_nodes t ON t.id=e.target_id
            WHERE s.id IS NULL OR t.id IS NULL""").fetchone()[0]
        self.assertEqual(dangling, 0)
        self.assertEqual(self.connection.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_sql_constraints_reject_domain_range_and_foreign_key_violations(self):
        self.add(extraction(1))
        self.build()
        edge_id, claim_id, waste_id = self.connection.execute(
            "SELECT id,source_id,target_id FROM kg_edges WHERE kind='INPUT_LABEL'"
        ).fetchone()
        with self.assertRaises(sqlite3.IntegrityError):
            self.connection.execute(
                "UPDATE kg_edges SET target_id=? WHERE id=?", (claim_id, edge_id)
            )
        self.connection.rollback()
        with self.assertRaises(sqlite3.IntegrityError):
            self.connection.execute("DELETE FROM kg_nodes WHERE id=?", (waste_id,))
        self.connection.rollback()
        self.assertEqual(graph.audit_graph(self.connection)["source_claim_count"], 1)

    def test_audit_rejects_domain_range_corruption_after_trigger_bypass(self):
        self.add(extraction(1))
        self.build()
        self.connection.execute("DROP TRIGGER kg_edge_types_update")
        self.connection.execute("UPDATE kg_edges SET target_id=source_id WHERE kind='INPUT_LABEL'")
        self.connection.commit()
        self.restore_edge_update_trigger()
        with self.assertRaisesRegex(ValueError, "domain/range"):
            graph.audit_graph(self.connection, check_source_records=False)

    def test_audit_rejects_dangling_endpoint_after_foreign_key_bypass(self):
        self.add(extraction(1))
        self.build()
        self.connection.execute("DROP TRIGGER kg_edge_types_update")
        self.connection.execute("PRAGMA foreign_keys=OFF")
        self.connection.execute(
            "UPDATE kg_edges SET target_id='missing-node' WHERE kind='INPUT_LABEL'"
        )
        self.connection.commit()
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.restore_edge_update_trigger()
        with self.assertRaisesRegex(ValueError, "Dangling"):
            graph.audit_graph(self.connection, check_source_records=False)

    def test_changed_source_document_is_rejected_until_graph_rebuild(self):
        original = extraction(1)
        document_id = self.add(original)
        self.build()
        self.connection.execute(
            "UPDATE documents SET text=? WHERE id=?", ("Changed source text", document_id)
        )
        self.connection.commit()
        with self.assertRaises(sqlite3.DatabaseError):
            graph.graph_candidates(self.connection, "pet")
        with self.assertRaisesRegex(ValueError, "source record changed"):
            graph.audit_graph(self.connection)
        self.build()
        self.assertEqual(
            graph.graph_candidates(self.connection, "pet")[0]["text"], "Changed source text"
        )


if __name__ == "__main__":
    unittest.main()
