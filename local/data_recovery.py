"""Local PostgreSQL/RDB backups and isolated restore drills; never restore in place.

Only the fixed Docker Desktop project is a backup source. Restore destinations are
fresh, labelled containers/volumes on an internal network without published ports.
The private manifest and dumps stay outside wwwroot and the image build context.
"""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import uuid

LOCAL = Path(__file__).resolve().parent
BACKUPS = LOCAL / ".backups"
LABS = LOCAL / ".restore-labs"
PUBLIC = LOCAL.parent / "src/Sentinel.Api/wwwroot/data-recovery-status.json"
DOCKER = ["docker", "--host", "npipe:////./pipe/dockerDesktopLinuxEngine"]
PROJECT = "msg-sla-real-local"
LABEL = "io.msgctf.sentinel.restore_run"
ID_RE = re.compile(r"[0-9]{8}T[0-9]{6}Z-[a-f0-9]{8}")
DATA = {
    "postgres-internal": {"asset_id": "internal-backend-database", "kind": "postgres", "db": "msg_internal", "volume": "internal_postgres"},
    "postgres-external": {"asset_id": "external-backend-database", "kind": "postgres", "db": "msg_external", "volume": "external_postgres"},
    "scheduler-db": {"asset_id": "scheduler-db", "kind": "postgres", "db": "scheduler", "volume": "scheduler_postgres"},
    "broker-db": {"asset_id": "broker-db", "kind": "postgres", "db": "broker", "volume": "broker_postgres"},
    "redis-internal": {"asset_id": "internal-backend-cache", "kind": "redis", "volume": "internal_redis"},
    "redis-external": {"asset_id": "external-backend-cache", "kind": "redis", "volume": "external_redis"},
}
APPS = ("backend-internal", "backend-external", "scheduler", "broker")


class RecoveryError(Exception):
    """A fixed, non-secret failure code that is safe to publish."""


class TerminalRecoveryError(RecoveryError):
    """A stopped restore container cannot become ready without a new action."""


def require(condition, code):
    if not condition:
        raise RecoveryError(code)


def now():
    return datetime.now(timezone.utc).isoformat()


def new_id():
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:8]


