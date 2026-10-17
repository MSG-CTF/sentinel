"""Local pull executor: fixed service bindings, one daemon, durable at-most-once execution."""
import argparse
import contextlib
import datetime as dt
import json
import os
from pathlib import Path
import secrets
import sqlite3
import subprocess
import time
import urllib.error
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parent
STATE = ROOT / ".recovery"
API = "http://127.0.0.1:58240"
DOCKER_HOST = "npipe:////./pipe/dockerDesktopLinuxEngine"
PROJECT = "msg-sla-real-local"
SERVICE = "backend-internal"
ASSET = "internal-backend"
BINDINGS = {
    "internal-backend": ("INTERNAL", "backend-internal", ("postgres-internal", "redis-internal")),
    "external-backend": ("EXTERNAL", "backend-external", ("postgres-external", "redis-external")),
    "scheduler": ("CONTROL", "scheduler", ("scheduler-db",)),
    "broker": ("CONTROL", "broker", ("broker-db",)),
    "internal-frontend": ("INTERNAL", "frontend-internal", ("backend-internal",)),
    "external-frontend": ("EXTERNAL", "frontend-external", ("backend-external",)),
    "edge": ("ACCESS", "edge", ("frontend-internal", "frontend-external", "backend-internal", "backend-external")),
    "runtime": ("RUNTIME", "runtime", ("runtime-db",)),
}
EXECUTOR = "local-docker"
ACTION = "start_stopped_service"
SCOPE_LABEL = "io.msgctf.sentinel.recovery"
SCOPE = "local-internal-backend"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, "Redirect rejected", headers, fp)


def initialize():
    (STATE / "secrets").mkdir(parents=True, exist_ok=True)
    token = STATE / "secrets" / "executor.token"
    if not token.exists():
        with token.open("x", encoding="ascii") as stream:
            stream.write(secrets.token_hex(32))
    value = token.read_text(encoding="ascii").strip()
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise RuntimeError("Invalid local executor token")


def api(path, body=None):
    # Neither arbitrary URLs, redirects nor system proxies are accepted by this local adapter.
    token = (STATE / "secrets" / "executor.token").read_text(encoding="ascii").strip()
    headers = {"Authorization": "Bearer " + token, "Accept": "application/json"}
    encoded = None if body is None else json.dumps(body).encode("utf-8")
    if encoded is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(API + "/internal/recovery" + path, data=encoded, headers=headers)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    with opener.open(request, timeout=5) as response:
        if response.headers.get_content_type() != "application/json":
            raise RuntimeError("Unexpected executor response")
        data = response.read(65537)
        if len(data) > 65536:
            raise RuntimeError("Executor response limit")
        return json.loads(data)


def docker(*arguments):
    completed = subprocess.run(["docker", "--host", DOCKER_HOST, *arguments], check=True,
                               capture_output=True, text=True, timeout=25)
    return completed.stdout


def valid_request(request, now=None):
    try:
        now = now or dt.datetime.now(dt.timezone.utc)
        expires = dt.datetime.fromisoformat(request["expires_at"].replace("Z", "+00:00"))
        return (str(uuid.UUID(request["request_id"])) == request["request_id"]
                and request["asset_id"] in BINDINGS and request["track"] == BINDINGS[request["asset_id"]][0]
                and request["executor_id"] == EXECUTOR and request["action"] == ACTION
                and request["status"] == "QUEUED" and now < expires <= now + dt.timedelta(seconds=300))
    except (KeyError, ValueError, TypeError, AttributeError):
        return False


