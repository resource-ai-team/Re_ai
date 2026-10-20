"""Real KG/local-RAG integration probe and separately labelled synthetic arithmetic."""

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from resource_ai.core.config import get_settings
from resource_ai.main import app


def evaluate():
    client, checks, cases = TestClient(app), [], []

    def check(label, condition):
        checks.append({"name": label, "passed": bool(condition)})

    for material, form in [
        ("PP", "scrap"),
        ("PE", "film"),
        ("PET", "bottle"),
        ("알루미늄", "can"),
        ("종이", "cardboard"),
    ]:
        response = client.post(
            "/analysis/research",
            json={
                "lot": {
                    "material": material,
                    "input_form": form,
                    "quantity_kg": 3000,
                    "page_size": 1,
                },
                "max_legal_queries": 3,
            },
        )
        check(f"{material}: research API 200", response.status_code == 200)
        if response.status_code != 200:
            cases.append({"material": material, "http_status": response.status_code})
            continue
        data = response.json()
        coverage = data["coverage"]
        check(
            f"{material}: all enumerated paths retained",
            len(data["candidates"]) == coverage["enumerated_in_graph"] > 0
            and coverage["all_pages_read"],
        )
        check(
            f"{material}: all paths have legal tasks",
            all(c["legal_tasks"] for c in data["candidates"]),
        )
        check(
            f"{material}: no operational approval",
            data["route"] == "REVIEW"
            and not data["final_recommendations"]
            and not data["dispatch_approved"],
        )
        check(f"{material}: no legal audit claimed", not coverage["legal_audit_complete"])
        searches = [
            {
                "search_id": s["search_id"],
                "status": s["status"],
                "attempted": s["attempted"],
                "result_count": len(s["results"]),
            }
            for s in data["legal_searches"]
        ]
        cases.append(
            {
                "input_is_synthetic": True,
                "material": material,
                "coverage": coverage,
                "graph_trace": data["graph_trace"],
                "legal_search_statuses": searches,
                "candidate_ids": [c["plan"]["plan_id"] for c in data["candidates"]],
            }
        )

    request = json.loads(
        Path("data/config/scenario_comparison_demo.json").read_text(encoding="utf8")
    )
    response = client.post("/analysis/compare-scenarios", json=request)
    check("synthetic arithmetic API 200", response.status_code == 200)
    arithmetic = response.json()
    if response.status_code == 200:
        check(
            "synthetic: Pareto A/B before C",
            arithmetic["pareto_fronts"] == [["simulation-A", "simulation-B"], ["simulation-C"]],
        )
        check("synthetic: no final recommendation", not arithmetic["final_recommendations"])
    return {
        "evaluated_at": datetime.now(UTC).isoformat(),
        "local_retrieval_config": {
            "embedding_provider": get_settings().embedding_provider,
            "embedding_model": get_settings().embedding_model,
            "database_connect_timeout_seconds": get_settings().database_connect_timeout_seconds,
        },
        "scope": "KG/API wiring and arithmetic regression; not expert/permit/recommendation accuracy",
        "cases": cases,
        "arithmetic_input_is_entirely_synthetic": True,
        "arithmetic": arithmetic,
        "checks": checks,
        "passed": sum(c["passed"] for c in checks),
        "total": len(checks),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = evaluate()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf8")
    print(f"Analysis wiring/arithmetic: {result['passed']}/{result['total']} passed")
    if result["passed"] != result["total"]:
        raise SystemExit(1)
