"""Read real endpoints and optionally stop only the new, exact local asset containers."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import time
import urllib.error
import urllib.request

LOCAL = Path(__file__).resolve().parent
DOCKER = ["docker", "--host", "npipe:////./pipe/dockerDesktopLinuxEngine"]
PROJECT = "msg-sla-real-local"
BASE = "http://127.0.0.1:58240"
CHECKS = []


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


CLIENT = urllib.request.build_opener(NoRedirect, urllib.request.ProxyHandler({}))


def get(url, token=None):
    headers = {"Authorization": "Bearer " + token} if token else {}
    try:
        response = CLIENT.open(urllib.request.Request(url, headers=headers), timeout=8)
    except urllib.error.HTTPError as response:
        return response.code, response.headers.get_content_type(), response.read(65537)
    with response:
        return response.status, response.headers.get_content_type(), response.read(65537)


def check(name, passed):
    if not passed:
        raise AssertionError(name)
    CHECKS.append(name)


def snapshot():
    return json.loads(get(BASE + "/api/status")[2])


def containers():
    ids = subprocess.check_output(DOCKER + ["ps", "-aq", "--filter", "label=com.docker.compose.project=" + PROJECT], text=True).split()
    records = json.loads(subprocess.check_output(DOCKER + ["inspect", *ids], text=True))
    return {item["Config"]["Labels"]["com.docker.compose.service"]: {
        "id": item["Id"], "started_at": item["State"]["StartedAt"], "status": item["State"]["Status"],
        "oneoff": item["Config"]["Labels"].get("com.docker.compose.oneoff"),
    } for item in records}


def wait_targets(expected, timeout=120):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        current = snapshot()
        targets = {t["target_id"]: t for t in current["targets"]}
        if all(targets.get(key, {}).get("status") == status for key, status in expected.items()):
            return current
        time.sleep(2)
    raise AssertionError("Target status deadline: " + json.dumps(expected))


def main(faults=False):
    credentials = dict(line.split("=",1) for line in (LOCAL / ".assets/services.env").read_text().splitlines() if "=" in line)
    connected = ["internal-backend", "external-backend", "edge", "internal-frontend", "external-frontend", "scheduler", "scheduler-db", "broker", "broker-db"]
    initial = wait_targets(dict.fromkeys(connected, "HEALTHY"), 180)
    check("local_recovery_mode_is_active", initial["profile"] == "local-recovery" and initial["recovery"]["enabled"])
    check("nine_direct_probes_healthy", len([t for t in initial["targets"] if t["status"] == "HEALTHY"]) == 9)
    missing = [t for t in initial["targets"] if not t["enabled"]]
    check("twelve_absent_roles_have_reasons_without_timestamps", len(missing) == 12 and all(t["reason_code"] != "ENDPOINT_NOT_CONFIGURED" and t["checked_at"] is None for t in missing))
    public = json.dumps(initial) + get(BASE + "/asset-access.json")[2].decode() + get(BASE + "/api/recovery")[2].decode()
    check("public_status_contains_no_service_credentials", all(value not in public for value in credentials.values()))
    before = containers()
    recovery_before = json.loads(get(BASE + "/api/recovery")[2])["requests"]
    scheduler = "http://127.0.0.1:58182/api/instances/recent"
    broker = "http://127.0.0.1:58183/v1/inventory/resource-targets"
    for name, url in [("scheduler",scheduler),("broker",broker)]:
        check(name + "_requires_authentication", get(url)[0] == 401)
    scheduler_result = get(scheduler, credentials["SCHEDULER_API_TOKEN"])
    broker_result = get(broker, credentials["INVENTORY_API_TOKEN"])
    check("scheduler_queries_real_empty_database", scheduler_result[0] == 200 and json.loads(scheduler_result[2])["data"] == [])
    inventory = json.loads(broker_result[2])
    check("broker_inventory_empty_without_fake_cloud_targets", broker_result[0] == 200 and inventory["total"] == 0 and inventory["items"] == [])
    check("broker_inventory_token_cannot_read_admin_accounts", get("http://127.0.0.1:58183/v1/admin/provider-accounts", credentials["INVENTORY_API_TOKEN"])[0] in (401,403))
    for track, backend, frontend, edge in [("internal",58180,58184,58186),("external",58181,58185,58187)]:
        endpoint = "/api/v1/challenges/00000000-0000-0000-0000-000000000000"
        direct = get(f"http://127.0.0.1:{backend}" + endpoint)
        for label, port in [("frontend",frontend),("edge",edge)]:
            page = get(f"http://127.0.0.1:{port}/")
            forwarded = get(f"http://127.0.0.1:{port}" + endpoint)
            check(track + "_" + label + "_serves_built_html", page[0] == 200 and b'<div id="root"></div>' in page[2] and b'/assets/' in page[2])
            check(track + "_" + label + "_forwards_real_backend_response", forwarded[0] == direct[0] and forwarded[1] == direct[1] == "application/json" and json.loads(forwarded[2]) == json.loads(direct[2]))
    faults_seen = []
    stopped = []
    try:
        if faults:
            for service, targets in [("scheduler-db",["scheduler","scheduler-db"]),("broker-db",["broker","broker-db"])]:
                deadline = time.monotonic() + 380
                while not next(item for item in snapshot()["recovery"]["assets"] if item["asset_id"] == targets[0])["armed"]:
                    if time.monotonic() >= deadline:
                        raise AssertionError("Recovery baseline was not armed before dependency fault")
                    time.sleep(2)
                check(service + "_recovery_armed_before_dependency_fault", True)
                record = before[service]
                check(service + "_exact_local_running_container", record["status"] == "running" and str(record["oneoff"]).lower() == "false")
                subprocess.run(DOCKER + ["stop", "--time", "5", record["id"]], check=True, capture_output=True, timeout=35)
                stopped.append(record["id"])
                failed = wait_targets(dict.fromkeys(targets, "DOWN"))
                readings = {t["target_id"]: t for t in failed["targets"]}
                faults_seen.append({"service":service,"observations":[{key:readings[target][key] for key in ("target_id","status","reason_code")} for target in targets]})
                check(service + "_failure_is_detected", all(readings[target]["status"] == "DOWN" for target in targets))
                time.sleep(48)  # More than three normal poll intervals; no data failure may dispatch a start.
                check(service + "_does_not_trigger_application_recovery", json.loads(get(BASE + "/api/recovery")[2])["requests"] == recovery_before)
                subprocess.run(DOCKER + ["start", record["id"]], check=True, capture_output=True, timeout=35)
                stopped.remove(record["id"])
                wait_targets(dict.fromkeys(targets, "HEALTHY"))
                check(service + "_manual_restoration_verified", True)
    finally:
        for identifier in stopped:
            subprocess.run(DOCKER + ["start", identifier], check=True, capture_output=True, timeout=35)
    final = wait_targets(dict.fromkeys(connected, "HEALTHY"))
    after = containers()
    unchanged = ["backend-internal","backend-external","postgres-internal","postgres-external","redis-internal","redis-external"]
    check("existing_six_containers_unchanged", all(before[name] == after[name] for name in unchanged))
    check("new_containers_keep_identity_and_persistent_volumes", all(before[name]["id"] == after[name]["id"] for name in before))
    check("seven_fixed_application_recovery_bindings", final["recovery"]["enabled"] and len(final["recovery"]["assets"]) == 7)
    result = {"passed":True,"checked_at":datetime.now(timezone.utc).isoformat(),"checks":CHECKS,"faults":faults_seen,
        "direct_probes":9,"healthy_assets_including_backend_dependencies":13,"unconfigured_roles":12,
        "recovery_for_added_assets":"seven_fixed_applications","source_versions":{"scheduler":"5c695142","broker":"99ff5b88","frontend":"c0a85359"}}
    path = LOCAL / "artifacts" / ("assets-verification-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + ".json")
    path.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"passed":True,"checks":len(CHECKS),"report":str(path)},ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--faults",action="store_true")
    main(parser.parse_args().faults)
