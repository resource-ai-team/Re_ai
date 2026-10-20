import hashlib
import json

import pytest
from pydantic import ValidationError

from resource_ai.api.schemas.facility_search import FacilitySearchRequest
from resource_ai.knowledge_graph import facility_codes as codes
from resource_ai.knowledge_graph import facility_registry as registry
from resource_ai.knowledge_graph import facility_search as search
from resource_ai.knowledge_graph import ontology as graph
from resource_ai.knowledge_graph.facility_evidence import assess_binding
from resource_ai.knowledge_graph.planning_reference import reference


def national_row(**changes):
    row = dict.fromkeys(registry.NATIONAL_COLUMNS, "")
    row.update(
        FLCT_NM="가상 테스트 시설",
        BUS_NM="폐기물종합재활용업",
        TRET_WAS_INF="PP 폐합성수지",
        LCTN_ROAD_NM_ADDR="충남 예산군 가상로 1",
        LAT="36.7",
        LOT="126.8",
        CRTR_YMD="2025-03-01",
    )
    row.update(changes)
    return row


def normalized(index=1, **changes):
    return registry.normalize_record(national_row(**changes), index, "national-json", "a" * 64)


def write_source(tmp_path, rows=None):
    path = tmp_path / "synthetic-registry.json"
    path.write_text(json.dumps(rows or [national_row()], ensure_ascii=False), encoding="utf8")
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


def test_registry_preserves_provenance_and_does_not_invent_current_terms(tmp_path):
    path, sha = write_source(tmp_path)
    records, manifest = registry.load_snapshot(path, "national-json", sha)
    record = records[0]
    assert record["source_sha256"] == sha
    assert record["source_row"] == 1
    assert record["record_sha256"] == graph.digest(national_row())
    assert manifest["records_sha256"] == graph.digest(records)
    assert record["current_acceptance"] is record["current_permit_scope"] is None
    assert record["current_available_capacity_kg"] is None
    assert record["process_capability_verified"] is False
    assert record["model_transfer_allowed"] is False


@pytest.mark.parametrize(
    "change", [{"extra": "unsafe"}, {"BUS_NM": ""}, {"FLCT_NM": ""}, {"LAT": 37.2}]
)
def test_unsupported_source_values_fail_closed(tmp_path, change):
    path, sha = write_source(tmp_path, [national_row(**change)])
    with pytest.raises(ValueError):
        registry.load_snapshot(path, "national-json", sha)


def test_checksum_change_does_not_import_a_different_file(tmp_path):
    path, _ = write_source(tmp_path)
    with pytest.raises(ValueError, match="checksum"):
        registry.load_snapshot(path, "national-json", "a" * 64)


@pytest.mark.parametrize(
    "point", [("", ""), ("nan", "127"), ("inf", "127"), ("127", "37"), ("0", "0")]
)
def test_invalid_or_swapped_coordinates_stay_unknown(point):
    record = normalized(LAT=point[0], LOT=point[1])
    assert record["coordinates"] is None
    assert record["coordinate_quality"] == "unavailable-or-invalid"


def test_old_and_invalid_dates_are_not_replaced_by_today():
    assert normalized(CRTR_YMD="2023-07-14")["as_of"] == "2023-07-14"
    record = normalized(CRTR_YMD="unknown")
    assert record["as_of"] is None and record["as_of_raw"] == "unknown"


def test_regional_registration_date_is_not_fabricated_as_the_record_reference_date():
    row = dict.fromkeys(registry.GIMHAE_COLUMNS, "")
    row.update(업소명="가상 지역 시설", 업종="폐기물종합재활용업", 등록일자="2010-01-01")
    record = registry.normalize_record(row, 1, "gimhae-csv", "a" * 64)
    assert record["as_of"] is None and record["as_of_raw"] == ""
    assert record["permit_date_raw"] == "2010-01-01"


@pytest.mark.parametrize(
    ("text", "material", "expected"),
    [
        ("폐 PET병", "pet", "material_label_only"),
        ("폐PE 필름", "pe", "material_label_only"),
        ("HDPE", "pe", "material_label_only"),
        ("PPS", "pp", None),
        ("PET", "pe", None),
        ("폴리에틸렌테레프탈레이트", "pe", None),
        ("폐합성수지류(PVC제외)", "pp", "broad_category_only"),
        ("폐금속류", "aluminium", "broad_category_only"),
        ("폐지방", "paper", None),
        ("폐지", "paper", "material_label_only"),
        ("고지", "paper", "material_label_only"),
        ("51-03-01+51-03-99", "pet", "code_category_only"),
        ("51-28-02", "paper", "code_category_only"),
        ("51-22-01", "pet", None),
        ("51-22-01", "paper", None),
        ("51-29-01", "aluminium", None),
        ("51-29-02", "aluminium", "code_category_only"),
        ("01-01-02", "pp", "named_material_code_only"),
        ("01-01-02", "pet", None),
        ("88-88-88", "pet", "unmapped_codes_require_review"),
        ("51-03-00", "pet", "unmapped_codes_require_review"),
        ("51-22-01+88-88-88", "pet", "unmapped_codes_require_review"),
    ],
)
def test_material_labels_are_search_hints_not_substring_or_code_approvals(text, material, expected):
    assert search.material_match(normalized(TRET_WAS_INF=text), material) == expected


