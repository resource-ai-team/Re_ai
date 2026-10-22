"""Replay synthetic documentary cases through the real API/Neo4j boundary."""

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from resource_ai.main import app


def evaluate():
    corpus = json.loads(
        Path("data/config/facility_assessment_eval.json").read_text(encoding="utf8")
    )
    client, checks, cases = TestClient(app), [], []

    def check(name, passed):
        checks.append({"name": name, "passed": bool(passed)})

    for case in corpus["cases"]:
        response = client.post(
            "/kg/facility-assessment",
            json={"capability_key": case["capability"], "lot": case["lot"]},
        )
        check(case["id"] + " API 200", response.status_code == 200)
        if response.status_code != 200:
            continue
        data = response.json()
        assessment = data["assessment"]
        found = {c["code"]: c for c in assessment["checks"]}
        check(case["id"] + " expected outcome", assessment["status"] == case["status"])
        for key, expected in case["checks"].items():
            check(case["id"] + " " + key, found[key]["status"] == expected)
        check(
            case["id"] + " no approval",
            assessment["dispatch_approved"] is False
            and assessment["all_operational_conditions_verified"] is False,
        )
        check(
            case["id"] + " observed conditions are not an actual incident",
            all(r["actual_incident_confirmed"] is False for r in assessment["risks"]),
        )
        cases.append(
            {"id": case["id"], "status": assessment["status"], "graph_trace": data["graph_trace"]}
        )

    scenario_request = {
        "material": "PP",
        "input_form": "scrap",
        "quantity_kg": 3000,
        "color": "검정",
        "moisture_pct": 25,
        "contamination_pct": 12,
        "designated_waste": False,
    }
    response = client.post("/kg/assessed-plans", json=scenario_request)
    check("PP full scenario API 200", response.status_code == 200)
    scenario = {"input_is_synthetic": True, "request": scenario_request, "paths": []}
    if response.status_code == 200:
        result = response.json()
        check(
            "PP three paths retained",
            len(result["planning"]["items"]) == len(result["assessments"]) == 3,
        )
        check("PP no final recommendation", result["recommended"] is False)
        scenario["graph_trace"] = result["planning"]["graph_trace"]
        for plan in result["assessments"]:
            summary = {"plan_id": plan["plan_id"], "status": plan["status"], "stages": []}
            for index, stage in enumerate(plan["stages"]):
                a = stage["assessment"]
                if a is None:
                    check("PP facility evidence present", False)
                    continue
                check("PP stage no approval", a["dispatch_approved"] is False)
                if index:
                    check(
                        "PP downstream lot facts not copied",
                        all(v is None for v in a["input_context"].values()),
                    )
                    check(
                        "PP downstream state not treated as measured",
                        a["input_basis"] == "projected_state_not_measured",
                    )
                summary["stages"].append(
                    {
                        "stage_id": stage["stage_id"],
                        "transition_key": stage["transition_key"],
                        "facility_key": a["facility_key"],
                        "input_basis": a["input_basis"],
                        "status": a["status"],
                        "check_statuses": {c["code"]: c["status"] for c in a["checks"]},
                        "risks": a["risks"],
                    }
                )
            scenario["paths"].append(summary)
    return {
        "evaluated_at": datetime.now(UTC).isoformat(),
        "scope": corpus["scope"],
        "case_version": corpus["version"],
        "cases": cases,
        "checks": checks,
        "passed": sum(c["passed"] for c in checks),
        "total": len(checks),
    }, scenario


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--scenario-output", type=Path)
    args = parser.parse_args()
    result, scenario = evaluate()
    for path, value in [(args.output, result), (args.scenario_output, scenario)]:
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(value, ensure_ascii=False, indent=2) + "\n",
                encoding="utf8",
                newline="\n",
            )
    print(f"Facility condition checks: {result['passed']}/{result['total']} passed")
    if result["passed"] != result["total"]:
        raise SystemExit(1)
