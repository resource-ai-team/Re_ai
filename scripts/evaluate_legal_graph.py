"""Read-only evaluation of real official structure. Never calls a generation model."""

import argparse
import hashlib
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from resource_ai.core.config import get_settings
from resource_ai.knowledge_graph.graph_store import get_driver, settings
from resource_ai.legal_graph.builder import build_structural_snapshot
from resource_ai.legal_graph.store import LegalGraphStore
from resource_ai.main import app
from resource_ai.rag.ingestion.dataset import validate_documents
from resource_ai.rag.ingestion.loader import load_jsonl


def evaluate(path):
    documents = load_jsonl(path)
    validation = validate_documents(documents)
    expected = build_structural_snapshot(
        documents, dataset_sha256=hashlib.sha256(path.read_bytes()).hexdigest()
    )
    config = get_settings()
    store = LegalGraphStore(namespace=config.legal_graph_namespace)
    snapshot, trace = store.load()
    checks = []

    def check(name, passed):
        checks.append({"name": name, "passed": bool(passed)})

    check("official source dataset validation", validation.passed)
    check(
        "actual Neo4j structure equals current official source build",
        snapshot.fingerprint() == expected.fingerprint(),
    )
    check("all original source spans persisted", len(snapshot.evidence) == validation.section_count)
    check(
        "structural graph does not claim model enrichment",
        snapshot.metadata.get("enrichment_complete") is False,
    )
    check("no reference promoted to APPLIES", all(e.relation != "APPLIES" for e in snapshot.edges))
    check(
        "case-to-article versions remain unresolved",
        all(
            e.data.get("version_resolved") is False for e in snapshot.edges if e.relation == "CITES"
        ),
    )
    check(
        "Korean low article numbers retained",
        {"제17조", "제25조", "제48조"}
        <= {n.data.get("article") for n in snapshot.nodes if n.kind == "Article"},
    )
    check(
        "case summary not converted to interpretation",
        not any(n.kind == "Interpretation" for n in snapshot.nodes),
    )
    client = TestClient(app)
    response = client.get("/legal-graph/status")
    check(
        "status API reports active generation",
        response.status_code == 200 and response.json().get("generation") == trace["generation"],
    )
    # Only probe analyze when server configuration forbids model use.
    if config.legal_graph_model_enabled is False:
        response = client.post("/legal-graph/analyze", json={"query": "위탁 처리 조건 확인"})
        check(
            "model-disabled request explicitly rejected",
            response.status_code == 503
            and response.json()["detail"]["code"] == "model-not-configured",
        )
    with get_driver(settings()).session(database=config.neo4j_database) as session:
        heads = [
            dict(r["h"])
            for r in session.run(
                "MATCH (h:ReSourceGraph {space:$s}) RETURN properties(h) AS h",
                s=config.neo4j_namespace,
            )
        ]
    check("separate process KG still present", len(heads) == 1)
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "scope": "real_official_source_structure_in_actual_neo4j",
        "graph_trace": trace,
        "counts": {
            "documents": validation.document_count,
            "source_spans": len(snapshot.evidence),
            "nodes": dict(Counter(n.kind for n in snapshot.nodes)),
            "relationships": dict(Counter(e.relation for e in snapshot.edges)),
        },
        "checks": checks,
        "passed": all(c["passed"] for c in checks),
        "model_validation": {
            "generation_api_calls": 0,
            "status": "deferred_at_user_request",
            "real_model_features_checklists_communities": False,
            "synthetic_model_tests_are_separate": True,
        },
        "remaining": [
            "additional Korean cases and historical law versions",
            "structured annex tables and official interpretations",
            "model enrichment and real-model accuracy evaluation",
            "expert-reviewed legal applicability ground truth",
        ],
        "not_proven": [
            "legal applicability accuracy",
            "semantic retrieval quality",
            "original paper benchmark scores",
            "facility permit or intake approval",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=Path("data/official/legal_documents.jsonl"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = evaluate(args.dataset)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "passed": report["passed"],
                "checks": len(report["checks"]),
                "output": str(args.output),
            },
            ensure_ascii=False,
        )
    )
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
