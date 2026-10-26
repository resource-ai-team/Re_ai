# Azure GPT-6 Astra 연결

서버 모델 어댑터는 **Azure Responses API / GPT-6 Astra / reasoning high**로 구현했습니다.
연결 위치는 기존 `legal_graph` 선택 경로와 별도의 선택 `semantic_critic`입니다.
GPT Critic은 기존 코드 검사 뒤에 전체 후보·허용 근거를 읽고 추가 반박을 제안합니다.
별도 서버 전송 설정·서비스 토큰이 필요합니다. 구현·모의 전송 검증에 더해 한 경로의 연결·단위 오류를 실제 GPT로 비판·재검사한 2회 호출이 성공했습니다. 전체 법규·시설·탄소의 GPT 반박 품질은 미검증입니다. [실제 호출 범위·기록](azure-critic-live.md)
[GPT Critic 설정·전송 범위](semantic-critic.md)를 참고하세요.
기본 법령 RAG 검색이나 Python 비용·탄소 계산이 LLM 호출로 바뀐 것은 아닙니다.
전체 파이프라인의 자동 실행·OCR·웹 검색은 별도 작업입니다.

## 설정

```dotenv
LEGAL_GRAPH_MODEL_PROVIDER=azure
AZURE_OPENAI_ENDPOINT=https://<리소스명>.openai.azure.com/openai/v1/
AZURE_OPENAI_DEPLOYMENT=<실제로 GPT-6 Astra를 배포한 이름>
AZURE_OPENAI_DEPLOYMENT_MODEL=gpt-6-astra
AZURE_OPENAI_REASONING_EFFORT=high
AZURE_OPENAI_MAX_OUTPUT_TOKENS=16384
AZURE_OPENAI_API_KEY=<서버 전용 키>
LEGAL_GRAPH_TIMEOUT_SECONDS=180
LEGAL_GRAPH_MODEL_ENABLED=true
LEGAL_GRAPH_ALLOW_EXTERNAL=true
```

**배포명**은 모델 카탈로그 이름과 다를 수 있습니다. 계정 로그인만으로 API가 만들어지지는 않습니다.
실제 Astra 배포·모델 사용 권한·키가 있어야 호출됩니다. 로그인 비밀번호를 API 키로 사용하지 않습니다.
API 키는 Git에서 제외된 `.env` 또는 서버 비밀 저장소에서 관리합니다.
기업 자료 전송 범위 검토 전에는 `LEGAL_GRAPH_ALLOW_EXTERNAL=false`를 유지합니다.
GPT Critic의 `SEMANTIC_CRITIC_ALLOW_EXTERNAL`은 이 값과 독립적이며 기본 false입니다.

Azure 모드에서 Solar로 자동 대체하지 않습니다. 응답이 모델명 대신 배포 별칭을 반환할 수 있으므로,
포털에서 해당 endpoint·배포의 실제 모델을 확인한 뒤에만 `DEPLOYMENT_MODEL=gpt-6-astra`를 설정합니다.
별칭은 서버에 설정한 배포명과 정확히 일치해야 하며, 명시적으로 다른 모델명·미완료 응답·거절·잘못된 JSON은 채택하지 않습니다.
별칭 자체가 실제 모델을 증명하지는 않습니다. 운영자가 배포를 교체하면 이 모델 대조를 다시 해야 합니다.
구조화 출력 스키마와 기존 근거 검증을 함께 사용합니다.

## 전송과 기록

작업 지시, 작업별 JSON 자료(질문·facts·공정 가설·검색 근거·조문 등), 출력 스키마를 Azure에 보냅니다.
기업 정보가 섞인 facts를 자동 익명화하지 않습니다. 임의 URL이나 사용자 제공 API 키는 받지 않습니다.
`store=false`, hosted tools 없음, 외부 검색 없음, 요청별 입력 15만 문자·출력 토큰 한도가 있습니다.
이 값은 Azure 운영/보안 로그까지 보관하지 않는다는 보장이 아닙니다.

모델 원문·숨은 추론을 trace에 저장하지 않습니다. 기존 BudgetModel에는 작업명·상태만 남습니다.
어댑터의 메모리 `last_call`에는 작업·상태·HTTP 코드·모델 식별 근거·입출력/추론 토큰 수가 남습니다.
입력·출력 본문·키는 넣지 않으며 자동 영구 저장하지 않습니다.
호출 실패 메시지에는 제공자의 원문 오류나 키를 넣지 않습니다. 모델 요청 간 캐시·자동 재시도는 없습니다.

## 검증 상태

- 합성 MockTransport로 Astra/high, 스키마, store=false, 모델 불일치, 거절, 오류·한도 검증 완료.
- 실제 계정 로그인 및 기존 교육용 Foundry 배포 조회 완료.
- **2026-10-25 실제 합성 연결 성공:** 배포 `resource-gpt-6-astra`, 실제 모델 GPT-6 Astra 2026-09-03, Global Standard.
  `high`·구조화 JSON 검증 완료. Azure가 반환한 모델 필드는 배포명이며 포털의 실제 모델·버전과 대조했습니다.
- 합성 호출 총 3회. 앞선 2회는 배포 별칭을 모델 불일치로 거절했고, 수정 후 1회가 통과했습니다.
  성공 호출은 입력 160·출력 135토큰(출력 중 추론 94토큰)입니다. [확인 기록](evaluation/azure-connection-2026-10-25.json).
- 로컬 `.env`에 서버 키·배포명·high를 연결했습니다. 기업 자료 외부 전송은 `false`로 유지하며,
  합성 테스트 스크립트만 명시적으로 호출했습니다. 기업 자료를 이용한 Auditor 실행·그래프 보강 품질 검증은 아직입니다.
- 현재 배포 한도는 1,000 TPM·분당 1요청입니다. 동시 처리나 긴 입력 부하 검증을 완료한 설정이 아닙니다.
- 같은 환경에서 재확인할 때 `python scripts/check_azure_model.py --live-synthetic`을 사용합니다. 실제 과금 호출이므로 자동 CI에서는 실행하지 않습니다.

공식 문서: [Azure Responses API](https://learn.microsoft.com/en-us/azure/foundry/openai/how-to/responses),
[Azure reasoning 모델](https://learn.microsoft.com/en-us/azure/foundry/openai/how-to/reasoning),
[GPT-6 Astra](https://developers.openai.com/api/docs/models/gpt-6-astra).
