# KG 전체 후보·조사 묶음 API

## 언제 쓰나

새 파이프라인의 **KG 전체 후보 조회 단계**에서 사용합니다.
기존 `/kg/candidates`는 호환성을 위해 기본 20건·형태 일치 조회를 유지합니다.
전체 조사에 그 제한 조회를 사용하지 않습니다.

`POST /kg/candidate-pool`

```json
{
  "material": "PP",
  "color": "검정",
  "quantity_kg": 3000,
  "generation_process": "사출",
  "input_form": null,
  "contamination": null,
  "current_treatment": null,
  "page_size": 25,
  "cursor": null,
  "audience": "internal"
}
```

이 예시는 가상 입력입니다. 실제 공장 측정값이나 PP 공정 적용 검증이 아닙니다.

## 페이지를 끝까지 받기

```python
import httpx

payload = {"material": "PP", "color": "검정", "page_size": 25}
items = []
with httpx.Client(base_url="http://127.0.0.1:8000", timeout=30) as client:
    while True:
        response = client.post("/kg/candidate-pool", json=payload)
        response.raise_for_status()
        page = response.json()
        items.extend(page["items"])
        cursor = page["pagination"]["next_cursor"]
        if cursor is None:
            break
        payload["cursor"] = cursor
```

`page_size`는 한 번에 전송할 개수(1~100)입니다. 추천 개수 K가 아닙니다.
`next_cursor=null`까지 따라가야 전체 조회가 끝납니다.
`complete_in_this_response`는 첫 페이지 한 번으로 전부 반환된 경우에만 `true`입니다.
그래프나 입력 조건이 바뀌면 부분 결과를 버리고 처음부터 다시 수집합니다.
커서는 조회 위치이며 인증 토큰이 아닙니다.
외부 모델용 커서는 소재·형태·보기 방식에만 입력을 결합합니다. 현재 조회에 쓰이지 않는
지역·물량·현행 처리 등 비공개 값은 본문과 커서의 해시에 넣지 않습니다.
내부 보기에서는 전체 입력 변경을 검사합니다.

## 응답에서 볼 것

| 필드 | 의미 |
| --- | --- |
| `pagination.total_candidates`, `totals_by_kind` | 저장된 문헌·공정 템플릿·조사 카드의 수. 실현 가능한 공정 수가 아님 |
| `items[].candidate_id` | 후보 ID. 다른 페이지와 중복 확인·ReAct 결과 연결에 사용 |
| `kind` | `process` / `scoped_claim` / `literature`. 문헌 한 건을 공정 경로 하나로 세지 않음 |
| `review_state` | 모두 `unassessed`. KG는 조사 전에 통과·탈락을 결정하지 않음 |
| `research_packet.conditions` | 확인 질문·근거 ID·작성 범위. `machine_rule_ready=false` |
| `research_packet.process_graph` | 조건부 경로의 단계·구간·리스크·근거 매핑·전역 occurrence ID |
| `research_packet.evidence` | 원문 위치·출처·검수 범위·제한 사항·외부 전송 허용 여부 |
| `form_comparison` | 형태 미확인·같은 표현·다른 표현·템플릿 형태 미지정. 적합성 판정이 아님 |
| `relevance` | 소재 검색 관계인지, 여러 소재에 관한 일반 배경 카드인지 구분 |
| `not_established`, `research_tasks` | 후속 조사할 법규·현장 품질·업체 인수·비용·탄소·운송 |
| `graph_trace` | 그래프 버전·해시·고정 파이프라인 버전·필터 미적용 표시 |

공정 경로가 없는 문헌·카드는 `process_graph=null`입니다.
쉼표로 묶인 공정명을 쪼개서 순서를 만들지 않습니다.
같은 경로의 입력 형태별 버전은 별도 후보로 유지합니다. 후속 단계에서 근거를 보존하며 묶을 수 있습니다.
`multiple` 배경 카드는 특정 소재의 허용 처리 방식을 입증하지 않습니다.

## 에이전트에서 호출

```python
from resource_ai.agents.tools.graph_tool import get_candidate_pool_page

page = get_candidate_pool_page(material="PET", page_size=25)
```

이 도구는 항상 `audience=external_model`을 적용합니다. 사용자 입력 내용은 응답에 되돌려 주지 않습니다.
근거 전송이 명시적으로 허용되지 않은 후보는 `research_packet=null`로 가립니다.
후보 ID·종류·전송 차단 근거 ID는 남으며, **후보 제외가 아니라 내부 검토 필요**라는 뜻입니다.
허용된 근거와 차단된 근거가 섞인 경로는 전체 묶음을 보수적으로 가립니다.
이 API와 도구 자체는 외부 모델을 호출하지 않습니다.

내부 응답을 그대로 외부 모델에 전달하면 이 경계를 우회하게 됩니다.
기존 제한 조회 도구는 내부용 호환 인터페이스이며, 외부 모델에는 새 도구의 보기를 사용합니다.

## 오류

- `400 invalid-cursor`: 잘못된 커서 또는 입력/내부·외부 보기 조건 변경. 처음부터 재조회.
- `409 stale-cursor`: 적재 버전 변경. 모은 부분 결과를 버리고 처음부터 재조회.
- `422`: 정의하지 않은 입력, 페이지 크기, 수치 오류. `top_k`, `priority`, 자유 Cypher는 받지 않음.
- `503`: Neo4j 설정·연결·미적재·무결성 문제. SQLite나 임의 기본 공정으로 대체하지 않음.

## 검증 실행

```bash
pytest -q
# 실제 Neo4j 통합 검사는 RUN_NEO4J_INTEGRATION=true 설정
python scripts/evaluate_kg_pool.py --output kg-pool-evaluation.json
```

전체 자료 검사는 원본의 후보 ID 집합과 API 전체 페이지의 집합을 비교합니다.
현장 공정 검증이나 추천 성능 평가는 별도로 필요합니다.
