"""Local planning evidence stays typed, site scoped, and short of operating approval."""

import json
import sqlite3
from collections import Counter
from contextlib import closing
from copy import deepcopy

import pytest
from pydantic import ValidationError

from resource_ai.knowledge_graph import ontology as graph
from resource_ai.knowledge_graph import planning_reference as planning
from resource_ai.knowledge_graph.builder import build_stage


@pytest.fixture
def payload():
    # Mutate the shipped corpus rather than maintaining a second reference dataset.
    return planning.reference().model_dump(mode="json")


@pytest.fixture
def stage():
    with closing(sqlite3.connect(":memory:")) as connection:
        build_stage(connection, [], include_curated=True)
        yield connection


def record(payload, group, key):
    return next(row for row in payload[group] if row["key"] == key)


def reseal_and_audit(connection):
    # Prove semantic validation catches tampering even if its checksum is resealed.
    connection.execute(
        "UPDATE kg_meta SET value=? WHERE key='graph_sha256'",
        (graph.graph_digest(connection),),
    )
    return graph.audit_graph(connection)


@pytest.mark.parametrize(
    ("group", "key", "field", "invalid", "message"),
    [
        ("states", "pet-bottle", "source_ids", ["missing-source"], "Unlinked planning source"),
        (
            "transitions",
            "pet-prepare",
            "source_ids",
            ["samyang-pet", "samyang-pet"],
            "Unlinked planning source",
        ),
        ("states", "pet-bottle", "material", "unrecognised", "Unknown planning material"),
        (
            "transitions",
            "pet-prepare",
            "input_state",
            "missing-state",
            "Unlinked material state",
        ),
        (
            "facilities",
            "samyang-sihwa",
            "organisation",
            "missing-organisation",
            "Unlinked organisation",
        ),
        (
            "capabilities",
            "samyang-sihwa--pet-prepare",
            "facility",
            "missing-facility",
            "Unlinked facility capability",
        ),
        (
            "capabilities",
            "samyang-sihwa--pet-prepare",
            "transition",
            "missing-transition",
            "Unlinked facility capability",
        ),
    ],
)
def test_reference_rejects_unbound_or_ambiguous_links(payload, group, key, field, invalid, message):
    record(payload, group, key)[field] = invalid
    with pytest.raises(ValidationError, match=message):
        planning.PlanningReference.model_validate(payload)


@pytest.mark.parametrize("group", planning.GROUPS)
def test_reference_rejects_duplicate_keys_within_each_record_type(payload, group):
    payload[group].append(deepcopy(payload[group][0]))
    with pytest.raises(ValidationError, match="Duplicate planning reference key"):
        planning.PlanningReference.model_validate(payload)


@pytest.mark.parametrize("output_state", ["pp-shredded", "pet-bottle"])
def test_transition_cannot_change_material_or_loop_to_its_input(payload, output_state):
    record(payload, "transitions", "pet-prepare")["output_state"] = output_state
    with pytest.raises(ValidationError, match="Invalid or cross-material transition"):
        planning.PlanningReference.model_validate(payload)


def test_same_local_key_in_distinct_types_does_not_collapse_state_and_operation():
    _, _, nodes, edges = planning.compile_reference()
    state = planning.node_id("MaterialState", "pp-compound")
    transition = planning.node_id("ProcessTransition", "pp-compound")
    assert state != transition
    assert nodes[state]["kind"] == "MaterialState"
    assert nodes[transition]["kind"] == "ProcessTransition"
    assert ("OUTPUT_STATE", transition, state) in edges


def test_reported_design_capacity_preserves_units_and_never_populates_live_capacity():
    value, _, nodes, _ = planning.compile_reference()
    facility = next(row for row in value.facilities if row.key == "samyang-sihwa")
    (metric,) = facility.reported_metrics
    assert (metric.value, metric.unit) == (50000, "tonne/year")
    assert metric.scope == "website-reported-design-capacity-not-current-available"
    assert metric.source_id in facility.source_ids
    stored = nodes[planning.node_id("Facility", facility.key)]["properties"]["record"]
    for key in (
        "current_permit_scope",
        "current_available_capacity_kg",
        "current_acceptance",
        "current_price_krw_per_kg",
    ):
        assert all(getattr(row, key) is None for row in value.facilities)
        assert stored[key] is None


