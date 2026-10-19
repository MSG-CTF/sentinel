"""SLA 하네스 시나리오 러너.

전제: targets.py와 receivers.py가 떠 있고, 감시자(Sentinel 또는 self test)가
targets를 읽어 receivers로 신호를 보낸다.

기본 모드는 실제 Sentinel을 기다리며 판정한다. 아직 구현 전이면 전부 실패가
정상이고 그 목록이 구현 체크리스트다.

--self-test 모드는 하네스가 스스로 감시자 역할을 흉내 내 배관과 계약 검증기가
동작하는지 증명한다.
"""

import argparse
import json
import sys
import time
import urllib.request
import uuid
from datetime import datetime, timezone

CONTROL = "http://127.0.0.1:18011"
RECEIVER = "http://127.0.0.1:18020"
BACKEND_TARGET = "http://127.0.0.1:18010/healthz"
KOTH_TARGET = "http://127.0.0.1:18090/healthz"
EVENTS_TARGET = "http://127.0.0.1:18001/api/instances/active"

results = []


def check(name, condition, detail=""):
    results.append((name, bool(condition), detail))


def http_json(method, url, body=None, timeout=5):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method=method
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read() or b"{}")


def received(kind=None):
    _, body = http_json("GET", RECEIVER + "/received")
    entries = body.get("entries", [])
    if kind:
        entries = [entry for entry in entries if entry["kind"] == kind]
    return entries


def wait_for(predicate, seconds):
    deadline = time.time() + seconds
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(1)
    return predicate()


def now_z():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def self_test_watcher():
    # 하네스 배관 증명용 가짜 감시자. 대상을 읽고 규약대로 신호를 만든다
    status, backend = http_json("GET", BACKEND_TARGET)
    if status != 200:
        http_json(
            "POST",
            RECEIVER + "/internal/monitor/alerts",
            {
                "alert_id": str(uuid.uuid4()),
                "severity": "CRITICAL",
                "title": "backend health check failed",
                "message": "database " + backend.get("database", "unknown"),
                "source": "self-test-watcher",
                "created_at": now_z(),
            },
        )

    status, _ = http_json("GET", KOTH_TARGET)
    if status != 200:
        http_json(
            "POST",
            RECEIVER + "/internal/monitor/signals",
            {
                "signal_id": str(uuid.uuid4()),
                "target_id": "koth-main",
                "target_type": "KOTH",
                "status": "DOWN",
                "action": "INVESTIGATE",
                "reason": "healthz non-200",
                "checked_at": now_z(),
            },
        )

    _, events = http_json("GET", EVENTS_TARGET)
    for instance in events.get("data", {}).get("instances", []):
        if instance["status"] == "FAILED":
            http_json(
                "POST",
                RECEIVER + "/internal/monitor/signals",
                {
                    "signal_id": str(uuid.uuid4()),
                    "target_id": instance["instance_id"],
                    "target_type": "INSTANCE",
                    "status": "DOWN",
                    "action": "CLEANUP",
                    "reason": "instance status FAILED",
                    "checked_at": now_z(),
                },
            )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true", help="하네스 배관 자체 검증")
    parser.add_argument("--wait", type=int, default=20, help="감시자 반응 대기 초")
    options = parser.parse_args()

    sys.stdout.reconfigure(encoding="utf-8")
    wait = 2 if options.self_test else options.wait

    baseline = len(received())

    # S1 정상 상태에서는 신호가 없어야 한다
    time.sleep(wait if options.self_test else min(wait, 5))
    check("S1 정상 상태 무신호", len(received()) == baseline)

    # S2 백엔드 DB 장애 -> admin alert
    http_json("POST", CONTROL + "/toggle", {"target": "backend", "component": "database", "state": "down"})
    if options.self_test:
        self_test_watcher()
    ok = wait_for(lambda: len(received("admin_alert")) > 0, wait)
    alerts = received("admin_alert")
    clean = all(not entry["violations"] for entry in alerts)
    check("S2 백엔드 장애 알림 수신", ok and clean,
          "수신 " + str(len(alerts)) + "건, 위반 " + str(sum(len(entry["violations"]) for entry in alerts)))
    http_json("POST", CONTROL + "/toggle", {"target": "backend", "component": "database", "state": "ok"})

    # S3 KOTH down -> health signal
    http_json("POST", CONTROL + "/toggle", {"target": "koth", "state": "down"})
    if options.self_test:
        self_test_watcher()
    ok = wait_for(
        lambda: any(
            entry["payload"] and entry["payload"].get("target_type") == "KOTH"
            for entry in received("health_signal")
        ),
        wait,
    )
    check("S3 KOTH 다운 신호 수신", ok)
    http_json("POST", CONTROL + "/toggle", {"target": "koth", "state": "ok"})

    # S4 인스턴스 FAILED -> cleanup signal
    http_json("POST", CONTROL + "/toggle", {"target": "instance", "state": "FAILED"})
    if options.self_test:
        self_test_watcher()
    ok = wait_for(
        lambda: any(
            entry["payload"]
            and entry["payload"].get("target_type") == "INSTANCE"
            and entry["payload"].get("action") == "CLEANUP"
            for entry in received("health_signal")
        ),
        wait,
    )
    check("S4 인스턴스 FAILED 정리 신호", ok)
    http_json("POST", CONTROL + "/toggle", {"target": "instance", "state": "RUNNING"})

    # S5 수신 전량 계약 위반 0
    entries = received()
    total_violations = sum(len(entry["violations"]) for entry in entries)
    check("S5 계약 위반 0", len(entries) > 0 and total_violations == 0,
          "수신 " + str(len(entries)) + "건, 위반 " + str(total_violations) + "건")

    print()
    passed = 0
    for name, condition, detail in results:
        mark = "PASS" if condition else "FAIL"
        passed += condition
        print("[" + mark + "] " + name + (("  (" + detail + ")") if detail else ""))
    print()
    print(str(passed) + "/" + str(len(results)) + " 통과")
    sys.exit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    main()
