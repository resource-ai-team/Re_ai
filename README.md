# Re:Source AI

공장에서 나온 폐자원으로 **어떤 처리 공정을 검토할 수 있는지**, 각 단계에서 **무엇을 확인해야 하는지** 보여주는 AI 엔진입니다.
기존 처리 방식은 모르는 상태에서 시작합니다. 여러 공정 후보를 찾고 근거·미확인 사항을 비교합니다.

## 10월 27일 Azure 직접 확인·배출계수 확보

로그인된 Azure를 직접 확인했습니다. **Astra 배포는 정상**이지만, Foundry의 연결 데이터·파일·인덱스와 본인 ML 작업 영역의 등록 데이터는 없었습니다.
Sustainability 앱·IEA 계수 사용권은 현재 계정 화면에서 확인되지 않았습니다.

공식 원본·방법론 **7파일**을 추가로 확보했습니다. 국내 전력계수는 **2023년 소비단 0.4173 kgCO₂e/kWh**입니다.
DEFRA·EPA의 폐기물 계수는 보고용 범위여서 공정 전체 탄소 순위에 그대로 쓸 수 없습니다.
단위·국가·연도·범위를 붙여 로컬 JSON으로 준비했고, **서비스 탄소 계산에는 아직 연결하지 않았습니다.**
[실제 수치·Azure 확인 범위·다음에 필요한 활동량](docs/emission-factors-and-azure-2026-10-27.md)

## 10월 27일 검증용 실제 자료 확보

공개 원본 **13개**를 로컬에 확보했습니다. 환경공단 가격 26품목·공개 가격 100개, 환경청 Excel 4개·921행, 화성·광산 명부 1,460행, 실제 위탁·매각 공고가 포함됩니다.
중복 업체·전용용기 제조업을 구분하며, 원본은 Git에 올리지 않습니다. 실제 공고를 바탕으로 검증에 쓸 사실 묶음 6개를 정리했습니다.
**아직 Critic 정확도 평가나 실제 거래 승인 자료는 아닙니다.** [Azure 조사·확보 자료·적용 범위](docs/verification-data-acquisition-2026-10-27.md)

## 10월 27일 법령 검색 보강

- **한국어 핵심 단어 검색 추가:** E5와 한국어 BM25를 결합하는 선택 기능을 구현했습니다. 기존에 놓친 설치·신고·정의·운반 의무 근거를 보강합니다.
- 수정에 사용한 법령 질문은 **23/30 → 29/30**, 기술 문헌은 **20/20 유지**입니다. 새 질문 8개는 두 방식 모두 **7/8**이었습니다.
- 다만 배출자 의무·경매 책임 질문에서 순위가 밀리는 사례가 있어 **전체 기본값은 기존 방식**입니다. 새 방식은 패키지·색인 준비 후 켤 수 있습니다. [사용법·비교·남은 문제](docs/korean-bm25-2026-10-27.md)

이번 점검은 기대 조문이 검색 결과에 있는지 보는 검사입니다. 기존 검증 질문도 수정에 사용했으므로 이제 독립 검증셋으로 보지 않습니다. 전문가의 법률 적용성 평가도 아닙니다.

앞서 적용한 기능:

- **지침과 상위법을 함께 조회:** 지침의 목적 조문·시행규칙에 실제로 적힌 법령 인용을 따라가 근거 조문을 함께 보여줍니다. 명확하지 않은 연결은 만들지 않습니다.
- **같은 조문 중복 줄이기:** 같은 조문·별표·지침 장의 여러 조각이 검색 결과를 차지하는 문제를 줄였습니다. 전체 문맥은 따로 보충합니다.
- **정확한 문서·조문 조회:** 현행 법령명과 한 조문을 명시하면 해당 조문을 직접 조회합니다.
- **Critic 입력 한도:** 엔진 사실과 기본 법령 근거를 우선해 조문·별표 묶음으로 선택하고, 보내지 못한 자료 수를 표시합니다. 모델은 전송하지 않은 근거를 인용할 수 없습니다.

1차 보강 당시 기본 법령 질문은 **5/7 → 7/7**, 추가 표현은 **3/8 → 7/8**, 당시 동결 검증 질문은 **6/14 → 8/14**였습니다. 신규 자료 조회는 **15/15**였습니다.

[동작·재현 방법·남은 문제](docs/grounded-rag-2026-10-27.md) · [최신 검증](docs/evaluation/grounded-rag-retrieval-2026-10-27.json)

## 10월 26일 자료 수집·당시 평가

