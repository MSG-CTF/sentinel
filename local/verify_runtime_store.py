"""Snapshot the real local Runtime store and restore into an isolated disposable lab.

No source restart, volume switch, real kubeconfig, or live-cluster access is used
Only terminal operation history is accepted for this bounded drill
"""
import json
import secrets
import subprocess
import time

import data_recovery as r
import execution_lab as execution

SERVICE = "runtime-db"
SPEC = {"kind": "postgres", "db": "runtime", "volume": "runtime_postgres", "asset_id": "runtime-db"}


def source():
    records = r.project_records(r.PROJECT)
    db, app = records[SERVICE], records["runtime"]
    r.require(db["State"]["Running"] and app["State"]["Running"], "RUNTIME_SOURCE_NOT_RUNNING")
    r.require(r.env(app).get("PROVISIONER_STORE_MODE") == "postgres", "RUNTIME_SOURCE_NOT_POSTGRES")
    r.require(app["Config"]["Labels"].get("io.msgctf.sentinel.recovery") == "local-runtime", "RUNTIME_SOURCE_SCOPE_MISMATCH")
    r.require(r.env(db).get("POSTGRES_DB") == "runtime" and r.env(db).get("POSTGRES_USER") == "runtime", "RUNTIME_DATABASE_MISMATCH")
    volumes = [m for m in db["Mounts"] if m["Destination"] == "/var/lib/postgresql/data"]
    r.require(len(volumes) == 1 and volumes[0]["Type"] == "volume", "RUNTIME_VOLUME_MISMATCH")
    volume = r.objects(["volume", "inspect", volumes[0]["Name"]])[0]
    labels = volume.get("Labels") or {}
    r.require(labels.get("com.docker.compose.project") == r.PROJECT
              and labels.get("com.docker.compose.volume") == SPEC["volume"], "RUNTIME_VOLUME_SCOPE_MISMATCH")
    return records


