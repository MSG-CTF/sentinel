# SLA 흐름 근거

기준: [Sentinel 849f2677](https://github.com/MSG-CTF/sentinel/tree/849f267795a3878bf234eb5db7054177ad5614b8)

| 역할 | 코드 |
| --- | --- |
| 서비스 주기 조회·상태 판정 | [PlatformMonitor](https://github.com/MSG-CTF/sentinel/blob/849f267795a3878bf234eb5db7054177ad5614b8/src/Sentinel.Monitor/Platform/PlatformMonitor.cs) · [Worker](https://github.com/MSG-CTF/sentinel/blob/849f267795a3878bf234eb5db7054177ad5614b8/src/Sentinel.Monitor/Platform/PlatformMonitorRegistration.cs) |
| 전체 인스턴스 목록 | [SchedulerInventoryClient](https://github.com/MSG-CTF/sentinel/blob/849f267795a3878bf234eb5db7054177ad5614b8/src/Sentinel.Monitor/Instances/SchedulerInventoryClient.cs) |
| 노드·실행 환경 수집 | [Fleet 수집기](https://github.com/MSG-CTF/sentinel/blob/849f267795a3878bf234eb5db7054177ad5614b8/deploy/telemetry/BROKER-FLEET.md) |
| Discord 장애·복구 알림 | [DiscordAlerts](https://github.com/MSG-CTF/sentinel/blob/849f267795a3878bf234eb5db7054177ad5614b8/src/Sentinel.Monitor/Platform/DiscordAlerts.cs) |
| Prometheus·Grafana | [메트릭 수집 설정](https://github.com/MSG-CTF/sentinel/blob/849f267795a3878bf234eb5db7054177ad5614b8/monitoring/README.md) |
| 운영자 수동 조치 | [AssetOperations](https://github.com/MSG-CTF/sentinel/blob/849f267795a3878bf234eb5db7054177ad5614b8/src/Sentinel.Monitor/Platform/AssetOperations.cs) |

그림의 화살표는 상태 데이터와 결과의 전달 방향입니다
인스턴스 생성·종료와 SLA의 주기 수집은 독립적으로 동작합니다

## 화면 기록

운영 중 저장한 원본 캡처입니다 · 상태와 수치는 촬영 시점 기준입니다

| 화면 | 촬영일 | 표시 내용 |
| --- | --- | --- |
| [Dashboard](screens/dashboard.jpg) | 2026-11-03 | 서비스별 상태 · 위험 신호 |
| [KOTH Monitoring](screens/koth.jpg) | 2026-11-03 | 게임 서버 · 실행 준비 상태 |
| [Nodes](screens/nodes.jpg) | 2026-11-03 | K3s 노드 상태 · CPU · 메모리 |
| [Integration](screens/integration.jpg) | 2026-11-03 | 연결 경로 · 실행 검증 기록 |
| [Grafana](screens/grafana.jpg) | 2026-10-14 | 응답 시간 · 상태 이력 · 메트릭 수집 |
