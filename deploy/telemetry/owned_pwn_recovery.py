"""Opt-in, expiring PWN repair: reconcile one Service port or recreate one owned Pod.

The reporter retains its existing wire contract. Successful submission is required
before repair, and only fresh application successes establish functional recovery.
No discovery, node repair, Deployment edits, shell commands or retry of a consumed
mutation is supported. A protected lease pins the actual Kubernetes UIDs and spec.
"""
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import sqlite3
import subprocess
import time
import uuid

import owned_challenge_reporter as reporter

PORTS = {"pwn-random6": 6666, "pwn-note": 9999, "pwn-ghost-file": 31337, "pwn-price-tag": 9999}
SUCCESS = {"pwn-random6": "VM_NONCE_ARITHMETIC_CONFIRMED", "pwn-note": "NAME_ROUNDTRIP_AND_MESSAGE_CONFIRMED",
           "pwn-ghost-file": "OWNED_NOTE_CREATE_EDIT_READ_DELETE_CONFIRMED", "pwn-price-tag": "VALID_PRODUCT_UPDATE_AND_ACCESS_RULE_CONFIRMED"}
DOWN = {"FUNCTION_UNAVAILABLE", "FUNCTION_RESPONSE_MISMATCH", "FUNCTION_TIMEOUT", "FUNCTION_UNREACHABLE",
        "CONNECTION_CLOSED_BEFORE_RESPONSE"}
REGISTRY = Path("/etc/secure-provisioner/registry.json")
KUBECTL = "/usr/local/bin/kubectl"


def now():
    return datetime.now(timezone.utc)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def require(condition):
    if not condition:
        raise ValueError("PWN recovery precondition rejected")


def name(value):
    require(isinstance(value, str) and re.fullmatch(r"[a-z0-9][a-z0-9.-]{0,252}", value))


def observe_owned(config, read=reporter.control, probe=reporter.probes.check, clock=now):
    # A verified DEGRADED workload may be probed to establish a real failure.
    # It can never contribute a healthy/recovery reading until Runtime is READY.
    phases = []
    def capture(base, path, token):
        value = read(base, path, token)
        if base == config["runtime_origin"]:
            phases.append(value.get("phase"))
        return value
    result = reporter.observe(config, capture, probe, clock,
        binding_check=lambda c, r, at: reporter.bound(c, r, at, allow_degraded=True))
    if result["status"] == "UP" and any(phase != "READY" for phase in phases):
        result.update(status="CHECKER_ERROR", reason_code="RUNTIME_NOT_READY")
    return result


def validate(config):
    require(set(config) == {"reporter", "workload"})
    r, w = config["reporter"], config["workload"]
    profile = r["binding"]["profile"]
    require(profile in PORTS)
    reporter.validate(r)
    require(set(w) == {"namespace_uid", "deployment_name", "deployment_uid", "deployment_spec_sha256",
                       "service_name", "service_uid", "service_spec", "images"})
    for key in ("namespace_uid", "deployment_uid", "service_uid"):
        require(str(uuid.UUID(w[key])) == w[key] and uuid.UUID(w[key]).int != 0)
    for key in ("deployment_name", "service_name"):
        name(w[key])
    require(re.fullmatch(r"[0-9a-f]{64}", w["deployment_spec_sha256"]))
    require(isinstance(w["images"], dict) and len(w["images"]) == 1)
    for container, image in w["images"].items():
        name(container)
        require(re.fullmatch(r"ghcr\.io/msg-ctf/challenges/" + profile + r"/[a-z0-9-]+@sha256:[0-9a-f]{64}", image))
    spec = w["service_spec"]
    require(spec["type"] == "NodePort" and len(spec["ports"]) == 1 and spec.get("selector"))
    port = spec["ports"][0]
    require(port.get("protocol", "TCP") == "TCP" and port["port"] == PORTS[profile]
            and port["targetPort"] == PORTS[profile]
            and port["nodePort"] == reporter.probes.endpoint(profile, r["service_url"], r["expected_host"])[1])
    require(len(json.dumps(config)) < 32768)