- **팀 저장 방식 유지:** PostgreSQL·pgvector + `multilingual-e5-small` 384차원을 그대로 사용합니다.
- **공식 원문 적재:** 법령 73개·행정규칙 25개, **98문서·21,979조각**을 로컬 RAG에 넣었습니다. 허가·재활용·운송·유해특성·환경·안전을 포함합니다. 첨부로만 제공되는 시험기준도 PDF 파일·페이지 단위로 읽습니다. [수집 범위·저장 위치·다시 넣는 방법](docs/legal-corpus-expansion.md)
- **조문 일부만 읽는 문제:** 검색한 조문의 다른 항·단서와 명시된 참조 조문·별표를 함께 조회합니다.
- **찾지 못한 근거도 표시:** 법명 불명확·누락 자료·위임 규정·조각 한도·버전 충돌을 확인 사항으로 남깁니다.
- **확장 전 모델 비교:** 질문 50개를 주제별로 개발용 27개·검증용 23개로 나눴습니다. MiniLM은 개발용에서 좋아졌지만 검증용 법령은 기존 10/14 → 8/14로 떨어져 **기본 모델 교체를 보류**했습니다. BGE는 이 CPU에서 너무 느려 제외했습니다.

자료 확장 직후 기존 검증 질문의 법령 회수는 10/14 → 6/14로 낮아졌습니다. 이후 보강과 선택 검색 기능의 결과는 위 10월 27일 기록을 참고하세요.

조문 보충과 리스크 표시는 적용됐습니다. 기존 규칙 정렬을 유지하며, 새로운 모델은 비교 시험용으로 연결할 수 있습니다. [변경 내용·평가 결과·실행 방법](docs/rag-hardening-2026-10-26.md)

## GPT Critic 연결

**Azure GPT-6 Astra / high 실제 비판·재검사 2회를 추가했습니다.** 한 경로의 연결 누락·운송 단위 오류를 GPT가 지적했고, 수정 후 같은 범위의 추가 지적은 0건이었습니다. 실제 KG 경로와 가상 수치를 사용한 집중 검사입니다. [실제 응답·사용량·범위](docs/azure-critic-live.md)

**지금은 오류 지적과 다시 시작할 단계 지정까지입니다. 서비스의 자동 수정·재실행은 아직 없습니다.**
실제 엔진으로 `연결 누락 지적 → KG 재조회 → 단위 오류 지적 → 환산·재계산 → REVIEW`를 실행한 재생 데모를 추가했습니다. 가상 오류·수치, 실제 KG·RAG·코드 Critic을 사용했고 GPT 호출은 0회입니다. [과정·실행 방법·남은 작업](docs/critic-revision-demo.md)

기존 코드 검사 뒤에 **Azure GPT-6 Astra / high**가 근거를 읽고 추가 반박을 제안하는 선택 단계를 구현했습니다.
지적의 근거 ID·원문 인용·후보/단계 연결을 확인하고, 유효한 제안은 지도 마커·보고서의 확인 사항에 추가합니다.
기존 오류나 계산 결과를 모델이 지우거나 PASS를 내릴 수는 없습니다.

전체 후보·법령·시설에 대한 GPT 오류 발견 성능은 아직 검증 전이며, 전체 근거의 기본 외부 전송은 비활성 상태입니다. 이번 실제 호출은 한 경로의 연결·단위만 검사했습니다.
업체 명부·기업 문서·사용 제한 LCI 원문은 모델 입력에서 제외합니다.
긴 근거를 보내려면 현재 교육용 배포의 할당량도 확인해야 합니다. [사용법·전송 항목·검증 범위](docs/semantic-critic.md)

## 함께 보강한 것

- **공정별 기본 조문 직접 조회:** 벡터 검색과 함께 법령 ID·조문 ID로 기본 확인 조문 10개를 조회합니다. 허가·신고·자체 처리의 조건과 예외는 별도로 검토합니다.
- **출처 오류 검사:** 다른 조문·중복된 현재 버전·조각 누락을 표시하고, 조사 중 같은 법령의 서로 다른 버전이 섞이면 재검토를 요청합니다.
- **지도 배지 집계:** 같은 공정·운송 구간의 확인 사항을 `map_marker_groups`로 묶습니다. 세부 근거는 그대로 남깁니다. 실제 화면 연결은 후속입니다.
- **검색 평가 강화:** 본문의 단순 인용을 정답으로 세지 않고, 핵심 질문이 실패하면 평균 점수가 좋아도 평가를 실패 처리합니다.

[추가 보강·검증·응답 필드](docs/pipeline-followup-2026-10-25.md)를 참고하세요. 일반 RAG 검색 정확도는 계속 개선이 필요합니다.

## 지금 어디까지 되나요?

**사업장·폐기물 입력 → 여러 공정 경로 → 주변 시설 조사 → 법령·기술 근거 → LCI 예비 산정 → 제출 수치 검토 → 리스크·보고서**를 로컬에서 실행합니다.
한 번에 묶은 내부 API는 `POST /analysis/site-review`입니다.