class StoreLab(r.Lab):
    def __init__(self, records):
        self.run_id = r.new_id()
        self.project = "msg-sla-runtime-restore-" + self.run_id.lower()
        self.folder = r.LABS / self.run_id
        self.folder.mkdir(parents=True, exist_ok=False)
        self.config_path = self.folder / "compose.json"
        self.records, self.created = records, False
        self.token, password = secrets.token_urlsafe(48), secrets.token_hex(24)
        self.config = {"name": self.project, "services": {}, "volumes": {}, "networks": {
            "isolated": {"internal": True, "labels": {r.LABEL: self.run_id},
                         "ipam": {"config": [{"subnet": r.choose_subnet()}]}}}}
        self.add_data(SERVICE, SPEC, records[SERVICE])
        self.config["services"][SERVICE]["environment"]["POSTGRES_PASSWORD"] = password
        # The application requires one registry entry. This disabled local stub has
        # no credentials and cannot reach any actual cluster
        r.save(self.folder / "registry.json", {"clusters": [{"target_id": "restore-inspection-disabled",
            "provider": "AWS", "region": "isolated-test", "architecture": "amd64", "enabled": False,
            "kubeconfig_path": "/restore/kubeconfig.json", "public_gateway": "http://127.0.0.1",
            "exposure_mode": "NODE_PORT"}]})
        r.save(self.folder / "kubeconfig.json", {"apiVersion": "v1", "kind": "Config", "current-context": "disabled",
            "clusters": [{"name": "disabled", "cluster": {"server": "https://127.0.0.1:1"}}],
            "contexts": [{"name": "disabled", "context": {"cluster": "disabled", "user": "empty"}}],
            "users": [{"name": "empty", "user": {}}]})
        app = self.base(records["runtime"]["Image"], 256)
        app.update(read_only=True, cap_drop=["ALL"], volumes=[
            str(self.folder / "registry.json") + ":/restore/registry.json:ro",
            str(self.folder / "kubeconfig.json") + ":/restore/kubeconfig.json:ro"],
            environment={"PROVISIONER_ADDR": "0.0.0.0:8080", "PROVISIONER_CLUSTER_REGISTRY": "/restore/registry.json",
            "PROVISIONER_SERVICE_TOKEN": self.token, "PROVISIONER_STORE_MODE": "postgres",
            "PROVISIONER_DATABASE_URL": "postgresql://runtime:" + password + "@runtime-db:5432/runtime?sslmode=disable",
            "PROVISIONER_WORKERS": "1"})
        self.config["services"]["runtime-copy"] = app
        probe = self.base(records["broker"]["Image"], 128)
        probe.update(read_only=True, cap_drop=["ALL"], entrypoint=["python", "-c", "import time;time.sleep(600)"])
        self.config["services"]["probe"] = probe
        r.save(self.config_path, self.config)

    def validate_record(self, record):
        labels = record["Config"]["Labels"]
        r.require(labels.get(r.LABEL) == self.run_id and labels.get("com.docker.compose.project") == self.project,
                  "DESTINATION_SCOPE_MISMATCH")
        service = labels.get("com.docker.compose.service")
        r.require(service in self.config["services"] and record["Image"] == self.config["services"][service]["image"],
                  "DESTINATION_IMAGE_MISMATCH")
        r.require(not record["HostConfig"].get("PortBindings") and not record["HostConfig"].get("Privileged"), "DESTINATION_EXPOSED")
        r.require(set(record["NetworkSettings"]["Networks"]) == {self.project + "_isolated"}, "DESTINATION_NETWORK_MISMATCH")
        for mount in record["Mounts"]:
            if mount["Type"] == "volume":
                r.require(service == SERVICE and mount["Name"] == self.project + "_runtime_db", "DESTINATION_VOLUME_MISMATCH")
            else:
                r.require(service == "runtime-copy" and mount["Type"] == "bind" and not mount["RW"]
                          and mount["Destination"] in ("/restore/registry.json", "/restore/kubeconfig.json"), "DESTINATION_MOUNT_MISMATCH")
        return record

    def create(self):
        r.require(not r.project_records(self.project), "DESTINATION_ALREADY_EXISTS")
        r.require(not r.run(["volume", "ls", "-q", "--filter", "name=^" + self.project + "_"]).strip(), "DESTINATION_VOLUME_ALREADY_EXISTS")
        r.require(not r.run(["network", "ls", "-q", "--filter", "name=^" + self.project + "_isolated$"]).strip(), "DESTINATION_NETWORK_ALREADY_EXISTS")
        self.created = True
        self.compose("create")
        for service in self.config["services"]: self.record(service)
        network = r.objects(["network", "inspect", self.project + "_isolated"])[0]
        r.require(network["Internal"] and network["Labels"].get(r.LABEL) == self.run_id, "DESTINATION_NOT_ISOLATED")

    def query_operations(self, expected):
        script = """import json,os,sys,urllib.request
expected=json.load(sys.stdin)
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
for op in expected:
 request=urllib.request.Request('http://runtime-copy:8080/internal/v1/operations/'+op['operation_id'],headers={'Authorization':'Bearer '+os.environ['RESTORE_PROBE_TOKEN']})
 with opener.open(request,timeout=4) as response:
  assert response.status==200 and json.loads(response.read(262144))==op
print(json.dumps({'operation_responses_match':True,'operation_count':len(expected)}))
"""
        return json.loads(r.run(["exec", "-i", "-e", "RESTORE_PROBE_TOKEN=" + self.token, self.record("probe")["Id"],
                                 "python", "-c", script], input=json.dumps(expected).encode()))


def verify_dump(path, digest):
    r.require(path.is_file() and not path.is_symlink() and r.digest(path) == digest, "RUNTIME_BACKUP_CHECKSUM_MISMATCH")
    with path.open("rb") as stream: r.require(stream.read(5) == b"PGDMP", "RUNTIME_BACKUP_FORMAT_MISMATCH")


