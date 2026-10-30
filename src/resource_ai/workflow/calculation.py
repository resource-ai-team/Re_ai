"""Interval arithmetic on explicit observations; partial totals never become totals."""

from decimal import Decimal, localcontext

from resource_ai.workflow.models import CandidateDraft, MetricLine

ZERO = Decimal(0)


def bounds(value):
    return (Decimal(value["low"]), Decimal(value.get("high") or value["low"]))


def serialized(pair):
    return {"low": str(pair[0]), "high": str(pair[1])}


def issues_for_source(identifier, sources, inputs):
    source = sources.get(identifier)
    if source is None:
        return ["evidence_missing"]
    issues = []
    if not source.get("user_confirmed"):
        issues.append("evidence_unconfirmed")
    if source.get("synthetic") and inputs["mode"] != "simulation":
        issues.append("synthetic_requires_simulation")
    as_of = inputs["assessment_on"]
    if source.get("observed_on") and source["observed_on"] > as_of:
        issues.append("future_evidence")
    if source.get("valid_until") and source["valid_until"] < as_of:
        issues.append("expired_evidence")
    return issues


def expected_targets(plan, tails=()):
    packet = plan["research_packet"] or {}
    stages = [s["stage_id"] for s in packet.get("stages", [])]
    legs = [
        f"{plan['plan_id']}:origin-transport",
        *[h["leg_id"] for h in packet.get("handoffs", [])],
        *[t.leg_id if hasattr(t, "leg_id") else t["leg_id"] for t in tails],
    ]
    return {
        (metric, category, target)
        for metric in ("cost", "carbon")
        for category, targets in (
            ("process", stages),
            ("transport", legs),
            ("residue", [plan["plan_id"]]),
            ("revenue", [plan["plan_id"]] if metric == "cost" else []),
        )
        for target in targets
    }


def target_mass_binding(plan, category, target_id, tails=()):
    """Return the one physical mass basis belonging to this stage or transport leg."""
    stages = plan["research_packet"]["stages"]
    if category == "process":
        return "stage_input", target_id
    if category == "revenue":
        return "stage_output", stages[-1]["stage_id"]
    if category == "residue":
        return "total_residue", None
    if target_id == f"{plan['plan_id']}:origin-transport":
        return "lot_input", None
    for index, leg in enumerate(plan["research_packet"]["handoffs"]):
        if leg["leg_id"] == target_id:
            return "stage_input", stages[index + 1]["stage_id"]
    for raw in tails:
        tail = raw.model_dump() if hasattr(raw, "model_dump") else raw
        if tail["leg_id"] == target_id:
            return ("stage_residue" if tail["purpose"] == "residue" else "stage_output"), tail[
                "from_stage_id"
            ]
    return None, None


def physical_mass(binding, stage_id, mass_map, quantity):
    if binding == "lot_input":
        return Decimal(str(quantity)) if quantity else None
    row = mass_map.get(stage_id)
    if binding == "total_residue":
        return sum((r.residue_kg for r in mass_map.values()), ZERO)
    key = {
        "stage_input": "input_kg",
        "stage_output": "output_kg",
        "stage_residue": "residue_kg",
    }.get(binding)
    return getattr(row, key, None) if key else None


