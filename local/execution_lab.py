"""Fixed local K3s/Runtime lab; never uses the user's default Kubernetes context."""
import argparse
import ipaddress
import json
import os
import subprocess
import time
import urllib.error
import urllib.request

import data_recovery as recovery
from prepare_assets import prepare

LOCAL = recovery.LOCAL
STATE = LOCAL / ".execution"
CLUSTER = "msg-sla-execution"
NODE = "k3d-msg-sla-execution-server-0"
NETWORK = "k3d-msg-sla-execution"
TARGET = "sentinel-local-k3s"
INSTANCE = "a716671b-48a3-4fcb-813a-1b9a2280463e"
TEAM = "18c79c7c-21f9-45c6-83a6-1cce547dfc40"
IMAGE = "nginxinc/nginx-unprivileged@sha256:aa8c9087d36d93e9d650c5365f883b421e8214aedbad24ade52b844c583358f1"
K3S_IMAGE = "ghcr.io/k3s-io/k3s:v1.35.5-k3s1"
K3S_DIGEST = "ghcr.io/k3s-io/k3s@sha256:2074403abe1bded11ef3dde09d457e13be8e0b64c218b1c4f8269b4565cfbc65"
RUN_LABEL = "io.msgctf.sentinel.execution"


def k3d(*args):
    executable = LOCAL / ".tooling/k3d/k3d.exe"
    recovery.require(recovery.digest(executable) == "49d0b9c796f5b8ba6f58a1dd5469e8b7b34bf9b6ce9078633eedb2789680a034", "K3D_CHECKSUM_MISMATCH")
    completed = subprocess.run([str(executable), *args], capture_output=True, timeout=240,
                               env=dict(os.environ, DOCKER_HOST=recovery.DOCKER[-1]))
    STATE.mkdir(exist_ok=True)
    (STATE / "k3d-last.log").write_bytes(completed.stdout + completed.stderr)
    recovery.require(completed.returncode == 0, "K3D_COMMAND_FAILED")
    return completed.stdout


def node_record():
    records = recovery.objects(["inspect", NODE])
    recovery.require(len(records) == 1, "K3S_NODE_AMBIGUOUS")
    record = records[0]
    labels = record["Config"].get("Labels") or {}
    recovery.require(labels.get(RUN_LABEL) == CLUSTER and labels.get("k3d.cluster") == CLUSTER, "K3S_SCOPE_MISMATCH")
    recovery.require(set(record["NetworkSettings"]["Networks"]) == {NETWORK}, "K3S_NETWORK_MISMATCH")
    recovery.require(all(port["HostIp"] == "127.0.0.1" for ports in record["HostConfig"]["PortBindings"].values()
                         for port in ports), "K3S_PORT_EXPOSED")
    return record


def kube(*args, body=None, timeout=90):
    node = node_record()
    expected_image = recovery.objects(["image", "inspect", K3S_DIGEST])[0]["Id"]
    recovery.require(node["Image"] == expected_image, "K3S_NODE_IMAGE_MISMATCH")
    return recovery.run(["exec", "-i", node["Id"], "kubectl", "--request-timeout=170s", *args],
                        input=None if body is None else json.dumps(body).encode(), timeout=timeout)


