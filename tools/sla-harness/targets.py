"""가짜 감시 대상 3종.

Sentinel이 감시해야 하는 실물이 아직 없으므로, 확정 계약 형태로 흉내 낸다.
- 백엔드 /healthz (18010): 신설 예정 계약의 선취 구현
- KOTH /healthz (18090): 출제 규정의 9090 분리 healthcheck 형태
- 스케줄러 인스턴스 이벤트 (18001): 활성 인스턴스 상태 조회

장애 주입: POST /toggle body {"target": "backend|koth|instance", "state": "..."}
컨트롤 포트는 18011이다.
"""

import json
import threading
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

STATE = {
    "backend": {"status": "ok", "database": "ok", "cache": "ok"},
    "koth": {"status": "ok"},
    "instance": {"status": "RUNNING"},
}
STATE_LOCK = threading.Lock()

INSTANCE_ID = str(uuid.uuid4())
TEAM_ID = str(uuid.uuid4())


def now_z():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def json_bytes(payload):
    return json.dumps(payload).encode("utf-8")


class BaseHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send_json(self, status, payload):
        raw = json_bytes(payload)
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


class BackendHandler(BaseHandler):
    def do_GET(self):
        if self.path == "/healthz":
            with STATE_LOCK:
                snapshot = dict(STATE["backend"])
            overall = 200 if all(value == "ok" for value in snapshot.values()) else 503
            self.send_json(overall, snapshot)
        else:
            self.send_json(404, {"error": "not_found"})


class KothHandler(BaseHandler):
    def do_GET(self):
        if self.path == "/healthz":
            with STATE_LOCK:
                snapshot = dict(STATE["koth"])
            overall = 200 if snapshot["status"] == "ok" else 503
            self.send_json(overall, snapshot)
        else:
            self.send_json(404, {"error": "not_found"})


class SchedulerHandler(BaseHandler):
    def do_GET(self):
        if self.path.startswith("/api/instances/active"):
            with STATE_LOCK:
                status = STATE["instance"]["status"]
            self.send_json(
                200,
                {
                    "code": "SUCCESS",
                    "message": "ok",
                    "data": {
                        "instances": [
                            {
                                "instance_id": INSTANCE_ID,
                                "team_id": TEAM_ID,
                                "status": status,
                                "updated_at": now_z(),
                            }
                        ]
                    },
                },
            )
        else:
            self.send_json(404, {"error": "not_found"})


class ControlHandler(BaseHandler):
    def do_POST(self):
        if self.path != "/toggle":
            self.send_json(404, {"error": "not_found"})
            return
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        target = body.get("target")
        with STATE_LOCK:
            if target == "backend":
                STATE["backend"][body.get("component", "database")] = body.get("state", "down")
            elif target == "koth":
                STATE["koth"]["status"] = body.get("state", "down")
            elif target == "instance":
                STATE["instance"]["status"] = body.get("state", "FAILED")
            else:
                self.send_json(400, {"error": "unknown_target"})
                return
            snapshot = {key: dict(value) for key, value in STATE.items()}
        self.send_json(200, snapshot)

    def do_GET(self):
        with STATE_LOCK:
            snapshot = {key: dict(value) for key, value in STATE.items()}
        self.send_json(200, snapshot)


def serve(port, handler):
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def main():
    serve(18010, BackendHandler)
    serve(18090, KothHandler)
    serve(18001, SchedulerHandler)
    control = ThreadingHTTPServer(("127.0.0.1", 18011), ControlHandler)
    print("가짜 감시 대상 기동")
    print("  backend  http://127.0.0.1:18010/healthz")
    print("  koth     http://127.0.0.1:18090/healthz")
    print("  events   http://127.0.0.1:18001/api/instances/active")
    print("  control  http://127.0.0.1:18011/toggle")
    control.serve_forever()


if __name__ == "__main__":
    main()
