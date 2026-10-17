"""Remove one owned fixture Pod and observe K3s Deployment reconciliation."""
import argparse
import json
import time
from datetime import datetime
import functional_lab as f
from verify_functional_flow import inventory_row, wait


def main(instance_id):
    started = f.recovery.now()
    report = {"started_at": started, "instance_id": instance_id, "passed": False,
        "scope": "owned_local_functional_fixture", "recovery_actor": "k3s_deployment_controller",
        "sentinel_recovery_request_sent": False, "sentinel_down_observed": False}
    before = f.pod(instance_id)
    initial = inventory_row(instance_id)
    f.recovery.require(initial is not None and initial["challenge_health"] == "UP", "FUNCTION_NOT_READY_BEFORE_DRILL")
    binding = f.owned(instance_id)[0]["binding"]
    namespace = before["metadata"]["namespace"]
    report["before_pod_uid"] = before["metadata"]["uid"]
    f.recovery.save(f.STATE / "pod-recovery-pending.json", report)
    # Name, namespace, instance label, team and image were checked by pod()
    f.lab.kube("delete", "pod", before["metadata"]["name"], "-n", namespace, "--wait=false")

    def recovered():
        row = inventory_row(instance_id)
        if row and row["status"] == "DOWN": report["sentinel_down_observed"] = True
        items = json.loads(f.lab.kube("get", "pods", "-n", namespace, "-o", "json"))["items"]
        matches = [p for p in items if p["metadata"]["uid"] != before["metadata"]["uid"]
            and p["metadata"].get("labels", {}).get("msgctf.io/instance-id") == instance_id
            and any(c["type"] == "Ready" and c["status"] == "True" for c in p.get("status", {}).get("conditions", []))]
        if len(matches) != 1 or row is None or row["challenge_health"] != "UP": return None
        fresh = datetime.fromisoformat(row["functional_check"]["checked_at"].replace("Z", "+00:00"))
        ready_at = max(datetime.fromisoformat(c["lastTransitionTime"].replace("Z", "+00:00"))
                       for c in matches[0]["status"]["conditions"] if c["type"] == "Ready")
        return (matches[0], row) if fresh >= ready_at else None

    try:
        after, observation = wait(recovered, seconds=120)
        runtime = f.lab.runtime("instances/" + instance_id + "/runtime-status")
        reserve = f.reservation(instance_id)
        f.recovery.require(runtime["phase"] == "READY" and runtime["runtime_workload_id"] == binding["runtime_workload_id"]
            and reserve["status"] == "COMMITTED", "RECOVERED_RUNTIME_BINDING_MISMATCH")
        report.update(passed=True, after_pod_uid=after["metadata"]["uid"], runtime_phase=runtime["phase"],
            broker_status=reserve["status"], observation=observation, finished_at=f.recovery.now())
    finally:
        f.recovery.save(f.lab.LOCAL / "artifacts/owned-pod-recovery-verification.json", report)
    (f.STATE / "pod-recovery-pending.json").unlink()
    print(json.dumps({"passed": report["passed"], "recovery_actor": report["recovery_actor"],
                      "sentinel_down_observed": report["sentinel_down_observed"]}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--instance", required=True)
    main(parser.parse_args().instance)
