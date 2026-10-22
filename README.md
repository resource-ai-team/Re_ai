# Re:Source AI

공장에서 생긴 폐자원의 **여러 공정·시설 경로를 찾고, 각 단계에서 확인할 조건과 리스크를 보여 주는 AI 서비스**입니다.
이 저장소는 화면 뒤에서 후보 탐색·근거 검색·검증·계산을 맡습니다. 기존 처리 방식은 사용자가 알려준 경우에만 받습니다.

## 처음 보는 팀원은 여기부터

| 알고 싶은 것 | 문서 |
| --- | --- |
| 전체 서비스가 어떤 순서로 동작해야 하나요? | [확정 파이프라인](docs/pipeline-baseline.md) |
| 지금 연결된 기능과 안 된 기능은? | [후보 조사·수치 비교 API](docs/candidate-analysis.md) |
| 실제 KG 후보에 수치를 붙이고 오류를 검사하려면? | [경로 검토·보고서 API](docs/grounded-decision-review.md) |
| 한 사례를 실제 DB로 돌리면 무엇이 나오나요? | [검정 PP 3톤 실행 결과 — 로트는 가정](docs/scenarios/pp-scrap-live-2026-10-22.md) |
| 법률 그래프·Researcher·Auditor는 어떻게 동작하나요? | [법률 그래프와 검증 흐름](docs/legal-hierargraph.md) |
| KG가 공정 순서와 시설 후보를 어떻게 만드나요? | [공정·시설 경로 탐색](docs/kg-process-plans.md) |
| 이번에 KG에서 무엇을 보강했나요? | [실제 제지 순서·등급 차이·빈 연결 검사](docs/kg-evidence-upgrade.md) |
| 팀 작업은 어떻게 나눴나요? | [Notion 진행보드 반영 결과](docs/progress-board-update.md) |
| 데이터·실행·검증 문서는 어디 있나요? | [전체 문서 안내](docs/README.md) |

## 지금 어디까지 되나요?

지금 연결된 흐름은 아래와 같습니다. 시작점은 사용자가 검수한 **구조화된 입력 항목**입니다.

~~~mermaid
flowchart LR
    A[폐자원 종류·형태·양 등] --> B[Neo4j 공정·시설 후보 조회]
    B --> C[시설 공개 조건 대조]
    C --> D[후보별 법령 근거 검색]
    D --> E[같은 로트·KG 경로에 제출 수치 연결]
    E --> F[Python 계산·수치상 Pareto 비교]
    F --> G[연결·계산·비교 오류 독립 검사]
    G --> H[확인 필요·수정 필요와 검토 보고서]
~~~

**POST /analysis/research**는 후보·근거 조사를 실행합니다. **POST /analysis/review-template**은 실제 경로에 맞는 빈 수치 양식을,
**POST /analysis/review**는 조사·제출 수치 계산·오류 검사·검토 보고서를 연결합니다.
자료 부족은 **REVIEW(확인 필요)**, 확인된 연결·계산 오류는 **REVISE(수정 필요)**입니다. 최종 추천을 승인하는 PASS는 아직 없습니다.
기본 흐름은 법령 검색까지입니다. 선택 옵션으로 **법률 그래프 → 세 검색 → 요건별 Auditor → 종합 판단**을 연결했습니다.
이 새 흐름은 가상 모델로 테스트했으며, 실제 생성 모델 연결과 실제 원문의 특징·요건 생성은 아직 실행하지 않았습니다.
실제 허가·인수 검증은 추가 작업입니다. [완료 범위·실행법](docs/legal-hierargraph.md)을 참고하세요.
법령 검색은 로컬 PostgreSQL과 문서 적재가 준비돼 있어야 하며, 실패·검색 결과 없음도 응답에 남깁니다.

| 되는 일 | 아직 필요한 일 |
| --- | --- |
| W2RKG 문헌 관계와 출처 조회 | 문헌 속 기술을 국내 사업장에 적용할 수 있는지 확인 |
| 별도 공개 자료의 상태 전이를 따라 공정·시설 조합 생성 | 현행 허가·투입 규격·실제 인수·여유 용량 검증 |
| 국내 시설 명부에서 추가 조사 대상 검색 | 그 시설의 해당 공정 수행 근거 확보 |
| 단계·운송 구간에 공개 조건과 미확인 연결, 법률 Auditor 코드·가상 검증 | 실제 모델·법률 데이터 보강·전문가 평가와 적합성 종합 판정 |
| 같은 로트·최신 KG·실제 공정/운송 ID에 연결한 수치 계산·Pareto 비교 | 실제 견적·도로거리·LCI 확보 및 원문 적용성 확인 |
| 결정론적 Critic, 수정 시작 단계·영향 범위, Markdown 검토 보고서·지도 마커 데이터 | 의미적 반박·최종 Top K·PASS 게이트·자동 부분 재실행·영구 이력·3D 연동 |

