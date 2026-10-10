"""Read actual Django/Sentinel responses; optional faults affect only this local Compose project."""
import argparse
import json
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

PROJECT = "msg-sla-real-local"
ROOT = Path(__file__).resolve().parent
parser = argparse.ArgumentParser()
parser.add_argument("--faults", action="store_true")
parser.add_argument("--docker-host", default="npipe:////./pipe/dockerDesktopLinuxEngine")
parser.add_argument("--monitor-url", default="http://127.0.0.1:58240")
args = parser.parse_args()
if args.faults and args.docker_host != "npipe:////./pipe/dockerDesktopLinuxEngine":
    raise SystemExit("Fault verification requires the local Docker Desktop Linux engine")
parts = urllib.parse.urlsplit(args.monitor_url)
if parts.scheme != "http" or parts.hostname not in {"127.0.0.1", "localhost"} or parts.username or parts.query or parts.fragment:
    raise SystemExit("The verifier only accepts a localhost monitor")
records = []
started = datetime.now(timezone.utc).isoformat()


def record(name, detail=None):
    records.append({"check": name, "passed": True, "detail": detail})
    print("PASS " + name, flush=True)


def get_json(path):
    with urllib.request.urlopen(args.monitor_url.rstrip("/") + path, timeout=8) as response:
        assert response.headers.get_content_type() == "application/json", "Monitor must return JSON"
        return json.load(response)


def targets():
    snapshot = get_json("/api/status")
    assert snapshot["mode"] == "observe_only"
    assert snapshot["profile"] == "local", "Switch to the local profile before verification"
    values = {row["target_id"]: row for row in snapshot["targets"]}
    for name, host in [("internal-backend", "backend-internal"), ("external-backend", "backend-external")]:
        uri = urllib.parse.urlsplit(values[name]["url"])
        assert uri.hostname == host and uri.port == 8080 and uri.path == "/healthz", "Actual local backend required"
    return values


def wait_for(predicate, label, seconds=55):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            record(label)
            return
        time.sleep(1)
    raise AssertionError("Timed out: " + label)


def compose(*command):
    result = subprocess.run(
        ["docker", "--host", args.docker_host, "compose", "--project-name", PROJECT, "-f", str(ROOT / "compose.yaml"), *command],
        capture_output=True, text=True, encoding="utf-8", timeout=45,
    )
    if result.returncode != 0:
        raise AssertionError("Local Compose command failed: " + " ".join(command[:2]))
    return result.stdout


def last_json(output):
    for line in reversed(output.splitlines()):
        if line.strip().startswith("{"):
            return json.loads(line)
    raise AssertionError("No JSON evidence returned by real backend")


def event_seen(target_id, status, since):
    return any(
        event["target_id"] == target_id and event["status"] == status
        and datetime.fromisoformat(event["created_at"].replace("Z", "+00:00")) >= since
        for event in get_json("/api/events")["events"]
    )


def actual_health_response(config, track):
    ports = config["services"]["backend-" + track]["ports"]
    port = next(row for row in ports if row["target"] == 8080)
    assert port["host_ip"] == "127.0.0.1", "Fault probes must stay on localhost"
    url = "http://127.0.0.1:" + str(port["published"]) + "/healthz"
    before = time.monotonic()
    try:
        response = urllib.request.urlopen(url, timeout=30)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        assert response.headers.get_content_type() == "application/json"
        return {"http_status_code": response.status, "body": json.load(response),
                "elapsed_seconds": round(time.monotonic() - before, 2)}


