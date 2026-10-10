"""Measure the fixed lab's policy enforcement before enabling Runtime creation."""
import json
import time

import data_recovery as recovery
import execution_lab as lab

NAMESPACE = "sentinel-capability-check"


def main():
    lab.load_image()
    namespaces = json.loads(lab.kube("get", "namespaces", "-o", "json"))["items"]
    existing = next((item for item in namespaces if item["metadata"]["name"] == NAMESPACE), None)
    if existing:
        recovery.require(existing["metadata"].get("labels", {}).get(lab.RUN_LABEL) == lab.CLUSTER, "CAPABILITY_NAMESPACE_FOREIGN")
    else:
        lab.kube("create", "-f", "-", body={"apiVersion": "v1", "kind": "Namespace",
            "metadata": {"name": NAMESPACE, "labels": {lab.RUN_LABEL: lab.CLUSTER}}})
    report = {"checked_at": recovery.now(), "scope": "local_k3s_only", "passed": False}
    try:
        for name in ("server", "client"):
            lab.kube("apply", "-f", "-", body={"apiVersion": "v1", "kind": "Pod",
                "metadata": {"name": name, "namespace": NAMESPACE, "labels": {"app": name}},
                "spec": {"automountServiceAccountToken": False,
                    "securityContext": {"runAsUser": 101, "runAsGroup": 101, "runAsNonRoot": True,
                        "supplementalGroupsPolicy": "Strict", "seccompProfile": {"type": "RuntimeDefault"}},
                    "containers": [{"name": "nginx", "image": lab.IMAGE, "imagePullPolicy": "Never",
                        "env": [{"name": "NGINX_WORKER_PROCESSES", "value": "1"}],
                        "securityContext": {"allowPrivilegeEscalation": False, "readOnlyRootFilesystem": True,
                            "capabilities": {"drop": ["ALL"]}},
                        "resources": {"requests": {"cpu": "50m", "memory": "32Mi"}, "limits": {"cpu": "250m", "memory": "96Mi"}},
                        "volumeMounts": [{"name": "tmp", "mountPath": "/tmp"}]}],
                    "volumes": [{"name": "tmp", "emptyDir": {"sizeLimit": "32Mi"}}]}})
        lab.kube("wait", "-n", NAMESPACE, "--for=condition=Ready", "pod", "--all", "--timeout=120s", timeout=150)
        server = json.loads(lab.kube("get", "pod", "server", "-n", NAMESPACE, "-o", "json"))
        groups = lab.kube("exec", "-n", NAMESPACE, "server", "--", "id", "-G").decode().strip().split()
        recovery.require(groups == ["101"], "STRICT_GROUPS_NOT_EFFECTIVE")
        config = json.loads(lab.kube("get", "--raw", "/api/v1/nodes/" + lab.NODE + "/proxy/configz"))
        recovery.require(config["kubeletconfig"]["podPidsLimit"] == 128, "POD_PID_LIMIT_NOT_CONFIGURED")
        uid = server["metadata"]["uid"]
        node = lab.node_record()
        directories = recovery.run(["exec", node["Id"], "find", "/sys/fs/cgroup", "-type", "d", "-name", "*pod" + uid.replace("-", "_") + "*"]).decode().splitlines()
        if not directories:
            directories = recovery.run(["exec", node["Id"], "find", "/sys/fs/cgroup", "-type", "d", "-name", "*pod" + uid + "*"]).decode().splitlines()
        recovery.require(len(directories) == 1, "POD_CGROUP_AMBIGUOUS")
        actual_limit = recovery.run(["exec", node["Id"], "cat", directories[0] + "/pids.max"]).decode().strip()
        recovery.require(actual_limit == "128", "POD_PID_LIMIT_NOT_EFFECTIVE")
        address = "http://" + server["status"]["podIP"] + ":8080/"
        baseline = lab.kube("exec", "-n", NAMESPACE, "client", "--", "wget", "-T", "3", "-qO-", address)
        recovery.require(b"Welcome to nginx" in baseline, "NETWORK_BASELINE_FAILED")
        lab.kube("apply", "-f", "-", body={"apiVersion": "networking.k8s.io/v1", "kind": "NetworkPolicy",
            "metadata": {"name": "deny-server", "namespace": NAMESPACE},
            "spec": {"podSelector": {"matchLabels": {"app": "server"}}, "policyTypes": ["Ingress"], "ingress": []}})
        time.sleep(3)
        blocked = False
        try:
            lab.kube("exec", "-n", NAMESPACE, "client", "--", "wget", "-T", "3", "-qO-", address)
        except recovery.RecoveryError:
            blocked = True
        recovery.require(blocked, "NETWORK_POLICY_NOT_ENFORCED")
        lab.kube("delete", "networkpolicy", "deny-server", "-n", NAMESPACE)
        recovery.wait_for(lambda: b"Welcome to nginx" in lab.kube("exec", "-n", NAMESPACE, "client", "--", "wget", "-T", "3", "-qO-", address), timeout=30)
        lab.kube("apply", "-f", "-", body={"apiVersion": "networking.k8s.io/v1", "kind": "NetworkPolicy",
            "metadata": {"name": "deny-client-egress", "namespace": NAMESPACE},
            "spec": {"podSelector": {"matchLabels": {"app": "client"}}, "policyTypes": ["Egress"], "egress": []}})
        time.sleep(3)
        blocked = False
        try:
            lab.kube("exec", "-n", NAMESPACE, "client", "--", "wget", "-T", "3", "-qO-", address)
        except recovery.RecoveryError:
            blocked = True
        recovery.require(blocked, "EGRESS_POLICY_NOT_ENFORCED")
        lab.kube("delete", "networkpolicy", "deny-client-egress", "-n", NAMESPACE)
        recovery.wait_for(lambda: b"Welcome to nginx" in lab.kube("exec", "-n", NAMESPACE, "client", "--", "wget", "-T", "3", "-qO-", address), timeout=30)
        path = lab.STATE / "runtime/clusters.json"
        registry = json.loads(path.read_text())
        registry["clusters"][0]["security_capabilities"].update(network_policy_enforced=True,
            supplemental_groups_policy_strict=True, pod_pid_limit_enforced=True)
        recovery.save(path, registry)
        report.update(passed=True, network_baseline_reachable=True, ingress_deny_enforced=True,
            ingress_restored=True, egress_deny_enforced=True, egress_restored=True,
            strict_groups=groups, pod_cgroup_pid_limit=128)
    finally:
        observed = json.loads(lab.kube("get", "namespace", NAMESPACE, "-o", "json"))
        recovery.require(observed["metadata"]["labels"].get(lab.RUN_LABEL) == lab.CLUSTER, "CAPABILITY_CLEANUP_SCOPE_MISMATCH")
        lab.kube("delete", "namespace", NAMESPACE, "--wait=true", "--timeout=60s")
        report["check_namespace_removed"] = True
        recovery.save(LOCAL_REPORT(), report)
    print(json.dumps(report), flush=True)


def LOCAL_REPORT():
    return recovery.LOCAL / "artifacts/execution-capabilities.json"


if __name__ == "__main__":
    main()
