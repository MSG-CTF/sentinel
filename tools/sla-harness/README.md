# SLA 하네스

Sentinel 구현을 계약 기준으로 판정하는 도구. 감시 대상 실물과 신호 수신자가
아직 없으므로 둘 다 확정 계약 형태로 흉내 내고, Sentinel이 그 사이에서
올바르게 감지하고 발신하는지 검사한다.

Sentinel 구현이 .NET이든 Python이든 HTTP 계약만 지키면 같은 하네스로 판정한다.
표준 라이브러리만 사용한다.

## 계약

contract.py가 단일 기준이다. 모든 키 snake_case, 식별자 UUID 문자열,
시각 ISO 8601 UTC Z.

health_signal (스케줄러 자동 처리 신호)

```json
{
  "signal_id": "uuid",
  "target_id": "감시 대상 식별자",
  "target_type": "BACKEND | KOTH | INSTANCE | SCHEDULER | BROKER",
  "status": "HEALTHY | DEGRADED | DOWN",
  "action": "RESTART | CLEANUP | RESCHEDULE | INVESTIGATE",
  "reason": "사유",
  "checked_at": "2026-11-08T10:00:00Z"
}
```

admin_alert (운영자 알림)

```json
{
  "alert_id": "uuid",
  "severity": "INFO | WARNING | CRITICAL",
  "title": "제목",
  "message": "내용",
  "source": "발신 컴포넌트",
  "created_at": "2026-11-08T10:00:00Z"
}
```

## 구성

- targets.py: 가짜 감시 대상. 백엔드 /healthz 18010, KOTH /healthz 18090,
  스케줄러 인스턴스 이벤트 18001, 장애 주입 컨트롤 18011
- receivers.py: 가짜 수신자 18020. 수신 즉시 계약을 검증해 위반을 기록
- run_scenarios.py: 시나리오 판정 러너

## 실행

터미널 2개로 대상과 수신자를 띄운다.

```bash
python tools/sla-harness/targets.py
```

```bash
python tools/sla-harness/receivers.py
```

감시자를 Sentinel로 실행한 뒤 판정한다. Sentinel의 감시 대상을 위 포트로,
발신 대상을 18020으로 설정한다.

```bash
python tools/sla-harness/run_scenarios.py
```

구현 전이면 S2부터 전부 실패가 정상이며 그 목록이 구현 체크리스트다.

하네스 자체가 멀쩡한지 확인하려면 self test를 쓴다. 하네스가 스스로 감시자
역할을 흉내 내 5개 시나리오가 전부 통과해야 한다.

```bash
python tools/sla-harness/run_scenarios.py --self-test
```

## 시나리오

1. S1 정상 상태에서는 어떤 신호도 없어야 한다
2. S2 백엔드 database 장애를 주입하면 기한 안에 admin_alert가 온다
3. S3 KOTH healthz가 죽으면 target_type KOTH의 health_signal이 온다
4. S4 인스턴스가 FAILED가 되면 action CLEANUP 신호가 온다
5. S5 수신된 모든 payload의 계약 위반이 0건이다