class Journal:
    """Commit the single execution claim before any mutation, including after a crash."""
    def __init__(self, path, epoch=None):
        self.epoch = epoch or str(uuid.uuid4())
        self.db = sqlite3.connect(str(path), timeout=5, isolation_level=None)
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("CREATE TABLE IF NOT EXISTS repairs (instance TEXT PRIMARY KEY, binding TEXT NOT NULL, state TEXT NOT NULL)")

    def close(self):
        self.db.close()

    def transact(self, config, update):
        key = config["reporter"]["binding"]["instance_id"]
        fingerprint = digest(config)
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row = self.db.execute("SELECT binding,state FROM repairs WHERE instance=?", (key,)).fetchone()
            require(row is None or row[0] == fingerprint)
            state = json.loads(row[1]) if row else {"attempts": 0, "status": "OBSERVING"}
            if state.get("epoch") != self.epoch:
                state.update(epoch=self.epoch, successes=0, failures=0, armed=False, last_at=None)
            result = update(state)
            self.db.execute("INSERT OR REPLACE INTO repairs VALUES (?,?,?)", (key, fingerprint, json.dumps(state)))
            self.db.execute("COMMIT")
            return result
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def observe(self, config, observation, at):
        b = config["reporter"]["binding"]
        require(all(observation.get(k) == v for k, v in b.items() if k != "expires_at"))
        require(observation.get("scope") == "owned_deployment_test")
        measured = reporter.timestamp(observation["checked_at"])
        require(-5 <= (at - measured).total_seconds() <= 30)
        def update(s):
            previous = reporter.timestamp(s["last_at"]) if s.get("last_at") else None
            if previous and measured <= previous:
                return False
            if previous and measured - previous > timedelta(seconds=45):
                s.update(successes=0, failures=0, armed=False)
            s["last_at"] = measured.isoformat()
            s.update(last_status=observation["status"], last_reason_code=observation["reason_code"])
            if s["attempts"] and s["status"] != "SUCCEEDED" and at > reporter.timestamp(s["claimed_at"]) + timedelta(seconds=180):
                s["status"] = "FAILED"
            fresh_lease = reporter.timestamp(b["expires_at"]) - at > timedelta(seconds=60)
            status, reason = observation["status"], observation["reason_code"]
            if not fresh_lease or status == "CHECKER_ERROR":
                s.update(successes=0, failures=0, armed=False)
                return False
            if status == "UP" and reason == SUCCESS[b["profile"]]:
                s.update(successes=min(s.get("successes", 0) + 1, 2), failures=0)
                if s["attempts"]:
                    if (s["status"] in {"CLAIMED", "VERIFYING", "INDETERMINATE"}
                            and at <= reporter.timestamp(s["claimed_at"]) + timedelta(seconds=180)
                            and measured > reporter.timestamp(s["claimed_at"]) and s["successes"] >= 2):
                        s.update(status="SUCCEEDED", verified_at=measured.isoformat())
                elif s["successes"] >= 2:
                    s["armed"] = True
            elif status == "DOWN" and reason in DOWN:
                s.update(successes=0, failures=min(s.get("failures", 0) + 1, 2))
            else:
                s.update(successes=0, failures=0, armed=False)
            if s["attempts"] and s["status"] != "SUCCEEDED" and at > reporter.timestamp(s["claimed_at"]) + timedelta(seconds=180):
                s["status"] = "FAILED"
            return not s["attempts"] and s["armed"] and s["failures"] >= 2
        return self.transact(config, update)

    def claim(self, config, at, action):
        require(action in {"RESTORE_SERVICE_PORT", "RECREATE_OWNED_POD"})
        def update(s):
            for (raw,) in self.db.execute("SELECT state FROM repairs WHERE instance<>?", (config["reporter"]["binding"]["instance_id"],)):
                other = json.loads(raw)
                if (other["status"] in {"CLAIMED", "VERIFYING", "INDETERMINATE"}
                        and at <= reporter.timestamp(other["claimed_at"]) + timedelta(seconds=180)):
                    return False
            if (s["attempts"] or not s.get("armed") or s.get("failures", 0) < 2
                    or not s.get("last_at") or at - reporter.timestamp(s["last_at"]) > timedelta(seconds=30)
                    or reporter.timestamp(config["reporter"]["binding"]["expires_at"]) - at <= timedelta(seconds=60)):
                return False
            s.update(attempts=1, armed=False, status="CLAIMED", claimed_at=at.isoformat(), action=action)
            return True
        return self.transact(config, update)

    def result(self, config, state):
        def update(s):
            require(s["attempts"] == 1 and state in {"VERIFYING", "INDETERMINATE"})
            s["status"] = state
        self.transact(config, update)

    def snapshot(self, config):
        return self.transact(config, lambda s: dict(s))

    def disarm(self, config, at=None):
        def update(s):
            s.update(successes=0, failures=0, armed=False)
            if (at is not None and s["attempts"] and s["status"] != "SUCCEEDED"
                    and at > reporter.timestamp(s["claimed_at"]) + timedelta(seconds=180)):
                s["status"] = "FAILED"
        self.transact(config, update)


