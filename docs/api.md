# API

## 법령 근거 동반 조회

LEGAL 검색과 명시한 법률 코퍼스 검색은 일반 근거와 원문에서 인용한 상위 근거를 함께 조회합니다. 정확한 현행 법령명·한 조문을 명시하면 직접 조회도 사용합니다. 검색 완료는 법규 적용성 검증 완료가 아닙니다.

- `/rag/search`와 파이프라인 법령 결과의 `evidence_role`: `retrieved`, `cited_basis`, `explicit_locator`.
- `reference_sources`: 상위 근거를 명시적으로 인용한 공개 원문 조각 ID. `top_k`는 동반 근거를 포함한 최종 개수입니다.
- 응답 순서는 근거 역할을 고려합니다. `score`는 법적 적합성 확률이 아니며 서로 다른 검색 경로의 절대 점수로 비교하지 않습니다.
- `legal_context.alias_definitions`: 같은 현재 문서에서 대조한 법·영·규칙 별칭 정의와 출처.
- Critic `coverage`: 사용 가능·선택·제외 법령 조각 수와 문서별 제외 수를 표시합니다. 전송하지 않은 근거의 인용은 거절합니다.

기본 `RETRIEVAL_LEGAL_BASIS=true`입니다. 배포 전 `resource-rag init-db`와 `scripts/build_legal_aliases.py`로 기존 문서의 정의 메타데이터를 준비하세요. 요청 계약은 유지되며 추가 필드·설정·검증 범위는 [법령 검색 보강](grounded-rag-2026-10-27.md)을 참고하세요.

## 상태 확인

`GET /health`

## 실제 KG 경로에 연결한 검토

`/analysis/review`와 `/analysis/site-review`에 선택 `semantic_critic: {"max_findings": 4}`를 추가할 수 있습니다.
서버의 GPT Critic·외부 전송 설정과 **X-Critic-Service-Token** 헤더가 있어야 실제 모델 호출을 실행합니다.
기본은 호출하지 않으며, 요청했지만 비활성/차단/자료 한도이면 결과에 그 상태를 남깁니다.
`semantic_critic` 결과와 `critic[].origin`·`semantic_finding_id`로 모델 제안과 코드 검사를 구분합니다.
제안은 인용·ID 대조를 통과해도 확인 사항이며 PASS·확정 오류·발주 승인으로 승격하지 않습니다.
현재 실제 GPT의 반박 정확도는 미검증입니다. [설정·전송 목록·상태·한도](semantic-critic.md).

공정 검토 응답에 `research.legal_baselines`와 후보의 `legal_baseline_tasks`가 추가됐습니다.
벡터 검색과 별도로 현재 로컬 공개 법령의 기본 조문 10개를 정확한 법령·조문 ID로 조회합니다.
확보는 적용성 검증이 아닙니다. 조회 일부 누락·버전 충돌·제목/인용 변경을 표시하며,
확인된 조문 연결 오류는 검증 단계 `REVISE`입니다. [필드 의미와 제한](pipeline-followup-2026-10-25.md).

`review.map_marker_groups`는 후보·단계/인계별 배지용 집계입니다. `issue_count`, `error_count`,
`confirmation_count`, `marker_ids`, `evidence_ids`를 제공합니다. 원래 `map_markers`·`critic`은 유지합니다.
전역 항목은 지도 집계에서 제외하므로 기존 critic 목록도 함께 표시해야 합니다.

- `POST /analysis/site-review`: `site-discovery` 요청에 `max_legal_queries`(기본 12), `max_technical_queries`(기본 6),
  `include_lci`(기본 true), 선택 `numeric`·`legal_graph`를 추가해 사업장 조사→근거 검색→수치 검토→선택 LCI를 묶습니다.
  응답의 `site`, `review`, `lci`, `report_markdown`을 사용합니다. 단계 사이 KG 세대가 달라지면 409입니다.
  명부 후보를 공정에 자동 삽입하거나 LCI 예비값을 총계에 합산하지 않습니다. `include_lci=false`면 로컬 LCI 파일도 열지 않습니다.
  도로 옵션은 기존 서버 허용 설정·키·Bearer 인증을 따릅니다. 생성 모델은 `legal_graph` 생략 시 호출하지 않습니다.
  `REVIEW/REVISE`만 반환합니다. [실제 검증·남은 범위](pipeline-validation-2026-10-25.md).
- `POST /analysis/site-discovery`: `origin` 사업장 정보와 `lot`으로 KG의 공정별 시설 명부를 직선거리순 조회합니다.
  전체 매칭 목록을 정렬한 뒤 페이지를 받고 KG·명부·코드표 버전과 부분 조회 범위를 확인합니다.
  `road_routing` 옵션과 서버 키·허용 설정·Bearer 인증이 있으면 한정한 시설의 도로 경로를 조회합니다.
  옵션 생략 시 직선거리 조사입니다. 사업장 권한·좌표 검수는 Re_server 책임입니다. 법규 검색·생성 모델·견적은 호출하지 않습니다.
  `REVIEW`·빈 최종 추천·`dispatch_approved=false`를 반환합니다. [요청·결과·실제 DB 검사](site-discovery-api.md).
- `POST /analysis/road-routes`: 실제 선택한 시설 간 연결 구간의 카카오/네이버 도로거리·시간·선택 geometry 조회.
  chain은 모든 구간이 연결되고 조회 성공한 경우만 합계를 반환합니다. 키 미설정·실패를 직선거리로 대체하지 않습니다.
  지도 키가 없어 실호출 미검증이며 비용·화물차 통행 가능성·허가를 확정하지 않습니다. [설정·입출력](road-routing.md).
