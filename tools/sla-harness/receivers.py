"""가짜 수신자 2종.

Sentinel이 보내는 신호와 알림을 받아 계약을 즉시 검증하고 기록한다.
- POST /internal/monitor/signals  스케줄러 신호 수신
- POST /internal/monitor/alerts   관리자 알림 수신
- GET  /received                  수신과 위반 기록 조회
포트는 18020이다.
"""

import json
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from contract import validate_payload

DATA_DIR = Path(__file__).resolve().parent / ".data"
DATA_DIR.mkdir(exist_ok=True)
LOG_PATH = DATA_DIR / "received.jsonl"
LOG_LOCK = threading.Lock()


def record(kind, payload, violations):
    entry = {
        "received_at": datetime.now(timezone.utc).isoformat(),
        "kind": kind,
        "payload": payload,
        "violations": violations,
    }
    with LOG_LOCK:
        with open(LOG_PATH, "a", encoding="utf-8") as log:
            log.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def read_all():
    rows = []
    try:
        with open(LOG_PATH, encoding="utf-8") as log:
            for line in log:
                rows.append(json.loads(line))
    except FileNotFoundError:
        pass
    return rows


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send_json(self, status, payload):
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self):
        kinds = {
            "/internal/monitor/signals": "health_signal",
            "/internal/monitor/alerts": "admin_alert",
        }
        kind = kinds.get(self.path)
        if kind is None:
            self.send_json(404, {"error": "not_found"})
            return

        length = int(self.headers.get("Content-Length", 0))
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            record(kind, None, [kind + ": JSON 파싱 실패"])
            self.send_json(400, {"error": "invalid_json"})
            return

        violations = validate_payload(kind, payload)
        record(kind, payload, violations)
        if violations:
            self.send_json(400, {"error": "contract_violation", "violations": violations})
        else:
            self.send_json(200, {"result": "accepted"})

    def do_GET(self):
        if self.path == "/received":
            self.send_json(200, {"entries": read_all()})
        else:
            self.send_json(404, {"error": "not_found"})


def main():
    LOG_PATH.unlink(missing_ok=True)
    print("가짜 수신자 기동")
    print("  signals  http://127.0.0.1:18020/internal/monitor/signals")
    print("  alerts   http://127.0.0.1:18020/internal/monitor/alerts")
    print("  received http://127.0.0.1:18020/received")
    ThreadingHTTPServer(("127.0.0.1", 18020), Handler).serve_forever()


if __name__ == "__main__":
    main()
