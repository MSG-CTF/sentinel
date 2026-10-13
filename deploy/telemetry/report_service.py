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
from urllib.parse import urlsplit


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
    return {
        "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
        "service_state": state, "restart_count": int(properties.get("NRestarts", 0)),
        "memory_used_percent": round(100 * (1 - memory["MemAvailable"] / memory["MemTotal"]), 1),
        "disk_used_percent": round(100 * disk.used / disk.total, 1),
        "load_one_minute": round(os.getloadavg()[0], 2),
        # Process state alone cannot prove the Discord gateway or ticket handlers work.
        "gateway_state": "unverified",
    }


def send(config, observation):
    url = urlsplit(config["endpoint"])
    if (url.scheme != "https" or not url.hostname or url.username or url.password or url.query or url.fragment
            or not re.fullmatch(r"/ingest/heartbeat/[a-z][a-z0-9_-]{0,63}", url.path)
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
