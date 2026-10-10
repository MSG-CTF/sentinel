"""Verify profile switching without restarting this project's backend or data containers."""
import json
import subprocess
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROJECT = "msg-sla-real-local"
HOST = "npipe:////./pipe/dockerDesktopLinuxEngine"
SERVICES = ["backend-internal", "backend-external", "postgres-internal", "postgres-external",
            "redis-internal", "redis-external"]
started = datetime.now(timezone.utc)
checks = []


def run(command):
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", timeout=90)
    if result.returncode:
        raise AssertionError("Scoped local command failed")
    return result.stdout


def record(name, detail=None):
    checks.append({"check": name, "passed": True, "detail": detail})
    print("PASS " + name, flush=True)


def containers():
    ids = run(["docker", "--host", HOST, "compose", "--project-name", PROJECT,
               "-f", str(ROOT / "compose.yaml"), "ps", "--all", "--quiet", *SERVICES]).split()
    assert len(ids) == len(SERVICES), "Start the complete local stack first"
    template = ('{"id":{{json .Id}},"started_at":{{json .State.StartedAt}},'
                '"running":{{json .State.Running}},'
                '"project":{{json (index .Config.Labels "com.docker.compose.project")}},'
                '"service":{{json (index .Config.Labels "com.docker.compose.service")}}}')
    rows = [json.loads(line) for line in run(["docker", "--host", HOST, "inspect", "--format", template, *ids]).splitlines()]
    assert all(row["project"] == PROJECT and row["running"] for row in rows)
    assert {row["service"] for row in rows} == set(SERVICES)
    return {row["service"]: row for row in rows}


def switch(profile):
    run(["powershell", "-NoProfile", "-File", str(ROOT / "Start-Monitor.ps1"),
         "-Profile", profile, "-DockerHost", HOST, "-SkipBuild"])


def wait_profile(profile):
    deadline = time.monotonic() + 75
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen("http://127.0.0.1:58240/api/status", timeout=8) as response:
                snapshot = json.load(response)
            if snapshot["profile"] == profile and snapshot["updated_at"]:
                assert snapshot["mode"] == "observe_only"
                rows = {row["target_id"]: row for row in snapshot["targets"]}
                if profile == "local" and any(rows[key]["status"] != "HEALTHY" for key in ["internal-backend", "external-backend"]):
                    time.sleep(1)
                    continue
                return rows
        except (OSError, ValueError):
            pass
        time.sleep(1)
    raise AssertionError("Profile did not become ready: " + profile)


try:
    wait_profile("local")
    before = containers()
    record("six_local_services_running_before_switch")
    try:
        switch("deployed")
        deployed = wait_profile("deployed")
        assert containers() == before, "Profile switch restarted a backend or data container"
        record("deployed_switch_preserves_container_ids_and_start_times")
        assert all(deployed[key]["track"] == "PUBLIC" for key in ["public-site", "public-backend"])
        assert all(deployed[key]["status"] == "UNCONFIGURED" for key in ["internal-backend", "external-backend"])
        row = deployed["public-backend"]
        if row["reason_code"] == "EXPECTED_JSON_RECEIVED_OTHER":
            assert row["status"] == "UNVERIFIED" and not row["components"]
        record("deployed_observation_keeps_track_unassigned", {
            "status": row["status"], "reason_code": row["reason_code"],
            "http_status_code": row["http_status_code"], "checked_at": row["checked_at"]})
    finally:
        switch("local")
        wait_profile("local")
        assert containers() == before, "Returning to local restarted a backend or data container"
        record("local_profile_restored_with_both_tracks_healthy_and_containers_unchanged")
except BaseException as error:
    checks.append({"check": "verification", "passed": False, "error_type": type(error).__name__})
    raise
finally:
    report = {"started_at": started.isoformat(), "finished_at": datetime.now(timezone.utc).isoformat(),
              "project": PROJECT, "checks": checks, "passed": bool(checks) and all(row["passed"] for row in checks)}
    artifacts = ROOT / "artifacts"
    artifacts.mkdir(exist_ok=True)
    destination = artifacts / ("profile-verification-" + started.strftime("%Y%m%dT%H%M%S%fZ") + ".json")
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Evidence: " + str(destination), flush=True)
