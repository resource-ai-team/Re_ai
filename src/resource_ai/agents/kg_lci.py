"""Evaluate actual, verified KG ProcessPlan paths against locally installed ILCD.

This service never infers unregistered transitions, yields a facility quote, or
silently promotes preliminary LCIA to a legally feasible recommendation.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from resource_ai.api.schemas.kg_lci import KGPathLCIRequest, KGPathLCIResponse
from resource_ai.calculators.lci import candidate_from_plan, evaluate_candidate
from resource_ai.knowledge_graph.planning import normalize_form

LIMITATIONS = [
    "The KG contains sourced research hypotheses, not confirmed operating routes or acceptance.",
    "LCIA values describe national-average source boundaries, not individual facilities.",
    "Costs, haul distances, legality, material loss, residues and total pathway carbon are not validated.",
    "Only exact known whole-route matches are estimated. No per-stage factor allocation.",
    "No cost/carbon ranking, Pareto, dispatch approval or final Top K is issued.",
]


class KGPathLCIIntegrityError(Exception):
    """Graph generation, pagination or condition alignment changed mid-evaluation."""


class KGPathLCIService:
    def __init__(self, graph, datasets):
        self.graph = graph
        self.datasets = datasets

    def run(self, request: KGPathLCIRequest) -> KGPathLCIResponse:
        seen_ids: set[str] = set()
        seen_cursors: set[str] = set()
        results: list[dict[str, Any]] = []
        trace = None
        cursor = None
        expected_total = None
        pages_read = 0
        fully_enumerated = True
        normalized_material = None
        graph_status = None

        for _ in range(request.max_pages):
            page = self.graph.assessed_plans(request.lot.model_copy(update={"cursor": cursor}))
            planning = page.planning
            pages_read += 1
            if trace is None:
                trace = planning.graph_trace
                expected_total = planning.pagination.enumerated_plans
                normalized_material = planning.normalized_material
                graph_status = planning.status
            elif (
                trace != planning.graph_trace
                or expected_total != planning.pagination.enumerated_plans
                or normalized_material != planning.normalized_material
            ):
                raise KGPathLCIIntegrityError("Graph generation, material or total changed")

            fully_enumerated &= planning.search_scope.get("all_paths_enumerated", False)
            current_ids = [plan.plan_id for plan in planning.items]
            if (
                len(current_ids) != len(set(current_ids))
                or len(current_ids) != planning.pagination.returned_count
                or set(current_ids) & seen_ids
            ):
                raise KGPathLCIIntegrityError("Duplicate or mismatched KG plan page")
            assessments = {row["plan_id"]: row for row in page.assessments}
            if len(assessments) != len(page.assessments) or set(assessments) != set(
                current_ids
            ):
                raise KGPathLCIIntegrityError("Facility assessments are not aligned to KG plans")
            for plan in planning.items:
                seen_ids.add(plan.plan_id)
                assessment = assessments[plan.plan_id]
                packet = plan.research_packet
                stages = packet.get("stages") if isinstance(packet, dict) else None
                stage_ids = [
                    stage.get("stage_id") for stage in stages if isinstance(stage, dict)
                ] if isinstance(stages, list) else []
                if stages is not None and (
                    len(stage_ids) != len(stages) or any(not sid for sid in stage_ids)
                ):
                    raise KGPathLCIIntegrityError("Stage IDs missing from KG evidence packet")
                handoffs = packet.get("handoffs") if isinstance(packet, dict) else None
                handoff_ids = [
                    leg.get("leg_id") for leg in handoffs if isinstance(leg, dict)
                ] if isinstance(handoffs, list) else []
                if handoffs is not None and len(handoff_ids) != len(handoffs):
                    raise KGPathLCIIntegrityError("Handoff IDs missing from KG evidence packet")
                if packet is not None and (
                    len(set(stage_ids)) != len(stage_ids)
                    or len(set(handoff_ids)) != len(handoff_ids)
                ):
                    raise KGPathLCIIntegrityError("Duplicate stage/handoff identifiers")

                row: dict[str, Any]
                if request.lot.quantity_kg is None:
                    row = {
                        "candidate_id": plan.plan_id,
                        "calculation_status": "UNAVAILABLE",
                        "reason": "User lot quantity_kg is missing",
                        "carbon_kgco2e": None,
                    }
                elif packet is None:
                    row = {
                        "candidate_id": plan.plan_id,
                        "calculation_status": "REVIEW_REQUIRED",
                        "reason": "KG evidence packet is unavailable; do not infer transitions",
                        "carbon_kgco2e": None,
                    }
                elif not self.datasets:
                    row = {
                        "candidate_id": plan.plan_id,
                        "calculation_status": "UNAVAILABLE",
                        "reason": "No licensed local ILCD dataset installed",
                        "carbon_kgco2e": None,
                    }
                else:
                    # Only an explicit user-supplied bottle form may activate the
                    # PET bottle LCIA input/output relationship. KG path labels
                    # alone never prove the lot is made of bottles.
                    bottle = (
                        normalized_material == "pet"
                        and normalize_form(request.lot.input_form) == "bottle"
                    )
                    candidate = candidate_from_plan(
                        plan.model_dump(mode="json"),
                        material=normalized_material or "",
                        input_mass_kg=str(Decimal(str(request.lot.quantity_kg))),
                        waste_form="PET_BOTTLES" if bottle else None,
                    )
                    row = evaluate_candidate(candidate, self.datasets)

                # Keep original identity and graph details on *every* candidate,
                # including missing-data and unsupported routes.
                row.update(
                    {
                        "plan_id": plan.plan_id,
                        "stage_ids": stage_ids,
                        "handoff_ids": handoff_ids,
                        "facility_assessment_status": assessment.get("status"),
                        "kg_state": plan.state,
                        "kg_evidence_available": packet is not None,
                        "recommended": False,
                        "dispatch_approved": False,
                        "comparable": False,
                    }
                )
                results.append(row)

            next_cursor = planning.pagination.next_cursor
            if bool(next_cursor) != planning.pagination.has_more:
                raise KGPathLCIIntegrityError("Cursor and has_more contradict")
            if not next_cursor:
                if len(seen_ids) != expected_total:
                    raise KGPathLCIIntegrityError("KG page count does not match total")
                cursor = None
                break
            if next_cursor in seen_cursors or not planning.items:
                raise KGPathLCIIntegrityError("KG pagination does not advance")
            seen_cursors.add(next_cursor)
            cursor = next_cursor

        coverage = {
            "pages_read": pages_read,
            "enumerated_in_graph": expected_total,
            "collected_candidates": len(results),
            "all_pages_read": cursor is None,
            "all_paths_enumerated_within_curated_scope": bool(fully_enumerated),
            "complete_within_curated_scope": cursor is None and bool(fully_enumerated),
            "next_cursor": cursor,
            "normalized_material": normalized_material,
            "kg_status": graph_status,
        }
        actions = []
        if cursor is not None or not fully_enumerated:
            actions.append("KG path enumeration is incomplete; increase page/step bounds.")
        if not self.datasets:
            actions.append("Install licensed source ILCD ZIP archives on the AI server.")
        if request.lot.quantity_kg is None:
            actions.append("Confirm the incoming lot mass in kg.")
        if any(r["calculation_status"] != "ESTIMATED_REVIEW_REQUIRED" for r in results):
            actions.append("Check unmatched or unavailable KG-to-LCI routes and source boundaries.")
        actions.append("Verify actual facility intake, law, transport, costs and comparable boundaries.")
        return KGPathLCIResponse(
            input_context=request.lot.model_dump(mode="json"),
            graph_trace=trace or {},
            coverage=coverage,
            datasets_loaded=sorted({d.dataset_key for d in self.datasets}),
            results=results,
            next_actions=actions,
            limitations=LIMITATIONS,
        )
