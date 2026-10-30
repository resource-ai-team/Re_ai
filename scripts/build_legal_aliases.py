"""Derive bounded alias metadata from current public legal source text, not an LLM."""

import argparse
import json
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

from resource_ai.core.config import get_settings
from resource_ai.evidence.models import EvidenceSection
from resource_ai.rag.legal_aliases import RECIPE, extract_aliases
from resource_ai.rag.retrieval.vector_store import Database


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    groups = defaultdict(list)
    with Database(get_settings().database_url).connect(register_vector_types=False) as c:
        c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        rows = c.execute("""
            SELECT d.id,c.heading,c.content,c.citation FROM legal_documents d
            JOIN legal_chunks c ON c.document_id=d.id
            WHERE d.document_type='LAW' AND d.tenant_id IS NULL AND d.is_current
              AND d.effective_from<=CURRENT_DATE ORDER BY c.id
        """).fetchall()
        for id_, heading, content, citation in rows:
            groups[id_].append(EvidenceSection(heading, content, citation))
        alias_count = 0
        for id_, sections in groups.items():
            aliases, conflicts = extract_aliases(sections)
            alias_count += len(aliases)
            c.execute(
                """UPDATE legal_documents SET metadata=metadata || %s::jsonb
                WHERE id=%s AND is_current AND tenant_id IS NULL AND document_type='LAW'""",
                (
                    json.dumps(
                        {
                            "declared_legal_aliases": aliases,
                            "conflicting_legal_aliases": conflicts,
                            "legal_alias_recipe": RECIPE,
                        },
                        ensure_ascii=False,
                    ),
                    id_,
                ),
            )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(
            {
                "generated_at": datetime.now(UTC).isoformat(),
                "documents": len(groups),
                "unique_aliases": alias_count,
                "recipe": RECIPE,
                "original_text_modified": False,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
