"""Reversible local service cutover drill with intact original data volumes.

The fixed application writers are stopped before backup. Fresh restored volumes
are temporarily attached to the original service names and checked through the
real applications. The drill returns to the original volumes before reopening
the edge and recovery executor. An interrupted drill leaves a rollback journal.
"""
import argparse
import copy
import json
from pathlib import Path
import subprocess
import sys
import time
import urllib.request

import data_recovery as r
import recovery_agent as agent

STATE = r.LOCAL / ".cutover"
ACTIVE = STATE / "active.json"
FRONTS = ("frontend-internal", "frontend-external", "edge")
WRITERS = r.APPS
APPLICATIONS = (*WRITERS, *FRONTS)


def compose(path, *arguments):
    return r.run(["compose", "--project-name", r.PROJECT, "-f", str(path), *arguments], timeout=180)


def scoped_config(config, expected_volumes):
    r.require(config.get("name") == r.PROJECT, "CUTOVER_PROJECT_MISMATCH")
    for service, spec in r.DATA.items():
        item = config["services"][service]
        r.require(not item.get("ports") and not item.get("privileged"), "CUTOVER_DATA_EXPOSED")
        destination = "/data" if spec["kind"] == "redis" else "/var/lib/postgresql/data"
        mounts = [m for m in item["volumes"] if m.get("target") == destination]
        r.require(len(mounts) == 1 and mounts[0].get("type") == "volume"
                  and mounts[0].get("source") == spec["volume"], "CUTOVER_BINDING_INVALID")
        r.require(config["volumes"][spec["volume"]]["name"] == expected_volumes[service], "CUTOVER_VOLUME_INVALID")


def assert_bindings(expected):
    records = r.project_records(r.PROJECT)
    for service, volume in expected.items():
        spec = r.DATA[service]
        path = "/data" if spec["kind"] == "redis" else "/var/lib/postgresql/data"
        mounts = [m for m in records[service]["Mounts"] if m["Destination"] == path]
        r.require(len(mounts) == 1 and mounts[0]["Type"] == "volume" and mounts[0]["Name"] == volume,
                  "CUTOVER_ACTIVE_BINDING_MISMATCH")


def checked_app(records, service):
    current = r.project_records(r.PROJECT)[service]
    r.require(current["Id"] == records[service]["Id"] and current["Image"] == records[service]["Image"], "CUTOVER_APPLICATION_CHANGED")
    return current


class ApplicationView:
    def __init__(self, records):
        self.records = records

    def compose(self, *args):
        r.require(args[:4] == ("up", "-d", "--no-deps", "--pull") and args[-1] in WRITERS, "CUTOVER_APPLICATION_COMMAND_INVALID")
        record = checked_app(self.records, args[-1])
        r.run(["start", record["Id"]])

    def record(self, service):
        return checked_app(self.records, service)


def stop_apps(records):
    for service in reversed(APPLICATIONS):
        checked_app(records, service)
    r.run(["stop", "--time", "20", *[records[name]["Id"] for name in reversed(APPLICATIONS)]], timeout=100)


def start_fronts(records):
    for service in FRONTS:
        record = checked_app(records, service)
        r.run(["start", record["Id"]])


def verify_data(volumes, manifest):
    assert_bindings(volumes)
    records = r.project_records(r.PROJECT)
    assets = {item["service"]: item for item in manifest["assets"]}
    for service, spec in r.DATA.items():
        record = records[service]
        if spec["kind"] == "postgres":
            r.wait_for(lambda: r.run(["exec", record["Id"], "pg_isready", "-U", spec["db"], "-d", spec["db"]]))
            with r.pg_session(record["Id"], spec["db"]) as query:
                r.require(r.pg_fingerprint(query) == assets[service]["tables"], "CUTOVER_ROWS_MISMATCH")
                r.require(r.pg_sequences(query) == assets[service]["sequences"], "CUTOVER_SEQUENCES_MISMATCH")
        else:
            password = r.redis_password(record)
            r.wait_for(lambda: r.redis(record["Id"], password, "PING") == "PONG")
            r.require(r.redis_info(record["Id"], password, "persistence")["aof_enabled"] == "1", "CUTOVER_AOF_DISABLED")


