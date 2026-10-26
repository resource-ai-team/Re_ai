# Azure GPT로 비판하고 다시 검사한 결과

**지원받은 Azure의 GPT-6 Astra / high를 실제로 두 번 호출했습니다.** 앞선 코드 전용 재생 데모와 별도로, GPT의 지적을 받은 후 KG·RAG 재조회와 단위 환산·재계산을 수행하고 GPT에 다시 보냈습니다.

## 무슨 사례인가요?

실제 KG에서 가져온 한 경로인 **PP 반입 선별·파쇄 → PP 세정 조사 → PP 압출 재생**을 검사했습니다. 공장·로트·운송 단가는 가상입니다. 초기 초안의 인계 구간 하나를 의도적으로 빼고, `3,000kg × 20,000원/t`를 환산 없이 계산한 잘못된 주장도 넣었습니다. 이 잘못된 금액은 Python 계산 엔진의 출력이 아닙니다.

| 순서 | 실제 수행·결과 |
| --- | --- |
| 1. GPT 비판 | S1→S2 인계 누락, kg↔t 환산 누락으로 운송비가 1,000배 커진 문제를 지적 |
| 2. 인용 확인 | 두 지적에 인용한 문구·후보가 제공한 근거와 일치하는지 코드로 대조. 거절된 지적 0건 |
| 3. 수정 | KG·법령·기술 RAG 재조회로 연결 복구. 20,000원/t를 20원/kg로 환산하고 재계산 |
| 4. GPT 재검사 | 같은 KG·가상 견적을 기준으로 연결·단위 범위의 추가 지적 0건 |
| 5. 정지 | 허가·이번 로트 인수·실제 견적·LCI 검증이 남아 `REVIEW`. 최종 승인 없음 |

GPT의 첫 지적은 프로그램에 답을 넣은 목데이터가 아닙니다. 첫 호출에는 코드 Critic의 오류 목록도 보내지 않았습니다. 단, 원문을 보고 구성한 **오류 주입 사례 하나**여서 이를 일반적인 오류 발견 정확도로 해석하지 않습니다. 수정은 허용된 작업을 개발용 실행기로 수행했고, 서비스 API의 자동 루프가 완성된 것은 아닙니다.

[첫 GPT 응답](evaluation/azure-critic-before-2026-10-27.json) · [실제 수정·코드 재검사](evaluation/azure-critic-code-repair-2026-10-27.json) · [GPT 재검사](evaluation/azure-critic-after-2026-10-27.json) · [종합 기록](evaluation/azure-critic-revision-2026-10-27.json)

## 실제 전송한 데이터와 한도

- 후보 3개 중 **한 경로의 구조**와 가상 물량·단가·초안만 전송했습니다. 법령 원문·기업 문서·업체 명부·실제 주소·LCI 원본은 전송하지 않았습니다.
- 현재 응답 헤더는 배포 한도 **1,000 TPM·분당 1요청**을 표시합니다. 전체 법령 묶음을 보내는 일반 Critic의 처리량을 검증한 것은 아닙니다.
- 호출별 출력 한도는 1,024토큰, 단계별 시도는 한 번이며 자동 재시도는 없습니다. 로컬 원장으로 두 호출과 보수적 예상 예산을 제한했습니다. 운영 서비스의 과금 통제를 대신하지 않습니다.
- 실제 사용량은 입력 **1,683토큰**, 출력 **955토큰**입니다. 추론 579토큰은 출력에 포함되므로 별도로 더하지 않습니다.
- 공개 표준 단가 기준 두 호출 예상액은 **USD 0.06458**입니다. 2,000원/USD의 계획용 여유 환산을 쓰면 약 **129원**이며, 실제 청구액·실제 환율은 아닙니다. 이전 연결 시험의 예비액을 포함한 사전 계획 한도도 1,000원 이내였습니다.
- Azure 응답은 배포 별칭을 반환합니다. 이전에 운영자가 확인한 배포 모델 설정과 별칭을 대조하는 기존 식별 방식을 사용했습니다.

[공식 모델·가격](https://developers.openai.com/api/docs/models/gpt-6-astra) · [Azure Responses API](https://learn.microsoft.com/en-us/azure/foundry/openai/how-to/responses) · [Azure 호출 한도](https://learn.microsoft.com/en-us/microsoft-cloud/dev/dev-proxy/concepts/azure-openai-429-token-rate-limit)

## 다시 실행하는 방법

아래 앞·뒤 호출은 **실제 과금**됩니다. 자동 CI에서는 실행하지 않습니다. 기존 개발용 원장은 두 호출을 이미 사용해서 그대로 다시 실행하면 차단됩니다. 키는 서버 환경변수를 사용합니다.

```powershell
python scripts/run_focused_azure_critic.py --live-public-synthetic --phase before --source docs/evaluation/critic-revision-replay-2026-10-27.json --output data/local/azure-before.json
python scripts/replay_critic_revision.py --output data/local/azure-code-repair.json
python scripts/run_focused_azure_critic.py --live-public-synthetic --phase after --source data/local/azure-code-repair.json --before-receipt data/local/azure-before.json --output data/local/azure-after.json
```

앞·뒤 모델 호출은 현재 배포의 RPM 제한에 맞춰 간격을 두어야 합니다. 수정 후 호출은 이전 성공 응답과 동일한 KG·견적 근거인지 확인한 뒤에만 실행합니다. 예산 원장 자체를 지워서 자동 재시도하지 않습니다.

기존 응답을 재생 화면으로 만드는 아래 명령은 **모델을 호출하지 않습니다.**

```powershell
python -m scripts.build_azure_critic_replay --code-trace docs/evaluation/azure-critic-code-repair-2026-10-27.json --before docs/evaluation/azure-critic-before-2026-10-27.json --after docs/evaluation/azure-critic-after-2026-10-27.json --output data/local/azure-replay.json --visual data/local/azure-replay.html
```

## 현재 서비스와의 관계

일반 `/analysis/review`·`/analysis/site-review`의 Azure Critic 어댑터는 이미 있습니다. 이번 집중 검사는 같은 어댑터·구조화 출력·인용 검사로 실행했습니다. `focused_critic` 작업은 개발용 집중 검사이며 공개 API를 추가한 것은 아닙니다.

현재 로컬의 **전체 근거 자동 전송 설정은 계속 비활성**입니다. 전체 파이프라인을 켜려면 긴 입력의 배포 할당량·비용 한도·근거 선택과 백엔드 서비스 인증을 맞춰야 합니다. 이번 검사는 별도 명시 호출로 진행했습니다. 전체 법규·시설·탄소의 GPT 반박 품질과 서비스의 자동 부분 재실행은 후속입니다.
