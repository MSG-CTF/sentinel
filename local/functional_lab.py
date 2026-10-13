"""Explicit local fixture lifecycle through the real Scheduler/Broker/Runtime APIs.

Only IDs in the private ledger can be inspected, faulted or deleted by this tool
The GHCR-shaped reference satisfies Scheduler validation; the image is imported
locally and is never published to a registry
"""
import argparse
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

import data_recovery as recovery
import execution_lab as lab

STATE = lab.STATE / "functional"
IMAGE_NAME = "ghcr.io/msg-ctf/sentinel-local-functional-fixture"
SCOPE = "sentinel_local_functional_fixture"


def api(service, path, body=None, method=None):
    ports = {"scheduler": 58182, "broker": 58183}
    keys = {"scheduler": "SCHEDULER_API_TOKEN", "broker": "SCHEDULER_BROKER_TOKEN"}
    values = dict(line.split("=", 1) for line in (lab.LOCAL / ".assets/services.env").read_text().splitlines() if "=" in line)
    request = urllib.request.Request(f"http://127.0.0.1:{ports[service]}{path}",
        data=None if body is None else json.dumps(body).encode(), method=method,
        headers={"Authorization": "Bearer " + values[keys[service]], "Content-Type": "application/json"})
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({}), lab.NoRedirect()).open(request, timeout=15) as response:
            result = json.load(response)
    except urllib.error.HTTPError as error:
        # Diagnostic body remains private; exceptions and stdout contain fixed codes
        recovery.save(STATE / "last-http-error.json", {"service": service, "status": error.code,
                      "body": error.read(32768).decode(errors="replace")})
        raise recovery.RecoveryError(f"{service.upper()}_HTTP_{error.code}") from None
    return result["data"] if service == "scheduler" else result


def ledger():
    path = STATE / "ledger.json"
    return json.loads(path.read_text()) if path.exists() else {"scope": SCOPE, "instances": {}}


def owned(instance_id):
    data = ledger()
    recovery.require(data["scope"] == SCOPE and instance_id in data["instances"], "INSTANCE_NOT_OWNED")
    item = data["instances"][instance_id]
    detail = api("scheduler", "/api/instances/" + str(uuid.UUID(instance_id)))
    recovery.require(detail["team_id"] == item["team_id"] and detail["challenge_id"] == item["challenge_id"], "INSTANCE_SCOPE_MISMATCH")
    return item, detail


def prepare():
    node = lab.node_record()
    nodes = json.loads(lab.kube("get", "nodes", "-o", "json"))["items"]
    recovery.require(len(nodes) == 1 and any(c["type"] == "Ready" and c["status"] == "True"
        for c in nodes[0]["status"]["conditions"]), "LOCAL_NODE_NOT_READY")
    archive = STATE / "fixture-image.tar"
    STATE.mkdir(exist_ok=True)
    recovery.run(["image", "save", "-o", str(archive), IMAGE_NAME + ":local-only"], timeout=120)
    recovery.run(["cp", str(archive), node["Id"] + ":/tmp/sentinel-functional-image.tar"], timeout=120)
    recovery.run(["exec", node["Id"], "ctr", "--namespace", "k8s.io", "images", "import", "--platform", "linux/amd64",
                  "--digests", "--base-name", IMAGE_NAME, "/tmp/sentinel-functional-image.tar"], timeout=120)
    rows = recovery.run(["exec", node["Id"], "ctr", "--namespace", "k8s.io", "images", "ls"]).decode().splitlines()
    matches = [line.split()[2] for line in rows if line.split()[0] == IMAGE_NAME + ":local-only"]
    recovery.require(len(matches) == 1 and re.fullmatch(r"sha256:[0-9a-f]{64}", matches[0]), "IMPORTED_IMAGE_DIGEST_MISSING")
    image = IMAGE_NAME + "@" + matches[0]
    recovery.run(["exec", node["Id"], "ctr", "--namespace", "k8s.io", "images", "tag", "--force", IMAGE_NAME + ":local-only", image])
    recovery.save(STATE / "image.json", {"image": image, "published": False, "scope": SCOPE})
    refresh_capacity()
    print(json.dumps({"prepared": True, "image": image, "cloud_verified": False}), flush=True)


def refresh_capacity():
    nodes = json.loads(lab.kube("get", "nodes", "-o", "json"))["items"]
    ready = len(nodes) == 1 and any(c["type"] == "Ready" and c["status"] == "True" for c in nodes[0]["status"]["conditions"])
    recovery.require(ready, "LOCAL_NODE_NOT_READY")
    records = recovery.project_records("msg-sla-real-local")
    result = recovery.run(["exec", "-i", records["broker"]["Id"], "python", "-c",
        (lab.LOCAL / "seed_functional_capacity.py").read_text()], input=json.dumps({"scope": SCOPE,
            "node_ready": ready, "target_id": lab.TARGET}).encode())
    recovery.save(STATE / "capacity.json", json.loads(result))


