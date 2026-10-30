"""Replay real code Critic -> bounded repair -> recheck with explicit injected faults.

Developer rehearsal only. Never changes a DB, enables GPT, grants PASS or guesses
missing business facts. The production API still has no automatic revision loop.
"""

import argparse
import json
import os
from copy import deepcopy
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from resource_ai.agents.decision_review import DecisionReviewService
from resource_ai.api.schemas.decision_review import DecisionReviewRequest
from resource_ai.knowledge_graph.ontology import digest


class SnapshotResearch:
    def __init__(self, research):
        self.research = research

    def run(self, _):
        return self.research.model_copy(deep=True)


def normalize_declared_cost_units(binding):
    """Convert a declared KRW/t factor to KRW/kg; never relabel it unchanged."""
    updated = deepcopy(binding)
    patches = []
    sources = {e["evidence_id"]: e for e in binding["comparison"]["evidence"]}
    for scenario in updated["comparison"]["scenarios"]:
        for line in scenario["lines"]:
            wanted = f"{'KRW' if line['metric'] == 'cost' else 'kgCO2e'}/{line['activity_unit']}"
            if line["factor_unit"] == wanted:
                continue
            source = sources.get(line["factor_evidence_id"], {})
            if not (
                line["metric"] == "cost"
                and line["activity_unit"] == "kg"
                and line["factor_unit"] == "KRW/t"
                and source.get("kind") == "cost_factor"
            ):
                raise ValueError(
                    "Unsupported unit or missing declared cost evidence; stop for review"
                )
            before = {
                key: line[key] for key in ("factor", "factor_unit", "activity", "activity_unit")
            }
            line["factor"] = str(Decimal(line["factor"]) / Decimal(1000))
            line["factor_unit"] = "KRW/kg"
            patches.append(
                {
                    "candidate_id": scenario["candidate_id"],
                    "line_id": line["line_id"],
                    "evidence_id": line["factor_evidence_id"],
                    "before": before,
                    "after": {key: line[key] for key in before},
                    "rule": "1 t = 1000 kg",
                    "source_content_verified": False,
                }
            )
    if not patches:
        raise ValueError("No supported unit mismatch; do not invent a correction")
    return updated, patches


