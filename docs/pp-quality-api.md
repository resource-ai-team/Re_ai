# PP 내부 API 계약

이 문서는 Re_server 담당자용입니다. 화면의 사용자 인증을 통과한 서버가 엔진 service token과 tenant를 붙입니다.
`tenant_id`는 사용자 요청 본문을 신뢰하지 않고 `X-Workflow-Tenant`에서 검증합니다.
사업장 소유권·사용자별 파일 권한의 최종 확인은 Re_server가 수행해야 합니다.

공통 헤더:

```http
Authorization: Bearer <server-only WORKFLOW_SERVICE_TOKEN>
X-Workflow-Tenant: <authenticated tenant>
X-Workflow-Actor: <authenticated actor>
Idempotency-Key: <new operation UUID; write requests only>
```

성공한 쓰기는 revision을 1 올립니다. 다음 요청은 응답의 최신 revision을 사용합니다.
다른 tenant의 case/document는 404입니다. 이미 쓴 ID의 근거 값 변경은 409, 잘못된 단위·종류는 보류 또는 422입니다.
API 응답은 `Cache-Control: no-store`입니다. 모델·지도·OCR 키는 브라우저에 주지 않습니다.

| 메서드·경로 | 기능 |
| --- | --- |
| POST `/workflow/cases` | 로트·위치가 없어도 `engine_request:null`로 시작 |
| POST `/workflow/cases/{id}/documents` | 기존 원본 저장 + OCR/문서 해석. field_photo면 문서 OCR 실행 안 함 |
| GET `/workflow/cases/{id}/documents/{document_id}` | 기존 tenant 제한 원본 조회 |
| PUT `/workflow/cases/{id}/quality-inputs` | 진술·관찰·명시적 문헌 가정 claim 추가 |
| PUT `/workflow/cases/{id}/quality-confirmations` | 선택 OCR 항목의 전사 내용과 시료 연결 확인 |
| GET `/workflow/cases/{id}/waste-profile` | 정규화 프로필·원문 claim·미확인·충돌 |
| GET `/workflow/cases/{id}/pp-assessment` | 현재 입력에 맞는 Level 0~3 사전 평가 |
| PUT `/workflow/cases/{id}/quality-scenario` | 명시적 목표·공정 성능/비용 가정 저장 |
| POST `/workflow/cases/{id}/pp-scenarios` | 최대 5개 조건의 읽기 전용 비교. revision 일치 필요 |
| POST `/workflow/cases/{id}/quality-research` | 확인된 프로필+명시한 사업장으로 기존 KG·RAG 조사 실행 |
| GET `/workflow/pp-reference-prices` | 해시 검증한 10월 PP 권역별 시장가격·미공표·출처 |

기존 `/analysis/research`와 `/kg/assessed-plans`의 요청·응답은 변경하지 않았습니다.
`quality-research`는 같은 SiteReviewService → 기존 후보 조사·시설 대조·RAG를 사용합니다.
기본 PP 공정 개념 배열과 실제 Neo4j 조회는 구분합니다. `kg_candidates_loaded`가 false면 실제 KG 결과가 아닙니다.
프로필이 바뀌면 조사 시 저장한 profile hash가 맞지 않아 false가 됩니다. `/quality-research`로 다시 조사합니다.
`/analysis/compare-scenarios`는 여전히 동일 경계의 비용·탄소 근거를 별도로 받습니다.
PP 탄소가 없는 새 사전 평가가 빈 값을 임의로 채워 이 비교를 통과시키지 않습니다.

## 1. 자료만 있는 검토 건 시작

```json
{
  "prompt": "국내 제조업 경질 PP 자료 검토",
  "engine_request": null,
  "mode": "research",
  "assessment_on": "2026-10-29"
}
```

업로드는 PDF/PNG/JPEG/UTF-8 TXT, 최대 6MB입니다. 확장자만 신뢰하지 않고 기존 signature 검사를 거칩니다.
배포 환경에서는 Re_server가 악성 파일 검사와 원본 접근권한 정책을 추가 적용해야 합니다.

```json
{
  "expected_revision": 1,
  "filename": "quality-report.png",
  "media_type": "image/png",
  "base64_content": "<original bytes encoded as base64>",
  "input_kind": "test_document",
  "use_azure_ocr": false,
  "use_local_image_ocr": true
}
```

Azure는 `use_azure_ocr:true`, 로컬 OCR은 false로 고릅니다. 두 방식을 동시에 선택하면 422입니다.
Azure는 서버의 `WORKFLOW_OCR_ENABLED`, `WORKFLOW_OCR_ALLOW_EXTERNAL`, endpoint/key 설정도 모두 필요합니다.
현재 로컬 실행기는 외부 전송을 비활성화합니다. API 키가 없어도 주입한 Mock으로 테스트할 수 있습니다.
Mock은 서버 테스트 의존성으로만 주입하며 공개 요청에서 `mock`를 골라 가짜 OCR을 받는 기능은 없습니다.
현장 사진은 `input_kind:field_photo`, 두 OCR 옵션 false입니다. 현재는 원본 저장과 미연결 상태만 반환합니다.

응답의 `documents[id].structured_report`에 document_type, metadata, rows, claims가 있습니다.
각 row에 원문 결과·정규값·단위·방법·qualifier·셀/위치·미확인 상태를 보존합니다.
복원하지 못한 행은 만들어내지 않습니다. 일반 `/documents/confirm`의 발췌 확인은 품질 claim 확인과 별개입니다.

## 2. 실제 HTTP 테스트에서 보낸 익명 요청 예시

아래 값은 **통합 테스트가 실제 API로 보낸 synthetic 요청**입니다. 고객사의 실측 요청을 확보한 것은 아닙니다.
실제 산업 데이터나 인증된 시험값으로 재사용하지 마세요.

