"""Real local lifecycle and reversible application-fault drill on ledger-owned fixtures."""
import argparse
import json
import time
import urllib.request
import uuid
import functional_lab as f

REPORT = f.lab.LOCAL / "artifacts/functional-flow-verification.json"


def inventory_row(instance_id):
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open("http://127.0.0.1:58240/api/instances", timeout=6) as response:
        data = json.load(response)
    f.recovery.require(data["complete_inventory"] is True and data["coverage"] == "all_pages", "INVALID_INVENTORY_SCOPE")
    return next((row for row in data["instances"] if row["instance_id"] == instance_id), None)


def wait(check, seconds=100):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        result = check()
        if result:
            return result
        time.sleep(2)
    raise f.recovery.RecoveryError("FUNCTIONAL_FLOW_CHECK_TIMEOUT")


def main(instance_id):
    report = {"started_at": f.recovery.now(), "passed": False,
        "scope": "real_local_scheduler_broker_runtime_with_imported_functional_fixture",
        "competition_challenge_verified": False, "cloud_api_verified": False, "node_agent_verified": False,
        "automatic_instance_recovery_enabled": False, "checks": []}

    def check(name, passed, evidence=None):
        report["checks"].append({"name": name, "passed": bool(passed), "checked_at": f.recovery.now(), "evidence": evidence})
        f.recovery.save(REPORT, report)
        f.recovery.require(passed, "CHECK_FAILED_" + name.upper())
        print(json.dumps({"check": name, "passed": True}), flush=True)

    def expect(health):
        return wait(lambda: (lambda row: row if row and row["challenge_health"] == health else None)(inventory_row(instance_id)))

    def deleted(target, cause):
        detail = wait(lambda: (lambda d: d if d["status"] == "CLEANED" else None)(f.owned(target)[1]), seconds=150)
        runtime = f.lab.runtime("instances/" + target + "/runtime-status")
        reserve = f.reservation(target)
        namespace = "ctf-" + uuid.UUID(target).hex
        absent = not f.lab.kube("get", "namespace", namespace, "--ignore-not-found", "-o", "json").strip()
        row = wait(lambda: (lambda r: r if r and r["status"] == "REMOVED" else None)(inventory_row(target)))
        check(cause + "_releases_real_resources", runtime["phase"] == "TERMINATED" and absent
              and reserve["status"] == "RELEASED", {"instance_id": target, "scheduler": detail["status"],
              "runtime": runtime["phase"], "broker": reserve["status"], "release_reason": reserve["release_reason"],
              "namespace_absent": absent, "sentinel": row["status"]})

    try:
        initial = expect("UP")
        check("real_create_and_commit", f.reservation(instance_id)["status"] == "COMMITTED"
              and initial["runtime_phase"] == "READY", {"instance_id": instance_id, "observation": initial})
        for mode, health in (("wrong_result", "DOWN"), ("invalid_json", "CHECKER_ERROR"), ("unavailable", "DOWN")):
            f.fault(instance_id, mode)
            row = expect(health)
            check(mode + "_distinguished_from_ready", row["runtime_phase"] == "READY"
                  and row["recovery_action"] == "manual_review", row)
        f.fault(instance_id, "none")
        restored = expect("UP")
        check("manual_fault_clear_restores_function", restored["status"] == "READY", restored)
        f.delete(instance_id)
        deleted(instance_id, "user_delete")
        ttl_id = f.create(1)
        report["ttl_instance_id"] = ttl_id
        deleted(ttl_id, "ttl_expiry")
        original = f.lab.runtime("instances/" + f.lab.INSTANCE + "/runtime-status")
        check("existing_nginx_fixture_preserved", original["phase"] == "READY")
        report["passed"] = True
    finally:
        # Restore the owned application even if an assertion fails before deletion
        try:
            if f.owned(instance_id)[1]["status"] == "RUNNING": f.fault(instance_id, "none")
        except (f.recovery.RecoveryError, OSError, ValueError):
            report["fault_restore_needs_review"] = True
        report["finished_at"] = f.recovery.now()
        f.recovery.save(REPORT, report)
    print(json.dumps({"passed": report["passed"], "checks": len(report["checks"]), "report": str(REPORT)}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--instance", required=True)
    args = parser.parse_args()
    main(args.instance)
