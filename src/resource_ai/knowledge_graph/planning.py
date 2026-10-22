"""Enumerate typed state transitions and explicit facility bindings from Neo4j.

This is a graph-compatible research pool. It does not infer W2RKG comma order,
approve material specifications, validate permits, calculate or rank routes.
"""

import base64
import binascii
import json
from dataclasses import dataclass

from resource_ai.api.schemas.kg_planning import (
    PathPlanningRequest,
    PathPlanningResponse,
    PlanningPagination,
    ProcessPlan,
)
from resource_ai.knowledge_graph import graph_store as store
from resource_ai.knowledge_graph import ontology as graph
from resource_ai.knowledge_graph.facility_evidence import assess_binding
from resource_ai.knowledge_graph.planning_reference import (
    GROUPS,
    PlanningReference,
    compile_reference,
    node_id,
)
from resource_ai.knowledge_graph.pool import cursor_error, unknown
from resource_ai.knowledge_graph.query import resolve_material

ALGORITHM = "typed-state-facility-dfs-v1"
MAX_EXPANSIONS = 10000
MAX_PLANS = 2000
LIMITATIONS = [
    "출처가 있는 상태 전이와 시설 역할을 연결한 조사 후보이며 실제 운영 경로나 인수 승인이 아닙니다.",
    "W2RKG의 쉼표 목록을 공정 순서로 해석하지 않습니다. 별도로 검수한 공정 전이만 연결합니다.",
    "사업장 역할로 연결한 시설은 가설입니다. 해당 설비·허가·원료 규격·현재 수용 여부를 확인해야 합니다.",
    "입력 형태가 같아도 수분·순도·등급 등 상태 조건 충족을 보장하지 않습니다. 회사 간 인계도 미확인입니다.",
    "국내 7개 시설의 제한된 공개 사례입니다. 누락 후보가 있으며 거리·가격·탄소·법규는 평가하지 않습니다.",
    "검색 한도 안에서 결정적 순서로 열거합니다. 비용·탄소 순위나 최종 Top K가 아닙니다.",
]
FORM_ALIASES = {
    "bottle": ["병", "pet병", "페트병"],
    "bale": ["압축베일", "베일"],
    "flake": ["플레이크", "플레이크형"],
    "scrap": ["스크랩", "잔재", "폐스크랩"],
    "rigid": ["경질", "경질플라스틱"],
    "film": ["필름"],
    "shredded": ["파쇄품", "파쇄물"],
    "pellet": ["펠렛", "펠릿"],
    "can": ["캔", "음료캔", "사용후음료캔", "ubc"],
    "cardboard": ["골판지", "폐골판지"],
    "recovered_paper": ["폐지", "고지"],
}


def normalize_form(value):
    if unknown(value):
        return None
    normalized = graph.normalize_label(value)
    return next(
        (key for key, aliases in FORM_ALIASES.items() if normalized in [key, *aliases]),
        normalized,
    )


