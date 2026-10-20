"""Complete, snapshot-bound candidate enumeration and evidence-scoped research packets.

This read-only layer does not apply suitability rules, invent process sequences,
or select the final K. Missing facts and mismatching template forms stay visible.
"""

import base64
import binascii
import json

from resource_ai.api.schemas.kg_pool import (
    CandidatePoolRequest,
    CandidatePoolResponse,
    PoolCandidate,
    PoolPagination,
)
from resource_ai.knowledge_graph import graph_store as store
from resource_ai.knowledge_graph import ontology as graph
from resource_ai.knowledge_graph.dataset import allowed_cards
from resource_ai.knowledge_graph.query import LIMITATIONS, resolve_material

UNKNOWN = {"unknown", "미확인", "모름", "미상", "불명", "n/a"}
KINDS = {0: "process", 1: "scoped_claim", 2: "literature"}
POOL_QUERY = """
CALL () {
    MATCH (c:ReSourceNode:RouteSnapshot {space:$space,generation:$generation})
          -[:FOR_MATERIAL]->(:ReSourceNode:Material
          {space:$space,generation:$generation,id:$material})
    RETURN DISTINCT 0 AS bucket,c.id AS id,c.properties_json AS props
    UNION ALL
    MATCH (c:ReSourceNode:ScopedClaim {space:$space,generation:$generation})
    WHERE c.id IN $background_ids OR EXISTS {
        MATCH (c)-[:FOR_MATERIAL]->(:ReSourceNode:Material
        {space:$space,generation:$generation,id:$material})
    }
    RETURN DISTINCT 1 AS bucket,c.id AS id,c.properties_json AS props
    UNION ALL
    MATCH (c:ReSourceNode:TransformationClaim {space:$space,generation:$generation})
          -[:INPUT_LABEL]->(w:ReSourceNode:WasteDescription
          {space:$space,generation:$generation})-[:MENTIONS_MATERIAL]->
          (:ReSourceNode:Material {space:$space,generation:$generation,id:$material})
    RETURN DISTINCT 2 AS bucket,c.id AS id,c.properties_json AS props
}
"""


def unknown(value):
    return value is None or graph.normalize_label(value) in UNKNOWN


def cursor_error(code="invalid-cursor"):
    message = (
        "그래프 버전이 바뀌었습니다. 커서 없이 첫 페이지부터 다시 조회해 주세요."
        if code == "stale-cursor"
        else "커서가 유효하지 않거나 조회 조건이 바뀌었습니다. 첫 페이지부터 다시 조회해 주세요."
    )
    return store.GraphStoreError(code, message)


def encode_cursor(value):
    return base64.urlsafe_b64encode(graph.canonical(value).encode()).decode().rstrip("=")


def decode_cursor(token):
    try:
        value = json.loads(
            base64.b64decode(token + "=" * (-len(token) % 4), altchars=b"-_", validate=True)
        )
        if (
            not isinstance(value, dict)
            or set(value) != {"v", "generation", "namespace", "request", "bucket", "id"}
            or type(value["v"]) is not int
            or value["v"] != 1
            or type(value["bucket"]) is not int
            or value["bucket"] not in KINDS
            or not all(
                isinstance(value[key], str) and value[key]
                for key in ("generation", "namespace", "request", "id")
            )
        ):
            raise ValueError("Invalid cursor shape")
        return value
    except (ValueError, TypeError, binascii.Error, UnicodeError):
        raise cursor_error() from None


def load_evidence(tx, params, identifiers):
    found = {}
    for row in tx.run(
        """MATCH (e:ReSourceNode:Evidence {space:$space,generation:$generation})
        WHERE e.id IN $ids
        MATCH (e)-[:FROM_SOURCE]->(s:ReSourceNode:Source)
        RETURN e.id AS id,e.properties_json AS props,s.id AS source,
               s.space AS source_space,s.generation AS source_generation,
               s.properties_json AS source_props ORDER BY e.id""",
        **params,
        ids=sorted(identifiers),
    ):
        props, source = json.loads(row["props"]), json.loads(row["source_props"])
        graph.validate_properties("Evidence", props, graph.ontology())
        graph.validate_properties("Source", source, graph.ontology())
        if (
            row["id"] in found
            or row["source_space"] != params["space"]
            or row["source_generation"] != params["generation"]
        ):
            raise store.integrity()
        found[row["id"]] = {
            "evidence_id": row["id"],
            "properties": props,
            "source_id": row["source"],
            "source": source,
        }
    if set(found) != set(identifiers):
        raise store.integrity()
    return list(found.values())


