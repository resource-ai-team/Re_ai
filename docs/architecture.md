# Re:Source AI 구조

`src/resource_ai`는 기능 책임별로 분리합니다.

- `rag`: 공식 문서 수집·검증, 구조 보존 파싱, PostgreSQL Hybrid Search
- `knowledge_graph`: 폐기물·처리방법·업체·허가의 관계와 조문 후보 탐색
- `rules`: 허가·처리 기준을 결정적으로 판정하는 규칙
- `agents`: 그래프, 규칙, RAG를 질문에 맞게 호출하는 오케스트레이션
- `evidence`: 답변에 표시할 조문·판례·출처 근거

현재 구현 범위는 공식 법률 데이터 수집·검증, 로컬 Hybrid Search와 Neo4j 공정 후보 탐색입니다. 검색은
multilingual-e5 벡터 후보와 PostgreSQL `tsvector` 키워드 후보를 각각 구한 뒤 RRF로
결합하고, 공식 출처·시행일·제목/조문 일치도를 기준으로 재정렬합니다. 빈 모듈은 이후
작업 경계를 표시하는 골격이며, 구현 순서는 검색 기준선 → 지식 그래프 → 규칙 엔진 →
Agent 통합입니다. KG 구현·데이터 범위와 후속 작업은 [knowledge-graph.md](knowledge-graph.md)에 있습니다.

KG는 `ontology.py`의 타입·출처 검증 → `builder.py`의 격리된 메모리 적재 준비 →
`graph_store.py`의 Neo4j 세대 적재·감사 → `query.py`의 고정 파라미터 조회 순서입니다.
`agents/tools/graph_tool.py`와 `/kg/candidates`는 같은 서비스를 호출합니다.
사용자 문서나 서비스 DB는 그래프 빌드에 전달하지 않습니다.

검색 데이터는 법령과 판례를 하나의 문서 모델로 저장하되 `document_type`, 조·항·호,
사건번호와 계층 경로 메타데이터로 구분합니다. 원문 파일은 `data/raw`, 전처리 결과는
`data/processed`, 공식 API 재수집 결과는 Git에서 제외되는 `data/official`에 둡니다.
수집 결과의 범위·버전·해시는 함께 생성되는 매니페스트로 확인합니다.