def cluster():
    STATE.mkdir(exist_ok=True)
    ids = recovery.run(["ps", "-aq", "--filter", "name=^/" + NODE + "$"]).decode().split()
    if not ids:
        # A cancelled first-time k3d setup can leave an empty image volume/network.
        networks = recovery.run(["network", "ls", "-q", "--filter", "name=^" + NETWORK + "$"]).decode().split()
        for network in recovery.objects(["network", "inspect", *networks]) if networks else []:
            recovery.require(network["Name"] == NETWORK and network["Labels"].get("app") == "k3d"
                             and not network["Containers"], "K3D_PARTIAL_NETWORK_IN_USE")
            recovery.run(["network", "rm", network["Id"]])
        volume = NETWORK + "-images"
        names = recovery.run(["volume", "ls", "-q", "--filter", "name=^" + volume + "$"]).decode().split()
        for item in recovery.objects(["volume", "inspect", *names]) if names else []:
            recovery.require(item["Name"] == volume and item["Labels"].get("k3d.cluster") == CLUSTER
                             and not recovery.run(["ps", "-aq", "--filter", "volume=" + volume]).strip(), "K3D_PARTIAL_VOLUME_IN_USE")
            recovery.run(["volume", "rm", volume])
        networks = recovery.objects(["network", "inspect", *recovery.run(["network", "ls", "-q"]).decode().split()])
        occupied = [ipaddress.ip_network(item["Subnet"]) for network in networks
                    for item in network.get("IPAM", {}).get("Config") or [] if item.get("Subnet")]
        for subnet in ("10.246.246.0/24", "10.246.247.0/24"):
            recovery.require(not any(ipaddress.ip_network(subnet).overlaps(item) for item in occupied if item.version == 4), "EXECUTION_SUBNET_COLLISION")
        pinned = recovery.objects(["image", "inspect", K3S_IMAGE])[0]
        recovery.require(K3S_DIGEST in pinned["RepoDigests"], "K3S_IMAGE_MISMATCH")
        k3d("cluster", "create", CLUSTER, "--image", K3S_IMAGE, "--servers", "1", "--agents", "0",
            "--servers-memory", "3g", "--subnet", "10.246.247.0/24", "--api-port", "127.0.0.1:58643",
            "--no-lb", "--kubeconfig-switch-context=false", "--kubeconfig-update-default=false",
            "--k3s-arg", "--disable=traefik,servicelb@server:0",
            "--k3s-arg", "--kubelet-arg=pod-max-pids=128@server:0",
            "--k3s-arg", "--kube-apiserver-arg=service-node-port-range=30080-30089@server:0",
            "--port", "127.0.0.1:30080-30089:30080-30089@server:0:direct",
            "--runtime-label", RUN_LABEL + "=" + CLUSTER + "@server:0", "--timeout", "180s")
    node = node_record()
    if not node["State"]["Running"]:
        recovery.run(["start", node["Id"]])
    # Docker start can return before the API server accepts connections.
    recovery.wait_for(lambda: kube("wait", "--for=condition=Ready", "node/" + NODE,
                                  "--timeout=10s", timeout=20), timeout=180)
    destination = STATE / "runtime"
    destination.mkdir(exist_ok=True)
    kubeconfig = recovery.run(["exec", node["Id"], "cat", "/etc/rancher/k3s/k3s.yaml"]).decode()
    recovery.require(kubeconfig.count("https://127.0.0.1:6443") == 1, "KUBECONFIG_ENDPOINT_UNEXPECTED")
    (destination / "kubeconfig.yaml").write_text(kubeconfig.replace("https://127.0.0.1:6443", "https://" + NODE + ":6443"), encoding="utf-8")
    (STATE / "kubeconfig.local.yaml").write_text(kubeconfig.replace("https://127.0.0.1:6443", "https://127.0.0.1:58643"), encoding="utf-8")
    # Runtime's current protocol has only cloud provider enums. AWS here is a
    # protocol placeholder, recorded explicitly as local Docker in the receipt.
    capabilities = {"network_policy_enforced": False, "supplemental_groups_policy_strict": False,
                    "pod_pid_limit_enforced": False, "network_policy_provider": "kube-router",
                    "dns_namespace": "kube-system", "dns_pod_selector": {"k8s-app": "kube-dns"}}
    prior = destination / "clusters.json"
    receipt = STATE / "cluster.json"
    if prior.exists() and receipt.exists() and json.loads(receipt.read_text()).get("node_container_id") == node["Id"]:
        capabilities = json.loads(prior.read_text())["clusters"][0]["security_capabilities"]
    recovery.save(prior, {"clusters": [{"target_id": TARGET, "provider": "AWS", "region": "local-docker",
        "architecture": "amd64", "kubeconfig_path": "/config/kubeconfig.yaml", "public_gateway": "http://127.0.0.1",
        "exposure_mode": "NODE_PORT", "enabled": True, "security_capabilities": capabilities}]})
    recovery.save(STATE / "cluster.json", {"cluster": CLUSTER, "scope": "local_docker_only", "image": K3S_DIGEST,
        "node_container_id": node["Id"], "provider_field_is_placeholder": True, "default_context_selected": False})
    print(json.dumps({"cluster": CLUSTER, "node_ready": True}), flush=True)


def load_image():
    node = node_record()
    names = recovery.run(["exec", node["Id"], "ctr", "--namespace", "k8s.io", "images", "ls", "-q"]).decode().splitlines()
    if "docker.io/" + IMAGE in names:
        print(json.dumps({"image_present": IMAGE}), flush=True)
        return
    archive = STATE / "smoke-image.tar"
    recovery.run(["image", "save", "-o", str(archive), IMAGE], timeout=120)
    recovery.run(["cp", str(archive), node["Id"] + ":/tmp/sentinel-smoke-image.tar"], timeout=120)
    recovery.run(["exec", node["Id"], "ctr", "--namespace", "k8s.io", "images", "import", "--platform", "linux/amd64",
                  "--digests", "--base-name", "docker.io/nginxinc/nginx-unprivileged", "/tmp/sentinel-smoke-image.tar"], timeout=120)
    print(json.dumps({"image_imported": IMAGE}), flush=True)


