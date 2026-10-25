# RAG 다중 코퍼스 인덱싱

## 범위

하나의 pgvector 기반에서 저장 문서를 다음 유형으로 구분합니다.

| `document_type` | 대상 | 필수 검증 |
| --- | --- | --- |
| `LAW` | 법령·시행령·시행규칙·별표 | 국가법령정보센터 출처·시행일 |
| `PRECEDENT` | 판례 | 국가법령정보센터 출처·사건번호 |
| `TECHNICAL_LITERATURE` | 논문·공공 보고서·공정 설명서 | 발행기관·색인 권리 |
| `HISTORICAL_CASE` | 성공·실패·거절·보류 처리 사례 | 검수 완료 여부 |
| `COMPANY_DOCUMENT` | 성적서·시설 회신·규격·처리계획서 | 기업 ID·검수 상태·외부 전송 차단 |

외부 검색 API는 저장 유형을 그대로 노출하지 않고 세 검색 그룹으로 묶습니다.

| `search_group` | 포함하는 `document_type` |
| --- | --- |
| `LEGAL` | `LAW`, `PRECEDENT` |
| `TECHNICAL` | `TECHNICAL_LITERATURE` |
| `COMPANY` | `COMPANY_DOCUMENT` |

세 그룹은 벡터 검색과 키워드 검색 결과를 RRF로 결합한 뒤 서로 다른 기준으로 재정렬합니다.
법률의 공식 출처·시행일 가산점은 `LEGAL`에만 적용하고, `TECHNICAL`과 `COMPANY`에는
제목·본문·원문 위치 일치도를 적용합니다. 기업 문서는 현재 버전만 검색하며 `tenant_id`로
기업 범위를 제한합니다.

`HISTORICAL_CASE`는 운영 DB에서 관리할 구조화 처리 이력과 혼동하지 않도록 세 검색 그룹에서
제외합니다. 장문 메모 검색이 필요해질 때 별도 Case Memory 계약으로 추가합니다.

`TECHNICAL`은 `collection_lane=technical`로 검수된 기술 문헌만 포함합니다. 시설·수취처
(`factory`, `recipient`) 자료는 KG와 업무 DB의 후보 근거로 관리하며 Technical RAG에
적재하지 않습니다.

시설 정보와 검수된 비용·탄소 계산값은 구조화 데이터 저장소에서 관리하며 RAG 코퍼스에
포함하지 않습니다. OCR과 사용자 문서 추출도 입력 영역의 책임입니다. RAG는 정규화된
JSONL을 전달받습니다.

## 공통 JSONL 계약

모든 문서는 `source_id`, `document_type`, `title`, `source_url`, `version`, `sections`를
가집니다. 각 구획은 `citation`, `page_no`, `metadata.source_position` 중 하나로 원문 위치를
제공해야 합니다. 버전이 바뀌어도 동일 문서임을 식별하도록 비법률 문서는
`metadata.canonical_source_id`를 반드시 제공합니다.

~~~json
{
  "source_id": "paper:pet-recycling:v1",
  "document_type": "TECHNICAL_LITERATURE",
  "title": "PET 재활용 공정 연구",
  "source_url": "https://example.org/papers/pet-recycling",
  "source_org": "발행기관",
  "version": "v1",
  "metadata": {
    "canonical_source_id": "paper:pet-recycling",
    "rights": {"indexing_allowed": true, "model_transfer_allowed": false}
  },
  "sections": [{"heading": "방법", "content": "...", "page_no": 3}]
}
~~~

과거 사례는 `metadata.verified=true`가 필요합니다. 기업 문서는 최상위 `tenant_id`,
`metadata.review_status`(`UNREVIEWED`, `REVIEWED`, `MODIFIED`)와
`metadata.model_transfer_allowed=false`가 필요합니다.

## 실행

~~~bash
resource-rag init-db
resource-rag build-public-evidence
resource-rag validate data/official/public_evidence_summaries.jsonl
resource-rag index data/official/public_evidence_summaries.jsonl
resource-rag validate path/to/documents.jsonl
resource-rag index path/to/documents.jsonl --manifest path/to/documents.manifest.json
~~~

`build-public-evidence`는 해시로 고정된 공개 출처 번들에서 검수 및 로컬 원본 확인을 통과한
카드만 선택한다. 원문 전체가 아니라 검수 요약·원문 위치·한계·확인되지 않은 사항을
`TECHNICAL_LITERATURE`로 생성한다. 법률·비용·탄소 카드는 제외하며, 실제 처리 결과가 없는
시설·수취 후보 자료를 `HISTORICAL_CASE`로 분류하지 않는다.

인덱싱은 전체 데이터셋을 하나의 트랜잭션으로 처리합니다. 동일한
`canonical_source_id`의 새 버전이 들어오면 이전 버전은 보존하되 검색 대상에서 제외합니다.
기업 문서는 검색 시 `tenant_id`가 없으면 반환되지 않으며, 기업 문서 코퍼스를 명시해
검색할 때는 `tenant_id`가 필수입니다.
