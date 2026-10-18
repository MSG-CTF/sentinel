# 런타임 API·PostgreSQL 감시

runtime은 기존 서비스·CPU·메모리·디스크 보고를 유지하고 runtime-api는 HTTPS 관리 API와 영속 저장소를 별도로 점검한다
운영 프로필은 GitHub aws-production 환경의 SENTINEL_PROFILE_JSON에서 관리하며 토큰 값은 기존 SENTINEL_PROBE_SECRETS_JSON의 RUNTIME_API_TOKEN을 재사용한다
저장소·브라우저·URL에는 토큰을 넣지 않는다

```json
{
  "target_id": "runtime-api",
  "name": "런타임 API·저장소",
  "role": "RUNTIME",
  "track": "RUNTIME",
  "kind": "runtime_healthz",
  "url": "https://runtime.example.test/internal/v1/healthz",
  "auth_token_env": "RUNTIME_API_TOKEN",
  "enabled": true,
  "evidence_scope": "인증된 API와 PostgreSQL 작업·바인딩 조회 · 실행 클러스터와 문제 기능은 별도 점검"
}
```

실제 운영 IP는 Secret의 프로필에서만 설정한다
프로필의 HTTP 주기와 제한 시간을 적용하며 현재 운영값은 15초·6초다
수신 API의 DB 조회 제한은 2초다

| 응답·근거 | 판정 | 원인 코드 |
| --- | --- | --- |
| 200, postgres, database ok, 최근 측정 | HEALTHY | RUNTIME_API_READY |
| 503, postgres, database down, 최근 측정 | DOWN | RUNTIME_DATABASE_DOWN |
| 503, memory, database not_configured | DOWN | RUNTIME_STORE_NOT_DURABLE |
| 인증 실패·경로 없음·계약 불일치 | UNVERIFIED | 실제 원인에 대응하는 코드 |
| 503 프록시 HTML·손상된 JSON | DOWN | HTTP_UNAVAILABLE |
| 연결 실패·시간 초과·502 | DOWN | 실제 통신 오류 코드 |

계약은 status·store_mode·database·mode·checked_at 다섯 문자열을 요구한다
같은 필드의 중복, HTTP 코드와 본문 상태 불일치, 30초보다 오래된 시각, 5초보다 앞선 미래 시각은 정상으로 인정하지 않는다
deployment_bridge는 배포 중 API만 제공하는 의도된 모드이며 저장소 조회가 성공하면 정상이다
측정 시각을 그대로 사용해 같은 캐시 응답이 복구 알림·상태 이력에서 두 번의 정상 측정으로 집계되지 않게 한다

Discord 경고와 복구는 기존 영속 상태·중복 방지·서로 다른 정상 측정 2회 정책을 따른다
DB 장애가 계속되거나 SLA가 재시작해도 같은 단계의 경고를 주기적으로 반복하지 않는다
프로세스가 실행 중이면 API·DB 장애만으로 SSH 기동을 허용하지 않는다
실제 호스트 서비스가 최신 보고에서 inactive 또는 failed인 경우만 기존 고정 기동 기능을 사용한다

실제 장애 유발은 일회용 PostgreSQL에서만 검증한다
운영 DB를 정지하거나 실행 중인 문제를 중단해 경고를 시험하지 않는다
