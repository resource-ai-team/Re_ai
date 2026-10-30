# Azure 직접 확인과 배출계수 확보 결과

확인일: **2026-10-27**. 로그인된 Codex 브라우저에서 Foundry, Azure Portal,
본인 Azure ML 작업 영역, Power Platform 관리센터를 직접 열었습니다.

**현재 지원은 모델·개발 인프라에 해당합니다. 프로젝트에서 바로 읽을 수 있는
폐자원·탄소 데이터 연결은 발견하지 못했습니다.** 대신 공식 공개 배출계수 원본을
직접 받아, 출처·단위·적용 범위를 붙여 로컬 검토 자료로 준비했습니다.

## Azure에서 실제 확인한 것

| 확인 위치 | 화면에서 확인한 결과 | 우리 프로젝트에서의 의미 |
| --- | --- | --- |
| 지원 Foundry 배포 | `gpt-6-astra`, Succeeded, GlobalStandard. 표시 할당량 1,000 token/min · 1 request/min | 모델 사용 가능. 전체 파이프라인의 처리량·문서 전송 한도는 별도 검증 필요 |
| Foundry → 관리 → 연결된 리소스 | 사용 가능한 데이터 없음 | 연결된 탄소 DB를 가져온 상태가 아님 |
| Foundry → 데이터 → 데이터 세트·파일 | 둘 다 없음 | 이 프로젝트에 미리 제공된 검증용 데이터는 발견하지 못함 |
| Foundry → 지식·인덱스 | IQ 연결 설정 화면, 인덱스 없음 | 외부 근거가 자동으로 검색되는 상태가 아님 |
| Azure Portal → 모든 리소스 | 현재 계정·선택 구독에서 163개, 종류 필터 19종 | 대부분 ML·저장소·모델·모니터링·네트워크. 목록에 AI Search·Azure Maps 리소스는 없었음 |
| 본인 Azure ML → 데이터 자산 | 데이터 자산 생성 안내, 등록 자산 목록 없음 | 본인 작업 영역에 등록된 탄소·폐자원 데이터 자산은 발견하지 못함 |
| 본인 Azure ML → 데이터 저장소 | 기본 Blob·파일 공유 저장소 4개 | 저장소 연결이 있는 것과 도메인 데이터가 제공되는 것은 구분해야 함 |
| 본인 Storage → 기본 ML 데이터 컨테이너 | 검색·필터 없이 항목 0개 | 기본 데이터 컨테이너에도 제공 데이터가 없었음. 다른 저장소 전체의 부재를 증명하는 것은 아님 |
| Power Platform 관리센터 | 로그인 성공. 관리·환경·Dynamics 앱 메뉴가 보이지 않음. 라이선싱 페이지는 찾을 수 없다는 화면 | Sustainability 설치 여부·사용권·IEA 접근권은 **미확인** |

이 결과로 테넌트 전체에 데이터나 라이선스가 없다고 단정하지 않습니다.
다른 학생의 저장소·키·업무 파일은 조사 대상에서 제외했습니다.
새 배포·컴퓨팅 시작·앱 설치·권한 변경·유료 모델 호출은 하지 않았습니다.

