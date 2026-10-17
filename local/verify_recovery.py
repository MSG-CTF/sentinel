"""Real local recovery regression; faults are restricted to this Compose project and restored."""
import datetime as dt
import json
from pathlib import Path
import time
import urllib.error
import urllib.request
import uuid

import recovery_agent as agent

ROOT = Path(__file__).resolve().parent
checks = []
started = dt.datetime.now(dt.timezone.utc)


def get(path):
    with urllib.request.urlopen(agent.API + path, timeout=8) as response:
        return json.load(response)


def check(name, condition, detail=None):
    if not condition:
        raise AssertionError(name)
    checks.append({"check": name, "passed": True, "detail": detail})
    print("PASS " + name, flush=True)


def wait(predicate, label, seconds=120):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            check(label, True)
            return
        time.sleep(2)
    raise AssertionError(label + " timed out")


def inventory():
    ids = agent.docker("ps", "-aq", "--filter", "label=com.docker.compose.project=" + agent.PROJECT).split()
    data = json.loads(agent.docker("inspect", *ids))
    return {item["Config"]["Labels"]["com.docker.compose.service"]:
            {"container_id": item["Id"], "started_at": item["State"]["StartedAt"], "status": item["State"]["Status"]}
            for item in data}


def response_code(path, data=None, auth=False):
    try:
        if auth:
            agent.api(path, data)
            return 200
        with urllib.request.urlopen(agent.API + "/internal/recovery" + path, timeout=5) as response:
            return response.status
    except urllib.error.HTTPError as error:
        return error.code


def main():
    stopped = set()
    originals = {}
    succeeded = False
    try:
        status = get("/api/status")
        check("explicit_local_recovery_profile", status["profile"] == "local-recovery" and status["mode"] == "automatic_local")
        check("executor_api_requires_authentication", response_code("/requests/next") == 401)
        fake_id = str(uuid.uuid4())
        check("camel_case_body_rejected", response_code("/requests/" + fake_id + "/claim", {"executorId": agent.EXECUTOR}, True) == 400)
        check("unknown_request_cannot_be_claimed", response_code("/requests/" + fake_id + "/claim", {"executor_id": agent.EXECUTOR}, True) == 409)
        wait(lambda: get("/api/recovery")["executor_connected"] and get("/api/recovery")["armed"], "fresh_healthy_baseline_and_executor_connected", 380)
        # Test this while armed, before any recovery cooldown can mask a dispatch bug.
        originals = inventory()
        count = len(get("/api/recovery")["requests"])
        backend_started = originals[agent.SERVICE]["started_at"]
        stopped.add("postgres-internal")
        agent.docker("stop", "--time", "10", originals["postgres-internal"]["container_id"])
        wait(lambda: any(item["target_id"] == "internal-backend" and item["reason_code"] == "DATABASE_DOWN"
                         for item in get("/api/status")["targets"]), "real_database_dependency_failure_observed", 50)
        first = time.monotonic()
        wait(lambda: time.monotonic() - first >= 48, "dependency_failure_observed_across_failure_threshold", 60)
        check("dependency_failure_does_not_dispatch_recovery", len(get("/api/recovery")["requests"]) == count)
        check("dependency_failure_does_not_restart_backend", inventory()[agent.SERVICE]["started_at"] == backend_started)
        agent.docker("start", originals["postgres-internal"]["container_id"])
        stopped.remove("postgres-internal")
        wait(lambda: all(item["status"] == "HEALTHY" for item in get("/api/status")["targets"]
                         if item["target_id"] in {"internal-backend", "external-backend"})
                         and get("/api/recovery")["armed"], "dependency_restored_with_recovery_still_armed", 60)
        before = inventory()
        originals = before
        previous_ids = {item["request_id"] for item in get("/api/recovery")["requests"]}
        internal_id = before[agent.SERVICE]["container_id"]
        stopped.add(agent.SERVICE)
        agent.docker("stop", "--time", "10", internal_id)
        check("real_internal_backend_stopped", inventory()[agent.SERVICE]["status"] == "exited")

        def completed():
            requests = [item for item in get("/api/recovery")["requests"] if item["request_id"] not in previous_ids]
            return len(requests) == 1 and requests[0]["status"] == "SUCCEEDED"
        wait(completed, "sentinel_executor_and_functional_verification_completed", 145)
        stopped.remove(agent.SERVICE)
        requests = [item for item in get("/api/recovery")["requests"] if item["request_id"] not in previous_ids]
        request = requests[0]
        check("one_scoped_request_with_verified_result", request["asset_id"] == "internal-backend"
              and request["track"] == "INTERNAL" and request["execution_result_code"] == "CONTAINER_STARTED"
              and request["reason_code"] == "FUNCTION_VERIFIED", request)
        after = inventory()
        check("same_container_started_without_recreate", after[agent.SERVICE]["container_id"] == internal_id
              and after[agent.SERVICE]["started_at"] != before[agent.SERVICE]["started_at"])
        untouched = [name for name in before if name != agent.SERVICE]
        check("other_containers_and_tracks_untouched", all(after[name] == before[name] for name in untouched))
        check("duplicate_claim_cannot_execute_again", response_code("/requests/" + request["request_id"] + "/claim", {"executor_id": agent.EXECUTOR}, True) == 409)
        serialized = json.dumps(get("/api/recovery"))
        token = (agent.STATE / "secrets" / "executor.token").read_text().strip()
        check("public_recovery_status_omits_claim_and_credentials", "claim_id" not in serialized and token not in serialized)

        wait(lambda: all(item["status"] == "HEALTHY" for item in get("/api/status")["targets"]
                         if item["target_id"] in {"internal-backend", "external-backend"}), "both_real_backends_restored", 60)
        succeeded = True
    finally:
        for name in sorted(stopped):
            if name in originals:
                agent.docker("start", originals[name]["container_id"])
        output = ROOT / "artifacts" / ("recovery-verification-" + started.strftime("%Y%m%dT%H%M%SZ") + ".json")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps({"started_at": started.isoformat(), "completed_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                                      "passed": succeeded, "checks": checks}, ensure_ascii=False, indent=2), encoding="utf-8")
        print("REPORT " + str(output), flush=True)


if __name__ == "__main__":
    main()
