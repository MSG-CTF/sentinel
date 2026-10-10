"""Check stop/start persistence for only the registered local K3s node."""
import json
import urllib.request

import data_recovery as r
import execution_lab as lab
from verify_execution import all_ready, selected, snapshot, wait


def main():
    report = {"schema_version": 1, "started_at": r.now(), "scope": "local_k3s_stop_start", "passed": False, "checks": []}

    def check(name, passed):
        report["checks"].append({"name": name, "passed": bool(passed)})
        r.require(passed, "K3S_LIFECYCLE_CHECK_FAILED_" + name.upper())
        print(json.dumps({"check": name, "passed": True}), flush=True)

    wait(all_ready)
    node = lab.node_record()
    records = r.project_records(r.PROJECT)
    report["monitor_image_id"] = records["sentinel"]["Image"]
    known = {item["request_id"] for item in snapshot()["recovery"]["requests"]}
    connection = json.loads((lab.STATE / "connection.json").read_text())
    operation = lab.runtime("operations/" + connection["operation_id"])
    try:
        lab.k3d("cluster", "stop", lab.CLUSTER)
        check("only_registered_node_stopped", not lab.node_record()["State"]["Running"])

        def observed_down():
            value = selected(snapshot())
            return value if value["linux-pool"]["status"] == "DOWN" and value["runtime-db"]["status"] == "HEALTHY" else None

        wait(observed_down, timeout=90)
        check("node_outage_distinguished_from_runtime_store", True)
        lab.cluster()
        wait(all_ready, timeout=180)
        current = lab.node_record()
        check("node_container_preserved_after_start", current["Id"] == node["Id"] and current["State"]["StartedAt"] != node["State"]["StartedAt"])
        after = lab.runtime("operations/" + connection["operation_id"])
        check("operation_and_service_binding_preserved", after["status"] == "SUCCEEDED" and after["result"] == operation["result"])
        with urllib.request.urlopen(connection["service_url"], timeout=5) as response:
            check("nodeport_serves_after_node_restart", response.status == 200 and b"Welcome to nginx" in response.read(65536))
        final_records = r.project_records(r.PROJECT)
        check("runtime_and_store_not_restarted", all(final_records[name]["Id"] == records[name]["Id"]
              and final_records[name]["State"]["StartedAt"] == records[name]["State"]["StartedAt"] for name in ("runtime", "runtime-db")))
        check("node_outage_does_not_trigger_host_application_recovery",
              known == {item["request_id"] for item in snapshot()["recovery"]["requests"]})
        report["passed"] = True
    finally:
        if not lab.node_record()["State"]["Running"]:
            lab.cluster()
        report["completed_at"] = r.now()
        r.save(r.LOCAL / "artifacts/execution-lifecycle.json", report)
    print(json.dumps({"passed": report["passed"], "checks": len(report["checks"])}))


if __name__ == "__main__":
    main()