class Kubernetes:
    def __init__(self, config, registry=None, run=None):
        self.config = config
        r = config["reporter"]
        self.binding, self.workload = r["binding"], config["workload"]
        self.namespace = "ctf-" + uuid.UUID(self.binding["instance_id"]).hex
        registry = registry if registry is not None else json.loads(REGISTRY.read_text())
        matches = [c for c in registry["clusters"] if c["target_id"] == self.binding["runtime_target_id"] and c["enabled"]]
        require(len(matches) == 1)
        path = PurePosixPath(matches[0]["kubeconfig_path"])
        require(path.parent == PurePosixPath("/etc/secure-provisioner/kubeconfigs") and path.suffix in {".yaml", ".yml", ".json"})
        self.prefix = [KUBECTL, "--cache-dir=/run/sentinel-pwn-recovery/cache", "--kubeconfig=" + str(path), "--request-timeout=6s"]
        self.run = run or self.command

    def command(self, args, body=None):
        result = subprocess.run(self.prefix + args, input=json.dumps(body) if body is not None else None,
                                capture_output=True, text=True, timeout=10)
        # Do not surface API bodies, workload environments or kubectl diagnostics.
        require(result.returncode == 0 and len(result.stdout) <= 2097152)
        return json.loads(result.stdout)

    def get(self, kind, resource=None, *, global_resource=False):
        return self.run(["get", kind] + ([resource] if resource else [])
                        + ([] if global_resource else ["-n", self.namespace]) + ["-o", "json"])

    def owned(self, item, uid=None):
        meta = item["metadata"]
        require(not meta.get("deletionTimestamp") and (uid is None or meta["uid"] == uid))
        labels = meta.get("labels", {})
        require(labels.get("msgctf.io/instance-id") == self.binding["instance_id"]
                and labels.get("msgctf.io/team-id") == self.binding["owner_team_id"]
                and labels.get("app.kubernetes.io/managed-by") == "secure-provisioner")
        if meta.get("namespace") is not None:
            require(meta["namespace"] == self.namespace)

    def plan(self):
        w = self.workload
        ns = self.get("namespace", self.namespace, global_resource=True); self.owned(ns, w["namespace_uid"])
        deployment = self.get("deployment", w["deployment_name"]); self.owned(deployment, w["deployment_uid"])
        require(digest(deployment["spec"]) == w["deployment_spec_sha256"] and deployment["spec"]["replicas"] == 1)
        require({c["name"]: c["image"] for c in deployment["spec"]["template"]["spec"]["containers"]} == w["images"])
        service = self.get("service", w["service_name"]); self.owned(service, w["service_uid"])
        current = json.loads(json.dumps(service["spec"]))
        require(len(current["ports"]) == 1 and type(current["ports"][0].get("targetPort")) is int)
        current["ports"][0]["targetPort"] = w["service_spec"]["ports"][0]["targetPort"]
        require(current == w["service_spec"])
        pods = self.get("pods")["items"]
        require(len(pods) == 1)
        pod = pods[0]; self.owned(pod)
        name(pod["metadata"]["name"])
        require({c["name"]: c["image"] for c in pod["spec"]["containers"]} == w["images"])
        owners = pod["metadata"].get("ownerReferences", [])
        require(len(owners) == 1 and owners[0].get("controller") is True and owners[0]["kind"] == "ReplicaSet")
        name(owners[0]["name"])
        replica = self.get("replicaset", owners[0]["name"]); self.owned(replica, owners[0]["uid"])
        require(any(o.get("controller") is True and o["kind"] == "Deployment" and o["uid"] == w["deployment_uid"]
                    for o in replica["metadata"].get("ownerReferences", [])))
        name(pod["spec"]["nodeName"])
        node = self.get("node", pod["spec"]["nodeName"], global_resource=True)
        conditions = {c["type"]: c["status"] for c in node["status"]["conditions"]}
        require(conditions.get("Ready") == "True" and all(conditions.get(k) == "False"
                for k in ("MemoryPressure", "DiskPressure", "PIDPressure")))
        action = "RESTORE_SERVICE_PORT" if service["spec"] != w["service_spec"] else "RECREATE_OWNED_POD"
        return {"action": action, "service": service, "pod": pod}

    def execute(self, plan, guard):
        require(guard() is None)
        w = self.workload
        self.owned(self.get("namespace", self.namespace, global_resource=True), w["namespace_uid"])
        if plan["action"] == "RESTORE_SERVICE_PORT":
            service = self.get("service", w["service_name"])
            self.owned(service, w["service_uid"])
            require(service["spec"] == plan["service"]["spec"])
            patch = [{"op": "test", "path": "/metadata/uid", "value": w["service_uid"]},
                     {"op": "test", "path": "/metadata/resourceVersion", "value": service["metadata"]["resourceVersion"]},
                     {"op": "test", "path": "/spec", "value": service["spec"]},
                     {"op": "replace", "path": "/spec/ports/0/targetPort", "value": w["service_spec"]["ports"][0]["targetPort"]}]
            self.run(["patch", "service", w["service_name"], "-n", self.namespace, "--type=json", "-p", json.dumps(patch), "-o", "json"])
        else:
            require(plan["action"] == "RECREATE_OWNED_POD")
            fresh = self.get("pod", plan["pod"]["metadata"]["name"])
            self.owned(fresh, plan["pod"]["metadata"]["uid"])
            require(fresh["spec"] == plan["pod"]["spec"]
                    and fresh["metadata"].get("ownerReferences") == plan["pod"]["metadata"].get("ownerReferences"))
            pod = fresh["metadata"]
            options = {"apiVersion": "v1", "kind": "DeleteOptions", "gracePeriodSeconds": 10,
                       "preconditions": {"uid": pod["uid"], "resourceVersion": pod["resourceVersion"]}}
            self.run(["delete", "--raw", "/api/v1/namespaces/" + self.namespace + "/pods/" + pod["name"], "-f", "-"], options)


