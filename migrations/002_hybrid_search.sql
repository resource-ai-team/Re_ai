ALTER TABLE legal_chunks
ADD COLUMN IF NOT EXISTS search_vector tsvector
GENERATED ALWAYS AS (
    setweight(to_tsvector('simple', coalesce(heading, '')), 'A') ||
    setweight(to_tsvector('simple', coalesce(citation, '')), 'A') ||
    setweight(to_tsvector('simple', coalesce(content, '')), 'B')
) STORED;

CREATE INDEX IF NOT EXISTS legal_chunks_search_vector_gin_idx
ON legal_chunks USING gin (search_vector);