def eligible_container(container, asset_id=ASSET):
    labels = container.get("Config", {}).get("Labels", {}) or {}
    state = container.get("State", {})
    return (labels.get("com.docker.compose.project") == PROJECT
            and labels.get("com.docker.compose.service") == BINDINGS[asset_id][1]
            and labels.get("com.docker.compose.oneoff", "false").lower() == "false"
            and labels.get(SCOPE_LABEL) == "local-" + asset_id
            and state.get("Status") == "exited" and not state.get("Running")
            and not state.get("Paused") and not state.get("Restarting") and not state.get("OOMKilled")
            and isinstance(container.get("Id"), str) and len(container["Id"]) == 64
            and all(character in "0123456789abcdef" for character in container["Id"]))


def inspect_service(service, run):
    ids = run("ps", "-aq", "--filter", "label=com.docker.compose.project=" + PROJECT,
              "--filter", "label=com.docker.compose.service=" + service).split()
    if len(ids) != 1:
        return None
    containers = json.loads(run("inspect", ids[0]))
    if len(containers) != 1:
        return None
    return containers[0]


def stopped_container(run=docker, asset_id=ASSET):
    container = inspect_service(BINDINGS[asset_id][1], run)
    if container is None or not eligible_container(container, asset_id):
        return None
    for service in BINDINGS[asset_id][2]:
        dependency = inspect_service(service, run)
        if dependency is None:
            return None
        if not ready_dependency(dependency, service):
            return None
    return container["Id"]


def ready_dependency(container, service):
    labels = container.get("Config", {}).get("Labels", {}) or {}
    state = container.get("State", {})
    return (labels.get("com.docker.compose.project") == PROJECT
            and labels.get("com.docker.compose.service") == service
            and labels.get("com.docker.compose.oneoff", "false").lower() == "false"
            and state.get("Status") == "running" and state.get("Running")
            and not state.get("Paused") and not state.get("Restarting") and not state.get("OOMKilled")
            and state.get("Health", {}).get("Status", "healthy") == "healthy")


def inspect_bindings(run=docker):
    ids = run("ps", "-aq", "--filter", "label=com.docker.compose.project=" + PROJECT).split()
    records = json.loads(run("inspect", *ids)) if ids else []
    services = {}
    for item in records:
        labels = item.get("Config", {}).get("Labels", {}) or {}
        if labels.get("com.docker.compose.project") == PROJECT:
            services.setdefault(labels.get("com.docker.compose.service"), []).append(item)
    result = []
    for asset_id, (_, service, dependencies) in BINDINGS.items():
        targets = services.get(service, [])
        stopped = (len(targets) == 1 and eligible_container(targets[0], asset_id)
                   and all(len(services.get(dependency, [])) == 1
                           and ready_dependency(services[dependency][0], dependency) for dependency in dependencies))
        result.append({"asset_id": asset_id, "stopped_eligible": stopped})
    return {"executor_id": EXECUTOR, "assets": result}


def database():
    connection = sqlite3.connect(STATE / "executor.sqlite")
    connection.execute("PRAGMA synchronous=FULL")
    connection.execute("""CREATE TABLE IF NOT EXISTS executions (
        request_id TEXT PRIMARY KEY, claim_id TEXT, phase TEXT NOT NULL,
        result_code TEXT, container_id TEXT, updated_at TEXT NOT NULL)""")
    connection.commit()
    return connection


