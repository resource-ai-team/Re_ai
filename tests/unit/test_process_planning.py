"""Bounded route enumeration and research packets; no Neo4j, models, or web calls."""

from itertools import product
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from resource_ai.api.schemas.kg_planning import PathPlanningRequest
from resource_ai.knowledge_graph import planning
from resource_ai.knowledge_graph.planning_reference import (
    PlanningReference,
    compile_reference,
    node_id,
    reference,
)


@pytest.fixture
def value():
    return reference()


@pytest.fixture
def synthetic(value):
    """A diamond with two facilities per operation, entirely synthetic evidence."""
    raw = value.model_dump(mode="json")
    source = {
        **raw["sources"][0],
        "key": "synthetic-source",
        "url": "https://example.invalid/synthetic-test-only",
        "publisher": "Synthetic fixture",
        "locator": "Test fixture",
        "summary": "Synthetic branching, not an operating claim.",
        "sha256": "0" * 64,
        "acquisition": "synthetic-local-test-fixture",
        "digest_basis": "curator-record-not-original-page",
    }
    sourced = {"source_ids": [source["key"]]}
    states = [
        {
            **raw["states"][0],
            **sourced,
            "key": key,
            "label": key,
            "input_forms": ["bottle"] if key == "synthetic-start" else [],
            "is_entry": key == "synthetic-start",
            "is_output": key == "synthetic-output",
        }
        for key in ("synthetic-start", "synthetic-a", "synthetic-b", "synthetic-output")
    ]
    transitions = [
        {
            **raw["transitions"][0],
            **sourced,
            "key": key,
            "label": key,
            "input_state": start,
            "output_state": end,
            "operation_steps": ["Synthetic operation"],
            "conditions": ["Synthetic condition"],
            "risk_checks": ["Synthetic risk question"],
        }
        for key, start, end in (
            ("synthetic-start-a", "synthetic-start", "synthetic-a"),
            ("synthetic-start-b", "synthetic-start", "synthetic-b"),
            ("synthetic-a-output", "synthetic-a", "synthetic-output"),
            ("synthetic-b-output", "synthetic-b", "synthetic-output"),
        )
    ]
    facilities = [
        {
            **raw["facilities"][0],
            **sourced,
            "key": key,
            "label": key,
            "organisation": "synthetic-organisation",
            "region": "Synthetic test location",
            "address": None,
            "reported_metrics": [],
        }
        for key in ("synthetic-east", "synthetic-west")
    ]
    return PlanningReference.model_validate(
        {
            **raw,
            "sources": [source],
            "states": states,
            "transitions": transitions,
            "organisations": [{"key": "synthetic-organisation", "label": "Synthetic", **sourced}],
            "facilities": facilities,
            "capabilities": [
                {
                    "key": f"{transition['key']}--{facility['key']}",
                    **sourced,
                    "facility": facility["key"],
                    "transition": transition["key"],
                    "basis": "site_role_hypothesis",
                    "conditions": ["Synthetic site check"],
                }
                for transition, facility in product(transitions, facilities)
            ],
        }
    )


def paths(value, material="pet", form="bottle", goal=None, max_steps=8):
    return planning.enumerate_paths(value, material, form, goal, max_steps)[0]


def signature(path):
    return tuple(
        (transition.key, capability.key if capability else None) for transition, capability in path
    )


@pytest.mark.parametrize(
    ("material", "form", "expected_outputs", "expected_lengths"),
    [
        ("pet", "bottle", ["pet-flake", "pet-pellet"], [3, 4]),
        ("pp", "scrap", ["pp-shredded", "pp-pellet", "pp-compound"], [1, 3, 4]),
        ("aluminium", "can", ["al-sheet"], [1]),
        ("aluminium", "chips", [], []),
    ],
)
def test_shipped_routes_respect_input_form_and_retain_intermediate_outputs(
    value, material, form, expected_outputs, expected_lengths
):
    result = paths(value, material, form)
    assert [path[-1][0].output_state for path in result.paths] == expected_outputs
    assert [len(path) for path in result.paths] == expected_lengths
    assert not result.budget_exhausted
    assert not result.depth_limited


