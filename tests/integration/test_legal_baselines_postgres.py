import os
from datetime import date
from pathlib import Path
from uuid import uuid4

import pytest

from resource_ai.core.config import get_settings
from resource_ai.evidence.models import EvidenceDocument, EvidenceSection
from resource_ai.rag.legal_baselines import fetch_baselines
from resource_ai.rag.retrieval.embeddings import HashEmbeddingProvider
from resource_ai.rag.retrieval.vector_store import Database
from resource_ai.rag.service import EvidenceRepository

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_INTEGRATION") != "true", reason="Real PostgreSQL required"
)


def document(
    version, *, article="제25조", title="폐기물처리업", tenant=None, effective=date(2020, 1, 1)
):
    return EvidenceDocument(
        source_id="synthetic-law:" + version,
        document_type="LAW",
        title="폐기물관리법",
        source_url="https://www.law.go.kr/법령/폐기물관리법",
        version=version,
        tenant_id=tenant,
        effective_from=effective,
        metadata={"canonical_source_id": "law:001771"},
        sections=[
            EvidenceSection(
                article + " ⑤항",
                "가상 업종 허가 원문",
                citation="폐기물관리법 " + article + " ⑤항",
                metadata={"article": article, "article_title": title},
            )
        ],
    )


def test_real_postgres_exact_article_versions_private_scope_and_duplicate_detection():
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import make_conninfo

    url = get_settings().database_url
    schema = "re_ai_baseline_test_" + uuid4().hex
    with psycopg.connect(url, connect_timeout=5) as c:
        c.execute("CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public")
        c.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    try:
        repository = EvidenceRepository(
            Database(make_conninfo(url, options=f"-c search_path={schema},public")),
            HashEmbeddingProvider(),
        )
        repository.initialize(Path("migrations"))
        repository.ingest(document("v1"))
        repository.ingest(document("private", tenant="synthetic-tenant"))
        by_id = lambda: {r["id"]: r for r in fetch_baselines(repository.database, max_chunks=1)}
        ref = by_id()["waste-business-permit"]
        assert ref["complete"] and [s["version"] for s in ref["sources"]] == ["v1"]
        repository.ingest(document("v2", article="제25조의3", title="폐기물처리업의 적합성확인"))
        assert by_id()["waste-business-permit"]["status"] == "not_found"
        repository.ingest(document("future", effective=date(2099, 1, 1)))
        assert by_id()["waste-business-permit"]["status"] == "not_found"
        repository.ingest(document("v3"))
        with repository.database.connect() as c:
            c.execute(
                "UPDATE legal_documents SET is_current=true WHERE source_id='synthetic-law:v1'"
            )
        ref = by_id()["waste-business-permit"]
        assert not ref["complete"] and "ambiguous_current_version" in ref["source_checks"]
    finally:
        assert schema.startswith("re_ai_baseline_test_") and len(schema) == 52
        with psycopg.connect(url, connect_timeout=5) as c:
            c.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
