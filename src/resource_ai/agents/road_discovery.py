"""Road measurements for an explicitly bounded subset, never nationwide ranking."""

from pydantic import ValidationError

from resource_ai.api.schemas.road_routing import RoadLeg, RoadPoint, RoadRoutesRequest
from resource_ai.knowledge_graph.ontology import identity


def enrich_road_distances(result, options, service):
    listings = {}
    for search in result.facility_searches:
        for row in search["registry_candidates"]:
            listings.setdefault(row["listing_id"], row)
    selected, invalid = {}, set()
    origin = RoadPoint(latitude=result.origin.latitude, longitude=result.origin.longitude)
    for listing_id, row in listings.items():
        point = (row.get("research_packet") or {}).get("coordinates")
        try:
            if not isinstance(point, (tuple, list)) or len(point) != 2:
                raise ValueError("Missing location")
            destination = RoadPoint(latitude=point[0], longitude=point[1])
        except (ValidationError, ValueError):
            invalid.add(listing_id)
            continue
        selected[listing_id] = destination
    # Selection must not depend on which process first returned the listing.
    ordered = sorted(
        selected,
        key=lambda key: (
            (listings[key].get("research_packet") or {}).get("straight_line_distance_km")
            if (listings[key].get("research_packet") or {}).get("straight_line_distance_km")
            is not None
            else float("inf"),
            key,
        ),
    )[: options.max_destinations]
    legs = [
        RoadLeg(leg_id=identity("road-leg", key), origin=origin, destination=selected[key])
        for key in ordered
    ]
    batch = (
        service.run(
            RoadRoutesRequest(
                legs=legs, include_geometry=options.include_geometry, mode="independent"
            )
        )
        if legs
        else None
    )
    by_listing = dict(zip(ordered, batch.routes)) if batch else {}
    for search in result.facility_searches:
        for row in search["registry_candidates"]:
            packet = row.get("research_packet")
            if packet is None:
                continue
            key = row["listing_id"]
            measurement = by_listing.get(key) or service.unavailable_measurement(
                identity("road-leg", key),
                "missing_coordinates" if key in invalid else "not_selected",
                wanted=options.include_geometry,
            )
            packet["road_route"] = measurement.model_dump(mode="json")
            packet["road_distance_km"] = measurement.road_distance_km
            packet["road_duration_seconds"] = measurement.duration_seconds
        search["registry_candidates"].sort(
            key=lambda row: (
                (row.get("research_packet") or {}).get("road_distance_km") is None,
                (row.get("research_packet") or {}).get("road_distance_km") or 0,
                (row.get("research_packet") or {}).get("straight_line_distance_km")
                if (row.get("research_packet") or {}).get("straight_line_distance_km") is not None
                else float("inf"),
                row["listing_id"],
            )
        )
        search["distance_scope"] = "origin_to_listing_road_on_selected_subset_not_process_chain"
        search["road_ranking_scope"] = "selected_collected_listings_only_not_entire_registry"
    found = sum(r.status == "found" for r in by_listing.values())
    result.road_routing = {
        "provider": service.provider,
        "status": batch.status if batch else "unavailable",
        "coverage": {
            "unique_collected_listings": len(listings),
            "selected_for_road_query": len(legs),
            "road_found": found,
            "missing_coordinates": len(invalid),
            "not_selected": len(selected) - len(ordered),
            "external_calls": batch.coverage["external_calls"] if batch else 0,
            "global_road_nearest_verified": False,
        },
        "selection_basis": "straight_line_prefilter_of_collected_pages_then_bounded_road_lookup",
        "all_selected_road_queries_succeeded": bool(batch) and batch.all_legs_routed,
        "not_a_validated_transport_chain": True,
    }
    result.limitations = [
        line
        for line in result.limitations
        if "유료 지도 API" not in line and "거리는 배출" not in line
    ] + [
        "직선거리 후보 중 한정한 시설만 지도 API로 도로 조회합니다. 전국 도로거리 최단 후보를 보장하지 않습니다.",
        "API 미설정/실패/미조회 도로거리는 null입니다. 직선거리를 도로거리로 대체하지 않습니다.",
        "발생지→각 시설 참고 경로이며 실제 공정 A→B→C 구간은 road-routes의 chain 모드로 별도 조회해야 합니다.",
        "법령 검색·생성 모델·운임 견적·최종 추천은 수행하지 않습니다.",
    ]
    return result
