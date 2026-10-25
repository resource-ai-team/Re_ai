# Re:Source AI

중소 제조기업의 폐자원 처리 의사결정을 지원하는 **AI·지식 기반 서비스 레포**입니다.
공정·시설 후보 탐색, 근거 문서 검색, 법규·시설 검증 지원, 비용·탄소 계산을 담당합니다.

- 전체 실행 흐름: [docs/pipeline-baseline.md](docs/pipeline-baseline.md)
- 코드와 데이터 구조: [docs/architecture.md](docs/architecture.md)
- 법률 그래프·Researcher·Auditor: [구현·가상 검증·실제 모델 미연결 범위](docs/legal-hierargraph.md)

## 지금 연결된 기능

**입력 항목 → KG 공정·시설 후보 → 공개 시설 조건·법령 검색 → 제출 수치 연결·계산 → 독립 오류 검사 → 확인 사항·검토 보고서**까지 실행됩니다.

| 기능 | 지금 되는 것 | 남은 것 |
| --- | --- | --- |
| 후보 조사 | 등록 KG의 여러 공정 순서와 시설 연결·근거·전체 페이지 조회 | 사업장별 실제 수행 공정·현행 허가·이번 로트 인수 확인 |
| 주변 시설 조사 | 사업장 → 공정별 명부 → 선택한 일부 시설의 카카오/네이버 도로거리 조회 코드 | 지도 키·실도로 호출 검증·사업장 선택/권한·시설별 수행 근거 |
| RAG | 동료가 구현한 멀티 코퍼스·기업 문서 격리·공개 문헌 적재 자동화. 법규 조사 도구는 LAW 범위 사용 | 원문 적용성·핵심 조항 회수·실제 사례 품질 평가 |
| 법률 그래프 | Researcher·Auditor·종합 판단, Azure Astra high 배포·실제 합성 호출 성공 | 기업 자료 전송 범위 확정·원문 모델 보강·Auditor 품질 평가 |
| 수치 검토 | 같은 로트·최신 KG·공정/인계 ID에 붙인 제출 수치 계산·수치상 Pareto | 실제 견적·도로거리·활동량·해당 공정 LCI 적용성 확인 |
| Critic·출력 | 연결·산술·Pareto 독립 검사, REVIEW/REVISE·수정 범위·검토 보고서·지도 마커 데이터 | 의미적 반박·PASS·최종 Top K·자동 부분 재실행·영구 이력·실제 3D 연동 |

- `/analysis/research`: 후보·법령 근거 조사. 검색 성공이 허가·법규 적용성 검증은 아닙니다.
- `/analysis/site-discovery`: 서버가 전달한 사업장 좌표와 로트로 공정별 주변 시설을 조사합니다. 가까운 순서이며 처리 가능·가격 순위가 아닙니다.
- `/analysis/review-template`: 실제 KG 경로에 맞는 빈 숫자 양식. 모르는 활동량·계수는 `null`입니다.
- `/analysis/review`: 조사·KG-수치 연결·계산·결정론적 Critic·검토 보고서를 실행합니다. 자료 부족은 REVIEW, 확인된 오류는 REVISE입니다.
- `/analysis/compare-scenarios`: 기존 별도 산술 도구입니다. 실제 KG 연결 검토는 `/analysis/review`를 사용합니다.

[검토 API와 다음 작업](docs/grounded-decision-review.md), [검정 PP 3톤 실제 DB 실행 — 로트는 가정](docs/scenarios/pp-scrap-live-2026-10-22.md)을 참고하세요.
앞선 검증 기록은 당시 코드·DB 상태의 기록이며, 현재 브랜치의 새 실행·CI 결과와 구분합니다.
공식 법령 수집 범위는 v1.1.0이며 현재 활성 자료의 수·해시는 실제 DB에서 확인해야 합니다.

다음 연결 방향은 **내 사업장 선택 → 폐기물별 공정 → 주변 시설 조사 → 처리 조건 검증 → 전체 거리·총비용 비교**입니다.
출발 사업장과 공정별 명부 조사는 연결했습니다. 도로 API 코드는 추가했으며 지도 키가 없어 실제 호출 검증은 남아 있습니다.
회원의 사업장 선택·권한 확인, 견적·수치 검토 연결도 남아 있습니다.
[도로거리 설정과 범위](docs/road-routing.md), [Azure Astra high 설정·연결 상태](docs/azure-model.md)를 참고하세요.
[실행 API](docs/site-discovery-api.md), [전체 설계](docs/site-centered-discovery.md), [PR 점검과 다음 작업](docs/pr-audit-2026-10-24.md)을 참고하세요.

실제 명부 9,604개로 시험했지만, 같은 가까운 업체가 네 공정에 반복해서 나타났습니다.
명부의 넓은 폐기물 분류만으로는 세척·압출 등 실제 수행 능력을 구분할 수 없습니다.
다음 데이터 보강은 **시설별 수행 공정·투입 규격·현행 허가·이번 로트 인수 조건**입니다.

