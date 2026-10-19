# API

## 상태 확인

`GET /health`

## 법률 근거 검색

`GET /rag/search?q={질문}&top_k=5`

결과에는 문서 유형, 제목, 조문 또는 판례 구획, 인용, 공식 출처 URL, 시행일, 결합 검색
점수와 `retrieval_method`가 포함됩니다. `retrieval_method`는 벡터·키워드 후보 포함 여부와
재정렬 적용을 표시합니다. 검색 실패 시 `503`을 반환합니다.
