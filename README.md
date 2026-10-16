# Re:Source AI 법령·판례 RAG

로컬 PostgreSQL과 pgvector에 법령·판례 청크를 적재하고 관련 근거를 검색하는 최소 구현입니다. RAG는 근거 검색만 담당하며, 적법성 판정은 향후 Knowledge Graph와 Rule Engine에서 처리합니다.

## 1. 준비

Docker Desktop을 실행한 뒤 환경 파일과 Python 가상환경을 준비합니다.

```bash
cp .env.example .env
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev,embeddings]'
```

기본 모델은 로컬에서 실행되는 `intfloat/multilingual-e5-small`이며 최초 실행 시 모델 다운로드가 필요합니다. 네트워크 없이 파이프라인만 확인할 때는 `.env`의 `EMBEDDING_PROVIDER=hash`로 변경합니다. Hash provider는 테스트용이며 실제 의미 검색 품질을 제공하지 않습니다.

## 2. PostgreSQL 실행

```bash
docker compose up -d
docker compose ps
legal-rag init-db
```

## 3. 테스트 데이터 적재 및 검색

샘플 파일은 형식 검증 전용이며 실제 법률 근거가 아닙니다.

```bash
legal-rag ingest data/samples/legal_documents.example.jsonl
legal-rag search "폐기물 처리 허가"
```

## 4. API 실행

```bash
uvicorn app.main:app --reload
curl 'http://127.0.0.1:8000/health'
curl --get 'http://127.0.0.1:8000/search' --data-urlencode 'q=폐기물 처리 허가'
```

## 5. 테스트

```bash
pytest
ruff check .
```

## 입력 JSONL

한 줄에 법령 또는 판례 문서 하나를 저장합니다. 공식 출처 URL, 시행일, 버전과 인용문을 반드시 포함하고 검증되지 않은 데이터를 운영 근거로 사용하지 않습니다.

```json
{"source_id":"unique-id","document_type":"LAW","title":"문서명","source_url":"https://공식출처","effective_from":"2026-01-01","version":"버전","sections":[{"heading":"제1조","content":"원문","citation":"문서명 제1조"}]}
```