- `POST /analysis/review-template`: `lot`·`max_pages`로 실제 KG 경로의 빈 수치 양식을 조회합니다. RAG·생성 모델 호출 없음.
  `input_sha256`·`graph_trace`·공정/인계 ID와 함께 활동량·계수·근거 ID를 `null`로 반환합니다.
  선택한 `site_context_sha256`을 양식과 `numeric`에 보존합니다. `/analysis/review`의 동일 필드 또는 site-review가
  새로 계산한 맥락과 다르면 수치를 제외합니다. 둘 다 생략한 기존 호출은 사업장 미연결로 명시됩니다. 서버 제출 해시이며 인증 수단은 아닙니다.
- `POST /analysis/review`: `research`와 선택 `numeric`을 받아 후보 조사 → 같은 로트·현재 그래프·순서 대조 → 제출 수치 계산 → 수치상 Pareto → 결정론적 Critic을 실행합니다.
  `numeric`은 양식의 `input_sha256`, `graph_trace`, 기존 계산 계약의 `comparison`을 포함합니다.
  다른 입력·구버전·가짜 공정의 수치는 비교에 넣지 않습니다.
- 자료 부족은 `REVIEW`, 확인된 오류는 `REVISE`와 `router.restart_from`·후속 무효화 범위를 반환합니다.
  자동 재실행은 없으며 `PASS`·최종 추천·발주 승인을 반환하지 않습니다.
- `candidate_reviews`, `map_markers`, `report_markdown`은 확인할 조건·오류·다음 행동을 제공합니다. 실제 3D 화면 연동은 후속입니다.
- 잘못된 목표는 `400`, 그래프 세대 변경은 `409`, DB/무결성 장애는 `503`, 스키마 오류는 `422`입니다.
- [요청 예제·숫자 확보 방법·제한](grounded-decision-review.md). 두 API는 내부용이며 서버 담당자 검토 후 연동합니다.

`/analysis/research`와 review의 `research`에는 `max_technical_queries`(기본 0, 최대 50), `technical_top_k`(기본 3, 최대 5)를
지정할 수 있습니다. 공정별 `technical_tasks`·`technical_searches`·검색 범위와 문서 버전·페이지·원문 위치를 보존합니다.
기술 자료 미확보·한도 소진·적용성 미검수는 공정별 확인 과제입니다. 법규 적합 판정으로 사용하지 않습니다.

로트 수량은 bool·비유한값을 받지 않으며 `0 < quantity_kg <= 1e12`입니다. 스키마 오류 422의 `detail`은
`loc/type/msg`만 포함하고 제출 원문 `input`·`ctx`는 제외합니다. NaN·Infinity가 오류 응답 직렬화에서 500을 일으키지 않습니다.

## RAG 근거 검색

후보 풀은 최종 Top K와 별도로 최대 100개까지 설정할 수 있습니다. 기본값은 기존 규칙 정렬입니다.
선택한 로컬 ONNX 재정렬 모드에서는 `reranker_score`·`reranker_model`이 추가됩니다.
이 값은 신경망 원점수·버전이며 적법성 확률이 아닙니다. 누락 모델·실행 장애는 `503`으로 표시합니다.
실험한 MiniLM은 검증용 법령 점수가 떨어져 기본 적용하지 않았습니다.

파이프라인의 `research.legal_searches[].results[].legal_context`는 같은 버전의 조문 전체와
명시된 참조 조문·별표를 보충합니다. 자료 누락·불명확한 위임·한도 초과는 `unresolved`·`source_checks`에 남습니다.
`/rag/search` 자체는 이 확장을 수행하지 않습니다. [범위와 필드](rag-hardening-2026-10-26.md).

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

## 법률 그래프 검증

- `GET /legal-graph/status`: 별도 법률 Neo4j 세대·원문 수·모델 보강 여부. `analysis_ready=false`면 구조만 적재된 상태.
- `POST /legal-graph/analyze`: 특징·커뮤니티·쟁점의 세 검색 → Auditor Check/Decide → 검증 근거 요약.
  입력은 `query`, `facts`, `top_k`, `max_model_calls`, `max_articles`. 모델·그래프·임베딩 설정은 서버에서만 받음.
- `POST /analysis/research`의 선택 필드 `legal_graph`: 각 공정 후보를 가설로 전달해 위 법규 검증을 실행.
  전체 후보가 모델 호출 한도를 공유하고 후보별 `legal_graph_review`에 결과·미확인·실패를 표시.
  생략하면 기존 Hybrid 검색 유지. Azure Astra high 배포·실제 합성 호출 검증 완료. 기업 자료 전송은 아직 비활성. [연결 기록](azure-model.md) 참고.
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

## KG 연계 LCI 예비 산정

`POST /analysis/kg-lci`: 구조화한 폐기물 로트 입력으로 Neo4j의 전체
`assessed-plans` 경로를 페이지 끝까지 조회하고, 서버에 설치된 국가 LCI DB를
정확히 일치하는 공정 경로에만 매핑해 **예비 탄소값·미산정 사유·KG ID·출처**를 반환.
기능단위·수거운송 경계가 서로 다른 후보의 자동 순위화는 수행하지 않음.
원본 데이터가 없으면 `UNAVAILABLE`, 그래프 조회 세대·페이지 불일치는 `409`.

[입력·출력·경계·실행 예시](kg-lci-integration.md).
기존 `/analysis/research`, `/analysis/compare-scenarios`는 변경되지 않음.
