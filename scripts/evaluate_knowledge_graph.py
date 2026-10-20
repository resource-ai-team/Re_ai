"""Real Neo4j API checks; no model/API charges or invented factory baseline."""

import argparse
import json
from pathlib import Path

from fastapi.testclient import TestClient

from resource_ai.knowledge_graph.graph_store import audit_generation
from resource_ai.main import app


def evaluate() -> dict:
    audit = audit_generation()
    checks = []

    def check(name, passed):
        checks.append({"name": name, "passed": bool(passed)})

    check("complete W2RKG source claims", audit["source_claim_count"] == 33679)
    check("blank process preserved", audit["missing_process_preserved"] == 7)
    check("blank references preserved", audit["missing_reference_preserved"] == 686)
    check("eligible evidence cards", audit["node_counts"].get("ScopedClaim") == 69)
    check("scoped authored route snapshots", audit["node_counts"].get("RouteSnapshot") == 18)
    check("no dispatch approval", audit["dispatch_approved"] is False)
    scenarios = []
    client = TestClient(app)
    for material, form in [
        ("PET", "bottle"),
        ("알루미늄", "chips"),
        ("종이", "unknown"),
        ("PP", None),
        ("PE", None),
    ]:
        payload = {"material": material, "input_form": form, "top_k": 200}
        response = client.post("/kg/candidates", json=payload)
        check(material + " API 200", response.status_code == 200)
        if response.status_code != 200:
            continue
        result = response.json()
        check(material + " source candidates exist", len(result["literature_candidates"]) > 0)
        check(
            material + " same immutable generation",
            result["graph_trace"]["generation"] == audit["generation"],
        )
        check(
            material + " baseline not invented",
            result["input_context"]["current_treatment"] is None,
        )
        check(material + " no approval", result["dispatch_approved"] is False)
        check(
            material + " curated coverage is explicit",
            bool(result["process_candidates"]) == (material not in {"PP", "PE"}),
        )
        ids = {e["evidence_id"] for e in result["evidence"]}
        for candidate in result["process_candidates"]:
            route, evidence_map = candidate["route"], candidate["evidence_map"]
            check(
                candidate["snapshot_id"] + " evidence resolves", set(evidence_map.values()) <= ids
            )
            targets = {n["id"] for n in route["nodes"]} | {e["id"] for e in route["edges"]}
            check(
                candidate["snapshot_id"] + " risk targets resolve",
                all(r["target_id"] in targets for r in route["risks"]),
            )
        scenarios.append(
            {
                "input": payload,
                "literature_count": len(result["literature_candidates"]),
                "process_count": len(result["process_candidates"]),
                "evidence_count": len(result["evidence"]),
                "missing_fields": result["missing_fields"],
            }
        )
    unknown = client.post("/kg/candidates", json={}).json()
    check(
        "unknown composition preserved",
        unknown["status"] == "needs_input" and unknown["input_context"]["material"] is None,
    )
    check(
        "unknown material never mapped to PET",
        client.post("/kg/candidates", json={"material": "PETroleum"}).json()["status"]
        == "unsupported_material",
    )
    return {
        "scope": "graph integrity and API regression; not recommendation accuracy or plant validation",
        "audit": audit,
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
    encoded = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded + "\n", encoding="utf8")
    print(f"Knowledge graph: {result['passed']}/{result['total']} passed")
    if result["passed"] != result["total"]:
        raise SystemExit(1)
