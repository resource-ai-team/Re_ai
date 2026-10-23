"""Discover listing candidates separately from sourced process/facility bindings."""

import base64
import binascii
import json
import math
import re
from collections import Counter

from resource_ai.api.schemas.facility_search import FacilitySearchResponse
from resource_ai.knowledge_graph import graph_store as store
from resource_ai.knowledge_graph import ontology as graph
from resource_ai.knowledge_graph.facility_codes import code_interpretations, load_codebook
from resource_ai.knowledge_graph.facility_evidence import assess_binding
from resource_ai.knowledge_graph.facility_identity import identity_evidence
from resource_ai.knowledge_graph.facility_registry import read_registry
from resource_ai.knowledge_graph.planning import load_reference
from resource_ai.knowledge_graph.planning_reference import node_id
from resource_ai.knowledge_graph.pool import cursor_error

ALGORITHM = "registry-facility-discovery-v3"
EQUIPMENT_HINTS = {
    "pet-prepare": ["압축", "선별"],
    "pet-wash": ["세척"],
    "pet-finish": ["건조", "금속분리", "금속 분리"],
    "pet-pelletise": ["압출", "펠렛", "펠릿"],
    "pp-prepare": ["파쇄", "분쇄", "선별"],
    "pe-prepare": ["파쇄", "분쇄", "선별"],
    "pp-clean": ["세척"],
    "pe-clean": ["세척"],
    "pp-pelletise": ["압출", "펠렛", "펠릿"],
    "pe-pelletise": ["압출", "펠렛", "펠릿"],
    "pp-compound": ["컴파운딩", "배합", "혼합"],
    "pe-compound": ["컴파운딩", "배합", "혼합"],
    "al-can-sheet": ["용해", "주조", "압연"],
    "paper-board": ["초지", "제지", "펄프"],
}
MATERIAL_LABELS = {
    "pet": r"(?<![a-z])pet(?![a-z])|페트|폴리에틸렌\s*테레프탈레이트",
    "pp": r"(?<![a-z])pp(?![a-z])|폴리프로필렌|polypropylene",
    "pe": r"(?<![a-z])(?:pe|hdpe|ldpe|lldpe)(?![a-z])|폴리에틸렌(?!\s*테레프탈레이트)|polyethylene(?!\s+terephthalate)",
    "paper": r"폐지(?!방)|종이|(?<![가-힣])고지(?![가-힣])|골판지|(?<![a-z])paper(?![a-z])",
    "aluminium": r"알루미늄|알미늄|(?<![a-z])alumin(?:um|ium)(?![a-z])",
}
LIMITATIONS = [
    "명부는 등록 레코드입니다. 시설 식별·소재 등급·공정 수행·현행 허가·인수·여유 용량은 별도 확인해야 합니다.",
    "폐합성수지는 넓은 분류이며 PET·PP·PE 적합 판정이 아닙니다. 해석하지 못한 폐기물 코드는 조사 대상으로 보존합니다.",
    "별표 4 코드는 검색 단서입니다. 명부의 과거 기준일과 현재 코드표의 일치·지정폐기물 여부·현행 허가 범위를 검증한 것이 아닙니다.",
    "직선거리는 시설 좌표와 사용자 입력의 참고값입니다. 도로 운송거리·운송비·탄소로 사용하지 않습니다.",
    "명부 후보는 공정 경로에 자동 삽입하지 않습니다. 공개 사례의 시설 연결과 별도로 반환합니다.",
    "외부 모델 보기에서는 명부 본문을 가리고 ID만 제공합니다. 명부 원문 전송은 활성화하지 않았습니다.",
]


def material_match(record, material, waste_code=None, *, codebook=None):
    if waste_code:
        return (
            "requested_code_not_material_validated" if waste_code in record["waste_codes"] else None
        )
    waste = record["waste_text"]
    if re.search(MATERIAL_LABELS[material], waste, re.IGNORECASE):
        return "material_label_only"
    if material in {"pet", "pp", "pe"} and re.search("합성수지|플라스틱|폐수지", waste):
        return "broad_category_only"
    if material == "aluminium" and re.search("폐금속|비철금속|금속스크랩", waste):
        return "broad_category_only"
    if record["waste_codes"]:
        book = codebook or load_codebook()
        rows = [book["records"].get(code) for code in record["waste_codes"]]
        hits = [r for r in rows if r and material in r["query_materials"]]
        if hits:
            return (
                "named_material_code_only"
                if any(r["match_kind"] == "named_material_code_only" for r in hits)
                else "code_category_only"
            )
        if any(row is None for row in rows):
            return "unmapped_codes_require_review"
    return None


