#!/usr/bin/env python3
"""Report a fixed systemd service's state; never read bot tokens or message data."""
import datetime
import http.client
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import platform
import shlex
import time
from urllib.parse import urlsplit


def cpu_ticks():
    values = [int(value) for value in Path('/proc/stat').read_text().splitlines()[0].split()[1:9]]
    return sum(values), values[3] + values[4]


def cpu_percent(before, after):
    total, idle = after[0] - before[0], after[1] - before[1]
    if total <= 0 or idle < 0 or idle > total:
        raise ValueError('Invalid CPU sample')
    return round(100 * (total - idle) / total, 1)


def collect(service):
    if not re.fullmatch(r"[A-Za-z0-9_.@-]{1,100}\.service", service):
        raise ValueError("Invalid service name")
    result = subprocess.run(["systemctl", "show", service, "--property=ActiveState", "--property=NRestarts"],
                            capture_output=True, text=True, check=True, timeout=5)
    properties = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    state = properties.get("ActiveState")
    if state not in {"active", "inactive", "failed", "activating", "deactivating"}:
        raise ValueError("Service state is unavailable")
    memory = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        key, value = line.split(":", 1)
        if key in {"MemTotal", "MemAvailable"}: memory[key] = int(value.split()[0])
    disk = shutil.disk_usage("/")
    before = cpu_ticks()
    time.sleep(1)
    cpu = cpu_percent(before, cpu_ticks())
    os_info = {}
    for line in Path('/etc/os-release').read_text().splitlines():
        if '=' in line and not line.startswith('#'):
            key, value = line.split('=', 1)
            parsed = shlex.split(value)
            if parsed: os_info[key] = parsed[0]
    model = next((line.split(':', 1)[1].strip() for line in Path('/proc/cpuinfo').read_text().splitlines()
                  if line.startswith('model name')), platform.machine())
    return {
        "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
        "service_state": state, "restart_count": int(properties.get("NRestarts", 0)),
        "memory_used_percent": round(100 * (1 - memory["MemAvailable"] / memory["MemTotal"]), 1),
        "disk_used_percent": round(100 * disk.used / disk.total, 1),
        "load_one_minute": round(os.getloadavg()[0], 2),
        "cpu_used_percent": cpu, "cpu_logical_count": os.cpu_count(), "cpu_model": model[:160],
        "os_name": os_info.get('PRETTY_NAME', platform.system())[:160],
        "architecture": platform.machine()[:32], "hostname": platform.node()[:128],
        "memory_total_bytes": memory['MemTotal'] * 1024,
        "memory_used_bytes": (memory['MemTotal'] - memory['MemAvailable']) * 1024,
        "disk_total_bytes": disk.total, "disk_used_bytes": disk.used,
        "uptime_seconds": int(float(Path('/proc/uptime').read_text().split()[0])),
        # Process state alone cannot prove the Discord gateway or ticket handlers work.
        "gateway_state": "unverified",
    }


def send(config, observation):
    url = urlsplit(config["endpoint"])
    if (url.scheme != "https" or not url.hostname or url.username or url.password or url.query or url.fragment
            or not re.fullmatch(r"/ingest/(?:heartbeat|cluster)/[a-z][a-z0-9_-]{0,63}", url.path)
            or not isinstance(config.get("token"), str) or len(config["token"]) < 32):
        raise ValueError("Invalid telemetry destination")
    conn = http.client.HTTPSConnection(url.hostname, url.port or 443, timeout=10)
    try:
        conn.request("POST", url.path, json.dumps(observation), headers={
            "Authorization": "Bearer " + config["token"], "Content-Type": "application/json",
            "User-Agent": "MSG-Sentinel-ServiceReporter/1.0",
        })
        response = conn.getresponse()
        if response.status != 200: raise RuntimeError("Telemetry delivery was not accepted")
        if json.loads(response.read(4096)).get("accepted") is not True:
            raise RuntimeError("Invalid telemetry acknowledgement")
    finally:
        conn.close()


if __name__ == "__main__":
    try:
        path = Path(os.environ["CREDENTIALS_DIRECTORY"]) / "reporter.json"
        config = json.loads(path.read_text())
        send(config, collect(config["service"]))
        print("Service heartbeat accepted")
    except Exception as error:
        # Do not log request headers, response bodies, or private configuration.
        print("Service heartbeat failed: " + type(error).__name__)
        raise SystemExit(1)
