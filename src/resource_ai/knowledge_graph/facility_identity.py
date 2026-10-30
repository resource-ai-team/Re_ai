"""Bind a reviewed registry row to one site without inferring operating approval.

Listing identifiers belong to a source snapshot. Names, nearby coordinates and
equipment words are deliberately not inputs to identity resolution here.
"""

from resource_ai.knowledge_graph.graph_store import integrity

BASIS = "official_site_address_and_registry_record_review"
CAPABILITY_BASES = {"published_site_process", "site_role_hypothesis"}


def identity_evidence(value, record, transition_key):
    """Return only exact, curator-reviewed identity and requested-stage evidence.

    ``value`` is the audited PlanningReference and ``record`` the normalized
    registry row. A new snapshot has a different listing ID and stays unlinked.
    Reusing an ID with changed bytes or binding one row to two sites fails
    closed. This function neither mutates records nor approves a facility.
    """
    result = {
        "identity_status": "not_linked",
        "facility_key": None,
        "transition_key": transition_key,
        "stage_capability": "not_established",
        "capability_keys": [],
        "identity_link": None,
        "source_evidence": [],
        "current_permit_scope": None,
        "current_acceptance": None,
        "current_available_capacity_kg": None,
        "dispatch_approved": False,
    }
    try:
        listing_id = record["listing_id"]
        matches = []
        for facility in value.facilities:
            for link in facility.registry_identity_links:
                if link.listing_id != listing_id:
                    continue
                if (
                    link.registry_source_sha256 != record["source_sha256"]
                    or link.record_sha256 != record["record_sha256"]
                    or link.basis != BASIS
                ):
                    raise integrity("같은 명부 레코드 ID의 원본·행 해시 또는 식별 근거가 다릅니다.")
                matches.append((facility, link))
        if not matches:
            return result
        if len(matches) != 1:
            raise integrity("한 명부 레코드의 사업장 연결이 중복되거나 모호합니다.")

        facility, link = matches[0]
        capabilities = sorted(
            (
                cap
                for cap in value.capabilities
                if cap.facility == facility.key and cap.transition == transition_key
            ),
            key=lambda cap: cap.key,
        )
        if any(cap.basis not in CAPABILITY_BASES for cap in capabilities):
            raise integrity("시설의 공정 수행 근거 유형이 검수 계약과 다릅니다.")
        ids = set(link.source_ids)
        for cap in capabilities:
            ids.update(cap.source_ids)
        sources = {source.key: source for source in value.sources}
        if not link.source_ids or not ids <= sources.keys():
            raise integrity("명부·사업장 식별 또는 공정의 출처 연결이 누락됐습니다.")
        evidence = [
            {
                "source_id": key,
                "checked_date": sources[key].checked_date,
                "source_date": sources[key].source_date,
                "url": sources[key].url,
                "locator": sources[key].locator,
            }
            for key in sorted(ids)
        ]
        result.update(
            identity_status="reviewed_same_site",
            facility_key=facility.key,
            stage_capability=(
                "published_site_process"
                if any(cap.basis == "published_site_process" for cap in capabilities)
                else "site_role_hypothesis"
                if capabilities
                else "not_established"
            ),
            capability_keys=[cap.key for cap in capabilities],
            identity_link={
                "listing_id": link.listing_id,
                "registry_source_sha256": link.registry_source_sha256,
                "record_sha256": link.record_sha256,
                "source_ids": sorted(set(link.source_ids)),
                "checked_date": link.checked_date,
                "basis": link.basis,
            },
            source_evidence=evidence,
        )
        return result
    except (AttributeError, KeyError, TypeError, ValueError):
        raise integrity("시설·명부 식별 연결의 구조 또는 출처 메타데이터가 잘못됐습니다.") from None
