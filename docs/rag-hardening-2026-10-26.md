# RAG 보강: 검색과 실제 적용 판단을 구분하기

## 이번에 바꾼 구조

```mermaid
flowchart LR
    Q[공정별 조사 질문] --> E[E5 의미 검색 + 키워드 검색]
    E --> P[설정에 따라 최대 100개 후보]
    P --> R[규칙 정렬 또는 로컬 Cross-encoder]
    R --> H[Top K 근거]
    H --> A[같은 버전의 조문 전체]
    A --> L[명시된 참조 조문·별표 1단계 조회]
    L --> C[누락·예산 초과·버전 충돌을 Critic에 전달]
```

기업·공장명은 법령 검색어에서 빼고, 소재·공정·확인할 법적 쟁점으로 검색합니다. 기업·사업장 정보 자체는 기존 후보 데이터에 남습니다.

후보 수와 최종 결과 수를 분리했습니다. Top 3만 보여줘도 먼저 최대 100개를 찾을 수 있습니다. 문서 유형·기업 범위·현재 버전 필터를 **후보 검색 전에** 적용합니다. 공개 검색에 기업 범위로 저장된 법령이 섞이는 것도 차단합니다.

## 모델을 실제로 비교하는 방법

- 평가 질문 50개: LEGAL 30개, TECHNICAL 20개. 개발용 27개와 검증용 23개는 질문 주제별로 분리했습니다.
- 정답은 보유 원문을 확인한 개발자 임시 라벨입니다. 독립 전문가 평가나 법적 적용성 정확도가 아닙니다. 질문·원문 지식 자체를 가린 실험도 아닙니다.
- 먼저 개발용으로 후보 수·모델·조문 중복 제거를 비교하고 설정을 정합니다. 검증용 결과를 보고 설정을 다시 맞추지 않습니다.
- `Hit@5`: 기대한 근거가 상위 5개 안에 있는 질문의 비율입니다. 가능한 모든 정답을 라벨링한 일반적인 문서 Recall은 아닙니다. `MRR`은 첫 기대 근거가 얼마나 위에 나오는지 봅니다.
- 후보 내 정답 포함 여부도 기록합니다. 후보 생성 실패와 재정렬 실패를 구분하기 위해서입니다.

비교한 모델은 BGE v2-m3 INT8 ONNX와 mMARCO MiniLM INT8 ONNX입니다. BGE는 이 CPU에서 100개 후보를 정렬하는 데 질문당 67~78초가 걸려 3문항 속도 시험에서 제외했습니다. **BGE 전체 품질 평가는 완료하지 않았습니다.** MiniLM은 개발용 전체 평가를 진행했습니다. 범용 다국어 모델이며 한국 법령에 최적화된 모델로 검증된 것은 아닙니다.

**기본 모델 교체는 보류했습니다.** 개발용에서 고른 MiniLM 100후보 설정을 검증용에 그대로 적용하자 법령 점수가 떨어졌습니다. 검증용을 보고 설정을 다시 맞추지 않았고, 기존 규칙 정렬을 유지합니다. 기술문헌에서는 점수가 올랐지만 이번 실험만으로 별도 기본 모드를 선택하지 않습니다.

| 구분 | 기존 규칙: Hit@5 / MRR | MiniLM 100후보: Hit@5 / MRR |
| --- | --- | --- |
| 개발 LEGAL 16문항 | 10/16 · 0.471 | 12/16 · 0.635 |
| 개발 TECHNICAL 11문항 | 11/11 · 0.909 | 11/11 · 1.000 |
| 검증 LEGAL 14문항 | 10/14 · 0.488 | 8/14 · 0.333 |
| 검증 TECHNICAL 9문항 | 9/9 · 0.796 | 9/9 · 0.833 |

