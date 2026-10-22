"""Read-only real KG/local-RAG wiring with explicitly synthetic lots and numbers.

This probe never enables a generation model or writes to either graph/database.
Passing checks establish binding/calculation behavior, not recommendation quality.
"""

import argparse
import json
from copy import deepcopy
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from resource_ai.core.config import get_settings
from resource_ai.core.dependencies import get_repository
from resource_ai.main import app
from resource_ai.rag.evaluation import evaluate_case, load_retrieval_cases


def synthetic_numeric(template):
    """Fill real path IDs with artificial activities/factors, never a real estimate."""
    basis = {
        "quantity_kg": template["basis_template"]["quantity_kg"],
        "region": "KR",
        "as_of": "2026-10-22",
        "assessment_year": 2026,
        "carbon_method": "SYNTHETIC-WIRING-PROBE-ONLY",
    }
    evidence = [
        {
            "evidence_id": kind,
            "source_url": "https://example.invalid/synthetic-wiring-probe",
            "locator": "Artificial numbers; not a quote, transport distance or LCI factor",
            "kind": kind,
            "valid_from": "2026-01-01",
            "valid_until": "2026-12-31",
            "region": "KR",
            "assessment_year": 2026,
            "carbon_method": basis["carbon_method"],
            "synthetic": True,
        }
        for kind in ("activity_record", "cost_factor", "carbon_factor")
    ]
    scenarios = []
    for index, candidate in enumerate(template["candidates"]):
        scenarios.append(
            {
                "candidate_id": candidate["candidate_id"],
                "basis": basis,
                "stage_ids": candidate["stage_ids"],
                "handoff_ids": candidate["handoff_ids"],
                "lines": [
                    {
                        **line,
                        "activity": "1",
                        "activity_unit": "kg",
                        "factor": str(index + 1),
                        "factor_unit": "KRW/kg" if line["metric"] == "cost" else "kgCO2e/kg",
                        "activity_evidence_id": "activity_record",
                        "factor_evidence_id": line["metric"] + "_factor",
                    }
                    for line in candidate["required_lines"]
                ],
            }
        )
    return {
        "input_sha256": template["input_sha256"],
        "graph_trace": template["graph_trace"],
        "comparison": {
            "mode": "simulation",
            "basis": basis,
            "evidence": evidence,
            "scenarios": scenarios,
        },
    }


def summary(result):
    searches = result["research"]["legal_searches"]
    numeric = result["numeric_review"]
    return {
        "run_id": result["run_id"],
        "route": result["route"],
        "binding": result["binding"],
        "candidate_count": len(result["candidate_reviews"]),
        "numeric_review": {
            **{key: value for key, value in numeric.items() if key != "results"},
            "results": [
                {
                    key: row[key]
                    for key in (
                        "candidate_id",
                        "status",
                        "cost_total_krw",
                        "carbon_total_kg_co2e",
                        "kg_binding",
                        "included_in_numeric_comparison",
                    )
                }
                for row in numeric["results"]
            ],
        },
        "router": result["router"],
        "issue_codes": sorted({row["code"] for row in result["critic"]}),
        "map_marker_count": len(result["map_markers"]),
        "on_map_count": sum(row["on_map"] for row in result["map_markers"]),
        "legal_searches": [
            {
                "status": row["status"],
                "result_count": len(row["results"]),
                "evidence_ids": [hit["evidence_id"] for hit in row["results"]],
            }
            for row in searches
            if row["attempted"]
        ],
        "final_recommendations": result["final_recommendations"],
        "dispatch_approved": result["dispatch_approved"],
    }


