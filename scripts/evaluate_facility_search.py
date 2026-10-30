"""Facility API integrity checks; no field validation or acceptance-accuracy claims."""

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from resource_ai.knowledge_graph import graph_store as store
from resource_ai.knowledge_graph.facility_codes import code_interpretations, load_codebook
from resource_ai.knowledge_graph.facility_identity import identity_evidence
from resource_ai.knowledge_graph.facility_registry import read_registry
from resource_ai.knowledge_graph.planning_reference import reference
from resource_ai.main import app


def evaluate():
    config = store.settings()
    snapshot = store._read(config, lambda tx: read_registry(tx, config))
    codebook, value = load_codebook(), reference()
    client, checks, scenarios = TestClient(app), [], []
    source = {r["listing_id"]: r for r in snapshot["records"]} if snapshot else {}

    def check(name, result):
        checks.append({"name": name, "passed": bool(result)})

    for stage in [
        "pp-prepare",
        "pp-clean",
        "pe-pelletise",
        "pet-wash",
        "al-can-sheet",
        "paper-board",
    ]:
        response = client.post(
            "/kg/facility-candidates", json={"transition_key": stage, "page_size": 5}
        )
        check(stage + " API 200", response.status_code == 200)
        page = response.json()
        if response.status_code != 200:
            continue
        check(
            stage + " explicit registry state",
            page["search_scope"]["registry_imported"] == bool(snapshot),
        )
        check(
            stage + " no automatic path insertion",
            page["search_scope"]["automatic_path_insertion"] is False,
        )
        for item in page["registry_candidates"]:
            packet, original = item["research_packet"], source[item["listing_id"]]
            check(
                stage + " selected source record intact",
                all(packet[k] == original[k] for k in original if k != "raw_record"),
            )
            check(stage + " registration role only", original["is_recycling_registration"] is True)
            check(
                stage + " versioned code interpretations are traceable, not permit validation",
                packet["code_interpretations"]
                == code_interpretations(original["waste_codes"], page["material"], codebook)
                and packet["code_mapping_source"] == codebook["source"]
                and page["graph_trace"]["codebook_sha256"] == codebook["sha256"],
            )
            check(
                stage + " process suitability not fabricated",
                packet["evidence_links"] == identity_evidence(value, original, stage)
                and packet["stage_capability"] == packet["evidence_links"]["stage_capability"]
                and packet["process_capability_verified"] is False,
            )
            check(
                stage + " permit and acceptance unknown",
                packet["current_acceptance"] is None
                and packet["current_permit_scope"] is None
                and packet["current_available_capacity_kg"] is None,
            )
            check(
                stage + " no road cost fabrication",
                packet["road_distance_km"] is None and packet["transport_cost_krw"] is None,
            )
        for item in page["reference_bindings"]:
            packet = item["research_packet"]
            if packet:
                check(
                    stage + " published process does not approve this lot",
                    packet["assessment"]["ready_for_dispatch"] is False
                    and packet["assessment"]["current_acceptance"] == "needs_verification",
                )
        scenarios.append(
            {
                "transition_key": stage,
                "total_registry_candidates": page["pagination"]["total_registry_candidates"],
                "returned": len(page["registry_candidates"]),
                "reference_bindings": len(page["reference_bindings"]),
                "search_scope": page["search_scope"],
                "shown_match_types": [
                    i["research_packet"]["material_match"] for i in page["registry_candidates"]
                ],
                "shown_equipment_mentions": [
                    i["research_packet"]["equipment_mentions"] for i in page["registry_candidates"]
                ],
            }
        )
    external = client.post(
        "/kg/facility-candidates",
        json={"transition_key": "pp-prepare", "audience": "external_model"},
    )
    check(
        "external registry packet withheld",
        external.status_code == 200
        and all(
            i["research_packet"] is None for i in external.json().get("registry_candidates", [])
        ),
    )
    check(
        "unknown transition HTTP 400",
        client.post("/kg/facility-candidates", json={"transition_key": "not-a-stage"}).status_code
        == 400,
    )
    check(
        "bad cursor HTTP 400",
        client.post(
            "/kg/facility-candidates", json={"transition_key": "pp-prepare", "cursor": "%%%"}
        ).status_code
        == 400,
    )
    return {
        "evaluated_at": datetime.now(UTC).isoformat(),
        "scope": "listing provenance, role separation, uncertainty and API regression; not acceptance or process accuracy",
        "registry_generation": snapshot["generation"] if snapshot else None,
        "registry_source_sha256": snapshot["manifest"]["source_sha256"] if snapshot else None,
        "registry_record_count": len(source),
        "codebook_sha256": codebook["sha256"],
        "codebook_source": codebook["source"],
        "scenarios": scenarios,
        "checks": checks,
        "passed": sum(c["passed"] for c in checks),
        "total": len(checks),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = evaluate()
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf8"
        )
    print(f"Facility API: {result['passed']}/{result['total']} passed")
    if result["passed"] != result["total"]:
        raise SystemExit(1)
