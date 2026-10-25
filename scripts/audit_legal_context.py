"""Inspect indexed basic articles and explicit links without paid/external inference."""

import argparse
import json
from datetime import UTC, date, datetime
from pathlib import Path

from resource_ai.core.config import get_settings
from resource_ai.evidence.models import SearchResult
from resource_ai.rag.legal_baselines import fetch_baselines
from resource_ai.rag.legal_context import expand_context
from resource_ai.rag.retrieval.vector_store import Database


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    db = Database(get_settings().database_url)
    refs = fetch_baselines(db)
    seeds = []
    for ref in refs:
        if not ref["sources"]:
            continue
        s = ref["sources"][0]
        seeds.append(
            SearchResult(
                chunk_id=int(s["evidence_id"].split(":")[-1]),
                title=s["title"],
                document_type="LAW",
                heading=s["heading"],
                content=s["content"],
                citation=s["citation"],
                source_url=s["source_url"],
                version=s["version"],
                effective_from=date.fromisoformat(s["effective_from"])
                if s["effective_from"]
                else None,
                score=0,
            )
        )
    packets = expand_context(db, seeds, max_roots=12)
    rows = [
        {
            "citation": hit.citation,
            "status": packets[hit.chunk_id]["status"],
            "primary_chunks": len(packets[hit.chunk_id]["primary"]),
            "linked": [
                {
                    "title": r["title"],
                    "locator": r["locator"],
                    "status": r["status"],
                    "chunks": len(r["sources"]),
                }
                for r in packets[hit.chunk_id]["linked"]
            ],
            "source_checks": packets[hit.chunk_id]["source_checks"],
            "unresolved": packets[hit.chunk_id]["unresolved"],
        }
        for hit in seeds
    ]
    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "scope": "10 baseline articles, indexed parent and bounded explicit one-hop references only",
        "applicability_audited": False,
        "rows": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf8")
    print(
        json.dumps(
            {
                "roots": len(rows),
                "primary_chunks": sum(r["primary_chunks"] for r in rows),
                "unresolved_references": sum(len(r["unresolved"]) for r in rows),
            }
        )
    )


if __name__ == "__main__":
    main()