```json
{
  "expected_revision": 2,
  "sample_id": "synthetic-sample-a",
  "claims": [
    {"evidence_id":"synthetic-material","field":"material","source_type":"operator_reported","text_value":"PP","sample_id":"synthetic-sample-a","same_lot_confirmed":true,"synthetic":true},
    {"evidence_id":"synthetic-material_form","field":"material_form","source_type":"operator_reported","text_value":"rigid_scrap","sample_id":"synthetic-sample-a","same_lot_confirmed":true,"synthetic":true},
    {"evidence_id":"synthetic-quantity_kg","field":"quantity_kg","source_type":"operator_reported","raw_value":"1000","normalized_value":"1000","unit":"kg","meaning":"lot_property","sample_id":"synthetic-sample-a","same_lot_confirmed":true,"synthetic":true}
  ]
}
```

측정값 항목은 `normalized_value`, 범주·설명은 `text_value`입니다. `raw_value`는 원문 그대로 둡니다.
수분·비PP 수치는 `unit:wt%`, `basis:wet_mass`, `meaning:lot_property`, 같은 sample_id와 로트 연결이 필요합니다.
직접 입력 값은 확인된 성적서 값을 사칭할 수 없습니다. 근거 미확인 진술도 Level 2 계산에는 쓸 수 있으나 출처를 그대로 표시합니다.
문헌 수치는 `source_type:literature_assumption`, assumptions 배열과 case mode=simulation이 필요합니다.

## 3. OCR 항목 선택 확인

```json
{
  "expected_revision": 3,
  "evidence_ids": ["ocr:<adapter-created evidence id>"],
  "sample_id": "synthetic-sample-a",
  "same_lot_confirmed": true
}
```

이미 다른 시료 ID가 있는 문서를 이 ID로 바꿔 붙일 수 없습니다. 성적서에 시료 ID가 없으면 사용자가 연결을 확인할 수 있지만
이 동작이 잘못 읽힌 숫자·분모·시험 목적을 자동 보정하지는 않습니다. 수정 전사는 새 근거로 추가하고 원문도 보존합니다.

## 4. 목표와 조건부 계산

```json
{
  "expected_revision": 4,
  "scenario": {
    "target": {
      "specification_id": "synthetic-buyer-target",
      "name": "가상 구매자 목표",
      "version": "1",
      "jurisdiction": "KR",
      "material": "PP",
      "forms": ["rigid_scrap"],
      "applies_to": "input_waste",
      "requirement_type": "scenario_target",
      "source": "synthetic scenario; not a domestic mandatory standard",
      "limits": [{"field":"contamination","max_value":"5","unit":"wt%","basis":"wet_mass"}],
      "explicitly_selected": true,
      "applicability_confirmed": true
    },
    "explicit_assumptions": ["가상 목표와 공정 성능·가격을 검토용으로 선언"],
    "stages": [{"stage_id":"synthetic-sort","process":"sorting","contaminant_removal_pct":"90","pp_loss_pct":"2","evidence_id":"synthetic-performance"}],
    "economics": {"preprocessing_krw":"100000","transport_krw":"10000","residue_krw_per_kg":"100","sale_price_krw_per_kg":"400","evidence_ids":["synthetic-economics"]}
  }
}
```

이 요청은 mode=simulation에서 사용합니다. 성능·가격은 선언한 가정이며 외부 원문 검증 완료가 아닙니다.
stages·economics를 빼면 이론 목표만 계산합니다. 수치가 없으면 목표를 넣어도 Level 1로 남습니다.
여러 시나리오는 `pp-scenarios`에 `{"expected_revision":5,"scenarios":[<scenario>,<scenario>]}`로 보냅니다.
총 PP 손실은 `overall_pp_loss_pct`로 별도 이상적 경계 계산에만 사용할 수 있고, stages와 함께 주면 422입니다.
단계 모델의 `declared_contamination_target_met`는 전체 규격 합격이 아닙니다.

주요 출력:

```json
{
  "level": 3,
  "mode": "simulation",
  "status": "CONDITIONAL",
  "mass_balance": {"minimum_contaminant_removal_kg":"157.8947368421052631578947368421052631579"},
  "carbon_kgco2e": null,
  "pareto_eligible": false,
  "recommended": false,
  "dispatch_approved": false,
  "legal_approved": false,
  "facility_acceptance_verified": false
}
```

물량 1,000·오염률 20 wt%에 대한 가상 예시입니다. 전체 응답에는 used_evidence_ids, claim_validation,
evidence_summary, questions, conflicts와 단계별 질량·손실·비용이 포함됩니다.

## 5. 기존 KG·RAG 조사 연결

```json
{
  "expected_revision": 5,
  "origin": {
    "organisation_id":"synthetic-company",
    "site_id":"synthetic-site",
    "label":"가상 사업장 · 실제 기업 아님",
    "pickup_address":"가상 주소 · 실제 수거 요청 아님",
    "latitude":37.2,
    "longitude":127.0
  },
  "max_plan_pages":1,
  "max_legal_queries":2,
  "max_technical_queries":2
}
```

위치는 서버가 제공해야 하며 기본 사업장·좌표를 채우지 않습니다. 이 API는 지도 외부 API를 호출하지 않습니다.
도로거리 조회는 기존 road-routing API와 키·전송/실행 허용 절차를 따릅니다. 직선거리로 바꿔 도로거리라고 표시하지 않습니다.
rigid_scrap/rigid_part는 기존 KG의 scrap 형태에 명시적으로 매핑하고, 미확인 수분·오염률은 전달하지 않습니다.
공정·시설 후보는 조사 결과이며 실제 인수·허가·최신 용량 확인을 대체하지 않습니다.