def load_reference(tx, config, generation, manifest):
    """Read and verify actual graph records and bindings, not a local runtime fallback."""
    if manifest["meta"].get("includes_curated") != "true":
        raise store.GraphStoreError(
            "planning-not-imported", "공정·시설 참조 자료를 포함해 적재해 주세요."
        )
    _, fingerprint, expected, expected_edges = compile_reference()
    params = {"space": config.namespace, "generation": generation}
    actual = {}
    unique_kinds = [v for v in GROUPS.values() if v not in {"Source", "Evidence"}]
    for row in tx.run(
        """MATCH (n:ReSourceNode {space:$space,generation:$generation})
        WHERE n.planning_key IS NOT NULL OR n.id IN $ids OR n.kind IN $kinds
        RETURN properties(n) AS fields,labels(n) AS labels""",
        **params,
        ids=sorted(expected),
        kinds=unique_kinds,
    ):
        fields = dict(row["fields"])
        identifier = fields["id"]
        if identifier not in expected or identifier in actual:
            raise store.integrity()
        node = expected[identifier]
        canonical = store._node_fields(
            identifier,
            node["kind"],
            node["label"],
            graph.canonical(node["properties"]),
            config,
            generation,
            [],
        )
        if fields != canonical or set(row["labels"]) != {"ReSourceNode", node["kind"]}:
            raise store.integrity()
        actual[identifier] = json.loads(fields["properties_json"])["record"]
    if set(actual) != set(expected):
        raise store.integrity()
    found = set()
    for row in tx.run(
        """MATCH (n:ReSourceNode {space:$space,generation:$generation})-[r]->(m)
        WHERE n.id IN $ids
        RETURN n.id AS start,m.id AS end,m.kind AS end_kind,m.space AS space,
        m.generation AS generation,labels(m) AS target_labels,properties(m) AS target_fields,
        type(r) AS kind,properties(r) AS fields""",
        **params,
        ids=sorted(expected),
    ):
        triple = (row["kind"], row["start"], row["end"])
        props = {"basis": "source-backed-planning-reference-v1", "dispatch_approved": False}
        fields = {
            "id": graph.identity("edge", list(triple)),
            "properties_json": graph.canonical(props),
        }
        if (
            triple not in expected_edges
            or triple in found
            or dict(row["fields"]) != fields
            or row["space"] != config.namespace
            or row["generation"] != generation
            or row["end_kind"]
            != (expected[row["end"]]["kind"] if row["end"] in expected else "Material")
            or set(row["target_labels"]) != {"ReSourceNode", row["end_kind"]}
        ):
            raise store.integrity()
        if row["end"] not in expected:
            key = row["end"].removeprefix("material:")
            spec = graph.ontology()["materials"][key]
            material_fields = store._node_fields(
                row["end"],
                "Material",
                spec["label"],
                graph.canonical({"canonical_id": key, "search_scope_only": True}),
                config,
                generation,
                manifest["aliases"],
            )
            if dict(row["target_fields"]) != material_fields:
                raise store.integrity()
        found.add(triple)
    if found != expected_edges:
        raise store.integrity()
    # Build from the records read in this transaction, after exact binding validation.
    value = PlanningReference.model_validate(
        {
            "version": "planning-reference-v1",
            "scope": "source-backed-research-transitions; not validated operating routes",
            **{
                group: [actual[i] for i, n in expected.items() if n["kind"] == kind]
                for group, kind in GROUPS.items()
            },
        }
    )
    return value, fingerprint


@dataclass
class Enumeration:
    paths: list
    expansions: int = 0
    budget_exhausted: bool = False
    depth_limited: bool = False


def enumerate_paths(value, material, input_form, goal_state, max_steps):
    """Deterministic bounded DFS; outputs can be intermediate states, not only sinks."""
    states = {s.key: s for s in value.states}
    adjacency, bindings = {}, {}
    for transition in sorted(value.transitions, key=lambda t: t.key):
        adjacency.setdefault(transition.input_state, []).append(transition)
    for capability in sorted(value.capabilities, key=lambda c: c.key):
        bindings.setdefault(capability.transition, []).append(capability)
    starts = sorted(
        s.key
        for s in value.states
        if s.material == material
        and s.is_entry
        and (input_form is None or input_form in s.input_forms)
    )
    result = Enumeration([])

    def walk(current, path, visited):
        if result.budget_exhausted:
            return
        if path and (current == goal_state if goal_state else states[current].is_output):
            result.paths.append(tuple(path))
            if len(result.paths) >= MAX_PLANS:
                result.budget_exhausted = True
                return
            if goal_state:
                return
        following = [t for t in adjacency.get(current, []) if t.output_state not in visited]
        if len(path) >= max_steps:
            result.depth_limited |= bool(following)
            return
        for transition in following:
            for capability in bindings.get(transition.key, [None]):
                if result.expansions >= MAX_EXPANSIONS:
                    result.budget_exhausted = True
                    return
                result.expansions += 1
                walk(
                    transition.output_state,
                    [*path, (transition, capability)],
                    visited | {transition.output_state},
                )
                if result.budget_exhausted:
                    return

    for start in starts:
        walk(start, [], {start})
        if result.budget_exhausted:
            break
    return result, starts


