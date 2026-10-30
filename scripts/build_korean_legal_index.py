"""Backfill public current legal lexical vectors without modifying E5 or raw text."""

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from resource_ai.core.config import get_settings
from resource_ai.rag.retrieval.korean_lexical import RECIPE, KoreanLegalTokenizer, index_coverage
from resource_ai.rag.retrieval.vector_store import Database


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--check-only", action="store_true", help="Read-only readiness check")
    args = parser.parse_args()
    db = Database(get_settings().database_url)
    with db.connect(register_vector_types=False) as c:
        if not args.check_only:
            c.execute(Path("migrations/006_korean_lexical.sql").read_text(encoding="utf-8"))
        if args.check_only:
            report = {
                "generated_at": datetime.now(UTC).isoformat(),
                "recipe": RECIPE,
                "mode": "check_only",
                **index_coverage(c),
            }
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
            print(json.dumps(report))
            return 0 if report["complete"] else 1
        rows = c.execute("""
            SELECT c.id,d.title,c.heading,c.citation,c.content,c.metadata->>'korean_lexical_recipe',
                c.korean_search_vector IS NOT NULL
            FROM legal_chunks c JOIN legal_documents d ON d.id=c.document_id
            WHERE d.is_current AND d.tenant_id IS NULL AND d.document_type IN ('LAW','PRECEDENT')
              AND (d.document_type<>'LAW' OR d.effective_from<=CURRENT_DATE)
            ORDER BY c.id
        """).fetchall()
    tokenizer = KoreanLegalTokenizer()
    updated = 0
    for offset in range(0, len(rows), 256):
        batch = []
        for id_, title, heading, citation, content, recipe, indexed in rows[offset : offset + 256]:
            if recipe == tokenizer.recipe and indexed:
                continue
            batch.append(
                (
                    tokenizer.text(title),
                    tokenizer.text(heading + " " + (citation or "")),
                    tokenizer.text(content),
                    tokenizer.recipe,
                    id_,
                )
            )
        with db.connect(register_vector_types=False) as c, c.cursor() as cursor:
            cursor.executemany(
                """
                    UPDATE legal_chunks c SET korean_search_vector=
                      setweight(to_tsvector('simple',%s),'A') ||
                      setweight(to_tsvector('simple',%s),'B') ||
                      setweight(to_tsvector('simple',%s),'D'),
                      metadata=c.metadata || jsonb_build_object('korean_lexical_recipe',%s::text)
                    FROM legal_documents d WHERE d.id=c.document_id AND c.id=%s
                      AND d.is_current AND d.tenant_id IS NULL
                      AND d.document_type IN ('LAW','PRECEDENT')
                """,
                batch,
            )
        updated += len(batch)
        print(f"lexical {min(offset + 256, len(rows))}/{len(rows)}", flush=True)
    with db.connect(register_vector_types=False) as c:
        coverage = index_coverage(c)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(
            {
                "generated_at": datetime.now(UTC).isoformat(),
                "recipe": tokenizer.recipe,
                **coverage,
                "updated_chunks": updated,
                "embedding_vectors_modified": False,
                "raw_quotes_modified": False,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return 0 if coverage["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