def evaluate_candidate(plan, draft, sources, inputs, roads=None):
    model = CandidateDraft.model_validate(draft)
    wanted = expected_targets(plan, model.tails)
    stages = [s["stage_id"] for s in plan["research_packet"]["stages"]]
    errors, missing, calculations = [], [], []
    covered, seen, line_ids = set(), set(), set()
    totals = {"cost": (ZERO, ZERO), "carbon": (ZERO, ZERO)}
    mass_map = {m.stage_id: m for m in model.masses}
    if len(mass_map) != len(model.masses):
        errors.append({"code": "duplicate_stage_mass", "target_id": plan["plan_id"]})
    previous = Decimal(str(inputs["engine_request"]["lot"].get("quantity_kg") or 0))
    if not previous:
        missing.append({"code": "lot_quantity_missing", "target_id": plan["plan_id"]})
    for stage in stages:
        m = mass_map.get(stage)
        if m is None:
            missing.append({"code": "mass_balance_missing", "target_id": stage})
            previous = None
            continue
        if m.input_kg != m.output_kg + m.residue_kg + m.other_output_kg or (
            previous is not None and m.input_kg != previous
        ):
            errors.append({"code": "mass_balance_mismatch", "target_id": stage})
        for eid in m.evidence_ids:
            for code in issues_for_source(eid, sources, inputs):
                missing.append({"code": code, "target_id": stage, "evidence_id": eid})
        previous = m.output_kg
    for extra in set(mass_map) - set(stages):
        errors.append({"code": "unknown_mass_stage", "target_id": extra})
    for line in model.lines:
        row_errors = []
        key = (line.metric, line.category, line.target_id)
        component = (*key, line.component)
        expected_binding, expected_stage = target_mass_binding(
            plan, line.category, line.target_id, model.tails
        )
        if key not in wanted or component in seen or line.line_id in line_ids:
            row_errors.append("unexpected_or_duplicate_component")
        seen.add(component)
        line_ids.add(line.line_id)
        for obs in (line.activity, line.factor):
            row_errors.extend(issues_for_source(obs.evidence_id, sources, inputs))
        expected_unit = f"{'KRW' if line.metric == 'cost' else 'kgCO2e'}/{line.activity.unit}"
        if line.factor.unit != expected_unit:
            row_errors.append("factor_unit_mismatch")
        if line.activity.unit not in (
            "kg",
            "t",
            "kWh",
            "MJ",
            "L",
            "m3",
            "km",
            "t_km",
            "trip",
            "batch",
        ):
            row_errors.append("unsupported_activity_unit")
        if line.activity.basis != line.factor.basis:
            row_errors.append("mass_basis_mismatch")
        scope = line.scope
        if (line.metric == "cost" and scope.purpose != "cost") or (
            line.metric == "carbon"
            and scope.purpose not in (line.category, "electricity", "reporting_only")
        ):
            row_errors.append("factor_purpose_mismatch")
        if (
            scope.region != "KR" or scope.reference_year != int(inputs["assessment_on"][:4])
        ) and not scope.transfer_explanation:
            row_errors.append("factor_transfer_explanation_required")
        if line.metric == "carbon":
            if scope.reference_year > int(inputs["assessment_on"][:4]):
                row_errors.append("future_factor_reference")
            if scope.system_boundary is None or scope.allocation is None:
                row_errors.append("carbon_boundary_and_allocation_required")
            if scope.gwp != inputs["carbon_method"]:
                row_errors.append("carbon_method_mismatch")
            if scope.purpose == "reporting_only":
                row_errors.append("reporting_factor_not_route_lca")
            if scope.purpose == "electricity" and line.covers_full_target:
                row_errors.append("electricity_is_partial_process")
            if scope.transport_included and any(
                other.metric == "carbon" and other.category == "transport" for other in model.lines
            ):
                row_errors.append("transport_overlap_requires_boundary_review")
        if line.activity.unit in ("kg", "t") and line.mass_binding == "none":
            row_errors.append("mass_binding_required")
        if (
            line.category == "revenue"
            and line.activity.unit in ("kg", "t")
            and (
                line.mass_binding != "stage_output"
                or line.activity.basis != "output_product"
                or line.mass_stage_id != stages[-1]
            )
        ):
            row_errors.append("revenue_requires_final_output_mass")
        if line.mass_binding != "none":
            actual_stage = line.mass_stage_id or (
                line.target_id if line.category == "process" else None
            )
            if line.mass_binding != expected_binding or actual_stage != expected_stage:
                row_errors.append("mass_binding_target_mismatch")
            target_mass = mass_map.get(line.mass_stage_id or line.target_id)
            bound_mass = (
                Decimal(str(inputs["engine_request"]["lot"].get("quantity_kg") or 0))
                if line.mass_binding == "lot_input"
                else getattr(
                    target_mass,
                    {
                        "stage_input": "input_kg",
                        "stage_output": "output_kg",
                        "stage_residue": "residue_kg",
                    }.get(line.mass_binding, "input_kg"),
                    None,
                )
            )
            if line.mass_binding == "total_residue":
                bound_mass = (
                    sum((m.residue_kg for m in model.masses), ZERO)
                    if set(mass_map) == set(stages)
                    else None
                )
            multiplier = Decimal(1000) if line.activity.unit == "t" else Decimal(1)
            if bound_mass is None and line.activity.unit in ("kg", "t"):
                row_errors.append("bound_mass_missing")
            elif (
                line.activity.unit not in ("kg", "t")
                or line.activity.low * multiplier != bound_mass
                or line.activity.high * multiplier != bound_mass
            ):
                row_errors.append("bound_mass_mismatch")
        if line.includes_targets and any(
            other.metric == line.metric and other.target_id in line.includes_targets
            for other in model.lines
            if other.line_id != line.line_id
        ):
            row_errors.append("included_cost_or_carbon_double_count")
        if any(
            other.line_id != line.line_id
            and other.metric == line.metric
            and other.category == line.category
            and other.target_id == line.target_id
            and (other.covers_full_target or line.covers_full_target)
            for other in model.lines
        ):
            row_errors.append("full_target_and_components_overlap")
        if any(
            not any(t[0] == line.metric and t[2] == identifier for t in wanted)
            for identifier in line.includes_targets
        ):
            row_errors.append("included_target_not_in_candidate")
        if any(
            sum(t[0] == line.metric and t[2] == identifier for t in wanted) > 1
            for identifier in line.includes_targets
        ):
            row_errors.append("included_target_ambiguous")
        if line.category == "transport" and line.activity.unit in ("km", "t_km"):
            measurement = (roads or {}).get(line.target_id)
            if not measurement or measurement.get("status") != "found":
                row_errors.append("road_distance_unverified")
            else:
                distance = Decimal(str(measurement["road_distance_km"])) * line.trip_count
                if line.activity.unit == "t_km":
                    if line.payload_kg_per_trip is None:
                        row_errors.append("transport_payload_missing")
                    else:
                        distance *= line.payload_kg_per_trip / Decimal(1000)
                        required_mass = physical_mass(
                            expected_binding,
                            expected_stage,
                            mass_map,
                            inputs["engine_request"]["lot"].get("quantity_kg"),
                        )
                        if required_mass is None:
                            row_errors.append("transport_mass_missing")
                        elif line.payload_kg_per_trip * line.trip_count != required_mass:
                            row_errors.append("transport_payload_mass_mismatch")
                if line.activity.low != distance or line.activity.high != distance:
                    row_errors.append("transport_activity_differs_from_measured_route")
        if (
            line.category == "transport"
            and line.activity.unit == "trip"
            and (line.activity.low != line.trip_count or line.activity.high != line.trip_count)
        ):
            row_errors.append("transport_trip_count_mismatch")
        if row_errors:
            unknown = {
                "evidence_missing",
                "evidence_unconfirmed",
                "factor_transfer_explanation_required",
                "road_distance_unverified",
                "transport_payload_missing",
                "carbon_boundary_and_allocation_required",
                "transport_mass_missing",
                "bound_mass_missing",
            }
            for code in dict.fromkeys(row_errors):
                (missing if code in unknown else errors).append(
                    {
                        "code": code,
                        "target_id": line.target_id,
                        "line_id": line.line_id,
                        "metric": line.metric,
                    }
                )
            continue
        with localcontext() as context:
            context.prec = 80
            amount = (line.activity.low * line.factor.low, line.activity.high * line.factor.high)
            if line.category == "revenue":
                amount = (-amount[1], -amount[0])
            low, high = totals[line.metric]
            totals[line.metric] = (low + amount[0], high + amount[1])
        if line.covers_full_target:
            covered.add(key)
            for included in line.includes_targets:
                matches = {
                    target
                    for target in wanted
                    if target[0] == line.metric and target[2] == included
                }
                covered.update(matches)
        calculations.append(
            {
                "line_id": line.line_id,
                "metric": line.metric,
                "target_id": line.target_id,
                "amount": serialized(amount),
                "evidence_ids": [line.activity.evidence_id, line.factor.evidence_id],
            }
        )
    missing.extend(
        {
            "code": "full_component_missing",
            "metric": metric,
            "category": category,
            "target_id": target,
        }
        for metric, category, target in sorted(wanted - covered)
    )
    complete = {
        metric: not any(m.get("metric", metric) == metric for m in [*errors, *missing])
        for metric in totals
    }
    return {
        "candidate_id": plan["plan_id"],
        "errors": errors,
        "missing": missing,
        "known_subtotals": {
            m: serialized(v) if any(c["metric"] == m for c in calculations) else None
            for m, v in totals.items()
        },
        "totals": {m: serialized(totals[m]) if complete[m] else None for m in totals},
        "calculations": calculations,
        "comparable": all(complete.values()),
        "scope": "declared_evidence_not_independently_verified",
        "dispatch_approved": False,
    }