기존 **POST /analysis/compare-scenarios**는 별도 산술 도구로 유지합니다. KG 연결까지 확인하려면 **/analysis/review**를 사용합니다.
같은 물량·단위·시점·경계의 활동량과 단가·배출계수를 제출해야 하며 빈 양식은 모르는 값을 `null`로 남깁니다.
숫자의 출처 내용·현장 적용성을 검수한 것은 아니므로 수치상 Pareto가 곧 실행 가능한 최종 추천은 아닙니다.

법령 자료는 공식 원문 **6문서·119구간**, 로컬 검색 DB **216청크**로 보강했습니다.
구법 적용 기간이 미검수인 자료는 현행 적용 근거로 확정하지 않습니다. 생성 모델 실호출은 요청대로 미실행입니다.
Docker 임시 통신 파일 오류를 복구했고 PostgreSQL도 정상입니다. 기존 6문서·216청크 보존과 실제 검색을 재확인했습니다.

예를 들어 검정 PP 스크랩을 입력하면 파쇄품·펠렛·컴파운드까지의 조사 경로를 찾을 수 있습니다.
각 단계에 공정·시설 근거와 확인할 조건이 붙습니다. 공장 인수 가능성이나 가장 유리한 경로는 추가 검증이 필요합니다.

종이는 실제 공개된 **10개 작업을 5개 공정 단계**로 연결했습니다. HDPE 같은 세부 수지는 PE 소재군과 구분해 남기고,
등급별 인수 조건은 추가 확인으로 표시합니다. **GET /kg/planning-coverage**로 끊긴 경로·시설 미연결을 볼 수 있습니다.
현재 별도 참조 자료는 출처 9건·상태 23개·전이 18개·시설 7곳입니다. W2RKG 전체나 현행 인수 가능 시설 수와는 다릅니다.

목표 흐름은 **입력 → KG 후보 → 법규·시설 검증 → 비용·탄소 → Pareto·잠정 Top K → 독립 Critic → PASS / REVISE / REVIEW → 최종 보고서·지도**입니다.
KG에서 후보를 미리 K개로 자르지 않으며, 핵심 정보가 미확인이면 최종 추천을 보류합니다.

## 빠른 시작

Python 3.11 이상과 Docker Desktop이 필요합니다. 아래는 저장소 루트에서 실행합니다.

Windows PowerShell:

~~~powershell
Copy-Item .env.example .env
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev,embeddings]"
~~~

macOS·Linux:

~~~bash
cp .env.example .env
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev,embeddings]'
~~~

.env에 로컬 **NEO4J_PASSWORD**를 정하고, 공식 법령 수집에 사용할 **LAW_API_OC**를 설정합니다.
두 DB를 실행합니다.

~~~bash
docker compose -f docker-compose.yml -f docker-compose.neo4j.yml up -d
~~~

다음 명령은 가상환경의 실행 파일을 사용합니다. Windows에서는 legal-rag를 .\.venv\Scripts\legal-rag.exe,
resource-kg를 .\.venv\Scripts\resource-kg.exe, uvicorn을 .\.venv\Scripts\uvicorn.exe로 실행하면 활성화 없이 사용할 수 있습니다.

~~~bash
legal-rag init-db
resource-kg import
legal-rag collect
legal-rag validate
legal-rag ingest data/official/legal_documents.jsonl
uvicorn resource_ai.main:app --reload
~~~

- API 설명·시험: http://127.0.0.1:8000/docs
- 프로세스 상태: **GET /health** — DB·데이터가 모두 준비됐다는 확인은 아닙니다.
- 그래프·참조 자료를 갱신했다면 **resource-kg import --rebuild** 후 **resource-kg audit**로 확인합니다. 이전 세대는 보존합니다.
- 국내 명부는 별도 수집·적재합니다. [시설 명부 실행 방법](docs/kg-facility-matching.md)을 참고하세요.