def create(ttl):
    data = ledger()
    # Every POST creates an instance, so never retry a create implicitly
    recovery.require(not (STATE / "pending-create.json").exists(), "CREATE_OUTCOME_REQUIRES_RECONCILIATION")
    refresh_capacity()
    request = {"team_id": str(uuid.uuid4()), "user_id": str(uuid.uuid4()), "challenge_id": str(uuid.uuid4()),
        "containers": [{"name": "calculator", "image": json.loads((STATE / "image.json").read_text())["image"],
                        "ports": [8080], "expose": True}],
        "registry_revision": 1, "isolation_profile": "WEB", "architecture": "AMD64",
        "resource_profile": {"cpu_millicores": 100, "memory_mib": 64, "ephemeral_storage_mib": 128},
        "ttl_minutes": ttl, "hard_timeout_minutes": ttl}
    recovery.save(STATE / "pending-create.json", request)
    response = api("scheduler", "/api/instances", request)
    instance_id = response["instance_id"]
    data["instances"][instance_id] = dict(request, created_at=recovery.now())
    recovery.save(STATE / "ledger.json", data)
    (STATE / "pending-create.json").unlink()
    print(json.dumps({"created": instance_id, "status": response["status"], "ttl_minutes": ttl}), flush=True)
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        _, detail = owned(instance_id)
        if detail["status"] == "RUNNING":
            register(instance_id)
            print(json.dumps({"running": instance_id, "reservation": reservation(instance_id)["status"]}), flush=True)
            return instance_id
        if detail["status"] in ("FAILED", "CLEANED"):
            recovery.save(STATE / (instance_id + "-events.json"), api("scheduler", "/api/instances/" + instance_id + "/events"))
            raise recovery.RecoveryError("SCHEDULER_CREATE_FAILED")
        time.sleep(2)
    raise recovery.RecoveryError("SCHEDULER_CREATE_TIMEOUT")


def register(instance_id):
    item, detail = owned(instance_id)
    status = lab.runtime("instances/" + instance_id + "/runtime-status")
    recovery.require(detail["status"] == "RUNNING" and detail["runtime_target_id"] == lab.TARGET
        and status["phase"] == "READY" and status["runtime_workload_id"] == detail["runtime_workload_id"], "RUNTIME_NOT_BOUND_READY")
    url = urllib.parse.urlparse(detail["service_url"])
    recovery.require(url.scheme == "http" and url.hostname == "127.0.0.1" and not url.username and not url.password
        and url.port in range(30080, 30090) and url.path in ("", "/") and not url.query and not url.fragment, "NONLOCAL_ENDPOINT")
    binding = {"check_id": "local_" + uuid.UUID(instance_id).hex, "instance_id": instance_id,
        "challenge_id": item["challenge_id"], "target_id": lab.TARGET, "runtime_workload_id": detail["runtime_workload_id"],
        "url": f"http://host.docker.internal:{url.port}/calculate"}
    data = ledger()
    data["instances"][instance_id]["binding"] = binding
    recovery.save(STATE / "ledger.json", data)
    monitor_bindings()


def monitor_bindings():
    config_path = lab.STATE / "monitor.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["instance_monitor"]["functional_checks"] = [item["binding"] for item in ledger()["instances"].values()
        if "binding" in item][-20:]
    recovery.save(config_path, config)


def reservation(instance_id):
    item, _ = owned(instance_id)
    reservation_id = item.get("reservation_id")
    if not reservation_id:
        records = recovery.project_records("msg-sla-real-local")
        query = "SELECT reservation_id FROM challenge_instance WHERE instance_id = '" + str(uuid.UUID(instance_id)) + "';"
        reservation_id = recovery.run(["exec", records["scheduler-db"]["Id"], "psql", "-U", "scheduler", "-d", "scheduler",
                                      "-tAc", query]).decode().strip()
        reservation_id = str(uuid.UUID(reservation_id))
        data = ledger()
        data["instances"][instance_id]["reservation_id"] = reservation_id
        recovery.save(STATE / "ledger.json", data)
    result = api("broker", "/v1/reservations/" + str(uuid.UUID(reservation_id)))
    recovery.require(result["instance_id"] == instance_id, "RESERVATION_IDENTITY_MISMATCH")
    return result


def pod(instance_id):
    item, detail = owned(instance_id)
    recovery.require(detail["runtime_target_id"] == lab.TARGET, "WRONG_RUNTIME_TARGET")
    # Select only the pod matching both this run's instance ID and imported fixture image
    namespace = "ctf-" + uuid.UUID(instance_id).hex
    pods = json.loads(lab.kube("get", "pods", "-n", namespace, "-o", "json"))["items"]
    matches = [p for p in pods if p["metadata"].get("labels", {}).get("msgctf.io/instance-id") == instance_id
        and p["metadata"]["labels"].get("msgctf.io/team-id") == item["team_id"]
        and any(c["image"] == item["containers"][0]["image"] for c in p["spec"]["containers"])]
    recovery.require(len(matches) == 1, "OWNED_POD_NOT_UNIQUE")
    return matches[0]


def fault(instance_id, mode):
    item = pod(instance_id)
    lab.kube("exec", "-n", item["metadata"]["namespace"], item["metadata"]["name"], "-c", "calculator", "--",
             "/functional-fixture", "fault", mode)
    print(json.dumps({"instance_id": instance_id, "fault": mode, "actor": "local_test_operator"}), flush=True)


def delete(instance_id):
    owned(instance_id)
    reservation(instance_id)  # Cache the ID before Scheduler clears its active reservation
    response = api("scheduler", "/api/instances/" + instance_id, {"delete_reason": "USER_REQUESTED"}, "DELETE")
    print(json.dumps({"instance_id": instance_id, "delete_status": response["status"]}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("prepare", "create", "register", "fault", "delete", "reservation"))
    parser.add_argument("--instance")
    parser.add_argument("--ttl", type=int, default=120, choices=range(1, 181))
    parser.add_argument("--mode", choices=("none", "wrong_result", "unavailable", "invalid_json"), default="none")
    args = parser.parse_args()
    try:
        if args.action == "prepare": prepare()
        elif args.action == "create": create(args.ttl)
        elif args.action == "register": register(args.instance)
        elif args.action == "fault": fault(args.instance, args.mode)
        elif args.action == "delete": delete(args.instance)
        else: print(json.dumps(reservation(args.instance)), flush=True)
    except recovery.RecoveryError as error:
        print(json.dumps({"error_code": str(error)}), flush=True)
        raise SystemExit(1)
