# Re:Source AI 구조

`src/resource_ai`는 기능 책임별로 분리합니다.

- `rag`: 공식 문서 수집·검증, 구조 보존 파싱, PostgreSQL Hybrid Search
- `knowledge_graph`: 폐기물·처리방법·업체·허가의 관계와 조문 후보 탐색
- `rules`: 허가·처리 기준을 결정적으로 판정하는 규칙
- `agents`: 그래프, 규칙, RAG를 질문에 맞게 호출하는 오케스트레이션
- `evidence`: 답변에 표시할 조문·판례·출처 근거

현재 구현 범위는 공식 법률 데이터 수집·검증과 로컬 Hybrid Search까지입니다. 검색은
multilingual-e5 벡터 후보와 PostgreSQL `tsvector` 키워드 후보를 각각 구한 뒤 RRF로
결합하고, 공식 출처·시행일·제목/조문 일치도를 기준으로 재정렬합니다. 빈 모듈은 이후
작업 경계를 표시하는 골격이며, 구현 순서는 검색 기준선 → 지식 그래프 → 규칙 엔진 →
Agent 통합입니다.

검색 데이터는 법령과 판례를 하나의 문서 모델로 저장하되 `document_type`, 조·항·호,
사건번호와 계층 경로 메타데이터로 구분합니다. 원문 파일은 `data/raw`, 전처리 결과는
`data/processed`, 공식 API 재수집 결과는 Git에서 제외되는 `data/official`에 둡니다.
수집 결과의 범위·버전·해시는 함께 생성되는 매니페스트로 확인합니다.