def make_plan(value, path, *, audience, assumed_form):
    identity = [(t.key, c.key if c else None) for t, c in path]
    pid = graph.identity("plan", identity)
    facilities = {f.key: f for f in value.facilities}
    organisations = {o.key: o for o in value.organisations}
    states = {s.key: s for s in value.states}
    stages, legs, checks, sources = [], [], [], set()

    def record(row, kind):
        sources.update(row.source_ids)
        return {"node_id": node_id(kind, row.key), **row.model_dump(mode="json")}

    for index, (transition, capability) in enumerate(path, 1):
        sid = f"{pid}:stage:{index}"
        facility = facilities[capability.facility] if capability else None
        stage = {
            "stage_id": sid,
            "transition": record(transition, "ProcessTransition"),
            "input_state": record(states[transition.input_state], "MaterialState"),
            "output_state": record(states[transition.output_state], "MaterialState"),
            "capability": record(capability, "FacilityCapability") if capability else None,
            "facility": record(facility, "Facility") if facility else None,
            "organisation": record(organisations[facility.organisation], "Organisation")
            if facility
            else None,
            "facility_binding": capability.basis if capability else "missing_facility",
            "facility_assessment": assess_binding(transition, capability),
            "facility_search_request": {"transition_key": transition.key},
            "suitability": "not_assessed",
            "input_state_specifications_verified": False,
        }
        stages.append(stage)
        for question in [
            *transition.conditions,
            *transition.risk_checks,
            *(capability.conditions if capability else []),
        ]:
            checks.append(
                {
                    "target_id": sid,
                    "question": question,
                    "state": "needs_verification",
                    "origin": "curator-authored-check",
                    "source_ids": sorted(
                        set(transition.source_ids + (capability.source_ids if capability else []))
                    ),
                }
            )
        checks.append(
            {
                "target_id": sid,
                "question": "이 로트의 규격·해당 시설 허가 범위·현재 인수·여유 용량·견적을 확인했습니까?",
                "state": "needs_verification",
                "origin": "generated-verification-question",
                "source_ids": [],
            }
        )
        if index > 1:
            previous = stages[-2]["facility"]
            same = previous and facility and previous["key"] == facility.key
            leg_id = f"{pid}:leg:{index - 1}"
            legs.append(
                {
                    "leg_id": leg_id,
                    "from_stage_id": stages[-2]["stage_id"],
                    "to_stage_id": sid,
                    "kind": "within_site_candidate" if same else "external_handover_candidate",
                    "handover_confirmed": False,
                    "distance_km": None,
                    "transport_cost_krw": None,
                    "transport_kg_co2e": None,
                }
            )
            checks.append(
                {
                    "target_id": leg_id,
                    "question": "앞 단계 산출물이 다음 단계의 순도·수분·등급·인수 규격을 만족합니까? 이동·전처리가 더 필요합니까?",
                    "state": "needs_verification",
                    "origin": "generated-verification-question",
                    "source_ids": [],
                }
            )
    evidence = [s for s in value.sources if s.key in sources]
    blocked = sorted(node_id("Evidence", s.key) for s in evidence if not s.model_transfer_allowed)
    packet = {
        "sequence_basis": "explicit-curated-state-transitions",
        "input_form_assumed": assumed_form,
        "input_state_specifications_verified": False,
        "stages": stages,
        "handoffs": legs,
        "checks": checks,
        "evidence": [
            {"evidence_id": node_id("Evidence", s.key), **s.model_dump(mode="json")}
            for s in evidence
        ],
        "origin_transport": {"state": "not_assessed", "distance_km": None},
        "legal": {"state": "not_assessed"},
        "cost": {"state": "not_evaluated", "total_krw": None},
        "carbon": {"state": "not_evaluated", "total_kg_co2e": None},
        "not_established": [
            "lot_state_specs",
            "current_permit_scope",
            "current_acceptance",
            "current_available_capacity",
            "origin_and_handoff_transport",
            "mass_balance_and_coinputs",
            "cost",
            "carbon",
            "recommendation",
        ],
    }
    return ProcessPlan(
        plan_id=pid,
        research_packet=None if audience == "external_model" and blocked else packet,
        external_model_allowed=not blocked,
        withheld_evidence_ids=blocked,
    )


def encode_cursor(value):
    return base64.urlsafe_b64encode(graph.canonical(value).encode()).decode().rstrip("=")


def decode_cursor(token):
    try:
        value = json.loads(
            base64.b64decode(token + "=" * (-len(token) % 4), altchars=b"-_", validate=True)
        )
        if (
            not isinstance(value, dict)
            or set(value) != {"v", "generation", "namespace", "request", "reference", "offset"}
            or value["v"] != ALGORITHM
            or type(value["offset"]) is not int
            or value["offset"] < 0
            or not all(
                isinstance(value[k], str) and value[k]
                for k in ("generation", "namespace", "request", "reference")
            )
        ):
            raise ValueError()
        return value
    except (ValueError, TypeError, binascii.Error, UnicodeError):
        raise cursor_error() from None


