"""EC2-local deployment with a separate candidate check and container rollback."""

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request

BASE = Path("/opt/msg-sentinel")
CURRENT = "msg-sentinel"
PREVIOUS = "msg-sentinel-previous"
CANDIDATE = "msg-sentinel-candidate"
OWNER_LABEL = "io.msgctf.managed-by"
OWNER = "sentinel-deploy"


class DeploymentError(RuntimeError):
    pass


def command(args, *, optional=False):
    result = subprocess.run(args, capture_output=True, text=True, timeout=300)
    if result.returncode and not optional:
        # Docker errors can contain configuration values; do not publish stderr.
        raise DeploymentError("Deployment command failed: " + args[0])
    return result


def container(name):
    result = command(["docker", "container", "inspect", name], optional=True)
    if result.returncode:
        return None
    info = json.loads(result.stdout)[0]
    if info["Config"].get("Labels", {}).get(OWNER_LABEL) != OWNER:
        raise DeploymentError("Container name is already owned by another application")
    return info


def remove_container(name):
    if container(name):
        command(["docker", "rm", "-f", name])


def start(name, image, release, data, port):
    data.mkdir(parents=True, exist_ok=True)
    os.chown(data, 1654, 1654)
    args = [
        "docker", "run", "-d", "--name", name,
        "--label", OWNER_LABEL + "=" + OWNER,
        "--restart", "unless-stopped", "--read-only",
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
        "--memory", "512m", "--cpus", "0.5", "--pids-limit", "128",
        "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m",
        "--log-driver", "json-file", "--log-opt", "max-size=5m",
        "--log-opt", "max-file=3",
        "--publish", "127.0.0.1:" + str(port) + ":8080",
        "--mount", "type=bind,src=" + str(release / "profile.json") + ",dst=/config/profile.json,readonly",
        "--mount", "type=bind,src=" + str(data) + ",dst=/app/data",
        "--env-file", str(release / "probe.env"),
        "--env", "ASPNETCORE_URLS=http://+:8080",
        "--env", "SENTINEL_PROFILE_FILE=/config/profile.json",
        "--env", "platform_recovery__enabled=false",
        image,
    ]
    command(args)


def healthy(name, expected_profile, timeout=90):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        info = container(name)
        if not info or not info["State"]["Running"]:
            return False
        bindings = info["NetworkSettings"]["Ports"].get("8080/tcp") or []
        if len(bindings) != 1 or bindings[0]["HostIp"] != "127.0.0.1":
            return False
        url = "http://127.0.0.1:" + bindings[0]["HostPort"]
        try:
            with urllib.request.urlopen(url + "/healthz", timeout=3) as response:
                health = json.load(response)
            with urllib.request.urlopen(url + "/api/status", timeout=3) as response:
                status = json.load(response)
            if (health.get("status") == "ok" and health.get("mode") == "observe_only"
                    and status.get("profile") == expected_profile
                    and status.get("event_storage_healthy") is True
                    and status.get("updated_at") is not None):
                return True
        except (urllib.error.URLError, OSError, ValueError):
            pass
        time.sleep(2)
    return False


