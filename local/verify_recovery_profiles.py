"""Exercise the real start script and durable recovery history without stopping data services."""
import datetime as dt
import json
import subprocess
import time
import urllib.request

import recovery_agent as agent


def read_status():
    with urllib.request.urlopen(agent.API + "/api/status", timeout=8) as response:
        return json.load(response)


def containers():
    ids = agent.docker("ps", "-aq", "--filter", "label=com.docker.compose.project=" + agent.PROJECT).split()
    records = json.loads(agent.docker("inspect", *ids))
    # Alembic is a completed job that Compose intentionally reruns on startup.
    # It is not a running service whose start time must remain unchanged.
    for item in records:
        if item["Config"]["Labels"]["com.docker.compose.service"] == "broker-migrate":
            assert item["State"]["Status"] == "exited" and item["State"]["ExitCode"] == 0
    return {item["Config"]["Labels"]["com.docker.compose.service"]: [item["Id"], item["State"]["StartedAt"]]
            for item in records
            if item["Config"]["Labels"]["com.docker.compose.service"] not in ("sentinel", "broker-migrate")}


def switch(profile):
    # A background Windows child can inherit a capture pipe and keep communicate()
    # waiting after PowerShell exits. A file keeps this verification bounded.
    with (agent.STATE / "profile-verification.log").open("a", encoding="utf-8") as output:
        subprocess.run(["powershell", "-NoProfile", "-File", str(agent.ROOT / "Start-Monitor.ps1"),
                        "-Profile", profile, "-SkipBuild"], check=True,
                       stdout=output, stderr=subprocess.STDOUT, timeout=90)


def wait_ready(profile):
    until = time.monotonic() + 60
    while time.monotonic() < until:
        try:
            status = read_status()
            if status["profile"] == profile and all(item["status"] == "HEALTHY" for item in status["targets"] if item["enabled"]):
                if profile != "local-recovery" or status["recovery"]["executor_connected"]:
                    return status
        except (OSError, ValueError):
            pass
        time.sleep(2)
    raise AssertionError("Profile did not become ready")


def main():
    baseline = wait_ready("local-recovery")
    previous = containers()
    history = baseline["recovery"]["requests"]
    assert history and all(item["status"] == "SUCCEEDED" for item in history), "Finish recovery verification first"
    checks = []
    try:
        switch("local")
        observation = wait_ready("local")
        assert observation["mode"] == "observe_only" and not observation["recovery"]["enabled"]
        with agent.exclusive_lock():
            pass
        checks.append("observe_only_switch_stops_executor")
        assert containers() == previous
        checks.append("observe_only_switch_preserves_all_running_services")
    finally:
        switch("local-recovery")
    restored = wait_ready("local-recovery")
    assert restored["recovery"]["requests"] == history
    checks.append("recovery_history_survives_monitor_and_executor_restart")
    assert containers() == previous
    checks.append("restart_does_not_replay_successful_recovery")
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = agent.ROOT / "artifacts" / ("recovery-profile-verification-" + stamp + ".json")
    path.write_text(json.dumps({"passed": True, "checks": checks, "final_profile": restored["profile"]}, indent=2), encoding="utf-8")
    print(json.dumps({"passed": True, "checks": checks, "report": str(path)}), flush=True)


if __name__ == "__main__":
    main()
