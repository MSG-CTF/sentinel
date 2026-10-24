"""참조 감시자.

Sentinel 본 구현의 자리를 검증하기 위한 최소 감시 루프다. 스택 결정과 무관하게
기대 동작을 실행 가능한 형태로 문서화한다.

- 백엔드: GET {backend}/healthz 가 200이고 모든 항목이 ok면 HEALTHY
- Registry: GET {registry}/v2/ 가 200 또는 401이면 HEALTHY (401은 인증 요구일 뿐 살아 있음)
- KOTH: GET {koth}/healthz 가 200이면 HEALTHY

상태가 DOWN으로 전이될 때 한 번만 receivers로 계약 payload를 보낸다.
복구되면 상태를 리셋해 다음 장애 때 다시 알린다.
"""

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone


def now_z():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def post_json(url, payload, timeout=5):
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status
    except urllib.error.HTTPError as error:
        return error.code
    except (urllib.error.URLError, TimeoutError):
        return None


def check_backend(base):
    try:
        with urllib.request.urlopen(base + "/healthz", timeout=5) as response:
            body = json.loads(response.read().decode("utf-8"))
            if response.status == 200 and all(value == "ok" for value in body.values()):
                return "HEALTHY", body
            return "DEGRADED", body
    except urllib.error.HTTPError as error:
        try:
            body = json.loads(error.read().decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            body = {}
        return "DOWN", body
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return "DOWN", {}


def check_registry(base):
    request = urllib.request.Request(base + "/v2/", method="GET")
    try:
        with urllib.request.urlopen(request, timeout=5):
            return "HEALTHY", {}
    except urllib.error.HTTPError as error:
        # 401은 인증을 요구할 뿐 레지스트리가 살아 있다는 뜻이다
        if error.code in (200, 401):
            return "HEALTHY", {"http": error.code}
        return "DOWN", {"http": error.code}
    except (urllib.error.URLError, TimeoutError):
        return "DOWN", {}


def check_koth(base):
    try:
        with urllib.request.urlopen(base + "/healthz", timeout=5) as response:
            return ("HEALTHY", {}) if response.status == 200 else ("DOWN", {})
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError):
        return "DOWN", {}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend", default="http://127.0.0.1:8010")
    parser.add_argument("--registry", default="https://ghcr.io")
    parser.add_argument("--koth", default="")
    parser.add_argument("--receiver", default="http://127.0.0.1:18020")
    parser.add_argument("--interval", type=int, default=5)
    parser.add_argument("--cycles", type=int, default=0, help="0이면 무한 반복")
    options = parser.parse_args()

    sys.stdout.reconfigure(encoding="utf-8")

    targets = [("backend", "BACKEND", lambda: check_backend(options.backend))]
    targets.append(("registry", "BROKER", lambda: check_registry(options.registry)))
    if options.koth:
        targets.append(("koth", "KOTH", lambda: check_koth(options.koth)))

    last_status = {name: "HEALTHY" for name, _, _ in targets}
    cycle = 0
    while True:
        cycle += 1
        line = []
        for name, target_type, probe in targets:
            status, detail = probe()
            line.append(name + "=" + status)

            if status != "HEALTHY" and last_status[name] == "HEALTHY":
                if name == "backend":
                    sent = post_json(
                        options.receiver + "/internal/monitor/alerts",
                        {
                            "alert_id": str(uuid.uuid4()),
                            "severity": "CRITICAL",
                            "title": "backend health check failed",
                            "message": "healthz " + status + " " + json.dumps(detail, ensure_ascii=False),
                            "source": "watcher-demo",
                            "created_at": now_z(),
                        },
                    )
                else:
                    sent = post_json(
                        options.receiver + "/internal/monitor/signals",
                        {
                            "signal_id": str(uuid.uuid4()),
                            "target_id": name + "-main",
                            "target_type": target_type,
                            "status": "DOWN",
                            "action": "INVESTIGATE",
                            "reason": name + " probe failed",
                            "checked_at": now_z(),
                        },
                    )
                line.append("(신호 발신 " + str(sent) + ")")
            last_status[name] = status

        print("[" + now_z() + "] " + "  ".join(line), flush=True)
        if options.cycles and cycle >= options.cycles:
            break
        time.sleep(options.interval)


if __name__ == "__main__":
    main()