class PageEvidence:
    """Fetch shared provenance once per page rather than once per candidate/step."""

    def __init__(self, tx, params, rows):
        owners = set()
        for row in rows:
            identifier, props = row["id"], json.loads(row["props"])
            owners.add(identifier)
            questions = []
            if row["bucket"] == 0:
                route = props["payload"]
                questions = route["input_conditions"]
                for name, kind in (
                    ("nodes", "StepOccurrence"),
                    ("edges", "LinkOccurrence"),
                    ("risks", "Risk"),
                ):
                    owners.update(
                        graph.identity(kind, [identifier, item["id"]]) for item in route[name]
                    )
            elif row["bucket"] == 1:
                questions = [
                    q if isinstance(q, str) else graph.canonical(q)
                    for q in props["raw_record"].get("evidence_requirements", [])
                ]
            owners.update(
                graph.identity("condition", [identifier, i, text])
                for i, text in enumerate(questions)
            )
        self.by_owner = {}
        if owners:
            for row in tx.run(
                """MATCH (n:ReSourceNode {space:$space,generation:$generation})
                WHERE n.id IN $owners
                OPTIONAL MATCH (n)-[:SUPPORTED_BY]->(e:ReSourceNode)
                RETURN n.id AS owner,collect({id:e.id,kind:e.kind,space:e.space,
                    generation:e.generation}) AS evidence""",
                **params,
                owners=sorted(owners),
            ):
                values = [v for v in row["evidence"] if v["id"]]
                if any(
                    v["kind"] != "Evidence"
                    or v["space"] != params["space"]
                    or v["generation"] != params["generation"]
                    for v in values
                ):
                    raise store.integrity()
                self.by_owner[row["owner"]] = {v["id"] for v in values}
        if set(self.by_owner) != owners:
            raise store.integrity()
        identifiers = set().union(*self.by_owner.values()) if owners else set()
        self.records = {e["evidence_id"]: e for e in load_evidence(tx, params, identifiers)}

    def direct(self, identifier):
        if identifier not in self.by_owner:
            raise store.integrity()
        return set(self.by_owner[identifier])

    def evidence(self, identifiers):
        if not identifiers <= self.records.keys():
            raise store.integrity()
        return [self.records[key] for key in sorted(identifiers)]


def load_conditions(tx, params, identifier, expected_texts, provenance):
    conditions = []
    for row in tx.run(
        """MATCH (:ReSourceNode {space:$space,generation:$generation,id:$id})
        -[:REQUIRES]->(c:ReSourceNode:Condition)
        RETURN c.id AS id,c.properties_json AS props,c.space AS space,
        c.generation AS generation ORDER BY c.id""",
        **params,
        id=identifier,
    ):
        props = json.loads(row["props"])
        graph.validate_properties("Condition", props, graph.ontology())
        if row["space"] != params["space"] or row["generation"] != params["generation"]:
            raise store.integrity()
        conditions.append(
            {
                "condition_id": row["id"],
                "question": props["text"],
                "origin": props["origin"],
                "validation": props["validation"],
                "state": "unassessed",
                "machine_rule_ready": False,
                "evidence_ids": sorted(provenance.direct(row["id"])),
            }
        )
    expected = {
        graph.identity("condition", [identifier, i, text]): text
        for i, text in enumerate(expected_texts)
    }
    if {c["condition_id"]: c["question"] for c in conditions} != expected or any(
        not c["evidence_ids"] for c in conditions
    ):
        raise store.integrity()
    return conditions


