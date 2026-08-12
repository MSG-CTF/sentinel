# Sentinel Monitoring

Prometheus와 Grafana로 sentinel metric을 로컬에서 확인하기 위한 설정입니다.

## 실행 순서

1. ctf-mock 실행

```sh
cd /Users/mnsoo/ctf-mock
.venv/bin/python manage.py runserver 8000
```

2. sentinel 실행

```sh
cd /Users/mnsoo/sentinel
dotnet run --project src/Sentinel.Api --urls http://localhost:5000
```

3. Prometheus/Grafana 실행

```sh
cd /Users/mnsoo/sentinel
docker compose -f docker-compose.monitoring.yml up -d
```

## 접속 URL

```text
sentinel:   http://localhost:5000
metrics:    http://localhost:5000/metrics
Prometheus: http://localhost:9090
Grafana:    http://localhost:3000
```

Grafana 기본 계정은 `admin / admin`입니다.

## scenario 테스트

```sh
curl -X POST http://localhost:8000/api/scenarios \
  -H "Content-Type: application/json" \
  -d '{"scenario":"django_db_down","enabled":true}'
```

Prometheus query 예시:

```promql
sentinel_django_health_check_success
sentinel_django_health_status
sentinel_django_api_status
sentinel_django_admin_alert_candidates
```

scenario 초기화:

```sh
curl -X POST http://localhost:8000/api/scenarios \
  -H "Content-Type: application/json" \
  -d '{"scenario":"reset","enabled":true}'
```
