"""Exact public-statute starting points, independent of vector ranking.

This catalog never decides applicability, exceptions, permits or intake.
No original enterprise values or hosted models are involved in these lookups.
"""

import hashlib
import json
import re
from collections import defaultdict
from functools import lru_cache
from importlib.resources import files
from urllib.parse import urlsplit


@lru_cache(maxsize=1)
def catalog():
    raw = files("resource_ai.rag").joinpath("resources/legal-baselines.json").read_bytes()
    value = json.loads(raw)
    refs = value["references"]
    if len(refs) > 12 or len({r["id"] for r in refs}) != len(refs):
        raise ValueError("Invalid legal baseline catalog")
    return value, hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest()


def reference_ids(topic):
    return [r["id"] for r in catalog()[0]["references"] if r["topic"] == topic]


def unavailable_references():
    return [
        {
            **r,
            "status": "unavailable",
            "sources": [],
            "complete": False,
            "applicability": "not_audited",
        }
        for r in catalog()[0]["references"]
    ]


def fetch_baselines(database, *, max_chunks=64):
    """One SQL snapshot, current public LAW only; no fuzzy title/article match."""
    if not 1 <= max_chunks <= 128:
        raise ValueError("max_chunks must be between 1 and 128")
    refs = catalog()[0]["references"]
    with database.connect() as connection:
        rows = connection.execute(
            """
            WITH anchors AS (
                SELECT * FROM jsonb_to_recordset(%s::jsonb)
                AS a(id text, canonical_source_id text, article text)
            ), documents AS MATERIALIZED (
                SELECT a.id AS anchor_id, a.article, d.id AS document_id,
                    d.source_id, d.title, d.source_url, d.version, d.effective_from,
                    d.metadata ->> 'collected_at' AS collected_at
                FROM anchors a JOIN legal_documents d
                  ON d.metadata ->> 'canonical_source_id' = a.canonical_source_id
                WHERE d.is_current = true AND d.document_type = 'LAW' AND d.tenant_id IS NULL
                  AND (d.effective_from IS NULL OR d.effective_from <= CURRENT_DATE)
            ), sections AS (
                SELECT d.*, c.id AS chunk_id, c.heading, c.content, c.citation,
                    c.metadata ->> 'article_title' AS article_title,
                    count(*) OVER (PARTITION BY d.anchor_id) AS total_chunks,
                    row_number() OVER (PARTITION BY d.anchor_id ORDER BY c.id) AS rn
                FROM documents d JOIN legal_chunks c ON c.document_id = d.document_id
                WHERE c.metadata ->> 'article' = d.article
                  AND COALESCE(c.metadata ->> 'article_title', '') <> ''
            )
            SELECT s.anchor_id, s.source_id, s.title, s.source_url, s.version,
                s.effective_from, s.collected_at, s.chunk_id, s.heading, s.content,
                s.citation, s.article_title, s.total_chunks,
                (SELECT count(*) FROM documents d WHERE d.anchor_id = s.anchor_id)
            FROM sections s WHERE rn <= %s ORDER BY anchor_id, rn
            """,
            (json.dumps(refs, ensure_ascii=False), max_chunks),
        ).fetchall()
    grouped = defaultdict(list)
    for row in rows:
        grouped[row[0]].append(row)
    results = []
    for ref in refs:
        selected = grouped[ref["id"]]
        status, sources, checks = "not_found", [], []
        for row in selected:
            (
                _,
                source_id,
                title,
                url,
                version,
                effective,
                collected,
                chunk_id,
                heading,
                content,
                citation,
                article_title,
                total,
                versions,
            ) = row
            if versions != 1:
                checks.append("ambiguous_current_version")
            parsed = urlsplit(url)
            if (
                parsed.scheme != "https"
                or parsed.hostname not in {"law.go.kr", "www.law.go.kr"}
                or parsed.username
                or parsed.password
            ):
                checks.append("official_origin_unverified")
            if effective is None or not version:
                checks.append("source_date_or_version_unknown")
            locator = re.escape(f"{ref['law_title']} {ref['article']}") + r"(?:\s|$)"
            if title != ref["law_title"] or not re.match(locator, citation or ""):
                checks.append("source_locator_mismatch")
            if article_title != ref["article_title"]:
                checks.append("article_title_changed")
            if len(selected) != total:
                checks.append("partial_article")
            sources.append(
                {
                    "evidence_id": f"legal-chunk:{chunk_id}",
                    "document_source_id": source_id,
                    "document_type": "LAW",
                    "title": title,
                    "heading": heading,
                    "content": content,
                    "citation": citation,
                    "source_url": url,
                    "version": version,
                    "effective_from": effective.isoformat() if effective else None,
                    "collected_at": collected,
                    "content_sha256": hashlib.sha256(content.encode()).hexdigest(),
                    "hash_scope": "local_quote_content_not_official_signature",
                    "retrieval_method": "exact-canonical-source-and-article",
                    "applicability": "not_audited",
                }
            )
        if selected:
            status = "retrieved" if not checks else "review_required"
        results.append(
            {
                **ref,
                "status": status,
                "complete": status == "retrieved",
                "sources": sources,
                "source_checks": sorted(set(checks)),
                "applicability": "not_audited",
                "freshness": "current_in_local_index_not_live_verified",
                "completeness_scope": "all_indexed_chunks_for_exact_article",
                "jurisdiction": "KR",
            }
        )
    return results
