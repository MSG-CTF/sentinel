# sentinel

`sentinel`은 MSG-CTF 운영 중 본 CTF 서버와 문제 인스턴스 상태를 감시하는 SLA 모니터링 및 자가회복 서비스입니다.

직접 사용자 요청을 처리하거나 문제 인스턴스를 생성/삭제하지 않고, 장애를 감지한 뒤 scheduler와 admin에 필요한 신호를 전달합니다.

```text
sentinel = 감지 + 판단 + 신호 전달 + 이벤트 기록
```

## 목적

- Django 기반 본 CTF 서버의 health 상태를 감시합니다.
- 문제 인스턴스의 health, TTL, idle timeout 상태를 감시합니다.
- quota, budget, provider, broker 장애를 감지합니다.
- 자동 처리 가능한 문제는 scheduler signal로 전달합니다.
- 사람이 확인해야 하는 문제는 admin alert로 전달합니다.
- 감지 결과와 전송 결과를 event로 기록합니다.

## 기술 스택

- Language: C#
- Framework: .NET 8 이상
- API: ASP.NET Core Web API
- Background Job: `BackgroundService`
- Configuration: `appsettings.json`, Options Pattern
- HTTP Client: `IHttpClientFactory`
- Logging: `Microsoft.Extensions.Logging`
- Test: xUnit

## 초기 프로젝트 구조

```text
sentinel/
  src/
    Sentinel.Api/             # sentinel 자체 API
    Sentinel.Monitor/         # health check, timeout, recovery policy
    Sentinel.Contracts/       # signal, event, alert 모델
    Sentinel.Infrastructure/   # Django, scheduler, broker, admin 연동

  tests/
    Sentinel.Tests/
    Sentinel.IntegrationTests/
```

## 주요 감시 대상

- Django 본 CTF 서버
  - `/health`
  - `/api/status`
  - database/cache 상태
  - 주요 API 상태
- 문제 인스턴스 Runtime
  - instance health
  - TTL
  - idle timeout
- Broker / Provider
  - quota
  - budget
  - provider failure
  - broker unavailable

## ctf-mock과의 관계

`ctf-mock`은 sentinel 개발과 통합 테스트를 위한 Django 기반 mock 서버입니다.

실제 본 CTF 서버, scheduler, broker, admin alert 채널이 준비되기 전까지 sentinel은 `ctf-mock`을 바라보며 연동 흐름을 검증합니다.

ctf-mock이 제공하는 주요 API는 다음과 같습니다.

- `GET /health`
- `GET /api/status`
- `GET /api/instances`
- `GET /api/instances/{instanceId}/health`
- `GET /api/broker/accounts`
- `POST /api/scheduler/signals`
- `POST /api/admin/alerts`
- `POST /api/scenarios`

## 개발 규칙

작업은 `dev` 브랜치를 기준으로 진행합니다.

브랜치 이름은 이슈 번호를 포함합니다.

```text
feat/#2-dotnet-bootstrap
docs/#1-sentinel-docs
fix/#10-health-check
```

커밋 메시지는 작업 종류를 명확히 드러내는 형식을 사용합니다.

```text
[Docs] Sentinel 기술 명세 작성
[Feat] Health Check Worker 추가
[Fix] Health Signal 생성 오류 수정
```

## 보안 및 로깅 규칙

로그, metric, alert에는 다음 값을 남기지 않습니다.

- password
- credential
- secret
- token
- raw token
- flag
- raw request body
- session cookie

외부 응답은 그대로 기록하지 않고, 필요한 필드만 추출해 event, signal, alert로 변환합니다.

## 다음 작업

1. .NET 솔루션 기본 구조 생성
2. Sentinel 계약 모델 정의
3. ctf-mock 연동 클라이언트 구현
4. Django 서버 Health Check Worker 구현
5. 문제 인스턴스 Health Check Worker 구현
6. Scheduler Signal 및 Admin Alert 연동 구현
7. Timeout 및 Resource Risk 감시 구현