def save(connection, request_id, **fields):
    allowed = {"claim_id", "phase", "result_code", "container_id"}
    if not set(fields) <= allowed:
        raise ValueError("Unexpected journal field")
    fields["updated_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
    assignments = ", ".join(name + " = ?" for name in fields)
    connection.execute("UPDATE executions SET " + assignments + " WHERE request_id = ?",
                       [*fields.values(), request_id])
    connection.commit()


def report_pending(connection):
    for request_id, claim_id, result_code in connection.execute(
            "SELECT request_id, claim_id, result_code FROM executions WHERE phase = 'REPORT'").fetchall():
        try:
            api("/requests/" + request_id + "/result",
                {"executor_id": EXECUTOR, "claim_id": claim_id, "result_code": result_code})
            save(connection, request_id, phase="DONE")
        except urllib.error.HTTPError as error:
            if error.code == 409:
                save(connection, request_id, phase="REJECTED")
            else:
                raise


def recover_interrupted(connection):
    # A crash after dispatch cannot prove whether Docker acted. Never replay it.
    connection.execute("""UPDATE executions SET phase = 'REPORT', result_code = 'OUTCOME_UNKNOWN'
        WHERE phase IN ('CLAIMED', 'EXECUTING') AND claim_id IS NOT NULL""")
    connection.commit()


def execute(request, connection, run=docker):
    if not valid_request(request):
        return
    request_id = request["request_id"]
    try:
        connection.execute("INSERT INTO executions(request_id, phase, updated_at) VALUES (?, 'RECEIVED', ?)",
                           (request_id, dt.datetime.now(dt.timezone.utc).isoformat()))
        connection.commit()
    except sqlite3.IntegrityError:
        return
    # Losing this response leaves the server claim consumed; a retry never executes twice.
    claimed = api("/requests/" + request_id + "/claim", {"executor_id": EXECUTOR})
    claim_id = claimed["claim_id"]
    same_fields = ("request_id", "asset_id", "track", "executor_id", "action", "created_at", "expires_at")
    if (str(uuid.UUID(claim_id)) != claim_id or claimed["request"].get("status") != "CLAIMED"
            or any(claimed["request"].get(field) != request.get(field) for field in same_fields)):
        raise RuntimeError("Invalid claim response")
    save(connection, request_id, phase="CLAIMED", claim_id=claim_id)
    result = "PRECONDITION_NOT_MET"
    try:
        container_id = stopped_container(run, request["asset_id"])
        if container_id and valid_request(request):
            # Persist before the only Docker mutation. Docker start is harmless if another
            # actor has already started this exact container; it never restarts a running one.
            save(connection, request_id, phase="EXECUTING", container_id=container_id)
            run("start", container_id)
            result = "CONTAINER_STARTED"
    except subprocess.TimeoutExpired:
        result = "OUTCOME_UNKNOWN"
    except (subprocess.SubprocessError, OSError, ValueError, KeyError):
        result = "EXECUTION_FAILED"
    save(connection, request_id, phase="REPORT", result_code=result)
    report_pending(connection)


@contextlib.contextmanager
def exclusive_lock():
    if os.name != "nt":
        raise RuntimeError("This executor is restricted to local Windows Docker Desktop")
    import msvcrt
    STATE.mkdir(parents=True, exist_ok=True)
    with (STATE / "executor.lock").open("a+b") as stream:
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        try:
            yield
        finally:
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)


def stop():
    STATE.mkdir(parents=True, exist_ok=True)
    (STATE / "stop").touch()
    for _ in range(35):
        try:
            with exclusive_lock():
                return
        except OSError:
            time.sleep(1)
    raise RuntimeError("Executor is still completing a bounded operation")


def run_agent():
    initialize()
    with exclusive_lock(), database() as connection:
        if (ROOT / ".cutover/active.json").exists():
            raise RuntimeError("An unfinished local data cutover requires rollback")
        (STATE / "stop").unlink(missing_ok=True)
        recover_interrupted(connection)
        print("Local executor active: fixed application bindings / start_stopped_service", flush=True)
        while not (STATE / "stop").exists():
            try:
                report_pending(connection)
                # Only booleans for fixed assets leave the host; Docker metadata and secrets stay local.
                api("/inspections", inspect_bindings())
                request = api("/requests/next").get("request")
                if request:
                    execute(request, connection)
            except (urllib.error.URLError, TimeoutError, OSError, ValueError, RuntimeError, KeyError) as error:
                # No credentials, response bodies, command output or exception messages.
                print("Executor poll deferred: " + type(error).__name__, flush=True)
            time.sleep(2)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["init", "run", "stop"])
    command = parser.parse_args().command
    {"init": initialize, "run": run_agent, "stop": stop}[command]()
