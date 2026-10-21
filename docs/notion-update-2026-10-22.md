# AI 진행보드 갱신 원고

**노션 반영 대기 — 아직 저장·정렬되지 않음**

2026-10-22 기준으로 작성한 복사·붙여넣기용 원고입니다. 현재 Notion 연결에서는 팀 보드가 404로 조회되고, 브라우저·컴퓨터 도구는 `kernel assets` 초기화 오류로 실행되지 않아 실제 노션 반영은 완료하지 못했습니다.

- 대상: [AI 진행보드](https://app.notion.com/p/AI-3e4301db7c40805e93a6e45b8560e64c)
- 구현 기준: [고정 커밋 0d5cd69](https://github.com/resource-ai-team/Re_ai/tree/0d5cd69e7fd8dd7f6e976de92b523e0331898737), [PR #24](https://github.com/resource-ai-team/Re_ai/pull/24)
- **17개 카드 구성은 2026-10-21 마지막 확인 기준입니다.** 현재 실제 보드는 404로 재조회하지 못했으므로, 이후 추가 카드나 동료의 변경이 없다고 단정하지 않습니다.
- 실제 반영 직전에 보드와 각 카드의 최신 내용·속성을 다시 읽습니다. 이후 생긴 카드와 동료 수정은 보존하고, 기존 항목을 중복 생성하지 않습니다.
- 담당자·날짜·우선순위·진행 상태는 임의로 바꾸지 않습니다. 구현·검증과 develop 리뷰·병합 완료를 구분합니다.
- 아래 보호 카드 3개는 **화면상의 위치만 옮깁니다.** 제목·본문·상태·담당자·기존 속성을 모두 보존합니다. 제목에 순서 번호도 붙이지 않습니다.
- 기존 체크리스트·GitHub 추적·참고 블록·완료 기준을 유지하고, 이번에 바뀐 현재 상태·남은 일·근거만 갱신합니다.
- [이전 반영 기록](progress-board-update.md)은 2026-10-21에 실제 저장한 내용의 기록이므로 수정하지 않습니다.

## 1. 진행보드 표시 순서

아래는 2026-10-21 마지막으로 확인한 17개 카드의 권장 표시 순서입니다. 실제 처리 흐름을 먼저 놓고, 전체 흐름을 지원하는 공통 작업은 뒤에 놓습니다. 13~17번은 출력 이후 실행되는 단계라는 뜻이 아닙니다. 현재 보드의 새 카드·동료 수정 여부를 다시 확인한 후 적용합니다.

| 표시 순서 | 카드 | 구분 |
| ---: | --- | --- |
| 1 | [입력] 자연어·문서 구조화 및 누락·모순 검수 | 01 입력 |
| 2 | [KG] 폐기물-처리경로 온톨로지 및 그래프 구축 | 02 후보 탐색 · 보호 카드 |
| 3 | [KG] 공정·시설 후보 탐색 및 국내 시설 조사 API | 02 후보 탐색 |
| 4 | [RAG/데이터] 근거 문서 수집·청킹·메타데이터 관리 | 03 법규 검증의 자료 준비 |
| 5 | [Spike/RAG] 로컬 pgvector Retriever 검증 | 03 법규 검증의 검색 기반 · 보호 카드 |
| 6 | [RAG] Hybrid Search·Reranking 및 검색 평가 | 03 법규 검증의 검색 기반 · 보호 카드 |
| 7 | [검증] 후보별 법규·시설 적합성 검증 및 Auditor | 03 법규·시설 검증 |
| 8 | [계산] 후보별 비용·탄소 계산 및 비교 조건 검수 | 04 비용·탄소 |
| 9 | [비교] 적합 후보 Pareto 비교 및 잠정 Top K | 05 비교 |
| 10 | [Critic] 경로·시설·근거·계산·추천 독립 검증 | 06 독립 검증 |
| 11 | [Router] PASS·REVISE·REVIEW 및 부분 재실행 | 07 분기·재실행 |
| 12 | [출력] 최종 공정·시설 추천·근거 보고서·지도 연동 | 08 보고서·지도 |
| 13 | [실행] 전체 파이프라인 상태·오류·이력·서버 연동 | 공통 실행 |
| 14 | [평가] 실제 사례 정답셋 및 전체 파이프라인 검증 | 공통 평가 |
| 15 | [공통] 확정 파이프라인 및 모듈 인터페이스 명세 | 공통 계약 |
| 16 | [공통] Neo4j·pgvector 개발·테스트 환경 및 CI | 공통 환경 |
| 17 | [데이터] 공정·시설·허가·견적·LCI 근거 데이터 보강 | 전 단계의 자료 보강 |

보드에 상태별 열이 있다면 각 열 안에서도 같은 파이프라인 순서를 사용합니다. 상태를 바꿔 카드를 다른 열로 옮기는 정렬은 하지 않습니다.

## 2. 보드 상단에 넣을 요약

### 현재 파이프라인

**입력 정리 → 공정 KG의 여러 공정·시설 후보 → 법규·시설 검증 → 비용·탄소 계산 → Pareto·잠정 Top K → 독립 Critic → PASS / REVISE / REVIEW → 보고서·공정 지도**

공정 KG는 조사할 공정·시설 경로를 만듭니다. 법률 KG는 관련 사례·조문·적용 요건을 연결합니다. 법규 검증 내부는 **Researcher의 세 검색 → Auditor의 요건별 Check·조문별 Decide → 검증된 근거로 조사 요약** 순서입니다.

법률 Auditor는 법규 검증 내부 역할입니다. 전체 공정 경로·시설·비용·탄소·추천 논리를 독립적으로 확인하는 최종 Critic과는 별개입니다.

현재 실제 공정 KG·법령 DB·법률 구조 그래프가 준비됐고, 법률 에이전트 흐름은 가상 응답으로 검증했습니다. **실제 생성 모델 호출은 0회**입니다. 실제 법규 정확도·시설 인수·최종 추천은 아직 검증하지 않았습니다. 열린 PR의 구현·검증과 develop 병합 완료를 구분합니다.

근거: [PR #24](https://github.com/resource-ai-team/Re_ai/pull/24), [고정 커밋의 법률 그래프 설명](https://github.com/resource-ai-team/Re_ai/blob/0d5cd69e7fd8dd7f6e976de92b523e0331898737/docs/legal-hierargraph.md).

## 3. 이번에 갱신할 카드 본문

다음 8개 카드의 **현재 된 것·남은 일·근거**를 갱신하기 위한 원고입니다. 실제 반영 전에 최신 본문을 다시 읽고 기존 목표·의존·완료 기준·체크리스트와 다른 팀원이 작성한 내용을 보존합니다.

### [검증] 후보별 법규·시설 적합성 검증 및 Auditor

**현재 된 것:** PR #24에서 별도 Neo4j 법률 그래프와 Researcher → Auditor → Adjudicator를 구현했다. 특징 의미 검색·단일 커뮤니티 검색·쟁점→사례→조문 검색을 합치고, 요건별 Check 후 별도 Decide로 조문 적용을 검토한다. 부적용 근거는 이번 결과에서만 제외하며 자료 부족·모델 오류는 확인 필요로 보존한다. `/analysis/research`의 선택 필드 `legal_graph`로 공정 후보와 연결했다. 실제 원문은 구조 적재까지, 특징·체크리스트·에이전트 흐름은 가상 모델 검증까지다.

**남은 일:** 실제 모델 연결과 원문 특징·요건 생성, 추가 판례·구법·별표·공식 해석 확보, 전문가 적용성 평가, 현행 허가·로트 규격·실제 인수 조건 확인과 시설 판정 연결. 최종 결과는 REVIEW를 유지한다.

**근거:** [PR #24](https://github.com/resource-ai-team/Re_ai/pull/24), [고정 커밋의 법률 그래프 구현·한계](https://github.com/resource-ai-team/Re_ai/blob/0d5cd69e7fd8dd7f6e976de92b523e0331898737/docs/legal-hierargraph.md), [로컬 설명](legal-hierargraph.md).

기존의 “적용성 Auditor가 없다”는 설명은 위 범위로 바꾼다. 새 법률 검증이 현장 허가·인수 승인이나 전체 Critic 완료를 뜻하지 않는다.

### [RAG/데이터] 근거 문서 수집·청킹·메타데이터 관리

**현재 된 것:** 기존 동료의 RAG 구현으로 공식 법령·판례 4문서·102절을 PostgreSQL에 193청크로 적재했다. multilingual-e5-small 384차원 의미 검색과 기존 Hybrid 검색 회귀 7/7을 확인했다. 같은 원문에서 법률 Neo4j 구조 그래프도 구축했다. 검색 성공은 법규 적용성 검증과 구분한다.

**남은 일:** PDF·OCR·표 구조와 원문 위치 보존, 갱신·중복·실패 관리, 사용 조건 관리. 법률 그래프에 필요한 추가 판례·구법·구조가 보존된 별표·공식 해석 확보.

**근거:** [고정 커밋의 자료 범위·법률 구조 검증](https://github.com/resource-ai-team/Re_ai/blob/0d5cd69e7fd8dd7f6e976de92b523e0331898737/docs/legal-hierargraph.md), [PR #24](https://github.com/resource-ai-team/Re_ai/pull/24), [데이터 관리](../data/README.md). 로컬 실행 근거는 `output/planning/legal-db-runtime-restored-2026-10-21.json`에 별도 보관했다.

기존의 “PostgreSQL 적재·실제 검색 정상화는 남아 있다”는 설명을 갱신한다. 이미 완료된 RAG 두 카드의 본문·상태는 수정하지 않는다.

### [KG] 공정·시설 후보 탐색 및 국내 시설 조사 API

**현재 된 것:** 전체 후보·페이지·상태 전이·시설 조합·국내 명부·공개 조건 API에 더해, PR #21에서 제지의 10개 작업을 5단계로 연결하고 PE 세부 수지·출처 검수 범위·끊긴 경로·시설 미연결 진단을 보강했다. 현재 별도 참조 자료는 출처 9건·상태 23개·전이 18개·시설 7곳·시설–공정 연결 18개다. PR #24의 법률 검증 옵션으로 후보별 추가 조사를 연결했다.

**남은 일:** 리뷰·서버 연동, 사업장별 실제 수행 공정·투입 규격·허가·인수 근거, 탐색 누락 평가. 법률 KG는 공정 KG를 대체하지 않는다. 명부 검색 결과를 인수 가능 시설로 확정하지 않는다.

**근거:** [PR #24](https://github.com/resource-ai-team/Re_ai/pull/24), [고정 커밋의 KG 보강 기록](https://github.com/resource-ai-team/Re_ai/blob/0d5cd69e7fd8dd7f6e976de92b523e0331898737/docs/kg-evidence-upgrade.md), [로컬 KG 보강 설명](kg-evidence-upgrade.md). 기존 PR #11·#13·#15·#17의 추적 정보는 보존한다.

### [데이터] 공정·시설·허가·견적·LCI 근거 데이터 보강

**현재 된 것:** W2RKG 문헌 관계 33,679건과 별도 참조 자료 9출처·23상태·18전이·7시설·18시설–공정 연결, 국내 명부 9,604개 등록 레코드를 확보했다. 공식 법률 자료는 4문서·102구간이며 법률 구조 그래프는 조문·별표 13개·판례 1개·참조 7개·관계 10개다.

**남은 일:** 판례·구법·공식 해석·별표의 구조적 근거, 실제 사업장 허가·투입 규격·서면 인수 조건·물질수지·견적·도로거리·적용 가능한 LCI/배출계수. 등록 명부와 문헌 관계를 현행 허가·실시간 인수 가능성으로 해석하지 않는다.

**근거:** [PR #24](https://github.com/resource-ai-team/Re_ai/pull/24), [고정 커밋의 KG 자료 수](https://github.com/resource-ai-team/Re_ai/blob/0d5cd69e7fd8dd7f6e976de92b523e0331898737/docs/kg-evidence-upgrade.md), [고정 커밋의 법률 그래프 검증 기록](https://github.com/resource-ai-team/Re_ai/blob/0d5cd69e7fd8dd7f6e976de92b523e0331898737/docs/evaluation/legal-hierargraph-2026-10-22.json).

기존의 “상태 19개·전이 14개·조건 프로필 14개”는 최신 23개·18개·18개로 갱신한다. 서로 다른 자료의 개수를 합쳐 검증된 실행 경로나 고유 기업 수로 표시하지 않는다.

### [공통] Neo4j·pgvector 개발·테스트 환경 및 CI

**현재 된 것:** 로컬 Docker PostgreSQL/pgvector 실행과 공식 자료 적재·의미 검색을 정상화했다. 공정 KG와 법률 KG는 별도 Neo4j namespace·세대로 관리한다. 최종 커밋 `0d5cd69`에서 실제 Neo4j·PostgreSQL을 포함한 회귀 482개와 subtest 24개가 통과했다. 법률 구조 그래프 검사 11개도 통과했다. 생성 모델 호출은 0회다.

**남은 일:** 팀원 환경의 재현·설정·모델 캐시·원본 자료 접근 및 Re_server 연동, 실제 생성 모델 연결. GitHub CI 완료는 확인한 결과만 별도로 기록한다.

**근거:** [PR #24의 최종 커밋 검증 결과](https://github.com/resource-ai-team/Re_ai/pull/24), [고정 커밋의 실제 법률 구조 검사](https://github.com/resource-ai-team/Re_ai/blob/0d5cd69e7fd8dd7f6e976de92b523e0331898737/docs/evaluation/legal-hierargraph-2026-10-22.json), [실행 방법](../README.md).

기존의 “로컬 Docker/PostgreSQL 정상화가 남아 있다”는 설명을 갱신한다. 실제 DB 경계 검사와 실제 법규 판단 정확도 평가는 구분한다.

### [실행] 전체 파이프라인 상태·오류·이력·서버 연동

**현재 된 것:** `/analysis/research`가 KG 전체 후보·시설 공개 조건·법령 조사를 연결한다. `legal_graph` 옵션으로 후보별 Researcher·Auditor·Adjudicator까지 연결했다. 후보들은 한 법률 그래프 세대와 모델 호출 한도를 공유한다. 옵션을 생략하면 기존 Hybrid 검색을 유지한다.

**남은 일:** 실제 모델·법률 그래프 보강, 비용·탄소→비교→전체 Critic·Router→최종 출력 연결, 영구 이력·진행 이벤트·취소·재시작 복구·서버 연동. 새 법률 옵션이 전체 오케스트레이터 완료를 뜻하지 않는다.

**근거:** [PR #24](https://github.com/resource-ai-team/Re_ai/pull/24), [고정 커밋의 분석 실행 코드](https://github.com/resource-ai-team/Re_ai/blob/0d5cd69e7fd8dd7f6e976de92b523e0331898737/src/resource_ai/agents/research.py), [법률 그래프 API 사용](legal-hierargraph.md), [현재 구조](architecture.md).

### [평가] 실제 사례 정답셋 및 전체 파이프라인 검증

**현재 된 것:** 실제 DB를 포함한 회귀 482개·subtest 24개, 실제 공식 법률 구조/API 검사 11개, 기존 실제 의미 검색 회귀 7/7을 확인했다. 가상 법령·사례·모델로 세 검색, 요건별 확인·종합 판단, 잘못된 인용·미래 시행일·자료 부족·예산 초과·결과 가지치기·Neo4j 복구를 검사했다.

**남은 일:** 실제 생성 모델 연결 후 특징·요건 추출 및 법규 적용성 평가, 전문가 정답셋, Hybrid 대비 검색 비교, 실제 후보·시설·비용·탄소의 E2E 평가.

**근거:** [PR #24의 회귀 검사 결과](https://github.com/resource-ai-team/Re_ai/pull/24), [고정 커밋의 실제 구조 평가](https://github.com/resource-ai-team/Re_ai/blob/0d5cd69e7fd8dd7f6e976de92b523e0331898737/docs/evaluation/legal-hierargraph-2026-10-22.json), [고정 커밋의 가상 법률 회귀 검사](https://github.com/resource-ai-team/Re_ai/blob/0d5cd69e7fd8dd7f6e976de92b523e0331898737/tests/unit/test_legal_hierargraph.py).

회귀 통과 수·검색 7/7은 법규 판단 정확도나 현장 추천 성능이 아니다. 판례 1건으로 커뮤니티 검색 효과를 주장하지 않는다. 실제 원문의 모델 보강은 아직 수행하지 않았다.

### [공통] 확정 파이프라인 및 모듈 인터페이스 명세

**현재 된 것:** 기존 v2 순서를 유지하면서 법규 검증 내부의 법률 KG·Researcher·Auditor·Adjudicator 책임과 공정 KG/최종 Critic과의 경계를 명세했다. `/legal-graph/status`, `/legal-graph/analyze`, `/analysis/research.legal_graph`를 추가했고 기본 Hybrid 경로를 유지했다.

**남은 일:** Re_server API 변경 리뷰, 단계별 사실·가설·미확인·근거 ID와 오류·예산·상태 계약, 전체 실행·이벤트 계약 합의. 모델 설정은 서버에서만 관리한다.

**근거:** [PR #24](https://github.com/resource-ai-team/Re_ai/pull/24), [고정 커밋의 내부 API](https://github.com/resource-ai-team/Re_ai/blob/0d5cd69e7fd8dd7f6e976de92b523e0331898737/docs/api.md), [고정 커밋의 전체 파이프라인](https://github.com/resource-ai-team/Re_ai/blob/0d5cd69e7fd8dd7f6e976de92b523e0331898737/docs/pipeline-baseline.md), [법률 그래프 설명](legal-hierargraph.md).

## 4. 노션에 반영한 뒤 확인할 것

- 마지막 확인 당시 17개 카드와 현재 보드를 비교해, 이후 추가된 카드·동료 수정·누락·중복을 확인한다. 새 항목을 지우거나 덮어쓰지 않는다.
- 보호 카드 3개의 제목·본문·상태·담당자·기존 속성이 동일한지 확인한다.
- 8개 카드의 갱신 문구와 PR·문서 링크를 다시 읽어 확인한다.
- 실제 원문 구조 적재, 가상 모델 검증, 실제 생성 모델 미실행이 구분되는지 확인한다.
- 입력·계산·비교·Critic·Router·출력은 새로운 구현 완료 근거가 없으므로 순서만 정리한다.
- 저장과 정렬을 실제로 확인한 뒤에만 이 원고의 반영 대기 표시를 완료 기록으로 바꾼다.
