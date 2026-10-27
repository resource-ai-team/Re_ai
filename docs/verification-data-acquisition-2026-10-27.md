# 검증 자료 확보: Azure 조사와 국내 공개 자료

2026-10-27 확인. 공개 원본 **13개**를 실제로 내려받아 로컬에 보관했습니다.
자료는 검증 준비용으로 정리했으며 운영 DB·KG에는 아직 넣지 않았습니다.
모델·지도 유료 호출, 공유 Azure 리소스 생성·설정 변경, 외부 문의는 하지 않았습니다.

## Azure에서 받을 수 있는 것

| 확인한 서비스 | 프로젝트에 도움이 되는 자료 | 접근 조건과 이번 확인 범위 |
| --- | --- | --- |
| [Azure Open Datasets](https://learn.microsoft.com/en-us/azure/open-datasets/dataset-catalog) | 확인한 목록은 교통·인구·경제·공공안전 등이 중심 | 국내 폐기물 성적서·시설 허가·견적이 연결된 완성 사례는 목록에서 찾지 못함. Azure 전체에 그런 자료가 없다는 뜻은 아님 |
| [Dynamics 365 Sustainability](https://learn.microsoft.com/en-us/dynamics365/sustainability/support-faq), 구 Sustainability Manager | [EPA·IPCC·DEFRA 등 계수 라이브러리](https://learn.microsoft.com/ko-kr/industry/sustainability/get-started-plans) | 별도 제품의 테넌트·사용자 라이선스 필요. 교육용 Azure 구독에 이용권이 있는지는 확인하지 않음. 데모 계수는 검증 근거에서 제외 |
| [IEA 계수 패키지](https://marketplace.microsoft.com/en-us/product/saas/mscrm.sust_iea2025?tab=overview) | 전력 관련 계수를 보충할 후보 | Premium용 패키지. Azure 크레딧만으로 자동 제공·재배포·프로젝트 API 사용이 가능하다고 확인한 것은 아님 |
| [Azure Maps 최신 Route](https://learn.microsoft.com/en-us/azure/azure-maps/routing-coverage) | 한국 도로거리·예상 시간·트럭 경로 | 공식 표에 한국 지원 표시. [한국 데이터 처리 설정](https://learn.microsoft.com/en-us/azure/azure-maps/how-to-manage-consent)이 필요. 계정 키·현재 구독의 권한·과금 조건·실제 API 응답은 확인 전 |

Azure Maps는 기존 카카오·네이버 어댑터에 추가할 수 있는 후보입니다. 아직 우리 코드에 Azure Maps 어댑터를 붙인 상태는 아닙니다.
이들 서비스에서 특정 회사의 이번 폐기물 로트, 실시간 잔여 용량, 실제 인수·계약단가를 얻을 수 있다고 확인하지는 못했습니다.

## 실제로 받은 국내 자료

| 자료 | 확보한 양 | 쓸 수 있는 곳 | 확인되지 않는 것 |
| --- | ---: | --- | --- |
| [한국환경공단 가격조사](https://www.data.go.kr/data/3076421/fileData.do) | 26품목 × 8권역 = 208칸. 공개 숫자 100개, 미공표 108개 | 가공형태·지역·시점별 참고가격과 미확인 표시 검사 | 특정 기업의 매입·처리 견적, 수율·가공비. CSV에 없는 VAT·물류비 포함 여부도 추정하지 않음 |
| [광산구 재활용업체](https://www.data.go.kr/data/15055958/fileData.do) | 55행 | 업종·폐기물 종류·좌표·기준일 조사 | 특정 수지 세부등급·오염 한도·이번 로트 인수 |
| [화성시 사업장폐기물 처리업체](https://www.data.go.kr/data/3046882/fileData.do) | 1,405행 | 처리업종·업체·주소의 지역 조사 | 공정 수행 근거, 인수 규격, 처리능력 |
| [낙동강유역환경청](https://mcee.go.kr/ndg/web/board/read.do?boardId=1878510&boardMasterId=160&condition.hideCate=1&menuId=3245) | Excel 1개·344행 | 영업대상 폐기물·업종 구분, 2026-07 기준 | 대부분 주소 열이 사무실 소재지. 처리시설 수거 좌표로 확정하지 않음 |
| [금강유역환경청](https://mcee.go.kr/gg/web/board/read.do?boardId=1841750&boardMasterId=228&menuId=2246) | Excel 1개·277행 | 대상 폐기물·처리방법·최초허가일·공시 용량, 2026-01 말 기준 | 원허가증과 다른 내용이 있을 수 있다고 기관이 안내. 8행은 전용용기 제조업으로 처리업체 추천 대상에서 제외 |
| [전북지방환경청](https://mcee.go.kr/smg/web/board/read.do?boardId=1845700&boardMasterId=282&menuId=826) | Excel 1개·63행 | 지정·의료 폐기물과 업종별 영업대상, 2026-02-26 기준 | 현행 허가·가동·잔여 용량 |
| [대구지방환경청](https://mcee.go.kr/daegu/web/board/read.do?boardId=1865350&boardMasterId=499&menuId=703) | Excel 1개·237행 | 업종·영업대상 폐기물, 2026-05-20 기준 | 로트 수탁·현행 허가증 원본 |

환경청 자료 합계는 **921행**이고, 공시 용량 셀이 있는 행은 **123행**입니다.
행 수는 고유 시설 수가 아닙니다. 지역 간·기존 전국 명부 9,604건과의 중복은 아직 대조하지 않았습니다.
공시 용량은 현재 남은 용량이 아니며, `㎥/일`을 밀도 없이 `톤/일`로 환산하지 않습니다.

가격은 **2026-10-10 조사·2026-10-26 공표** 자료입니다. 조회일을 가격 조사일로 바꾸지 않습니다.
예를 들어 수도권 PP는 압축품 381원/kg, 플레이크 533원/kg, 펠렛 802원/kg입니다.
수율·가공비·운송비가 없으므로 가격 차이를 이익으로 표시하거나 펠렛을 최적이라고 결정할 수 없습니다.

## 실제 사례를 구성할 공고·문서

| 자료 | 원문에서 확인한 사실 | 검증에 쓸 문제 |
| --- | --- | --- |
| [SR센터 2026 폐합성수지 처리 공고](https://srcenter.kr/bidding/?bmode=view&idx=168684088) + 과업지시서 HWP | 연간 예정물량 400,000kg, 기초단가 310원/kg, 기초금액 124,000,000원. 상차·운반·처리·VAT 포함. 계약 목적과 허가 범위 요구 명시 | 비용 중복 합산, 기초금액과 낙찰단가 혼동, 혼합수지 조성 추정, 공고별 조건 누락 |
| [SR센터 2025 파쇄수지 매각 공고](https://srcenter.kr/bidding/?bmode=view&idx=157899470) | 소형가전 파쇄수지 878,770kg, 예정단가 258원/kg. 계약 예정기간은 2026-03-31까지 | 과거 예정가격을 현재 거래가로 오인, 처리 비용과 매각 수익 혼동 |
| [SR센터 2026 금속류 매각 공고](https://srcenter.kr/bidding/?bmode=view&idx=168784452) | 비압축 A샷시 23,908kg, 예정단가 2,660원/kg | 샷시를 음료캔 공정에 그대로 적용, 예정가격을 개별 견적으로 오인 |
| [해운대백병원 제안요청서](https://www.paik.ac.kr/haeundae/cmmn/file/fileDown.do?atchFileId=35378fb2b66c4023bdb03f9b9d777d7b&bbsId=BMSR00064&fileSn=2&menuNo=500124) | 20쪽. 폐지·알루미늄캔 등 업무 범위와 계약 예정기간 | 문서에 적힌 업무 범위와 개별 성적서·견적을 구분. 가격란이 없다고 가격을 생성하지 않음 |

공고의 예정물량은 실제 처리 실적이 아니고, 기초단가는 낙찰단가가 아닙니다.
서로 다른 폐기물의 매각·처리 공고를 동일 로트의 대안으로 합치지 않습니다.
위탁 공고의 SRF 등 제한은 해당 계약 조건이며 모든 폐합성수지의 법적 의무로 일반화하지 않습니다.

## 추가 탄소 참고 자료

[US EPA WARM v16](https://www.epa.gov/waste-reduction-model/versions-waste-reduction-model)의 원본 XLS도 확보했습니다.
25개 시트와 Summary Data 표의 소재명 있는 62행을 읽었습니다. 나머지 빈 행은 소재로 세지 않았습니다.
표의 캐시값을 읽은 것으로 Excel 재계산 검증은 아닙니다.
이는 미국 조건의 비교 LCA입니다. 국내 공장 실측이나 국가 LCI를 대신하지 않습니다.
회피배출·운송 포함 범위와 short ton 기준을 대조해야 하므로 국내 후보의 기본 탄소 순위에는 적용하지 않았습니다.

## 지금 준비된 검증 범위

[공개 사실 묶음 6개](../data/config/public_verification_case_facts_v1.json)를 정리했습니다.
공고의 수치·산술, CSV 가격, Excel 용량 셀을 원본과 대조했습니다.
이 묶음은 개발자 점검용이며 독립 평가셋·전문가 법률 정답셋이 아닙니다. Critic 평가도 아직 실행하지 않았습니다.

- 가능한 검사: 포함 비용 중복, 단위·산술 오류, 소재·형상 오인, 과거 자료 오용, 자료 없는 사실의 확정, 명부의 처리업·운반업·용기 제조업 혼동.
- 아직 없는 완성 사례: 같은 로트의 성적서 + 검수된 시설 허가·인수 조건 + 여러 견적 + 실제 도로 경로 + 동일 경계의 탄소 + 검토자가 확인한 결론.
- 공개 자료로 남는 구멍: 실제 로트 시료, 최종 계약단가, 현재 인수·잔여 용량. 웹 명부나 다른 사업장의 보고서로 대신 채우지 않습니다.

다음 검증은 공개 사실에 대한 올바른 해석과 잘못된 확정을 먼저 평가합니다.
거래 가능한 최적 시설의 정답 정확도는 완성된 실제 사례가 확보된 뒤 별도로 평가해야 합니다.

## 저장 위치와 재현

- 원본·각 파일 SHA-256·수집시각: `data/local/acquisition-2026-10-27/`.
- 정리한 가격·환경청 명부·지역 명부·목록: 같은 폴더의 `prepared/`.
- [출처·기준일·라이선스·용도 목록](../data/config/verification_sources_v1.json).
- [수집·셀 대조 검사 기록](evaluation/verification-data-acquisition-2026-10-27.json).
- 원본·사업자별 행·파생 원문은 Git 제외. 공개 자료여도 원문 재배포·모델 전송 허용으로 자동 해석하지 않음.
- 네트워크 수집은 공개 사이트의 다운로드 절차로 진행. 공공데이터포털의 CAPTCHA 필요 여부를 확인했고 필요 없음 응답인 파일만 내려받음. 인증 우회 없음.

다운로드를 재현할 때는 위 원본 링크에서 파일을 받아 새 스냅샷 폴더에 저장하고 수집시각·해시 manifest를 준비합니다.
manifest 형식과 파일명은 검사 기록의 각 출처 및 기존 로컬 manifest를 참고합니다. 파일이 바뀌면 기존 해시로 통과시키지 않습니다.
로컬 원본이 있는 환경에서는 다음 명령으로 정리 결과를 재현합니다. `openpyxl`이 포함된 Python을 사용합니다.

```powershell
python scripts/prepare_verification_data.py `
  --input data/local/acquisition-2026-10-27 `
  --output data/local/acquisition-2026-10-27/prepared-again
```

출력 폴더가 이미 있거나 원본 해시·CSV 형식이 다르면 멈춥니다. 원본 덮어쓰기·운영 DB 쓰기는 하지 않습니다.
HWP·XLS의 추가 열람에 쓴 `olefile 0.47`, `xlrd 2.0.2`는 무시되는 로컬 reader-deps에만 설치했습니다.
프로젝트 운영 의존성·모델 설정은 변경하지 않았습니다.

검사: 정리 스크립트 단위 테스트 **13개 통과**, 공고 3개의 공개 수치·Decimal 산술, PP 가격 3종, 환경청 용량 셀 대조 통과.
이는 수집·정리 검증입니다. 기존 전체 엔진 테스트 911개를 이번에 다시 실행했다는 뜻은 아닙니다.
