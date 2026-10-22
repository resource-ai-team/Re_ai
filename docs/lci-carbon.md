# LCI 기반 PET 공정 탄소 예비 평가 (내부 연구용)

이 모듈은 **W2RKG 경로의 일부**를 국가 LCI DB(ILCD ZIP)에 보수적으로 매핑한
*예비 탄소 추정* 도구입니다. 현장 실측·검증·공장 인수 승인·Pareto 최적 추천이 아닙니다.

## 원본 데이터와 사용 조건

공식 배포된 다음 ILCD ZIP을 **직접 내려받아** `data/local/lci/`에 둡니다.

- `LCI-2023-168.zip`: 폐PET 병 → 재생 PET 플레이크, 기준 흐름은 **제품 1 kg 산출**
- `LCI-2023-188.zip`: 폐PET 소각, 기준 흐름은 **폐기물 1 kg 투입**
- `LCI-2024-411(1).zip`: 신규 PET 제조. Climate change LCIA 총계가 원본에 없으면 `UNAVAILABLE`

데이터셋은 별도 라이선스가 있으며, 원본 ZIP과 파생 원시 흐름은 Git에
커밋하지 않습니다. 배포·상용 이용은 제공기관의 라이선스를 확인하세요.

## 실행

```bash
mkdir -p data/local/lci
# 사용자가 보유한 ZIP을 위 디렉터리에 복사한다 (저장소에는 미포함).
python -m pip install -e '.[dev]'
resource-lci inspect --data data/local/lci
resource-lci evaluate --data data/local/lci --candidates data/config/lci_pet_demo.json
pytest -q tests/unit/test_lci_carbon.py
```

### KG ProcessPlan 직접 연결 (일부 경로만)

`/kg/process-plans`가 반환한 `items` 배열을 JSON으로 저장한 뒤:

```bash
resource-lci evaluate-kg --data data/local/lci --candidates plans.json \
  --material PET --waste-form PET_BOTTLES --input-mass-kg 1000
```

`candidate_from_plan`은 검증된 `research_packet.stages[].transition.key` 순서만
사용합니다. **`pet-prepare → pet-wash → pet-finish`** 전체 경로가 정확히
일치할 때만 재생 PET 플레이크 통합 LCI에 매핑됩니다. `pet-pelletise`가
포함되거나 다른 공정 단계가 누락되면 `UNMATCHED`를 반환하며 계수를
생성하지 않습니다. 시설 역할은 조사 가설이므로 국가 평균을 개별 공장의
실측값으로 바꾸지 않습니다.

별도 입력용 후보 JSON은 `data/config/lci_pet_demo.json`을 참고하세요.

## 계산 방식

재활용 LCI에서 원본 투입–산출 관계는 **재생 플레이크 1 kg당 폐PET 병 1.27949 kg**입니다.

- 폐PET 병 1,000 kg → `1000 / 1.27949` kg 재생 플레이크
- 총 예비 배출량 → `산출량 × 0.57164725210341 kgCO₂e/kg 플레이크`
- 소각은 별도 공정 기준 → `1,000 × 2.45408129128249 kgCO₂e/kg 폐PET`

이 두 숫자는 **처리 결과물 및 시스템 경계가 다릅니다.** 재활용 데이터에
수거·운송이 포함된 반면 소각은 외부 수거 운송이 제외되어 있으므로
직접 비교하거나 감축률로 사용하면 안 됩니다. `compare_candidates`는
자동 순위화를 차단합니다. 방법론 단위 메타데이터 일부가 ZIP에 없어
`ESTIMATED_REVIEW_REQUIRED`를 유지합니다.

이 모듈은 실제 운송견적·도로거리·회피 배출·공정별 원가와는 독립적입니다.
원본 LCI를 KG 단계마다 반복 합산하지 않으며, 같은 경로에 운송을 중복으로
추가할 가능성이 있으면 결과를 보류합니다.

## 프로젝트 통합 범위

현재 CLI와 Python 함수가 구현되어 있습니다.

- `load_local_datasets` → 원본 파싱 및 SHA-256 근거
- `candidate_from_plan` → 실제 KG `ProcessPlan` 전이 키의 정확한 매핑
- `evaluate_candidate` → 질량·경계 검증과 산술
- `compare_candidates` → 비교 불가능 후보 자동 정렬 차단

기존 `/analysis/compare-scenarios`, 법규, Critic, Neo4j 쓰기 경로를 변경하지
않습니다. 별도 검증된 일치 조건과 출처·사업장 데이터가 확보되면
후속 PR에서 API/수치 비교 경로에 연동해야 합니다.
