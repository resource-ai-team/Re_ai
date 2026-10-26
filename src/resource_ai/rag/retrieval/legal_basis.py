"""Companion legal basis from a retrieved rule's literal purpose, not a query→article map."""

import re
from dataclasses import replace
from urllib.parse import urlsplit

from resource_ai.rag.legal_aliases import verified_aliases
from resource_ai.rag.legal_context import references


def purpose_text(text):
    # A free-form guideline contains other sections after 1. 목적. Do not label them purpose.
    if not re.search(r"목\s*적", text[:500]):
        return ""
    return re.split(r"(?m)^\s*(?:2[.)]|제2조|[ⅡⅢⅣⅤ]+\.)", text, maxsplit=1)[0][:2500]


def instrument_level(title, kind):
    if kind == "administrative_rule":
        return 0
    if title.endswith(("시행규칙", "에 관한 규칙")):
        return 1
    if title.endswith("시행령"):
        return 2
    if title.endswith(("법", "법률")):
        return 3
    return 0


def official_url(value):
    url = urlsplit(value)
    return (
        url.scheme == "https"
        and url.hostname in {"law.go.kr", "www.law.go.kr"}
        and not url.username
        and not url.password
    )


def explicit_locator(database, query, result_builder):
    """Named public current law + immediately owned article; no fuzzy title binding."""
    normalized = re.sub(r"[\sㆍ·]", "", query)
    articles = set(re.findall(r"제\d+조(?:의\d+)?", normalized))
    if len(articles) != 1 or re.search(
        r"\d{4}년|\d{4}[-./]\d{1,2}|연혁|종전|개정전|구법|예전", normalized
    ):
        return []
    with database.connect() as c:
        c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        catalog = c.execute("""SELECT id,title FROM legal_documents
            WHERE is_current AND tenant_id IS NULL AND document_type='LAW'
              AND effective_from<=CURRENT_DATE""").fetchall()
        matches = {}
        for id_, title in catalog:
            name = re.sub(r"[\sㆍ·]", "", title)
            if not name:
                continue
            pattern = re.escape(name) + r"[」\"\)\]]*(?:의)?(제\d+조(?:의\d+)?)"
            for match in re.finditer(pattern, normalized):
                if re.search(
                    r"(?:는|은|이|가|을|를)?(?:말고|아니|제외)",
                    normalized[match.end() : match.end() + 10],
                ):
                    continue
                if re.search(
                    r"(?:구|옛|종전|개정전)$", normalized[max(0, match.start() - 6) : match.start()]
                ):
                    continue
                matches.setdefault((name, match[1]), []).append((id_, title))
        results = []
        for name, article in list(matches)[:2]:
            # Even if only one body matches, two current source versions are ambiguous.
            current = [id_ for id_, title in catalog if re.sub(r"[\sㆍ·]", "", title) == name]
            if len(current) != 1:
                continue
            rows = c.execute(
                """
                SELECT ch.id,d.title,d.document_type,ch.heading,ch.content,ch.citation,
                    d.source_url,d.effective_from,1.0,d.version,ch.page_no,
                    COALESCE(ch.citation,ch.metadata->>'source_position')
                FROM legal_chunks ch JOIN legal_documents d ON d.id=ch.document_id
                WHERE d.id=%s AND ch.metadata->>'article'=%s
                  AND COALESCE(ch.metadata->>'article_title','')<>''
                ORDER BY ch.id LIMIT 1
            """,
                (current[0], article),
            ).fetchall()
            results.extend(
                replace(h, evidence_role="explicit_locator")
                for h in result_builder(rows, "exact-legal-locator")
                if official_url(h.source_url)
            )
    return results


