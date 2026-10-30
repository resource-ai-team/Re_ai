"""Resolve only unique literal legal definitions; never infer an upper law by title."""

import json
import re
from collections import defaultdict

RECIPE = "literal-unique-legal-alias-v1"
DEFINITION_GAP = r'\(\s*이하\s*["“‘\'](?:법|영|규칙|고시|규정|지침)["”’\'](?:이|으)?라\s*한다\s*\)'
_DEFINITION = re.compile(
    r'「(?P<title>[^」]{2,150})」\s*\(\s*이하\s*(?P<scope>[^"“‘\']{0,40})'
    r'["“‘\'](?P<alias>법|영|규칙|고시|규정|지침)["”’\'](?:이|으)?라\s*한다\s*\)'
)


def extract_aliases(sections):
    found, blocked = defaultdict(list), set()
    for section in sections:
        for match in _DEFINITION.finditer(section.content):
            alias, title = match["alias"], match["title"]
            historical = re.search(
                r"구\s*$", section.content[max(0, match.start() - 5) : match.start()]
            )
            if match["scope"].strip() or historical:
                blocked.add(alias)
            found[alias].append(
                {"title": title, "definition": match.group(), "citation": section.citation}
            )
    aliases = {}
    for alias, records in found.items():
        if alias not in blocked and len({r["title"] for r in records}) == 1:
            aliases[alias] = records[0]
        else:
            blocked.add(alias)
    return aliases, sorted(blocked)


def verified_aliases(connection, document_id, metadata):
    """Definition syntax and its presence in this exact current document must agree."""
    if not isinstance(metadata, dict) or len(metadata) > 8:
        return {}, []
    definitions = []
    for alias, record in metadata.items():
        if not isinstance(record, dict):
            continue
        definition = record.get("definition", "")
        match = _DEFINITION.fullmatch(definition) if isinstance(definition, str) else None
        if (
            match
            and not match["scope"].strip()
            and match["alias"] == alias
            and match["title"] == record.get("title")
        ):
            definitions.append({"alias": alias, "title": record["title"], "definition": definition})
    if not definitions:
        return {}, []
    rows = connection.execute(
        """
        SELECT a.alias,a.title,a.definition,c.id,c.citation
        FROM jsonb_to_recordset(%s::jsonb) a(alias text,title text,definition text)
        JOIN legal_chunks c ON c.document_id=%s AND strpos(c.content,a.definition)>0
        JOIN legal_documents d ON d.id=c.document_id AND d.is_current
          AND d.tenant_id IS NULL AND d.document_type='LAW' AND d.effective_from<=CURRENT_DATE
        ORDER BY c.id
    """,
        (json.dumps(definitions, ensure_ascii=False), document_id),
    ).fetchall()
    aliases, proofs = {}, []
    for alias, title, definition, chunk_id, citation in rows:
        if alias in aliases:
            continue
        aliases[alias] = title
        proofs.append(
            {
                "alias": alias,
                "title": title,
                "definition": definition,
                "evidence_id": f"legal-chunk:{chunk_id}",
                "citation": citation,
            }
        )
    return aliases, proofs
