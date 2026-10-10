"""Fault checks for the fixed local Runtime, store and K3s smoke workload."""
import json
import time
import urllib.error
import urllib.request

import data_recovery as r
import execution_lab as lab


def snapshot():
    with urllib.request.urlopen("http://127.0.0.1:58240/api/status", timeout=5) as response:
        return json.load(response)


def wait(check, timeout=150):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            result = check()
            if result:
                return result
        except (OSError, ValueError, KeyError, r.RecoveryError):
            pass
        time.sleep(2)
    raise r.RecoveryError("EXECUTION_VERIFICATION_TIMEOUT")


def selected(snapshot_value):
    return {item["target_id"]: item for item in snapshot_value["targets"]
            if item["target_id"] in ("runtime", "runtime-db", "linux-pool")}


def all_ready():
    value = snapshot()
    rows = selected(value)
    return value if len(rows) == 3 and all(item["status"] == "HEALTHY" for item in rows.values()) else None


def runtime_armed():
    return any(asset["asset_id"] == "runtime" and asset["armed"] for asset in snapshot()["recovery"]["assets"])


def main():
    report = {"schema_version": 1, "started_at": r.now(), "scope": "local_runtime_k3s", "passed": False, "checks": []}

    def check(name, condition):
        report["checks"].append({"name": name, "passed": bool(condition)})
        r.require(condition, "EXECUTION_CHECK_FAILED_" + name.upper())
        print(json.dumps({"check": name, "passed": True}), flush=True)

    records = r.project_records(r.PROJECT)
    report["monitor_image_id"] = records["sentinel"]["Image"]
    runtime = records["runtime"]
    database = records["runtime-db"]
    r.require(runtime["Config"]["Labels"].get("io.msgctf.sentinel.recovery") == "local-runtime", "RUNTIME_RECOVERY_SCOPE_MISSING")
    connection = json.loads((lab.STATE / "connection.json").read_text())
    operation_id = connection["operation_id"]
    namespace = lab.runtime("operations/" + operation_id)["result"]["runtime_workload_id"].split("/")[1]
    r.require(namespace == "ctf-" + lab.INSTANCE.replace("-", ""), "WORKLOAD_NAMESPACE_MISMATCH")
    try:
        before = wait(all_ready)
        check("three_real_runtime_observations", set(selected(before)) == {"runtime", "runtime-db", "linux-pool"})
        check("postgres_store_selected", r.env(runtime)["PROVISIONER_STORE_MODE"] == "postgres")
        with urllib.request.urlopen(connection["service_url"], timeout=5) as response:
            check("loopback_nodeport_serves_workload", response.status == 200 and b"Welcome to nginx" in response.read(65536))
        try:
            urllib.request.urlopen("http://127.0.0.1:58188/internal/v1/operations/" + operation_id, timeout=5)
            unauthorized = False
        except urllib.error.HTTPError as error:
            unauthorized = error.code == 401
        check("runtime_rejects_unauthenticated_access", unauthorized)
        wait(runtime_armed, timeout=330)
        before_requests = snapshot()["recovery"]["requests"]
        known_ids = {item["request_id"] for item in before_requests}
        status = lab.runtime("instances/" + lab.INSTANCE + "/runtime-status")
        pod_before = status["containers"][0]["pod_name"]
        r.run(["stop", "--time", "10", runtime["Id"]])

        def recovered():
            value = snapshot()
            requests = [item for item in value["recovery"]["requests"] if item["request_id"] not in known_ids and item["asset_id"] == "runtime"]
            return requests[0] if requests and requests[0]["status"] == "SUCCEEDED" else None

        recovered_request = wait(recovered, timeout=210)
        report["recovery_request_id"] = recovered_request["request_id"]
        check("runtime_stopped_service_automatically_recovered", recovered_request["execution_result_code"] == "CONTAINER_STARTED")
        now_records = r.project_records(r.PROJECT)
        check("runtime_restart_keeps_container_identity", now_records["runtime"]["Id"] == runtime["Id"]
              and now_records["runtime"]["State"]["StartedAt"] != runtime["State"]["StartedAt"])
        check("runtime_recovery_does_not_restart_store", now_records["runtime-db"]["Id"] == database["Id"]
              and now_records["runtime-db"]["State"]["StartedAt"] == database["State"]["StartedAt"])
        check("stored_operation_survives_runtime_restart", lab.runtime("operations/" + operation_id)["status"] == "SUCCEEDED")
        check("workload_survives_runtime_restart", lab.runtime("instances/" + lab.INSTANCE + "/runtime-status")["containers"][0]["pod_name"] == pod_before)

        pod = json.loads(lab.kube("get", "pod", pod_before, "-n", namespace, "-o", "json"))
        check("workload_is_owned_by_fixed_instance", pod["metadata"]["labels"].get("msgctf.io/instance-id") == lab.INSTANCE)
        lab.kube("delete", "pod", pod_before, "-n", namespace, "--wait=true", "--timeout=45s")

        def replacement():
            state = lab.runtime("instances/" + lab.INSTANCE + "/runtime-status")
            return state if state["phase"] == "READY" and state["endpoint_ready"] and state["containers"] and all(
                container["ready"] and container["pod_name"] != pod_before for container in state["containers"]) else None

        restored = wait(replacement)
        check("k3s_controller_recreates_deleted_pod", restored["containers"][0]["pod_name"] != pod_before)
        check("pod_recovery_preserves_runtime_binding", restored["runtime_workload_id"] == status["runtime_workload_id"])
        wait(all_ready)
        # Recovery is deliberately disarmed for 300 seconds after an incident.
        # A second independent fault must start after healthy re-arming, without
        # shortening that guard or asking the executor to bypass it.
        print(json.dumps({"phase": "waiting_for_runtime_recovery_cooldown"}), flush=True)
        wait(runtime_armed, timeout=330)
        check("store_fault_starts_with_recovery_armed", runtime_armed())
        known_requests = {item["request_id"] for item in snapshot()["recovery"]["requests"]}
        runtime_before_store_fault = r.project_records(r.PROJECT)["runtime"]
        r.run(["stop", "--time", "10", database["Id"]])
        observed = wait(lambda: (lambda value: value if selected(value)["runtime-db"]["status"] == "DOWN" else None)(snapshot()), timeout=90)
        report["store_fault_reason"] = selected(observed)["runtime-db"]["reason_code"]
        check("store_outage_is_not_reported_healthy", selected(observed)["runtime-db"]["status"] == "DOWN")
        samples = set()

        def store_still_down():
            value = snapshot()
            if selected(value)["runtime-db"]["status"] == "DOWN":
                samples.add(selected(value)["runtime-db"]["checked_at"])
            r.require({item["request_id"] for item in value["recovery"]["requests"]} == known_requests,
                      "RUNTIME_RECOVERY_REQUESTED_WITH_STORE_STOPPED")
            return len(samples) >= 3

        wait(store_still_down, timeout=90)
        during_fault = r.project_records(r.PROJECT)
        runtime_exited = during_fault["runtime"]["State"]["Status"] == "exited"
        report["runtime_exited_on_store_disconnect"] = runtime_exited
        check("no_runtime_recovery_while_store_is_stopped",
              during_fault["runtime-db"]["State"]["Status"] == "exited"
              and during_fault["runtime"]["State"]["StartedAt"] == runtime_before_store_fault["State"]["StartedAt"])
        r.run(["start", database["Id"]])
        if runtime_exited:
            def cascade_recovered():
                requests = [item for item in snapshot()["recovery"]["requests"]
                            if item["request_id"] not in known_requests and item["asset_id"] == "runtime"]
                return requests[0] if requests and requests[0]["status"] == "SUCCEEDED" else None

            cascade = wait(cascade_recovered, timeout=210)
            report["store_cascade_recovery_request_id"] = cascade["request_id"]
            check("runtime_automatically_recovered_after_store_ready", cascade["execution_result_code"] == "CONTAINER_STARTED")
        else:
            check("running_runtime_is_not_restarted_for_store_outage",
                  {item["request_id"] for item in snapshot()["recovery"]["requests"]} == known_requests)
        wait(all_ready)
        check("store_restart_preserves_completed_operation", lab.runtime("operations/" + operation_id)["status"] == "SUCCEEDED")
        final_records = r.project_records(r.PROJECT)
        check("store_cascade_preserves_container_identity", final_records["runtime"]["Id"] == runtime["Id"]
              and final_records["runtime-db"]["Id"] == database["Id"])
        final = snapshot()
        check("all_connected_targets_healthy", all(item["status"] == "HEALTHY" for item in final["targets"] if item["enabled"]))
        report["final_targets"] = [{"target_id": item["target_id"], "status": item["status"], "reason_code": item["reason_code"]}
                                   for item in selected(final).values()]
        report["metrics_available"] = restored["metrics_available"]
        report["passed"] = True
    finally:
        # A failed test must not leave a stopped local store or Runtime behind.
        for service, original in (("runtime-db", database), ("runtime", runtime)):
            current = r.project_records(r.PROJECT).get(service)
            if current and current["Id"] == original["Id"] and current["State"]["Status"] == "exited":
                r.run(["start", current["Id"]])
        report["completed_at"] = r.now()
        r.save(r.LOCAL / "artifacts/execution-verification.json", report)
    print(json.dumps({"passed": report["passed"], "checks": len(report["checks"])}))


if __name__ == "__main__":
    main()
