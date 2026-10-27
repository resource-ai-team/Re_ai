-- Workflow state is separate from the Neo4j process graph and public RAG corpus.
CREATE TABLE IF NOT EXISTS workflow_provider_slots (
    scope TEXT PRIMARY KEY,
    last_started TIMESTAMPTZ NOT NULL,
    active_until TIMESTAMPTZ NOT NULL
);
CREATE TABLE IF NOT EXISTS workflow_cases (
    tenant_id TEXT NOT NULL,
    case_id UUID NOT NULL,
    revision INTEGER NOT NULL CHECK (revision > 0),
    state JSONB NOT NULL,
    active_operation UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, case_id)
);
CREATE TABLE IF NOT EXISTS workflow_operations (
    operation_id UUID PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    case_id UUID NOT NULL,
    idempotency_key TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('running','completed','failed')),
    result JSONB,
    error_code TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ,
    UNIQUE (tenant_id, case_id, idempotency_key),
    FOREIGN KEY (tenant_id, case_id) REFERENCES workflow_cases ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS workflow_revisions (
    tenant_id TEXT NOT NULL,
    case_id UUID NOT NULL,
    revision INTEGER NOT NULL,
    state JSONB NOT NULL,
    state_sha256 TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, case_id, revision),
    FOREIGN KEY (tenant_id, case_id) REFERENCES workflow_cases ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS workflow_documents (
    tenant_id TEXT NOT NULL,
    case_id UUID NOT NULL,
    document_id TEXT NOT NULL,
    media_type TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    content BYTEA NOT NULL CHECK (octet_length(content) <= 6000000),
    PRIMARY KEY (tenant_id, case_id, document_id),
    FOREIGN KEY (tenant_id, case_id) REFERENCES workflow_cases ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS workflow_ocr_claims (
    tenant_id TEXT NOT NULL,
    case_id UUID NOT NULL,
    document_sha256 TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY(tenant_id,case_id,document_sha256),
    FOREIGN KEY(tenant_id,case_id) REFERENCES workflow_cases ON DELETE CASCADE
);
