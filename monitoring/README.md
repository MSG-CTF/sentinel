# Sentinel monitoring

민수님의 [PR #15](https://github.com/MSG-CTF/sentinel/pull/15)의 Prometheus 수집기와 Grafana 구성을 실제 PlatformMonitor에 연결했다
기존 DjangoHealthCheckService 메트릭 인터페이스와 테스트도 유지한다

운영 /metrics는 관리자 세션 인증이 필요하다
AWS는 같은 EC2에서 Prometheus와 Grafana를 실행하며 관리자 로그인 후 /grafana/로 접속한다
민수 PR의 대시보드 UID와 수집기 인터페이스를 유지하고 실제 자산 지표·미확인 상태를 표시한다
내장 대시보드의 /api/history는 최근 120회 점검이며 재시작 시 초기화된다
Grafana에서 조회하는 Prometheus 데이터와 Grafana 설정은 별도 호스트 디렉터리에 보존된다

## AWS 연결

- Caddy는 /grafana/의 모든 요청에서 Sentinel 관리자 세션을 검사한다
- 외부에서 보낸 사용자 헤더는 제거하고 인증된 사용자만 Grafana에 전달한다
- Grafana는 읽기 권한으로 자동 연결되며 별도 로그인 쿠키를 만들지 않는다
- Sentinel에서 로그아웃하면 Grafana의 페이지와 API도 접근할 수 없다
- Grafana 3000, Prometheus 9090, Sentinel 8080은 호스트 루프백에서만 수신한다
- Prometheus는 전용 토큰으로 /internal/metrics를 15초마다 수집한다
- 내부 인증·수집 경로는 공개 프록시에서 404로 차단한다
- 보관은 최대 7일 또는 TSDB 블록 256MB이며 WAL과 현재 메모리 블록의 디스크 사용은 별도다
- 메모리 제한은 Grafana 512MiB, Prometheus 256MiB다
- 공식 Grafana 13.2.4와 Prometheus 3.15.0 이미지를 digest로 고정한다
- 프로비저닝 설정과 대시보드는 앱 이미지에 포함되어 GitHub 배포 시 함께 적용된다
- 수집 또는 프록시 활성화가 실패하면 이전 앱·모니터링 컨테이너를 다시 시작한다

데이터베이스 스키마가 바뀌는 Grafana 버전 업그레이드는 데이터 백업과 복원 검증을 별도로 수행해야 한다

## 실제 자산 지표

- sentinel_target_health: 자산별 1 정상, 0 장애, -1 미검증·미연결·오래된 결과
- sentinel_target_latency_ms: 마지막 HTTP 응답 시간
- sentinel_target_checked_at_seconds: 마지막 관측 시각
- sentinel_django_check_success: backend_healthz 계약 검증 성공 여부
- sentinel_django_component_health: target_id·component별 Django, PostgreSQL, Redis 상태
- sentinel_event_storage_healthy: 상태 변경 기록 저장 가능 여부

HTML 200이나 오래된 관측값은 Django·DB·Redis 정상으로 표시하지 않는다
SSH 서비스 보고기의 정상은 systemd 프로세스 상태이며 Discord 기능 점검과 구분한다

## 로컬 Prometheus·Grafana

로컬 점검 프로필을 사용하는 Sentinel을 localhost:5000에서 실행한 뒤 GRAFANA_ADMIN_PASSWORD를 설정하고 docker-compose.monitoring.yml을 실행한다
Prometheus는 host.docker.internal:5000/metrics를 수집한다
두 도구의 공개 포트는 127.0.0.1에만 바인딩한다
이 선택 구성은 AWS의 관리자 로그인 경로에 적용하는 설정이 아니다
AWS 설정은 deploy/aws/deploy_host.py에서 생성하며 로컬 Compose와 인증 방식을 공유하지 않는다
