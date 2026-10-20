"""Full reference-pool regression, not recycling suitability or recommendation accuracy."""

import argparse
import json
import re
from pathlib import Path

from fastapi.testclient import TestClient

from resource_ai.knowledge_graph import ontology as graph
from resource_ai.knowledge_graph.dataset import allowed_cards, authored_reports, read_candidates
from resource_ai.knowledge_graph.graph_store import audit_generation
from resource_ai.main import app


def expected_pool(material):
    pattern = graph.ontology()["materials"][material]["search_pattern"]
    literature = {
        "claim:" + row["candidate_id"]
        for row in read_candidates()
        if re.search(pattern, row["waste"], re.IGNORECASE)
    }
    cards = {
        graph.identity("scoped-claim", key)
        for key, card in allowed_cards().items()
        if card.get("material") in {material, "multiple"}
    }
    routes = set()
    for report in authored_reports():
        if report["material"]["id"] != material:
            continue
        for route in report["process_graph"]["routes"]:
            content = {
                "material": material,
                "input_form": report["process_graph"].get("input_form", "unknown"),
                "route": route,
                "evidence_map": {
                    e["id"]: graph.identity("evidence", ["card", e["document_id"].split(":", 1)[1]])
                    if e["document_id"].startswith("process-card:")
                    else graph.identity("evidence", e["document_id"])
                    for e in report["evidence"]
                },
                "evidence_fingerprint": graph.digest(report["evidence"]),
            }
            routes.add("route:" + graph.digest(content)[:32])
    return {"literature": literature, "scoped_claim": cards, "process": routes}


def evaluate():
    audit = audit_generation()
    client = TestClient(app)
    checks, scenarios = [], []

    def check(name, passed):
        checks.append({"name": name, "passed": bool(passed)})

    for material, alias in [
        ("pet", "PET"),
        ("aluminium", "알루미늄"),
        ("paper", "종이"),
        ("pp", "PP"),
        ("pe", "HDPE"),
    ]:
        context = {"material": alias, "page_size": 37}
        if material == "pp":
            context.update(color="검정", quantity_kg=3000, generation_process="사출")
        cursor, items, pages, generations = None, [], [], set()
        for _ in range(100):
            response = client.post("/kg/candidate-pool", json={**context, "cursor": cursor})
            check(f"{material} page {len(pages) + 1} API 200", response.status_code == 200)
            if response.status_code != 200:
                break
            page = response.json()
            pages.append(page)
            items.extend(page["items"])
            generations.add(page["graph_trace"]["generation"])
            cursor = page["pagination"]["next_cursor"]
            if cursor is None:
                break
        expected = expected_pool(material)
        expected_ids = set.union(*expected.values())
        actual_ids = [i["candidate_id"] for i in items]
        check(material + " paging terminates", cursor is None)
        check(material + " source-exact complete candidate pool", set(actual_ids) == expected_ids)
        check(material + " no duplicate pages", len(actual_ids) == len(set(actual_ids)))
        check(material + " immutable generation", generations == {audit["generation"]})
        check(
            material + " total matches independent source counts",
            all(p["pagination"]["total_candidates"] == len(expected_ids) for p in pages),
        )
        check(
            material + " input form does not discard candidates",
            all(p["graph_trace"]["form_filter_applied"] is False for p in pages),
        )
        check(
            material + " no premature top K",
            all(p["graph_trace"]["top_k_applied"] is False for p in pages),
        )
        check(
            material + " no invented baseline",
            all(p["input_context"]["current_treatment"] is None for p in pages),
        )
        check(
            material + " no suitability decision",
            all(i["review_state"] == "unassessed" and not i["dispatch_approved"] for i in items),
        )
        linked = True
        questions_only = True
        for item in items:
            packet = item["research_packet"]
            evidence_ids = {e["evidence_id"] for e in packet["evidence"]}
            for condition in packet["conditions"]:
                linked &= set(condition["evidence_ids"]) <= evidence_ids
                questions_only &= condition["machine_rule_ready"] is False
            if packet["process_graph"]:
                process = packet["process_graph"]
                linked &= set(process["evidence_map"].values()) <= evidence_ids
                linked &= all(
                    r["target_id"] in process["occurrence_ids"] for r in process["route"]["risks"]
                )
        check(material + " risk and condition evidence resolves", linked)
        check(material + " questions are not eligibility rules", questions_only)
        scenarios.append(
            {
                "input": context,
                "synthetic_case_not_factory_measurement": True,
                "counts": {k: len(v) for k, v in expected.items()},
                "total": len(items),
                "pages": len(pages),
                "missing_fields": pages[0]["missing_fields"] if pages else [],
                "received_kind_counts": {k: sum(i["kind"] == k for i in items) for k in expected},
            }
        )

    external = client.post(
        "/kg/candidate-pool",
        json={
            "material": "PET",
            "page_size": 100,
            "region": "synthetic-private-factory",
            "audience": "external_model",
        },
    )
    check("external model API 200", external.status_code == 200)
    result = external.json()
    check("private request context not exported", result.get("input_context") == {})
    check(
        "blocked candidate bodies withheld",
        any(not i["external_model_allowed"] for i in result.get("items", []))
        and all(
            i["research_packet"] is None
            for i in result.get("items", [])
            if not i["external_model_allowed"]
        ),
    )
    check("no private context leaked", "synthetic-private-factory" not in external.text)
    check(
        "top K is not a pool request field",
        client.post("/kg/candidate-pool", json={"material": "PET", "top_k": 1}).status_code == 422,
    )
    return {
        "pipeline_baseline": "2026-10-20-fixed-v1",
        "scope": "reference coverage, pagination, provenance and API regression; not legal or plant validation",
        "generation": audit["generation"],
        "graph_sha256": audit["graph_sha256"],
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
    print(f"KG research pool: {result['passed']}/{result['total']} passed")
    if result["passed"] != result["total"]:
        raise SystemExit(1)