def validate_route(tx, params, identifier, props, provenance):
    """Check the immutable snapshot AND the Neo4j occurrence/target relationships."""
    graph.validate_properties("RouteSnapshot", props, graph.ontology())
    route = props["payload"]
    content = {
        "material": props["material"],
        "input_form": props["input_form"],
        "route": route,
        "evidence_map": props["evidence_map"],
        "evidence_fingerprint": props["evidence_fingerprint"],
    }
    if (
        graph.digest(content) != props["snapshot_hash"]
        or identifier != "route:" + props["snapshot_hash"][:32]
    ):
        raise store.integrity()
    expected = {}
    for name, kind, relation in (
        ("nodes", "StepOccurrence", "HAS_STEP"),
        ("edges", "LinkOccurrence", "HAS_LINK"),
        ("risks", "Risk", "HAS_RISK"),
    ):
        for item in route[name]:
            node_id = graph.identity(kind, [identifier, item["id"]])
            targets = set()
            if name == "edges":
                targets = {
                    (rel, graph.identity("StepOccurrence", [identifier, item[field]]))
                    for rel, field in (("FROM_STEP", "from"), ("TO_STEP", "to"))
                }
            if name == "risks":
                target_kind = {"node": "StepOccurrence", "edge": "LinkOccurrence"}[
                    item["target_type"]
                ]
                targets = {
                    ("TARGETS", graph.identity(target_kind, [identifier, item["target_id"]]))
                }
            expected[node_id] = (kind, relation, item, targets)
    actual = set()
    for row in tx.run(
        """MATCH (:ReSourceNode:RouteSnapshot
        {space:$space,generation:$generation,id:$id})
        -[r:HAS_STEP|HAS_LINK|HAS_RISK]->(n:ReSourceNode)
        OPTIONAL MATCH (n)-[t:FROM_STEP|TO_STEP|TARGETS]->(target:ReSourceNode)
        RETURN n.id AS id,n.kind AS kind,n.space AS space,n.generation AS generation,
        n.properties_json AS props,type(r) AS relation,
        collect({relation:type(t),id:target.id,space:target.space,
                 generation:target.generation}) AS targets""",
        **params,
        id=identifier,
    ):
        if row["id"] not in expected or row["id"] in actual:
            raise store.integrity()
        kind, relation, item, targets = expected[row["id"]]
        node = json.loads(row["props"])
        graph.validate_properties(kind, node, graph.ontology())
        if (
            row["space"] != params["space"]
            or row["generation"] != params["generation"]
            or row["kind"] != kind
            or row["relation"] != relation
            or node["route_snapshot_id"] != identifier
            or node["payload"] != item
            or {(t["relation"], t["id"]) for t in row["targets"] if t["id"]} != targets
            or any(
                t["space"] != params["space"] or t["generation"] != params["generation"]
                for t in row["targets"]
                if t["id"]
            )
            or provenance.direct(row["id"])
            != {props["evidence_map"][eid] for eid in item["evidence_ids"]}
        ):
            raise store.integrity()
        actual.add(row["id"])
    if actual != set(expected) or provenance.direct(identifier) != {
        props["evidence_map"][eid] for eid in route["evidence_ids"]
    }:
        raise store.integrity()


