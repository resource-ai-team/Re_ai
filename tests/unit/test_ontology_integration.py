"""Scoped repository evidence + route occurrences, without the full corpus or AI."""

import json
import sqlite3
import unittest

from resource_ai.knowledge_graph import ontology as graph
from resource_ai.knowledge_graph.dataset import allowed_cards as _allowed_cards


class CuratedOntologyTests(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(":memory:")
        self.addCleanup(self.connection.close)
        self.connection.execute(
            "CREATE TABLE documents(id TEXT PRIMARY KEY,kind TEXT,source_id TEXT,title TEXT,text TEXT,metadata TEXT)"
        )
        self.audit = graph.build_graph(self.connection)

    def nodes(self, kind):
        return [
            (row[0], json.loads(row[1]))
            for row in self.connection.execute(
                "SELECT id,properties FROM kg_nodes WHERE kind=?", (kind,)
            )
        ]

    def test_imports_only_eligible_cards_without_inventing_transformation_tuples(self):
        cards = _allowed_cards()
        actual = {value["card_id"]: value for _, value in self.nodes("ScopedClaim")}
        self.assertEqual(set(actual), set(cards))
        self.assertNotIn("TECH-025", actual)
        self.assertEqual(self.audit["source_claim_count"], 0)
        self.assertEqual(self.audit["edge_counts"].get("INPUT_LABEL", 0), 0)
        for key, value in actual.items():
            self.assertEqual(value["raw_record"], cards[key])
            self.assertFalse(value["dispatch_approved"])
            self.assertEqual(value["scope"], cards[key]["scope"])

    def test_occurrence_identity_separates_pet_bottle_and_flake_snapshots(self):
        routes = [
            (key, value)
            for key, value in self.nodes("RouteSnapshot")
            if value["original_route_id"] == "process-pet-chip-upgrading"
        ]
        self.assertEqual(
            {value["input_form"] for _, value in routes}, {"unknown", "bottle", "flake"}
        )
        self.assertEqual(len({key for key, _ in routes}), 3)
        repeated = [
            value
            for _, value in self.nodes("StepOccurrence")
            if value["original_id"] == "process-pet-chip-upgrading-n1"
        ]
        self.assertEqual(len({value["route_snapshot_id"] for value in repeated}), 3)
        self.assertTrue(all(value["dispatch_approved"] is False for _, value in routes))

    def test_audit_rejects_cross_snapshot_risk_target_even_with_valid_types(self):
        risks, steps = self.nodes("Risk"), self.nodes("StepOccurrence")
        risk_id, risk = risks[0]
        other_step = next(
            key for key, value in steps if value["route_snapshot_id"] != risk["route_snapshot_id"]
        )
        self.connection.execute(
            "UPDATE kg_edges SET target_id=? WHERE kind='TARGETS' AND source_id=?",
            (other_step, risk_id),
        )
        with self.assertRaisesRegex(ValueError, "Cross-snapshot risk"):
            graph.audit_graph(self.connection)

    def test_audit_rejects_modified_definition_and_material_alias(self):
        self.connection.execute("DELETE FROM kg_relations WHERE kind='HAS_STEP'")
        with self.assertRaisesRegex(ValueError, "relation definitions"):
            graph.audit_graph(self.connection)


if __name__ == "__main__":
    unittest.main()