def _tick(config, journal, *, observe=observe_owned, publish=reporter.control, adapter=Kubernetes, clock=now, read=reporter.control):
    validate(config)
    r = config["reporter"]
    observation = observe(r)
    require(publish(r["sentinel_origin"], "/ingest/challenge/" + r["target_id"], r["reporter_token_env"], body=observation) == {"accepted": True})
    eligible = journal.observe(config, observation, clock())
    if eligible:
        kube = adapter(config)
        plan = kube.plan()
        # A fresh end-to-end failure is confirmed after the Kubernetes inspection.
        confirmation = observe(r)
        require(publish(r["sentinel_origin"], "/ingest/challenge/" + r["target_id"], r["reporter_token_env"], body=confirmation) == {"accepted": True})
        confirmed = journal.observe(config, confirmation, clock())
        if confirmed and journal.claim(config, clock(), plan["action"]):
            try:
                kube.execute(plan, lambda: reporter.bound(r, read, clock(), allow_degraded=True))
                journal.result(config, "VERIFYING")
            except Exception:
                # A timeout can occur after mutation: consume the attempt and never retry it.
                journal.result(config, "INDETERMINATE")
    state = journal.snapshot(config)
    return {"target_id": r["target_id"], "instance_id": r["binding"]["instance_id"],
            "checked_at": observation["checked_at"],
            "functional_status": observation["status"], "reason_code": observation["reason_code"],
            "recovery_status": state["status"], "attempts": state["attempts"], "action": state.get("action")}


def tick(config, journal, **dependencies):
    validate(config)
    try:
        return _tick(config, journal, **dependencies)
    except Exception:
        # Undelivered evidence, failed inspections and storage errors cannot leave
        # an old healthy baseline armed for a later mutation.
        journal.disarm(config, dependencies.get("clock", now)())
        raise


def main():
    import fcntl
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", required=True); parser.add_argument("--state", required=True)
    args = parser.parse_args()
    os.umask(0o077)
    lock = open(args.state + ".lock", "a")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    journal = Journal(args.state)
    while True:
        try:
            paths = sorted(Path(args.directory).glob("*.json"))
            require(len(paths) <= 8)
            configs = []
            for path in paths:
                require(not path.is_symlink() and path.stat().st_size < 32768)
                config = json.loads(path.read_text()); validate(config); configs.append(config)
            require(len({c["reporter"]["binding"]["instance_id"] for c in configs}) == len(configs))
            require(len({c["reporter"]["target_id"] for c in configs}) == len(configs))
            for config in configs:
                try:
                    print(json.dumps(tick(config, journal)), flush=True)
                except Exception:
                    print(json.dumps({"target_id": config["reporter"]["target_id"], "recovery_status": "BLOCKED"}), flush=True)
        except Exception:
            print(json.dumps({"recovery_status": "CONFIGURATION_REJECTED"}), flush=True)
        time.sleep(15)


if __name__ == "__main__":
    main()