def load_active():
    state = json.loads(ACTIVE.read_text(encoding="utf-8"))
    r.require(state.get("schema_version") == 1 and r.ID_RE.fullmatch(state.get("run_id", "")), "CUTOVER_JOURNAL_INVALID")
    folder = STATE / state["run_id"]
    r.require(folder.resolve().parent == STATE.resolve() and not folder.is_symlink(), "CUTOVER_PATH_INVALID")
    original = folder / "original.json"
    r.require(r.digest(original) == state["original_sha256"], "CUTOVER_CONFIG_CHANGED")
    config = json.loads(original.read_text())
    expected = {name: r.PROJECT + "_" + spec["volume"] for name, spec in r.DATA.items()}
    scoped_config(config, expected)
    records = json.loads((folder / "applications.json").read_text())
    for name in APPLICATIONS:
        checked_app(records, name)
    return state, folder, records, expected


def rollback_locked():
    state, folder, records, expected = load_active()
    stop_apps(records)
    compose(folder / "original.json", "up", "-d", "--no-deps", "--pull", "never", *r.DATA)
    assert_bindings(expected)
    if state.get("backup_id"):
        _, manifest = r.validate_backup(state["backup_id"])
        verify_data(expected, manifest)
    applications = [r.application_check(ApplicationView(records), service) for service in WRITERS]
    start_fronts(records)
    state["phase"] = "rolled_back"
    r.save(folder / "journal.json", state)
    ACTIVE.unlink()
    return applications


def resume_executor():
    # Do not reconnect an armed executor while the monitor still holds the
    # intentional outage samples produced by this maintenance drill.
    deadline = time.monotonic() + 90
    previous = None
    healthy_samples = 0
    expected = {"internal-backend", "external-backend", "scheduler", "broker", *FRONTS}
    expected -= {"frontend-internal", "frontend-external"}
    expected |= {"internal-frontend", "external-frontend"}
    while time.monotonic() < deadline:
        with urllib.request.urlopen("http://127.0.0.1:58240/api/status", timeout=5) as response:
            snapshot = json.load(response)
        observed = {item["target_id"] for item in snapshot["targets"] if item["status"] == "HEALTHY"}
        if snapshot.get("updated_at") != previous:
            healthy_samples = healthy_samples + 1 if expected.issubset(observed) else 0
            previous = snapshot.get("updated_at")
        if healthy_samples >= 2:
            break
        time.sleep(2)
    r.require(healthy_samples >= 2, "CUTOVER_MONITOR_NOT_RECOVERED")
    agent.initialize()
    with (agent.STATE / "stdout.log").open("ab") as out, (agent.STATE / "stderr.log").open("ab") as err:
        subprocess.Popen([sys.executable, str(r.LOCAL / "recovery_agent.py"), "run"], cwd=r.LOCAL,
                         stdin=subprocess.DEVNULL, stdout=out, stderr=err,
                         creationflags=subprocess.CREATE_NO_WINDOW)