def straight_line_km(origin, point):
    if point is None or origin is None:
        return None
    lat1, lon1, lat2, lon2 = map(math.radians, [*origin, *point])
    term = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    )
    return round(6371.0088 * 2 * math.asin(min(1.0, math.sqrt(max(0.0, term)))), 1)


def equipment_mentions(record, transition_key):
    return [
        term for term in EQUIPMENT_HINTS.get(transition_key, []) if term in record["equipment_text"]
    ]


def registry_matches(records, material, request, *, codebook=None):
    book = codebook or load_codebook()
    matches, counts = (
        [],
        {"non_recycling_registration": 0, "region_no_match": 0, "no_material_or_code_hit": 0},
    )
    origin = (
        (request.origin_latitude, request.origin_longitude)
        if request.origin_latitude is not None
        else None
    )
    for record in records:
        if not record["is_recycling_registration"]:
            counts["non_recycling_registration"] += 1
            continue
        if (
            request.region
            and record["address"]
            and graph.normalize_label(request.region)
            not in graph.normalize_label(record["address"])
        ):
            counts["region_no_match"] += 1
            continue
        match = material_match(record, material, request.waste_code, codebook=book)
        if match is None:
            counts["no_material_or_code_hit"] += 1
            continue
        distance = straight_line_km(origin, record["coordinates"])
        matches.append((record, match, distance))
    buckets = {
        "material_label_only": 0,
        "requested_code_not_material_validated": 0,
        "named_material_code_only": 0,
        "broad_category_only": 1,
        "code_category_only": 1,
        "unmapped_codes_require_review": 2,
    }

    def relevance(v):
        return (
            not bool(equipment_mentions(v[0], request.transition_key)),
            buckets[v[1]],
            v[2] if v[2] is not None else math.inf,
            v[0]["listing_id"],
        )

    matches.sort(
        key=lambda v: (
            (v[2] if v[2] is not None else math.inf, *relevance(v))
            if request.sort_by == "straight_line_distance"
            else relevance(v)
        )
    )
    return matches, counts


def encode_cursor(value):
    return base64.urlsafe_b64encode(graph.canonical(value).encode()).decode().rstrip("=")


def decode_cursor(value):
    try:
        row = json.loads(
            base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
        )
        if (
            not isinstance(row, dict)
            or set(row)
            != {"algorithm", "graph", "registry", "codes", "request", "namespace", "offset"}
            or row["algorithm"] != ALGORITHM
            or type(row["offset"]) is not int
            or row["offset"] < 0
            or not all(
                isinstance(row[k], str) and row[k]
                for k in ("graph", "registry", "codes", "request", "namespace")
            )
        ):
            raise ValueError()
        return row
    except (ValueError, TypeError, binascii.Error, UnicodeError):
        raise cursor_error() from None


def reference_bindings(value, transition, audience):
    facilities = {f.key: f for f in value.facilities}
    sources = {s.key: s for s in value.sources}
    items = []
    for cap in sorted(value.capabilities, key=lambda c: c.key):
        if cap.transition != transition.key:
            continue
        facility = facilities[cap.facility]
        sids = sorted(set(cap.source_ids + transition.source_ids + facility.source_ids))
        blocked = [node_id("Evidence", s) for s in sids if not sources[s].model_transfer_allowed]
        packet = {
            "facility": facility.model_dump(mode="json"),
            "capability": cap.model_dump(mode="json"),
            "assessment": assess_binding(transition, cap),
            "evidence": [
                {"evidence_id": node_id("Evidence", s), **sources[s].model_dump(mode="json")}
                for s in sids
            ],
        }
        items.append(
            {
                "capability_id": node_id("FacilityCapability", cap.key),
                "research_packet": None if blocked and audience == "external_model" else packet,
                "external_model_allowed": not blocked,
                "withheld_evidence_ids": blocked,
                "dispatch_approved": False,
            }
        )
    return items


