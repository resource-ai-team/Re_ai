-- Optional local Korean lexical lane; original quotes and E5 vectors are unchanged.
ALTER TABLE legal_chunks ADD COLUMN IF NOT EXISTS korean_search_vector tsvector;
CREATE INDEX IF NOT EXISTS legal_chunks_korean_search_idx
ON legal_chunks USING gin (korean_search_vector);