def make_packet(tx, params, row, request, literature, cards, provenance):
    kind, identifier = KINDS[row["bucket"]], row["id"]
    props = json.loads(row["props"])
    evidence_ids = provenance.direct(identifier)
    conditions, process, form, source_record = [], None, "not_assessed", None
    if kind == "process":
        validate_route(tx, params, identifier, props, provenance)
        route = props["payload"]
        conditions = load_conditions(tx, params, identifier, route["input_conditions"], provenance)
        evidence_ids.update(
            props["evidence_map"][eid]
            for group in ("nodes", "edges", "risks")
            for item in route[group]
            for eid in item["evidence_ids"]
        )
        process = {
            "snapshot_id": identifier,
            "route": route,
            "evidence_map": {
                key: value for key, value in props["evidence_map"].items() if value in evidence_ids
            },
            "occurrence_ids": {
                item["id"]: graph.identity(node_kind, [identifier, item["id"]])
                for name, node_kind in (
                    ("nodes", "StepOccurrence"),
                    ("edges", "LinkOccurrence"),
                    ("risks", "Risk"),
                )
                for item in route[name]
            },
        }
        form = (
            "input_unknown"
            if unknown(request.input_form)
            else "template_unspecified"
            if unknown(props["input_form"])
            else "same_label_not_feasibility"
            if request.input_form == props["input_form"]
            else "different_label_requires_review"
        )
        source_record = {"input_form": props["input_form"], "validation": props["validation"]}
    elif kind == "scoped_claim":
        graph.validate_properties("ScopedClaim", props, graph.ontology())
        if props["raw_record"] != cards.get(props["card_id"]):
            raise store.integrity()
        requirements = props["raw_record"].get("evidence_requirements", [])
        expected = [v if isinstance(v, str) else graph.canonical(v) for v in requirements]
        conditions = load_conditions(tx, params, identifier, expected, provenance)
        source_record = props
    else:
        source_record = literature[identifier]
        if props["raw_record"] != source_record["metadata"]:
            raise store.integrity()
    for condition in conditions:
        evidence_ids.update(condition["evidence_ids"])
    if not evidence_ids:
        raise store.integrity()
    evidence = provenance.evidence(evidence_ids)
    blocked = [
        e["evidence_id"]
        for e in evidence
        if e["properties"].get("model_transfer_allowed") is not True
        or e["source"]["metadata"].get("model_transfer_allowed") is False
    ]
    # No blocked text, template derivatives or private request fields in external-model view.
    if request.audience == "external_model" and blocked:
        return PoolCandidate(
            candidate_id=identifier,
            kind=kind,
            research_packet=None,
            external_model_allowed=False,
            withheld_evidence_ids=blocked,
        )
    tasks = [
        {
            "topic": "process",
            "question": "원문에서 투입물·산출물·공정 순서와 적용 범위를 확인",
            "state": "unassessed",
        },
        {
            "topic": "law",
            "question": "국내 현행 분류·허용 유형·시설 허가·예외를 별도 조회",
            "state": "unassessed",
        },
        {
            "topic": "risk",
            "question": "단계·인계 구간별 누락 조건·반례·대응 행동 확인",
            "state": "unassessed",
        },
    ]
    packet = {
        "candidate_id": identifier,
        "candidate_kind": kind,
        "relevance": (
            "background-not-material-specific"
            if kind == "scoped_claim" and props["raw_record"].get("material") == "multiple"
            else "material-search-family-not-confirmed-composition"
        ),
        "source_record": source_record,
        "form_comparison": form,
        "conditions": conditions,
        "process_graph": process,
        "evidence": evidence,
        "research_tasks": tasks,
        "not_established": [
            "current_legal_eligibility",
            "lot_quality",
            "facility_acceptance",
            "cost",
            "carbon",
            "transport_route",
        ],
        "sequence_policy": "authored-conditional-template" if process else "no-sequence-inference",
        "rule_decision": None,
        "dispatch_approved": False,
    }
    return PoolCandidate(
        candidate_id=identifier,
        kind=kind,
        research_packet=packet,
        external_model_allowed=not blocked,
        withheld_evidence_ids=blocked,
    )


