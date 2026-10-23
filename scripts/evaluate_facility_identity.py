"""Opt-in actual KG/registry API receipt. Never writes raw registry text."""

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from resource_ai.knowledge_graph import graph_store as store
from resource_ai.knowledge_graph.facility_identity import identity_evidence
from resource_ai.knowledge_graph.facility_registry import read_registry
from resource_ai.knowledge_graph.planning import load_reference
from resource_ai.main import app


def evaluate():
    config = store.settings()

    def snapshot(tx):
        generation, manifest = store._head(tx, config)
        value, fingerprint = load_reference(tx, config, generation, manifest)
        return generation, fingerprint, value, read_registry(tx, config)

    generation, fingerprint, value, registry = store._read(config, snapshot)
    if registry is None:
        raise ValueError("Import the local facility registry before this opt-in evaluation")
    originals = {row["listing_id"]: row for row in registry["records"]}
    client, rows = TestClient(app), []
    for facility in value.facilities:
        for link in facility.registry_identity_links:
            record = originals.get(link.listing_id)
            if record is None:
                rows.append({"facility_key": facility.key, "status": "not_in_active_snapshot"})
                continue
            for transition in ("pet-wash", "pp-clean"):
                # Address is used locally to find the reviewed row, never emitted in the receipt.
                response = client.post(
                    "/kg/facility-candidates",
                    json={
                        "transition_key": transition,
                        "region": record["address"],
                        "page_size": 100,
                    },
                )
                response.raise_for_status()
                data = response.json()
                trace = data["graph_trace"]
                assert trace["generation"] == generation
                assert trace["reference_sha256"] == fingerprint
                assert trace["registry_generation"] == registry["generation"]
                assert trace["registry_source_sha256"] == link.registry_source_sha256
                hits = [
                    r for r in data["registry_candidates"] if r["listing_id"] == link.listing_id
                ]
                assert len(hits) == 1, "Reviewed row not found within the address page limit"
                packet = hits[0]["research_packet"]
                expected = identity_evidence(value, record, transition)
                assert packet["evidence_links"] == expected
                assert expected["identity_status"] == "reviewed_same_site"
                assert packet["stage_capability"] == expected["stage_capability"]
                assert packet["current_acceptance"] is None
                assert packet["current_permit_scope"] is None
                assert not hits[0]["dispatch_approved"]
                rows.append(
                    {
                        "facility_key": facility.key,
                        "listing_id": link.listing_id,
                        "transition_key": transition,
                        "identity_status": expected["identity_status"],
                        "stage_capability": expected["stage_capability"],
                        "capability_keys": expected["capability_keys"],
                        "source_evidence": expected["source_evidence"],
                        "current_acceptance": None,
                        "current_permit_scope": None,
                        "dispatch_approved": False,
                    }
                )
    return {
        "evaluated_at": datetime.now(UTC).isoformat(),
        "scope": "Actual KG and registry HTTP; not an operational permit or intake accuracy evaluation",
        "graph_generation": generation,
        "reference_sha256": fingerprint,
        "registry_generation": registry["generation"],
        "registry_source_sha256": registry["manifest"]["source_sha256"],
        "registry_record_count": len(originals),
        "rows": rows,
        "registry_raw_text_included": False,
        "actual_api_rows_checked": sum("transition_key" in row for row in rows),
        "generation_model_calls": 0,
        "dispatch_approved": False,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = evaluate()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf8")
    print(f"Actual facility identity API rows checked: {report['actual_api_rows_checked']}")
