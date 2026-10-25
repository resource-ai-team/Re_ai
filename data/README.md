# 데이터 관리

## 디렉터리

| 경로 | 역할 | Git 포함 |
| --- | --- | --- |
| `config/legal_scope.json` | 공식 API 수집 범위와 데이터셋 버전 | 포함 |
| `config/public_evidence_scope.json` | 검수된 공개 근거 요약의 RAG 적재 범위 | 포함 |
| `config/retrieval_eval.json` | 질문별 기대 근거 평가셋 | 포함 |
| `config/facility_assessment_eval.json` | 가상 로트와 문서 기반 조건 대조 기대 결과. 현장 정답 아님 | 포함 |
| `samples/` | 입력 형식 확인용 가짜 데이터 | 포함 |
| `raw/` | 수동 확보 원본을 둘 자리 | 원칙적으로 검토 후 결정 |
| `processed/` | 전처리 중간 산출물 | 원칙적으로 검토 후 결정 |
| `official/` | 공식 API에서 재생성한 JSONL·매니페스트 | 제외 |
| `knowledge_graph/` | 고정 해시의 W2RKG 인덱스·출처·라이선스 | 포함 |
| `local/facilities/` | 별도 수집한 국내 시설 명부 원본·매니페스트 | 제외 |
| `local/source-review/` | 공정 보강에 확인한 공식 원본 HTML·PDF. 해시만 검수 기록에 포함 | 제외 |
| `local/historical-cases/` | 익명화·검수된 실제 처리 결과 반입 원본 | 제외 |
| `local/company-documents/<tenant_id>/` | 고객사별 성적서·회신·규격·견적서 등 비공개 원본 | 제외 |

KG 조사 카드·출처 메타데이터·조건부 공정 템플릿은 Python 배포에도 포함되도록
`src/resource_ai/knowledge_graph/resources/`에 둡니다. 원본은 Re-Prototype의
`414cd8d` 커밋이며 `bundle-manifest.json`이 각 파일 해시를 기록합니다.
새 근거로 변경할 때는 카드·범위·출처와 해시를 함께 검토합니다.
공정·시설 참조 자료와 공식 코드표는 별도로 추가한 자료이며, 원본·버전과 출처를
`bundle-manifest.json.additional_provenance`에서 구분합니다.
`planning-reference-v2`의 시설별 `input_profile`도 같은 번들에 포함하며, 공개 예시·제외 정책을
실제 투입 규격·현행 허가·인수 승인과 구분합니다. 원본 해시·출처·확인일을 함께 검토합니다.
각 출처의 `supported_records`에 사람이 검수한 상태·전이·시설 등의 연결 범위를 명시합니다.
`Facility.registry_identity_links`는 공식 사업장 자료와 특정 명부 행을 검수 연결한 기록입니다.
명부 ID·원본/행 해시·출처·확인일을 포함하며 같은 회사 이름만으로 연결하지 않습니다.
새 명부 스냅샷은 재검수가 필요하고 동일 사업장 식별이 공정 능력·현행 허가·인수를 입증하지 않습니다.
범위 밖 연결은 거부하지만 원문 의미를 자동으로 검증한 것으로 해석하지 않습니다.
추가 확보한 제지·PP 지침의 해시와 반영 범위는 `knowledge_graph/planning-source-review-2026-10-21.json`에 기록했습니다.
국내 시설 명부는 별도 Neo4j 세대에 적재하며 원본 재배포·외부 모델 전송은 활성화하지
않았습니다. [수집·적재 방법과 한계](../docs/kg-facility-matching.md)를 참고하세요.
Neo4j 데이터 볼륨·로컬 비밀번호·국가 LCI 원본은 Git에 포함하지 않습니다.

## 공식 법률 수집 범위 v2

팀의 `config/legal_scope.json` v2를 기준으로 법령·시행령·시행규칙 15종 전체 조문·별표와 판례 12건을 수집합니다. 설정 변경과 실제 DB 재적재 완료는 구분해야 합니다.

## 과거 검증 기록: 데이터셋 v1.1.0

아래는 2026-10-22 당시의 P0 법률 근거 검색 기준선 기록입니다. 국가법령정보 공동활용 API에서
폐기물관리법 제2·17·25·33·48조, 시행규칙 별표 4·4의2·4의3·5의3,
순환경제사회 전환 촉진법 제15·21·23·28·30·33조, 대법원 2023두62830·2002다46331·2019다226548을 수집합니다.

2026-10-22 로컬 활성 자료는 **6문서·119구간·216청크**입니다. 초기 v1의 4문서·102구간을 보존하고 조문·판례를 추가했습니다.
실제 법률 그래프에는 조문/별표 15개·판례 3개·참조 24개와 관계 31개가 있습니다.
모델 특징·체크리스트 생성은 아직 없으며 구조 검증 통과가 법규 적용성 정확도를 의미하지 않습니다.
[구조 대조 기록](../docs/evaluation/legal-expansion-2026-10-22.json)을 참고하세요.

추가 판례 원문은 [2002다46331](https://www.law.go.kr/precInfoP.do?precSeq=82028),
[2019다226548](https://www.law.go.kr/precInfoP.do?precSeq=226949)입니다.
사건번호·선고일이 정확히 맞는 참조만 실제 판례로 연결하며, 이것을 적용 확정으로 해석하지 않습니다.

구법 후보는 활성 자료와 분리해 보관했습니다. 시행일 스냅샷과 완전한 연혁 검수가 없으면 적용 기간을 확정하지 않습니다.
동일 source ID에 다른 시행일·본문이 들어오는 충돌은 적재를 중단합니다. 희소한 수집 자료 사이의 기간을 법적 유효기간으로 추정하지 않습니다.

```bash
legal-rag collect
legal-rag validate
legal-rag index
```

수집 시 `legal_documents.jsonl`과 `legal_documents.manifest.json`이 생성된다. 매니페스트에는
범위와 데이터 파일의 SHA-256, 문서·구획 수, 법령 버전, 시행일과 공식 URL이 기록된다.
`legal-rag index`는 이 해시와 데이터 검증을 통과한 파일만 pgvector에 원자적으로 적재한다.

법령 외 기술문헌·과거 사례·기업 문서는 공통 JSONL 계약으로 정규화한 뒤
`resource-rag validate`와 `resource-rag index`를 사용한다. 코퍼스별 필수 메타데이터와
기업 문서 권한 경계는 [다중 코퍼스 인덱싱](../docs/rag-multi-corpus.md)에 정의한다.

검수된 공개 근거 카드는 아래 명령으로 기술문헌 요약 데이터셋을 재생성한다. 저작권이
확인되지 않은 원문 전체 대신 검수 요약과 출처 위치만 포함한다.

```bash
resource-rag build-public-evidence
resource-rag index data/official/public_evidence_summaries.jsonl
```

## 신뢰 경계

- `samples/`는 실제 법률 근거가 아니다.
- 공식 API 원문도 서비스 적용 전 시행일과 현행 여부를 다시 확인한다.
- 검색 결과는 근거 후보이며 적법성 판정이 아니다.
- 판정은 검수된 규칙 엔진이 담당하고 미확인 값은 확정하지 않는다.
- 판례·논문·행정해석은 출처와 이용 조건을 확인한 항목만 범위 설정에 추가한다.
