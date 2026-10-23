-- Keep the original table names for backward-compatible migrations. They now hold
-- every supported evidence corpus, not only legal documents.
ALTER TABLE legal_documents
ADD COLUMN IF NOT EXISTS tenant_id TEXT;

ALTER TABLE legal_documents
ADD COLUMN IF NOT EXISTS tenant_key TEXT
GENERATED ALWAYS AS (COALESCE(tenant_id, '')) STORED;

ALTER TABLE legal_documents
DROP CONSTRAINT IF EXISTS legal_documents_source_id_key;

ALTER TABLE legal_documents
DROP CONSTRAINT IF EXISTS evidence_documents_source_tenant_key;

ALTER TABLE legal_documents
ADD CONSTRAINT evidence_documents_source_tenant_key UNIQUE (source_id, tenant_key);

ALTER TABLE legal_documents
DROP CONSTRAINT IF EXISTS evidence_documents_supported_corpus_check;

ALTER TABLE legal_documents
ADD CONSTRAINT evidence_documents_supported_corpus_check
CHECK (document_type IN (
    'LAW',
    'PRECEDENT',
    'TECHNICAL_LITERATURE',
    'HISTORICAL_CASE',
    'COMPANY_DOCUMENT'
));

ALTER TABLE legal_documents
DROP CONSTRAINT IF EXISTS evidence_documents_company_tenant_check;

ALTER TABLE legal_documents
ADD CONSTRAINT evidence_documents_company_tenant_check
CHECK (document_type <> 'COMPANY_DOCUMENT' OR tenant_id IS NOT NULL);

CREATE INDEX IF NOT EXISTS evidence_documents_corpus_current_idx
ON legal_documents (document_type, is_current);

CREATE INDEX IF NOT EXISTS evidence_documents_tenant_corpus_current_idx
ON legal_documents (tenant_id, document_type, is_current)
WHERE tenant_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS evidence_chunks_document_id_idx
ON legal_chunks (document_id);

CREATE INDEX IF NOT EXISTS evidence_chunks_metadata_gin_idx
ON legal_chunks USING gin (metadata);
