from copy import deepcopy
from types import SimpleNamespace as NS

import pytest

from resource_ai.knowledge_graph.facility_identity import BASIS, identity_evidence
from resource_ai.knowledge_graph.graph_store import GraphStoreError
from resource_ai.knowledge_graph.planning_reference import reference


def test_curated_pet_cup_notice_establishes_identity_but_no_bottle_process():
    value = reference()
    facility = next(row for row in value.facilities if row.key == "rm-hwaseong")
    link = facility.registry_identity_links[0]
    record = {
        "listing_id": link.listing_id,
        "source_sha256": link.registry_source_sha256,
        "record_sha256": link.record_sha256,
    }
    for transition in ("pet-prepare", "pet-wash", "pet-pelletise", "pp-clean"):
        result = identity_evidence(value, record, transition)
        assert result["identity_status"] == "reviewed_same_site"
        assert result["stage_capability"] == "not_established"
        assert result["capability_keys"] == []
        assert result["current_permit_scope"] is None
        assert result["current_acceptance"] is None
    notice = next(row for row in value.sources if row.key == "cosmo-rm-cups")
    assert notice.source_date is None  # No publication date in the acquired PDF.


def source(key):
    return NS(
        key=key,
        url=f"https://example.test/{key}",
        checked_date="2026-10-24",
        source_date="2023-01-01",
        locator=f"사업장·공정 {key}",
    )


def fixture():
    record = {
        "listing_id": "facility-listing:reviewed-snapshot-row",
        "source_sha256": "a" * 64,
        "record_sha256": "b" * 64,
        "name": "가상 테스트 회사",
        "address": "가상 테스트 사업장 주소",
        "coordinates": [37.2, 127.0],
        "current_acceptance": True,  # Untrusted operational claims cannot leak through.
        "current_permit_scope": "not verified",
        "current_available_capacity_kg": 1000,
    }
    link = NS(
        listing_id=record["listing_id"],
        registry_source_sha256=record["source_sha256"],
        record_sha256=record["record_sha256"],
        source_ids=["identity-site"],
        checked_date="2026-10-24",
        basis=BASIS,
    )
    value = NS(
        facilities=[NS(key="test-site", registry_identity_links=[link])],
        capabilities=[
            NS(
                key="test-site--pp-prepare",
                facility="test-site",
                transition="pp-prepare",
                basis="published_site_process",
                source_ids=["process-prepare"],
            ),
            NS(
                key="test-site--pp-clean",
                facility="test-site",
                transition="pp-clean",
                basis="site_role_hypothesis",
                source_ids=["process-clean-hypothesis"],
            ),
            NS(
                key="other-site--pp-prepare",
                facility="other-site",
                transition="pp-prepare",
                basis="published_site_process",
                source_ids=["unrelated-site"],
            ),
        ],
        sources=[
            source(key)
            for key in (
                "identity-site",
                "process-prepare",
                "process-clean-hypothesis",
                "unrelated-site",
            )
        ],
    )
    return value, record


def test_exact_reviewed_row_binds_only_its_site_and_requested_stage():
    value, record = fixture()
    result = identity_evidence(value, record, "pp-prepare")
    assert result["identity_status"] == "reviewed_same_site"
    assert result["facility_key"] == "test-site"
    assert result["transition_key"] == "pp-prepare"
    assert result["stage_capability"] == "published_site_process"
    assert result["capability_keys"] == ["test-site--pp-prepare"]
    assert result["identity_link"] == vars(value.facilities[0].registry_identity_links[0])
    assert result["source_evidence"] == [
        {"source_id": key, **{k: v for k, v in vars(source(key)).items() if k != "key"}}
        for key in ["identity-site", "process-prepare"]
    ]
    assert (
        result["current_permit_scope"]
        is result["current_acceptance"]
        is result["current_available_capacity_kg"]
        is None
    )
    assert result["dispatch_approved"] is False


def test_site_identity_does_not_promote_an_organisation_process_hypothesis():
    value, record = fixture()
    result = identity_evidence(value, record, "pp-clean")
    assert result["stage_capability"] == "site_role_hypothesis"
    assert result["capability_keys"] == ["test-site--pp-clean"]
    assert {row["source_id"] for row in result["source_evidence"]} == {
        "identity-site",
        "process-clean-hypothesis",
    }