def activate(image, release, expected_profile):
    """Keep the previous container's image AND configuration for rollback."""
    candidate_data = release / "candidate-data"
    remove_container(CANDIDATE)
    try:
        start(CANDIDATE, image, release, candidate_data, 0)
        if not healthy(CANDIDATE, expected_profile):
            raise DeploymentError("Candidate did not become ready; current version unchanged")
    finally:
        remove_container(CANDIDATE)
        if candidate_data.exists():
            shutil.rmtree(candidate_data)

    had_current = container(CURRENT) is not None
    moved_previous = False
    try:
        if had_current:
            previous = container(PREVIOUS)
            if previous and previous["State"]["Running"]:
                raise DeploymentError("Previous container is running; deployment refused")
            remove_container(PREVIOUS)
            command(["docker", "stop", "--time", "20", CURRENT])
            command(["docker", "rename", CURRENT, PREVIOUS])
            moved_previous = True
        start(CURRENT, image, release, BASE / "data", 8080)
        if not healthy(CURRENT, expected_profile):
            raise DeploymentError("New version did not become ready")
    except Exception as error:
        if moved_previous or not had_current:
            remove_container(CURRENT)
        if had_current:
            if moved_previous:
                command(["docker", "rename", PREVIOUS, CURRENT])
            command(["docker", "start", CURRENT])
            old_profile_path = next(
                Path(m["Source"]) for m in container(CURRENT)["Mounts"]
                if m["Destination"] == "/config/profile.json"
            )
            old_profile = json.loads(old_profile_path.read_text())["platform_monitor"]["profile"]
            if not healthy(CURRENT, old_profile):
                raise DeploymentError("Deployment failed and previous version failed health check") from error
            raise DeploymentError("Deployment failed; previous version restored and verified") from error
        raise DeploymentError("Initial deployment failed; no previous version exists") from error


def prune_releases():
    # Only this application's stopped images and config directories are eligible.
    retained = set()
    images = set()
    for name in (CURRENT, PREVIOUS):
        info = container(name)
        if info:
            images.add(info["Image"])
            retained.update(Path(m["Source"]).parent.resolve() for m in info["Mounts"]
                            if m["Destination"] == "/config/profile.json")
    releases = (BASE / "releases").resolve()
    for path in releases.iterdir():
        if (path.is_dir() and not path.is_symlink() and path.resolve().parent == releases
                and path.resolve() not in retained
                and re.fullmatch(r"[0-9a-f]{40}-[0-9]+-[0-9]+", path.name)):
            shutil.rmtree(path)
    listed = command(["docker", "image", "ls", "msg-sentinel", "--format", "{{.ID}}"], optional=True)
    for short_id in set(listed.stdout.splitlines()):
        info = command(["docker", "image", "inspect", short_id], optional=True)
        if info.returncode:
            continue
        item = json.loads(info.stdout)[0]
        if (item["Id"] not in images and item.get("Config", {}).get("Labels", {}).get(
                "org.opencontainers.image.source") == "https://github.com/MSG-CTF/sentinel"):
            command(["docker", "image", "rm", short_id], optional=True)


def deploy(source):
    manifest = json.loads((source / "release.json").read_text())
    revision = manifest["revision"]
    release_id = manifest["release_id"]
    if not re.fullmatch(r"[0-9a-f]{40}", revision) or not re.fullmatch(
            re.escape(revision) + r"-[0-9]+-[0-9]+", release_id):
        raise DeploymentError("Invalid release identity")
    image = "msg-sentinel:" + revision
    command(["docker", "load", "--input", str(source / "image.tar.gz")])
    metadata = json.loads(command(["docker", "image", "inspect", image]).stdout)[0]
    labels = metadata["Config"].get("Labels", {})
    if (metadata["Id"] != manifest["image_id"]
            or labels.get("org.opencontainers.image.revision") != revision
            or labels.get("org.opencontainers.image.source") != "https://github.com/MSG-CTF/sentinel"):
        raise DeploymentError("Loaded image does not match tested release")
    profile = json.loads((source / "profile.json").read_text())
    if profile.get("platform_recovery", {}).get("enabled") is not False:
        raise DeploymentError("AWS release must use observation mode")
    release = BASE / "releases" / release_id
    release.mkdir(parents=True, exist_ok=False)
    for name in ("profile.json", "probe.env", "release.json"):
        shutil.copyfile(source / name, release / name)
        os.chmod(release / name, 0o600)
    os.chown(release / "profile.json", 1654, 1654)
    activate(image, release, profile["platform_monitor"]["profile"])
    prune_releases()
    print(json.dumps({"status": "deployed", "revision": revision, "mode": "observe_only"}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    try:
        deploy(args.directory.resolve())
    except Exception as error:
        # Include only our controlled diagnostics, not values from configuration.
        print(str(error) if isinstance(error, DeploymentError) else "Deployment failed: " + type(error).__name__)
        raise SystemExit(1)
