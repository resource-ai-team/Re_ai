"""RAG 문서 인덱싱과 벡터·키워드 결합 검색을 제공하는 저장소 서비스."""

import json
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

from pgvector import Vector

from resource_ai.evidence.models import (
    CorpusType,
    EvidenceDocument,
    SearchGroup,
    SearchResult,
    corpus_types_for_search_group,
)
from resource_ai.rag.ingestion.chunker import chunk_for_corpus
from resource_ai.rag.retrieval.embeddings import EmbeddingProvider
from resource_ai.rag.retrieval.reranker import rerank_results
from resource_ai.rag.retrieval.retriever import reciprocal_rank_fusion
from resource_ai.rag.retrieval.vector_store import Database


class EvidenceRepository:
    """문서 버전·기업 경계를 보존하며 RAG 근거를 적재하고 검색한다."""

    def __init__(
        self,
        database: Database,
        embeddings: EmbeddingProvider,
        *,
        candidate_multiplier: int = 4,
        rrf_k: int = 60,
    ) -> None:
        self.database = database
        self.embeddings = embeddings
        self.candidate_multiplier = candidate_multiplier
        self.rrf_k = rrf_k

    def initialize(self, migration_path: Path) -> None:
        migration_files = (
            sorted(migration_path.glob("*.sql")) if migration_path.is_dir() else [migration_path]
        )
        if not migration_files:
            raise ValueError(f"No SQL migrations found in {migration_path}")
        # 첫 마이그레이션이 vector 확장을 만들기 전에는 pgvector 타입을 등록할 수 없다.
        with self.database.connect(register_vector_types=False) as connection:
            for path in migration_files:
                connection.execute(path.read_text(encoding="utf-8"))

    @staticmethod
    def _canonical_source_id(document: EvidenceDocument) -> str:
        """버전이 달라도 같은 원문임을 식별할 안정적인 출처 ID를 만든다."""
        explicit = document.metadata.get("canonical_source_id")
        if explicit:
            return str(explicit)
        if document.document_type == "LAW" and document.metadata.get("law_id"):
            return f"law:{document.metadata['law_id']}"
        if document.document_type == "PRECEDENT" and document.metadata.get("precedent_id"):
            return f"precedent:{document.metadata['precedent_id']}"
        if document.document_type == "COMPANY_DOCUMENT":
            return f"company_document:{document.tenant_id}:{document.source_id}"
        return document.source_id

    def _prepare_document(
        self, document: EvidenceDocument
    ) -> tuple[
        EvidenceDocument, list[tuple[str, str, str | None, int | None, dict]], list[list[float]]
    ]:
        """DB 트랜잭션 전에 청킹과 임베딩을 끝내 적재 중간 실패를 줄인다."""
        canonical_source_id = self._canonical_source_id(document)
        document = replace(
            document,
            metadata={**document.metadata, "canonical_source_id": canonical_source_id},
        )
        chunk_records: list[tuple[str, str, str | None, int | None, dict]] = []
        for section in document.sections:
            for chunk in chunk_for_corpus(section.content, document.document_type):
                metadata = {
                    **section.metadata,
                    "chunk_index": chunk.index,
                    "corpus_type": document.document_type,
                    "source_id": document.source_id,
                    "source_url": document.source_url,
                    "version": document.version,
                    "tenant_id": document.tenant_id,
                    "source_position": section.citation or section.page_no,
                }
                chunk_records.append(
                    (section.heading, chunk.content, section.citation, section.page_no, metadata)
                )
        if not chunk_records:
            raise ValueError(f"Document {document.source_id!r} produced no searchable chunks")
        vectors = self.embeddings.embed_documents([record[1] for record in chunk_records])
        if len(vectors) != len(chunk_records):
            raise ValueError(
                f"Embedding count mismatch for {document.source_id}: "
                f"expected {len(chunk_records)}, got {len(vectors)}"
            )
        return document, chunk_records, vectors

    @staticmethod
    def _write_document(connection, document, chunk_records, vectors) -> int:
        """동일 원문의 이전 버전을 비활성화하고 현재 버전의 청크를 교체한다."""
        canonical_source_id = document.metadata["canonical_source_id"]
        connection.execute(
            """
            UPDATE legal_documents
            SET is_current = false, updated_at = now()
            WHERE source_id <> %s
              AND metadata ->> 'canonical_source_id' = %s
              AND tenant_id IS NOT DISTINCT FROM %s
              AND is_current = true
            """,
            (document.source_id, canonical_source_id, document.tenant_id),
        )
        row = connection.execute(
            """
                INSERT INTO legal_documents (
                    source_id, document_type, title, source_url, source_org,
                    document_no, effective_from, version, tenant_id, metadata
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                ON CONFLICT (source_id, tenant_key) DO UPDATE SET
                    document_type = EXCLUDED.document_type,
                    title = EXCLUDED.title,
                    source_url = EXCLUDED.source_url,
                    source_org = EXCLUDED.source_org,
                    document_no = EXCLUDED.document_no,
                    effective_from = EXCLUDED.effective_from,
                    version = EXCLUDED.version,
                    tenant_id = EXCLUDED.tenant_id,
                    metadata = EXCLUDED.metadata,
                    is_current = true,
                    updated_at = now()
                RETURNING id
                """,
            (
                document.source_id,
                document.document_type,
                document.title,
                document.source_url,
                document.source_org,
                document.document_no,
                document.effective_from,
                document.version,
                document.tenant_id,
                json.dumps(document.metadata, ensure_ascii=False),
            ),
        ).fetchone()
        if row is None:
            raise RuntimeError("Document upsert did not return an id")
        document_id = row[0]
        connection.execute("DELETE FROM legal_chunks WHERE document_id = %s", (document_id,))
        for record, vector in zip(chunk_records, vectors, strict=True):
            heading, content, citation, page_no, metadata = record
            connection.execute(
                """
                    INSERT INTO legal_chunks (
                        document_id, heading, content, citation, page_no, metadata, embedding
                    ) VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s)
                    """,
                (
                    document_id,
                    heading,
                    content,
                    citation,
                    page_no,
                    json.dumps(metadata, ensure_ascii=False),
                    Vector(vector),
                ),
            )
        return len(chunk_records)

    def ingest_many(self, documents: Sequence[EvidenceDocument]) -> int:
        """모든 임베딩을 먼저 검증한 뒤 데이터셋 전체를 한 트랜잭션으로 적재한다."""
        prepared = [self._prepare_document(document) for document in documents]
        if not prepared:
            raise ValueError("At least one document is required")
        with self.database.connect() as connection:
            return sum(
                self._write_document(connection, document, chunk_records, vectors)
                for document, chunk_records, vectors in prepared
            )

    def ingest(self, document: EvidenceDocument) -> int:
        return self.ingest_many([document])

    @staticmethod
    def _results(rows: list[tuple], method: str) -> list[SearchResult]:
        return [
            SearchResult(
                chunk_id=row[0],
                title=row[1],
                document_type=row[2],
                heading=row[3],
                content=row[4],
                citation=row[5],
                source_url=row[6],
                effective_from=row[7],
                score=row[8],
                retrieval_method=method,
                version=row[9],
                page_no=row[10],
                source_position=row[11],
                metadata=(row[12] or {}) if len(row) > 12 else {},
            )
            for row in rows
        ]

    def search(
        self,
        query: str,
        top_k: int = 5,
        *,
        corpus_types: Sequence[str] | None = None,
        tenant_id: str | None = None,
        search_group: SearchGroup | str | None = None,
    ) -> list[SearchResult]:
        """검색 그룹 범위 안에서 벡터·키워드 후보를 결합해 Top K 근거를 반환한다."""
        if top_k <= 0:
            raise ValueError("top_k must be positive")
        if search_group is not None and corpus_types:
            raise ValueError("search_group and corpus_types cannot be used together")
        if search_group is not None:
            # 외부에는 세 검색 그룹만 노출하고 DB의 세부 문서 유형은 그대로 유지한다.
            normalized_types = list(corpus_types_for_search_group(search_group))
        else:
            normalized_types = list(dict.fromkeys(corpus_types or [])) or None
        supported_types = {item.value for item in CorpusType}
        invalid_types = sorted(set(normalized_types or []) - supported_types)
        if invalid_types:
            raise ValueError(f"Unsupported corpus types: {', '.join(invalid_types)}")
        tenant_id = tenant_id.strip() if tenant_id else None
        # 기업 문서 검색은 tenant_id가 있어야 하며 SQL에서도 같은 값으로 범위를 제한한다.
        if CorpusType.COMPANY_DOCUMENT in (normalized_types or []) and not tenant_id:
            raise ValueError("tenant_id is required when searching company documents")
        query_vector = Vector(self.embeddings.embed_query(query))
        candidate_count = max(top_k * self.candidate_multiplier, top_k)
        with self.database.connect() as connection:
            # 의미 유사도 검색과 정확한 용어 검색을 따로 수행한 뒤 순위 기반으로 결합한다.
            vector_rows = connection.execute(
                """
                WITH filtered_chunks AS MATERIALIZED (
                    SELECT
                        c.id,
                        d.title,
                        d.document_type,
                        c.heading,
                        c.content,
                        c.citation,
                        d.source_url,
                        d.effective_from,
                        d.version,
                        c.page_no,
                        c.metadata AS chunk_metadata,
                        c.embedding,
                        jsonb_build_object('document', d.metadata, 'chunk', c.metadata) AS metadata
                    FROM legal_chunks c
                    JOIN legal_documents d ON d.id = c.document_id
                    WHERE
                        d.is_current = true
                        AND (%s::text[] IS NULL OR d.document_type = ANY(%s::text[]))
                        AND (d.document_type <> 'COMPANY_DOCUMENT' OR d.tenant_id = %s)
                        AND (
                            d.document_type <> 'LAW'
                            OR d.effective_from IS NULL
                            OR d.effective_from <= CURRENT_DATE
                        )
                )
                SELECT
                    id,
                    title,
                    document_type,
                    heading,
                    content,
                    citation,
                    source_url,
                    effective_from,
                    1 - (embedding <=> %s) AS score,
                    version,
                    page_no,
                    COALESCE(citation, chunk_metadata ->> 'source_position') AS source_position,
                    metadata
                FROM filtered_chunks
                ORDER BY embedding <=> %s
                LIMIT %s
                """,
                (
                    normalized_types,
                    normalized_types,
                    tenant_id,
                    query_vector,
                    query_vector,
                    candidate_count,
                ),
            ).fetchall()
            keyword_rows = connection.execute(
                """
                SELECT
                    c.id,
                    d.title,
                    d.document_type,
                    c.heading,
                    c.content,
                    c.citation,
                    d.source_url,
                    d.effective_from,
                    ts_rank_cd(
                        c.search_vector,
                        websearch_to_tsquery('simple', %s)
                    ) AS score,
                    d.version,
                    c.page_no,
                    COALESCE(c.citation, c.metadata ->> 'source_position') AS source_position,
                    jsonb_build_object('document', d.metadata, 'chunk', c.metadata) AS metadata
                FROM legal_chunks c
                JOIN legal_documents d ON d.id = c.document_id
                WHERE
                    d.is_current = true
                    AND (%s::text[] IS NULL OR d.document_type = ANY(%s::text[]))
                    AND (d.document_type <> 'COMPANY_DOCUMENT' OR d.tenant_id = %s)
                    AND (
                        d.document_type <> 'LAW'
                        OR d.effective_from IS NULL
                        OR d.effective_from <= CURRENT_DATE
                    )
                    AND c.search_vector @@ websearch_to_tsquery('simple', %s)
                ORDER BY score DESC, c.id
                LIMIT %s
                """,
                (query, normalized_types, normalized_types, tenant_id, query, candidate_count),
            ).fetchall()
        fused = reciprocal_rank_fusion(
            self._results(vector_rows, "vector"),
            self._results(keyword_rows, "keyword"),
            limit=candidate_count,
            rrf_k=self.rrf_k,
        )
        return rerank_results(
            query,
            fused,
            limit=top_k,
            search_group=search_group,
        )


# 기존 애플리케이션의 import 경로를 깨지 않도록 이전 이름을 호환 별칭으로 유지한다.
LegalRepository = EvidenceRepository