def runtime(path, body=None):
    values = dict(line.split("=", 1) for line in (LOCAL / ".assets/services.env").read_text().splitlines() if "=" in line)
    request = urllib.request.Request("http://127.0.0.1:58188/internal/v1/" + path,
        data=None if body is None else json.dumps(body).encode(),
        headers={"Authorization": "Bearer " + values["SCHEDULER_RUNTIME_TOKEN"], "Content-Type": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    with opener.open(request, timeout=10) as response:
        return json.load(response)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise recovery.RecoveryError("RUNTIME_REDIRECT_BLOCKED")


def connect():
    request = {"request_id": "sentinel-local-connection-v1", "instance_id": INSTANCE, "team_id": TEAM,
        "isolation_profile": "WEB", "target": {"runtime_type": "KUBERNETES", "target_id": TARGET},
        "workload": {"containers": [{"name": "smoke", "image": IMAGE, "ports": [8080], "expose": True,
            "run_as_user": 101, "writable_paths": [{"path": "/tmp", "size_mib": 32}]}],
            "resource_limits": {"cpu_millicores": 250, "memory_mib": 128, "ephemeral_storage_mib": 256}}}
    operation = runtime("instances", request)
    recovery.save(STATE / "operation.json", operation)
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        operation = runtime("operations/" + operation["operation_id"])
        recovery.save(STATE / "operation.json", operation)
        if operation["status"] == "SUCCEEDED":
            break
        recovery.require(operation["status"] not in ("FAILED", "CANCELLED"), "RUNTIME_CREATE_FAILED")
        time.sleep(2)
    recovery.require(operation["status"] == "SUCCEEDED", "RUNTIME_CREATE_TIMEOUT")
    status = runtime("instances/" + INSTANCE + "/runtime-status")
    recovery.require(status["phase"] == "READY" and status["endpoint_ready"] and status["node"]["ready"], "RUNTIME_WORKLOAD_NOT_READY")
    recovery.save(STATE / "connection.json", {"connected_at": recovery.now(), "instance_id": INSTANCE,
        "target_id": TARGET, "operation_id": operation["operation_id"], "service_url": operation["result"]["service_url"],
        "workload_image": IMAGE, "workload_scope": "nginx_connection_fixture_not_a_competition_challenge"})
    print(json.dumps({"connected": True, "service_url": operation["result"]["service_url"]}), flush=True)


def monitor(profile):
    connection = json.loads((STATE / "connection.json").read_text())
    config = json.loads((LOCAL.parent / "src/Sentinel.Api/profiles" / (profile + ".json")).read_text(encoding="utf-8"))
    config["instance_monitor"] = {"enabled": True, "runtime_targets": [
        {"target_id": TARGET, "base_url": "http://runtime:8080", "auth_token_env": "RUNTIME_MONITOR_TOKEN"}
    ]}
    functional_ledger = STATE / "functional/ledger.json"
    if functional_ledger.exists():
        entries = json.loads(functional_ledger.read_text())
        recovery.require(entries["scope"] == "sentinel_local_functional_fixture", "FUNCTIONAL_LEDGER_SCOPE_MISMATCH")
        config["instance_monitor"]["functional_checks"] = [item["binding"] for item in entries["instances"].values()
            if "binding" in item][-20:]
    if profile == "local-recovery":
        config["platform_recovery"]["enabled_assets"].append("runtime")
    for target in config["platform_monitor"]["targets"]:
        name = target["target_id"]
        if name not in ("runtime", "runtime-db", "linux-pool"):
            continue
        target.update(enabled=True, auth_token_env="RUNTIME_MONITOR_TOKEN")
        target.pop("unconfigured_reason", None)
        if name == "runtime-db":
            target.update(kind="runtime_operation", url="http://runtime:8080/internal/v1/operations/" + connection["operation_id"],
                expected_fields={"operation_id": connection["operation_id"]},
                evidence_scope="로컬 PostgreSQL store의 작업 기록을 Runtime API로 조회 · Runtime 중단 시 DB 단독 상태는 판별하지 못함")
        else:
            target.update(kind="runtime_node" if name == "linux-pool" else "runtime_status",
                url="http://runtime:8080/internal/v1/instances/" + INSTANCE + "/runtime-status",
                expected_fields={"instance_id": INSTANCE, "target_id": TARGET},
                evidence_scope=("로컬 K3s 단일 노드의 Ready·pressure를 Runtime을 통해 조회" if name == "linux-pool" else
                    "로컬 Nginx 연결 시험 인스턴스의 실제 Pod·endpoint 상태 · 대회 문제와 Scheduler 생성 경로는 별도"))
    recovery.save(STATE / "monitor.json", config)
    values = dict(line.split("=", 1) for line in (LOCAL / ".assets/services.env").read_text().splitlines() if "=" in line)
    (STATE / "monitor.env").write_text("RUNTIME_MONITOR_TOKEN=" + values["SCHEDULER_RUNTIME_TOKEN"] + "\n", encoding="utf-8")
    print(json.dumps({"execution_monitor_profile": profile, "connected_targets": 3}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("cluster", "load-image", "connect", "monitor", "stop-cluster"))
    parser.add_argument("--profile", choices=("local", "local-recovery"), default="local-recovery")
    args = parser.parse_args()
    if args.action == "cluster": cluster()
    elif args.action == "load-image": load_image()
    elif args.action == "connect": connect()
    elif args.action == "monitor": monitor(args.profile)
    else:
        node_record()
        k3d("cluster", "stop", CLUSTER)