def run(args, *, input=None, timeout=90, output=None):
    # Never include command arguments or stderr in an exception: envs contain secrets.
    try:
        result = subprocess.run(DOCKER + args, input=input, stdout=output or subprocess.PIPE,
                                stderr=subprocess.PIPE, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        raise RecoveryError("DOCKER_COMMAND_TIMEOUT") from None
    require(result.returncode == 0, "DOCKER_COMMAND_FAILED")
    return result.stdout or b""


def objects(args):
    return json.loads(run(args))


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp-" + uuid.uuid4().hex)
    with temporary.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def env(record):
    return dict(item.split("=", 1) for item in record["Config"]["Env"] if "=" in item)


def project_records(project):
    ids = run(["ps", "-aq", "--filter", "label=com.docker.compose.project=" + project]).decode().split()
    records = objects(["inspect", *ids]) if ids else []
    result = {}
    for record in records:
        labels = record["Config"].get("Labels") or {}
        service = labels.get("com.docker.compose.service")
        require(labels.get("com.docker.compose.project") == project and service not in result, "AMBIGUOUS_PROJECT")
        require(str(labels.get("com.docker.compose.oneoff")).lower() == "false", "ONEOFF_NOT_ALLOWED")
        result[service] = record
    return result


def source_records(require_running=True):
    records = project_records(PROJECT)
    require(set(DATA).union(APPS).issubset(records), "SOURCE_SERVICES_MISSING")
    for service, spec in DATA.items():
        record = records[service]
        if require_running:
            require(record["State"]["Running"] and not record["State"].get("Paused")
                    and not record["State"].get("Restarting"), "SOURCE_NOT_RUNNING")
        destination = "/data" if spec["kind"] == "redis" else "/var/lib/postgresql/data"
        mounts = [m for m in record["Mounts"] if m["Destination"] == destination]
        require(len(mounts) == 1 and mounts[0]["Type"] == "volume"
                and mounts[0]["Name"] == PROJECT + "_" + spec["volume"], "SOURCE_VOLUME_MISMATCH")
        volume = objects(["volume", "inspect", mounts[0]["Name"]])[0]
        require(volume.get("Labels", {}).get("com.docker.compose.project") == PROJECT, "SOURCE_VOLUME_UNLABELLED")
        if spec["kind"] == "postgres":
            require(env(record).get("POSTGRES_DB") == spec["db"]
                    and env(record).get("POSTGRES_USER") == spec["db"], "SOURCE_DATABASE_MISMATCH")
        else:
            redis_password(record)
    return records


def source_identity(records):
    return {service: {"container_id": r["Id"], "started_at": r["State"]["StartedAt"],
                      "status": r["State"]["Status"], "image_id": r["Image"]}
            for service, r in records.items()}


def redis_password(record):
    command = record["Config"]["Cmd"]
    require(command.count("--requirepass") == 1, "REDIS_PASSWORD_BINDING_MISSING")
    password = command[command.index("--requirepass") + 1]
    require(bool(re.fullmatch(r"[A-Za-z0-9_-]{12,128}", password)), "REDIS_PASSWORD_FORMAT_UNSUPPORTED")
    return password


@contextmanager
def pg_session(container_id, database):
    # A single exporting transaction stays open until pg_dump and row hashes finish.
    # Server-side timeouts bound readline if SQL/locks stall.
    with tempfile.TemporaryFile() as errors:
        process = subprocess.Popen(DOCKER + ["exec", "-i", container_id, "psql", "-X", "-A", "-t", "-q",
                                            "-v", "ON_ERROR_STOP=1", "-U", database, "-d", database],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=errors, text=True,
                                   encoding="utf-8")
        def query(sql):
            require(process.poll() is None, "PG_SESSION_ENDED")
            process.stdin.write(sql + ";\n")
            process.stdin.flush()
            line = process.stdout.readline()
            require(bool(line), "PG_QUERY_FAILED")
            return line.strip()
        try:
            query("SET statement_timeout='45s'; SET lock_timeout='5s'; "
                  "SET idle_in_transaction_session_timeout='180s'; BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY; SELECT 1")
            yield query
        finally:
            try:
                process.stdin.write("ROLLBACK;\n\\q\n")
                process.stdin.flush()
                process.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                process.kill()
                process.wait(timeout=5)
            process.stdin.close()
            process.stdout.close()


def identifier(value):
    return '"' + value.replace('"', '""') + '"'


def pg_fingerprint(query):
    tables = json.loads(query("SELECT coalesce(json_agg(json_build_array(schemaname, tablename) ORDER BY schemaname, tablename), '[]') "
                              "FROM pg_tables WHERE schemaname NOT IN ('pg_catalog','information_schema') "
                              "AND schemaname NOT LIKE 'pg_toast%'") )
    result = []
    for schema, table in tables:
        qualified = identifier(schema) + "." + identifier(table)
        data = json.loads(query("SELECT json_build_object('row_count',count(*),'content_hash',"
                                "md5(coalesce(string_agg(md5(to_jsonb(t)::text),'' ORDER BY md5(to_jsonb(t)::text)),''))) "
                                "FROM " + qualified + " t"))
        result.append({"schema": schema, "table": table, **data})
    return result


def pg_sequences(query):
    sequences = json.loads(query("SELECT coalesce(json_agg(json_build_array(schemaname,sequencename) ORDER BY schemaname,sequencename),'[]') FROM pg_sequences"))
    result = []
    for schema, name in sequences:
        values = json.loads(query("SELECT json_build_object('last_value',last_value,'is_called',is_called) FROM "
                                  + identifier(schema) + "." + identifier(name)))
        result.append({"schema": schema, "sequence": name, **values})
    return result


def redis(container_id, password, *args):
    return run(["exec", "-e", "REDISCLI_AUTH=" + password, container_id, "redis-cli", "-e", "--raw", *args]).decode().strip()


def redis_info(container_id, password, section):
    return dict(line.split(":", 1) for line in redis(container_id, password, "INFO", section).splitlines() if ":" in line)


def write_dump(container_id, spec, path, password=None, snapshot=None):
    if spec["kind"] == "postgres":
        args = ["exec", container_id, "pg_dump", "-U", spec["db"], "-d", spec["db"], "--format=custom",
                "--lock-wait-timeout=5s", "--snapshot=" + snapshot]
    else:
        args = ["exec", "-e", "REDISCLI_AUTH=" + password, container_id, "redis-cli", "--rdb", "-"]
    with path.open("xb") as stream:
        run(args, output=stream, timeout=120)
        stream.flush()
        os.fsync(stream.fileno())
    require(path.stat().st_size > 20, "EMPTY_BACKUP")
    with path.open("rb") as stream:
        require(stream.read(5) == (b"PGDMP" if spec["kind"] == "postgres" else b"REDIS"), "BACKUP_HEADER_INVALID")


def backup():
    records = source_records()
    backup_id = new_id()
    folder = BACKUPS / backup_id
    folder.mkdir(parents=True, exist_ok=False)
    manifest = {"schema_version": 1, "backup_id": backup_id, "source_project": PROJECT,
                "started_at": now(), "complete": False, "consistency": "per_store_snapshot_not_cross_service_atomic",
                "application_images": {s: records[s]["Image"] for s in APPS}, "assets": []}
    save(folder / "manifest.json", manifest)
    for service, spec in DATA.items():
        record = records[service]
        filename = service + (".dump" if spec["kind"] == "postgres" else ".rdb")
        path = folder / filename
        asset = {"service": service, "asset_id": spec["asset_id"], "kind": spec["kind"], "filename": filename,
                 "image_id": record["Image"], "started_at": now(), "source_container_id": record["Id"]}
        if spec["kind"] == "postgres":
            with pg_session(record["Id"], spec["db"]) as query:
                asset["engine_version"] = query("SHOW server_version")
                snapshot = query("SELECT pg_export_snapshot()")
                sequences = pg_sequences(query)
                write_dump(record["Id"], spec, path, snapshot=snapshot)
                asset["tables"] = pg_fingerprint(query)
                asset["sequences"] = pg_sequences(query)
                require(asset["sequences"] == sequences, "SEQUENCE_CHANGED_DURING_BACKUP")
        else:
            password = redis_password(record)
            asset["engine_version"] = redis_info(record["Id"], password, "server")["redis_version"]
            write_dump(record["Id"], spec, path, password=password)
        asset.update({"completed_at": now(), "bytes": path.stat().st_size, "sha256": digest(path)})
        manifest["assets"].append(asset)
        save(folder / "manifest.json", manifest)
        print(json.dumps({"phase": "backup", "service": service, "bytes": asset["bytes"]}), flush=True)
    require(source_identity(records) == source_identity(source_records()), "SOURCE_CHANGED_DURING_BACKUP")
    manifest.update({"complete": True, "completed_at": now()})
    save(folder / "manifest.json", manifest)
    return backup_id


def validate_backup(backup_id):
    require(bool(ID_RE.fullmatch(backup_id)), "INVALID_BACKUP_ID")
    folder = BACKUPS / backup_id
    require(folder.resolve().parent == BACKUPS.resolve() and not folder.is_symlink(), "BACKUP_PATH_ESCAPE")
    manifest_path = folder / "manifest.json"
    require(not manifest_path.is_symlink() and manifest_path.resolve().parent == folder.resolve(), "BACKUP_PATH_ESCAPE")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    require(manifest.get("schema_version") == 1 and manifest.get("complete") is True
            and manifest.get("source_project") == PROJECT and manifest.get("backup_id") == backup_id, "INCOMPLETE_OR_FOREIGN_BACKUP")
    assets = manifest.get("assets", [])
    require(len(assets) == len(DATA) and {a.get("service") for a in assets} == set(DATA), "BACKUP_ASSET_SET_MISMATCH")
    require(set(manifest.get("application_images", {})) == set(APPS), "BACKUP_APPLICATION_SET_MISMATCH")
    for asset in assets:
        spec = DATA[asset["service"]]
        expected = asset["service"] + (".dump" if spec["kind"] == "postgres" else ".rdb")
        require(asset.get("filename") == expected and asset.get("asset_id") == spec["asset_id"]
                and asset.get("kind") == spec["kind"], "BACKUP_BINDING_MISMATCH")
        path = folder / expected
        require(path.resolve().parent == folder.resolve() and not path.is_symlink(), "BACKUP_PATH_ESCAPE")
        require(path.stat().st_size == asset.get("bytes") and digest(path) == asset.get("sha256"), "BACKUP_CHECKSUM_MISMATCH")
    return folder, manifest


def choose_subnet():
    ids = run(["network", "ls", "-q"]).decode().split()
    occupied = [ipaddress.ip_network(c["Subnet"]) for n in objects(["network", "inspect", *ids])
                for c in (n.get("IPAM", {}).get("Config") or []) if c.get("Subnet")]
    for octet in range(245, 255):
        candidate = ipaddress.ip_network(f"10.246.{octet}.0/24")
        if not any(candidate.overlaps(n) for n in occupied if n.version == 4):
            return str(candidate)
    raise RecoveryError("NO_FREE_RESTORE_SUBNET")


class Lab:
    def __init__(self, records, manifest):
        self.run_id = new_id()
        self.project = "msg-sla-restore-" + self.run_id.lower()
        self.folder = LABS / self.run_id
        self.folder.mkdir(parents=True, exist_ok=False)
        self.config_path = self.folder / "compose.json"
        self.records = records
        self.created = False
        self.config = {"name": self.project, "services": {}, "volumes": {}, "networks": {
            "isolated": {"internal": True, "labels": {LABEL: self.run_id}, "ipam": {"config": [{"subnet": choose_subnet()}]}}}}
        self.assets = {a["service"]: a for a in manifest["assets"]}
        for service, spec in DATA.items():
            require(records[service]["Image"] == self.assets[service]["image_id"], "SOURCE_IMAGE_CHANGED")
            self.add_data(service, spec, records[service])
        for service in APPS:
            record = records[service]
            require(record["Image"] == manifest["application_images"][service], "APPLICATION_IMAGE_CHANGED")
            item = self.base(record["Image"], 1024 if service == "scheduler" else 512)
            item["environment"] = {key: value.replace("$", "$$") for key, value in env(record).items()}
            item["read_only"] = True
            item["cap_drop"] = ["ALL"]
            if service.startswith("backend-"):
                item["volumes"] = [str(LOCAL / "backend_settings.py") + ":/local/backend_settings.py:ro"]
            if service == "scheduler":
                item["environment"].update({"SCHEDULER_CLEANUP_ENABLED": "false",
                                            "SCHEDULER_BROKER_BASE_URL": "http://unbound-broker:8000",
                                            "SCHEDULER_RUNTIME_BASE_URL": "http://unbound-runtime:8080"})
            self.config["services"][service] = item
        save(self.config_path, self.config)

    def base(self, image, memory):
        return {"image": image, "pull_policy": "never", "labels": {LABEL: self.run_id},
                "networks": ["isolated"], "mem_limit": str(memory) + "m", "cpus": 1,
                "tmpfs": ["/tmp"], "security_opt": ["no-new-privileges:true"]}

    def add_data(self, service, spec, record):
        item = self.base(record["Image"], 384 if spec["kind"] == "postgres" else 128)
        volume = service.replace("-", "_")
        self.config["volumes"][volume] = {"labels": {LABEL: self.run_id}}
        item["volumes"] = [volume + (":/data" if spec["kind"] == "redis" else ":/var/lib/postgresql/data")]
        if spec["kind"] == "postgres":
            source_env = env(record)
            item["environment"] = {key: source_env[key] for key in ("POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD")}
        else:
            item["command"] = ["redis-server", "/data/redis.conf"]
        self.config["services"][service] = item

    def compose(self, *args):
        return run(["compose", "--project-name", self.project, "-f", str(self.config_path), *args], timeout=180)

    def validate_record(self, record):
        require(record["Config"]["Labels"].get(LABEL) == self.run_id
                and record["Config"]["Labels"].get("com.docker.compose.project") == self.project, "DESTINATION_SCOPE_MISMATCH")
        require(not record["HostConfig"].get("PortBindings") and not record["HostConfig"].get("Privileged"), "DESTINATION_EXPOSED")
        require(set(record["NetworkSettings"]["Networks"]) == {self.project + "_isolated"}, "DESTINATION_NETWORK_MISMATCH")
        for mount in record["Mounts"]:
            if mount["Type"] == "volume":
                require(mount["Name"].startswith(self.project + "_"), "DESTINATION_VOLUME_MISMATCH")
            else:
                require(mount["Type"] == "bind" and mount["Destination"] == "/local/backend_settings.py"
                        and not mount["RW"], "DESTINATION_MOUNT_MISMATCH")
        return record

    def record(self, service):
        require(service in self.config["services"], "DESTINATION_SERVICE_MISMATCH")
        return self.validate_record(project_records(self.project)[service])

    def create(self):
        require(not project_records(self.project), "DESTINATION_ALREADY_EXISTS")
        existing = run(["volume", "ls", "-q"]).decode().split()
        require(not any(name.startswith(self.project + "_") for name in existing), "DESTINATION_VOLUME_ALREADY_EXISTS")
        require(not run(["network", "ls", "-q", "--filter", "name=^" + self.project + "_isolated$"]).strip(), "DESTINATION_NETWORK_ALREADY_EXISTS")
        self.created = True  # Include partially created resources in bounded cleanup.
        self.compose("create", *DATA)
        for service in DATA:
            self.record(service)
        network = objects(["network", "inspect", self.project + "_isolated"])[0]
        require(network["Internal"] and network["Labels"].get(LABEL) == self.run_id, "DESTINATION_NOT_ISOLATED")

    def prepare_redis(self, service, source_service, path):
        record = self.record(service)
        require(record["State"]["Status"] == "created", "REDIS_DESTINATION_NOT_FRESH")
        config = self.folder / (service + ".conf")
        password = redis_password(self.records[source_service])
        config.write_text("bind 0.0.0.0\nprotected-mode yes\ndir /data\ndbfilename dump.rdb\nappendonly no\n"
                          "save \"\"\nrequirepass " + password + "\n", encoding="utf-8")
        run(["cp", str(path), record["Id"] + ":/data/dump.rdb"])
        run(["cp", str(config), record["Id"] + ":/data/redis.conf"])

    def cleanup(self):
        if not self.created:
            return
        # Only this unpredictable, labelled run may be removed, never a source resource.
        records = project_records(self.project)
        for record in records.values():
            self.validate_record(record)
        volumes = run(["volume", "ls", "-q", "--filter", "label=com.docker.compose.project=" + self.project]).decode().split()
        for volume in objects(["volume", "inspect", *volumes]) if volumes else []:
            require(volume["Name"].startswith(self.project + "_") and volume.get("Labels", {}).get(LABEL) == self.run_id,
                    "CLEANUP_VOLUME_SCOPE_MISMATCH")
        self.compose("down", "--volumes", "--timeout", "10")
        require(not project_records(self.project), "CLEANUP_CONTAINERS_REMAIN")
        require(not run(["volume", "ls", "-q", "--filter", "label=com.docker.compose.project=" + self.project]).strip(), "CLEANUP_VOLUMES_REMAIN")
        require(not run(["network", "ls", "-q", "--filter", "label=" + LABEL + "=" + self.run_id]).strip(), "CLEANUP_NETWORK_REMAINS")


def wait_for(check, timeout=150):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            result = check()
            if result:
                return result
        except TerminalRecoveryError:
            raise
        except (RecoveryError, ValueError, KeyError):
            pass
        time.sleep(2)
    raise RecoveryError("READINESS_DEADLINE_EXCEEDED")


def redis_restore_check(lab, service, source_service):
    record = lab.record(service)
    password = redis_password(lab.records[source_service])
    wait_for(lambda: redis(record["Id"], password, "PING") == "PONG")
    output = run(["exec", record["Id"], "redis-check-rdb", "/data/dump.rdb"]).decode()
    match = re.search(r"(\d+) keys read", output)
    require(match is not None and "Checksum OK" in output, "RDB_INTEGRITY_UNVERIFIED")
    info = redis_info(record["Id"], password, "persistence")
    loaded = int(info["rdb_last_load_keys_loaded"])
    expired = int(info["rdb_last_load_keys_expired"])
    require(loaded + expired == int(match[1]), "RDB_KEY_ACCOUNTING_MISMATCH")
    # Load the RDB first. Enabling AOF at first boot would give an empty AOF precedence.
    require(redis(record["Id"], password, "CONFIG", "SET", "appendonly", "yes") == "OK", "AOF_ENABLE_FAILED")
    wait_for(lambda: (lambda p: p.get("aof_rewrite_in_progress") == "0" and p.get("aof_last_bgrewrite_status") == "ok"
                     and p.get("aof_rewrite_scheduled") == "0")(redis_info(record["Id"], password, "persistence")))
    # This pinned Redis 8 image's entrypoint appends --loadmodule itself. CONFIG
    # REWRITE would persist those modules and load them twice on the next boot.
    # Persist just the one setting in the minimal, lab-owned configuration.
    config = lab.folder / (service + ".conf")
    contents = config.read_text(encoding="utf-8")
    require(contents.count("appendonly no\n") == 1, "REDIS_CONFIG_UNEXPECTED")
    config.write_text(contents.replace("appendonly no\n", "appendonly yes\n"), encoding="utf-8")
    run(["cp", str(config), record["Id"] + ":/data/redis.conf"])
    return {"snapshot_keys": int(match[1]), "loaded_keys": loaded, "expired_keys": expired, "aof_enabled": True}


def redis_canary(lab, source_service):
    base = lab.record(source_service)
    password = redis_password(lab.records[source_service])
    key = "sentinel-restore-canary:" + lab.run_id
    value = uuid.uuid4().hex
    redis(base["Id"], password, "-n", "1", "SET", key, value)
    redis(base["Id"], password, "-n", "7", "SET", key, value, "EX", "3600")
    backup_time = time.time()
    path = lab.folder / (source_service + "-canary.rdb")
    write_dump(base["Id"], DATA[source_service], path, password=password)
    service = source_service + "-proof"
    lab.add_data(service, DATA[source_service], lab.records[source_service])
    save(lab.config_path, lab.config)
    lab.compose("create", service)
    lab.prepare_redis(service, source_service, path)
    lab.compose("start", service)
    redis_restore_check(lab, service, source_service)
    proof = lab.record(service)
    run(["restart", "--time", "5", proof["Id"]])
    wait_for(lambda: redis(proof["Id"], password, "PING") == "PONG")
    require(redis(proof["Id"], password, "-n", "1", "GET", key) == value, "REDIS_VALUE_NOT_RESTORED")
    require(redis(proof["Id"], password, "-n", "1", "TTL", key) == "-1", "REDIS_PERSISTENCE_NOT_RESTORED")
    require(redis(proof["Id"], password, "-n", "7", "GET", key) == value, "REDIS_OTHER_DATABASE_NOT_RESTORED")
    ttl = int(redis(proof["Id"], password, "-n", "7", "TTL", key))
    expected = 3600 - (time.time() - backup_time)
    require(0 < ttl <= 3600 and abs(ttl - expected) < 5, "REDIS_TTL_NOT_PRESERVED")
    return {"passed": True, "scope": "isolated_fixture_only", "databases": [1, 7], "ttl_seconds_remaining": ttl,
            "aof_restart_verified": True}


def application_check(lab, service):
    lab.compose("up", "-d", "--no-deps", "--pull", "never", service)
    record = lab.record(service)
    client = lab.record("backend-internal")["Id"]
    credentials = env(lab.records[service])
    if service.startswith("backend-"):
        url, token = "http://" + service + ":8080/healthz", None
    elif service == "scheduler":
        url, token = "http://scheduler:8080/api/instances/recent", credentials["SCHEDULER_API_TOKEN"]
    else:
        url, token = "http://broker:8000/v1/inventory/resource-targets", credentials["INVENTORY_API_TOKEN"]
    # The request runs inside the isolated network, with no host port or proxy.
    code = """import json,sys,urllib.request
request=json.load(sys.stdin)
client=urllib.request.build_opener(urllib.request.ProxyHandler({}))
headers={'Authorization':'Bearer '+request['token']} if request['token'] else {}
with client.open(urllib.request.Request(request['url'],headers=headers),timeout=4) as response:
    print(json.dumps(json.load(response)))
"""
    def query():
        if not objects(["inspect", record["Id"]])[0]["State"]["Running"]:
            raise TerminalRecoveryError("RESTORED_APPLICATION_EXITED")
        response = json.loads(run(["exec", "-i", client, "python", "-c", code],
                                  input=json.dumps({"url": url, "token": token}).encode(), timeout=10))
        if service.startswith("backend-"):
            return response if response == {"status": "ok", "database": "ok", "cache": "ok"} else None
        if service == "scheduler":
            return response if response.get("code") == "SUCCESS" and isinstance(response.get("data"), list) else None
        return response if isinstance(response.get("items"), list) and isinstance(response.get("total"), int) else None
    response = wait_for(query)
    result = {"service": service, "passed": True, "scope": "healthz_database_cache" if service.startswith("backend-") else "authenticated_inventory_query"}
    if service.startswith("backend-"):
        # A model-layer read proves restored migrations and table access through Django.
        script = "import django,json; django.setup(); from django.contrib.auth import get_user_model; from django.db.migrations.recorder import MigrationRecorder; print(json.dumps({'users':get_user_model().objects.count(),'migrations':MigrationRecorder.Migration.objects.count()}))"
        result["model_rows"] = json.loads(run(["exec", record["Id"], "python", "-c", script]))
    else:
        result["returned_items"] = len(response["data"] if service == "scheduler" else response["items"])
    return result


def public_result(report):
    # Explicit projection: never expose dump names, locations, credentials, row hashes or IDs.
    return {key: report[key] for key in ("schema_version", "run_id", "backup_id", "started_at", "completed_at", "passed",
            "scope", "automatic_restore_enabled", "consistency", "assets", "applications", "source_unchanged", "lab_removed", "duration_seconds")}


def restore_data(lab, folder, *, verify_canary=True):
    """Restore fresh lab volumes, omitting fixture keys for service cutover."""
    lab.create()
    for service, spec in DATA.items():
        if spec["kind"] == "redis":
            lab.prepare_redis(service, service, folder / lab.assets[service]["filename"])
    lab.compose("start", *DATA)
    assets = []
    for service, spec in DATA.items():
        record = lab.record(service)
        asset = {"asset_id": spec["asset_id"], "service": service, "kind": spec["kind"], "passed": False}
        if spec["kind"] == "postgres":
            wait_for(lambda: run(["exec", record["Id"], "pg_isready", "-U", spec["db"], "-d", spec["db"]]))
            with pg_session(record["Id"], spec["db"]) as query:
                require(pg_fingerprint(query) == [], "POSTGRES_DESTINATION_NOT_EMPTY")
            path = folder / lab.assets[service]["filename"]
            # Binary stdin avoids Windows PowerShell's text-pipeline corruption.
            with path.open("rb") as stream:
                result = subprocess.run(DOCKER + ["exec", "-i", record["Id"], "pg_restore", "--exit-on-error", "--single-transaction",
                                                 "--no-owner", "--no-acl", "-U", spec["db"], "-d", spec["db"]],
                                        stdin=stream, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
            require(result.returncode == 0, "PG_RESTORE_FAILED")
            with pg_session(record["Id"], spec["db"]) as query:
                actual = pg_fingerprint(query)
                actual_sequences = pg_sequences(query)
            require(actual == lab.assets[service]["tables"], "POSTGRES_CONTENT_MISMATCH")
            require(actual_sequences == lab.assets[service].get("sequences"), "POSTGRES_SEQUENCE_MISMATCH")
            asset.update({"tables": len(actual), "rows": sum(t["row_count"] for t in actual), "row_hashes_match": True,
                          "sequences": len(actual_sequences), "sequences_match": True})
        else:
            asset.update(redis_restore_check(lab, service, service))
            if verify_canary:
                asset["canary"] = redis_canary(lab, service)
        asset["passed"] = True
        assets.append(asset)
        print(json.dumps({"phase": "restore_verified", "service": service}), flush=True)
    return assets


def restore(backup_id):
    folder, manifest = validate_backup(backup_id)  # No Docker mutation before full integrity validation.
    records = source_records(require_running=False)
    before = source_identity(records)
    start = time.monotonic()
    lab = Lab(records, manifest)
    report = {"schema_version": 1, "run_id": lab.run_id, "backup_id": backup_id, "started_at": now(), "passed": False,
              "scope": "isolated_local_restore_drill", "automatic_restore_enabled": False,
              "consistency": manifest["consistency"], "assets": [], "applications": [], "source_unchanged": False, "lab_removed": False}
    failure = None
    try:
        report["assets"] = restore_data(lab, folder)
        for service in APPS:
            report["applications"].append(application_check(lab, service))
            print(json.dumps({"phase": "application_verified", "service": service}), flush=True)
    except Exception as error:
        failure = error
        report["failure_code"] = str(error) if isinstance(error, RecoveryError) else type(error).__name__
    finally:
        try:
            lab.cleanup()
            report["lab_removed"] = True
        except Exception as error:
            failure = error
            report["cleanup_failure"] = str(error) if isinstance(error, RecoveryError) else type(error).__name__
        try:
            report["source_unchanged"] = before == source_identity(source_records(require_running=False))
            require(report["source_unchanged"], "SOURCE_CHANGED_DURING_RESTORE")
        except Exception as error:
            failure = error
            report["source_check_failure"] = str(error) if isinstance(error, RecoveryError) else type(error).__name__
        report.update({"completed_at": now(), "duration_seconds": round(time.monotonic() - start, 2),
                       "passed": failure is None and len(report["assets"]) == 6 and len(report["applications"]) == 4})
        report_path = LOCAL / "artifacts" / ("data-restore-" + lab.run_id + ".json")
        save(report_path, report)
        save(PUBLIC, public_result(report))
    require(report["passed"], "RESTORE_DRILL_FAILED_SEE_PRIVATE_REPORT")
    return report_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("backup", "restore", "drill"))
    parser.add_argument("--backup-id")
    args = parser.parse_args()
    require(args.action == "restore" or args.backup_id is None, "BACKUP_ID_ONLY_FOR_RESTORE")
    if args.action == "restore":
        require(args.backup_id is not None, "BACKUP_ID_REQUIRED")
        result = restore(args.backup_id)
    else:
        backup_id = backup()
        result = restore(backup_id) if args.action == "drill" else backup_id
    print(json.dumps({"passed": True, "action": args.action, "result": str(result)}))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(json.dumps({"passed": False, "reason": str(error) if isinstance(error, RecoveryError) else type(error).__name__}), file=sys.stderr)
        sys.exit(1)
