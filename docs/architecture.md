# Re:Source AI 구조

전체 목표는 **[공정·시설 추천 아키텍처 v2](pipeline-baseline.md)**를 기준으로 합니다.
KG 탐색 → 법규·시설 검증 → 비용·탄소 → Trade-off의 잠정 Top K →
최종 Critic → PASS/REVISE/REVIEW → 최종 출력 순서입니다.
Critic 지적은 원인 단계로 돌아가 수정하고 영향받은 후속 결과를 갱신합니다.
조사 내부에 ReAct를 사용할 수 있으며, 규칙·계산·재실행 제어는 코드가 맡습니다.
아래 구현 내용은 KG 기반과 법령 검색이며 새 전체 실행기는 아직 없습니다.

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
새 전체 후보 조회는 `/kg/candidate-pool`과 `get_candidate_pool_page()`를 사용합니다.
기존 제한 조회는 호환성을 위해 유지하며, [조사 묶음 API](kg-research-pool.md)와 구분합니다.
사용자 문서나 서비스 DB는 그래프 빌드에 전달하지 않습니다.

공정 순서·시설 조합은 `/kg/process-plans`에서 조회합니다. 각 단계의
`facility_search_request`로 `/kg/facility-candidates`를 호출하면 기존 공개 공정 근거와
별도 Neo4j 명부의 추가 조사 대상이 나뉘어 나옵니다. 공식 코드표·설비 단어·직선거리는
검색 단서이며, 그 시설의 현행 허가나 이번 물량의 인수 승인이 아닙니다.
명부를 기존 경로에 자동 삽입하지 않으며, 시설 검증 이후의 전체 추천 실행기는 아직 없습니다.
[시설 조사 구조와 실행 방법](kg-facility-matching.md)을 참고합니다.

공개 조건 대조는 `/kg/facility-assessment`, 경로와 단계별 대조 연결은 `/kg/assessed-plans`를
사용합니다. 조건 프로필은 실제 Neo4j의 시설–공정 노드에 들어갑니다. 공개 예시와 명시적
제외 정책을 구분하고 수분·허가·인수 등 미확보 조건은 확인 필요로 남깁니다.
후속 단계는 KG의 예상 상태이며 원래 로트의 측정값을 복사하지 않습니다.
[조건 대조와 검증 사례](kg-facility-conditions.md)를 참고합니다.

검색 데이터는 법령과 판례를 하나의 문서 모델로 저장하되 `document_type`, 조·항·호,
사건번호와 계층 경로 메타데이터로 구분합니다. 원문 파일은 `data/raw`, 전처리 결과는
`data/processed`, 공식 API 재수집 결과는 Git에서 제외되는 `data/official`에 둡니다.
수집 결과의 범위·버전·해시는 함께 생성되는 매니페스트로 확인합니다.
