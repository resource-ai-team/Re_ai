import os
from pathlib import Path
from uuid import uuid4

import pytest

from resource_ai.core.config import get_settings
from resource_ai.evidence.models import EvidenceDocument, EvidenceSection
from resource_ai.rag.retrieval.embeddings import HashEmbeddingProvider
from resource_ai.rag.retrieval.vector_store import Database
from resource_ai.rag.service import EvidenceRepository

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_INTEGRATION") != "true", reason="Real PostgreSQL required"
)


def _document(
    source_id: str,
    corpus_type: str,
    title: str,
    content: str,
    *,
    canonical_source_id: str,
    tenant_id: str | None = None,
) -> EvidenceDocument:
    metadata = {"canonical_source_id": canonical_source_id}
    if corpus_type == "TECHNICAL_LITERATURE":
        metadata["rights"] = {"indexing_allowed": True}
    elif corpus_type == "HISTORICAL_CASE":
        metadata["verified"] = True
    elif corpus_type == "COMPANY_DOCUMENT":
        metadata.update({"review_status": "REVIEWED", "model_transfer_allowed": False})
    return EvidenceDocument(
        source_id=source_id,
        document_type=corpus_type,
        title=title,
        source_url=f"https://example.org/{source_id}",
        source_org="테스트 기관",
        version=source_id.rsplit(":", 1)[-1],
        tenant_id=tenant_id,
        sections=[EvidenceSection("본문", content, page_no=1)],
        metadata=metadata,
    )


def test_real_postgres_indexes_versions_and_isolates_company_documents() -> None:
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import make_conninfo

    database_url = get_settings().database_url
    schema = "re_ai_corpus_test_" + uuid4().hex
    with psycopg.connect(database_url, connect_timeout=5) as connection:
        connection.execute("CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public")
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    try:
        isolated_url = make_conninfo(database_url, options=f"-c search_path={schema},public")
        repository = EvidenceRepository(Database(isolated_url), HashEmbeddingProvider())
        repository.initialize(Path("migrations"))
        repository.initialize(Path("migrations"))
        repository.ingest_many(
            [
                _document(
                    "paper:pet:v1",
                    "TECHNICAL_LITERATURE",
                    "PET 공정 연구 v1",
                    "세척 공정 조건",
                    canonical_source_id="paper:pet",
                ),
                _document(
                    "case:pet:v1",
                    "HISTORICAL_CASE",
                    "PET 처리 사례",
                    "처리 성공 조건",
                    canonical_source_id="case:pet",
                ),
                _document(
                    "report:1:v1",
                    "COMPANY_DOCUMENT",
                    "A기업 성적서",
                    "A기업 수분 5%",
                    canonical_source_id="report:1",
                    tenant_id="tenant-a",
                ),
                _document(
                    "report:1:v1",
                    "COMPANY_DOCUMENT",
                    "B기업 성적서",
                    "B기업 수분 9%",
                    canonical_source_id="report:1",
                    tenant_id="tenant-b",
                ),
            ]
        )
        repository.ingest(
            _document(
                "paper:pet:v2",
                "TECHNICAL_LITERATURE",
                "PET 공정 연구 v2",
                "개정된 세척 공정 조건",
                canonical_source_id="paper:pet",
            )
        )

        with repository.database.connect() as connection:
            versions = connection.execute(
                """
                SELECT source_id, is_current
                FROM legal_documents
                WHERE metadata ->> 'canonical_source_id' = 'paper:pet'
                ORDER BY source_id
                """
            ).fetchall()
        assert versions == [("paper:pet:v1", False), ("paper:pet:v2", True)]

        tenant_a = repository.search(
            "수분",
            search_group="COMPANY",
            tenant_id="tenant-a",
        )
        tenant_b = repository.search(
            "수분",
            search_group="COMPANY",
            tenant_id="tenant-b",
        )
        public_results = repository.search("수분")
        assert {result.title for result in tenant_a} == {"A기업 성적서"}
        assert {result.title for result in tenant_b} == {"B기업 성적서"}
        assert all(result.document_type != "COMPANY_DOCUMENT" for result in public_results)
    finally:
        assert schema.startswith("re_ai_corpus_test_") and len(schema) == 50
        with psycopg.connect(database_url, connect_timeout=5) as connection:
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