후보 50/100개만 늘리는 방법과 조문 중복 제거는 개발용에서 추가 효과가 없었습니다. 따라서 기본 후보 설정·중복 제거 설정도 유지합니다. 조문 전체·명시 참조 확장과 미확인 표시, 법령 질문에서 공장명 제거는 실제 파이프라인에 적용됩니다.

개발용 탐색을 두 차례 했으며, 검증용은 그 뒤에 사용합니다.

- [첫 개발 비교](evaluation/reranker-dev-2026-10-26.json)
- [조문 중복 제거 추가 비교](evaluation/reranker-diversity-dev-2026-10-26.json)
- [BGE 중단된 속도 시험](evaluation/reranker-bge-latency-pilot-2026-10-26.json)
- [고정한 설정](../data/config/retrieval_study_selection_v1.json) · [별도 검증 결과](evaluation/reranker-test-2026-10-26.json) · [기본 적용 거절 기록](evaluation/reranker-activation-2026-10-26.json)

## 찾은 조문을 어떻게 보충하나요?

검색 청크 ID를 DB에서 다시 확인하고, 같은 문서·버전·조문의 인덱싱된 청크를 모읍니다. 예를 들어 제25조 첫 항이 검색되면 같은 조의 다른 항과 단서도 같이 제공합니다. 제25조의3은 다른 조문으로 취급합니다.

그 본문에 명시된 `「법명」 제N조`, 시행규칙의 `법 제N조`·`영 제N조`, 같은 문서의 `별표 N`은 1단계까지 조회합니다. 단순 `제N조`처럼 법명이 불분명한 참조나 과거 법령은 임의로 현재 법령에 연결하지 않고 `unresolved`에 남깁니다. 연결된 조문에서 다시 링크를 무제한 따라가지 않습니다.

- 검색 결과당 조문/별표 최대 48청크, 합계 60,000자, 참조 대상 8개.
- 조문 또는 별표 전체가 한도를 넘으면 `partial_context`입니다.
- 자료 없음·현재 버전 중복·날짜/공식 출처 이상·오래된 검색 ID를 각각 드러냅니다.
- 이 결과의 `retrieved`는 해당 조회 범위에서 자료를 모았다는 뜻입니다. 적용성·예외·허가·인수 검증 완료를 뜻하지 않습니다.

실제 기본 조문 10개에서 **62청크**를 모았고, 미해결 참조·위임 문구 **59개 항목**을 드러냈습니다. 법명 미확정, 미보유 문서, 조문 미지정 위임, 예산 한도 등이 포함됩니다. 59개의 서로 다른 누락 문서를 뜻하지 않습니다. [실제 보유 법령 검사](evaluation/legal-context-2026-10-26.json)

## 응답에서 팀원이 사용할 필드

`research.legal_searches[].results[]`에 다음이 추가됩니다.

| 필드 | 뜻 |
| --- | --- |
| `reranker_score`, `reranker_model` | 신경망 원점수와 고정 모델 버전. 확률·적법성 점수가 아님 |
| `legal_context.primary` | 검색된 조문/별표의 같은 버전 청크들 |
| `legal_context.linked` | 명시 참조를 따라 조회한 조문·별표와 상태 |
| `legal_context.unresolved` | 대상 불명확·미보유·예산 초과 참조 |
| `legal_context.source_checks` | 오래된 청크·본문 일부 누락·메타데이터 이상 |

`/rag/search`에는 모델 점수·버전만 추가됩니다. 조문 확장은 파이프라인의 조사 단계에서 수행합니다. 기존 Top K 검색 지표에 확장 결과를 끼워 넣어 점수를 부풀리지 않습니다.

Critic은 문맥 확보 미완료를 `legal_context_incomplete`, 오래된 검색 근거를 `legal_context_stale`로 남깁니다. 같은 법령의 버전이 충돌하면 오류로 표시합니다. 실제 지도 화면의 배지 연결은 여전히 프런트 작업이 필요합니다.

## 실행과 재현

추가 모델 없이 기존 검색·조문 확장을 실행할 수 있습니다. 신경망 재정렬은 다음과 같이 별도로 준비합니다.