[Microsoft 계수 안내](https://learn.microsoft.com/en-us/dynamics365/sustainability/calculate-emission-factors)에
있는 DEFRA·EPA는 원 출처에서 직접 확보할 수 있습니다. Sustainability 제품의 계수
라이브러리는 별도 Power Platform/Dataverse 환경에서 설치·관리합니다.
Azure 모델 배포와 같은 서비스가 아닙니다.

IEA는 Premium 기능이며, 숫자 조회·PDF 출력에도 제한이 있습니다.
우리 엔진에 원본을 대량 추출해 넣어도 된다는 권한은 확인되지 않았습니다.
[IEA 공식 사용 조건](https://learn.microsoft.com/en-us/dynamics365/sustainability/calculations-emission-factors-iea)

## 실제 확보한 배출계수

원본·방법론 **7파일**의 크기·SHA-256·다운로드 시각을 기록했습니다.
원본 및 전체 추출 결과는 `data/local/emission-factors-2026-10-27/`에만 있습니다.

| 자료 | 원본에서 확인한 수치 | 쓰임새와 제한 |
| --- | --- | --- |
| 국내 GIR 2025년 승인 / 2023년 소비단 | **0.4173 tCO₂e/MWh = 0.4173 kgCO₂e/kWh** | 공정 전력 사용량이 있으면 전력 부분 계산 가능. 2026년 측정값이 아님 |
| 같은 자료 / 2021–2023년 소비단 평균 | **0.4330 tCO₂e/MWh = 0.4330 kgCO₂e/kWh** | 단년도와 평균을 임의 혼용하지 않음. 평가 기준을 정해 선택 |
| DEFRA 2026 / 일반 비냉장 디젤 화물차 평균 적재 | **0.10356 kgCO₂e/(tonne·km)** | 영국 차량 조건의 운송 참고값. 국내 차량의 실측값으로 쓰지 않음 |
| 위 운송의 연료 생산·공급 WTT | **0.02359 kgCO₂e/(tonne·km)** | 운행 배출과 다른 범위. 어떤 경계를 계산할지 정한 뒤 사용 |
| DEFRA / 폐PET open-loop·closed-loop·소각 | 모두 **4.65358 kgCO₂e/tonne** | 폐기물 발생자의 Scope 3 보고 범위. 공정 전체 LCA가 아니므로 경로 순위 산정 금지 |
| EPA 2025 / PET 재활용 | **0.23 metric tonnes CO₂e / US short ton material** | 운송·선별 범위. 재생 공정 전체 배출량이 아님. kg/metric tonne으로 오독하면 안 됨 |

국내 전력은 [GIR 공고와 첨부 PDF](https://www.gir.go.kr/home/board/read.do?boardId=86&boardMasterId=2&menuId=36)
1쪽 표·2쪽 주석을 눈으로 대조했습니다. 새 산정 기준은 IPCC 2006 지침·AR5 GWP이며,
주석에 병기된 옛 기준의 `0.3996`·`0.4164`와 섞지 않습니다.

DEFRA는 [2026년 공식 배포](https://www.gov.uk/government/publications/greenhouse-gas-reporting-conversion-factors-2026)의
**7월 31일 수정 flat 파일**을 읽었습니다. 미제공 값을 0으로 잘못 해석하지 않도록
빈칸을 유지합니다. 운송값은 `Factors by Category`의 J6389·J7228입니다.
영국 보고 체계라는 것과 모든 활동의 물리적 지역이 영국이라는 것은 다르므로,
전체 추출 행에는 보고 국가와 적용 지역 미확인을 따로 기록합니다.

EPA는 [2025 Hub 원본](https://www.epa.gov/climateleadership/ghg-emission-factors-hub)의
Table 9, D443과 C499 주석입니다. 운송이 포함되고 회피 배출은 제외되며 AR4 GWP입니다.
국내 전력 AR5·국내 LCI와 자동으로 합산·비교하지 않습니다.

### 왜 PET 재활용과 소각 숫자가 똑같은가?

DEFRA는 폐기물 발생 기업의 보고 범위에서 재활용·에너지 회수의 상당 부분을
수요 기업 쪽에 배분합니다. 위 4.65358은 공정 전체가 아니라 수거·첫 처리지 인계
범위입니다. 실제 소각과 재활용이 같은 배출량이라는 뜻이 아닙니다.

[방법론](https://assets.publishing.service.gov.uk/media/6a2940543b15d05a7ce3202e/2026-GHG-conversion-factors-methodology-report.pdf)
12.22–12.27절, 인쇄 123–124쪽은 이 폐기물 계수로 처리 대안의 생애주기 우열을
비교할 수 없다고 명시합니다. 운송을 이미 포함하므로 실제 도로거리 배출을 그대로
더하면 중복될 수 있습니다.

EPA Hub도 재활용의 운송·선별을 포함하지만 전체 재생 공정은 아닙니다.
대안 비교용 WARM과 보고용 Hub는 서로 바꿔 쓸 수 없습니다.
[EPA의 구분 안내](https://www.epa.gov/sites/default/files/2020-04/documents/guidanceefwastefactors_vs_warm.pdf)

## 지금 프로그램에 어디까지 쓸 수 있나?

**계수 확보와 수치 비교는 별개입니다.** 현재 작업은 출처를 검사하는 오프라인
자료 준비입니다. 서비스 DB·API의 탄소 순위 계산에 새 계수를 연결하지 않았습니다.

1. **전력 부분:** 현장 또는 검증된 공정 문헌의 kWh가 있으면 국내 계수로 산정할 수 있습니다.
   예를 들어 *가상 활동량* 1,000 kWh × 0.4173 = 417.3 kgCO₂e입니다.
   이것은 실제 공장의 사용량·공정 총배출량이 아닙니다.
2. **운송 부분:** 기존 카카오·네이버 도로 API에서 확인한 구간 거리, 물량,
   차량·적재율을 사용합니다. 국내 적합 계수 또는 명시적 해외 대용 가정이 필요합니다.
   지도 키가 없는 현재 상태에서는 도로거리도 미확인입니다.
3. **처리 공정 부분:** 기존 국가 LCI 7개를 정확히 맞는 경로에 사용합니다.
   PET 플레이크 재활용과 플라스틱 소각의 경계 차이는 그대로 남아 있습니다.
   새 DEFRA·EPA 자료로 그 차이가 해결된 것은 아닙니다.
4. **대안 비교:** 같은 폐자원 투입량, 처리 결과물·수율, 수거·운송·잔재물 처리,
   대체 원료·회피 배출, 국가·연도·GWP를 맞춰야 합니다. 미확인 부분은 0으로 채우지 않습니다.

따라서 다음 확보 우선순위는 **후보별 전력·연료·수율·잔재물 활동량**,
**실제 구간별 도로거리·차량**, **같은 경계의 국내 공정 LCI**입니다.
공장 실측이 없으면 문헌 기반 예비값과 불확실성을 표시해야 합니다.

## 준비한 파일과 재현

- `data/config/emission_factor_sources_v1.json`: 출처·해시·용도, 국내 전력 2개와 운송 참고값
- `scripts/prepare_emission_factors.py`: 로컬 원본 검증 → 범위·단위가 붙은 JSON 준비
- `tests/unit/test_prepare_emission_factors.py`: 누락·0·단위·원본 변조·범위 검사

```powershell
& '<문서용 Python 경로>' scripts/prepare_emission_factors.py `
  --root data/local/emission-factors-2026-10-27 `
  --output data/local/emission-factors-2026-10-27/prepared/factors.json
```

문서용 Python에는 `openpyxl`이 필요합니다. 파일을 읽기만 하며 원본 Excel을
다시 저장하거나 수식을 재계산하지 않습니다. EPA 원본의 일부 머리말·확장기능을
읽지 못한다는 경고가 있지만, 숫자 표·열 제목은 확인했고 원본은 변경하지 않았습니다.

준비 결과는 DEFRA 관련 4분류 **2,152행**, EPA 61소재 × 6방법 **366셀**입니다.
그중 숫자가 있는 셀 **1,838개**, 미제공 **680개**, 국내 전력 **2개**를 별도로 보존합니다.
이것을 1,838개의 국내 재활용 공정 LCI로 세면 안 됩니다.

새 파서 검사 11개와 앞선 공개자료 파서 검사 13개, **총 24개 통과**했습니다.
이는 자료 처리 검사이며 탄소 산정·Critic 정확도·현장 적용성의 검증 결과가 아닙니다.
