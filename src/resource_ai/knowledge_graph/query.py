"""Bounded, parameterized Neo4j candidate retrieval; no process/legal approval."""

import json

from resource_ai.api.schemas.graph import CandidateRequest, CandidateResponse
from resource_ai.knowledge_graph import graph_store as store
from resource_ai.knowledge_graph import ontology as graph

LIMITATIONS = [
    "문헌 후보는 미검증 자동 추출이며 실제 처리 가능성·국내 허가·업체 인수를 입증하지 않습니다.",
    "공정 지도는 조건부 조사 템플릿이며 실제 공장 공정도나 최적 순위가 아닙니다.",
    "현재 처리 방식은 사용자가 제공한 경우만 기록하며 기존 경로를 추정하지 않습니다.",
    "PP·PE는 문헌 탐색을 지원하며 검수된 국내 공정·손실률·대응 행동 데이터 보강이 필요합니다.",
    "비용·탄소·거리 계산, 법적 판정, MCP 서버 및 LLM 오케스트레이션은 별도 구현 범위입니다.",
]


def resolve_material(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = graph.normalize_label(value)
    return next(
        (
            key
            for key, spec in graph.ontology()["materials"].items()
            if normalized in {graph.normalize_label(v) for v in [key, *spec["aliases"]]}
        ),
        None,
    )


class GraphQuery:
    def __init__(self, config: store.Settings | None = None):
        self.config = config

    def candidate_pool(self, request):
        from resource_ai.knowledge_graph.pool import CandidatePool

        return CandidatePool(self.config).search(request)

    def process_plans(self, request):
        from resource_ai.knowledge_graph.planning import ProcessPlanner

        return ProcessPlanner(self.config).search(request)

    def facility_candidates(self, request):
        from resource_ai.knowledge_graph.facility_search import FacilitySearch

        return FacilitySearch(self.config).search(request)

    def search(self, request: CandidateRequest) -> CandidateResponse:
        material = resolve_material(request.material)
        missing = [
            name
            for name in ("material", "input_form", "color", "contamination")
            if getattr(request, name) is None
            or getattr(request, name).casefold() in {"unknown", "미확인", "모름"}
        ]
        base = {
            "normalized_material": material,
            "input_context": request.model_dump(),
            "missing_fields": missing,
            "limitations": list(LIMITATIONS),
        }
        if request.material is None:
            return CandidateResponse(status="needs_input", **base)
        if material is None:
            return CandidateResponse(status="unsupported_material", **base)
        config = self.config or store.settings()

        def lookup(tx):
            generation, manifest = store._head(tx, config)
            params = {
                "space": config.namespace,
                "generation": generation,
                "material": "material:" + material,
            }
            literature = store.candidates(
                None,
                material,
                limit=request.top_k,
                config=config,
                transaction=tx,
                active_snapshot=(generation, manifest),
            )
            rows = tx.run(
                """MATCH (r:ReSourceNode:RouteSnapshot)-[:FOR_MATERIAL]->
                (m:ReSourceNode:Material {space:$space,generation:$generation,id:$material})
                WHERE r.space=$space AND r.generation=$generation
                RETURN r.id AS id,r.properties_json AS props ORDER BY r.id LIMIT 100""",
                **params,
            )
            routes = []
            evidence_ids = set()
            requested_form = request.input_form or "unknown"
            for row in rows:
                props = json.loads(row["props"])
                if props["input_form"] != requested_form:
                    continue
                graph.validate_properties("RouteSnapshot", props, graph.ontology())
                content = {
                    "material": props["material"],
                    "input_form": props["input_form"],
                    "route": props["payload"],
                    "evidence_map": props["evidence_map"],
                    "evidence_fingerprint": props["evidence_fingerprint"],
                }
                if (
                    graph.digest(content) != props["snapshot_hash"]
                    or row["id"] != "route:" + props["snapshot_hash"][:32]
                ):
                    raise store.integrity()
                used = set(props["payload"]["evidence_ids"])
                for group in ("nodes", "edges", "risks"):
                    for item in props["payload"][group]:
                        used.update(item["evidence_ids"])
                local_map = {key: props["evidence_map"][key] for key in sorted(used)}
                evidence_ids.update(local_map.values())
                routes.append(
                    {
                        "snapshot_id": row["id"],
                        "input_form": props["input_form"],
                        "validation": props["validation"],
                        "route": props["payload"],
                        "evidence_map": local_map,
                        "dispatch_approved": False,
                    }
                )
            scoped = []
            for row in tx.run(
                """MATCH (c:ReSourceNode:ScopedClaim)-[:FOR_MATERIAL]->
                (m:ReSourceNode:Material {space:$space,generation:$generation,id:$material})
                MATCH (c)-[:SUPPORTED_BY]->(e:ReSourceNode:Evidence)
                WHERE all(n IN [c,e] WHERE n.space=$space AND n.generation=$generation)
                RETURN c.id AS id,c.properties_json AS props,e.id AS evidence ORDER BY c.id LIMIT 100""",
                **params,
            ):
                scoped.append(
                    {
                        "claim_id": row["id"],
                        **json.loads(row["props"]),
                        "evidence_id": row["evidence"],
                    }
                )
                evidence_ids.add(row["evidence"])
            evidence = []
            for row in tx.run(
                """MATCH (e:ReSourceNode:Evidence {space:$space,generation:$generation})
                WHERE e.id IN $ids
                MATCH (e)-[:FROM_SOURCE]->(s:ReSourceNode:Source {space:$space,generation:$generation})
                RETURN e.id AS id,e.properties_json AS props,s.id AS source,s.properties_json AS source_props
                ORDER BY e.id""",
                space=config.namespace,
                generation=generation,
                ids=sorted(evidence_ids),
            ):
                evidence.append(
                    {
                        "evidence_id": row["id"],
                        "properties": json.loads(row["props"]),
                        "source_id": row["source"],
                        "source": json.loads(row["source_props"]),
                    }
                )
            if {row["evidence_id"] for row in evidence} != evidence_ids:
                raise store.integrity()
            trace = {
                "generation": generation,
                "namespace": config.namespace,
                "ontology_id": manifest["meta"]["ontology_id"],
                "graph_sha256": manifest["meta"]["graph_sha256"],
                "source_records_sha256": manifest["meta"]["source_records_sha256"],
                "form_filter": requested_form,
                "ranking": "stable-source-order-not-utility-score",
            }
            return CandidateResponse(
                status="candidates" if literature or routes or scoped else "no_candidates",
                literature_candidates=literature,
                process_candidates=routes,
                scoped_claims=scoped,
                evidence=evidence,
                graph_trace=trace,
                **base,
            )

        try:
            return store._read(config, lookup)
        except (KeyError, TypeError, ValueError):
            raise store.integrity() from None
