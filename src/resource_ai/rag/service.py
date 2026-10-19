import json
from pathlib import Path

from pgvector import Vector

from resource_ai.evidence.models import LegalDocument, SearchResult
from resource_ai.rag.ingestion.chunker import chunk_text
from resource_ai.rag.retrieval.embeddings import EmbeddingProvider
from resource_ai.rag.retrieval.reranker import rerank_legal_results
from resource_ai.rag.retrieval.retriever import reciprocal_rank_fusion
from resource_ai.rag.retrieval.vector_store import Database


class LegalRepository:
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
        # The vector type does not exist until this migration creates the extension.
        with self.database.connect(register_vector_types=False) as connection:
            for path in migration_files:
                connection.execute(path.read_text(encoding="utf-8"))

    def ingest(self, document: LegalDocument) -> int:
        chunk_records: list[tuple[str, str, str | None, int | None, dict]] = []
        for section in document.sections:
            for chunk in chunk_text(section.content):
                metadata = {**section.metadata, "chunk_index": chunk.index}
                chunk_records.append(
                    (section.heading, chunk.content, section.citation, section.page_no, metadata)
                )
        vectors = self.embeddings.embed_documents([record[1] for record in chunk_records])

        with self.database.connect() as connection:
            row = connection.execute(
                """
                INSERT INTO legal_documents (
                    source_id, document_type, title, source_url, source_org,
                    document_no, effective_from, version, metadata
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                ON CONFLICT (source_id) DO UPDATE SET
                    document_type = EXCLUDED.document_type,
                    title = EXCLUDED.title,
                    source_url = EXCLUDED.source_url,
                    source_org = EXCLUDED.source_org,
                    document_no = EXCLUDED.document_no,
                    effective_from = EXCLUDED.effective_from,
                    version = EXCLUDED.version,
                    metadata = EXCLUDED.metadata,
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

    @staticmethod
    def _results(rows: list[tuple], method: str) -> list[SearchResult]:
        return [SearchResult(*row, retrieval_method=method) for row in rows]

    def search(self, query: str, top_k: int = 5) -> list[SearchResult]:
        if top_k <= 0:
            raise ValueError("top_k must be positive")
        query_vector = Vector(self.embeddings.embed_query(query))
        candidate_count = max(top_k * self.candidate_multiplier, top_k)
        with self.database.connect() as connection:
            vector_rows = connection.execute(
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
                    1 - (c.embedding <=> %s) AS score
                FROM legal_chunks c
                JOIN legal_documents d ON d.id = c.document_id
                WHERE
                    d.is_current = true
                    AND (
                        d.document_type <> 'LAW'
                        OR d.effective_from IS NULL
                        OR d.effective_from <= CURRENT_DATE
                    )
                ORDER BY c.embedding <=> %s
                LIMIT %s
                """,
                (query_vector, query_vector, candidate_count),
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
                    ) AS score
                FROM legal_chunks c
                JOIN legal_documents d ON d.id = c.document_id
                WHERE
                    d.is_current = true
                    AND (
                        d.document_type <> 'LAW'
                        OR d.effective_from IS NULL
                        OR d.effective_from <= CURRENT_DATE
                    )
                    AND c.search_vector @@ websearch_to_tsquery('simple', %s)
                ORDER BY score DESC, c.id
                LIMIT %s
                """,
                (query, query, candidate_count),
            ).fetchall()
        fused = reciprocal_rank_fusion(
            self._results(vector_rows, "vector"),
            self._results(keyword_rows, "keyword"),
            limit=candidate_count,
            rrf_k=self.rrf_k,
        )
        return rerank_legal_results(query, fused, limit=top_k)
