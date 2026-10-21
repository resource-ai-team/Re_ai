"""Real API route regression with synthetic inputs, never plant operating validation."""

import argparse
import json
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path

from fastapi.testclient import TestClient

from resource_ai.knowledge_graph.graph_store import audit_generation
from resource_ai.main import app

CASES = [
    (
        {"material": "PET", "input_form": "bottle"},
        [
            ("pet-prepare", "pet-wash", "pet-finish"),
            ("pet-prepare", "pet-wash", "pet-finish", "pet-pelletise"),
        ],
    ),
    ({"material": "PET", "input_form": "flake"}, [("pet-pelletise",)]),
    (
        {"material": "PP", "input_form": "scrap", "color": "검정", "quantity_kg": 3000},
        [
            ("pp-prepare",),
            ("pp-prepare", "pp-clean", "pp-pelletise"),
            ("pp-prepare", "pp-clean", "pp-pelletise", "pp-compound"),
        ],
    ),
    (
        {"material": "PP", "input_form": "shredded"},
        [("pp-clean", "pp-pelletise"), ("pp-clean", "pp-pelletise", "pp-compound")],
    ),
    (
        {"material": "HDPE", "input_form": "scrap"},
        [
            ("pe-prepare",),
            ("pe-prepare", "pe-clean", "pe-pelletise"),
            ("pe-prepare", "pe-clean", "pe-pelletise", "pe-compound"),
        ],
    ),
    ({"material": "알루미늄", "input_form": "can"}, [("al-can-sheet",)]),
    ({"material": "알루미늄", "input_form": "chips"}, []),
    (
        {"material": "종이", "input_form": "cardboard"},
        [
            (
                "paper-pulping",
                "paper-stock-preparation",
                "paper-forming",
                "paper-drying",
                "paper-board",
            )
        ],
    ),
    ({"material": "PP", "input_form": "scrap", "goal_state": "pp-shredded"}, [("pp-prepare",)]),
]


def evaluate():
    audit = audit_generation()
    client, checks, scenarios = TestClient(app), [], []

    def check(name, result):
        checks.append({"name": name, "passed": bool(result)})

    for number, (context, expected) in enumerate(CASES, 1):
        prefix, cursor, items, pages = f"case {number}", None, [], []
        for _ in range(100):
            response = client.post(
                "/kg/process-plans", json={**context, "page_size": 1, "cursor": cursor}
            )
            check(prefix + " API 200", response.status_code == 200)
            if response.status_code != 200:
                break
            page = response.json()
            pages.append(page)
            items.extend(page["items"])
            cursor = page["pagination"]["next_cursor"]
            if cursor is None:
                break
        packets = [i["research_packet"] for i in items]
        paths = [tuple(s["transition"]["key"] for s in p["stages"]) for p in packets]
        check(prefix + " independently specified paths", sorted(paths) == sorted(expected))
        check(
            prefix + " no missing or duplicate page",
            cursor is None and len(items) == len({i["plan_id"] for i in items}),
        )
        check(
            prefix + " immutable graph version",
            bool(pages)
            and all(p["graph_trace"]["generation"] == audit["generation"] for p in pages),
        )
        check(
            prefix + " complete within declared reference scope",
            bool(pages) and all(p["search_scope"]["all_paths_enumerated"] for p in pages),
        )
        check(
            prefix + " not ranked or approved",
            all(
                not i["recommended"]
                and not i["dispatch_approved"]
                and i["state"] == "graph_compatible"
                for i in items
            ),
        )
        check(
            prefix + " no fabricated cost or carbon",
            all(
                p["cost"]["total_krw"] is None and p["carbon"]["total_kg_co2e"] is None
                for p in packets
            ),
        )
        linked = True
        for packet in packets:
            stages = packet["stages"]
            check(
                prefix + " typed state continuity",
                all(
                    a["output_state"]["key"] == b["input_state"]["key"] for a, b in pairwise(stages)
                ),
            )
            check(
                prefix + " shape is not specification proof",
                packet["input_state_specifications_verified"] is False,
            )
            source_ids = {e["key"] for e in packet["evidence"]}
            targets = {s["stage_id"] for s in stages} | {h["leg_id"] for h in packet["handoffs"]}
            linked &= all(
                set(c["source_ids"]) <= source_ids and c["target_id"] in targets
                for c in packet["checks"]
            )
            for stage in stages:
                facility = stage["facility"]
                check(
                    prefix + " current facility facts unknown",
                    facility is not None
                    and all(
                        facility[k] is None
                        for k in (
                            "current_acceptance",
                            "current_permit_scope",
                            "current_available_capacity_kg",
                            "current_price_krw_per_kg",
                        )
                    ),
                )
                linked &= (
                    stage["capability"]["transition"] == stage["transition"]["key"]
                    and stage["capability"]["facility"] == facility["key"]
                )
        check(prefix + " stage risk targets and provenance resolve", linked)
        scenarios.append(
            {
                "input": context,
                "input_is_synthetic_not_factory_measurement": True,
                "facility_records_are_public_reference_cases_not_acceptance": True,
                "expected_transition_paths": expected,
                "received_transition_paths": paths,
                "pages": len(pages),
                "plans": len(items),
            }
        )

    shallow = client.post(
        "/kg/process-plans", json={"material": "PET", "input_form": "bottle", "max_steps": 2}
    ).json()
    check(
        "short horizon never claims complete search",
        shallow.get("search_scope", {}).get("depth_limited") is True
        and shallow["search_scope"]["all_paths_enumerated"] is False,
    )
    unknown = client.post("/kg/process-plans", json={"material": "PET"}).json()
    check(
        "missing form labels state assumptions",
        bool(unknown.get("items"))
        and all(i["research_packet"]["input_form_assumed"] for i in unknown["items"]),
    )
    external = client.post(
        "/kg/process-plans",
        json={
            "material": "PP",
            "input_form": "scrap",
            "page_size": 1,
            "audience": "external_model",
            "region": "synthetic-private-factory",
        },
    )
    check(
        "external context removed",
        external.status_code == 200
        and external.json()["input_context"] == {}
        and "synthetic-private-factory" not in external.text,
    )
    check(
        "unknown goal is HTTP 400",
        client.post(
            "/kg/process-plans", json={"material": "PP", "goal_state": "al-sheet"}
        ).status_code
        == 400,
    )
    return {
        "evaluated_at": datetime.now(UTC).isoformat(),
        "scope": "source-backed reference path/API integrity regression; not permits, economics or plant validation",
        "generation": audit["generation"],
        "graph_sha256": audit["graph_sha256"],
        "ontology_id": audit["ontology_id"],
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
    print(f"KG process plans: {result['passed']}/{result['total']} passed")
    if result["passed"] != result["total"]:
        raise SystemExit(1)
