"""Prepare an immutable release, inject environment secrets, and deploy via SSM."""

import argparse
import base64
import hashlib
import http.client
import http.cookies
import ipaddress
import json
import os
from pathlib import Path
import re
import subprocess
import tarfile
import tempfile
import time
from urllib.parse import urlsplit

from deploy_host import validate_web_config


def run(args, *, optional=False):
    result = subprocess.run(args, text=True, capture_output=True, timeout=600)
    if result.returncode and not optional:
        raise RuntimeError("Command failed: " + args[0])
    return result


def token_name(value):
    denied = {"PATH", "HOME", "PYTHONPATH", "PYTHONHOME", "ENV", "BASH_ENV", "NODE_OPTIONS",
              "SENTINEL_PROFILE_FILE", "SENTINEL_AUTH_FILE", "SENTINEL_CANDIDATE",
              "SENTINEL_OPERATIONS_JSON", "SENTINEL_SSH_DISCORD_B64", "SENTINEL_DISCORD_WEBHOOK_URL"}
    if (not isinstance(value, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", value)
            or "__" in value or value in denied or value.startswith(("AWS_", "DOTNET_", "ASPNETCORE_", "LD_", "SENTINEL_SSH_"))):
        raise ValueError("Invalid probe credential name")
    return value


def remote_endpoint(value, *, https=False):
    # urlsplit strips some control characters; reject them before parsing.
    if not isinstance(value, str) or not value or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError("Invalid remote endpoint")
    try:
        url = urlsplit(value)
        host = (url.hostname or "").rstrip(".").lower()
        if (url.scheme not in ({"https"} if https else {"http", "https"}) or not host
                or url.username is not None or url.password is not None or "?" in value or "#" in value
                or (url.port is not None and not 1 <= url.port <= 65535)
                or host in {"localhost", "host.docker.internal"} or host.endswith(".localhost")):
            raise ValueError("Invalid remote endpoint")
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            if (all(re.fullmatch(r"(?:[0-9]+|0x[0-9a-f]+)", label) for label in host.split("."))
                    or len(host) > 253 or not all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                                         for label in host.split("."))):
                raise ValueError("Invalid remote endpoint")
        else:
            address = getattr(address, "ipv4_mapped", None) or address
            if address.is_loopback or address.is_unspecified or address.is_link_local:
                raise ValueError("Remote endpoint must not address the local host")
    except (ValueError, TypeError):
        raise ValueError("Remote endpoint must use a valid host, port and permitted transport without credentials") from None
    return url


def instance_credentials(config):
    """Production inventory is read-only and uses explicit TLS control planes."""
    fields = {"enabled", "scheduler_url", "scheduler_token_env", "poll_interval_seconds", "timeout_seconds",
              "runtime_targets", "functional_checks"}
    if not isinstance(config, dict) or set(config) - fields or type(config.get("enabled")) is not bool:
        raise ValueError("Invalid instance monitor configuration")
    if not config["enabled"]:
        return set()
    for field, default, low, high in (("poll_interval_seconds", 30, 5, 300), ("timeout_seconds", 4, 1, 10)):
        value = config.get(field, default)
        if type(value) is not int or not low <= value <= high:
            raise ValueError("Invalid instance monitor polling or timeout")
    if remote_endpoint(config.get("scheduler_url"), https=True).path != "/api/instances":
        raise ValueError("Production inventory requires the full scheduler list endpoint")
    names = {token_name(config.get("scheduler_token_env", "SCHEDULER_API_TOKEN"))}
    runtimes = config.get("runtime_targets", [])
    if not isinstance(runtimes, list) or len(runtimes) > 16:
        raise ValueError("Invalid runtime inventory targets")
    seen = set()
    for target in runtimes:
        if (not isinstance(target, dict) or set(target) != {"target_id", "base_url", "auth_token_env"}
                or not isinstance(target.get("target_id"), str)
                or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}", target["target_id"])
                or target["target_id"] in seen):
            raise ValueError("Invalid or duplicate runtime inventory target")
        seen.add(target["target_id"])
        if remote_endpoint(target["base_url"], https=True).path not in {"", "/"}:
            raise ValueError("Runtime inventory requires a control-plane origin")
        names.add(token_name(target["auth_token_env"]))
    if config.get("functional_checks", []) != []:
        raise ValueError("Local functional fixtures are not enabled in production")
    return names


