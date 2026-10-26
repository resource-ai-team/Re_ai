"""After retrieval, expand exact public article and one-hop explicit citations.

This retrieves indexed text, not a complete legal graph or applicability judgment.
Unknown references, stale seeds, duplicate versions and budgets remain visible.
"""

import re
from urllib.parse import urlsplit

from resource_ai.rag.legal_aliases import DEFINITION_GAP, verified_aliases

_ARTICLE = r"제\s*\d+\s*조(?:\s*의\s*\d+)?"
_ANNEX = r"별표\s*\d+(?:\s*의\s*\d+)?"
_EXPLICIT = re.compile(
    rf"(?<![가-힣A-Za-z])(?P<owner>「[^」]+」|같은\s*법\s*시행령|같은\s*법|이\s*법|이\s*영|이\s*규칙|법|영|규칙)"
    rf"\s*(?:{DEFINITION_GAP}\s*)?(?:의\s*)?(?P<locator>{_ARTICLE}|{_ANNEX})"
)
_LOCAL_ANNEX = re.compile(_ANNEX)


def references(text, title, *, administrative_rule=False, aliases=None):
    """Resolve only explicit document names, 법/영 and same-document annexes."""
    base = re.sub(r"\s*(?:시행령|시행규칙)$", "", title)
    found, unresolved, spans = set(), set(), []
    for match in _EXPLICIT.finditer(text):
        owner, locator = match.group("owner", "locator")
        if not owner.startswith("「"):
            owner = re.sub(r"\s+", "", owner)
        locator = re.sub(r"\s+", "", locator)
        spans.append(match.span())
        preceding = text[max(0, match.start() - 8) : match.start()]
        if re.search(r"구\s*$", preceding):
            unresolved.add("historical_reference:" + match.group())
            continue
        if owner.startswith("「"):
            target = owner[1:-1]
        elif owner in (aliases or {}):
            target = aliases[owner]
        elif owner == "같은법" and "법" in (aliases or {}):
            target = aliases["법"]
        elif owner == "같은법시행령" and "법" in (aliases or {}):
            target = aliases["법"] + " 시행령"
        elif administrative_rule or not re.search(r"(법|법률|시행령|시행규칙)$", title):
            # 지침의 '법'은 앞에서 정의한 상위법일 수 있다. 지침명에 결합하지 않는다.
            prefix = (
                "unresolved_administrative_alias:"
                if administrative_rule
                else "unresolved_legal_alias:"
            )
            unresolved.add(prefix + match.group())
            continue
        elif owner in {"이규칙", "이영"}:
            target = title
        elif "시행령" in owner or owner == "영":
            target = base + " 시행령"
        else:
            target = base
        found.add((target, locator))
    for match in _LOCAL_ANNEX.finditer(text):
        if not any(a <= match.start() < b for a, b in spans):
            found.add((title, re.sub(r"\s+", "", match.group())))
    # Bare article mentions can refer to another law in the surrounding sentence.
    # Do not silently bind them to the seed statute.
    for match in re.finditer(_ARTICLE, text):
        if not any(a <= match.start() < b for a, b in spans):
            unresolved.add("unresolved_article_owner:" + re.sub(r"\s+", "", match.group()))
    for match in re.finditer(r"(대통령령|[가-힣]*부령|조례|고시)(?:으로|에서|로)\s*정", text):
        unresolved.add("delegated_instrument_locator_unknown:" + match.group(1))
    return sorted(found), sorted(unresolved)


def _source(row):
    return {
        "evidence_id": f"legal-chunk:{row[0]}",
        "title": row[1],
        "heading": row[2],
        "content": row[3],
        "citation": row[4],
        "source_url": row[5],
        "version": row[6],
        "effective_from": row[7].isoformat() if row[7] else None,
        "document_source_id": row[8],
        "document_type": "LAW",
        "source_position": row[4],
        "applicability": "not_audited",
    }


