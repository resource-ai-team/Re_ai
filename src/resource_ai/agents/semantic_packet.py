"""Minimized, permission-bounded critic input. Never serialize whole engine objects."""

import json
from datetime import date
from urllib.parse import urlsplit

from resource_ai.knowledge_graph.ontology import digest


class PacketError(ValueError):
    pass


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def build_packet(research, numeric_review, numeric, issues, *, max_findings, extra=None):
    records, by_key, targets = {}, {}, {}
    excluded, legal_incomplete = set(), 0

    def add(key, text, *, candidate=None, target=None, kind="engine_record", metadata=None):
        if key in by_key:
            previous = records[by_key[key]]
            if (
                previous["text"] != text
                or previous["metadata"] != (metadata or {})
                or previous["candidate_id"] != candidate
                or previous["target_id"] != target
                or previous["kind"] != kind
            ):
                raise PacketError("evidence_conflict")
            return by_key[key]
        if len(records) >= 600:
            raise PacketError("context_budget_exceeded")
        ref = f"E{len(records) + 1:04d}"
        records[ref] = {
            "ref_id": ref,
            "text": text,
            "candidate_id": candidate,
            "target_id": target,
            "kind": kind,
            "evidence_id": key,
            "metadata": metadata or {},
        }
        by_key[key] = ref
        return ref

    for candidate in research.candidates:
        plan, packet = candidate.plan, candidate.plan.research_packet
        stages = {s["stage_id"] for s in packet["stages"]}
        legs = {s["leg_id"] for s in packet["handoffs"]} | {plan.plan_id + ":origin-transport"}
        targets[plan.plan_id] = {"candidate": {plan.plan_id}, "stage": stages, "handoff": legs}
        assessments = {
            r["stage_id"]: r["assessment"] for r in candidate.facility_assessment.get("stages", [])
        }
        value = {
            "candidate_id": plan.plan_id,
            "status": candidate.status,
            "route_is_hypothesis": True,
            "dispatch_approved": False,
            "stages": [
                {
                    "stage_id": s["stage_id"],
                    "process": s["transition"]["label"],
                    "operational_conditions_verified": False,
                    "public_condition_status": assessments.get(s["stage_id"], {}).get(
                        "status", "unknown"
                    ),
                }
                for s in packet["stages"]
            ],
            "handoffs": [{"leg_id": s["leg_id"]} for s in packet["handoffs"]],
        }
        add(
            "semantic-plan:" + plan.plan_id,
            canonical(value),
            candidate=plan.plan_id,
            target=plan.plan_id,
        )

    # Explicitly permitted structured facts; excludes names, addresses, coordinates,
    # free-text notes, raw registry records and source excerpts from the KG.
    lot = {
        k: research.input_context.get(k)
        for k in (
            "material",
            "input_form",
            "quantity_kg",
            "moisture_pct",
            "contamination_pct",
            "designated_waste",
        )
    }
    add("semantic-input", canonical({"submitted_lot": lot, "independently_verified": False}))
    add(
        "semantic-code-checks",
        canonical(
            {
                "issues": [
                    {
                        k: i.get(k)
                        for k in (
                            "code",
                            "stage",
                            "severity",
                            "candidate_id",
                            "target_type",
                            "target_id",
                        )
                    }
                    for i in issues
                ],
                "numeric_comparison_scope": numeric_review["scope"],
                "included_candidate_ids": numeric_review["included_candidate_ids"],
                "pareto_fronts": numeric_review["pareto_fronts"],
                "unsubmitted_candidate_ids": numeric_review["unsubmitted_candidate_ids"],
            }
        ),
    )

    if numeric is not None:
        for scenario in numeric.scenarios:
            if scenario.candidate_id not in targets:
                continue
            if scenario.candidate_id not in numeric_review["included_candidate_ids"]:
                add(
                    "semantic-numeric:" + scenario.candidate_id,
                    canonical(
                        {
                            "candidate_id": scenario.candidate_id,
                            "numeric_details_withheld": True,
                            "reason": "not_admitted_to_bound_numeric_comparison",
                        }
                    ),
                    candidate=scenario.candidate_id,
                    target=scenario.candidate_id,
                )
                continue
            # Source URLs, locators, client evidence IDs and private documents are omitted.
            add(
                "semantic-numeric:" + scenario.candidate_id,
                canonical(
                    {
                        "candidate_id": scenario.candidate_id,
                        "basis": scenario.basis.model_dump(mode="json"),
                        "submission_mode": numeric.mode,
                        "source_documents_audited": False,
                        "included_in_comparison": scenario.candidate_id
                        in numeric_review["included_candidate_ids"],
                        "lines": [
                            {
                                k: line.model_dump(mode="json")[k]
                                for k in (
                                    "target_id",
                                    "metric",
                                    "category",
                                    "activity",
                                    "activity_unit",
                                    "factor",
                                    "factor_unit",
                                )
                            }
                            for line in scenario.lines
                        ],
                    }
                ),
                candidate=scenario.candidate_id,
                target=scenario.candidate_id,
            )

    def law(hit):
        nonlocal legal_incomplete
        key = hit.get("evidence_id", "unknown")
        try:
            url = urlsplit(hit.get("source_url") or "")
        except (ValueError, TypeError, AttributeError):
            excluded.add(str(key))
            return
        try:
            effective = date.fromisoformat(hit.get("effective_from") or "")
        except (ValueError, TypeError):
            effective = None
        if (
            hit.get("document_type") != "LAW"
            or url.scheme != "https"
            or url.hostname not in {"law.go.kr", "www.law.go.kr"}
            or url.username
            or url.password
            or not hit.get("version")
            or not effective
            or effective > date.today()
            or not hit.get("citation")
            or not hit.get("content")
            or hit.get("source_checks")
        ):
            excluded.add(str(key))
            return
        add(
            str(key),
            hit["content"],
            kind="official_statute",
            metadata={
                k: hit.get(k)
                for k in ("title", "citation", "version", "effective_from", "source_url")
            },
        )

    for search in research.legal_searches:
        for hit in search.results:
            law(hit)
            context = hit.get("legal_context") or {}
            legal_incomplete += context.get("status") != "retrieved"
            for source in context.get("primary", []):
                law(source)
            for linked in context.get("linked", []):
                for source in linked.get("sources", []):
                    law(source)
    for ref in research.legal_baselines:
        if ref.get("complete") is True:
            for source in ref.get("sources", []):
                law(source)
        else:
            legal_incomplete += 1
    for search in research.technical_searches:
        for hit in search.results:
            excluded.add(str(hit.get("evidence_id", "technical-permission-unknown")))

    # Pass only coverage flags from site/LCI; restricted results, coefficients,
    # facility rows and road provider geometries never enter the model payload.
    additional = {}
    if extra:
        site, lci = extra.get("site"), extra.get("lci")
        if site is not None:
            additional["site"] = {
                "facility_searches": len(site.facility_searches),
                "registry_rows_transferred": False,
                "origin_identity_transferred": False,
                "road_requested": site.road_routing is not None,
                "permit_and_intake_verified": False,
            }
        if lci is not None:
            additional["lci"] = {
                "screening_result_count": len(lci.results),
                "raw_inventory_or_factors_transferred": False,
                "screening_not_added_to_numeric_totals": True,
            }
    coverage = {
        "candidate_count": len(targets),
        "all_candidate_contexts_included": True,
        "evidence_count": len(records) + 1,
        "excluded_source_count": len(excluded),
        "incomplete_legal_context_count": legal_incomplete,
        "technical_raw_transfer_allowed": False,
        "registry_raw_transfer_allowed": False,
        "licensed_lci_raw_transfer_allowed": False,
        "additional_context": additional,
        "semantically_verified": False,
    }
    add("semantic-coverage", canonical(coverage))
    # Evidence IDs supplied by clients remain internal; only generated E#### aliases
    # and engine-generated candidate/target IDs are exposed to the model.
    payload = {
        "task_version": "semantic-critic-v1",
        "max_findings": max_findings,
        "allowed_targets": targets,
        "coverage": coverage,
        "evidence": [
            {k: v for k, v in record.items() if k != "evidence_id"} for record in records.values()
        ],
    }
    # JSON cannot encode sets. Preserve a canonical order for reproducible hashes.
    payload["allowed_targets"] = {
        p: {k: sorted(v) for k, v in groups.items()} for p, groups in targets.items()
    }
    return payload, records, targets, coverage, digest(payload)
