"""Opt-in reporter for one owned, expiring administrator deployment test.

Run once per interval under an operator. Credentials are environment variables,
never workload headers. This does not discover, create, fault or repair instances.
"""
import argparse
from datetime import datetime, timezone, timedelta
import http.client
import json
import os
import re
import socket
import threading
from urllib.parse import urlsplit
import uuid

import challenge_probes as probes

BINDING_FIELDS = {"instance_id", "challenge_id", "release_id", "owner_team_id", "runtime_target_id",
                  "runtime_workload_id", "profile", "source_revision", "expires_at"}


def timestamp(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.utcoffset() is None:
        raise ValueError("Timestamp requires an offset")
    return parsed


def origin(value):
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username is not None
            or parsed.password is not None or parsed.port not in (None, 443)
            or parsed.path not in ("", "/") or "?" in value or "#" in value
            or any(c.isspace() or ord(c) < 32 for c in value)):
        raise ValueError("Control plane requires a configured HTTPS origin")
    return parsed.hostname


def validate(config):
    if set(config) != {"target_id", "binding", "scheduler_origin", "runtime_origin", "sentinel_origin",
                       "service_url", "expected_host", "scheduler_token_env", "runtime_token_env", "reporter_token_env"}:
        raise ValueError("Unexpected reporter configuration")
    binding = config["binding"]
    if set(binding) != BINDING_FIELDS:
        raise ValueError("Incomplete ownership binding")
    for key in ("instance_id", "challenge_id", "release_id", "owner_team_id"):
        if str(uuid.UUID(binding[key])) != binding[key] or uuid.UUID(binding[key]).int == 0:
            raise ValueError("Invalid owner identity")
    if (not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", config["target_id"])
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", binding["runtime_target_id"])
            or not isinstance(binding["runtime_workload_id"], str) or not 1 <= len(binding["runtime_workload_id"]) <= 256
            or any(ord(c) < 32 or ord(c) == 127 for c in binding["runtime_workload_id"])
            or binding["source_revision"] != probes.SOURCE_REVISION):
        raise ValueError("Invalid deployment binding")
    timestamp(binding["expires_at"])
    probes.endpoint(binding["profile"], config["service_url"], config["expected_host"])
    for field in ("scheduler_origin", "runtime_origin", "sentinel_origin"):
        origin(config[field])
    names = [config[field] for field in ("scheduler_token_env", "runtime_token_env", "reporter_token_env")]
    if any(not re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", value) for value in names) or len(set(names)) != 3:
        raise ValueError("Reporter credential must be dedicated")


def control(base, path, token_env, *, body=None):
    token = os.environ.get(token_env)
    if not token or any(ord(c) <= 32 or ord(c) >= 127 for c in token):
        raise ValueError("Control credential unavailable")
    connection = http.client.HTTPSConnection(origin(base), timeout=4)
    alarm = None
    headers = {"Authorization": "Bearer " + token, "Accept": "application/json", "Cache-Control": "no-store"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    try:
        connection.connect()
        transport = connection.sock
        def expire():
            try:
                transport.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        alarm = threading.Timer(8, expire)
        alarm.daemon = True
        alarm.start()
        connection.request("POST" if body is not None else "GET", path,
                           json.dumps(body) if body is not None else None, headers)
        response = connection.getresponse()
        if response.status != 200 or response.getheader("Content-Type", "").split(";")[0] != "application/json":
            raise ValueError("Control request rejected")
        data = response.read(262145)
        if len(data) > 262144:
            raise ValueError("Control response too large")
        return json.loads(data)
    finally:
        if alarm is not None:
            alarm.cancel()
        connection.close()


def bound(config, read, now):
    b = config["binding"]
    if not now < timestamp(b["expires_at"]) <= now + timedelta(hours=24):
        return "OWNERSHIP_BINDING_REJECTED"
    row = read(config["scheduler_origin"], "/api/instances/" + b["instance_id"], config["scheduler_token_env"])["data"]
    expected = {"instance_id": b["instance_id"], "challenge_id": b["challenge_id"], "release_id": b["release_id"],
                "team_id": b["owner_team_id"], "runtime_target_id": b["runtime_target_id"],
                "runtime_workload_id": b["runtime_workload_id"], "runtime_type": "KUBERNETES", "status": "RUNNING"}
    if any(row.get(key) != value for key, value in expected.items()):
        return "OWNERSHIP_BINDING_REJECTED"
    for key in ("expires_at", "hard_expires_at", "idle_expires_at"):
        if row.get(key) is not None and timestamp(row[key]) < timestamp(b["expires_at"]):
            return "OWNERSHIP_BINDING_REJECTED"
    if not any(item.get("service_url") == config["service_url"] for item in row.get("endpoints", [])):
        return "OWNERSHIP_BINDING_REJECTED"
    runtime = read(config["runtime_origin"], "/internal/v1/instances/" + b["instance_id"] + "/runtime-status",
                   config["runtime_token_env"])
    if any(runtime.get(key) != value for key, value in {"instance_id": b["instance_id"],
           "target_id": b["runtime_target_id"], "runtime_workload_id": b["runtime_workload_id"]}.items()):
        return "OWNERSHIP_BINDING_REJECTED"
    age = (now - timestamp(runtime["observed_at"])).total_seconds()
    if age < -5 or age > 60:
        return "CONTROL_PLANE_UNVERIFIED"
    return None if runtime.get("phase") == "READY" else "RUNTIME_NOT_READY"


def observe(config, read=control, probe=probes.check, clock=lambda: datetime.now(timezone.utc)):
    validate(config)
    b = config["binding"]
    result = {key: b[key] for key in BINDING_FIELDS - {"expires_at"}}
    result.update(scope="owned_deployment_test", status="CHECKER_ERROR", reason_code="CONTROL_PLANE_UNVERIFIED", elapsed_ms=0)
    try:
        failure = bound(config, read, clock())
        if failure is None:
            observation = probe(b["profile"], config["service_url"], config["expected_host"])
            # Do not attribute a response to an instance that disappeared or changed during the probe.
            failure = bound(config, read, clock())
            if failure is None:
                for key in ("status", "reason_code", "elapsed_ms"):
                    result[key] = observation[key]
        if failure is not None:
            result.update(status="CHECKER_ERROR", reason_code=failure)
    except (OSError, ValueError, KeyError, TypeError, http.client.HTTPException):
        result.update(status="CHECKER_ERROR", reason_code="CONTROL_PLANE_UNVERIFIED")
    result["checked_at"] = clock().isoformat()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    try:
        with open(args.config, encoding="utf-8") as source:
            config = json.load(source)
        result = observe(config)
        accepted = control(config["sentinel_origin"], "/ingest/challenge/" + config["target_id"],
                           config["reporter_token_env"], body=result)
        if accepted != {"accepted": True}:
            raise ValueError("Report not accepted")
        print(json.dumps(result))
    except Exception as error:
        print(json.dumps({"error": type(error).__name__, "reason_code": "REPORT_NOT_DELIVERED"}))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
