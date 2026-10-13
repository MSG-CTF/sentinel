"""Read-only local checks: real recent inventory, then a synthetic Scheduler row joined to real Runtime."""
import datetime as dt
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import tempfile
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import execution_lab as lab

ROOT = Path(__file__).resolve().parent
REPORT = ROOT / "artifacts" / "instance-inventory-verification.json"


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def get(url, token=None):
    request = urllib.request.Request(url, headers={} if token is None else {"Authorization": "Bearer " + token})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), lab.NoRedirect())
    with opener.open(request, timeout=6) as response:
        return json.load(response)


def wait(check):
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        try:
            result = check()
            if result:
                return result
        except (OSError, ValueError, KeyError):
            pass
        time.sleep(0.5)
    raise RuntimeError("INSTANCE_VERIFICATION_TIMEOUT")


def main():
    report = {"started_at": now(), "passed": False, "checks": [],
              "scope": "live_empty_recent_inventory_and_synthetic_row_joined_to_real_runtime",
              "scheduler_creation_flow_verified": False, "competition_challenge_verified": False,
              "instance_mutations_sent": 0}

    def check(name, condition):
        report["checks"].append({"name": name, "passed": bool(condition)})
        if not condition:
            raise RuntimeError("INSTANCE_CHECK_FAILED_" + name.upper())
        print(json.dumps({"check": name, "passed": True}), flush=True)

    credentials = dict(line.split("=", 1) for line in (ROOT / ".assets/services.env").read_text().splitlines() if "=" in line)
    process = None
    server = None
    try:
        live = wait(lambda: (lambda value: value if value["status"] == "PARTIAL" else None)(get("http://127.0.0.1:58240/api/instances")))
        scheduler = get("http://127.0.0.1:58182/api/instances/recent", credentials["SCHEDULER_API_TOKEN"])
        check("live_recent_window_is_partial", live["complete_inventory"] is False and live["coverage"] == "recent_20")
        check("live_recent_window_matches_real_scheduler", scheduler["code"] == "SUCCESS"
              and {row["instance_id"] for row in live["instances"]} == {row["instance_id"] for row in scheduler["data"]})
        report["live_recent_count"] = len(live["instances"])
        status = lab.runtime("instances/" + lab.INSTANCE + "/runtime-status")
        check("fixed_runtime_fixture_ready", status["phase"] == "READY" and status["instance_id"] == lab.INSTANCE)
        fixture_token = secrets.token_urlsafe(32)
        expiry = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1)).isoformat()
        row = {"instance_id": lab.INSTANCE, "status": "RUNNING", "runtime_type": "KUBERNETES",
               "runtime_target_id": lab.TARGET, "runtime_workload_id": status["runtime_workload_id"],
               "expires_at": expiry, "hard_expires_at": expiry, "idle_expires_at": None}
        state = {"row": row, "code": 200}

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                if self.path != "/api/instances/recent" or self.headers.get("Authorization") != "Bearer " + fixture_token:
                    self.send_response(401)
                    self.end_headers()
                    return
                payload = json.dumps({"code": "SUCCESS", "data": [dict(state["row"])]}).encode()
                self.send_response(state["code"])
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            api_port = reservation.getsockname()[1]
        with tempfile.TemporaryDirectory(prefix="inventory-check-", dir=ROOT / ".execution") as directory:
            folder = Path(directory)
            profile = {"platform_monitor": {"profile": "instance-verification", "event_file": str(folder / "events.jsonl"),
                        "targets": [{"target_id": "unused", "name": "Unused", "role": "TEST", "track": "TEST", "enabled": False}]},
                       "platform_recovery": {"enabled": False},
                       "instance_monitor": {"enabled": True, "poll_interval_seconds": 5,
                        "scheduler_url": f"http://127.0.0.1:{server.server_port}/api/instances/recent",
                        "scheduler_token_env": "INSTANCE_FIXTURE_TOKEN", "runtime_targets": [
                            {"target_id": lab.TARGET, "base_url": "http://127.0.0.1:58188", "auth_token_env": "INSTANCE_FIXTURE_RUNTIME_TOKEN"}]}}
            profile_path = folder / "profile.json"
            profile_path.write_text(json.dumps(profile), encoding="utf-8")
            env = dict(os.environ, SENTINEL_PROFILE_FILE=str(profile_path), ASPNETCORE_URLS=f"http://127.0.0.1:{api_port}",
                       INSTANCE_FIXTURE_TOKEN=fixture_token, INSTANCE_FIXTURE_RUNTIME_TOKEN=credentials["SCHEDULER_RUNTIME_TOKEN"],
                       DOTNET_ROOT=str(ROOT / ".tooling/dotnet"))
            with (folder / "host.log").open("w", encoding="utf-8") as log:
                process = subprocess.Popen([str(ROOT / ".tooling/dotnet/dotnet.exe"),
                    str(ROOT.parent / "src/Sentinel.Api/bin/Debug/net8.0/Sentinel.Api.dll")],
                    env=env, cwd=folder, stdout=log, stderr=log)
                endpoint = f"http://127.0.0.1:{api_port}/api/instances"

                def observe(reason):
                    return wait(lambda: (lambda value: value if len(value["instances"]) == 1
                        and value["instances"][0]["reason_code"] == reason else None)(get(endpoint)))

                try:
                    joined = observe("RUNTIME_READY_HEALTHCHECK_UNVERIFIED")
                    check("synthetic_row_joins_real_runtime_readiness", joined["instances"][0]["status"] == "READY")
                    check("joined_row_keeps_health_and_track_unverified", joined["instances"][0]["challenge_health"] == "UNVERIFIED"
                          and joined["instances"][0]["track"] == "UNMAPPED" and joined["complete_inventory"] is False)
                    state["row"] = dict(row, status="CLEANED")
                    check("synthetic_terminal_row_detects_real_resource", observe("TERMINAL_RESOURCE_PRESENT")["instances"][0]["status"] == "DOWN")
                    state["row"] = dict(row, expires_at="2026-01-01T00:00:00Z")
                    check("synthetic_expiry_detects_real_resource", observe("TTL_EXPIRED_RESOURCE_PRESENT")["instances"][0]["ttl_expired"])
                    state["row"] = dict(row, runtime_workload_id="different/namespace")
                    check("synthetic_binding_mismatch_stays_unverified", observe("RUNTIME_IDENTITY_MISMATCH")["instances"][0]["status"] == "UNVERIFIED")
                    state["code"] = 503
                    failed = wait(lambda: (lambda value: value if value["status"] == "UNAVAILABLE" else None)(get(endpoint)))
                    check("scheduler_failure_clears_joined_rows", failed["instances"] == [])
                finally:
                    process.terminate()
                    process.wait(timeout=10)
                    process = None
        after = lab.runtime("instances/" + lab.INSTANCE + "/runtime-status")
        check("real_fixture_binding_preserved", after["runtime_workload_id"] == status["runtime_workload_id"] and after["phase"] == "READY")
        report["passed"] = True
    finally:
        if process and process.poll() is None:
            process.terminate()
            process.wait(timeout=10)
        if server:
            server.shutdown()
            server.server_close()
        report["completed_at"] = now()
        REPORT.parent.mkdir(exist_ok=True)
        REPORT.write_text(json.dumps(report, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