이어서 공식 자료 3건을 반영해 삼양 시화 주소를 보강하고 알엠 화성공장의 명부 행을 검수 연결했습니다.
**같은 사업장인지 / 해당 공정 근거가 있는지 / 현재 인수 가능한지**를 구분해 반환합니다.
알엠의 PET 컵 관련 공고만으로 세척·압출 능력을 추가하지는 않았습니다.
[근거 연결과 현재 한계](docs/facility-identity-evidence.md)를 참고하세요.
- 팀 공통 규칙: [resource-ai-team/.github CONTRIBUTING](https://github.com/resource-ai-team/.github/blob/main/CONTRIBUTING.md)

## 조직 레포 구성

| 레포 | 역할 |
| --- | --- |
| `Re_ai` (이 레포) | AI Agent, RAG, Knowledge Graph, 비용·탄소 계산 |
| `Re_server` | API Gateway, 인증·회원, 기업 정보, 업무 데이터 |
| `Re_client` | 사용자 화면 |
| `Re_infra` | 전체 통합 실행, 컨테이너 구성, 클라우드 인프라 |
| `.github` | 공통 규칙과 Issue·PR 템플릿 |

## 빠른 시작

### A. macOS·Linux

필수 환경은 Python 3.11 이상과 Docker Desktop입니다.

```bash
cp .env.example .env
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev,embeddings]'
```

`.env`에서 아래 값을 설정합니다.

```dotenv
NEO4J_PASSWORD=로컬에서_사용할_비밀번호
LAW_API_OC=국가법령정보_공동활용_API_OC
```

PostgreSQL·pgvector와 Neo4j를 실행합니다.

```bash
docker compose -f docker-compose.yml -f docker-compose.neo4j.yml up -d
```

DB 스키마와 지식그래프를 준비합니다.

```bash
resource-rag init-db
resource-kg import
resource-kg audit
```

공식 법령·판례 데이터를 수집하고 인덱싱합니다.

```bash
legal-rag collect
legal-rag index
resource-rag build-public-evidence
resource-rag index data/official/public_evidence_summaries.jsonl
```

API를 실행합니다.

```bash
uvicorn resource_ai.main:app --reload
```

실행 후 확인:

- Swagger UI: http://127.0.0.1:8000/docs
- 상태 확인: http://127.0.0.1:8000/health
- Neo4j Browser: http://127.0.0.1:7474

```bash
curl http://127.0.0.1:8000/health
```

정상 응답:

```json
{"status":"ok"}
```

### B. Windows PowerShell

```powershell
Copy-Item .env.example .env
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev,embeddings]"
docker compose -f docker-compose.yml -f docker-compose.neo4j.yml up -d
.\.venv\Scripts\resource-rag.exe init-db
.\.venv\Scripts\resource-kg.exe import
.\.venv\Scripts\resource-kg.exe audit
.\.venv\Scripts\legal-rag.exe collect
.\.venv\Scripts\legal-rag.exe index
.\.venv\Scripts\uvicorn.exe resource_ai.main:app --reload
```

### C. 전체 서비스 통합 실행

이 레포의 Docker Compose는 AI 로컬 개발용 PostgreSQL·Neo4j만 실행합니다.
`Re_client → Re_server → Re_ai` 전체 서비스 실행과 배포는 `Re_infra`의 Compose·운영 절차를 따릅니다.

## RAG 데이터 실행

RAG는 다음 코퍼스를 공통 pgvector 인프라에서 논리적으로 분리합니다.

| 코퍼스 | 대상 |
| --- | --- |
| `LAW` | 법령·시행령·시행규칙·별표 |
| `PRECEDENT` | 판례 |
| `TECHNICAL_LITERATURE` | 논문·공공 보고서·공정 설명서 |
| `HISTORICAL_CASE` | 검수된 성공·실패 처리 사례 |
| `COMPANY_DOCUMENT` | 성적서·시설 회신·규격·처리계획서 |

저장소의 검수된 공개 출처 요약은 다음 명령으로 재생성하고 적재합니다. 원문 전체가 아니라
출처 위치·적용 범위·한계를 포함한 검수 요약을 `TECHNICAL_LITERATURE`로 저장합니다.

```bash
resource-rag build-public-evidence
resource-rag validate data/official/public_evidence_summaries.jsonl
resource-rag index data/official/public_evidence_summaries.jsonl
```

그 밖의 정규화된 JSONL 데이터는 다음과 같이 확인하고 적재합니다.

```bash
resource-rag validate path/to/documents.jsonl
resource-rag index path/to/documents.jsonl --manifest path/to/documents.manifest.json
```

문서 계약과 기업별 권한 격리는 [다중 코퍼스 인덱싱](docs/rag-multi-corpus.md)을 참고합니다.
OCR과 사용자 문서 입력값 추출은 입력 영역, 시설·비용·탄소의 확정값은 구조화 데이터 영역에서 담당합니다.

## 기술스택

| 영역 | 선택 |
| --- | --- |
| 언어·API | Python 3.11+ / FastAPI |
| RAG 저장소 | PostgreSQL 16 / pgvector |
| 지식그래프 | Neo4j 5 |
| 검색 | Vector Search + Keyword Search + RRF + Reranking |
| 임베딩 | `intfloat/multilingual-e5-small` |
| DB 클라이언트 | psycopg / Neo4j Python Driver |
| 품질 | pytest / ruff |
| 로컬 인프라 | Docker Compose |

`EMBEDDING_PROVIDER=hash`는 테스트용이며 실제 의미 검색에는 사용하지 않습니다.

## 현재 구조

```text
Re_ai/
├── src/resource_ai/
│   ├── agents/            후보 조사 실행과 RAG·KG 호출
│   ├── api/               FastAPI 라우터와 입출력 스키마
│   ├── calculators/       비용·탄소·시나리오 비교
│   ├── core/              설정과 의존성 구성
│   ├── evidence/          공통 근거 문서·검색 결과 모델
│   ├── integrations/      국가법령정보 공동활용 API 연동
│   ├── knowledge_graph/   Neo4j 적재·공정 경로·시설 후보 조회
│   ├── rag/               수집·검증·청킹·임베딩·검색
│   └── rules/             검수된 판정 규칙 골격
├── migrations/            PostgreSQL·pgvector 마이그레이션
├── data/                  설정·샘플·평가 데이터
├── docs/                  아키텍처·실행·검증 문서
├── tests/                 단위·통합 테스트
├── docker-compose.yml     PostgreSQL·pgvector
└── docker-compose.neo4j.yml
```

## 주요 API

| 메서드 | 경로 | 설명 |
| --- | --- | --- |
| `GET` | `/health` | API 프로세스 상태 확인 |
| `GET` | `/rag/search` | RAG 근거 검색 |
| `GET` | `/kg/ontology` | 지식그래프 온톨로지 조회 |
| `GET` | `/kg/planning-coverage` | 공정·시설 연결 범위 진단 |
| `POST` | `/kg/candidates` | 소재 기반 공정 후보 조회 |
| `POST` | `/kg/candidate-pool` | 전체 후보 조사 묶음 조회 |
| `POST` | `/kg/process-plans` | 공정·시설 경로 후보 생성 |
| `POST` | `/kg/facility-candidates` | 시설 후보 조회 |
| `POST` | `/kg/facility-assessment` | 시설 공개 조건 대조 |
| `POST` | `/kg/assessed-plans` | 경로 생성과 시설 조건 대조 |
| `POST` | `/analysis/research` | KG 후보와 법률 근거 연결 |
| `POST` | `/analysis/compare-scenarios` | 비용·탄소·Pareto 비교 |

API별 입력·출력과 현재 한계는 [docs/api.md](docs/api.md)와 실행 중인 Swagger UI에서 확인합니다.

## 테스트

```bash
ruff check .
ruff format --check .
pytest -q
```

실제 PostgreSQL 통합 테스트:

```bash
docker compose up -d postgres
RUN_POSTGRES_INTEGRATION=true pytest -q tests/integration/test_multi_corpus_postgres.py
```

실제 Neo4j 통합 테스트는 `RUN_NEO4J_INTEGRATION=true`를 지정해 실행합니다.

## 문서

| 문서 | 내용 |
| --- | --- |
| [docs/pipeline-baseline.md](docs/pipeline-baseline.md) | 전체 서비스 파이프라인과 역할 경계 |
| [docs/architecture.md](docs/architecture.md) | 코드·DB·서비스 구조 |
| [docs/api.md](docs/api.md) | API 목록과 오류 처리 |
| [docs/rag-multi-corpus.md](docs/rag-multi-corpus.md) | RAG 문서 계약과 코퍼스별 정책 |
| [docs/knowledge-graph.md](docs/knowledge-graph.md) | KG 적재·조회·검증 |
| [docs/candidate-analysis.md](docs/candidate-analysis.md) | 후보 조사와 비용·탄소 비교 |
| [data/README.md](data/README.md) | 데이터 위치·출처·권리 관리 |
| [docs/README.md](docs/README.md) | 전체 문서 안내 |

## 기여 방법

1. Jira 티켓과 GitHub Issue를 생성합니다.
2. `develop`에서 `feat/깃허브이슈번호-작업명` 브랜치를 만듭니다.
3. 커밋은 `feat:`, `fix:`, `refactor:`, `docs:` 머리말을 사용합니다.
4. 모든 PR은 `develop`을 대상으로 생성하고 CI와 리뷰를 통과합니다.
5. PR이 병합되면 Jira 티켓을 `Done`으로 변경합니다.

`develop → main` 배포 PR은 인프라 담당자만 병합합니다.
