"""Export one complete active Neo4j generation without changing the graph."""

import argparse
import gzip
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from resource_ai.knowledge_graph import graph_store as store
from resource_ai.knowledge_graph.ontology import ontology

DATA_DIR = Path(__file__).resolve().parent / ".data"


def build_snapshot(generation, manifest, nodes, edges, namespace):
    definition = ontology()
    kinds = list(definition["classes"])
    relations = list(definition["relations"])
    kind_index = {kind: i for i, kind in enumerate(kinds)}
    relation_index = {kind: i for i, kind in enumerate(relations)}
    node_index = {node["id"]: i for i, node in enumerate(nodes)}
    if not nodes or not edges:
        raise ValueError("The active graph is empty; sample data will not be substituted.")
    if len(node_index) != len(nodes):
        raise ValueError("Duplicate node IDs in the snapshot.")
    node_counts = Counter(node["kind"] for node in nodes)
    edge_counts = Counter(edge["kind"] for edge in edges)
    if node_counts != manifest["node_counts"] or edge_counts != manifest["edge_counts"]:
        raise ValueError("Export counts differ from the active generation manifest.")
    degrees = [0] * len(nodes)
    triples = []
    aggregates = Counter()
    for edge in edges:
        if edge["source"] not in node_index or edge["target"] not in node_index:
            raise ValueError("A relationship endpoint is missing from the snapshot.")
        a, b = node_index[edge["source"]], node_index[edge["target"]]
        rule = definition["relations"].get(edge["kind"])
        if (
            rule is None
            or nodes[a]["kind"] not in rule["domain"]
            or nodes[b]["kind"] not in rule["range"]
        ):
            raise ValueError("Relationship types do not match the ontology.")
        r = relation_index[edge["kind"]]
        degrees[a] += 1
        degrees[b] += 1
        triples.append([a, b, r])
        aggregates[(kind_index[nodes[a]["kind"]], kind_index[nodes[b]["kind"]], r)] += 1
    return {
        "meta": {
            "namespace": namespace,
            "generation": generation,
            "exported_at": datetime.now(UTC).isoformat(),
            "node_count": len(nodes),
            "edge_count": len(edges),
            "source": "Neo4j active generation; complete node and relationship export",
            "graph_sha256": manifest["meta"]["graph_sha256"],
            "node_counts": dict(node_counts),
            "edge_counts": dict(edge_counts),
        },
        "kinds": [
            {"id": k, "label": definition["classes"][k]["label"], "count": node_counts[k]}
            for k in kinds
        ],
        "relations": [
            {"id": k, **definition["relations"][k], "count": edge_counts[k]} for k in relations
        ],
        "nodes": [
            [n["id"], n["label"] or n["id"], kind_index[n["kind"]], degrees[i]]
            for i, n in enumerate(nodes)
        ],
        "edges": triples,
        "aggregates": [[a, b, r, n] for (a, b, r), n in sorted(aggregates.items())],
    }


def export_snapshot(destination=DATA_DIR):
    config = store.settings()

    def extract(tx):
        generation, manifest = store._head(tx, config)
        params = {"space": config.namespace, "generation": generation}
        nodes = [
            dict(row)
            for row in tx.run(
                "MATCH (n:ReSourceNode {space:$space,generation:$generation}) "
                "RETURN n.id AS id,n.kind AS kind,n.label AS label ORDER BY n.kind,n.id",
                **params,
            )
        ]
        edges = [
            dict(row)
            for row in tx.run(
                "MATCH (s:ReSourceNode {space:$space,generation:$generation})-[r]->"
                "(t:ReSourceNode {space:$space,generation:$generation}) "
                "RETURN s.id AS source,t.id AS target,type(r) AS kind ORDER BY source,kind,target",
                **params,
            )
        ]
        return build_snapshot(generation, manifest, nodes, edges, config.namespace)

    data = store._read(config, extract)
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    # Stop the explorer before refreshing these three files (see README).
    (destination / "graph.json").write_bytes(raw)
    (destination / "graph.json.gz").write_bytes(gzip.compress(raw, compresslevel=6))
    (destination / "manifest.json").write_text(
        json.dumps(data["meta"], ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return data["meta"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DATA_DIR)
    args = parser.parse_args()
    print(json.dumps(export_snapshot(args.output), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
