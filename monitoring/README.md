# Sentinel monitoring

민수님의 [PR #15](https://github.com/MSG-CTF/sentinel/pull/15)의 Prometheus 수집기와 Grafana 구성을 실제 PlatformMonitor에 연결했다
기존 DjangoHealthCheckService 메트릭 인터페이스와 테스트도 유지한다

운영 /metrics는 관리자 세션 인증이 필요하다
AWS에는 추가 Prometheus·Grafana 컨테이너를 배포하지 않으며 대시보드는 /api/history의 최근 120회 점검을 사용한다
프로세스를 재시작하면 이 짧은 이력은 초기화되며 장애 이벤트 파일은 보존된다

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
현재 작업에서는 AWS의 실제 /metrics 응답과 내장 대시보드를 검증하며 선택 구성의 Grafana 실행은 별도다