def test_identity_without_requested_process_does_not_borrow_another_capability():
    value, record = fixture()
    result = identity_evidence(value, record, "pp-pelletise")
    assert result["identity_status"] == "reviewed_same_site"
    assert result["stage_capability"] == "not_established"
    assert result["capability_keys"] == []
    assert [row["source_id"] for row in result["source_evidence"]] == ["identity-site"]


def test_new_snapshot_id_is_explicitly_unlinked_even_when_name_address_and_point_match():
    value, record = fixture()
    record.update(listing_id="facility-listing:new-snapshot-row", source_sha256="c" * 64)
    result = identity_evidence(value, record, "pp-prepare")
    assert result["identity_status"] == "not_linked"
    assert result["facility_key"] is result["identity_link"] is None
    assert result["stage_capability"] == "not_established"
    assert result["capability_keys"] == result["source_evidence"] == []
    assert result["dispatch_approved"] is False


def test_empty_identity_links_do_not_use_names_or_equipment_as_identity():
    value, record = fixture()
    value.facilities[0].registry_identity_links = []
    assert identity_evidence(value, record, "pp-prepare")["identity_status"] == "not_linked"


@pytest.mark.parametrize("field", ["source_sha256", "record_sha256"])
def test_reusing_reviewed_listing_id_with_changed_hash_fails_closed(field):
    value, record = fixture()
    record[field] = "c" * 64
    with pytest.raises(GraphStoreError) as caught:
        identity_evidence(value, record, "pp-prepare")
    assert caught.value.code == "integrity"


@pytest.mark.parametrize("same_facility", [True, False])
def test_duplicate_or_ambiguous_site_links_cannot_pick_one_arbitrarily(same_facility):
    value, record = fixture()
    duplicate = deepcopy(value.facilities[0])
    if same_facility:
        value.facilities[0].registry_identity_links += duplicate.registry_identity_links
    else:
        duplicate.key = "second-site"
        value.facilities.append(duplicate)
    with pytest.raises(GraphStoreError) as caught:
        identity_evidence(value, record, "pp-prepare")
    assert caught.value.code == "integrity"


@pytest.mark.parametrize("group", ["identity", "process"])
def test_missing_identity_or_requested_process_source_is_an_integrity_failure(group):
    value, record = fixture()
    missing = "identity-site" if group == "identity" else "process-prepare"
    value.sources = [row for row in value.sources if row.key != missing]
    with pytest.raises(GraphStoreError) as caught:
        identity_evidence(value, record, "pp-prepare")
    assert caught.value.code == "integrity"


def test_process_source_for_an_unrequested_stage_is_not_needed_or_returned():
    value, record = fixture()
    value.sources = [row for row in value.sources if row.key != "process-clean-hypothesis"]
    result = identity_evidence(value, record, "pp-prepare")
    assert result["stage_capability"] == "published_site_process"
    assert "process-clean-hypothesis" not in {row["source_id"] for row in result["source_evidence"]}


@pytest.mark.parametrize("change", ["wrong_basis", "empty_identity_sources", "invalid_capability"])
def test_malformed_review_metadata_is_not_silently_accepted(change):
    value, record = fixture()
    link = value.facilities[0].registry_identity_links[0]
    if change == "wrong_basis":
        link.basis = "same_company_name"
    elif change == "empty_identity_sources":
        link.source_ids = []
    else:
        value.capabilities[0].basis = "equipment_word_found"
    with pytest.raises(GraphStoreError) as caught:
        identity_evidence(value, record, "pp-prepare")
    assert caught.value.code == "integrity"


def test_function_does_not_mutate_the_reference_or_registry_input():
    value, record = fixture()
    expected_value, expected_record = deepcopy(value), deepcopy(record)
    first = identity_evidence(value, record, "pp-prepare")
    second = identity_evidence(value, record, "pp-prepare")
    assert first == second
    assert value == expected_value
    assert record == expected_record