def test_true_branches_enumerate_every_facility_combination(synthetic):
    result = paths(synthetic)
    assert len(result.paths) == 8
    actual = {
        (path[0][0].output_state, path[0][1].facility, path[1][1].facility) for path in result.paths
    }
    assert actual == set(
        product(
            ("synthetic-a", "synthetic-b"),
            ("synthetic-east", "synthetic-west"),
            ("synthetic-east", "synthetic-west"),
        )
    )
    plans = [
        planning.make_plan(synthetic, path, audience="internal", assumed_form=False)
        for path in result.paths
    ]
    assert len({plan.plan_id for plan in plans}) == 8
    assert {plan.research_packet["handoffs"][0]["kind"] for plan in plans} == {
        "within_site_candidate",
        "external_handover_candidate",
    }


def test_missing_facility_keeps_the_route_and_exposes_the_gap(synthetic):
    raw = synthetic.model_dump(mode="json")
    raw["capabilities"] = [
        cap for cap in raw["capabilities"] if cap["transition"] != "synthetic-a-output"
    ]
    synthetic = PlanningReference.model_validate(raw)
    result = paths(synthetic)
    assert len(result.paths) == 6
    missing = [path for path in result.paths if path[-1][1] is None]
    assert len(missing) == 2
    plan = planning.make_plan(synthetic, missing[0], audience="internal", assumed_form=False)
    stage = plan.research_packet["stages"][-1]
    assert stage["facility_binding"] == "missing_facility"
    assert stage["facility"] is None
    assert stage["organisation"] is None
    assert stage["capability"] is None
    assert stage["suitability"] == "not_assessed"


def test_cycle_guard_does_not_repeat_states_or_hide_the_acyclic_branches(synthetic):
    raw = synthetic.model_dump(mode="json")
    raw["transitions"].append(
        {
            **raw["transitions"][0],
            "key": "synthetic-cycle",
            "input_state": "synthetic-a",
            "output_state": "synthetic-start",
        }
    )
    cyclic = PlanningReference.model_validate(raw)
    result = paths(cyclic)
    assert [signature(path) for path in result.paths] == [
        signature(path) for path in paths(synthetic).paths
    ]
    assert not result.budget_exhausted
    assert all("synthetic-cycle" not in {t.key for t, _ in path} for path in result.paths)


@pytest.mark.parametrize(
    ("max_steps", "count", "depth_limited"), [(2, 0, True), (3, 1, True), (4, 2, False)]
)
def test_depth_limit_reports_unexplored_continuations(value, max_steps, count, depth_limited):
    result = paths(value, max_steps=max_steps)
    assert len(result.paths) == count
    assert result.depth_limited is depth_limited
    assert all(len(path) <= max_steps for path in result.paths)


def test_expansion_budget_stops_deterministically_and_reports_truncation(monkeypatch, synthetic):
    full = paths(synthetic).paths
    monkeypatch.setattr(planning, "MAX_EXPANSIONS", 2)
    result = paths(synthetic)
    assert result.expansions == 2
    assert result.budget_exhausted is True
    assert len(result.paths) == 1
    assert signature(result.paths[0]) == signature(full[0])


def test_plan_budget_retains_the_deterministic_prefix(monkeypatch, synthetic):
    full = paths(synthetic).paths
    monkeypatch.setattr(planning, "MAX_PLANS", 3)
    limited = paths(synthetic)
    assert limited.budget_exhausted is True
    assert [signature(path) for path in limited.paths] == [signature(path) for path in full[:3]]


@pytest.mark.parametrize(
    ("goal", "expected_lengths"), [("pet-sorted", [1]), ("pet-flake", [3]), ("pet-pellet", [4])]
)
def test_explicit_goal_can_be_a_nonoutput_state_and_stops_at_the_goal(
    value, goal, expected_lengths
):
    result = paths(value, goal=goal)
    assert [len(path) for path in result.paths] == expected_lengths
    assert all(path[-1][0].output_state == goal for path in result.paths)