def probe_credentials(profile, environment):
    if not isinstance(profile, dict) or not isinstance(profile.get("instance_monitor"), dict):
        raise ValueError("Invalid instance monitor configuration")
    credentials = json.loads(environment.get("SENTINEL_PROBE_SECRETS_JSON") or "{}")
    if not isinstance(credentials, dict):
        raise ValueError("Probe credentials must contain a JSON object")
    inventory = profile.get("instance_monitor", {})
    # A staged scheduler secret must not enter a disabled inventory container.
    if (inventory.get("enabled") is True
            and inventory.get("scheduler_token_env", "SCHEDULER_API_TOKEN") == "SCHEDULER_API_TOKEN"):
        dedicated = environment.get("SCHEDULER_API_TOKEN")
        if dedicated:
            if "SCHEDULER_API_TOKEN" in credentials and credentials["SCHEDULER_API_TOKEN"] != dedicated:
                raise ValueError("Conflicting scheduler credentials")
            credentials["SCHEDULER_API_TOKEN"] = dedicated
    return credentials


def validate_configuration(profile, credentials):
    if not isinstance(profile, dict) or not isinstance(credentials, dict):
        raise ValueError("Configuration must contain JSON objects")
    monitor = profile.get("platform_monitor", {})
    if (not isinstance(monitor, dict) or monitor.get("profile") != "aws"
            or monitor.get("event_file") != "data/events.jsonl"):
        raise ValueError("AWS profile identity or event file is invalid")
    if profile.get("platform_recovery", {}).get("enabled") is not False:
        raise ValueError("Production recovery adapter has not been enabled for deployment")
    names = instance_credentials(profile.get("instance_monitor"))
    targets = monitor.get("targets")
    if not isinstance(targets, list) or not 1 <= len(targets) <= 32:
        raise ValueError("Invalid target collection")
    owned_instances = set()
    for target in targets:
        if not isinstance(target, dict):
            raise ValueError("Invalid target definition")
        if target.get("enabled", True):
            if target.get("kind") in {"service_heartbeat", "cluster_heartbeat", "challenge_heartbeat"}:
                if target.get("url") or not target.get("auth_token_env"):
                    raise ValueError("Heartbeat requires a dedicated token and no probe URL")
                name = token_name(target["auth_token_env"])
                if not isinstance(credentials.get(name), str) or len(credentials[name]) < 32:
                    raise ValueError("Heartbeat credential must contain at least 32 characters")
                names.add(name)
                if target.get("kind") == "cluster_heartbeat" and not re.fullmatch(
                        r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", target.get("expected_fields", {}).get("cluster_target_id", "")):
                    raise ValueError("Cluster heartbeat requires an explicit cluster binding")
                if target.get("kind") == "challenge_heartbeat":
                    validate_challenge_binding(target.get("expected_fields"))
                    if sum(t.get("auth_token_env") == name for t in targets if t.get("enabled", True)) != 1:
                        raise ValueError("Owned challenge reporter requires a dedicated credential")
                    if list(credentials.values()).count(credentials[name]) != 1:
                        raise ValueError("Owned challenge reporter cannot reuse another probe credential")
                    instance = target["expected_fields"]["instance_id"]
                    if instance in owned_instances:
                        raise ValueError("Owned challenge instance has duplicate reporter bindings")
                    owned_instances.add(instance)
                continue
            name = target.get("auth_token_env")
            if target.get("kind") == "runtime_healthz" and (
                    not name or remote_endpoint(target.get("url"), https=True).path != "/internal/v1/healthz"):
                raise ValueError("Runtime readiness requires authenticated HTTPS and its exact endpoint")
            remote_endpoint(target.get("url"), https=bool(name))
            if name:
                names.add(token_name(name))
    if set(credentials) != names:
        raise ValueError("Probe credentials must match the enabled profile's token names")
    for name, value in credentials.items():
        token_name(name)
        if (not isinstance(value, str) or not value.strip() or len(value) > 8192
                or any(ord(c) <= 32 or ord(c) >= 127 for c in value)):
            raise ValueError("Invalid probe credential entry")
    return "".join(name + "=" + value + "\n" for name, value in sorted(credentials.items()))


def validate_challenge_binding(fields):
    import uuid
    from datetime import datetime, timezone, timedelta
    required = {"instance_id", "challenge_id", "release_id", "owner_team_id", "runtime_target_id",
                "runtime_workload_id", "profile", "source_revision", "expires_at"}
    if not isinstance(fields, dict) or set(fields) != required or not all(isinstance(v, str) for v in fields.values()):
        raise ValueError("Owned challenge requires its complete expiring identity")
    for key in ("instance_id", "challenge_id", "release_id", "owner_team_id"):
        if str(uuid.UUID(fields[key])) != fields[key] or uuid.UUID(fields[key]).int == 0:
            raise ValueError("Invalid owned challenge identity")
    profiles = {"web-grade-tampering", "web-notebook", "pwn-random6", "pwn-note", "pwn-ghost-file", "pwn-price-tag"}
    if (fields["profile"] not in profiles or fields["source_revision"] != "1dea73b652f9eeb98204c1fda7f66b9afb89505d"
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", fields["runtime_target_id"])
            or not 1 <= len(fields["runtime_workload_id"]) <= 256
            or any(ord(c) < 32 or ord(c) == 127 for c in fields["runtime_workload_id"])):
        raise ValueError("Invalid owned challenge binding")
    expires = datetime.fromisoformat(fields["expires_at"].replace("Z", "+00:00"))
    if expires.utcoffset() is None or not datetime.now(timezone.utc) < expires <= datetime.now(timezone.utc) + timedelta(hours=24):
        raise ValueError("Owned challenge lease must expire within 24 hours")


def build_manifest(directory, revision, run_id, attempt):
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("Invalid source revision")
    if not re.fullmatch(r"[0-9]+", run_id) or not re.fullmatch(r"[0-9]+", attempt):
        raise ValueError("Invalid workflow run identity")
    image = "msg-sentinel:" + revision
    info = json.loads(run(["docker", "image", "inspect", image]).stdout)[0]
    manifest = {"revision": revision, "release_id": revision + "-" + run_id + "-" + attempt,
                "image_id": info["Id"]}
    (directory / "release.json").write_text(json.dumps(manifest) + "\n", encoding="utf-8")


def package(directory, destination):
    profile = json.loads(os.environ["SENTINEL_PROFILE_JSON"])
    credentials = probe_credentials(profile, os.environ)
    env_file = validate_configuration(profile, credentials)
    env_file += operations_environment(profile, os.environ)
    web_config = validate_web_config(json.loads(os.environ["SENTINEL_WEB_CONFIG_JSON"]))
    manifest = json.loads((directory / "release.json").read_text())
    manifest["public_web"] = web_config
    with tempfile.TemporaryDirectory(prefix="sentinel-config-") as temp:
        root = Path(temp)
        os.chmod(root, 0o700)
        (root / "profile.json").write_text(json.dumps(profile, ensure_ascii=False) + "\n", encoding="utf-8")
        (root / "probe.env").write_bytes(env_file.encode("utf-8"))
        (root / "release.json").write_text(json.dumps(manifest) + "\n", encoding="utf-8")
        for name in ("profile.json", "probe.env", "release.json"):
            os.chmod(root / name, 0o600)
        with tarfile.open(destination, "w:gz") as archive:
            for name in ("image.tar.gz", "deploy_host.py"):
                archive.add(directory / name, arcname=name, recursive=False)
            for name in ("profile.json", "probe.env", "release.json"):
                entry = archive.gettarinfo(root / name, arcname=name)
                entry.mode = 0o600
                with (root / name).open("rb") as stream:
                    archive.addfile(entry, stream)
    os.chmod(destination, 0o600)


def operations_environment(profile, environment):
    """Dedicated secrets never enter the public artifact or profile JSON."""
    lines = []
    webhook = environment.get('SENTINEL_DISCORD_WEBHOOK_URL', '').strip()
    if profile.get('discord_alerts', {}).get('enabled'):
        if not re.fullmatch(r'https://discord\.com/api/(?:v10/)?webhooks/[0-9]{16,22}/[A-Za-z0-9_-]{30,200}', webhook):
            raise ValueError('Discord notification credential is missing or invalid')
        if profile['discord_alerts'].get('state_file') != 'data/discord-alerts.json':
            raise ValueError('Invalid alert state path')
        lines.append('SENTINEL_DISCORD_WEBHOOK_URL=' + webhook)
    raw = environment.get('SENTINEL_OPERATIONS_JSON', '').strip()
    if raw:
        assets = json.loads(raw)
        if not isinstance(assets, list) or not 1 <= len(assets) <= 32:
            raise ValueError('Invalid operations collection')
        seen = set()
        key_names = set()
        allowed_keys = {'SENTINEL_SSH_DISCORD_B64', 'SENTINEL_SSH_SCHEDULER_B64', 'SENTINEL_SSH_RUNTIME_B64', 'SENTINEL_SSH_BACKEND_B64'}
        for asset in assets:
            if (not isinstance(asset, dict) or set(asset) != {'asset_id', 'host', 'port', 'username', 'host_key_sha256', 'service', 'private_key_env'}
                    or not re.fullmatch(r'[a-z][a-z0-9_-]{0,63}', asset['asset_id']) or asset['asset_id'] in seen
                    or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9.-]{0,252}', asset['host'])
                    or not isinstance(asset['port'], int) or not 1 <= asset['port'] <= 65535
                    or not re.fullmatch(r'[a-z_][a-z0-9_-]{0,31}', asset['username'])
                    or not re.fullmatch(r'(?:[a-zA-Z0-9_.@-]{1,100}\.service|docker:[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127})', asset['service'])
                    or asset['private_key_env'] not in allowed_keys):
                raise ValueError('Invalid SSH operations binding')
            if len(base64.b64decode(asset['host_key_sha256'], validate=True)) != 32:
                raise ValueError('SSH host key pin required')
            seen.add(asset['asset_id'])
            key_names.add(asset['private_key_env'])
        key_lines = []
        for name in sorted(key_names):
            key = environment.get(name, '').strip()
            decoded = base64.b64decode(key, validate=True)
            if not 100 <= len(decoded) <= 8192 or b'PRIVATE KEY-----' not in decoded:
                raise ValueError('Dedicated SSH key required')
            key_lines.append(name + '=' + key)
        lines.extend(['SENTINEL_OPERATIONS_JSON=' + json.dumps(assets, separators=(',', ':')), *key_lines])
    return ''.join(line + '\n' for line in lines)


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def verify_web():
    config = validate_web_config(json.loads(os.environ["SENTINEL_WEB_CONFIG_JSON"]))
    password = os.environ["SENTINEL_ADMIN_PASSWORD"]
    if not 24 <= len(password) <= 72 or any(c in password for c in "\r\n\0"):
        raise ValueError("Invalid administrator verification credential")
    domain = config["domain"]
    cookies = http.cookies.SimpleCookie()

    def request(path, method="GET", data=None, csrf=None, authenticated=False, https=True, extra_headers=None):
        conn_type = http.client.HTTPSConnection if https else http.client.HTTPConnection
        conn = conn_type(domain, timeout=15)
        headers = dict(extra_headers or {})
        if authenticated and cookies:
            headers["Cookie"] = "; ".join(key + "=" + value.value for key, value in cookies.items())
        if csrf: headers["X-CSRF-TOKEN"] = csrf
        if data is not None: headers["Content-Type"] = "application/json"
        try:
            conn.request(method, path, json.dumps(data) if data is not None else None, headers)
            response = conn.getresponse()
            for key, value in response.getheaders():
                if key.lower() == "set-cookie": cookies.load(value)
            return response.status, dict(response.getheaders()), response.read(2 * 1024 * 1024)
        finally:
            conn.close()

    status, headers, body = request("/", https=False)
    if status != 308 or headers.get("Location") != "https://" + domain + "/":
        raise RuntimeError("Public HTTP did not redirect directly to HTTPS")
    status, headers, body = request("/")
    if status != 302 or headers.get("Location") != "/login":
        raise RuntimeError("Dashboard did not redirect to administrator login")
    if request("/login")[0] != 200:
        raise RuntimeError("Administrator login page is unavailable")
    for path in ("/api/status", "/api/history", "/api/instances", "/api/events", "/api/recovery", "/metrics", "/healthz", "/asset-access.json"):
        if request(path)[0] != 401:
            raise RuntimeError("Operational endpoint did not require administrator authentication")
    if any(request(path)[0] != 404 for path in ("/internal/readiness", "/internal/recovery", "/internal/metrics", "/internal/grafana-auth")):
        raise RuntimeError("Internal endpoints are exposed through the public proxy")
    for path in ("/grafana/", "/grafana/api/search"):
        status, headers, _ = request(path)
        if status != 302 or headers.get("Location") != "/login":
            raise RuntimeError("Grafana did not require Sentinel administrator authentication")
    if request("/ingest/heartbeat/discord-bot", method="POST", data={})[0] != 401:
        raise RuntimeError("Heartbeat endpoint did not require its dedicated credential")
    status, _, body = request("/auth/csrf")
    if status != 200: raise RuntimeError("Login CSRF bootstrap failed")
    csrf = json.loads(body)["request_token"]
    invalid = {"username": config["username"], "password": "deliberately-invalid-test-password"}
    if request("/auth/login", "POST", invalid, csrf, True)[0] != 401:
        raise RuntimeError("Login accepted an invalid password")
    valid = {"username": config["username"], "password": password}
    if request("/auth/login", "POST", valid, authenticated=True)[0] != 400:
        raise RuntimeError("Login accepted a request without CSRF protection")
    if request("/auth/login", "POST", valid, csrf, True)[0] != 200:
        raise RuntimeError("Administrator login failed")
    status, _, body = request("/api/status", authenticated=True)
    snapshot = json.loads(body) if status == 200 else {}
    if (snapshot.get("profile") != "aws" or snapshot.get("updated_at") is None
            or snapshot.get("event_storage_healthy") is not True):
        raise RuntimeError("Authenticated monitoring verification failed")
    if request("/", authenticated=True)[0] != 200 or request("/metrics", authenticated=True)[0] != 200:
        raise RuntimeError("Authenticated dashboard or metrics verification failed")
    status, _, body = request("/grafana/api/dashboards/uid/sentinel-django-health", authenticated=True)
    if status != 200 or json.loads(body).get("dashboard", {}).get("uid") != "sentinel-django-health":
        raise RuntimeError("Authenticated Grafana dashboard verification failed")
    status, _, body = request("/grafana/api/datasources/proxy/uid/Prometheus/api/v1/query?query=sentinel_target_health", authenticated=True)
    if status != 200 or not json.loads(body).get("data", {}).get("result"):
        raise RuntimeError("Grafana has no collected Sentinel target metrics")
    for metric in ("sentinel_django_component_health", "sentinel_django_check_success", "sentinel_target_latency_ms"):
        end = int(time.time() * 1000)
        query = {"queries": [{"refId": "A", "datasource": {"uid": "Prometheus", "type": "prometheus"},
                 "expr": metric, "instant": True, "range": False, "intervalMs": 15000, "maxDataPoints": 400}],
                 "from": str(end - 3600000), "to": str(end)}
        status, _, body = request("/grafana/api/ds/query", "POST", query, authenticated=True,
                                 extra_headers={"X-Panel-Title": "Django · DB · Redis", "X-Dashboard-Title": "Sentinel · Operations"})
        if status != 200 or not json.loads(body).get("results", {}).get("A", {}).get("frames"):
            raise RuntimeError("Grafana panel query failed: " + metric)
    _, _, body = request("/auth/csrf", authenticated=True)
    csrf = json.loads(body)["request_token"]
    if request("/auth/logout", "POST", csrf=csrf, authenticated=True)[0] != 200:
        raise RuntimeError("Administrator logout failed")
    if request("/api/status", authenticated=True)[0] != 401:
        raise RuntimeError("Logged-out browser can still read monitoring data")
    if request("/grafana/api/search", authenticated=True)[0] != 302:
        raise RuntimeError("Logged-out browser can still read Grafana data")
    print("Public HTTPS, administrator login, Grafana metrics, CSRF, heartbeat boundary and logout checks passed")


