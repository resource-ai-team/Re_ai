# API

## 상태 확인

`GET /health`

## 법률 근거 검색

`GET /rag/search?q={질문}&top_k=5`

결과에는 문서 유형, 제목, 조문 또는 판례 구획, 인용, 공식 출처 URL, 시행일, 결합 검색
점수와 `retrieval_method`가 포함됩니다. `retrieval_method`는 벡터·키워드 후보 포함 여부와
재정렬 적용을 표시합니다. 검색 실패 시 `503`을 반환합니다.

## 지식그래프

- `GET /kg/ontology`: 클래스·관계·추론 금지 조건. Neo4j 없이 확인 가능.
- `POST /kg/candidates`: `api/schemas/graph.py`의 구조화 입력을 받아 문헌/공정 후보,
  리스크·근거·조회 버전을 반환. 모르는 입력은 `null`로 보존.
- `POST /kg/candidate-pool`: 소재별 전체 후보를 페이지로 조회하고 ReAct 조사 묶음을 반환.
  `api/schemas/kg_pool.py`를 사용. 형태 미확인 후보를 보존하며 최종 K개를 미리 선정하지 않음.
  입력 예시·외부 모델용 보기·커서 사용은 [kg-research-pool.md](kg-research-pool.md) 참고.
- 새 후보 풀의 커서 오류는 `400`, 그래프 버전 변경은 `409`. 부분 결과를 버리고 처음부터 재조회.
- `POST /kg/process-plans`: `api/schemas/kg_planning.py`의 입력으로 소재 상태 전이를 따라
  공정·시설 경로 후보를 열거. 시설 역할 가설·인계 구간·출처·확인 과제와 탐색 한도를 반환.
  [실행 예시와 응답](kg-process-plans.md). 잘못된 목표 상태는 `400`.
  `graph_compatible`은 적합성·최적성 검증 전 상태이며 최종 추천이 아님.
- 설정 누락·미적재·Neo4j 장애·무결성 오류는 `503`과 `detail.code/message`로 반환.
- 입력 검증 실패는 `422`, 미지원 소재는 `200` + `status=unsupported_material`,
  소재 누락은 `200` + `status=needs_input`. 후보가 없음을 적법성/안전성으로 해석하지 않음.

기존 `/rag/search` 계약은 유지했습니다. KG는 Re_server/에이전트용 추가 API이며
사용법과 한계는 [knowledge-graph.md](knowledge-graph.md)를 참고합니다.