def test_enumeration_and_plan_ids_ignore_storage_order_and_do_not_mutate_reference(synthetic):
    original = synthetic.model_dump(mode="json")
    shuffled = PlanningReference.model_validate(
        {
            **original,
            **{group: list(reversed(original[group])) for group in planning.GROUPS},
        }
    )
    before = paths(synthetic).paths
    after = paths(shuffled).paths
    assert [signature(path) for path in before] == [signature(path) for path in after]
    assert [
        planning.make_plan(synthetic, path, audience="internal", assumed_form=False).plan_id
        for path in before
    ] == [
        planning.make_plan(shuffled, path, audience="internal", assumed_form=False).plan_id
        for path in after
    ]
    assert synthetic.model_dump(mode="json") == original


def test_plan_preserves_hypotheses_unknown_current_terms_and_unverified_handoffs(value):
    plan = planning.make_plan(
        value, paths(value).paths[-1], audience="internal", assumed_form=False
    )
    packet = plan.research_packet
    assert plan.state == "graph_compatible"
    assert plan.recommended is False
    assert plan.dispatch_approved is False
    assert packet["input_state_specifications_verified"] is False
    assert packet["stages"][-1]["facility_binding"] == "site_role_hypothesis"
    assert packet["stages"][-1]["facility"]["key"] == "bgf-yesan"
    assert {stage["facility_binding"] for stage in packet["stages"][:-1]} == {
        "published_site_process"
    }
    for stage in packet["stages"]:
        assert stage["suitability"] == "not_assessed"
        assert all(
            stage["facility"][field] is None
            for field in (
                "current_permit_scope",
                "current_available_capacity_kg",
                "current_acceptance",
                "current_price_krw_per_kg",
            )
        )
    assert [leg["kind"] for leg in packet["handoffs"]] == [
        "within_site_candidate",
        "within_site_candidate",
        "external_handover_candidate",
    ]
    assert all(leg["handover_confirmed"] is False for leg in packet["handoffs"])
    assert all(
        leg["distance_km"] is None
        and leg["transport_cost_krw"] is None
        and leg["transport_kg_co2e"] is None
        for leg in packet["handoffs"]
    )
    assert packet["legal"]["state"] == "not_assessed"
    assert packet["cost"] == {"state": "not_evaluated", "total_krw": None}
    assert packet["carbon"] == {"state": "not_evaluated", "total_kg_co2e": None}
    targets = {stage["stage_id"] for stage in packet["stages"]} | {
        leg["leg_id"] for leg in packet["handoffs"]
    }
    assert {check["target_id"] for check in packet["checks"]} == targets
    assert {check["state"] for check in packet["checks"]} == {"needs_verification"}


def test_one_blocked_source_withholds_the_entire_external_packet_but_keeps_identity(value):
    raw = value.model_dump(mode="json")
    next(source for source in raw["sources"] if source["key"] == "samyang-pet")[
        "model_transfer_allowed"
    ] = False
    blocked = PlanningReference.model_validate(raw)
    path = paths(blocked).paths[-1]
    internal = planning.make_plan(blocked, path, audience="internal", assumed_form=False)
    external = planning.make_plan(blocked, path, audience="external_model", assumed_form=False)
    assert internal.research_packet is not None
    assert external.research_packet is None
    assert external.plan_id == internal.plan_id
    assert internal.external_model_allowed is external.external_model_allowed is False
    assert external.withheld_evidence_ids == [node_id("Evidence", "samyang-pet")]
    assert "stages" not in external.model_dump_json()


def test_blocked_source_outside_a_route_does_not_withhold_unrelated_evidence(value):
    raw = value.model_dump(mode="json")
    next(source for source in raw["sources"] if source["key"] == "bgf-recycling")[
        "model_transfer_allowed"
    ] = False
    blocked = PlanningReference.model_validate(raw)
    plan = planning.make_plan(
        blocked, paths(blocked).paths[0], audience="external_model", assumed_form=False
    )
    assert plan.external_model_allowed is True
    assert plan.withheld_evidence_ids == []
    assert plan.research_packet is not None
    assert {source["key"] for source in plan.research_packet["evidence"]} == {"samyang-pet"}