try:
    config = json.loads(compose("config", "--format", "json"))
    assert config["name"] == PROJECT
    for track in ["internal", "external"]:
        environment = config["services"]["backend-" + track]["environment"]
        assert environment["POSTGRES_HOST"] == "postgres-" + track
        assert urllib.parse.urlsplit(environment["REDIS_URL"]).hostname == "redis-" + track
    record("local_project_and_data_hosts_are_scoped")
    wait_for(lambda: all(targets()[name]["status"] == "HEALTHY" for name in ["internal-backend", "external-backend"]),
             "actual_internal_and_external_backends_ready")
    current = targets()
    assert current["internal-backend"]["components"] == {"status": "ok", "database": "ok", "cache": "ok"}
    assert current["external-backend"]["components"] == {"status": "ok", "database": "ok", "cache": "ok"}
    assert all(row["checked_at"] is None and row["status"] == "UNCONFIGURED" for row in current.values() if not row["enabled"])
    record("unconnected_roles_are_not_falsely_healthy")
    assert get_json("/healthz")["mode"] == "observe_only"
    for route in ["/api/status", "/api/events"]:
        request = urllib.request.Request(args.monitor_url.rstrip("/") + route, data=b"{}", method="POST",
                                         headers={"Content-Type": "application/json"})
        try:
            urllib.request.urlopen(request, timeout=8).close()
            raise AssertionError("Read-only API unexpectedly accepted POST")
        except urllib.error.HTTPError as error:
            assert error.code == 405
    record("monitor_status_apis_reject_post")

    for track in ["internal", "external"]:
        evidence = last_json(compose("exec", "-T", "backend-" + track, "python", "manage.py", "shell", "-c",
            "import json; from django.db import connection; cursor=connection.cursor(); "
            "cursor.execute('SELECT current_database()'); print(json.dumps({'database':cursor.fetchone()[0]}))"))
        assert evidence["database"] == "msg_" + track
        record("actual_database_" + track, evidence)
    marker = "sla-isolation-" + str(time.time_ns())
    compose("exec", "-T", "backend-internal", "python", "manage.py", "shell", "-c",
        "from django.core.cache import cache; cache.set('" + marker + "','internal-only',30)")
    other = last_json(compose("exec", "-T", "backend-external", "python", "manage.py", "shell", "-c",
        "import json; from django.core.cache import cache; print(json.dumps({'shared':cache.get('" + marker + "') is not None}))"))
    assert other["shared"] is False
    record("redis_data_is_not_shared_between_tracks")

    if args.faults:
        for service, target_id, other_id, reason in [
            ("postgres-internal", "internal-backend", "external-backend", "DATABASE_DOWN"),
            ("redis-external", "external-backend", "internal-backend", "CACHE_DOWN"),
        ]:
            since = datetime.now(timezone.utc)
            print("FAULT local service: " + service, flush=True)
            try:
                compose("stop", "-t", "5", service)
                track = "internal" if target_id == "internal-backend" else "external"
                response = actual_health_response(config, track)
                assert response["http_status_code"] == 503
                assert response["body"] == {"status": "down",
                    "database": "down" if service.startswith("postgres") else "ok",
                    "cache": "down" if service.startswith("redis") else "ok"}
                record(service + "_actual_backend_reports_dependency_failure", response)
                def isolated_failure():
                    snapshot = targets()
                    # A dependency can return 503 after the bounded monitor deadline.
                    # TIMEOUT proves the missed deadline, not which dependency failed.
                    return snapshot[target_id]["status"] == "DOWN" and snapshot[target_id]["reason_code"] in {reason, "TIMEOUT"} and snapshot[other_id]["status"] == "HEALTHY"
                wait_for(isolated_failure, service + "_failure_isolated_to_its_track")
                observation = targets()[target_id]
                record(service + "_monitor_classification", {"reason_code": observation["reason_code"],
                    "dependency_identified_by_monitor": observation["reason_code"] == reason,
                    "injected_fault": service, "monitor_latency_ms": observation["latency_ms"]})
                wait_for(lambda: event_seen(target_id, "DOWN", since), service + "_incident_recorded")
                events = [event for event in get_json("/api/events")["events"] if event["target_id"] == target_id and event["status"] == "DOWN"
                          and datetime.fromisoformat(event["created_at"].replace("Z", "+00:00")) >= since]
                assert len(events) == 1
                record(service + "_incident_not_duplicated")
            finally:
                compose("start", service)
                wait_for(lambda: targets()[target_id]["status"] == "HEALTHY", service + "_actual_service_recovered")
                wait_for(lambda: event_seen(target_id, "HEALTHY", since), service + "_recovery_recorded")
    wait_for(lambda: all(targets()[name]["status"] == "HEALTHY" for name in ["internal-backend", "external-backend"]),
             "both_tracks_healthy_at_finish")
    record("verification_finished_without_production_writes")
except BaseException as error:
    records.append({"check": "verification", "passed": False, "error_type": type(error).__name__, "detail": str(error)})
    raise
finally:
    artifacts = ROOT / "artifacts"
    artifacts.mkdir(exist_ok=True)
    report = {"started_at": started, "finished_at": datetime.now(timezone.utc).isoformat(), "faults": args.faults,
              "project": PROJECT, "checks": records, "passed": bool(records) and all(row["passed"] for row in records)}
    stamp = datetime.fromisoformat(started).strftime("%Y%m%dT%H%M%S%fZ")
    prefix = "actual-fault-verification" if args.faults else "actual-verification"
    destination = artifacts / (prefix + "-" + stamp + ".json")
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Evidence: " + str(destination), flush=True)
