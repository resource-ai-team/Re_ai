"""Exact site identities in real Neo4j, using synthetic rows and site/source fixtures.

The shipped reference is copied and monkeypatched only within each test. No real
company-to-registry identity assertion is authored or persisted outside its own
temporary namespace, and no network source or generation model is called.
"""

import hashlib
import json
import os
import sqlite3
from dataclasses import replace
from uuid import uuid4

import pytest

from resource_ai.api.schemas.facility_search import FacilitySearchRequest
from resource_ai.knowledge_graph import facility_registry as registry
from resource_ai.knowledge_graph import graph_store as store
from resource_ai.knowledge_graph import planning_reference as reference
from resource_ai.knowledge_graph.builder import build_stage
from resource_ai.knowledge_graph.facility_search import FacilitySearch
from tests.unit.test_facility_search import national_row

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_NEO4J_INTEGRATION") != "true", reason="Real Neo4j required"
)


@pytest.fixture
def identity_graph(tmp_path, monkeypatch):
    config = replace(store.settings(), namespace="resource-identity-test-" + uuid4().hex)
    rows = [
        national_row(
            FLCT_NM="가상 명부 법인 - 실제 기업 아님",
            LCTN_ROAD_NM_ADDR="가상 테스트 전용 주소 101",
            TRET_WAS_INF="PP PET PE 폐지 알루미늄",
            FLCT_EQU_NM="가상 분쇄시설",
            BUS_AREA="가상 명부에만 존재하는 비공개 전송 검사용 문자열",
        )
    ]
    path = tmp_path / "synthetic-identity-registry.json"
    path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf8")
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    records, _ = registry.load_snapshot(path, "national-json", sha)
    record = records[0]

    payload = reference.reference().model_dump(mode="json")
    facility = next(row for row in payload["facilities"] if row["key"] == "jungang-gongju")
    facility.update(
        label="가상 참조 사업장 - 실제 사업장 아님",
        address=rows[0]["LCTN_ROAD_NM_ADDR"],
        registry_identity_links=[
            {
                "listing_id": record["listing_id"],
                "registry_source_sha256": sha,
                "record_sha256": record["record_sha256"],
                "source_ids": ["jungang-materials"],
                "checked_date": "2026-10-24",
                "basis": "official_site_address_and_registry_record_review",
            }
        ],
    )
    source = next(row for row in payload["sources"] if row["key"] == "jungang-materials")
    source.update(
        url="https://example.test/synthetic-site-identity",
        publisher="가상 통합테스트 출처 - 실증 자료 아님",
        summary="합성 명부와 가상 사업장 주소의 식별 연결을 검증하는 테스트 전용 근거",
        locator="synthetic identity fixture; not an actual official record",
    )
    checked = reference.PlanningReference.model_validate(payload)
    monkeypatch.setattr(reference, "reference", lambda: checked)

    stage = sqlite3.connect(":memory:")
    try:
        build_stage(stage, [], include_curated=True)
        store.import_graph(stage, config=config)
        registry.import_registry(path, "national-json", sha, config=config)
        yield config, checked, path, sha, record
    finally:
        assert config.namespace.startswith("resource-identity-test-")
        with store.get_driver(config).session(database=config.database) as session:
            session.run(
                "MATCH (n) WHERE n.space=$space DETACH DELETE n", space=config.namespace
            ).consume()
        stage.close()


def search(config, transition="pp-prepare", **kwargs):
    return FacilitySearch(config).search(
        FacilitySearchRequest(transition_key=transition, page_size=100, **kwargs)
    )


def test_exact_reviewed_snapshot_is_read_from_neo4j_with_process_and_source_scope(identity_graph):
    config, checked, _, sha, record = identity_graph
    response = search(config)
    assert response.graph_trace["registry_source_sha256"] == sha
    row = next(
        item for item in response.registry_candidates if item["listing_id"] == record["listing_id"]
    )
    packet = row["research_packet"]
    linked = packet["evidence_links"]
    assert linked["identity_status"] == "reviewed_same_site"
    assert linked["facility_key"] == "jungang-gongju"
    assert linked["identity_link"]["record_sha256"] == record["record_sha256"]
    assert packet["stage_capability"] == linked["stage_capability"] == "published_site_process"
    assert linked["capability_keys"] == [
        cap.key
        for cap in checked.capabilities
        if cap.facility == "jungang-gongju" and cap.transition == "pp-prepare"
    ]
    source = next(
        item for item in linked["source_evidence"] if item["source_id"] == "jungang-materials"
    )
    assert source["url"] == "https://example.test/synthetic-site-identity"
    assert source["checked_date"] and "source_date" in source and source["locator"]
    assert packet["process_capability_verified"] is False
    assert linked["current_permit_scope"] is linked["current_acceptance"] is None
    assert linked["current_available_capacity_kg"] is None
    assert packet["current_acceptance"] is packet["current_permit_scope"] is None
    assert not row["dispatch_approved"] and not linked["dispatch_approved"]
    assert not response.search_scope["automatic_path_insertion"]


def test_site_identity_does_not_propagate_its_capability_to_another_process(identity_graph):
    config, checked, _, _, record = identity_graph
    supported = {cap.transition for cap in checked.capabilities if cap.facility == "jungang-gongju"}
    other = next(
        transition.key for transition in checked.transitions if transition.key not in supported
    )
    response = search(config, other)
    packet = next(
        item["research_packet"]
        for item in response.registry_candidates
        if item["listing_id"] == record["listing_id"]
    )
    identity = packet["evidence_links"]
    assert identity["identity_status"] == "reviewed_same_site"
    assert identity["transition_key"] == other
    assert packet["stage_capability"] == "not_established"
    assert identity["capability_keys"] == []
    assert [row["source_id"] for row in identity["source_evidence"]] == ["jungang-materials"]
    assert identity["dispatch_approved"] is False


def test_external_model_response_withholds_registry_text_and_identity_packet(identity_graph):
    config, _, _, _, record = identity_graph
    response = search(config, audience="external_model")
    row = next(
        item for item in response.registry_candidates if item["listing_id"] == record["listing_id"]
    )
    assert row["research_packet"] is None
    assert row["withheld"] is True and row["external_model_allowed"] is False
    assert row["dispatch_approved"] is False
    assert "evidence_links" not in json.dumps(row)
    assert record["name"] not in response.model_dump_json()
    assert record["raw_record"]["BUS_AREA"] not in response.model_dump_json()


def test_new_registry_snapshot_does_not_reuse_old_reviewed_identity(identity_graph, tmp_path):
    config, _, path, original_sha, original_record = identity_graph
    rows = json.loads(path.read_text(encoding="utf8"))
    rows[0]["CRTR_YMD"] = "2026-10-24"
    updated = tmp_path / "synthetic-new-snapshot.json"
    updated.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf8")
    sha = hashlib.sha256(updated.read_bytes()).hexdigest()
    assert sha != original_sha
    registry.import_registry(updated, "national-json", sha, config=config)
    response = search(config)
    assert response.graph_trace["registry_source_sha256"] == sha
    assert original_record["listing_id"] not in {
        row["listing_id"] for row in response.registry_candidates
    }
    assert response.registry_candidates
    for row in response.registry_candidates:
        packet = row["research_packet"]
        assert packet["stage_capability"] == "not_established"
        assert packet["evidence_links"]["identity_status"] == "not_linked"
        assert packet["evidence_links"]["facility_key"] is None
        assert packet["evidence_links"]["capability_keys"] == []
        assert row["dispatch_approved"] is False