기본 임베딩은 로컬 intfloat/multilingual-e5-small이며 첫 사용에 모델 다운로드가 필요합니다.
EMBEDDING_PROVIDER=hash는 연결·회귀 검사에만 쓰며 실제 의미 검색용은 아닙니다.

## 데이터와 기술에서 각각 남은 것

| 구분 | 채울 것 |
| --- | --- |
| 데이터 | 사업장별 실제 수행 공정, 현행 허가, 해당 로트 인수 규격·물량·견적, 도로 운송 경로, 물질수지, 적용 가능한 LCI·배출계수 |
| 기술 | OCR·입력 검수, 법률 모델 보강·실호출 평가, 의미적 Critic, 실제 적용성 검수·PASS 게이트·잠정 Top K, 자동 부분 재실행·영구 실행 이력·서버·화면 통합 |
| 평가 | 현장·전문가가 검수한 실제 사례 정답셋과 전체 추천 품질 평가 |

실제 자료·가상 입력·가상 산술 예제를 구분합니다. 원본 사용·전송·재배포 조건은 [데이터 관리](data/README.md)를 따릅니다.
키·비밀번호·국가 LCI 원본·로컬 DB 볼륨은 Git에 올리지 않습니다.

## 저장소 구조와 팀 경계

~~~text
src/resource_ai/
├── api/               내부 API와 입출력 스키마
├── knowledge_graph/   온톨로지·Neo4j 적재·후보/시설 조회·공개 조건 대조
├── rag/               공식 문서 수집·청킹·Hybrid Search·Reranking
├── agents/            후보 조사·KG-수치 연결·결정론적 Critic·검토 보고서
├── calculators/       제출된 비용·탄소 수치 계산·Pareto
├── rules/             검수된 법규·시설 판정 엔진의 골격
├── evidence/          근거 모델·출처 연결
├── integrations/      공식 법령 API, 생성 모델 연결 골격
└── core/              설정·의존성·공통 인프라
~~~

화면은 Re_client, 인증·기업·업무 API는 Re_server가 맡습니다. 호출은 **Re_client → Re_server → Re_ai**입니다.
실제 모듈과 데이터 연결은 [구조 설명](docs/architecture.md), 전체 통합 실행은 인프라 저장소의 절차를 따릅니다.

## 검증과 기여

가상환경에서 실행합니다.

~~~bash
ruff check .
ruff format --check .
pytest -q
~~~

일반 CI는 린트·포맷·테스트를 실행하고, KG CI는 실제 Neo4j·PostgreSQL 경계와 데이터 적재·조회도 검사합니다.
가상 법령·테스트 임베딩을 사용하는 검사와 실제 법령·현장 정확도 평가는 구분합니다.
이전 실행 수치와 검증 범위는 [검증 기록 안내](docs/README.md#검증-기록과-이전-설계)에 모았습니다.

2026-10-22 로컬 회귀는 실제 Neo4j를 포함해 **554개와 subtest 24개 통과·PostgreSQL 통합 1개 건너뜀**입니다.
Docker 복구 후 건너뛴 PostgreSQL 통합 검사도 별도로 **1개 통과**했습니다. GitHub CI 두 작업도 통과했습니다.
실제 활성 KG와 가상 수치의 API 검사는 최초 17/19에서 복구 후 **18/19 통과**입니다.
실제 검색은 정상이며, 확장된 자료에서 기존 검색 평가 **6/7**입니다. 허가 범위 질문의 기대 조문 누락 1건은 검색 품질 보강 과제입니다.
실제 견적·LCI·법규 적용성 평가를 통과했다는 뜻은 아닙니다. [검증 기록과 다음 작업](docs/grounded-decision-review.md#검증-기록과-다음-작업-순서)을 함께 봅니다.

[팀 공통 규칙](https://github.com/resource-ai-team/.github/blob/main/CONTRIBUTING.md)을 따릅니다.
**Issue → 작업 브랜치 → develop 대상 PR → CI·필요 리뷰 → 병합** 순서입니다.
API 스키마 변경은 서버 담당자 리뷰·승인 후 병합하며, develop·main에 직접 push하지 않습니다.
develop → main 릴리스는 인프라·서버 담당자가 진행합니다.
