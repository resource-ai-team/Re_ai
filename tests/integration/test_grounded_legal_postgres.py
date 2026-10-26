import os
from dataclasses import replace
from datetime import date
from pathlib import Path
from uuid import uuid4

import pytest
from pgvector import Vector

from resource_ai.core.config import get_settings
from resource_ai.evidence.models import EvidenceDocument, EvidenceSection
from resource_ai.rag.legal_aliases import verified_aliases
from resource_ai.rag.legal_context import expand_context
from resource_ai.rag.retrieval.embeddings import HashEmbeddingProvider
from resource_ai.rag.retrieval.legal_basis import cited_basis, explicit_locator
from resource_ai.rag.retrieval.vector_store import Database
from resource_ai.rag.service import EvidenceRepository

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_INTEGRATION") != "true", reason="Real PostgreSQL required"
)


def test_literal_basis_alias_proofs_current_public_versions_and_freeform_sections():
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import make_conninfo

    url = get_settings().database_url
    schema = "re_ai_basis_test_" + uuid4().hex
    with psycopg.connect(url, connect_timeout=5) as c:
        c.execute("CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public")
        c.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    try:
        repo = EvidenceRepository(
            Database(make_conninfo(url, options=f"-c search_path={schema},public")),
            HashEmbeddingProvider(),
        )
        repo.initialize(Path("migrations"))
        law = EvidenceDocument(
            "synthetic-law",
            "LAW",
            "가상폐기물법",
            "https://www.law.go.kr/법령/가상폐기물법",
            [
                EvidenceSection(
                    "제25조(허가)",
                    "permit 위탁 허가 기준",
                    "가상폐기물법 제25조",
                    metadata={"article": "제25조", "article_title": "허가"},
                )
            ],
            effective_from=date(2020, 1, 1),
            version="v1",
            metadata={"legal_source_kind": "statute"},
        )
        quote = '「가상폐기물법」(이하 "법"이라 한다)'
        guide = EvidenceDocument(
            "synthetic-guide",
            "LAW",
            "가상 허가 지침",
            "https://www.law.go.kr/LSW/admRulInfoP.do?admRulSeq=123",
            [
                EvidenceSection(
                    "Ⅰ. 일반사항",
                    "1. 목적\n" + quote + " 제25조에 따른다.\n2. 적용대상",
                    "가상 허가 지침 I",
                ),
                EvidenceSection(
                    "Ⅲ. 허가 검토", "permit 법 제25조의 허가 확인", "가상 허가 지침 III"
                ),
            ],
            effective_from=date(2020, 1, 1),
            version="v1",
            metadata={"legal_source_kind": "administrative_rule"},
        )
        repo.ingest_many(
            [
                law,
                guide,
                replace(law, source_id="future-law", effective_from=date(2099, 1, 1)),
                replace(law, source_id="private-law", tenant_id="tenant-private"),
            ]
        )
        hits = repo.search("permit", 30, search_group="LEGAL")
        seed = next(h for h in hits if h.heading == "Ⅲ. 허가 검토")
        vector = Vector(repo.embeddings.embed_query("permit"))
        basis = cited_basis(repo.database, [seed], vector, repo._results)
        assert len(basis) == 1 and basis[0].title == "가상폐기물법"
        assert basis[0].evidence_role == "cited_basis" and basis[0].reference_sources
        exact = explicit_locator(repo.database, "가상폐기물법 제25조", repo._results)
        assert len(exact) == 1 and exact[0].evidence_role == "explicit_locator"
        assert explicit_locator(repo.database, "구 가상폐기물법 제25조", repo._results) == []
        assert (
            explicit_locator(repo.database, "가상폐기물법 제25조 말고 제26조", repo._results) == []
        )
        assert explicit_locator(repo.database, "가상폐기물법 제25조의3", repo._results) == []
        packet = expand_context(repo.database, [seed])[seed.chunk_id]
        assert packet["alias_definitions"][0]["definition"] == quote
        assert packet["primary"] and packet["linked"][0]["title"] == "가상폐기물법"
        assert packet["linked"][0]["status"] == "retrieved"
        assert (
            cited_basis(repo.database, [replace(seed, content="stale")], vector, repo._results)
            == []
        )
        with repo.database.connect() as c:
            private_id = c.execute(
                "select id from legal_documents where source_id='private-law'"
            ).fetchone()[0]
            assert verified_aliases(
                c, private_id, {"법": {"title": "가상폐기물법", "definition": quote}}
            ) == ({}, [])
        repo.ingest(replace(law, source_id="duplicate-law"))
        assert cited_basis(repo.database, [seed], vector, repo._results) == []
        assert explicit_locator(repo.database, "가상폐기물법 제25조", repo._results) == []
    finally:
        assert schema.startswith("re_ai_basis_test_") and len(schema) == 49
        with psycopg.connect(url, connect_timeout=5) as c:
            c.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