class CandidatePool:
    def __init__(self, config=None):
        self.config = config

    def search(self, request: CandidatePoolRequest) -> CandidatePoolResponse:
        material = resolve_material(request.material)
        context = request.model_dump(exclude={"cursor", "page_size", "audience"})
        base = {
            "normalized_material": material,
            "input_context": context if request.audience == "internal" else {},
            "missing_fields": [
                key
                for key in ("material", "input_form", "color", "contamination")
                if unknown(context[key])
            ],
            "limitations": [
                *LIMITATIONS,
                "전체 후보는 적재된 검색 수지군의 후보이며 세상의 모든 공정이나 적합성 보장이 아닙니다.",
                "형태가 다르거나 미확인이어도 후보를 보존합니다. 조건 판정과 최종 K개 선정은 후속 단계입니다.",
                "적용 조건은 조사 질문입니다. 원문·입력 사실 검증 없이 자동 제외 규칙으로 사용하지 않습니다.",
            ],
        }
        if unknown(request.material):
            return CandidatePoolResponse(status="needs_input", **base)
        if material is None:
            return CandidatePoolResponse(status="unsupported_material", **base)
        cursor = decode_cursor(request.cursor) if request.cursor else None
        binding = graph.digest({"context": context, "audience": request.audience})
        config = self.config or store.settings()
        cards = allowed_cards()
        background_ids = [
            graph.identity("scoped-claim", key)
            for key, card in cards.items()
            if card.get("material") == "multiple"
        ]

        def lookup(tx):
            generation, manifest = store._head(tx, config)
            if cursor:
                if cursor["namespace"] != config.namespace or cursor["request"] != binding:
                    raise cursor_error()
                if cursor["generation"] != generation:
                    raise cursor_error("stale-cursor")
            params = {
                "space": config.namespace,
                "generation": generation,
                "material": "material:" + material,
                "background_ids": background_ids,
            }
            totals = {kind: 0 for kind in KINDS.values()}
            for row in tx.run(POOL_QUERY + " RETURN bucket,count(*) AS total", **params):
                totals[KINDS[row["bucket"]]] = row["total"]
            rows = list(
                tx.run(
                    POOL_QUERY
                    + """ WITH bucket,id,props
                WHERE bucket>$bucket OR (bucket=$bucket AND id>$after)
                RETURN bucket,id,props ORDER BY bucket,id LIMIT $limit""",
                    **params,
                    bucket=cursor["bucket"] if cursor else -1,
                    after=cursor["id"] if cursor else "",
                    limit=request.page_size + 1,
                )
            )
            has_more = len(rows) > request.page_size
            rows = rows[: request.page_size]
            literature_ids = [r["id"] for r in rows if r["bucket"] == 2]
            literature = {}
            if literature_ids:
                documents = store.candidates(
                    None,
                    material,
                    limit=len(literature_ids),
                    config=config,
                    transaction=tx,
                    active_snapshot=(generation, manifest),
                    claim_ids=literature_ids,
                )
                literature = {v["graph_trace"]["claim_id"]: v for v in documents}
                if set(literature) != set(literature_ids):
                    raise store.integrity()
            provenance = PageEvidence(tx, params, rows)
            items = [
                make_packet(tx, params, row, request, literature, cards, provenance) for row in rows
            ]
            next_cursor = None
            if has_more:
                last = rows[-1]
                next_cursor = encode_cursor(
                    {
                        "v": 1,
                        "generation": generation,
                        "namespace": config.namespace,
                        "request": binding,
                        "bucket": last["bucket"],
                        "id": last["id"],
                    }
                )
            return CandidatePoolResponse(
                status="candidates" if sum(totals.values()) else "no_candidates",
                items=items,
                pagination=PoolPagination(
                    total_candidates=sum(totals.values()),
                    totals_by_kind=totals,
                    returned_count=len(items),
                    has_more=has_more,
                    next_cursor=next_cursor,
                    complete_in_this_response=not cursor and not has_more,
                ),
                graph_trace={
                    "generation": generation,
                    "namespace": config.namespace,
                    "ontology_id": manifest["meta"]["ontology_id"],
                    "graph_sha256": manifest["meta"]["graph_sha256"],
                    "source_records_sha256": manifest["meta"]["source_records_sha256"],
                    "order": "kind-and-id-not-recommendation",
                    "form_filter_applied": False,
                    "suitability_filter_applied": False,
                    "top_k_applied": False,
                    "audience": request.audience,
                    "background_cards_are_not_material_specific": True,
                    "pipeline_baseline": "2026-10-20-fixed-v1",
                },
                **base,
            )

        try:
            return store._read(config, lookup)
        except (KeyError, TypeError, ValueError):
            raise store.integrity() from None
