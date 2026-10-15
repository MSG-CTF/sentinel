"""Isolated PostgreSQL -> real Scheduler -> real Sentinel HTTP pagination check.

Requires a built Scheduler JAR, a Release Sentinel build, Java 24, .NET 8 and Docker.
No production credentials, Runtime calls, workload creation or existing databases are used.
"""
import argparse
import datetime as dt
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def get(url, token=None):
    headers = {"Authorization": "Bearer " + token} if token else {}
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        with opener.open(urllib.request.Request(url, headers=headers), timeout=8) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait(check, processes, seconds=60):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if any(process.poll() is not None for process in processes):
            raise RuntimeError("A test service exited; inspect the local service logs")
        try:
            result = check()
            if result:
                return result
        except (OSError, ValueError, KeyError, IndexError):
            pass
        time.sleep(0.2)
    raise RuntimeError("Timed out waiting for a verified test state")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scheduler-jar", type=Path, required=True)
    parser.add_argument("--java", default="java")
    parser.add_argument("--dotnet", default="dotnet")
    parser.add_argument("--wsl-distro", help="Use Docker in this WSL distribution")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    folder = args.output.resolve()
    folder.mkdir(parents=True, exist_ok=True)
    jar = args.scheduler_jar.resolve()
    sentinel = root / "src/Sentinel.Api/bin/Release/net8.0/Sentinel.Api.dll"
    if not jar.is_file() or not sentinel.is_file():
        raise RuntimeError("Build the Scheduler JAR and Sentinel Release assembly first")
    docker_command = ["wsl", "-d", args.wsl_distro, "--", "docker"] if args.wsl_distro else ["docker"]

    def docker(*command, input=None):
        result = subprocess.run(docker_command + list(command), input=input, capture_output=True,
                                encoding="utf-8", errors="replace", timeout=60)
        if result.returncode:
            raise RuntimeError("Docker test operation failed: " + result.stderr[:500])
        return result.stdout.strip()

    name = "sentinel-pagination-" + uuid.uuid4().hex[:12]
    token = secrets.token_urlsafe(32)
    db_password = secrets.token_urlsafe(24)
    processes, logs = [], []
    proxy = None
    created = False
    report = {"started_at": dt.datetime.now(dt.timezone.utc).isoformat(), "passed": False,
              "scope": "real_scheduler_postgresql_and_sentinel_http_no_runtime", "checks": []}

    def check(name, condition, **details):
        if not condition:
            raise AssertionError(name)
        record = {"name": name, "passed": True, **details}
        report["checks"].append(record)
        print(json.dumps(record), flush=True)

    def start(command, env, log_name):
        log = (folder / log_name).open("w", encoding="utf-8")
        logs.append(log)
        # Popen uses pipes/files; it does not open an interactive window on Windows
        process = subprocess.Popen(command, cwd=folder, env=env, stdout=log, stderr=log)
        processes.append(process)

    def sql(statement):
        return docker("exec", "-i", name, "psql", "-U", "pagination", "-d", "pagination",
                      "-v", "ON_ERROR_STOP=1", "-At", input=statement)

    def identifiers():
        return sql("SELECT instance_id FROM challenge_instance ORDER BY created_at DESC, instance_id DESC;").splitlines()

    try:
        docker("run", "--detach", "--name", name, "--label", "sentinel.test=scheduler-pagination",
               "--publish", "127.0.0.1::5432", "--tmpfs", "/var/lib/postgresql/data",
               "--env", "POSTGRES_USER=pagination", "--env", "POSTGRES_DB=pagination",
               "--env", "POSTGRES_PASSWORD=" + db_password, "postgres:17-alpine")
        created = True
        db_port = int(docker("port", name, "5432/tcp").rsplit(":", 1)[1])
        scheduler_port = free_port()
        env = dict(os.environ, SPRING_DATASOURCE_URL=f"jdbc:postgresql://127.0.0.1:{db_port}/pagination",
                   SPRING_DATASOURCE_USERNAME="pagination", SPRING_DATASOURCE_PASSWORD=db_password,
                   SCHEDULER_API_TOKEN=token, SERVER_ADDRESS="127.0.0.1", SERVER_PORT=str(scheduler_port),
                   SPRING_PROFILES_ACTIVE="test", SCHEDULER_OPERATION_ENABLED="false", SCHEDULER_CLEANUP_ENABLED="false")
        start([args.java, "-jar", str(jar), "--spring.jpa.hibernate.ddl-auto=validate"], env, "scheduler.log")
        upstream = f"http://127.0.0.1:{scheduler_port}"
        wait(lambda: get(upstream + "/api/instances", token)[0] == 200, processes)
        check("real_scheduler_authentication", get(upstream + "/api/instances")[0] == 401
              and get(upstream + "/api/instances", "wrong")[0] == 401)
        state = {"mode": "normal", "requests": [], "page_sizes": [], "wrong_auth": 0, "non_list": 0}

        class Proxy(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_GET(self):
                path = urllib.parse.urlsplit(self.path)
                query = urllib.parse.parse_qs(path.query)
                mode = state["mode"]
                if path.path != "/api/instances":
                    state["non_list"] += 1
                if self.headers.get("Authorization") != "Bearer " + token:
                    state["wrong_auth"] += 1
                state["requests"].append({"mode": mode, "cursor": "cursor" in query, "limit": query.get("limit")})
                if "cursor" in query and mode in ("503", "401"):
                    code, body = int(mode), b'{"code":"TEST_FAILURE"}'
                else:
                    code, body = get(upstream + self.path, token)
                    if code == 200:
                        payload = json.loads(body)
                        if isinstance(payload.get("data"), dict):
                            state["page_sizes"].append(len(payload["data"]["items"]))
                            if "cursor" in query and mode == "cursor_cycle":
                                payload["data"]["next_cursor"] = query["cursor"][0]
                                body = json.dumps(payload).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                try:
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        proxy = ThreadingHTTPServer(("127.0.0.1", 0), Proxy)
        threading.Thread(target=proxy.serve_forever, daemon=True).start()
        scheduler_url = f"http://127.0.0.1:{proxy.server_port}/api/instances"
        profile = {
            "platform_monitor": {"profile": "pagination-test", "poll_interval_seconds": 5, "timeout_seconds": 2,
                "event_file": str(folder / "events.jsonl"), "targets": [{"target_id": "scheduler", "name": "Scheduler",
                "role": "SCHEDULER", "track": "CONTROL", "kind": "scheduler_inventory", "url": scheduler_url,
                "auth_token_env": "PAGINATION_SCHEDULER_TOKEN"}]},
            "platform_recovery": {"enabled": False}, "discord_alerts": {"enabled": False},
            "instance_monitor": {"enabled": True, "poll_interval_seconds": 5, "timeout_seconds": 2,
                "scheduler_url": scheduler_url + "/recent", "scheduler_token_env": "PAGINATION_SCHEDULER_TOKEN"},
        }
        profile_file = folder / "profile.json"
        profile_file.write_text(json.dumps(profile), encoding="utf-8")
        api_port = free_port()
        env = {key: value for key, value in os.environ.items()
               if not key.upper().startswith(("SENTINEL_", "PLATFORM_MONITOR__", "PLATFORM_RECOVERY__", "INSTANCE_MONITOR__", "DISCORD_ALERTS__"))}
        env.update(SENTINEL_PROFILE_FILE=str(profile_file), PAGINATION_SCHEDULER_TOKEN=token,
                   ASPNETCORE_URLS=f"http://127.0.0.1:{api_port}", Logging__LogLevel__Default="Warning",
                   Logging__LogLevel__System_Net_Http_HttpClient="Warning")
        start([args.dotnet, str(sentinel)], env, "sentinel.log")
        api = f"http://127.0.0.1:{api_port}"

        def snapshot():
            return json.loads(get(api + "/api/instances")[1])

        def target():
            return json.loads(get(api + "/api/status")[1])["targets"][0]

        def complete(expected):
            value = snapshot()
            return value if (value["status"] == "COMPLETE" and value["complete_inventory"] is True
                             and [row["instance_id"] for row in value["instances"]] == expected) else None

        empty = wait(lambda: complete([]), processes)
        check("empty_database_is_complete", empty["reason_code"] == "INVENTORY_EMPTY")
        states = ["REQUESTED", "SCHEDULING", "PROVISIONING", "RUNNING", "RESTARTING", "RESETTING",
                  "STOPPING", "STOPPED", "FAILED", "EXPIRED", "CLEANUP_PENDING", "CLEANED"]
        rows = []
        for index in range(1, 438):
            instance_id = f"{'80000000' if index % 2 else '00000000'}-0000-4000-8000-{index:012x}"
            status = "CLEANUP_PENDING" if index == 1 else states[index % len(states)]
            created_at = "2000-01-01T00:00:00.123456Z" if index == 1 else "2026-10-15T00:00:00.123456Z"
            expiry = "2000-01-02T00:00:00Z" if index == 1 else "2099-01-01T00:00:00Z"
            rows.append(f"('{instance_id}', '{instance_id}', '{instance_id}', '{instance_id}', '{status}', "
                        f"'{created_at}', '{created_at}', '{expiry}', '2099-01-02T00:00:00Z', 'WEB')")
        sql("INSERT INTO challenge_instance (instance_id,team_id,user_id,challenge_id,status,created_at,updated_at,"
            "expires_at,hard_expires_at,isolation_profile) VALUES " + ",".join(rows) + ";")
        expected = identifiers()
        full = wait(lambda: complete(expected), processes)
        probe = wait(lambda: (lambda value: value if value["components"].get("inventory_total") == "437" else None)(target()), processes)
        recent = json.loads(get(upstream + "/api/instances/recent", token)[1])["data"]
        old_id = "80000000-0000-4000-8000-000000000001"
        check("all_437_database_ids_match_in_order", len(expected) == 437
              and len({row["instance_id"] for row in full["instances"]}) == 437, missing=0, duplicates=0,
              includes_equal_microsecond_timestamps=True, includes_unsigned_uuid_boundary=True)
        check("older_cleanup_pending_survives_recent_window", len(recent) == 20
              and old_id not in {row["instance_id"] for row in recent}
              and full["instances"][-1]["instance_id"] == old_id and full["instances"][-1]["ttl_expired"])
        check("all_scheduler_states_are_preserved", {row["scheduler_status"] for row in full["instances"]} == set(states))
        check("probe_also_reads_three_pages", probe["status"] == "HEALTHY" and probe["components"]["page_count"] == "3"
              and all(size in state["page_sizes"] for size in [200, 37]))
        check("unconfigured_runtime_is_not_claimed_healthy", all(row["status"] == "UNVERIFIED"
              and row["challenge_health"] == "UNVERIFIED" for row in full["instances"]))
        (folder / "complete-snapshot.json").write_text(json.dumps(full), encoding="utf-8")

        for mode, reason in [("503", "UPSTREAM_UNAVAILABLE"), ("401", "AUTH_REQUIRED"), ("cursor_cycle", "INVALID_SCHEDULER_INVENTORY")]:
            state["mode"] = mode
            failed = wait(lambda: (lambda value: value if value["reason_code"] == reason else None)(snapshot()), processes)
            failed_probe = wait(lambda: (lambda value: value if value["status"] != "HEALTHY" else None)(target()), processes)
            check("later_page_" + mode + "_is_not_complete", failed["status"] == "UNAVAILABLE"
                  and failed["complete_inventory"] is False and failed["instances"] == [] and not failed_probe["components"])
            state["mode"] = "normal"
            wait(lambda: complete(expected), processes)
            wait(lambda: target()["status"] == "HEALTHY", processes)
            check("fresh_scan_recovers_after_" + mode, True)

        sql("DELETE FROM challenge_instance WHERE instance_id = '" + expected[0] + "';"
            "INSERT INTO challenge_instance (instance_id,team_id,user_id,challenge_id,status,created_at,updated_at,expires_at,hard_expires_at,isolation_profile) "
            "VALUES ('ffffffff-ffff-4fff-8fff-ffffffffffff','ffffffff-ffff-4fff-8fff-ffffffffffff','ffffffff-ffff-4fff-8fff-ffffffffffff',"
            "'ffffffff-ffff-4fff-8fff-ffffffffffff','RUNNING','2090-01-01Z','2090-01-01Z','2099-01-01Z','2099-01-02Z','WEB');")
        changed = identifiers()
        wait(lambda: complete(changed), processes)
        check("next_scan_starts_from_beginning_after_insert_delete", changed[0] == "ffffffff-ffff-4fff-8fff-ffffffffffff"
              and expected[0] not in changed and old_id in changed)
        check("legacy_profile_uses_only_authenticated_new_endpoint", state["wrong_auth"] == 0 and state["non_list"] == 0
              and all(record["limit"] == ["200"] for record in state["requests"]))
        check("no_credentials_in_public_snapshot", token not in json.dumps(full) and db_password not in json.dumps(full))
        report["passed"] = True
    finally:
        for process in reversed(processes):
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)
        if proxy:
            proxy.shutdown()
            proxy.server_close()
        for log in logs:
            log.close()
        if created:
            docker("rm", "--force", "--volumes", name)
        report["cleaned_up"] = all(process.poll() is not None for process in processes) and (not created or not docker("ps", "-aq", "--filter", "name=^/" + name + "$"))
        report["completed_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
        (folder / "result.json").write_text(json.dumps(report, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