@pytest.fixture
def local_planner(monkeypatch, value):
    """Isolate orchestration from the separately tested verified Neo4j loader."""
    config = SimpleNamespace(namespace="synthetic-test-namespace")
    snapshot = {"generation": "synthetic-generation", "reference": compile_reference(value)[1]}
    monkeypatch.setattr(planning.store, "_read", lambda config, operation: operation(object()))
    monkeypatch.setattr(
        planning.store,
        "_head",
        lambda *args: (
            snapshot["generation"],
            {"meta": {"graph_sha256": "synthetic-graph-digest"}},
        ),
    )
    monkeypatch.setattr(planning, "load_reference", lambda *args: (value, snapshot["reference"]))
    return planning.ProcessPlanner(config), snapshot


@pytest.mark.parametrize("form", [None, "unknown", "미확인"])
def test_unknown_input_form_reports_entry_assumptions_in_every_plan(local_planner, form):
    planner, _ = local_planner
    result = planner.search(PathPlanningRequest(material="PET", input_form=form))
    assert result.status == "plans"
    assert result.search_scope["entry_state_keys"] == ["pet-bottle", "pet-flake"]
    assert result.pagination.enumerated_plans == 3
    assert "input_form" in result.missing_fields
    assert all(item.research_packet["input_form_assumed"] is True for item in result.items)


def test_input_aliases_are_normalized_and_do_not_expand_aluminium_chips_to_cans(local_planner):
    planner, _ = local_planner
    cans = planner.search(PathPlanningRequest(material="알루미늄", input_form="음료캔"))
    assert cans.search_scope["normalized_form"] == "can"
    assert cans.pagination.enumerated_plans == 1
    chips = planner.search(PathPlanningRequest(material="aluminium", input_form="chips"))
    assert chips.status == "no_plans"
    assert chips.search_scope["entry_state_not_covered"] is True
    assert chips.items == []


def test_all_pages_cover_the_same_pool_and_allow_page_size_changes(local_planner):
    planner, _ = local_planner
    first = planner.search(PathPlanningRequest(material="PP", input_form="scrap", page_size=1))
    assert first.pagination.enumerated_plans == 3
    assert first.pagination.has_more is True
    assert first.pagination.complete_in_this_response is False
    last = planner.search(
        PathPlanningRequest(
            material="PP",
            input_form="scrap",
            page_size=100,
            cursor=first.pagination.next_cursor,
        )
    )
    full = planner.search(PathPlanningRequest(material="PP", input_form="scrap"))
    assert [item.plan_id for item in [*first.items, *last.items]] == [
        item.plan_id for item in full.items
    ]
    assert last.pagination.returned_count == 2
    assert last.pagination.has_more is False
    assert last.pagination.next_cursor is None
    assert last.pagination.complete_in_this_response is False
    assert full.pagination.complete_in_this_response is True


@pytest.mark.parametrize("field", ["generation", "reference"])
def test_cursor_rejects_changed_graph_or_reference_snapshot(local_planner, field):
    planner, snapshot = local_planner
    first = planner.search(PathPlanningRequest(material="PET", input_form="bottle", page_size=1))
    snapshot[field] = "replacement-snapshot"
    with pytest.raises(planning.store.GraphStoreError) as error:
        planner.search(
            PathPlanningRequest(
                material="PET",
                input_form="bottle",
                cursor=first.pagination.next_cursor,
            )
        )
    assert error.value.code == "stale-cursor"


@pytest.mark.parametrize(
    "changes", [{"input_form": "flake"}, {"max_steps": 3}, {"audience": "external_model"}]
)
def test_cursor_is_bound_to_the_original_request(local_planner, changes):
    planner, _ = local_planner
    original = {"material": "PET", "input_form": "bottle"}
    first = planner.search(PathPlanningRequest(**original, page_size=1))
    with pytest.raises(planning.store.GraphStoreError) as error:
        planner.search(
            PathPlanningRequest(**{**original, **changes}, cursor=first.pagination.next_cursor)
        )
    assert error.value.code == "invalid-cursor"


