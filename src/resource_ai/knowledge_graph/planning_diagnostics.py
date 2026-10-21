"""Structural gaps in the curated graph, without inventing missing processes."""

from collections import Counter


def diagnose_reference(value, material, entry_keys=None, goal_state=None):
    states = {s.key: s for s in value.states if s.material == material}
    transitions = [t for t in value.transitions if t.input_state in states]
    entries = (
        set(entry_keys)
        if entry_keys is not None
        else {s.key for s in states.values() if s.is_entry}
    )
    reached = set(entries)
    while True:
        following = {t.output_state for t in transitions if t.input_state in reached}
        expanded = reached | following
        if expanded == reached:
            break
        reached = expanded
    outgoing = {t.input_state for t in transitions}
    # Connectivity is measured against registered outputs, not a requested stop.
    targets = {s.key for s in states.values() if s.is_output}
    can_reach_target = set(targets)
    while True:
        predecessors = {t.input_state for t in transitions if t.output_state in can_reach_target}
        expanded = can_reach_target | predecessors
        if expanded == can_reach_target:
            break
        can_reach_target = expanded
    capabilities = [
        c for c in value.capabilities if any(t.key == c.transition for t in transitions)
    ]
    bound = {c.transition for c in capabilities}
    reachable_transitions = [t for t in transitions if t.input_state in reached]
    return {
        "material": material,
        "requested_goal_state": goal_state,
        "entry_state_keys": sorted(entries),
        "registered_states": len(states),
        "registered_transitions": len(transitions),
        "reachable_state_keys": sorted(reached),
        "unreachable_transition_keys": sorted(
            t.key for t in transitions if t.input_state not in reached
        ),
        "unreachable_output_state_keys": sorted(targets - reached),
        "non_output_dead_end_state_keys": sorted(
            key for key in reached if key not in outgoing and not states[key].is_output
        ),
        "reachable_states_without_output_path": sorted(reached - can_reach_target),
        "transitions_without_facility_keys": sorted(
            t.key for t in reachable_transitions if t.key not in bound
        ),
        "facility_bindings_by_basis": dict(sorted(Counter(c.basis for c in capabilities).items())),
        "scope": "registered_curated_graph_only_not_all_real_world_options",
        "not_a_feasibility_decision": True,
    }


def coverage_report(value):
    used_facilities = {c.facility for c in value.capabilities}
    return {
        "counts": {
            group: len(getattr(value, group))
            for group in (
                "sources",
                "states",
                "transitions",
                "organisations",
                "facilities",
                "capabilities",
            )
        },
        "materials": [
            diagnose_reference(value, m) for m in sorted({s.material for s in value.states})
        ],
        "facilities_without_transition_keys": sorted(
            f.key for f in value.facilities if f.key not in used_facilities
        ),
        "source_support_scope": "curator_declared_record_links_not_independent_content_audit",
        "sources": [
            {
                "source_key": s.key,
                "checked_date": s.checked_date,
                "source_date": s.source_date,
                "sha256": s.sha256,
                "digest_basis": s.digest_basis,
                "model_transfer_allowed": s.model_transfer_allowed,
            }
            for s in sorted(value.sources, key=lambda s: s.key)
        ],
    }
