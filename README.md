# Re:Source AI

중소 제조기업의 폐기물 처리 의사결정을 지원하는 **AI·지식 기반 서비스**입니다.
폐기물과 공정 정보를 바탕으로 관련 근거를 찾고, 이후 지식 그래프·규칙 엔진·계산기를
결합해 검증 가능한 처리 방안을 만드는 역할을 담당합니다.

현재는 **법령 근거 검색과 Neo4j 공정 후보 탐색**까지 동작합니다.
폐자원 정보를 넣으면 문헌 후보와, 자료가 있는 소재의 공정·리스크·근거를 돌려줍니다.
법적 판정, 비용·탄소 순위 계산과 최종 추천은 아직 구현되지 않았습니다.

- 팀 공통 규칙: [resource-ai-team/.github CONTRIBUTING](https://github.com/resource-ai-team/.github/blob/main/CONTRIBUTING.md)
- 구조 설명: [docs/architecture.md](docs/architecture.md)
- 데이터 관리: [data/README.md](data/README.md)
- API 설명: [docs/api.md](docs/api.md)
- KG 실행·입출력·남은 작업: [docs/knowledge-graph.md](docs/knowledge-graph.md)
- KG 전체 구조를 보는 로컬 개발 도구: [scripts/kg_explorer](scripts/kg_explorer/README.md)

## 지금 어디까지 되나요?

| 흐름 | 현재 상태 |
| --- | --- |
| 문서·자연어 입력을 항목으로 정리 | 입력 형식만 정의. OCR·LLM 추출·검수 화면은 후속 작업 |
| 관련 문헌·공정 후보 찾기 | W2RKG 33,679건을 Neo4j에 적재하고 조회 가능 |
| 공정 단계와 리스크·근거 연결 | PET·알루미늄·종이 조건부 경로 18개와 조사 카드 69개. PP·PE는 문헌 탐색부터 가능 |
| 법령 근거 찾기 | 기존 Hybrid Search·Reranking 사용 가능 |
| 허가·규정 판정, 비용·탄소·운송 비교 | 검수 데이터와 규칙·계산기 연동 필요 |
| 에이전트 통합·최종 추천·3D·보고서 | KG 호출 도구까지 제공. 전체 서비스 연결은 후속 작업 |

사용자의 기존 처리 방식은 아는 경우에만 받습니다. 모르면 임의로 만들지 않습니다.

## 조직 저장소 구성

| 저장소 | 역할 |
| --- | --- |
| `Re_client` | 화면과 3D 시각화, `Re_server` API 호출 |
| `Re_server` | 인증·회원·기업 정보, 업무 API와 서비스 DB |
| **`Re_ai` (이 저장소)** | AI 오케스트레이션, 근거 검색, 지식 그래프, 규칙과 계산 |
| `RE-Main` | 현재 통합 프로토타입, 향후 infra 역할 정리 대상 |
| `.github` | 팀 공통 규칙과 Issue·PR 템플릿 |

호출 경계는 다음을 기준으로 합니다.

```text
Re_client ──HTTP──▶ Re_server ──HTTP──▶ Re_ai
                       │                  │
                       ▼                  ▼
                  서비스 DB       AI 검색·지식 기반 DB
```

클라이언트가 AI 서비스를 직접 호출하지 않으며, 서버와 AI 사이의 요청·응답 계약은 별도
연동 명세로 관리합니다.

## 빠른 시작

Docker Desktop과 Python 3.11 이상이 필요합니다.

```bash
cp .env.example .env
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev,embeddings]'

docker compose up -d
legal-rag init-db
```

기본 임베딩은 로컬 `intfloat/multilingual-e5-small`입니다. 최초 실행 시 모델을
다운로드합니다. 파이프라인 테스트만 할 때는 `.env`에서
`EMBEDDING_PROVIDER=hash`를 사용할 수 있지만 실제 의미 검색용은 아닙니다.

## 공식 데이터 수집과 적재

수집 범위는 `data/config/legal_scope.json`에 버전과 함께 관리합니다.

```bash
legal-rag collect
legal-rag validate
legal-rag ingest data/official/legal_documents.jsonl
legal-rag evaluate --top-k 5
```

`collect`는 공식 원문 JSONL과 SHA-256·출처·버전·시행일을 기록한 매니페스트를 함께
생성합니다. 생성 데이터는 재수집 가능하므로 Git에 포함하지 않습니다. 팀·운영 환경은
국가법령정보 공동활용 신청 후 발급받은 `LAW_API_OC`를 사용합니다.

현재 평가셋은 구현 회귀 확인용이며 법률 정확도나 전체 서비스 품질을 보장하지 않습니다.

## Hybrid Search와 Reranking

검색은 다음 단계로 동작합니다.

1. multilingual-e5 임베딩과 pgvector 코사인 검색
2. PostgreSQL `tsvector` 키워드 검색
3. Reciprocal Rank Fusion(RRF)으로 순위 결합
4. 공식 출처·시행일·제목/조문 일치도 기반 재정렬
5. 공식 URL·시행일·인용과 함께 상위 근거 반환

```bash
legal-rag search "폐기물을 다른 업체에 맡기면 배출자 책임은 없어지나요?"
```

검색 결과는 법률 근거 후보이며 Rule Engine의 적법성 판정을 대신하지 않습니다.

## API 실행

```bash
uvicorn resource_ai.main:app --reload
```

- Swagger UI: `http://127.0.0.1:8000/docs`
- 상태 확인: `GET /health`
- 근거 검색: `GET /rag/search?q={질문}&top_k=5`
- 온톨로지 확인: `GET /kg/ontology`
- 공정 후보 조회: `POST /kg/candidates` — [실행 예시](docs/knowledge-graph.md)

## 기술 스택

| 영역 | 선택 |
| --- | --- |
| 언어·API | Python 3.11+, FastAPI, Pydantic |
| 검색 DB | PostgreSQL 16, pgvector, `tsvector` |
| 지식그래프 | Neo4j 5.26, 출처·공정·리스크 온톨로지 |
| 임베딩 | sentence-transformers, multilingual-e5-small |
| 공식 데이터 | 국가법령정보 공동활용 API |
| 품질 | Pytest, Ruff, GitHub Actions |
| 실행 | Docker Compose, Uvicorn |

## 프로젝트 구조

```text
src/resource_ai/
├── api/               FastAPI 라우트와 입출력 스키마
├── agents/            향후 AI 도구 오케스트레이션
├── rag/               수집·검증·Hybrid Search·생성
├── knowledge_graph/   온톨로지·Neo4j 적재·공정 후보 조회
├── rules/             결정적 법규 판정 (골격)
├── calculators/       경제성·환경성 계산 (골격)
├── evidence/          근거 모델과 근거 카드
├── integrations/      법령 API와 외부 모델 연동
└── core/              설정과 공통 인프라
```

`rules`, `calculators`, 에이전트 전체 오케스트레이션은 아직 골격입니다.
KG는 별도 모듈로 동작하며 에이전트에서 호출할 Python 도구를 제공합니다.

## 테스트와 CI

```bash
ruff check .
ruff format --check .
pytest -q
```

GitHub Actions는 PR과 `develop`·`main` push에서 린트, 포맷 검사와 테스트를 실행합니다.
실제 외부 API·Azure 키 없이 검증하도록 단위 테스트에서는 mock과 테스트 임베딩을
사용합니다.

## 기여 절차

```text
Issue → develop에서 작업 브랜치 생성 → 구현·테스트 → develop 대상 PR → CI → merge
```

`main`은 배포용이며 `develop → main` 릴리스 병합은 인프라 담당자가 진행합니다.