def main():
    started = time.monotonic()
    records = source()
    lab = StoreLab(records)
    report = {"schema_version": 1, "run_id": lab.run_id, "started_at": r.now(), "passed": False,
        "scope": "isolated_local_runtime_store_restore", "source_unchanged": False, "lab_removed": False,
        "automatic_restore_enabled": False, "live_cluster_reconnected": False,
        "source_images": {name: records[name]["Image"] for name in (SERVICE, "runtime")}, "checks": []}
    path = r.LOCAL / "artifacts" / ("runtime-store-restore-" + lab.run_id + ".json")
    def check(name, condition, **evidence):
        report["checks"].append({"name": name, "passed": bool(condition), **evidence})
        r.save(path, report)
        r.require(condition, "RUNTIME_STORE_CHECK_FAILED_" + name.upper())
        print(json.dumps({"check": name, "passed": True}), flush=True)
    try:
        with r.pg_session(records[SERVICE]["Id"], "runtime") as query:
            check("no_inflight_operations_at_snapshot", query("SELECT count(*) FROM runtime_operations WHERE status IN ('QUEUED','RUNNING','RETRYING')") == "0")
            snapshot = query("SELECT pg_export_snapshot()")
            tables, sequences = r.pg_fingerprint(query), r.pg_sequences(query)
            operation_ids = json.loads(query("SELECT coalesce(json_agg(operation_id ORDER BY operation_id),'[]') FROM runtime_operations"))
            r.require(0 < len(operation_ids) <= 100, "RUNTIME_OPERATION_SAMPLE_OUT_OF_BOUNDS")
            dump = lab.folder / "runtime.dump"
            r.write_dump(records[SERVICE]["Id"], SPEC, dump, snapshot=snapshot)
        expected = [execution.runtime("operations/" + operation_id) for operation_id in operation_ids]
        checksum = r.digest(dump)
        r.save(lab.folder / "manifest.json", {"sha256": checksum, "tables": tables, "sequences": sequences, "expected_operations": expected})
        try:
            verify_dump(dump, "0" * 64)
            corruption_rejected = False
        except r.RecoveryError as error:
            corruption_rejected = str(error) == "RUNTIME_BACKUP_CHECKSUM_MISMATCH"
        check("checksum_mismatch_rejected_before_restore", corruption_rejected and not lab.created)
        verify_dump(dump, checksum)
        lab.create()
        check("restore_destination_isolated", True)
        lab.compose("start", SERVICE)
        db = lab.record(SERVICE)
        r.wait_for(lambda: r.run(["exec", db["Id"], "pg_isready", "-U", "runtime", "-d", "runtime"]))
        with r.pg_session(db["Id"], "runtime") as query:
            r.require(r.pg_fingerprint(query) == [], "RESTORE_DATABASE_NOT_EMPTY")
        with dump.open("rb") as stream:
            result = subprocess.run(r.DOCKER + ["exec", "-i", db["Id"], "pg_restore", "--exit-on-error", "--single-transaction",
                "--no-owner", "--no-acl", "-U", "runtime", "-d", "runtime"], stdin=stream,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
        r.require(result.returncode == 0, "RUNTIME_PG_RESTORE_FAILED")
        def same_rows():
            with r.pg_session(db["Id"], "runtime") as query:
                return r.pg_fingerprint(query) == tables and r.pg_sequences(query) == sequences
        check("restored_rows_and_sequences_match", same_rows(), tables=len(tables), rows=sum(t["row_count"] for t in tables))
        lab.compose("start", "runtime-copy", "probe")
        response = r.wait_for(lambda: lab.query_operations(expected), timeout=60)
        check("real_runtime_reads_restored_operations", response["operation_responses_match"], operations=response["operation_count"])
        check("runtime_start_keeps_restored_rows", same_rows())
        lab.compose("stop", "runtime-copy")
        lab.compose("restart", SERVICE)
        r.wait_for(lambda: r.run(["exec", db["Id"], "pg_isready", "-U", "runtime", "-d", "runtime"]))
        check("restored_database_restart_preserves_rows", same_rows())
        lab.compose("start", "runtime-copy")
        response = r.wait_for(lambda: lab.query_operations(expected), timeout=60)
        check("runtime_restart_keeps_operation_responses", response["operation_responses_match"])
        after = source()
        unchanged = all(after[name]["Id"] == records[name]["Id"] and after[name]["State"]["StartedAt"] == records[name]["State"]["StartedAt"]
                        and after[name]["Mounts"] == records[name]["Mounts"] for name in (SERVICE, "runtime"))
        with r.pg_session(records[SERVICE]["Id"], "runtime") as query:
            unchanged = unchanged and r.pg_fingerprint(query) == tables and r.pg_sequences(query) == sequences
        check("source_containers_volumes_and_rows_unchanged", unchanged)
        report["source_unchanged"] = unchanged
        report["passed"] = True
    except Exception as error:
        report["failure_code"] = str(error) if isinstance(error, r.RecoveryError) else type(error).__name__
        raise
    finally:
        try:
            lab.cleanup()
            report["lab_removed"] = True
        except Exception as error:
            report["passed"] = False
            report["cleanup_failure"] = str(error) if isinstance(error, r.RecoveryError) else type(error).__name__
            raise
        finally:
            report["completed_at"] = r.now()
            report["duration_seconds"] = round(time.monotonic() - started, 2)
            r.save(path, report)
    print(json.dumps({"passed": report["passed"], "checks": len(report["checks"]), "report": str(path)}), flush=True)


if __name__ == "__main__":
    main()