class FacilitySearch:
    def __init__(self, config=None):
        self.config = config

    def search(self, request):
        config = self.config or store.settings()
        cursor = decode_cursor(request.cursor) if request.cursor else None
        binding = graph.digest(request.model_dump(exclude={"cursor", "page_size"}))

        def lookup(tx):
            generation, manifest = store._head(tx, config)
            value, fingerprint = load_reference(tx, config, generation, manifest)
            transition = next(
                (t for t in value.transitions if t.key == request.transition_key), None
            )
            if transition is None:
                raise store.GraphStoreError(
                    "invalid-transition", "적재된 공정 전이 key를 사용해 주세요."
                )
            material = next(s.material for s in value.states if s.key == transition.input_state)
            codebook = load_codebook()
            registry = read_registry(tx, config)
            stamp = {
                "algorithm": ALGORITHM,
                "graph": generation,
                "registry": registry["generation"] if registry else "not-imported",
                "codes": codebook["sha256"],
                "request": binding,
                "namespace": config.namespace,
            }
            if cursor:
                if cursor["namespace"] != config.namespace or cursor["request"] != binding:
                    raise cursor_error()
                if any(cursor[key] != stamp[key] for key in ("graph", "registry", "codes")):
                    raise cursor_error("stale-cursor")
            matches, counts = (
                registry_matches(registry["records"], material, request, codebook=codebook)
                if registry
                else ([], {})
            )
            offset = cursor["offset"] if cursor else 0
            if cursor and offset >= len(matches):
                raise cursor_error()
            selected = matches[offset : offset + request.page_size]
            more = offset + len(selected) < len(matches)
            items = []
            for record, match, distance in selected:
                packet = {k: v for k, v in record.items() if k != "raw_record"}
                identity = identity_evidence(value, record, transition.key)
                packet.update(
                    material_match=match,
                    code_interpretations=code_interpretations(
                        record["waste_codes"], material, codebook
                    ),
                    code_mapping_source=codebook["source"],
                    stage_capability=identity["stage_capability"],
                    evidence_links=identity,
                    equipment_mentions=equipment_mentions(record, transition.key),
                    equipment_evidence_scope="literal_mentions_only_not_whole_transition_or_material_compatibility",
                    region_match="not_requested"
                    if not request.region
                    else "label_match"
                    if record["address"]
                    else "unknown",
                    straight_line_distance_km=distance,
                    road_distance_km=None,
                    transport_cost_krw=None,
                    next_action="시설 식별·해당 공정 설비·투입 규격·현행 허가·이번 로트 인수 조건 확인",
                )
                external = request.audience == "external_model"
                items.append(
                    {
                        "listing_id": record["listing_id"],
                        "research_packet": None if external else packet,
                        "external_model_allowed": False,
                        "withheld": external,
                        "dispatch_approved": False,
                    }
                )
            refs = reference_bindings(value, transition, request.audience)
            return FacilitySearchResponse(
                status="reference_only"
                if registry is None
                else "candidates"
                if matches or refs
                else "no_candidates",
                transition_key=transition.key,
                material=material,
                reference_bindings=refs,
                registry_candidates=items,
                pagination={
                    "total_registry_candidates": len(matches),
                    "returned_count": len(items),
                    "has_more": more,
                    "next_cursor": encode_cursor({**stamp, "offset": offset + len(items)})
                    if more
                    else None,
                },
                graph_trace={
                    "generation": generation,
                    "reference_sha256": fingerprint,
                    "codebook_sha256": codebook["sha256"],
                    "registry_generation": stamp["registry"],
                    "registry_source_sha256": registry["manifest"]["source_sha256"]
                    if registry
                    else None,
                },
                search_scope={
                    "registry_imported": registry is not None,
                    "registry_record_count": len(registry["records"]) if registry else 0,
                    "filter_counts": counts,
                    "match_counts": dict(Counter(match for _, match, _ in matches)),
                    "ordering": "straight_line_then_equipment_then_material_match_then_id"
                    if request.sort_by == "straight_line_distance"
                    else "equipment_mentions_then_material_match_bucket_then_straight_line_then_id",
                    "not_feasibility_or_cost_ranking": True,
                    "code_categories_not_material_or_permit_approval": True,
                    "unmapped_codes_preserved": request.waste_code is None,
                    "location_filters_apply_to_registry_only": True,
                    "automatic_path_insertion": False,
                },
                limitations=LIMITATIONS,
            )

        try:
            return store._read(config, lookup)
        except (KeyError, TypeError, ValueError):
            raise store.integrity() from None