@pytest.mark.parametrize("offset", [2, 1000])
def test_cursor_cannot_point_past_the_enumerated_pool(local_planner, offset):
    planner, _ = local_planner
    first = planner.search(PathPlanningRequest(material="PET", input_form="bottle", page_size=1))
    stamp = planning.decode_cursor(first.pagination.next_cursor)
    stamp["offset"] = offset
    with pytest.raises(planning.store.GraphStoreError) as error:
        planner.search(
            PathPlanningRequest(
                material="PET",
                input_form="bottle",
                cursor=planning.encode_cursor(stamp),
            )
        )
    assert error.value.code == "invalid-cursor"


@pytest.mark.parametrize("goal", ["missing-state", "pp-pellet"])
def test_goal_must_exist_for_the_requested_material(local_planner, goal):
    planner, _ = local_planner
    with pytest.raises(planning.store.GraphStoreError) as error:
        planner.search(PathPlanningRequest(material="PET", input_form="bottle", goal_state=goal))
    assert error.value.code == "invalid-goal"


def test_search_reports_depth_and_budget_limits_without_claiming_exhaustive_search(
    local_planner, monkeypatch
):
    planner, _ = local_planner
    shallow = planner.search(PathPlanningRequest(material="PET", input_form="bottle", max_steps=2))
    assert shallow.status == "no_plans"
    assert shallow.search_scope["depth_limited"] is True
    assert shallow.search_scope["all_paths_enumerated"] is False
    monkeypatch.setattr(planning, "MAX_EXPANSIONS", 3)
    bounded = planner.search(PathPlanningRequest(material="PET", input_form="bottle"))
    assert bounded.pagination.enumerated_plans == 1
    assert bounded.search_scope["budget_exhausted"] is True
    assert bounded.search_scope["all_paths_enumerated"] is False
    assert bounded.search_scope["ranking_applied"] is False


def test_external_response_does_not_echo_customer_context(local_planner):
    planner, _ = local_planner
    result = planner.search(
        PathPlanningRequest(
            material="PET",
            input_form="bottle",
            region="private-customer-location",
            current_treatment="private-current-treatment",
            audience="external_model",
        )
    )
    assert result.input_context == {}
    assert "private-customer" not in result.model_dump_json()
    assert "private-current" not in result.model_dump_json()


@pytest.mark.parametrize(
    ("field", "private_value", "replacement"),
    [
        ("color", "private-color-64183", "private-color-97216"),
        ("contamination", "private-contamination-64183", "private-contamination-97216"),
        ("quantity_kg", 77777.125, 88888.375),
        ("generation_process", "private-generation-64183", "private-generation-97216"),
        ("region", "private-region-64183", "private-region-97216"),
        ("current_treatment", "incineration", "landfill"),
    ],
)
def test_external_cursor_cannot_fingerprint_private_context_or_prevent_private_changes(
    local_planner, field, private_value, replacement
):
    planner, _ = local_planner
    context = {"material": "PET", "input_form": "bottle", "audience": "external_model"}
    public = planner.search(PathPlanningRequest(**context, page_size=1))
    private = planner.search(PathPlanningRequest(**context, **{field: private_value}, page_size=1))
    # The complete opaque token must be independent of both presence and value,
    # preventing dictionary recovery of low-entropy fields from its fingerprint.
    assert private.pagination.next_cursor == public.pagination.next_cursor
    resumed = planner.search(
        PathPlanningRequest(
            **context,
            **{field: replacement},
            cursor=private.pagination.next_cursor,
        )
    )
    assert resumed.status == "plans"
    assert resumed.pagination.returned_count == 1
    assert resumed.items[0].plan_id != private.items[0].plan_id
    assert private.input_context == resumed.input_context == {}
    assert str(private_value) not in private.model_dump_json()
    assert str(replacement) not in resumed.model_dump_json()


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("color", "private-color"),
        ("contamination", "private-contamination"),
        ("quantity_kg", 77777.125),
        ("generation_process", "private-generation"),
        ("region", "private-region"),
        ("current_treatment", "incineration"),
    ],
)
def test_internal_cursor_remains_bound_to_the_full_private_request(
    local_planner, field, replacement
):
    planner, _ = local_planner
    context = {"material": "PET", "input_form": "bottle", "audience": "internal"}
    first = planner.search(PathPlanningRequest(**context, page_size=1))
    with pytest.raises(planning.store.GraphStoreError) as error:
        planner.search(
            PathPlanningRequest(
                **context, **{field: replacement}, cursor=first.pagination.next_cursor
            )
        )
    assert error.value.code == "invalid-cursor"