def deploy(directory):
    instance = os.environ["AWS_INSTANCE_ID"]
    bucket = os.environ["AWS_ARTIFACT_BUCKET"]
    document = os.environ["AWS_DEPLOY_DOCUMENT"]
    if not re.fullmatch(r"i-[0-9a-f]{8,17}", instance):
        raise ValueError("Invalid EC2 instance ID")
    if not re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", bucket):
        raise ValueError("Invalid artifact bucket")
    if not re.fullmatch(r"[A-Za-z0-9_.-]{3,128}", document):
        raise ValueError("Invalid deployment document")
    manifest = json.loads((directory / "release.json").read_text())
    release_id = manifest["release_id"]
    if not re.fullmatch(r"[0-9a-f]{40}-[0-9]+-[0-9]+", release_id):
        raise ValueError("Invalid release identity")
    key = "releases/" + release_id + ".tar.gz"
    with tempfile.TemporaryDirectory(prefix="sentinel-release-") as temp:
        archive = Path(temp) / "release.tar.gz"
        package(directory, archive)
        run(["aws", "s3", "cp", str(archive), "s3://" + bucket + "/" + key,
             "--sse", "AES256", "--only-show-errors"])
        request = {
            "InstanceIds": [instance], "DocumentName": document,
            "TimeoutSeconds": 600, "Comment": "Sentinel " + manifest["revision"],
            "Parameters": {"ArtifactKey": [key], "ArtifactSha256": [digest(archive)]},
        }
        parameters = Path(temp) / "command.json"
        parameters.write_text(json.dumps(request), encoding="utf-8")
        response = json.loads(run(["aws", "ssm", "send-command", "--cli-input-json",
                                  "file://" + str(parameters), "--output", "json"]).stdout)
        command_id = response["Command"]["CommandId"]
        for _ in range(120):
            result = run(["aws", "ssm", "get-command-invocation", "--command-id", command_id,
                          "--instance-id", instance, "--output", "json"], optional=True)
            if result.returncode == 0:
                state = json.loads(result.stdout)
                if state["Status"] == "Success":
                    print("Deployment and health checks passed for " + manifest["revision"])
                    summary = os.environ.get("GITHUB_STEP_SUMMARY")
                    if summary:
                        with open(summary, "a", encoding="utf-8") as stream:
                            stream.write("Sentinel deployed and verified in observation mode\n\nCommit: "
                                         + manifest["revision"] + "\n")
                    return
                if state["Status"] not in {"Pending", "InProgress", "Delayed"}:
                    for line in state.get("StandardOutputContent", "").splitlines():
                        if line.startswith("SENTINEL_DIAGNOSTIC "):
                            print(line[:3000])
                    raise RuntimeError("SSM deployment ended with status " + state["Status"]
                                       + "; inspect the command in the AWS console")
            time.sleep(5)
        raise RuntimeError("Deployment confirmation timed out; inspect SSM before retrying")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["manifest", "deploy", "validate", "verify-web"])
    parser.add_argument("--directory", type=Path, default=Path("release"))
    args = parser.parse_args()
    try:
        if args.action == "manifest":
            build_manifest(args.directory, os.environ["GITHUB_SHA"],
                           os.environ["GITHUB_RUN_ID"], os.environ["GITHUB_RUN_ATTEMPT"])
        elif args.action == "validate":
            profile = json.loads(os.environ["SENTINEL_PROFILE_JSON"])
            validate_configuration(profile, probe_credentials(profile, os.environ))
            print("Deployment configuration is valid")
        elif args.action == "verify-web":
            verify_web()
        else:
            deploy(args.directory)
    except Exception as error:
        print(str(error) if isinstance(error, RuntimeError) else "Release failed: " + type(error).__name__)
        raise SystemExit(1)
