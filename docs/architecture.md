# Re:Source AI 구조

`src/resource_ai`는 기능 책임별로 분리합니다.

- `rag`: 공식 문서 수집, 구조 보존 파싱, 임베딩, PostgreSQL/pgvector 검색
- `knowledge_graph`: 폐기물·처리방법·업체·허가의 관계와 조문 후보 탐색
- `rules`: 허가·처리 기준을 결정적으로 판정하는 규칙
- `agents`: 그래프, 규칙, RAG를 질문에 맞게 호출하는 오케스트레이션
- `evidence`: 답변에 표시할 조문·판례·출처 근거

현재 구현 범위는 로컬 RAG 검색까지입니다. 빈 모듈은 이후 작업 경계를 표시하는
골격이며, 구현 순서는 RAG 검색 기준선 → 지식 그래프 → 규칙 엔진 → Agent 통합입니다.

검색 데이터는 법령과 판례를 하나의 문서 모델로 저장하되 `document_type`, 조·항·호,
사건번호와 계층 경로 메타데이터로 구분합니다. 원문 파일은 `data/raw`, 전처리 결과는
`data/processed`, 공식 API 재수집 결과는 Git에서 제외되는 `data/official`에 둡니다.
