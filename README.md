# Re:Source AI

공장에서 나온 폐자원으로 **어떤 처리 공정을 검토할 수 있는지**, 각 단계에서 **무엇을 확인해야 하는지** 보여주는 AI 엔진입니다.
기존 처리 방식은 모르는 상태에서 시작합니다. 여러 공정 후보를 찾고 근거·미확인 사항을 비교합니다.

## 지금 어디까지 되나요?

**사업장·폐기물 입력 → 여러 공정 경로 → 주변 시설 조사 → 법령·기술 근거 → LCI 예비 산정 → 제출 수치 검토 → 리스크·보고서**를 로컬에서 실행합니다.
한 번에 묶은 내부 API는 `POST /analysis/site-review`입니다.

| 단계 | 지금 되는 것 | 아직 필요한 것 |
| --- | --- | --- |
| 입력 | 사업장 ID·수거 좌표·소재·형태·물량을 구조화해 받음 | 로그인한 회원의 실제 공장 선택·권한, OCR |
| 공정 후보 | Neo4j에서 근거가 연결된 여러 공정 순서를 탐색 | 추가 소재/공정 근거. 현재 알루미늄·종이는 경로가 각각 하나 |
| 시설 조사 | 실제 전국 명부에서 공정별 조사 대상 검색, 거리순 조회 | 실제 수행 공정·현행 허가·이번 로트 인수 가능 여부 |
| 운송 | 카카오·네이버 도로 API 연동 코드와 다중 구간 검사 | 지도 키와 실제 호출 검증. 키가 없으면 도로거리는 미확인 |
| 근거 조사 | LEGAL(법령·판례), TECHNICAL(기술문헌) 검색. 기업 문서 격리는 별도 COMPANY 검색 | 검색 원문 적용성·예외 검수. 법규 회수 검수 질문은 현재 6/7 |
| 비용·탄소 | 맞는 경로의 LCI 예비값, 근거를 제출한 수치의 산술·Pareto | 실제 견적·활동량·같은 경계의 LCI. 예비값을 전체 순위로 합산하지 않음 |
| 오류·리스크 | 다른 로트·사업장·KG의 수치 제외, 누락·단위·계산 검사, 단계별 확인 사항 | 의미적 반박·전체 Auditor 정확도 평가·자동 부분 재실행 |
| 결과 | REVIEW/REVISE·근거·다음 행동·지도 마커 데이터·보고서 | PASS·최종 Top K·영구 이력·실제 3D 화면 연결 |

**현재는 조사·검토 도구입니다. 실제로 가장 싸거나 탄소가 가장 적은 공장을 확정하는 단계는 아닙니다.**

## 실제로 돌려 봤나요?

2026-10-25 기준, 실제 Neo4j·PostgreSQL·로컬 LCI로 검증했습니다. 사업장·폐기물 로트·단가는 가상입니다.

- 69개 API 시나리오: PP·PE·PET·알루미늄·종이, 형태 누락/불일치, 잘못된 수량, 여러 사업장, 오래된 수치, LCI 매핑 등을 검사했습니다.
- 실제 DB를 켠 코드 테스트는 **772개 + subtest 24개 통과, skip 0개**입니다. [검사 기록](docs/evaluation/pipeline-tests-2026-10-25.json)
- 세부 검사 **336/338 통과**. 남은 두 실패는 같은 법령 질문의 핵심 제25조 회수 문제입니다. 모든 법규·현장 사례의 정확도를 검증한 결과가 아닙니다.
- 추가 표현 8개로 법령 검색도 더 시험했고 **5/8**이었습니다. 이 질문·정답은 개발자가 만든 점검용이라 전문가 검수와 검색 개선이 필요합니다.
- 공식 법령·판례와 기술 요약 **41문서·3,385청크**, 실제 시설 명부 **9,604건**, 보유 LCI 원본 **7개**를 로컬에 연결했습니다. 동료 PR #46에 맞춰 시설·수취처 자료는 기술 RAG 검색에서 제외했습니다.
- 이번 시나리오에서 유료 모델·지도 API 호출은 하지 않았습니다. Azure Astra high는 앞선 합성 연결 시험만 성공한 상태입니다.

팀원 PR 확인, 발견한 문제, 수정 내용, **데이터/기술별 다음 작업**은 [이번 전체 검증 정리](docs/pipeline-validation-2026-10-25.md)를 보면 됩니다.

## 팀이 바로 써 볼 API

- `/analysis/site-review`: 사업장·주변 시설·근거·LCI·수치 검토·보고서를 한 요청으로 실행
- `/analysis/site-discovery`: 사업장별 공정·시설 조사. 명부 일치는 공정 수행·인수 확인이 아닙니다.
- `/analysis/research`: 후보별 법령·선택한 기술 근거 조사. 검색 성공과 적법 판정은 다릅니다.
- `/analysis/review-template` → `/analysis/review`: 실제 경로 양식에 근거 수치를 채우고 연결·계산·리스크 검토
- `/analysis/road-routes`, `/analysis/kg-lci`: 실제 도로 경로 조회와 국가 LCI 예비 산정 도구

[API 설명](docs/api.md) · [전체 목표 구조](docs/pipeline-baseline.md) · [코드 구조](docs/architecture.md) · [Azure 설정](docs/azure-model.md) · [도로 설정](docs/road-routing.md)

공식 문서·시설 명부·사용 조건이 있는 LCI 파일은 각 개발 환경에 별도로 준비합니다. 원본·키는 Git에 올리지 않습니다.
팀 공통 규칙: [CONTRIBUTING](https://github.com/resource-ai-team/.github/blob/main/CONTRIBUTING.md)

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

RAG 검색 API는 다음 세 그룹을 제공합니다.

| 검색 그룹 | 대상 |
| --- | --- |
| `LEGAL` | 법령·시행령·시행규칙·별표·판례 |
| `TECHNICAL` | 논문·공공 보고서·공정 설명서 |
| `COMPANY` | 기업별 성적서·시설 회신·규격·처리계획서 |

내부 저장소는 기존 문서 유형을 유지하며 검색 시 위 그룹으로 매핑합니다. 실제 처리 이력은
업무 DB에서 관리하고 기본 RAG 검색 그룹에 포함하지 않습니다.

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
