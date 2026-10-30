-- Keep the previous column for older clients; raw quoted content is unchanged.
ALTER TABLE legal_chunks
ADD COLUMN IF NOT EXISTS context_search_vector tsvector
GENERATED ALWAYS AS (
    to_tsvector('simple', coalesce(heading, '') || ' ' || coalesce(citation, '') || ' ' || content)
) STORED;

CREATE INDEX IF NOT EXISTS legal_chunks_context_search_idx
ON legal_chunks USING gin (context_search_vector);
