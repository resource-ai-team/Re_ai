"""Legal graph generations, separate from the process KG and user case data."""

import json
import re
from uuid import uuid4

from neo4j.exceptions import DriverError, Neo4jError
from pydantic import ValidationError

from resource_ai.knowledge_graph.graph_store import get_driver, settings
from resource_ai.legal_graph.models import GraphSnapshot, LegalGraphError, canonical

OWNER = "re-ai-legal-hierargraph-v1"


def invalid():
    return LegalGraphError(
        "graph-integrity", "법률 그래프의 구조·원문·근거 연결 검사가 실패했습니다."
    )


class LegalGraphStore:
    def __init__(self, config=None, *, namespace="re-ai-legal"):
        if not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", namespace):
            raise LegalGraphError("configuration", "법률 그래프 namespace를 확인하세요.")
        self.config = config or settings()
        self.namespace = namespace

    def _execute(self, work, *, write=False):
        try:
            with get_driver(self.config).session(database=self.config.database) as session:
                return (session.execute_write if write else session.execute_read)(work)
        except (Neo4jError, DriverError, OSError):
            raise LegalGraphError(
                "graph-unavailable", "Neo4j 법률 그래프 연결에 실패했습니다."
            ) from None

    def _load(self, tx, generation=None):
        if generation is None:
            heads = list(
                tx.run(
                    "MATCH (h:ReLegalHead {space:$s}) RETURN properties(h) AS h", s=self.namespace
                )
            )
            if not heads:
                raise LegalGraphError(
                    "graph-not-imported", "legal-graph import로 법률 그래프를 적재하세요."
                )
            if len(heads) != 1 or heads[0]["h"].get("owner") != OWNER:
                raise invalid()
            generation = heads[0]["h"].get("generation")
        manifests = list(
            tx.run(
                "MATCH (m:ReLegalSnapshot {space:$s,generation:$g}) RETURN properties(m) AS m",
                s=self.namespace,
                g=generation,
            )
        )
        if len(manifests) != 1:
            raise invalid()
        manifest = manifests[0]["m"]
        try:
            if manifest["owner"] != OWNER:
                raise invalid()
            rows = list(
                tx.run(
                    """MATCH (n {space:$s,generation:$g})
                WHERE NOT n:ReLegalSnapshot AND NOT n:ReLegalHead
                RETURN labels(n) AS labels, properties(n) AS p""",
                    s=self.namespace,
                    g=generation,
                )
            )
            nodes, evidence, expected_support = [], [], set()
            for row in rows:
                p, labels = row["p"], row["labels"]
                record = json.loads(p["record_json"])
                if p["id"] != record["id"] or p["owner"] != OWNER:
                    raise invalid()
                if labels == ["ReLegalNode"]:
                    if p["kind"] != record["kind"]:
                        raise invalid()
                    nodes.append((p["ordinal"], record))
                    expected_support.update((p["id"], eid) for eid in record["evidence_ids"])
                elif labels == ["ReLegalEvidence"]:
                    evidence.append((p["ordinal"], record))
                else:
                    raise invalid()
            edges, actual_support = [], set()
            rels = tx.run(
                """MATCH (a {space:$s,generation:$g})-[r]->(b)
                RETURN a.id AS a, b.id AS b, b.space AS space, b.generation AS generation,
                       labels(a) AS al, labels(b) AS bl, type(r) AS type, properties(r) AS p""",
                s=self.namespace,
                g=generation,
            )
            for row in rels:
                if row["space"] != self.namespace or row["generation"] != generation:
                    raise invalid()
                p = row["p"]
                if row["type"] == "RE_LEGAL_LINK" and row["al"] == row["bl"] == ["ReLegalNode"]:
                    record = json.loads(p["record_json"])
                    if (record["source"], record["target"], record["relation"]) != (
                        row["a"],
                        row["b"],
                        p["relation"],
                    ):
                        raise invalid()
                    edges.append((p["ordinal"], record))
                elif (
                    row["type"] == "RE_LEGAL_SUPPORT"
                    and row["al"] == ["ReLegalNode"]
                    and row["bl"] == ["ReLegalEvidence"]
                ):
                    pair = row["a"], row["b"]
                    if pair in actual_support or p:
                        raise invalid()
                    actual_support.add(pair)
                else:
                    raise invalid()
            if actual_support != expected_support:
                raise invalid()

            def ordered(items):
                items.sort(key=lambda item: item[0])
                if [i for i, _ in items] != list(range(len(items))):
                    raise invalid()
                return [value for _, value in items]

            header = json.loads(manifest["header_json"])
            snapshot = GraphSnapshot.model_validate(
                {
                    **header,
                    "nodes": ordered(nodes),
                    "edges": ordered(edges),
                    "evidence": ordered(evidence),
                }
            )
            if snapshot.fingerprint() != manifest["fingerprint"]:
                raise invalid()
        except (KeyError, TypeError, ValueError, ValidationError):
            raise invalid() from None
        return snapshot, {
            "namespace": self.namespace,
            "generation": generation,
            "fingerprint": manifest["fingerprint"],
            "dataset_sha256": snapshot.dataset_sha256,
        }

    def load(self):
        return self._execute(self._load)

    def status(self):
        snapshot, trace = self.load()
        return {
            **trace,
            "node_count": len(snapshot.nodes),
            "edge_count": len(snapshot.edges),
            "evidence_count": len(snapshot.evidence),
            "metadata": snapshot.metadata,
            "analysis_ready": snapshot.metadata.get("enrichment_complete") is True,
        }

    def publish(self, snapshot):
        # Revalidate even an already-instantiated object, which may have been mutated.
        snapshot = GraphSnapshot.model_validate(snapshot.model_dump(mode="json"))
        generation = uuid4().hex
        common = {"s": self.namespace, "g": generation, "owner": OWNER}

        def write(tx):
            tx.run(
                """CREATE (m:ReLegalSnapshot {space:$s,generation:$g,owner:$owner,
                   fingerprint:$fingerprint,header_json:$header})""",
                **common,
                fingerprint=snapshot.fingerprint(),
                header=canonical(
                    snapshot.model_dump(mode="json", exclude={"nodes", "edges", "evidence"})
                ),
            ).consume()
            for label, records in (
                ("ReLegalNode", snapshot.nodes),
                ("ReLegalEvidence", snapshot.evidence),
            ):
                tx.run(
                    f"""UNWIND $rows AS row CREATE (n:{label})
                    SET n = row, n.space=$s,n.generation=$g,n.owner=$owner""",
                    **common,
                    rows=[
                        {
                            "id": item.id,
                            "ordinal": i,
                            "record_json": canonical(item.model_dump(mode="json")),
                            **({"kind": item.kind} if label == "ReLegalNode" else {}),
                        }
                        for i, item in enumerate(records)
                    ],
                ).consume()
            tx.run(
                """UNWIND $rows AS row MATCH (a:ReLegalNode {space:$s,generation:$g,id:row.source}),
                (b:ReLegalNode {space:$s,generation:$g,id:row.target})
                CREATE (a)-[r:RE_LEGAL_LINK]->(b) SET r.ordinal=row.ordinal,r.relation=row.relation,r.record_json=row.record_json""",
                **common,
                rows=[
                    {
                        "source": e.source,
                        "target": e.target,
                        "relation": e.relation,
                        "ordinal": i,
                        "record_json": canonical(e.model_dump(mode="json")),
                    }
                    for i, e in enumerate(snapshot.edges)
                ],
            ).consume()
            tx.run(
                """MATCH (n:ReLegalNode {space:$s,generation:$g})
                WITH n UNWIND $rows AS row WITH n,row WHERE n.id=row.node
                MATCH (e:ReLegalEvidence {space:$s,generation:$g,id:row.evidence}) CREATE (n)-[:RE_LEGAL_SUPPORT]->(e)""",
                **common,
                rows=[
                    {"node": n.id, "evidence": eid}
                    for n in snapshot.nodes
                    for eid in n.evidence_ids
                ],
            ).consume()
            _, trace = self._load(tx, generation)
            tx.run(
                """MERGE (h:ReLegalHead {space:$s}) SET h.owner=$owner,h.generation=$g""", **common
            ).consume()
            return trace

        def constraint(tx):
            tx.run(
                "CREATE CONSTRAINT re_legal_head_space IF NOT EXISTS FOR (h:ReLegalHead) REQUIRE h.space IS UNIQUE"
            ).consume()

        self._execute(constraint, write=True)
        return self._execute(write, write=True)