def cited_basis(database, seeds, query_vector, result_builder):
    """At most 3 current public administrative seeds, 12 exact references, 2 basis articles."""
    origins = {}
    processed_admin = set()
    with database.connect() as c:
        c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        for seed in seeds[:3]:
            if seed.document_type != "LAW" or not official_url(seed.source_url):
                continue
            row = c.execute(
                """
                SELECT d.id,d.metadata->'declared_legal_aliases',d.metadata->>'legal_source_kind'
                FROM legal_chunks ch JOIN legal_documents d ON d.id=ch.document_id
                WHERE ch.id=%s AND ch.content=%s AND d.title=%s AND d.version=%s
                  AND d.source_url=%s AND d.tenant_id IS NULL AND d.is_current
                  AND d.document_type='LAW' AND d.effective_from<=CURRENT_DATE
            """,
                (seed.chunk_id, seed.content, seed.title, seed.version, seed.source_url),
            ).fetchone()
            if not row:
                continue
            if row[2] == "administrative_rule":
                if row[0] in processed_admin:
                    continue
                processed_admin.add(row[0])
            level = instrument_level(seed.title, row[2])
            if level >= 3:
                continue
            aliases, _ = verified_aliases(c, row[0], row[1])
            purposes = (
                c.execute(
                    """
                SELECT id,content FROM legal_chunks
                WHERE document_id=%s AND (
                    metadata->>'article'='제1조' OR id=(
                        SELECT min(id) FROM legal_chunks WHERE document_id=%s))
                ORDER BY id LIMIT 2
            """,
                    (row[0], row[0]),
                ).fetchall()
                if row[2] == "administrative_rule"
                else [(seed.chunk_id, seed.content)]
            )
            for source_id, body in purposes:
                text = purpose_text(body) if row[2] == "administrative_rule" else body
                if not text:
                    continue
                found, _ = references(
                    text,
                    seed.title,
                    administrative_rule=row[2] == "administrative_rule",
                    aliases=aliases,
                )
                for title, locator in found:
                    if (
                        title == seed.title
                        or not locator.startswith("제")
                        or instrument_level(title, "statute") <= level
                    ):
                        continue
                    if len(origins) < 12 or (title, locator) in origins:
                        origins.setdefault((title, locator), set()).add(f"legal-chunk:{source_id}")
        records = []
        for (title, article), origin_ids in origins.items():
            documents = c.execute(
                """
                SELECT id FROM legal_documents WHERE title=%s AND is_current
                  AND tenant_id IS NULL AND document_type='LAW' AND effective_from<=CURRENT_DATE
                  AND metadata->>'legal_source_kind' IS DISTINCT FROM 'administrative_rule'
            """,
                (title,),
            ).fetchall()
            if len(documents) != 1:
                continue
            rows = c.execute(
                """
                SELECT ch.id,d.title,d.document_type,ch.heading,ch.content,ch.citation,
                    d.source_url,d.effective_from,1-(ch.embedding<=>%s),d.version,ch.page_no,
                    COALESCE(ch.citation,ch.metadata->>'source_position')
                FROM legal_chunks ch JOIN legal_documents d ON d.id=ch.document_id
                WHERE d.id=%s AND ch.metadata->>'article'=%s
                  AND COALESCE(ch.metadata->>'article_title','')<>''
                ORDER BY ch.embedding<=>%s,ch.id LIMIT 1
            """,
                (query_vector, documents[0][0], article, query_vector),
            ).fetchall()
            for hit in result_builder(rows, "legal-reference"):
                if not official_url(hit.source_url):
                    continue
                records.append(
                    replace(
                        hit,
                        evidence_role="cited_basis",
                        reference_sources=tuple(sorted(origin_ids)),
                    )
                )
    return sorted(records, key=lambda h: (-h.score, h.chunk_id))[:2]


def balance_basis(ranked, basis, limit, rrf_k=60, query=""):
    """Up to two cited basis articles plus retrieved guides; no applicability verdict."""
    if not basis:
        return ranked[:limit]
    normal = lambda value: re.sub(r"[\sㆍ·]", "", value)
    explicit = [hit for hit in ranked if normal(hit.title) and normal(hit.title) in normal(query)]
    preferred = max(explicit, key=lambda h: len(normal(h.title))) if explicit else None
    priority = []
    for reference, rank in zip(basis, (1, 2)):
        priority.append(
            replace(
                reference,
                score=1 / (rrf_k + rank),
                retrieval_method="legal-reference+basis-balanced",
            )
        )
    # Keep both evidence roles visible. One basis is enough for a one-result request.
    interleaved = [preferred] if preferred is not None else []
    for i in range(max(len(priority), len(ranked))):
        if i < len(priority):
            interleaved.append(priority[i])
        if i < len(ranked):
            interleaved.append(ranked[i])
    seen = set()
    output = []
    for hit in interleaved:
        article = re.search(r"제\s*\d+\s*조(?:\s*의\s*\d+)?", hit.citation or "")
        key = (
            (hit.title, hit.version, hit.source_url, article.group())
            if article
            else ("id", hit.chunk_id)
        )
        if key in seen:
            continue
        seen.add(key)
        output.append(hit)
        if len(output) == limit:
            break
    return output
