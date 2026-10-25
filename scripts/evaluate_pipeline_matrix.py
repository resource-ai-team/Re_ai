"""Real local KG/RAG/LCI scenario exploration; all lots and prices are synthetic.

Never enables external map/model calls, writes DB data or stores registry names.
This finite matrix checks declared behavior, not every possible industrial case.
"""

import argparse
import json
import os
from copy import deepcopy
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

from fastapi.testclient import TestClient

from resource_ai.core.config import get_settings
from resource_ai.core.dependencies import get_repository
from resource_ai.evidence.models import SearchGroup
from resource_ai.main import app
from resource_ai.rag.evaluation import evaluate_case, load_retrieval_cases
from scripts.evaluate_decision_review import synthetic_numeric

MATERIALS = [
    ("PP", "scrap"),
    ("PE", "film"),
    ("PET", "bottle"),
    ("알루미늄", "can"),
    ("종이", "cardboard"),
]


def evaluate():
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    settings = get_settings()
    settings.road_routing_enabled = False
    settings.legal_graph_model_enabled = False
    settings.legal_graph_allow_external = False
    client = TestClient(app, raise_server_exceptions=False)
    checks, cases, quality_gaps = [], [], []

    def check(category, name, condition):
        checks.append({"category": category, "name": name, "passed": bool(condition)})

    def post(category, name, path, body, expected=200, *, raw=False):
        start = perf_counter()
        response = (
            client.post(path, content=body, headers={"content-type": "application/json"})
            if raw
            else client.post(path, json=body)
        )
        check(category, name + ": HTTP", response.status_code == expected)
        row = {
            "category": category,
            "name": name,
            "path": path,
            "http_status": response.status_code,
            "duration_seconds": round(perf_counter() - start, 3),
        }
        cases.append(row)
        if response.status_code != 200:
            if response.status_code == 422:
                detail = response.json().get("detail", [])
                check(
                    category,
                    name + ": no raw validation input",
                    all("input" not in x and "ctx" not in x for x in detail),
                )
            return None
        data = response.json()
        check(category, name + ": no dispatch approval", data.get("dispatch_approved") is False)
        if "route" in data:
            check(category, name + ": no PASS", data["route"] in {"REVIEW", "REVISE"})
        if "final_recommendations" in data:
            check(
                category, name + ": no invented final ranking", data["final_recommendations"] == []
            )
        return data

    # Explore every curated material against correct, missing, mismatched and unknown forms.
    for material, form in MATERIALS:
        for label, selected in [
            ("confirmed", form),
            ("missing", None),
            ("other", "pellet"),
            ("unknown", "not-a-known-form"),
        ]:
            data = post(
                "KG",
                f"{material}/{label}",
                "/kg/process-plans",
                {
                    "material": material,
                    "input_form": selected,
                    "quantity_kg": 1000,
                    "page_size": 100,
                },
            )
            if data:
                check(
                    "KG",
                    f"{material}/{label}: page cardinality",
                    len(data["items"]) == data["pagination"]["returned_count"],
                )
                check(
                    "KG",
                    f"{material}/{label}: no assumed old treatment",
                    data["input_context"].get("current_treatment") is None,
                )
                cases[-1].update(
                    {"planning_status": data["status"], "candidate_count": len(data["items"])}
                )
                if label == "confirmed":
                    check("KG", material + ": sourced candidate exists", len(data["items"]) >= 1)
                    if len(data["items"]) < 2:
                        quality_gaps.append(
                            {
                                "area": "KG",
                                "material": material,
                                "gap": "Only one curated candidate; alternative comparison not demonstrated",
                                "next_action": "Add independently sourced process/facility capabilities, not fabricated paths",
                            }
                        )
    for label, lot in [
        ("no material", {}),
        ("unsupported material", {"material": "unknown mixed industrial sludge"}),
    ]:
        data = post("KG", label, "/analysis/research", {"lot": lot, "max_legal_queries": 1})
        if data:
            check("KG", label + ": no fabricated paths", not data["candidates"])

    for value, label in [
        (True, "boolean"),
        (False, "false"),
        (0, "zero"),
        (-1, "negative"),
        (1e100, "excessive"),
    ]:
        for path in ["/kg/process-plans", "/analysis/review-template", "/analysis/kg-lci"]:
            lot = {"material": "PP", "quantity_kg": value}
            post(
                "INPUT",
                f"{label} {path}",
                path,
                lot if path.startswith("/kg/") else {"lot": lot},
                422,
            )
    for value in ["NaN", "Infinity", "-Infinity"]:
        post(
            "INPUT",
            value,
            "/kg/process-plans",
            '{"material":"PP","quantity_kg":' + value + "}",
            422,
            raw=True,
        )

    for material, form in MATERIALS:
        data = post(
            "RESEARCH",
            material,
            "/analysis/review",
            {
                "research": {
                    "lot": {
                        "material": material,
                        "input_form": form,
                        "quantity_kg": 1000,
                        "page_size": 1,
                    },
                    "max_legal_queries": 3,
                    "max_technical_queries": 3,
                }
            },
        )
        if not data:
            continue
        research = data["research"]
        check(
            "RESEARCH",
            material + ": all bounded paths",
            research["coverage"]["complete_within_curated_scope"],
        )
        check(
            "RESEARCH",
            material + ": legal evidence present",
            any(s["results"] for s in research["legal_searches"]),
        )
        check(
            "RESEARCH",
            material + ": technical evidence present",
            any(s["results"] for s in research["technical_searches"]),
        )
        check(
            "RESEARCH",
            material + ": unknown totals stay unknown",
            data["numeric_review"]["results"] == [],
        )
        check("RESEARCH", material + ": risks have map targets", bool(data["map_markers"]))
        check(
            "RESEARCH",
            material + ": technical results correctly grouped",
            all(
                h["document_type"] == "TECHNICAL_LITERATURE"
                for s in research["technical_searches"]
                for h in s["results"]
            ),
        )
        cases[-1].update(
            {
                "candidate_count": len(research["candidates"]),
                "map_markers": len(data["map_markers"]),
                "legal_hits": sum(len(s["results"]) for s in research["legal_searches"]),
                "technical_hits": sum(len(s["results"]) for s in research["technical_searches"]),
            }
        )

    lot = {
        "material": "PP",
        "input_form": "scrap",
        "quantity_kg": 3000,
        "region": "KR",
        "page_size": 1,
    }
    template = post(
        "NUMERIC",
        "worksheet",
        "/analysis/review-template",
        {"lot": lot, "site_context_sha256": "a" * 64},
    )
    if template:
        bound = synthetic_numeric(template)
        bound["site_context_sha256"] = "a" * 64
        research = {"lot": lot, "max_legal_queries": 1}
        initial = {"research": research, "numeric": bound, "site_context_sha256": "a" * 64}
        data = post("NUMERIC", "valid synthetic arithmetic", "/analysis/review", initial)
        if data:
            check(
                "NUMERIC",
                "all real path IDs bind",
                len(data["numeric_review"]["included_candidate_ids"])
                == len(template["candidates"]),
            )
            check(
                "NUMERIC",
                "simulation label retained",
                data["numeric_review"]["mode"] == "simulation",
            )
        mutations = [
            ("different lot", lambda p: p["numeric"].update(input_sha256="0" * 64)),
            ("different graph", lambda p: p["numeric"].update(graph_trace={"generation": "stale"})),
            ("different site", lambda p: p.update(site_context_sha256="b" * 64)),
            ("site stripped", lambda p: p.pop("site_context_sha256")),
            (
                "synthetic called real",
                lambda p: p["numeric"]["comparison"].update(mode="submitted_evidence"),
            ),
            (
                "expired evidence",
                lambda p: p["numeric"]["comparison"]["evidence"][0].update(
                    valid_until="2026-01-02"
                ),
            ),
            (
                "wrong carbon method",
                lambda p: p["numeric"]["comparison"]["basis"].update(carbon_method="other-method"),
            ),
            ("missing evidence", lambda p: p["numeric"]["comparison"].update(evidence=[])),
        ]
        for name, change in mutations:
            payload = deepcopy(initial)
            change(payload)
            data = post("NUMERIC", name, "/analysis/review", payload)
            if data:
                check(
                    "NUMERIC",
                    name + ": invalid values excluded",
                    not data["numeric_review"]["included_candidate_ids"],
                )
        for name, change in [
            ("wrong unit", lambda s: s["lines"][0].update(factor_unit="KRW/unknown")),
            (
                "missing transport",
                lambda s: s.update(lines=[r for r in s["lines"] if r["category"] != "transport"]),
            ),
            ("forged stage", lambda s: s["stage_ids"].__setitem__(0, "synthetic-forged-stage")),
        ]:
            payload = deepcopy(initial)
            scenario = payload["numeric"]["comparison"]["scenarios"][0]
            target = scenario["candidate_id"]
            change(scenario)
            data = post("NUMERIC", name, "/analysis/review", payload)
            if data:
                check(
                    "NUMERIC",
                    name + ": bad candidate excluded",
                    target not in data["numeric_review"]["included_candidate_ids"],
                )

    origin = {
        "organisation_id": "synthetic-semiconductor-producer",
        "site_id": "synthetic-site-a",
        "label": "가상 포장재 배출 사업장",
        "pickup_address": "시나리오용 가상 수거 위치",
        "latitude": 37.28,
        "longitude": 127.44,
    }
    contexts = []
    for site, lat in [("a", 37.28), ("b", 36.64)]:
        current = {**origin, "site_id": "synthetic-site-" + site, "latitude": lat}
        data = post(
            "SITE",
            "same company/site " + site,
            "/analysis/site-discovery",
            {
                "origin": current,
                "lot": lot,
                "facility_page_size": 2,
                "max_facility_pages": 1,
                "road_routing": {"max_destinations": 2},
            },
        )
        if data:
            contexts.append(data["context_sha256"])
            check(
                "SITE",
                site + ": map calls disabled",
                data["road_routing"]["coverage"]["external_calls"] == 0,
            )
            check(
                "SITE",
                site + ": no road fallback",
                all(
                    row["research_packet"]["road_distance_km"] is None
                    for s in data["facility_searches"]
                    for row in s["registry_candidates"]
                ),
            )
            check(
                "SITE",
                site + ": no automatic registry insertion",
                all(not s["automatic_path_insertion"] for s in data["facility_searches"]),
            )
            cases[-1].update(
                {
                    "context_sha256": data["context_sha256"],
                    "process_count": len(data["facility_searches"]),
                    "registry_imported": any(
                        s["coverage"]["registry_imported"] for s in data["facility_searches"]
                    ),
                    "registry_records_returned": sum(
                        len(s["registry_candidates"]) for s in data["facility_searches"]
                    ),
                }
            )
            check("SITE", site + ": real registry active", cases[-1]["registry_imported"])
    check(
        "SITE", "different sites invalidate context", len(contexts) == 2 and len(set(contexts)) == 2
    )

    for material, form in MATERIALS[:3:2]:
        data = post(
            "COMPOSED",
            material,
            "/analysis/site-review",
            {
                "origin": origin,
                "lot": {
                    "material": material,
                    "input_form": form,
                    "quantity_kg": 1000,
                    "page_size": 1,
                },
                "facility_page_size": 2,
                "max_facility_pages": 1,
                "max_legal_queries": 3,
                "max_technical_queries": 3,
                "road_routing": {"max_destinations": 2},
            },
        )
        if data:
            check(
                "COMPOSED",
                material + ": same graph snapshot",
                data["site"]["graph_trace"]
                == data["review"]["research"]["graph_trace"]
                == data["lci"]["graph_trace"],
            )
            check(
                "COMPOSED",
                material + ": origin bound",
                data["review"]["binding"]["site_context_sha256"] == data["site"]["context_sha256"],
            )
            check(
                "COMPOSED",
                material + ": screening not promoted to numeric totals",
                not data["review"]["numeric_review"]["results"],
            )
            check("COMPOSED", material + ": map risks present", bool(data["review"]["map_markers"]))
            check(
                "COMPOSED",
                material + ": no external road calls",
                data["site"]["road_routing"]["coverage"]["external_calls"] == 0,
            )
            cases[-1].update(
                {
                    "candidate_count": len(data["review"]["candidate_reviews"]),
                    "map_markers": len(data["review"]["map_markers"]),
                }
            )

    for material, form in MATERIALS:
        data = post(
            "LCI",
            material,
            "/analysis/kg-lci",
            {
                "lot": {
                    "material": material,
                    "input_form": form,
                    "quantity_kg": 1000,
                    "page_size": 1,
                }
            },
        )
        if data:
            estimates = [r for r in data["results"] if r["carbon_kgco2e"] is not None]
            check(
                "LCI",
                material + ": every path retained",
                len(data["results"]) == data["coverage"]["enumerated_in_graph"],
            )
            check(
                "LCI",
                material + ": no mismatched resin factor",
                len(estimates) == (1 if material == "PET" else 0),
            )
            check(
                "LCI",
                material + ": no premature comparability",
                all(not r["comparable"] for r in data["results"]),
            )
            cases[-1].update(
                {
                    "estimated_paths": len(estimates),
                    "installed_datasets": len(data["datasets_loaded"]),
                }
            )
    data = post(
        "LCI",
        "missing mass",
        "/analysis/kg-lci",
        {"lot": {"material": "PET", "input_form": "bottle"}},
    )
    if data:
        check(
            "LCI",
            "missing mass never becomes zero",
            all(r["carbon_kgco2e"] is None for r in data["results"]),
        )

    regression = []
    repository = get_repository()
    for case in load_retrieval_cases(Path("data/config/retrieval_eval.json")):
        hits = repository.search(case.query, 5, search_group=SearchGroup.LEGAL)
        row = asdict(evaluate_case(case, hits))
        row["locator_matched"] = any(
            expected in (hit.citation or "") for expected in case.expected_any for hit in hits
        )
        regression.append(row)
        check("RAG", case.query, row["passed"])
        check("RAG_LOCATOR", case.query, row["locator_matched"])
        if not row["locator_matched"]:
            quality_gaps.append(
                {
                    "area": "LEGAL_RETRIEVAL",
                    "query": case.query,
                    "gap": "Expected primary source locator absent from top five",
                    "next_action": "Evaluate article-level retrieval and reranking on independent labelled queries before legal automation",
                }
            )
    data = post(
        "MODEL",
        "external transfer gate",
        "/analysis/research",
        {"lot": lot, "legal_graph": {"query": "합성 로트의 위탁 요건 확인", "max_model_calls": 1}},
    )
    if data:
        check(
            "MODEL",
            "disabled model makes no calls",
            data["coverage"]["legal_graph_model_calls"] == 0,
        )

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "scope": "finite scenario categories, not exhaustive industrial/legality accuracy",
        "input_lots_and_numeric_evidence": "entirely synthetic",
        "real_dependencies": ["Neo4j", "pgvector", "local multilingual E5", "licensed local ILCD"],
        "live_external_model_and_map_calls": 0,
        "cases": cases,
        "checks": checks,
        "retrieval_regression": regression,
        "quality_gaps": quality_gaps,
        "passed": sum(c["passed"] for c in checks),
        "total": len(checks),
        "all_passed": all(c["passed"] for c in checks),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = evaluate()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "cases": len(report["cases"]),
                "passed": report["passed"],
                "total": report["total"],
                "all_passed": report["all_passed"],
            }
        )
    )
    return 0 if report["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