class ProcessPlanner:
    def __init__(self, config=None):
        self.config = config

    def search(self, request: PathPlanningRequest):
        material = resolve_material(request.material)
        form = normalize_form(request.input_form)
        missing = [
            k
            for k in ("material", "input_form", "color", "contamination", "quantity_kg", "region")
            if getattr(request, k) is None
            or isinstance(getattr(request, k), str)
            and unknown(getattr(request, k))
        ]
        base = {
            "normalized_material": material,
            "input_context": {}
            if request.audience == "external_model"
            else request.model_dump(exclude={"cursor", "page_size", "audience"}),
            "missing_fields": missing,
            "limitations": LIMITATIONS,
        }
        if unknown(request.material):
            return PathPlanningResponse(status="needs_input", **base)
        if material is None:
            return PathPlanningResponse(status="unsupported_material", **base)
        cursor = decode_cursor(request.cursor) if request.cursor else None
        config = self.config or store.settings()
        binding_input = request.model_dump(exclude={"cursor", "page_size"})
        if request.audience == "external_model":
            # Private facts do not affect this enumeration. Do not expose even a
            # guessable digest of them in an external-model pagination token.
            binding_input = request.model_dump(
                include={"material", "input_form", "goal_state", "max_steps", "audience"}
            )
        request_hash = graph.digest(
            {
                "request": binding_input,
                "algorithm": ALGORITHM,
                "max_expansions": MAX_EXPANSIONS,
                "max_plans": MAX_PLANS,
            }
        )

        def lookup(tx):
            generation, manifest = store._head(tx, config)
            value, fingerprint = load_reference(tx, config, generation, manifest)
            stamp = {
                "v": ALGORITHM,
                "generation": generation,
                "namespace": config.namespace,
                "request": request_hash,
                "reference": fingerprint,
            }
            if cursor:
                if cursor["generation"] != generation or cursor["reference"] != fingerprint:
                    raise cursor_error("stale-cursor")
                if any(cursor[k] != v for k, v in stamp.items()):
                    raise cursor_error()
            if request.goal_state and not any(
                s.key == request.goal_state and s.material == material for s in value.states
            ):
                raise store.GraphStoreError(
                    "invalid-goal", "같은 소재의 상태 key를 goal_state로 입력해 주세요."
                )
            result, starts = enumerate_paths(
                value, material, form, request.goal_state, request.max_steps
            )
            offset = cursor["offset"] if cursor else 0
            if cursor and offset >= len(result.paths):
                raise cursor_error()
            selected = result.paths[offset : offset + request.page_size]
            more = offset + len(selected) < len(result.paths)
            items = [
                make_plan(value, p, audience=request.audience, assumed_form=form is None)
                for p in selected
            ]
            return PathPlanningResponse(
                status="plans" if result.paths else "no_plans",
                **base,
                items=items,
                pagination=PlanningPagination(
                    enumerated_plans=len(result.paths),
                    returned_count=len(items),
                    has_more=more,
                    next_cursor=encode_cursor({**stamp, "offset": offset + len(items)})
                    if more
                    else None,
                    complete_in_this_response=not cursor and not more,
                ),
                search_scope={
                    "entry_state_keys": starts,
                    "normalized_form": form,
                    "entry_state_not_covered": not starts,
                    "max_steps": request.max_steps,
                    "max_expansions": MAX_EXPANSIONS,
                    "max_plans": MAX_PLANS,
                    "expansions": result.expansions,
                    "budget_exhausted": result.budget_exhausted,
                    "depth_limited": result.depth_limited,
                    "all_paths_enumerated": not result.budget_exhausted
                    and not result.depth_limited,
                    "scope": "curated-transitions-and-explicit-capabilities-only",
                    "ranking_applied": False,
                },
                graph_trace={
                    "generation": generation,
                    "graph_sha256": manifest["meta"]["graph_sha256"],
                    "ontology_id": graph.ontology()["id"],
                    "reference_sha256": fingerprint,
                    "reference_version": value.version,
                    "algorithm": ALGORITHM,
                },
            )

        try:
            return store._read(config, lookup)
        except (KeyError, TypeError, ValueError):
            raise store.integrity() from None