@pytest.mark.parametrize(
    ("field", "claimed_value"),
    [
        ("current_permit_scope", "all PET recycling"),
        ("current_available_capacity_kg", 50000000),
        ("current_acceptance", True),
        ("current_price_krw_per_kg", 100),
    ],
)
def test_reference_cannot_promote_published_information_to_current_terms(
    payload, field, claimed_value
):
    record(payload, "facilities", "samyang-sihwa")[field] = claimed_value
    with pytest.raises(ValidationError) as error:
        planning.PlanningReference.model_validate(payload)
    assert any(item["loc"][-1] == field for item in error.value.errors())


def test_reported_metric_requires_its_own_facility_source(payload):
    metric = record(payload, "facilities", "samyang-sihwa")["reported_metrics"][0]
    # This is a real bundled source, but it supports a different organisation.
    metric["source_id"] = "bgf-recycling"
    with pytest.raises(ValidationError, match="Unlinked reported metric"):
        planning.PlanningReference.model_validate(payload)


def test_reported_capacity_cannot_be_relabelled_as_currently_available(payload):
    metric = record(payload, "facilities", "samyang-sihwa")["reported_metrics"][0]
    metric["scope"] = "currently-available"
    with pytest.raises(ValidationError):
        planning.PlanningReference.model_validate(payload)


def test_site_role_hypotheses_remain_scoped_and_distinct_from_published_site_processes():
    value, _, nodes, edges = planning.compile_reference()
    bgf = [cap for cap in value.capabilities if cap.facility.startswith("bgf-")]
    assert bgf
    assert {cap.facility for cap in bgf} == {"bgf-yesan", "bgf-hwaseong"}
    assert all(cap.basis == "site_role_hypothesis" for cap in bgf)
    assert all(cap.source_ids == ["bgf-recycling"] and cap.conditions for cap in bgf)
    # The listed sorting site has no evidence-backed transition in this corpus.
    assert not any(cap.facility == "bgf-incheon" for cap in value.capabilities)
    direct = next(cap for cap in value.capabilities if cap.key == "samyang-sihwa--pet-prepare")
    assert direct.basis == "published_site_process"
    for capability in (direct, *bgf):
        identifier = planning.node_id("FacilityCapability", capability.key)
        props = nodes[identifier]["properties"]
        assert props["record"]["basis"] == capability.basis
        assert props["dispatch_approved"] is False
        assert (
            "AT_FACILITY",
            identifier,
            planning.node_id("Facility", capability.facility),
        ) in edges
        assert (
            "FOR_TRANSITION",
            identifier,
            planning.node_id("ProcessTransition", capability.transition),
        ) in edges


def test_curated_stage_imports_the_complete_planning_corpus_with_source_links(stage):
    rows = stage.execute(
        "SELECT id,kind,properties FROM kg_nodes "
        "WHERE json_extract(properties,'$.planning_key') IS NOT NULL"
    ).fetchall()
    assert Counter(row[1] for row in rows) == {
        "Source": 7,
        "Evidence": 7,
        "MaterialState": 19,
        "ProcessTransition": 14,
        "Organisation": 5,
        "Facility": 7,
        "FacilityCapability": 14,
    }
    planning_edges = stage.execute(
        "SELECT e.kind,e.properties FROM kg_edges e JOIN kg_nodes n ON n.id=e.source_id "
        "WHERE json_extract(n.properties,'$.planning_key') IS NOT NULL"
    ).fetchall()
    assert len(planning_edges) == 172
    assert Counter(kind for kind, _ in planning_edges)["FROM_SOURCE"] == 7
    assert all(json.loads(props)["dispatch_approved"] is False for _, props in planning_edges)
    assert len({json.loads(row[2])["planning_bundle_sha256"] for row in rows}) == 1
    planning.audit_reference(stage)
    assert graph.audit_graph(stage)["dispatch_approved"] is False


@pytest.mark.parametrize("change", ["label", "record", "fingerprint"])
def test_audit_rejects_modified_planning_node_with_resealed_digest(stage, change):
    identifier = planning.node_id("FacilityCapability", "bgf-yesan--pet-pelletise")
    label, encoded = stage.execute(
        "SELECT label,properties FROM kg_nodes WHERE id=?", (identifier,)
    ).fetchone()
    props = json.loads(encoded)
    if change == "label":
        label = "Unexpected published site process"
    elif change == "record":
        props["record"]["basis"] = "published_site_process"
    else:
        props["planning_bundle_sha256"] = "0" * 64
    stage.execute(
        "UPDATE kg_nodes SET label=?,properties=? WHERE id=?",
        (label, graph.canonical(props), identifier),
    )
    with pytest.raises(ValueError, match="Planning reference record mismatch"):
        reseal_and_audit(stage)


