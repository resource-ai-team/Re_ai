CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS legal_documents (
    id BIGSERIAL PRIMARY KEY,
    source_id TEXT NOT NULL UNIQUE,
    document_type VARCHAR(40) NOT NULL,
    title TEXT NOT NULL,
    source_url TEXT NOT NULL,
    source_org TEXT,
    document_no TEXT,
    effective_from DATE,
    version TEXT,
    is_current BOOLEAN NOT NULL DEFAULT true,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS legal_chunks (
    id BIGSERIAL PRIMARY KEY,
    document_id BIGINT NOT NULL REFERENCES legal_documents(id) ON DELETE CASCADE,
    heading TEXT NOT NULL,
    content TEXT NOT NULL,
    citation TEXT,
    page_no INTEGER,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    embedding vector(384) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS legal_chunks_document_id_idx
ON legal_chunks (document_id);

CREATE INDEX IF NOT EXISTS legal_chunks_embedding_hnsw_idx
ON legal_chunks USING hnsw (embedding vector_cosine_ops);

CREATE INDEX IF NOT EXISTS legal_chunks_metadata_gin_idx
ON legal_chunks USING gin (metadata);

