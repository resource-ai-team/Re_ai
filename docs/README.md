# 문서 안내

처음 보는 팀원은 루트 [README](../README.md)의 현재 흐름을 읽고, 아래에서 필요한 문서를 선택하면 됩니다.
문서에 적힌 목표 설계와 지금 실행되는 API, 이전 검증 기록을 구분합니다.

## 기준과 현재 상태

| 문서 | 볼 내용 |
| --- | --- |
| [확정 파이프라인 v2](pipeline-baseline.md) | 전체 서비스의 목표 단계·책임·Critic·부분 재실행 원칙. 모든 단계의 구현 완료를 뜻하지 않음 |
| [코드와 데이터 구조](architecture.md) | 지금 실행되는 기능·모듈·DB·서비스 호출 경계 |
| [후보별 조사·수치 비교](candidate-analysis.md) | 연결된 분석 API 두 개의 입력·출력·현재 한계 |
| [진행보드 반영 결과](progress-board-update.md) | Notion 기존 11개 수정·신규 3개·완료 3개 보존 및 카드별 남은 일 |

## KG를 개발하거나 연결할 때

| 문서 | 볼 내용 |
| --- | --- |
| [KG 기반과 실행](knowledge-graph.md) | 온톨로지·W2RKG 적재·기존 조회·검증 |
| [전체 후보 조사 묶음](kg-research-pool.md) | 문헌·기존 조건부 경로·조사 카드의 전체 페이지 조회·출처·외부 보기 |
| [공정·시설 경로 탐색](kg-process-plans.md) | 별도 상태 전이, 여러 공정 순서·시설 조합, 역할 가설·검색 한도 |
| [공정 근거와 빈 연결 보강](kg-evidence-upgrade.md) | 제지 10개 작업·PE 등급 구분·PP 확인 질문·출처 연결 범위·연결 진단 API |
| [국내 시설 추가 조사](kg-facility-matching.md) | 등록 명부 수집·적재·공정별 검색·직선거리와 확인할 조건 |
| [시설 공개 조건 대조](kg-facility-conditions.md) | 소재·명시적 제외·미확인 대조, 공정·운송 구간별 표시와 PP 사례 |
| [KG 고도화 계획과 기록](kg-upgrade-plan.md) | 추가할 데이터·기술과 각 조회 버전의 실행 기록 |

문헌 관계, 별도 공정 전이, 국내 명부는 자료 범위가 서로 다릅니다.
한 API의 후보 수를 다른 API의 경로 수나 국내 전체 시설 수로 사용하지 않습니다.
새 근거를 넣은 뒤에는 번들 검증·Neo4j 재적재·감사·관련 회귀 검사를 함께 수행합니다.

## API·데이터·실행

- [API 목록과 오류](api.md), 실행 중 서비스의 /docs: 각 내부 엔드포인트와 스키마.
- [분석 API 상세](candidate-analysis.md): /analysis/research, /analysis/compare-scenarios의 예제와 별도 가상 수치 시나리오.
- [데이터 관리](../data/README.md): 원본·가상 데이터·공식 수집 범위·사용 조건·Git 포함 여부.
- [루트 빠른 시작](../README.md#빠른-시작): Python·DB·적재·API 실행 순서.
- [팀 CONTRIBUTING](https://github.com/resource-ai-team/.github/blob/main/CONTRIBUTING.md): 레포 역할·브랜치·PR·API 변경 규칙.

## 검증 기록과 이전 설계

검증 JSON은 **기록에 적힌 데이터·코드·실행 범위의 결과**입니다. 최신 기능의 전체 완료율이나 실제 추천 정확도와 같지 않습니다.

| 기록 | 검증 범위 |
| --- | --- |
| [KG 조회](evaluation/knowledge-graph-2026-10-21.json) | 고정 문헌·참조 자료 조회와 연결 |
| [전체 후보 조사 묶음](evaluation/kg-pool-2026-10-21.json) | 후보·페이지·출처·자료 범위 |
| [공정·시설 경로](evaluation/kg-plans-2026-10-21.json) | 등록 상태 전이·경로 열거·시설 연결 |
| [KG 보강 후 재검증](kg-evidence-upgrade.md#이번-검증-결과) | 새 제지 순서·출처 범위·등급·연결 진단, 실제 활성 세대와 전체 테스트 |
| [후보 조사와 가상 산술](evaluation/analysis-2026-10-21.json) | 실제 KG/API와 로컬 검색 상태, 별도로 만든 가상 수치 계산 |
| [이전 파이프라인 v1](pipeline-baseline-v1.md) | 이전 의사결정 기록. 현재 구현의 기준은 v2 문서 |

단위·통합 테스트의 성공은 후보 누락·연결·계산·실패 처리 등을 확인한 결과입니다.
실제 법규 적용성·현장 인수·최종 추천 품질은 실제 자료와 전문가 정답셋으로 따로 검증해야 합니다.