def drill():
    r.require(not ACTIVE.exists(), "CUTOVER_ALREADY_ACTIVE_USE_ROLLBACK")
    records = r.source_records()
    r.require(all(records[s]["State"]["Running"] for s in APPLICATIONS), "CUTOVER_APPLICATION_NOT_RUNNING")
    STATE.mkdir(exist_ok=True)
    run_id = r.new_id()
    folder = STATE / run_id
    folder.mkdir()
    raw = r.run(["compose", "--project-name", r.PROJECT, "-f", str(r.LOCAL / "compose.yaml"),
                 "-f", str(r.LOCAL / "compose.assets.yaml"), "config", "--format", "json"])
    original = json.loads(raw)
    for service in r.DATA:
        original["services"][service]["image"] = records[service]["Image"]
    original_volumes = {name: r.PROJECT + "_" + spec["volume"] for name, spec in r.DATA.items()}
    scoped_config(original, original_volumes)
    r.save(folder / "original.json", original)
    r.save(folder / "applications.json", {name: records[name] for name in APPLICATIONS})
    state = {"schema_version": 1, "run_id": run_id, "phase": "prepared", "original_sha256": r.digest(folder / "original.json")}
    report = {"schema_version": 1, "run_id": run_id, "started_at": r.now(), "scope": "local_service_cutover_round_trip",
              "passed": False, "automatic_data_restore_enabled": False, "assets": [], "applications": [], "rollback_applications": []}
    lab = None
    failure = None
    agent.stop()
    with agent.exclusive_lock():
        # Start-Monitor and any second executor must refuse to run while this exists.
        r.save(ACTIVE, state)
        try:
            stop_apps(records)
            state["phase"] = "writers_stopped"
            r.save(ACTIVE, state)
            backup_id = r.backup()
            state["backup_id"] = backup_id
            r.save(ACTIVE, state)
            backup_folder, manifest = r.validate_backup(backup_id)
            report["backup_id"] = backup_id
            lab = r.Lab(records, manifest)
            state["restore_run_id"] = lab.run_id
            r.save(ACTIVE, state)
            report["assets"] = r.restore_data(lab, backup_folder, verify_canary=False)
            lab.compose("stop", "--timeout", "10", *r.DATA)
            restored = {name: lab.project + "_" + name.replace("-", "_") for name in r.DATA}
            switched = copy.deepcopy(original)
            for service, spec in r.DATA.items():
                switched["volumes"][spec["volume"]] = {"name": restored[service], "external": True}
            scoped_config(switched, restored)
            r.save(folder / "restored.json", switched)
            state["phase"] = "switching"
            r.save(ACTIVE, state)
            compose(folder / "restored.json", "up", "-d", "--no-deps", "--pull", "never", *r.DATA)
            verify_data(restored, manifest)
            report["restored_volume_bindings_verified"] = True
            report["applications"] = [r.application_check(ApplicationView(records), service) for service in WRITERS]
            state["phase"] = "restored_services_verified"
            r.save(ACTIVE, state)
        except Exception as error:
            failure = error
            report["failure_code"] = str(error) if isinstance(error, r.RecoveryError) else type(error).__name__
        finally:
            try:
                report["rollback_applications"] = rollback_locked()
                report["original_volume_bindings_verified"] = True
            except Exception as error:
                failure = error
                report["rollback_failure"] = str(error) if isinstance(error, r.RecoveryError) else type(error).__name__
            if lab and not ACTIVE.exists():
                try:
                    lab.cleanup()
                    report["restore_lab_removed"] = True
                except Exception as error:
                    failure = error
                    report["cleanup_failure"] = type(error).__name__
    if not ACTIVE.exists():
        try:
            resume_executor()
            report["executor_resumed_after_healthy_samples"] = True
        except Exception as error:
            failure = error
            report["executor_resume_failure"] = str(error) if isinstance(error, r.RecoveryError) else type(error).__name__
    report.update(completed_at=r.now(), passed=failure is None and len(report["applications"]) == 4
                  and len(report["rollback_applications"]) == 4)
    r.save(r.LOCAL / "artifacts" / ("data-cutover-" + run_id + ".json"), report)
    public = {key: report[key] for key in ("schema_version", "run_id", "started_at", "completed_at", "scope", "passed",
        "automatic_data_restore_enabled", "assets", "applications", "rollback_applications")}
    for key in ("restored_volume_bindings_verified", "original_volume_bindings_verified", "restore_lab_removed"):
        public[key] = report.get(key, False)
    r.save(r.LOCAL.parent / "src/Sentinel.Api/wwwroot/service-cutover-status.json", public)
    r.require(report["passed"], "CUTOVER_DRILL_FAILED_SEE_REPORT")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("drill", "rollback"))
    args = parser.parse_args()
    try:
        if args.action == "drill":
            result = drill()
            print(json.dumps({"passed": result["passed"], "run_id": result["run_id"]}))
        else:
            agent.stop()
            with agent.exclusive_lock():
                rollback_locked()
            resume_executor()
            print(json.dumps({"rolled_back": True}))
    except Exception as error:
        print(json.dumps({"passed": False, "reason": str(error) if isinstance(error, r.RecoveryError) else type(error).__name__}))
        sys.exit(1)
