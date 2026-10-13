"""Prepare an immutable release, inject environment secrets, and deploy via SSM."""

import argparse
import base64
import hashlib
import http.client
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


def validate_configuration(profile, credentials):
    if not isinstance(profile, dict) or not isinstance(credentials, dict):
        raise ValueError("Configuration must contain JSON objects")
    monitor = profile.get("platform_monitor", {})
    if monitor.get("profile") != "aws" or monitor.get("event_file") != "data/events.jsonl":
        raise ValueError("AWS profile identity or event file is invalid")
    if profile.get("platform_recovery", {}).get("enabled") is not False:
        raise ValueError("Production recovery adapter has not been enabled for deployment")
    if profile.get("instance_monitor", {}).get("enabled") is not False:
        raise ValueError("Remote instance inventory contract must be verified before enabling")
    targets = monitor.get("targets")
    if not isinstance(targets, list) or not 1 <= len(targets) <= 32:
        raise ValueError("Invalid target collection")
    names = set()
    for target in targets:
        if not isinstance(target, dict):
            raise ValueError("Invalid target definition")
        if target.get("enabled", True):
            url = urlsplit(target.get("url", ""))
            if (url.scheme not in {"http", "https"} or not url.hostname
                    or url.username or url.password or url.query or url.fragment
                    or url.hostname in {"localhost", "127.0.0.1", "host.docker.internal", "::1"}):
                raise ValueError("Enabled target needs a remote endpoint without embedded credentials")
            name = target.get("auth_token_env")
            if name:
                if url.scheme != "https":
                    raise ValueError("Authenticated remote probes require HTTPS")
                names.add(name)
    if set(credentials) != names:
        raise ValueError("Probe credentials must match the enabled profile's token names")
    denied = {"PATH", "HOME", "PYTHONPATH", "PYTHONHOME", "ENV", "BASH_ENV", "NODE_OPTIONS"}
    for name, value in credentials.items():
        if (not re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", name) or name in denied
                or name.startswith(("AWS_", "DOTNET_", "ASPNETCORE_", "LD_"))
                or not isinstance(value, str) or not value or len(value) > 8192
                or any(c in value for c in "\r\n\0")):
            raise ValueError("Invalid probe credential entry")
    return "".join(name + "=" + value + "\n" for name, value in sorted(credentials.items()))


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
    credentials = json.loads(os.environ.get("SENTINEL_PROBE_SECRETS_JSON") or "{}")
    env_file = validate_configuration(profile, credentials)
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
                archive.add(root / name, arcname=name, recursive=False)
    os.chmod(destination, 0o600)


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

    def request(path, authorization=None, https=True):
        # No redirects: credentials must never leave this exact HTTPS origin.
        conn_type = http.client.HTTPSConnection if https else http.client.HTTPConnection
        conn = conn_type(domain, timeout=15)
        headers = {"Authorization": authorization} if authorization else {}
        try:
            conn.request("GET", path, headers=headers)
            response = conn.getresponse()
            return response.status, dict(response.getheaders()), response.read(2 * 1024 * 1024)
        finally:
            conn.close()

    def basic(value):
        return "Basic " + base64.b64encode((config["username"] + ":" + value).encode()).decode()

    status, headers, body = request("/", https=False)
    if status != 308 or headers.get("Location") != "https://" + domain + "/":
        raise RuntimeError("Public HTTP did not redirect directly to HTTPS")
    for path in ("/", "/api/status", "/api/instances", "/api/events", "/api/recovery", "/metrics", "/healthz"):
        status, headers, body = request(path)
        if status != 401 or not any(k.lower() == "www-authenticate" for k in headers):
            raise RuntimeError("Public endpoint did not require administrator authentication")
    if request("/api/status", basic("deliberately-invalid-test-password"))[0] != 401:
        raise RuntimeError("Public endpoint accepted invalid credentials")
    status, headers, body = request("/healthz", basic(password))
    health = json.loads(body) if status == 200 else {}
    if health.get("status") != "ok" or health.get("mode") != "observe_only":
        raise RuntimeError("Authenticated health verification failed")
    status, headers, body = request("/api/status", basic(password))
    snapshot = json.loads(body) if status == 200 else {}
    if (snapshot.get("profile") != "aws" or snapshot.get("updated_at") is None
            or snapshot.get("event_storage_healthy") is not True):
        raise RuntimeError("Authenticated monitoring verification failed")
    if request("/", basic(password))[0] != 200:
        raise RuntimeError("Authenticated dashboard verification failed")
    if request("/internal/recovery", basic(password))[0] != 404:
        raise RuntimeError("Internal recovery endpoint must stay unavailable through the public proxy")
    print("Public HTTPS certificate, redirect, authentication, dashboard and monitoring checks passed")


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
            validate_configuration(json.loads(os.environ["SENTINEL_PROFILE_JSON"]),
                                   json.loads(os.environ.get("SENTINEL_PROBE_SECRETS_JSON") or "{}"))
            print("Deployment configuration is valid")
        elif args.action == "verify-web":
            verify_web()
        else:
            deploy(args.directory)
    except Exception as error:
        print(str(error) if isinstance(error, RuntimeError) else "Release failed: " + type(error).__name__)
        raise SystemExit(1)
