# API

## 상태 확인

`GET /health`

## RAG 근거 검색

`GET /rag/search?q={질문}&top_k=5&search_group=LEGAL`

`search_group`은 다음 세 그룹을 지원합니다.

| 값 | 검색 문서 |
| --- | --- |
| `LEGAL` | 법령·판례 |
| `TECHNICAL` | 기술문헌 |
| `COMPANY` | 기업 업로드 문서 |

`COMPANY` 검색에는 `tenant_id`가 필수이며 해당 기업 문서만 반환합니다.
`search_group`을 생략하는 기존 호출도 호환됩니다.
`tenant_id`는 외부 사용자 입력을 그대로 신뢰하지 않고 인증을 마친 Re_server가 전달해야 합니다.

`GET /rag/search?q={질문}&search_group=COMPANY&tenant_id={기업 ID}`

결과에는 문서 유형, 제목, 구획, 인용, 출처 URL, 시행일, 문서 버전, 페이지·원문 위치,
결합 검색 점수와 `retrieval_method`가 포함됩니다. 벡터·키워드 후보를 RRF로 결합한 뒤
LEGAL은 공식 출처·시행일·인용 위치, TECHNICAL은 제목·공정 문맥·원문 위치,
COMPANY는 시험 항목·현재 문서 버전·원문 위치를 기준으로 재정렬합니다. 잘못된 검색 조건은
`400` 또는 `422`, 검색 장애는 `503`을 반환합니다.

## 지식그래프

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

## KG reference와 RAG 근거 검증

`POST /analysis/reference-validation`

KG 후보 풀을 첫 페이지부터 조회하고 후보에 연결된 Evidence ID, reference 문자열과 DOI 후보를
Technical RAG 검색 결과에 연결합니다. 단순 DOI·URL 일치는 논문 내용의 지지를 뜻하지 않으므로
`UNKNOWN`을 유지합니다. 검수된 RAG 청크가 `kg_evidence_ids`와 `kg_claim_status`를 명시한
경우에만 `SUPPORTED`, `CONFLICT`, `SCOPE_MISMATCH`를 확정합니다.

근거가 `UNKNOWN`이면 `max_critic_retries` 범위에서 출처·원문 위치를 포함해 재검색합니다.
충돌은 `REVISE`, 미확인은 `REVIEW`, 모든 근거가 명시적으로 지지된 후보만 `PASS`입니다.
응답에는 KG Evidence ID, RAG Chunk ID, 출처 URL과 인용 위치가 포함됩니다. 원문 이용 권한이
없는 자료를 이 API가 새로 수집하거나 저장하지는 않습니다.