@pytest.mark.parametrize(
    "changes",
    [
        {"material": "PP"},
        {"input_form": "flake"},
        {"goal_state": "pet-flake"},
        {"max_steps": 3},
        {"audience": "internal"},
    ],
)
def test_external_cursor_still_binds_the_public_enumeration_inputs(local_planner, changes):
    planner, _ = local_planner
    context = {"material": "PET", "input_form": "bottle", "audience": "external_model"}
    first = planner.search(PathPlanningRequest(**context, page_size=1))
    with pytest.raises(planning.store.GraphStoreError) as error:
        planner.search(
            PathPlanningRequest(**{**context, **changes}, cursor=first.pagination.next_cursor)
        )
    assert error.value.code == "invalid-cursor"


@pytest.mark.parametrize("budget", ["MAX_EXPANSIONS", "MAX_PLANS"])
def test_external_cursor_is_invalidated_by_changed_search_budgets(
    local_planner, monkeypatch, budget
):
    planner, _ = local_planner
    context = {"material": "PET", "input_form": "bottle", "audience": "external_model"}
    first = planner.search(PathPlanningRequest(**context, page_size=1))
    monkeypatch.setattr(planning, budget, getattr(planning, budget) + 1)
    with pytest.raises(planning.store.GraphStoreError) as error:
        planner.search(PathPlanningRequest(**context, cursor=first.pagination.next_cursor))
    assert error.value.code == "invalid-cursor"


@pytest.mark.parametrize(
    ("material", "status"),
    [(None, "needs_input"), ("미확인", "needs_input"), ("PETroleum", "unsupported_material")],
)
def test_unusable_material_does_not_open_a_graph_transaction(monkeypatch, material, status):
    def unexpected_read(*args):
        raise AssertionError("Graph access is unnecessary for unknown or unsupported material")

    monkeypatch.setattr(planning.store, "_read", unexpected_read)
    result = planning.ProcessPlanner().search(PathPlanningRequest(material=material))
    assert result.status == status
    assert result.items == []


@pytest.mark.parametrize(
    "raw",
    [
        {"max_steps": 0},
        {"max_steps": 9},
        {"goal_state": ""},
        {"goal_state": "x" * 102},
        {"page_size": 0},
        {"page_size": 101},
        {"quantity_kg": float("nan")},
        {"cursor": "x" * 2049},
        {"top_k": 1},
        {"priority": "carbon"},
        {"cypher": "MATCH (n) RETURN n"},
        {"audience": "public"},
    ],
)
def test_request_contract_rejects_unsafe_or_out_of_scope_fields(raw):
    with pytest.raises(ValidationError):
        PathPlanningRequest.model_validate(raw)


@pytest.mark.parametrize("token", ["%%%", "e30", "W10", "bnVsbA", "wA", "a"])
def test_malformed_cursor_returns_public_invalid_cursor_error(token):
    with pytest.raises(planning.store.GraphStoreError) as error:
        planning.decode_cursor(token)
    assert error.value.code == "invalid-cursor"


@pytest.mark.parametrize(
    "changes",
    [
        {"offset": True},
        {"offset": -1},
        {"offset": 1.5},
        {"v": "previous-algorithm"},
        {"generation": ""},
        {"namespace": 1},
        {"request": None},
        {"reference": []},
        {"unexpected": "query"},
    ],
)
def test_cursor_schema_rejects_ambiguous_offsets_or_unbound_identity(changes):
    stamp = {
        "v": planning.ALGORITHM,
        "generation": "g",
        "namespace": "n",
        "request": "request-digest",
        "reference": "reference-digest",
        "offset": 0,
    }
    assert planning.decode_cursor(planning.encode_cursor(stamp)) == stamp
    with pytest.raises(planning.store.GraphStoreError) as error:
        planning.decode_cursor(planning.encode_cursor({**stamp, **changes}))
    assert error.value.code == "invalid-cursor"
