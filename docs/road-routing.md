# 실제 도로 경로 연결

이제 카카오모빌리티·네이버 Directions 어댑터가 있습니다. **지도 키가 없어 실도로 호출은 아직 검증하지 않았습니다.**
기본 설정은 비활성입니다. 키·외부 전송 허용·백엔드 인증을 설정한 경우에만 조회합니다.

## 어디에 들어가나

`사업장 + 폐자원 → KG 공정 후보 → 공정별 시설 명부 → 조사 대상 선택 → 도로 API → 도로거리와 시간 표시`

`POST /analysis/site-discovery`에 다음 옵션을 추가합니다.

```json
"road_routing": {"max_destinations": 10, "include_geometry": false}
```

각 `registry_candidates[].research_packet`에 `road_route`, `road_distance_km`,
`road_duration_seconds`가 붙고, 루트의 `road_routing.coverage`에 조사 범위를 표시합니다.
중복 시설은 요청 안에서 한 번 조회합니다. 각 공정 목록은 조회에 성공한 시설을 도로거리순으로,
미조회·실패 시설은 뒤에 보존합니다. 도로 API 실패를 직선거리로 채우지 않습니다.

직선거리는 **명부 검색·한정된 조회 대상 선택**에 남아 있습니다. 받은 명부 중 가까운 일부를
도로 조회하므로 전국 명부에서 도로상 가장 가까운 시설을 보장하지 않습니다.
좌표가 없는 시설, 페이지 밖 시설, 호출 한도 때문에 미조회한 시설은 확인 필요로 남습니다.
옵션을 생략하면 이전처럼 직선거리 조사만 합니다.

## 시설 간 이어지는 경로

사업장 조사에서는 모든 거리가 `발생 사업장 → 개별 시설`입니다. 이를 공정 전체의 운송거리로 합치지 않습니다.
시설 A·B·C를 실제로 선택했다면 `POST /analysis/road-routes`에 **사업장→A, A→B, B→C**를 보냅니다.

```json
{
  "mode": "chain",
  "include_geometry": false,
  "legs": [
    {"leg_id": "origin-to-a", "origin": {"latitude":37.2,"longitude":127.0},
     "destination": {"latitude":37.3,"longitude":127.1}},
    {"leg_id": "a-to-b", "origin": {"latitude":37.3,"longitude":127.1},
     "destination": {"latitude":37.4,"longitude":127.2}}
  ]
}
```

위 좌표는 합성 예시입니다. 연결이 끊긴 chain은 422입니다. 모든 구간 성공 시에만 총거리·총시간을
만듭니다. `mode=independent`는 여러 목적지 비교이며 합계를 만들지 않습니다.
거리 단위는 km, 시간은 초, geometry 좌표는 `[경도, 위도]`입니다.

## 서버 설정

`.env.example`의 아래 값을 설정하고 서버를 재시작합니다. 키는 브라우저·요청 본문·Git에 넣지 않습니다.

```dotenv
ROAD_ROUTING_ENABLED=true
ROAD_ROUTING_ALLOW_EXTERNAL=true
ROAD_ROUTING_PROVIDER=kakao
ROAD_ROUTING_SERVICE_TOKEN=<백엔드와 공유할 별도 토큰>
KAKAO_MOBILITY_REST_API_KEY=<서버 키>
# 네이버를 쓰면 PROVIDER=naver, 아래 두 값 사용
NAVER_MAPS_API_KEY_ID=
NAVER_MAPS_API_KEY=
```

백엔드는 `Authorization: Bearer <ROAD_ROUTING_SERVICE_TOKEN>`을 보냅니다.
외부 호출이 가능한 설정인데 서비스 토큰이 없으면 503, 인증이 틀리면 401입니다.
비활성·키 미설정은 외부 호출 없이 진단 상태를 반환합니다. 기존 다른 API 전체의 인증을 대체하지 않습니다.

- 요청은 최대 20구간. `ROAD_ROUTING_MAX_CALLS=10` 기본값으로 유료 호출 수를 제한합니다.
- 요청 내부 중복만 제거하며 요청 간 도로 결과 캐시는 없습니다.
- 구간 timeout 기본 5초, 다음 호출 시작을 막는 총 예산 기본 20초입니다. 엄격한 전체 응답시간 SLA는 아닙니다.
- 401/403·할당량 오류 후 추가 호출을 멈춥니다. 재시도·다른 지도사 자동 전환은 없습니다.
- 지도사에는 출발·도착 좌표와 경로 옵션만 보냅니다. 기업 ID·회사명·주소·폐자원 성분은 보내지 않습니다.
- 요청·결과는 서비스 내부에서 자동 영구 저장하지 않습니다. 운영 접근/디버그 로그·프록시 정책은 별도로 정해야 합니다.
  HTTPX INFO의 지도 URL은 가리지만 모든 네트워크 디버그 로그 마스킹을 보장하지 않습니다.

## 이 값으로 아직 확정하지 않는 것

지도 API의 기본 자동차 경로는 화물차 중량·높이·위험물 통행 가능성이나 폐기물 운반 허가를 보장하지 않습니다.
택시비·지도 연료비를 실제 운송 견적으로 사용하지 않습니다. `transport_cost_krw=null`,
`dispatch_approved=false`입니다. 비용·탄소 계산과 실제 시설 인수 확인은 후속 단계입니다.

검증: MockTransport로 좌표 순서·거리/시간 단위·도로 순위·부분 실패·인증·한도·로그 노출을 검사했습니다.
실제 키를 받은 뒤 같은 구간의 지도 화면과 API 결과를 대조하는 확인이 남아 있습니다.

공식 문서: [카카오 Directions](https://developers.kakaomobility.com/en/guide/navi-api/directions.html),
[카카오 결과 코드](https://developers.kakaomobility.com/en/guide/navi-api/reference.html),
[네이버 Directions 5](https://api.ncloud-docs.com/docs/en/application-maps-directions5).