def test_official_code_excerpts_preserve_pages_and_designated_scope_without_permit_claims():
    book = codes.load_codebook()
    assert len(book["records"]) == 313
    assert book["records"]["51-22-01"]["label_excerpt"] == "폐콘크리트"
    assert book["records"]["51-03-01"]["page"] == 6
    result = codes.code_interpretations(["01-01-02", "51-28-02", "88-88-88"], "pp", book)
    assert result[0]["section"] == "designated"
    assert result[0]["query_scope"] == "search_hint_only"
    assert result[1]["query_scope"] == "no_configured_material_hint"
    assert result[2]["label_excerpt"] is None
    assert result[2]["query_scope"] == "unmapped_requires_review"
    assert all(r["permit_validated"] is False for r in result)
    assert all(r["historical_code_consistency_verified"] is False for r in result)


def test_code_dictionary_checksum_or_structure_failure_is_not_silently_ignored(monkeypatch):
    def corrupted(_):
        raise ValueError("changed bundle")

    monkeypatch.setattr(codes, "read_bundle", corrupted)
    with pytest.raises(search.store.GraphStoreError) as caught:
        codes.load_codebook()
    assert caught.value.code == "codebook-integrity"


def test_tampered_codebook_shape_fails_closed(monkeypatch):
    monkeypatch.setattr(codes, "read_bundle", lambda _: {"schema_version": 1, "records": []})
    with pytest.raises(search.store.GraphStoreError) as caught:
        codes.load_codebook()
    assert caught.value.code == "codebook-integrity"


def test_requested_code_is_exact_and_not_claimed_as_material_validation():
    record = normalized(TRET_WAS_INF="51-03-01+51-03-99")
    assert (
        search.material_match(record, "pp", "51-03-01") == "requested_code_not_material_validated"
    )
    assert search.material_match(record, "pp", "51-03-02") is None


def test_equipment_mentions_change_by_stage_without_becoming_capability_approval():
    grinder = normalized(FLCT_EQU_NM="분쇄시설")
    washer = normalized(2, FLCT_EQU_NM="세척시설")
    for stage, expected_first in [("pp-prepare", 1), ("pp-clean", 2)]:
        hits, _ = search.registry_matches(
            [grinder, washer], "pp", FacilitySearchRequest(transition_key=stage)
        )
        assert hits[0][0]["source_row"] == expected_first
        assert len(hits) == 2
        assert all(record["process_capability_verified"] is False for record, _, _ in hits)


def test_collection_carrier_does_not_become_a_recycling_process_facility():
    rows = [
        normalized(),
        normalized(2, BUS_NM="폐기물수집및운반업"),
        normalized(3, BUS_NM="폐기물중간처분업"),
    ]
    hits, counts = search.registry_matches(
        rows, "pp", FacilitySearchRequest(transition_key="pp-prepare")
    )
    assert [r[0]["source_row"] for r in hits] == [1]
    assert counts["non_recycling_registration"] == 2


def test_unknown_locations_are_preserved_and_distance_is_not_a_road_estimate():
    rows = [normalized(LCTN_ROAD_NM_ADDR="", LAT="", LOT=""), normalized(2)]
    request = FacilitySearchRequest(
        transition_key="pp-prepare", region="충남", origin_latitude=36.7, origin_longitude=126.8
    )
    hits, _ = search.registry_matches(rows, "pp", request)
    assert len(hits) == 2
    assert hits[0][2] == 0.0 and hits[1][2] is None
    assert search.straight_line_km((0, 0), (1, 0)) == 111.2


@pytest.mark.parametrize("basis", ["published_site_process", "site_role_hypothesis", None])
def test_site_evidence_never_satisfies_acceptance_permit_or_capacity(basis):
    value = reference()
    transition, cap = value.transitions[0], value.capabilities[0]
    if basis:
        cap = cap.model_copy(update={"basis": basis})
    else:
        cap = None
    result = assess_binding(transition, cap)
    assert result["ready_for_dispatch"] is False
    assert result["specific_lot_specifications"] == "needs_verification"
    assert (
        result["current_permit_scope"]
        == result["current_acceptance"]
        == result["available_capacity"]
        == "needs_verification"
    )
    assert (result["published_site_process"] == "source_support") == (
        basis == "published_site_process"
    )


@pytest.mark.parametrize(
    "payload",
    [
        {"transition_key": ""},
        {"transition_key": "MATCH (n) RETURN n"},
        {"waste_code": "51-03"},
        {"page_size": 101},
        {"origin_latitude": 37},
        {"origin_longitude": 127},
        {"origin_latitude": float("nan"), "origin_longitude": 127},
        {"audience": "external_model", "region": "private-factory"},
        {"audience": "external_model", "origin_latitude": 37, "origin_longitude": 127},
        {"top_k": 1},
    ],
)
def test_search_contract_rejects_ambiguous_or_unexportable_constraints(payload):
    with pytest.raises(ValidationError):
        FacilitySearchRequest.model_validate({"transition_key": "pp-prepare", **payload})


@pytest.mark.parametrize("token", ["%%%", "e30", "bnVsbA", "W10"])
def test_bad_cursor_is_a_public_bad_request(token):
    with pytest.raises(search.store.GraphStoreError) as caught:
        search.decode_cursor(token)
    assert caught.value.code == "invalid-cursor"


def test_rebuild_flag_reaches_graph_import(monkeypatch):
    import sys

    from resource_ai.knowledge_graph import cli

    called = []
    monkeypatch.setattr(cli, "build_reference", lambda path, **kwargs: called.append(kwargs) or {})
    monkeypatch.setattr(sys, "argv", ["resource-kg", "import", "--rebuild"])
    cli.main()
    assert called == [{"rebuild": True}]
