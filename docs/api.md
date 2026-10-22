# API

## 상태 확인

`GET /health`

## 실제 KG 경로에 연결한 검토

- `POST /analysis/review-template`: `lot`·`max_pages`로 실제 KG 경로의 빈 수치 양식을 조회합니다. RAG·생성 모델 호출 없음.
  `input_sha256`·`graph_trace`·공정/인계 ID와 함께 활동량·계수·근거 ID를 `null`로 반환합니다.
- `POST /analysis/review`: `research`와 선택 `numeric`을 받아 후보 조사 → 같은 로트·현재 그래프·순서 대조 → 제출 수치 계산 → 수치상 Pareto → 결정론적 Critic을 실행합니다.
  `numeric`은 양식의 `input_sha256`, `graph_trace`, 기존 계산 계약의 `comparison`을 포함합니다.
  다른 입력·구버전·가짜 공정의 수치는 비교에 넣지 않습니다.
- 자료 부족은 `REVIEW`, 확인된 오류는 `REVISE`와 `router.restart_from`·후속 무효화 범위를 반환합니다.
  자동 재실행은 없으며 `PASS`·최종 추천·발주 승인을 반환하지 않습니다.
- `candidate_reviews`, `map_markers`, `report_markdown`은 확인할 조건·오류·다음 행동을 제공합니다. 실제 3D 화면 연동은 후속입니다.
- 잘못된 목표는 `400`, 그래프 세대 변경은 `409`, DB/무결성 장애는 `503`, 스키마 오류는 `422`입니다.
- [요청 예제·숫자 확보 방법·제한](grounded-decision-review.md). 두 API는 내부용이며 서버 담당자 검토 후 연동합니다.

## 법률 근거 검색

`GET /rag/search?q={질문}&top_k=5`

결과에는 문서 유형, 제목, 조문 또는 판례 구획, 인용, 공식 출처 URL, 시행일, 결합 검색
점수와 `retrieval_method`가 포함됩니다. `retrieval_method`는 벡터·키워드 후보 포함 여부와
재정렬 적용을 표시합니다. 검색 실패 시 `503`을 반환합니다.

## 법률 그래프 검증

- `GET /legal-graph/status`: 별도 법률 Neo4j 세대·원문 수·모델 보강 여부. `analysis_ready=false`면 구조만 적재된 상태.
- `POST /legal-graph/analyze`: 특징·커뮤니티·쟁점의 세 검색 → Auditor Check/Decide → 검증 근거 요약.
  입력은 `query`, `facts`, `top_k`, `max_model_calls`, `max_articles`. 모델·그래프·임베딩 설정은 서버에서만 받음.
- `POST /analysis/research`의 선택 필드 `legal_graph`: 각 공정 후보를 가설로 전달해 위 법규 검증을 실행.
  전체 후보가 모델 호출 한도를 공유하고 후보별 `legal_graph_review`에 결과·미확인·실패를 표시.
  생략하면 기존 Hybrid 검색 유지. 실제 생성 모델은 아직 연결하지 않았음.
- 모델 미연결·외부 전송 비활성·그래프 미적재·구조 전용 그래프·무결성 오류는 `503`과 명시적 `detail.code`.
  항목 검증 중 자료 부족·모델 오류는 `unknown`, 최종 결과는 `REVIEW`. 허가·인수 승인이 아님.
- [입출력 예시·논문 반영 범위](legal-hierargraph.md). 이 API는 내부용이며 Re_server 검토 후 연동.

## 공정·시설 지식그래프

- `GET /kg/ontology`: 클래스·관계·추론 금지 조건. Neo4j 없이 확인 가능.
- `GET /kg/planning-coverage`: 활성 Neo4j 공정 참조 자료의 수·끊긴 가지·도달 불가 산출물·시설 미연결·출처 메타데이터.
  계약 `kg-planning-coverage-v1`. 등록 그래프 범위의 연결 검사이며 실행 가능성·적법성 판정이 아님.
  실제 Neo4j 조회·무결성 대조가 필요하며 장애·미적재·불일치는 `503`. [필드 의미](kg-evidence-upgrade.md).
- `POST /kg/facility-assessment`: 한 `capability_key`와 로트 조건을 공개 프로필에 대조.
- `POST /kg/assessed-plans`: 공정 경로 생성과 단계별 조건 대조를 연결. 원래 로트의 값을
  후속 단계의 실측값으로 복사하지 않음. 위험은 공정·인계 구간 ID에 연결.
  두 API는 내부용이며 현행 허가·인수·적법성 승인이 아님. [실행 예시](kg-facility-conditions.md).
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
- `POST /kg/facility-candidates`: 공정 전이 key로 공개 시설 연결과 별도 명부 조사 대상을 조회.
  `api/schemas/facility_search.py`를 사용. 소재 코드·설비 단어·직선거리는 검색 단서이며
  현행 허가·공정 수행·인수 가능성 판정이 아님. [실행 예시](kg-facility-matching.md).
  등록 명부가 없으면 `reference_only`, 변조된 명부·코드표는 `503`.
  외부 모델 보기에서는 명부 본문을 가리며, 지역·발생지 좌표 조건은 내부 검색에서만 사용.
- 설정 누락·미적재·Neo4j 장애·무결성 오류는 `503`과 `detail.code/message`로 반환.
- 입력 검증 실패는 `422`, 미지원 소재는 `200` + `status=unsupported_material`,
  소재 누락은 `200` + `status=needs_input`. 후보가 없음을 적법성/안전성으로 해석하지 않음.

기존 `/rag/search` 계약은 유지했습니다. KG는 Re_server/에이전트용 추가 API이며
사용법과 한계는 [knowledge-graph.md](knowledge-graph.md)를 참고합니다.
