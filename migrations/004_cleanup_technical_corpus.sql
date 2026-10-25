-- factory·recipient 공개 근거는 KG 후보 탐색용이며 Technical RAG 대상이 아니다.
-- 출처 추적을 위해 행은 보존하고 현재 검색에서만 제외합니다.
UPDATE legal_documents AS document
SET is_current = false,
    updated_at = now()
WHERE document.document_type = 'TECHNICAL_LITERATURE'
  AND document.source_id LIKE 'public-evidence:%'
  AND NOT EXISTS (
      SELECT 1
      FROM legal_chunks AS chunk
      WHERE chunk.document_id = document.id
        AND chunk.metadata ->> 'collection_lane' = 'technical'
  );