def expand_context(database, hits, *, max_roots=5, max_links=8, max_chunks=48, max_chars=60000):
    if not 1 <= max_roots <= 20 or not 0 <= max_links <= 16:
        raise ValueError("Invalid context root/link budget")
    if not 1 <= max_chunks <= 64 or not 1000 <= max_chars <= 120000:
        raise ValueError("Invalid context text budget")
    results = {}
    with database.connect(register_vector_types=False) as connection:
        # Same read snapshot for anchors, parent articles and their linked documents.
        connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        for index, hit in enumerate(hits):
            packet = {
                "root_chunk_id": hit.chunk_id,
                "status": "review_required",
                "primary": [],
                "linked": [],
                "unresolved": [],
                "source_checks": [],
                "scope": "indexed_parent_article_plus_explicit_one_hop_references",
                "applicability": "not_audited",
                "freshness": "current_in_local_index_not_live_verified",
            }
            results[hit.chunk_id] = packet
            if hit.document_type != "LAW":
                packet["status"] = "not_a_statute"
                continue
            if index >= max_roots:
                packet["source_checks"].append("root_budget_exhausted")
                continue
            anchor = connection.execute(
                """
                SELECT d.id, d.title, d.version, d.source_url, c.content,
                    c.metadata->>'article', c.metadata->>'article_title', c.metadata->>'annex',
                    d.metadata->>'legal_source_kind',d.metadata->'declared_legal_aliases'
                FROM legal_chunks c JOIN legal_documents d ON d.id=c.document_id
                WHERE c.id=%s AND d.document_type='LAW' AND d.tenant_id IS NULL
                  AND d.is_current=true AND d.effective_from<=CURRENT_DATE
            """,
                (hit.chunk_id,),
            ).fetchone()
            if not anchor or anchor[1:5] != (hit.title, hit.version, hit.source_url, hit.content):
                packet["source_checks"].append("stale_or_unresolvable_seed")
                continue
            aliases, alias_proofs = verified_aliases(connection, anchor[0], anchor[9])
            packet["alias_definitions"] = alias_proofs
            root_locator = (
                anchor[5]
                if anchor[5] and anchor[6]
                else ("별표" + anchor[7] if anchor[7] else None)
            )
            if root_locator is None and anchor[8] == "administrative_rule":
                root_locator = "section:" + hit.heading
                packet["scope"] = "indexed_admin_section_plus_explicit_one_hop_references"
            if not root_locator:
                packet["source_checks"].append("non_normative_or_missing_locator")
                continue
            used_chars = 0

            def fetch(title, locator):
                nonlocal used_chars
                docs = connection.execute(
                    """
                    SELECT id FROM legal_documents
                    WHERE title=%s AND document_type='LAW' AND tenant_id IS NULL
                      AND is_current=true AND effective_from<=CURRENT_DATE
                """,
                    (title,),
                ).fetchall()
                if not docs:
                    return [], "document_missing"
                if len(docs) != 1:
                    return [], "ambiguous_current_version"
                is_annex = locator.startswith("별표")
                is_section = locator.startswith("section:")
                key, value = (
                    ("heading", locator[8:])
                    if is_section
                    else ("annex", locator[2:])
                    if is_annex
                    else ("article", locator)
                )
                rows = connection.execute(
                    """
                    SELECT c.id,d.title,c.heading,c.content,c.citation,d.source_url,
                        d.version,d.effective_from,d.source_id
                    FROM legal_chunks c JOIN legal_documents d ON d.id=c.document_id
                    WHERE d.id=%s AND (
                        (%s AND c.heading=%s) OR (NOT %s AND c.metadata->>%s=%s
                        AND (%s OR COALESCE(c.metadata->>'article_title','')<>'')))
                    ORDER BY c.id LIMIT %s
                """,
                    (
                        docs[0][0],
                        is_section,
                        value,
                        is_section,
                        key,
                        value,
                        is_annex,
                        max_chunks + 1,
                    ),
                ).fetchall()
                if not rows:
                    return [], "locator_not_indexed"
                truncated = len(rows) > max_chunks
                sources = []
                for row in rows[:max_chunks]:
                    parsed = urlsplit(row[5])
                    if (
                        parsed.scheme != "https"
                        or parsed.hostname not in {"law.go.kr", "www.law.go.kr"}
                        or parsed.username
                        or parsed.password
                        or not row[6]
                    ):
                        return [], "source_metadata_unverified"
                    if used_chars + len(row[3]) > max_chars:
                        truncated = True
                        break
                    used_chars += len(row[3])
                    sources.append(_source(row))
                return sources, "partial_context" if truncated else None

            primary, issue = fetch(hit.title, root_locator)
            packet["primary"] = primary
            if issue:
                packet["source_checks"].append(issue)
            explicit, unresolved = references(
                "\n".join(s["content"] for s in primary),
                hit.title,
                administrative_rule=anchor[8] == "administrative_rule",
                aliases=aliases,
            )
            explicit = [r for r in explicit if r != (hit.title, root_locator)]
            packet["unresolved"].extend(unresolved)
            for position, (title, locator) in enumerate(explicit):
                if position >= max_links:
                    packet["unresolved"].append(f"link_budget_exhausted:{title} {locator}")
                    continue
                sources, error = fetch(title, locator)
                packet["linked"].append(
                    {
                        "title": title,
                        "locator": locator,
                        "status": error or "retrieved",
                        "sources": sources,
                    }
                )
                if error:
                    packet["unresolved"].append(f"{error}:{title} {locator}")
            if primary and not packet["source_checks"] and not packet["unresolved"]:
                packet["status"] = "retrieved"
    return results