def robust_pareto(results):
    """Only an entire interval below another establishes guaranteed dominance."""
    points = {r["candidate_id"]: r for r in results if r["comparable"]}
    front = []
    for key, row in points.items():
        if not any(
            other_key != key
            and all(
                Decimal(other["totals"][m]["high"]) <= Decimal(row["totals"][m]["low"])
                for m in ("cost", "carbon")
            )
            and any(
                Decimal(other["totals"][m]["high"]) < Decimal(row["totals"][m]["low"])
                for m in ("cost", "carbon")
            )
            for other_key, other in points.items()
        ):
            front.append(key)
    return sorted(front)


def break_even(plan, draft, result, other_result):
    """One explicit cost factor varied at a time; no invented uncertainty distribution."""
    if not result["comparable"] or not other_result["comparable"]:
        return []
    rows = []
    for raw in draft["lines"]:
        line = MetricLine.model_validate(raw)
        if (
            line.metric != "cost"
            or line.activity.low != line.activity.high
            or line.activity.low == 0
            or line.factor.low != line.factor.high
        ):
            continue
        sign = Decimal(-1) if line.category == "revenue" else Decimal(1)
        base = bounds(result["totals"]["cost"])
        own = line.activity.low * line.factor.low * sign
        other = bounds(other_result["totals"]["cost"])
        thresholds = sorted(
            (other[i] - (base[1 - i] - own)) / (sign * line.activity.low) for i in (0, 1)
        )
        if thresholds[1] < 0:
            continue
        rows.append(
            {
                "candidate_id": plan["plan_id"],
                "other_candidate_id": other_result["candidate_id"],
                "line_id": line.line_id,
                "factor_unit": line.factor.unit,
                "break_even_factor": serialized((max(ZERO, thresholds[0]), thresholds[1])),
                "assumption": "other_inputs_fixed_no_probability_claim",
            }
        )
    return rows
