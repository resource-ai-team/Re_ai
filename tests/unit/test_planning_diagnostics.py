"""Coverage omissions, resin scope and independently sourced paper sequence."""

import pytest
from pydantic import ValidationError

from resource_ai.api.schemas.facility_assessment import LotFacts
from resource_ai.knowledge_graph.facility_assessment import assess_capability
from resource_ai.knowledge_graph.material_scope import material_scope
from resource_ai.knowledge_graph.planning import enumerate_paths, make_plan
from resource_ai.knowledge_graph.planning_diagnostics import coverage_report, diagnose_reference
from resource_ai.knowledge_graph.planning_reference import PlanningReference, reference
from resource_ai.knowledge_graph.query import resolve_material


def prune_supports(raw):
    """Fixture edits must also remove declarations for deliberately deleted records."""
    for source in raw["sources"]:
        source["supported_records"] = {
            group: [key for key in keys if any(r["key"] == key for r in raw[group])]
            for group, keys in source["supported_records"].items()
        }
        source["supported_records"] = {
            group: keys for group, keys in source["supported_records"].items() if keys
        }
    return raw


def test_real_paper_sequence_preserves_ten_operations_in_five_sourced_stages():
    value = reference()
    enumeration, _ = enumerate_paths(value, "paper", "cardboard", None, 6)
    assert len(enumeration.paths) == 1
    path = enumeration.paths[0]
    assert [step for transition, _ in path for step in transition.operation_steps] == [
        "해리",
        "정선",
        "농축",
        "고해",
        "지필형성",
        "압착탈수",
        "건조",
        "광택",
        "권취",
        "재단",
    ]
    assert len(path) == 5
    assert {c.facility for _, c in path} == {"hansol-damyang"}
    assert all("hansol-process" in t.source_ids for t, _ in path)
    assert path[0][0].input_state == "paper-recovered"
    assert path[-1][0].output_state == "paper-containerboard"
    assert not enumeration.depth_limited
    packet = make_plan(value, path, audience="internal", assumed_form=False).research_packet
    assert all(not leg["handover_confirmed"] for leg in packet["handoffs"])


def test_missing_transition_exposes_dead_branch_and_unreachable_outputs():
    raw = reference().model_dump(mode="json")
    raw["transitions"] = [t for t in raw["transitions"] if t["key"] != "pp-pelletise"]
    raw["capabilities"] = [c for c in raw["capabilities"] if c["transition"] != "pp-pelletise"]
    value = PlanningReference.model_validate(prune_supports(raw))
    scope = diagnose_reference(value, "pp", ["pp-scrap"])
    assert "pp-washed" in scope["non_output_dead_end_state_keys"]
    assert "pp-compound" in scope["unreachable_transition_keys"]
    assert {"pp-pellet", "pp-compound"} <= set(scope["unreachable_output_state_keys"])
    assert "pp-washed" in scope["reachable_states_without_output_path"]
    assert scope["not_a_feasibility_decision"] is True


def test_missing_facility_is_not_mislabelled_as_a_confirmed_cross_site_identity():
    raw = reference().model_dump(mode="json")
    raw["capabilities"] = [c for c in raw["capabilities"] if c["transition"] != "pp-clean"]
    value = PlanningReference.model_validate(prune_supports(raw))
    enumeration, _ = enumerate_paths(value, "pp", "scrap", "pp-pellet", 6)
    packet = make_plan(
        value, enumeration.paths[0], audience="internal", assumed_form=False
    ).research_packet
    assert {leg["kind"] for leg in packet["handoffs"]} == {"unresolved_handover_candidate"}
    assert all(not leg["facility_identity_resolved"] for leg in packet["handoffs"])
    assert diagnose_reference(value, "pp", ["pp-scrap"])["transitions_without_facility_keys"] == [
        "pp-clean"
    ]


@pytest.mark.parametrize(
    "label,subtype",
    [("HDPE", "hdpe"), ("LDPE", "ldpe"), ("LLDPE", "lldpe"), ("고밀도 폴리에틸렌", "hdpe")],
)
def test_resin_alias_never_claims_grade_specific_intake(label, subtype):
    assert resolve_material(label) == "pe"
    scope = material_scope(label, "pe")
    assert scope["supplied_subtype"] == subtype
    assert scope["subtype_applicability_verified"] is False
    value = reference()
    capability = next(c for c in value.capabilities if c.transition == "pe-prepare")
    result = assess_capability(value, capability, LotFacts(material=label, input_form="film"))
    check = next(c for c in result.checks if c.code == "material_subtype")
    assert check.status == "needs_verification"
    assert check.observed == subtype
    assert result.dispatch_approved is False


def test_global_coverage_reports_unbound_site_without_fabricating_capabilities():
    report = coverage_report(reference())
    assert report["facilities_without_transition_keys"] == ["bgf-incheon", "rm-hwaseong"]
    assert {row["material"] for row in report["materials"]} == {
        "pet",
        "pp",
        "pe",
        "aluminium",
        "paper",
    }
    assert (
        report["source_support_scope"]
        == "curator_declared_record_links_not_independent_content_audit"
    )


def test_projected_pe_stage_keeps_grade_unknown_without_copying_initial_resin():
    value = reference()
    capability = next(c for c in value.capabilities if c.transition == "pe-pelletise")
    result = assess_capability(value, capability, LotFacts(), projected=True)
    check = next(c for c in result.checks if c.code == "material_subtype")
    assert check.status == "needs_verification"
    assert check.observed is None
    assert result.input_context.material is None
    assert result.input_basis == "projected_state_not_measured"


def test_intermediate_goal_does_not_mislabel_downstream_processes_as_broken():
    report = diagnose_reference(reference(), "pp", ["pp-scrap"], "pp-shredded")
    assert report["requested_goal_state"] == "pp-shredded"
    assert report["reachable_states_without_output_path"] == []


@pytest.mark.parametrize(
    "field,value", [("checked_date", "2026-02-30"), ("source_date", "2025-99-01")]
)
def test_source_dates_must_be_real_calendar_dates(field, value):
    raw = reference().model_dump(mode="json")
    raw["sources"][0][field] = value
    with pytest.raises(ValidationError):
        PlanningReference.model_validate(raw)
