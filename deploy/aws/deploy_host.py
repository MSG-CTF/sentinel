"""EC2-local deployment with a separate candidate check and container rollback."""

import argparse
import base64
import secrets
import http.client
import ipaddress
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
WEB_CURRENT = "msg-sentinel-web"
WEB_PREVIOUS = "msg-sentinel-web-previous"
# Official Caddy 2.11.7 multi-platform image; upgrades require a reviewed digest.
WEB_IMAGE = "caddy:2.11.7-alpine@sha256:d8542f48d34a9cf4e4c11a478865229840e87e4c96ea3f439101f31a5d35f75f"
GRAFANA_IMAGE = "grafana/grafana:13.2.4@sha256:dbbc38ea21103cefb50396340cd88afec779c7e070b0a7bf5a75b4ce46651d3f"
PROMETHEUS_IMAGE = "prom/prometheus:v3.15.0@sha256:efd719c99d83b060d9daefdcf00360461adf279f45ef5391f8d111892118753e"
MONITORS = ("msg-sentinel-prometheus", "msg-sentinel-grafana")
MONITORING_DIAGNOSTICS = {}


class DeploymentError(RuntimeError):
    pass


def validate_web_config(value):
    if not isinstance(value, dict) or set(value) != {"domain", "username", "password_hash"}:
        raise ValueError("Public HTTPS requires a domain and hashed administrator credentials")
    domain, username, password_hash = (value[k] for k in ("domain", "username", "password_hash"))
    if (not isinstance(domain, str) or len(domain) > 253
            or not re.fullmatch(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}", domain)
            or domain.endswith((".localhost", ".local", ".internal", ".test", ".invalid"))):
        raise ValueError("Public HTTPS requires a public DNS hostname")
    try:
        ipaddress.ip_address(domain)
    except ValueError:
        pass
    else:
        raise ValueError("Public HTTPS requires a DNS hostname, not an IP address")
    if not isinstance(username, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{2,31}", username):
        raise ValueError("Invalid administrator username")
    if (not isinstance(password_hash, str)
            or not re.fullmatch(r"pbkdf2-sha256\$600000\$[A-Za-z0-9+/]{22}==\$[A-Za-z0-9+/]{43}=", password_hash)):
        raise ValueError("Administrator password must be a PBKDF2-SHA256 hash with 600000 iterations")
    if len(base64.b64decode(password_hash.split("$")[2], validate=True)) != 16 or len(base64.b64decode(password_hash.split("$")[3], validate=True)) != 32:
        raise ValueError("Invalid administrator password hash")
    return dict(value)


def web_caddyfile(config):
    value = validate_web_config(config)
    # Session authentication lives in ASP.NET; internal control routes stay private.
    return """{
    admin off
    servers {
        protocols h1 h2
    }
}
https://%s {
    header {
        -Server
        Strict-Transport-Security "max-age=31536000"
        ?Cache-Control "no-store"
        X-Robots-Tag "noindex, nofollow"
    }
    @graphics path /login.css /login.js /mechanism.css /sentinel-core.js /sentinel-scene.js /vendor/three/three.module.js /vendor/three/three.core.js /vendor/three/HDRLoader.js /vendor/three/RectAreaLightUniformsLib.js /vendor/three/RectAreaLightTexturesLib.js /assets/studio-small-09.hdr
    encode @graphics zstd gzip {
        match {
            status 200
            header Content-Type text/javascript*
            header Content-Type application/javascript*
            header Content-Type text/css*
            header Content-Type image/vnd.radiance*
        }
    }
    route {
        @internal path /internal /internal/*
        respond @internal 404
        @grafana path /grafana /grafana/*
        handle @grafana {
            route {
                request_header -X-Sentinel-User
                # Grafana sends Latin-1 display titles as diagnostic headers.
                # They are not identity and can fail Kestrel's UTF-8 parser.
                request_header -X-Panel-Title
                request_header -X-Dashboard-Title
                forward_auth 127.0.0.1:8080 {
                    uri /internal/grafana-auth
                    copy_headers X-Sentinel-User
                    header_up -Authorization
                    header_up -Proxy-Authorization
                }
                reverse_proxy 127.0.0.1:3000 {
                    header_up -Authorization
                    header_up -Proxy-Authorization
                    header_up -Cookie
                    header_up -X-WEBAUTH-USER
                }
            }
        }
        @heartbeat path /ingest/heartbeat/* /ingest/cluster/*
        reverse_proxy @heartbeat 127.0.0.1:8080 {
            header_up -Proxy-Authorization
        }
        reverse_proxy 127.0.0.1:8080 {
            header_up -Authorization
            header_up -Proxy-Authorization
        }
    }
    handle_errors {
        header {
            -Server
            Strict-Transport-Security "max-age=31536000"
            Cache-Control "no-store"
            X-Robots-Tag "noindex, nofollow"
        }
        respond "" {err.status_code}
    }
}
""" % value["domain"]


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
    key_dir = data / "auth-keys"
    key_dir.mkdir(mode=0o700, exist_ok=True)
    os.chown(key_dir, 1654, 1654)
    os.chmod(key_dir, 0o700)
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
        "--mount", "type=bind,src=" + str(release / "auth.json") + ",dst=/config/auth.json,readonly",
        "--env", "SENTINEL_AUTH_FILE=/config/auth.json",
        "--env-file", str(release / "probe.env"),
        "--env", "ASPNETCORE_URLS=http://+:8080",
        "--env", "SENTINEL_PROFILE_FILE=/config/profile.json",
        "--env", "platform_recovery__enabled=false",
        "--env", "SENTINEL_CANDIDATE=" + ("true" if name == CANDIDATE else "false"),
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
            auth_mount = next((m for m in info.get("Mounts", []) if m["Destination"] == "/config/auth.json"), None)
            if auth_mount:
                auth = json.loads(Path(auth_mount["Source"]).read_text())
                request = urllib.request.Request(url + "/internal/readiness", headers={"Authorization": "Bearer " + auth["ReadinessToken"]})
                with urllib.request.urlopen(request, timeout=3) as response:
                    health = status = json.load(response)
            else:
                # Rollback can target the previous release that used proxy authentication.
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


def activate(image, release, expected_profile, finalize=None):
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
        if finalize:
            finalize()
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


def prepare_web(release, config):
    path = release / "Caddyfile"
    path.write_text(web_caddyfile(config), encoding="utf-8")
    os.chmod(path, 0o600)
    command(["docker", "pull", WEB_IMAGE])
    command(["docker", "run", "--rm", "--network", "none", "--read-only",
             "--cap-drop", "ALL", "--cap-add", "NET_BIND_SERVICE", "--security-opt", "no-new-privileges:true",
             "--memory", "192m", "--pids-limit", "64",
             "--tmpfs", "/data:rw,noexec,nosuid,size=16m",
             "--tmpfs", "/config:rw,noexec,nosuid,size=16m",
             "--mount", "type=bind,src=" + str(path) + ",dst=/etc/caddy/Caddyfile,readonly",
             WEB_IMAGE, "caddy", "validate", "--config", "/etc/caddy/Caddyfile"])


def start_web(release):
    for directory in ("web-data", "web-config"):
        (BASE / directory).mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(BASE / directory, 0o700)
    command([
        "docker", "run", "-d", "--name", WEB_CURRENT,
        "--label", OWNER_LABEL + "=" + OWNER, "--restart", "unless-stopped",
        "--network", "host", "--read-only", "--cap-drop", "ALL",
        "--cap-add", "NET_BIND_SERVICE", "--security-opt", "no-new-privileges:true",
        "--memory", "192m", "--cpus", "0.5", "--pids-limit", "64",
        "--log-driver", "json-file", "--log-opt", "max-size=5m", "--log-opt", "max-file=3",
        "--mount", "type=bind,src=" + str(release / "Caddyfile") + ",dst=/etc/caddy/Caddyfile,readonly",
        "--mount", "type=bind,src=" + str(BASE / "web-data") + ",dst=/data",
        "--mount", "type=bind,src=" + str(BASE / "web-config") + ",dst=/config",
        WEB_IMAGE, "caddy", "run", "--config", "/etc/caddy/Caddyfile",
    ])


def web_ready(domain, timeout=30):
    # This checks startup before DNS / ingress is enabled. Public TLS and login
    # are independently verified by the runner after first-time DNS setup.
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        info = container(WEB_CURRENT)
        if not info or not info["State"]["Running"]:
            return False
        conn = http.client.HTTPConnection("127.0.0.1", 80, timeout=3)
        try:
            conn.request("GET", "/", headers={"Host": domain})
            response = conn.getresponse()
            if response.status == 308 and response.getheader("Location") == "https://" + domain + "/":
                return True
        except (OSError, http.client.HTTPException):
            pass
        finally:
            conn.close()
        time.sleep(1)
    return False


def activate_web(release, config):
    had_current = container(WEB_CURRENT) is not None
    moved_previous = False
    try:
        if had_current:
            previous = container(WEB_PREVIOUS)
            if previous and previous["State"]["Running"]:
                raise DeploymentError("Previous HTTPS proxy is running; deployment refused")
            remove_container(WEB_PREVIOUS)
            command(["docker", "stop", "--time", "10", WEB_CURRENT])
            command(["docker", "rename", WEB_CURRENT, WEB_PREVIOUS])
            moved_previous = True
        start_web(release)
        if not web_ready(config["domain"]):
            raise DeploymentError("HTTPS proxy did not become ready")
    except Exception as error:
        if moved_previous or not had_current:
            remove_container(WEB_CURRENT)
        if had_current:
            if moved_previous:
                command(["docker", "rename", WEB_PREVIOUS, WEB_CURRENT])
            command(["docker", "start", WEB_CURRENT])
            old_release = next(Path(m["Source"]).parent for m in container(WEB_CURRENT)["Mounts"]
                               if m["Destination"] == "/etc/caddy/Caddyfile")
            old_domain = json.loads((old_release / "release.json").read_text())["public_web"]["domain"]
            if not web_ready(old_domain):
                raise DeploymentError("HTTPS activation failed and previous proxy failed its startup check") from error
            raise DeploymentError("HTTPS activation failed; previous proxy restored") from error
        raise DeploymentError("Initial HTTPS activation failed; public proxy remains closed") from error


def prepare_monitoring(image, release, config, token):
    # Ship Minsoo's provisioned dashboard inside the tested application image;
    # the SSM bundle's strict file allowlist therefore stays unchanged.
    name = "msg-sentinel-monitoring-assets"
    remove_container(name)
    try:
        command(["docker", "create", "--name", name, "--label", OWNER_LABEL + "=" + OWNER, image])
        command(["docker", "cp", name + ":/opt/sentinel-monitoring", str(release / "monitoring")])
    finally:
        remove_container(name)
    root = release / "monitoring"
    (root / "prometheus" / "prometheus.yml").write_text("""global:
  scrape_interval: 15s
  scrape_timeout: 10s
scrape_configs:
  - job_name: sentinel
    metrics_path: /internal/metrics
    authorization:
      credentials_file: /etc/prometheus/metrics.token
    static_configs:
      - targets: ['127.0.0.1:8080']
""", encoding="utf-8")
    secret = root / "prometheus" / "metrics.token"
    secret.write_text(token, encoding="utf-8")
    os.chmod(secret, 0o400)
    os.chown(secret, 65534, 65534)
    datasource = root / "grafana" / "provisioning" / "datasources" / "prometheus.yml"
    datasource.write_text(datasource.read_text().replace("http://prometheus:9090", "http://127.0.0.1:9090") + "    editable: false\n", encoding="utf-8")
    dashboard_path = root / "grafana" / "dashboards" / "sentinel-django-health.json"
    dashboard = json.loads(dashboard_path.read_text(encoding="utf-8"))
    # Grafana resolves slash-prefixed links beneath its own /grafana/ base.
    dashboard["links"][0]["url"] = "https://" + config["domain"] + "/"
    dashboard_path.write_text(json.dumps(dashboard, ensure_ascii=False), encoding="utf-8")
    (root / "grafana.ini").write_text("""[server]
http_addr = 127.0.0.1
http_port = 3000
domain = %s
root_url = https://%s/grafana/
serve_from_sub_path = true
[security]
disable_initial_admin_creation = true
cookie_secure = true
cookie_samesite = strict
allow_embedding = false
data_source_proxy_whitelist = 127.0.0.1:9090
[auth]
disable_login_form = true
disable_signout_menu = true
[auth.basic]
enabled = false
[auth.anonymous]
enabled = false
[auth.proxy]
enabled = true
header_name = X-Sentinel-User
header_property = username
auto_sign_up = true
whitelist = 127.0.0.1
enable_login_token = false
[users]
allow_sign_up = false
auto_assign_org_role = Viewer
default_theme = dark
[dashboards]
default_home_dashboard_path = /etc/sentinel-dashboards/sentinel-django-health.json
[analytics]
reporting_enabled = false
check_for_updates = false
check_for_plugin_updates = false
[news]
news_feed_enabled = false
[plugins]
preinstall_disabled = true
[unified_alerting]
enabled = false
[live]
max_connections = 0
[log]
mode = console
level = warn
""" % (config["domain"], config["domain"]), encoding="utf-8")
    # SSM deliberately uses umask 077. Grant only the Grafana process access
    # to its newly created config instead of depending on the runner's umask.
    os.chmod(root / "grafana.ini", 0o400)
    os.chown(root / "grafana.ini", 472, 472)
    provider = root / "grafana" / "provisioning" / "dashboards" / "sentinel.yml"
    provider.write_text(provider.read_text().replace("/var/lib/grafana/dashboards", "/etc/sentinel-dashboards"), encoding="utf-8")
    for service, uid in (("prometheus", 65534), ("grafana", 472)):
        data = BASE / (service + "-data")
        data.mkdir(exist_ok=True)
        os.chown(data, uid, uid)
        os.chmod(data, 0o700)
    for image_ref in (PROMETHEUS_IMAGE, GRAFANA_IMAGE):
        command(["docker", "pull", image_ref])
    command(["docker", "run", "--rm", "--network", "none", "--read-only", "--cap-drop", "ALL",
             "--mount", "type=bind,src=" + str(root / "prometheus") + ",dst=/etc/prometheus,readonly",
             "--entrypoint", "/bin/promtool", PROMETHEUS_IMAGE, "check", "config", "/etc/prometheus/prometheus.yml"])


def start_monitoring(name, release):
    prometheus = name == MONITORS[0]
    root = release / "monitoring"
    args = ["docker", "run", "-d", "--name", name, "--label", OWNER_LABEL + "=" + OWNER,
            "--restart", "unless-stopped", "--network", "host", "--read-only", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges:true", "--memory", "256m" if prometheus else "512m",
            "--cpus", "0.35" if prometheus else "0.5", "--pids-limit", "128",
            "--log-driver", "json-file", "--log-opt", "max-size=5m", "--log-opt", "max-file=3",
            "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m"]
    def mount(source, destination, readonly=True):
        args.extend(["--mount", "type=bind,src=" + str(source) + ",dst=" + destination + (",readonly" if readonly else "")])
    if prometheus:
        mount(root / "prometheus", "/etc/prometheus")
        mount(BASE / "prometheus-data", "/prometheus", False)
        args += [PROMETHEUS_IMAGE, "--config.file=/etc/prometheus/prometheus.yml", "--web.listen-address=127.0.0.1:9090",
                 "--storage.tsdb.path=/prometheus", "--storage.tsdb.retention.time=7d", "--storage.tsdb.retention.size=256MB",
                 "--query.max-concurrency=4", "--query.max-samples=100000", "--query.timeout=15s"]
    else:
        mount(root / "grafana.ini", "/etc/grafana/grafana.ini")
        mount(root / "grafana" / "provisioning", "/etc/grafana/provisioning")
        mount(root / "grafana" / "dashboards", "/etc/sentinel-dashboards")
        mount(BASE / "grafana-data", "/var/lib/grafana", False)
        args += [GRAFANA_IMAGE]
    command(args)


def monitoring_ready(username, timeout=90):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        collected = provisioned = False
        for name in MONITORS:
            info = container(name)
            state = info.get("State", {}) if info else {}
            MONITORING_DIAGNOSTICS[name] = {key: state.get(key) for key in ("Running", "OOMKilled", "ExitCode")}
        try:
            with urllib.request.urlopen("http://127.0.0.1:9090/api/v1/query?query=up%7Bjob%3D%22sentinel%22%7D", timeout=3) as response:
                result = json.load(response)["data"]["result"]
            collected = bool(result and result[0]["value"][1] == "1")
            MONITORING_DIAGNOSTICS["scrape_up"] = collected
            if not collected:
                with urllib.request.urlopen("http://127.0.0.1:9090/api/v1/targets", timeout=3) as response:
                    targets = json.load(response)["data"]["activeTargets"]
                # Only classify the failure; do not log URLs or response bodies.
                error = targets[0].get("lastError", "") if targets else ""
                MONITORING_DIAGNOSTICS["scrape_failure"] = next((code for code in ("401", "403", "404", "500", "connection refused", "parsing", "Content-Type") if code in error), "no sample")
        except (OSError, ValueError, KeyError, urllib.error.URLError) as error:
            MONITORING_DIAGNOSTICS["prometheus_error"] = type(error).__name__
        try:
            request = urllib.request.Request("http://127.0.0.1:3000/grafana/api/dashboards/uid/sentinel-django-health",
                                             headers={"X-Sentinel-User": username})
            with urllib.request.urlopen(request, timeout=3) as response:
                dashboard = json.load(response)["dashboard"]
            provisioned = dashboard["uid"] == "sentinel-django-health"
            MONITORING_DIAGNOSTICS["dashboard_provisioned"] = provisioned
        except (OSError, ValueError, KeyError, urllib.error.URLError) as error:
            MONITORING_DIAGNOSTICS["grafana_error"] = type(error).__name__
            if isinstance(error, urllib.error.HTTPError): MONITORING_DIAGNOSTICS["grafana_http_status"] = error.code
        if collected and provisioned:
            return True
        time.sleep(2)
    return False


def activate_monitoring(release, config, finalize):
    moved, started = [], []
    try:
        for name in MONITORS:
            if container(name):
                previous = container(name + "-previous")
                if previous and previous["State"]["Running"]:
                    raise DeploymentError("Previous monitoring service is still running")
                remove_container(name + "-previous")
                command(["docker", "stop", "--time", "15", name])
                # Include stopped-but-not-renamed containers in rollback too.
                moved.append((name, False))
                command(["docker", "rename", name, name + "-previous"])
                moved[-1] = (name, True)
            started.append(name)
            start_monitoring(name, release)
        if not monitoring_ready(config["username"]):
            raise DeploymentError("Grafana provisioning or Prometheus collection failed")
        finalize()
    except Exception:
        for name in reversed(started):
            remove_container(name)
        for name, renamed in reversed(moved):
            if renamed:
                remove_container(name)
                command(["docker", "rename", name + "-previous", name])
            command(["docker", "start", name])
        raise


def prune_releases():
    # Only this application's stopped images and config directories are eligible.
    retained = set()
    images = set()
    for name in (CURRENT, PREVIOUS, WEB_CURRENT, WEB_PREVIOUS, *MONITORS, *(n + "-previous" for n in MONITORS)):
        info = container(name)
        if info:
            images.add(info["Image"])
            for mount in info["Mounts"]:
                path = Path(mount["Source"]).resolve()
                retained.update(parent for parent in (path, *path.parents)
                                if parent.parent == (BASE / "releases").resolve())
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
    web_config = validate_web_config(manifest.get("public_web"))
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
    auth_path = release / "auth.json"
    token_path = BASE / "metrics.token"
    if not token_path.exists():
        token_path.write_text(secrets.token_urlsafe(32), encoding="utf-8")
        os.chmod(token_path, 0o600)
    token = token_path.read_text()
    if not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
        raise DeploymentError("Invalid private metrics credential")
    auth_path.write_text(json.dumps({"Enabled": True, "Username": web_config["username"],
        "PasswordHash": web_config["password_hash"], "ReadinessToken": secrets.token_urlsafe(32),
        "MetricsToken": token}) + "\n", encoding="utf-8")
    os.chmod(auth_path, 0o400)
    os.chown(auth_path, 1654, 1654)
    prepare_web(release, web_config)
    prepare_monitoring(image, release, web_config, token)
    activate(image, release, profile["platform_monitor"]["profile"],
             finalize=lambda: activate_monitoring(release, web_config, lambda: activate_web(release, web_config)))
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
        causes = []
        cause = error
        while cause is not None and len(causes) < 5:
            if isinstance(cause, DeploymentError): causes.append(str(cause))
            cause = cause.__cause__
        print("SENTINEL_DIAGNOSTIC " + json.dumps({"causes": causes, "monitoring": MONITORING_DIAGNOSTICS}))
        print(str(error) if isinstance(error, DeploymentError) else "Deployment failed: " + type(error).__name__)
        raise SystemExit(1)