def _synthetic_inputs(template):
    basis = {
        "quantity_kg": template["basis_template"]["quantity_kg"],
        "region": "KR",
        "as_of": "2026-10-27",
        "assessment_year": 2026,
        "carbon_method": "SYNTHETIC-REPLAY-NOT-LCI",
    }
    evidence = [
        {
            "evidence_id": kind,
            "kind": kind,
            "source_url": "https://example.invalid/synthetic-rehearsal",
            "locator": "Explicitly synthetic rehearsal inputs, not real quotes or LCI factors",
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
    cost = {"process": "100", "transport": "20", "residue": "50", "revenue": "150"}
    carbon = {"process": "0.4", "transport": "0.02", "residue": "0.05"}
    for candidate in template["candidates"]:
        lines = []
        for line in candidate["required_lines"]:
            lines.append(
                {
                    **line,
                    "activity": "300" if line["category"] == "residue" else "3000",
                    "activity_unit": "kg",
                    "factor": (cost if line["metric"] == "cost" else carbon)[line["category"]],
                    "factor_unit": "KRW/kg" if line["metric"] == "cost" else "kgCO2e/kg",
                    "activity_evidence_id": "activity_record",
                    "factor_evidence_id": line["metric"] + "_factor",
                }
            )
        scenarios.append(
            {
                "candidate_id": candidate["candidate_id"],
                "stage_ids": candidate["stage_ids"],
                "handoff_ids": candidate["handoff_ids"],
                "basis": basis,
                "lines": lines,
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


def _round(index, research, request, binding, focus_id, line_id):
    response = DecisionReviewService(SnapshotResearch(research)).run(
        DecisionReviewRequest(research=request, numeric=binding)
    )
    focus = next(c for c in research.candidates if c.plan.plan_id == focus_id)
    packet = focus.plan.research_packet
    line = next(
        line
        for s in binding["comparison"]["scenarios"]
        for line in s["lines"]
        if line["line_id"] == line_id
    )
    return {
        "round": index,
        "run_id": response.run_id,
        "route": response.route,
        "router": response.router,
        "errors": [i.model_dump(mode="json") for i in response.critic if i.severity == "error"],
        "missing_count": sum(i.severity == "missing" for i in response.critic),
        "remaining_checks": list(
            dict.fromkeys(i.message for i in response.critic if i.severity == "missing")
        ),
        "focus": {
            "candidate_id": focus_id,
            "stages": [
                {"id": s["stage_id"], "label": s["transition"]["label"]} for s in packet["stages"]
            ],
            "handoffs": [
                {"id": leg["leg_id"], "from": leg["from_stage_id"], "to": leg["to_stage_id"]}
                for leg in packet["handoffs"]
            ],
            "transport_line": deepcopy(line),
        },
        "numeric_results": [
            {
                k: row[k]
                for k in (
                    "candidate_id",
                    "status",
                    "cost_total_krw",
                    "carbon_total_kg_co2e",
                    "included_in_numeric_comparison",
                )
            }
            for row in response.numeric_review["results"]
        ],
        "final_recommendations": response.final_recommendations,
        "dispatch_approved": response.dispatch_approved,
        "model_calls": response.semantic_critic.model_calls,
    }


def build_replay(service, request, template, *, max_repairs=2):
    if type(max_repairs) is not int or not 0 <= max_repairs <= 2:
        raise ValueError("Rehearsal allows at most two repairs")
    if (
        request.lot.material.strip().upper() != "PP"
        or request.lot.input_form != "scrap"
        or request.lot.quantity_kg != 3000
        or Decimal(str(template["basis_template"]["quantity_kg"])) != Decimal(3000)
    ):
        raise ValueError("This labelled rehearsal requires PP scrap, 3000 kg")
    real = service.run(request)
    if real.graph_trace != template["graph_trace"]:
        raise ValueError("Graph changed after the template; recreate the rehearsal")
    focus = next((c for c in real.candidates if c.plan.research_packet["handoffs"]), None)
    if focus is None:
        raise ValueError("A real multi-stage KG path is required")
    focus_id = focus.plan.plan_id
    numeric = _synthetic_inputs(template)
    scenario = next(s for s in numeric["comparison"]["scenarios"] if s["candidate_id"] == focus_id)
    faulty = next(
        l for l in scenario["lines"] if l["metric"] == "cost" and l["category"] == "transport"
    )
    faulty["factor"], faulty["factor_unit"] = "20000", "KRW/t"
    line_id = faulty["line_id"]
    current = real.model_copy(deep=True)
    damaged = next(c for c in current.candidates if c.plan.plan_id == focus_id)
    removed = damaged.plan.research_packet["handoffs"].pop(0)
    rounds, actions, stop = [], [], "repair_limit"
    for index in range(max_repairs + 1):
        row = _round(index, current, request, numeric, focus_id, line_id)
        if rounds:
            previous = {e["code"] for e in rounds[-1]["errors"]}
            row["resolved_error_codes"] = sorted(previous - {e["code"] for e in row["errors"]})
        rounds.append(row)
        if not row["errors"]:
            stop = "human_verification_required"
            break
        if index == max_repairs:
            break
        codes = {e["code"] for e in row["errors"]}
        if "kg_handoff_count_mismatch" in codes:
            # A fresh query, not restoring the original snapshot by assignment.
            fresh = service.run(request)
            if fresh.graph_trace != real.graph_trace:
                stop = "graph_changed_requires_new_numeric_binding"
                break
            actions.append(
                {
                    "after_round": index,
                    "kind": "refresh_kg_and_dependent_research",
                    "source": "fresh KG and RAG query through CandidateResearchService.run",
                    "graph_trace_unchanged": True,
                    "old_numeric_binding_rewritten": False,
                    "refreshed_research_sha256": digest(fresh.model_dump(mode="json")),
                }
            )
            current = fresh
        elif codes == {"calculation_factor_unit_mismatch"}:
            try:
                updated, patches = normalize_declared_cost_units(numeric)
            except ValueError:
                stop = "unsupported_correction_requires_review"
                break
            actions.append(
                {
                    "after_round": index,
                    "kind": "normalize_units_and_recalculate",
                    "patches": patches,
                    "new_prices_or_emission_factors_invented": False,
                }
            )
            numeric = updated
        else:
            stop = "unsupported_issue_requires_review"
            break
    final = rounds[-1]
    checks = {
        "initial_kg_fault_detected": any(
            e["code"] == "kg_handoff_count_mismatch" for e in rounds[0]["errors"]
        ),
        "initial_unit_fault_detected": any(
            e["code"] == "calculation_factor_unit_mismatch" for e in rounds[0]["errors"]
        ),
        "bounded": len(actions) <= max_repairs,
        "no_paid_model_calls": all(r["model_calls"] == 0 for r in rounds),
        "no_approval": all(
            not r["dispatch_approved"] and not r["final_recommendations"] for r in rounds
        ),
        "production_auto_loop_not_claimed": all(
            not r["router"]["automatic_reexecution"] for r in rounds
        ),
    }
    if max_repairs == 2:
        checks.update(
            {
                "final_errors_resolved": not final["errors"],
                "final_is_review": final["route"] == "REVIEW" and final["missing_count"] > 0,
            }
        )
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "schema": "critic-revision-replay-v1",
        "scope": "developer-controlled fault injection and code Critic rehearsal, not a production automatic loop",
        "input": "가상 공장에서 발생한 PP 스크랩 3톤: 공정 후보·연결·계산 검토",
        "provenance": {
            "kg_and_rag": "actual configured service",
            "lot_and_numeric": "synthetic",
            "faults": "explicitly injected into copies",
            "critic": "real deterministic code",
            "gpt_calls": 0,
            "production_automatic_reexecution": False,
        },
        "candidate_count": len(real.candidates),
        "graph_trace": real.graph_trace,
        "injected_faults": {
            "removed_handoff": removed["leg_id"],
            "unit": "kg activity with a declared 20000 KRW/t factor",
        },
        "legal_sources": [
            {k: h.get(k) for k in ("evidence_id", "title", "citation", "version", "source_url")}
            for s in real.legal_searches
            for h in s.results
        ],
        "rounds": rounds,
        "actions": actions,
        "stop_reason": stop,
        "checks": checks,
        "all_passed": all(checks.values()),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--visual", type=Path)
    args = parser.parse_args()
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    import torch

    from resource_ai.agents.research import CandidateResearchService
    from resource_ai.api.schemas.analysis import ResearchRequest
    from resource_ai.api.schemas.decision_review import ReviewTemplateRequest
    from resource_ai.core.config import get_settings
    from resource_ai.core.dependencies import get_graph_query, get_repository

    torch.set_num_threads(4)
    settings = get_settings()
    settings.road_routing_enabled = False
    settings.legal_graph_model_enabled = settings.legal_graph_allow_external = False
    settings.semantic_critic_enabled = settings.semantic_critic_allow_external = False
    if settings.embedding_provider != "sentence-transformers":
        raise ValueError("The actual replay requires local E5, not a test embedding provider")
    service = CandidateResearchService(get_graph_query(), get_repository())
    request = ResearchRequest(
        lot={"material": "PP", "input_form": "scrap", "quantity_kg": 3000, "page_size": 3},
        max_legal_queries=1,
        max_technical_queries=1,
    )
    template = DecisionReviewService(service).template(ReviewTemplateRequest(lot=request.lot))
    trace = build_replay(service, request, template)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(trace, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.visual:
        source = Path(__file__).resolve().parents[1] / "examples" / "critic-revision.html"
        fragment = source.read_text(encoding="utf-8").replace(
            "__CRITIC_TRACE__", json.dumps(trace, ensure_ascii=False).replace("</", "<\\/")
        )
        args.visual.parent.mkdir(parents=True, exist_ok=True)
        args.visual.write_text(fragment, encoding="utf-8")
    print(
        json.dumps(
            {
                "rounds": len(trace["rounds"]),
                "actions": len(trace["actions"]),
                "routes": [r["route"] for r in trace["rounds"]],
                "error_counts": [len(r["errors"]) for r in trace["rounds"]],
                "stop_reason": trace["stop_reason"],
                "all_passed": trace["all_passed"],
            }
        )
    )
    return 0 if trace["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