| 단계 | 지금 되는 것 | 아직 필요한 것 |
| --- | --- | --- |
| 입력 | 사업장 ID·수거 좌표·소재·형태·물량을 구조화해 받음 | 로그인한 회원의 실제 공장 선택·권한, OCR |
| 공정 후보 | Neo4j에서 근거가 연결된 여러 공정 순서를 탐색 | 추가 소재/공정 근거. 현재 알루미늄·종이는 경로가 각각 하나 |
| 시설 조사 | 실제 전국 명부에서 공정별 조사 대상 검색, 거리순 조회 | 실제 수행 공정·현행 허가·이번 로트 인수 가능 여부 |
| 운송 | 카카오·네이버 도로 API 연동 코드와 다중 구간 검사 | 지도 키와 실제 호출 검증. 키가 없으면 도로거리는 미확인 |
| 근거 조사 | LEGAL·TECHNICAL 검색, 명시 조문·상위법·문맥 보충, 선택 한국어 BM25. 기업 문서는 별도 격리 | 적용성·예외 검수. 검색에 여전히 누락·순위 저하가 있음 |
| 비용·탄소 | 맞는 경로의 LCI 예비값, 근거를 제출한 수치의 산술·Pareto | 실제 견적·활동량·같은 경계의 LCI. 예비값을 전체 순위로 합산하지 않음 |
| 오류·리스크 | 코드 검사, 선택 GPT Critic 연결·인용 대조, 단계별 확인 사항·지도 마커 | 실제 GPT 반박 정확도·전체 Auditor 평가·자동 부분 재실행 |
| 결과 | REVIEW/REVISE·근거·다음 행동·지도 마커 데이터·보고서 | PASS·최종 Top K·영구 이력·실제 3D 화면 연결 |

**현재는 조사·검토 도구입니다. 실제로 가장 싸거나 탄소가 가장 적은 공장을 확정하는 단계는 아닙니다.**

## 실제로 돌려 봤나요?

2026-10-26 기준, 실제 Neo4j·PostgreSQL·로컬 LCI로 검증했습니다. 사업장·폐기물 로트·단가는 가상입니다.

- 69개 API 시나리오: PP·PE·PET·알루미늄·종이, 형태 누락/불일치, 잘못된 수량, 여러 사업장, 오래된 수치, LCI 매핑 등을 검사했습니다.
- 실제 DB를 켠 코드 테스트는 **911개 + subtest 24개 통과, skip 0개**입니다. [최신 검사 기록](docs/evaluation/azure-critic-tests-2026-10-27.json)
- 코퍼스 확장 전 69개 시나리오의 세부 검사는 **358/360 통과**. 남은 두 실패는 같은 법령 질문의 핵심 제25조 회수 문제입니다. [최신 시나리오 기록](docs/evaluation/pipeline-rag-hardening-2026-10-26.json). 모든 법규·현장 사례의 정확도를 검증한 결과가 아닙니다.
- 최신 법령 질문은 **7/7**, 추가 표현은 **7/8**, 신규 자료 조회는 **15/15**입니다. 개발자 점검용 질문이며 법률 적용성 평가는 아닙니다. [이번 검색 결과](docs/evaluation/grounded-rag-retrieval-2026-10-27.json)
- 공식 법령·판례와 기술 요약 **124문서·22,095청크**, 실제 시설 명부 **9,604건**, 보유 LCI 원본 **7개**를 로컬에 연결했습니다. 동료 PR #46에 맞춰 시설·수취처 자료는 기술 RAG 검색에서 제외했습니다.
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
python -m pip install -e '.[dev,embeddings,legal-attachments]'
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

확장 법령 코퍼스와 기술 요약을 수집하고 인덱싱합니다. 원본은 로컬에 저장합니다.

```bash
python scripts/expand_legal_corpus.py collect
python scripts/expand_legal_corpus.py index
python scripts/build_legal_aliases.py --output data/official/legal-aliases.json
resource-rag build-public-evidence
resource-rag index data/official/public_evidence_summaries.jsonl
```

판례도 새로 준비할 때는 기존 `legal-rag collect` → `legal-rag index`를 **확장 법령 적재 전에** 실행합니다. 기존 수집 명령은 15개 법령도 포함하므로 나중에 실행하면 확장 수집 버전을 교체할 수 있습니다. [자세한 데이터 관리 방법](docs/legal-corpus-expansion.md)

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
.\.venv\Scripts\python.exe -m pip install -e ".[dev,embeddings,legal-attachments]"
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

Legal·Technical RAG 검색 품질은 적재가 완료된 PostgreSQL에서 다음과 같이 확인합니다.

```bash
resource-rag evaluate --cases data/config/retrieval_eval.json
```

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