@pytest.mark.parametrize(
    ("kind", "source_kind", "source_key", "target_kind", "wrong_target_key"),
    [
        ("OUTPUT_STATE", "ProcessTransition", "pet-prepare", "MaterialState", "pet-pellet"),
        ("SUPPORTED_BY", "ProcessTransition", "pet-prepare", "Evidence", "bgf-recycling"),
        ("OPERATED_BY", "Facility", "samyang-sihwa", "Organisation", "bgf-ecocycle"),
        (
            "AT_FACILITY",
            "FacilityCapability",
            "bgf-yesan--pet-pelletise",
            "Facility",
            "bgf-incheon",
        ),
        (
            "FOR_TRANSITION",
            "FacilityCapability",
            "samyang-sihwa--pet-prepare",
            "ProcessTransition",
            "pet-pelletise",
        ),
    ],
)
def test_audit_rejects_misbound_planning_edge_of_valid_ontology_types(
    stage, kind, source_kind, source_key, target_kind, wrong_target_key
):
    source = planning.node_id(source_kind, source_key)
    target = planning.node_id(target_kind, wrong_target_key)
    cursor = stage.execute(
        "UPDATE kg_edges SET id=?,target_id=? WHERE kind=? AND source_id=?",
        (graph.identity("edge", [kind, source, target]), target, kind, source),
    )
    assert cursor.rowcount == 1
    with pytest.raises(ValueError, match="Planning reference edge binding mismatch"):
        reseal_and_audit(stage)


def test_audit_rejects_planning_edge_promoted_to_operational_basis(stage):
    source = planning.node_id("ProcessTransition", "pet-prepare")
    stage.execute(
        "UPDATE kg_edges SET properties=? WHERE kind='OUTPUT_STATE' AND source_id=?",
        (
            graph.canonical({"dispatch_approved": False, "basis": "approved-operating-route"}),
            source,
        ),
    )
    with pytest.raises(ValueError, match="Planning reference edge changed"):
        reseal_and_audit(stage)


@pytest.mark.parametrize("change", ["missing", "extra"])
def test_audit_rejects_incomplete_or_expanded_planning_bindings(stage, change):
    source = planning.node_id("ProcessTransition", "pet-prepare")
    if change == "missing":
        stage.execute("DELETE FROM kg_edges WHERE kind='OUTPUT_STATE' AND source_id=?", (source,))
    else:
        target = planning.node_id("MaterialState", "pet-pellet")
        stage.execute(
            "INSERT INTO kg_edges VALUES(?,?,?,?,?)",
            (
                graph.identity("edge", ["OUTPUT_STATE", source, target]),
                "OUTPUT_STATE",
                source,
                target,
                graph.canonical(
                    {"dispatch_approved": False, "basis": "source-backed-planning-reference-v1"}
                ),
            ),
        )
    with pytest.raises(ValueError, match="Planning reference edge binding mismatch"):
        reseal_and_audit(stage)


def test_uncurated_stage_excludes_planning_records_and_relations():
    with closing(sqlite3.connect(":memory:")) as connection:
        build_stage(connection, [], include_curated=False)
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM kg_nodes "
                "WHERE json_extract(properties,'$.planning_key') IS NOT NULL"
            ).fetchone()[0]
            == 0
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM kg_nodes WHERE kind IN "
                "('MaterialState','ProcessTransition','Organisation','Facility','FacilityCapability')"
            ).fetchone()[0]
            == 0
        )
        assert not planning.PLANNING_RELATIONS.intersection(
            row[0] for row in connection.execute("SELECT DISTINCT kind FROM kg_edges")
        )
        planning.audit_reference(connection)
        assert graph.audit_graph(connection)["dispatch_approved"] is False


def test_audit_rejects_planning_data_marked_as_uncurated(stage):
    stage.execute("UPDATE kg_meta SET value='false' WHERE key='includes_curated'")
    with pytest.raises(ValueError, match="Unexpected planning data without curated import"):
        reseal_and_audit(stage)