def evaluate():
    checks, results = [], {}
    config = get_settings()
    # This read-only probe must not silently select a hosted embedding provider.
    if config.embedding_provider != "sentence-transformers":
        raise ValueError("This probe requires the local sentence-transformers embedding provider")
    client = TestClient(app)

    def check(name, passed):
        checks.append({"name": name, "passed": bool(passed)})

    def post(path, payload):
        response = client.post(path, json=payload)
        if response.status_code != 200:
            # Do not log provider errors, credentials, or arbitrary uploaded content.
            raise RuntimeError(f"{path}: HTTP {response.status_code}")
        return response.json()

    request = {
        "lot": {
            "material": "PP",
            "input_form": "scrap",
            "quantity_kg": 3000,
            "region": "KR",
            "page_size": 1,
        },
        "max_legal_queries": 1,
    }
    template = post("/analysis/review-template", {"lot": request["lot"]})
    check("actual KG yields multiple paths", len(template["candidates"]) >= 2)
    check(
        "worksheet enumerates the curated scope",
        template["coverage"]["complete_within_curated_scope"],
    )
    check(
        "worksheet does not call a generation model", template["generation_model_called"] is False
    )
    check(
        "worksheet leaves all activities/factors unknown",
        all(
            row["activity"] is None and row["factor"] is None
            for candidate in template["candidates"]
            for row in candidate["required_lines"]
        ),
    )

    missing = post("/analysis/review", {"research": request})
    check(
        "missing numbers preserve every path",
        len(missing["candidate_reviews"]) == len(template["candidates"]),
    )
    check(
        "missing data stays REVIEW without invented totals",
        missing["route"] == "REVIEW" and not missing["numeric_review"]["results"],
    )
    check(
        "actual local legal retrieval returns evidence",
        any(
            row["status"] == "retrieved" and row["results"]
            for row in missing["research"]["legal_searches"]
        ),
    )
    check(
        "unverified facility conditions have map targets",
        any(row["on_map"] for row in missing["map_markers"]),
    )
    results["missing_numeric"] = summary(missing)

    bound = synthetic_numeric(template)
    reviewed = post("/analysis/review", {"research": request, "numeric": bound})
    ids = {row["candidate_id"] for row in template["candidates"]}
    check(
        "synthetic numeric comparison binds to actual path IDs",
        set(reviewed["numeric_review"]["included_candidate_ids"]) == ids,
    )
    check(
        "synthetic comparison never grants PASS",
        reviewed["route"] == "REVIEW"
        and not reviewed["final_recommendations"]
        and not reviewed["dispatch_approved"],
    )
    check(
        "synthetic comparison remains labelled",
        "simulation_not_operational_evidence" in {row["code"] for row in reviewed["critic"]},
    )
    check(
        "comparison exposes numeric Pareto only",
        bool(reviewed["numeric_review"]["pareto_fronts"])
        and not reviewed["numeric_review"]["constraint_feasible_verified"],
    )
    results["synthetic_numbers_on_real_paths"] = summary(reviewed)

    for label, mutate in (
        ("stale_input", lambda value: value.update(input_sha256="0" * 64)),
        ("stale_graph", lambda value: value.update(graph_trace={"generation": "synthetic-stale"})),
    ):
        changed = deepcopy(bound)
        mutate(changed)
        response = post("/analysis/review", {"research": request, "numeric": changed})
        check(
            f"{label}: old numbers rejected",
            response["route"] == "REVISE"
            and not response["numeric_review"]["included_candidate_ids"]
            and not response["numeric_review"]["pareto_fronts"],
        )
        check(
            f"{label}: invalidation starts at KG",
            response["router"]["restart_from"] == "kg"
            and response["router"]["automatic_reexecution"] is False,
        )
        results[label] = summary(response)

    forged = deepcopy(bound)
    candidate = forged["comparison"]["scenarios"][0]
    old = candidate["stage_ids"][0]
    candidate["stage_ids"][0] = "synthetic-forged-stage"
    for line in candidate["lines"]:
        if line["target_id"] == old:
            line["target_id"], line["factor"] = "synthetic-forged-stage", "0"
    response = post("/analysis/review", {"research": request, "numeric": forged})
    check(
        "forged cheap stage excluded from numerical comparison",
        response["route"] == "REVISE"
        and candidate["candidate_id"] not in response["numeric_review"]["included_candidate_ids"],
    )
    check(
        "other correctly bound paths remain visible",
        set(response["numeric_review"]["included_candidate_ids"])
        == ids - {candidate["candidate_id"]}
        and len(response["candidate_reviews"]) == len(ids),
    )
    results["forged_stage"] = summary(response)

    regression, retrieval_status = [], "completed"
    try:
        for case in load_retrieval_cases(Path("data/config/retrieval_eval.json")):
            regression.append(asdict(evaluate_case(case, get_repository().search(case.query, 5))))
    except Exception:  # noqa: BLE001 - record a failure, never expose DB/provider details
        retrieval_status = "unavailable"
    check(
        "existing curated retrieval cases still pass",
        retrieval_status == "completed"
        and bool(regression)
        and all(row["passed"] for row in regression),
    )
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "scope": "real KG/API wiring and local RAG availability check; synthetic lots and numbers",
        "input_lot_is_synthetic": True,
        "all_numeric_inputs_are_synthetic": True,
        "generation_model_calls": 0,
        "data_provenance": {
            "kg": "existing curated public process/facility evidence; not current intake approval",
            "legal_retrieval": "configured local official corpus; success/unavailability recorded in checks",
            "embedding_model": config.embedding_model,
            "numeric": "artificial values and example.invalid sources; not quotes or LCI evidence",
        },
        "template": template,
        "cases": results,
        "retrieval_regression": regression,
        "retrieval_regression_status": retrieval_status,
        "checks": checks,
        "passed": all(row["passed"] for row in checks),
        "not_proven": [
            "source-content correctness and process-specific LCI applicability",
            "permit legality, facility intake, current capacity, transport distance or prices",
            "expert recommendation quality, model-based Critic or final Top K",
            "automatic stage reexecution, persistent runs or live 3D integration",
        ],
    }, reviewed["report_markdown"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    report, markdown = evaluate()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf8")
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            "> **가상 입력·가상 수치로 실행한 연결 검증 예시입니다. 실제 사업장·견적·LCI 평가가 아닙니다.**\n\n"
            + markdown,
            encoding="utf8",
        )
    print(
        json.dumps(
            {
                "passed": report["passed"],
                "checks": len(report["checks"]),
                "output": str(args.output),
            }
        )
    )
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