```bash
python -m pip install -e '.[embeddings,reranker]'
python -m scripts.setup_reranker onnx-minilm
```

**비교 시험 전용** 설정 예시입니다. 기본 적용 검증을 통과한 권장 설정이 아닙니다.

```dotenv
HYBRID_CANDIDATE_MINIMUM=100
RERANKER_PROVIDER=onnx-minilm
RETRIEVAL_ARTICLE_DIVERSITY=false
```

모델은 정확한 revision과 weight SHA-256을 고정합니다. 실제 검색 시 다운로드·외부 추론·자동 모델 대체를 하지 않습니다. 누락/손상 모델은 오류가 되며 정상적인 빈 검색으로 숨기지 않습니다. weights와 `.env`는 Git에 포함하지 않습니다. 작은 INT8 MiniLM weight 약 119 MB, BGE weight 약 571 MB이며 tokenizer 등은 별도입니다.

이 PC의 MiniLM 100후보 법령 재정렬도 대략 15~23초가 걸렸습니다. 공유 CPU에서 측정한 참고값이며 동시접속 응답시간 보장은 아닙니다. 기술 요약은 후보 자체가 24청크라 더 짧습니다. 품질과 지연시간 모두 후속 과제입니다.

```bash
python -m scripts.evaluate_rerankers --split dev --profiles rules20 rules50 rules100 minilm50 minilm100 --output output/dev.json
python -m scripts.audit_legal_context --output output/legal-context.json
python -m scripts.smoke_local_reranker --output output/model-wiring.json
```

마지막 명령은 가상 공장·PP 3톤 입력으로 실제 로컬 KG·DB·MiniLM이 HTTP 파이프라인에 연결되는지만 검사합니다. 추가 API 과금은 없고 법적 판단 품질 검사가 아닙니다. [실행 기록](evaluation/reranker-pipeline-smoke-2026-10-26.json)

## 아직 필요한 작업

실제 로컬 Neo4j·PostgreSQL을 켠 최종 코드 테스트는 **813개 + subtest 24개 통과, skip 0개**입니다. [코드 테스트 기록](evaluation/rag-hardening-tests-2026-10-26.json). 기존 69개 HTTP 시나리오 검사는 **358/360**이며 같은 허가 조문 질문의 두 검사 실패가 남아 있습니다. [시나리오 기록](evaluation/pipeline-rag-hardening-2026-10-26.json). 모델 추가나 코드 테스트 통과를 검색 품질 완성으로 간주하지 않습니다.

1. 전문가가 만든 실제 업무 질문과 여러 정답·오답·적용 예외 라벨.
2. 현재 못 찾는 허가/순환자원 질문 개선. 단순 후보 확대나 범용 재정렬만으로 충분하지 않습니다.
3. 위임 규정·참조 법명 확정·조례·개정 전후 시점의 연결과 누락 자료 확보.
4. TECHNICAL 원문을 더 확보하고 국내 공정·로트에 적용되는지 검수. 현재 평가 대상은 보유 요약 24청크뿐입니다.
5. 실제 견적·도로 키·시설별 허가/인수·같은 경계의 탄소값, 최종 판단 Auditor 평가.

이번 보강은 **자료를 더 잘 찾고 읽을 문맥을 제공하는 단계**입니다. 최종 Top K·PASS·실제 인수 승인·법률 적용 판단을 완성한 것은 아닙니다.

모델/방식의 원 출처: [Cross-encoder 2단계 검색](https://www.sbert.net/examples/cross_encoder/applications/README.html), [BGE 원 모델](https://huggingface.co/BAAI/bge-reranker-v2-m3), [사용한 ONNX 변환](https://huggingface.co/onnx-community/bge-reranker-v2-m3-ONNX), [사용한 MiniLM 모델](https://huggingface.co/cross-encoder/mmarco-mMiniLMv2-L12-H384-v1).
