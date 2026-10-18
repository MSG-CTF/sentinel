"""Operator-run functional probes for explicitly owned, disposable deployment tests.

These checks do not solve challenges or operate on participant instances. Bind the
URL to a fresh authenticated Runtime observation before calling ``check``. No
response body, endpoint, cookie or credential is included in the returned result.
"""
from datetime import datetime, timezone
import http.client
import ipaddress
import json
import secrets
import socket
import struct
import time
from urllib.parse import urlencode, urlsplit

SOURCE_REVISION = "1dea73b652f9eeb98204c1fda7f66b9afb89505d"
PROFILES = {
    "web-grade-tampering": "http",
    "web-notebook": "http",
    "pwn-random6": "tcp",
    "pwn-note": "tcp",
    "pwn-ghost-file": "tcp",
    "pwn-price-tag": "tcp",
}
LIMIT = 32768


class ProbeFailure(Exception):
    def __init__(self, reason, status="DOWN"):
        self.reason, self.status = reason, status


def endpoint(profile, value, expected_host):
    """Numeric, public IPv4 NodePorts only; never follow a discovered arbitrary URL."""
    try:
        if (profile not in PROFILES or not isinstance(value, str) or len(value) > 512
                or not isinstance(expected_host, str)
                or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value)):
            raise ValueError()
        host = ipaddress.IPv4Address(expected_host)
        url = urlsplit(value)
        if (not host.is_global or host.is_multicast or url.scheme != PROFILES[profile] or url.hostname != str(host)
                or url.username is not None or url.password is not None
                or url.path not in {"", "/"} or "?" in value or "#" in value
                or not 30000 <= (url.port or 0) <= 32767):
            raise ValueError()
        return str(host), url.port
    except (TypeError, ValueError):
        raise ProbeFailure("ENDPOINT_BINDING_REJECTED", "CHECKER_ERROR") from None


def require(condition, reason="FUNCTION_RESPONSE_MISMATCH"):
    if not condition:
        raise ProbeFailure(reason)


def inspect(data):
    # Discard accidental secret-bearing output without returning or logging it.
    if b"MSG{" in data or b"FLAG " in data:
        raise ProbeFailure("SENSITIVE_RESPONSE_DISCARDED", "CHECKER_ERROR")


class Http:
    def __init__(self, host, port, deadline):
        self.host, self.port, self.deadline = host, port, deadline

    def request(self, method, path, data=None):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError()
        connection = http.client.HTTPConnection(self.host, self.port, timeout=min(4, remaining))
        headers = {"Accept": "application/json, text/html", "Cache-Control": "no-store", "User-Agent": "MSG-Sentinel-Functional/1"}
        if data is not None:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        try:
            connection.request(method, path, data, headers)
            response = connection.getresponse()
            if 300 <= response.status < 400:
                raise ProbeFailure("REDIRECT_BLOCKED", "CHECKER_ERROR")
            if response.status >= 500:
                raise ProbeFailure("FUNCTION_UNAVAILABLE")
            length = response.getheader("Content-Length")
            expected_length = None
            if length is not None:
                try:
                    expected_length = int(length)
                    if expected_length < 0 or expected_length > LIMIT:
                        raise ValueError()
                except ValueError:
                    raise ProbeFailure("RESPONSE_SIZE_INVALID", "CHECKER_ERROR") from None
            body = bytearray()
            while True:
                remaining = self.deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError()
                if connection.sock is not None:
                    connection.sock.settimeout(min(4, remaining))
                chunk = response.read1(min(4096, LIMIT + 1 - len(body)))
                if not chunk:
                    break
                body.extend(chunk)
                if len(body) > LIMIT:
                    raise ProbeFailure("RESPONSE_TOO_LARGE", "CHECKER_ERROR")
            if expected_length is not None and len(body) != expected_length:
                raise ProbeFailure("TRUNCATED_RESPONSE", "CHECKER_ERROR")
            inspect(body)
            return response.status, response.getheader("Content-Type", "").split(";")[0].lower(), bytes(body)
        finally:
            connection.close()


class Tcp:
    def __init__(self, host, port, deadline):
        self.deadline, self.buffer, self.received = deadline, bytearray(), 0
        self.connection = socket.create_connection((host, port), timeout=4)

    def close(self):
        self.connection.close()

    def timeout(self):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError()
        self.connection.settimeout(min(4, remaining))

    def send(self, value):
        self.timeout()
        self.connection.sendall(value)

    def until(self, marker):
        while marker not in self.buffer:
            self.timeout()
            chunk = self.connection.recv(min(4096, LIMIT + 1 - self.received))
            if not chunk:
                raise ProbeFailure("CONNECTION_CLOSED_BEFORE_RESPONSE")
            self.received += len(chunk)
            self.buffer.extend(chunk)
            if self.received > LIMIT:
                raise ProbeFailure("RESPONSE_TOO_LARGE", "CHECKER_ERROR")
            inspect(self.buffer)
        end = self.buffer.index(marker) + len(marker)
        value = bytes(self.buffer[:end])
        del self.buffer[:end]
        return value


def web(profile, client, nonce):
    if profile == "web-grade-tampering":
        code, content_type, body = client.request("GET", "/healthz")
        require(code == 200 and content_type == "application/json" and json.loads(body) == {"status": "ok"})
        code, content_type, body = client.request("GET", "/api/notices/1")
        require(code == 200 and content_type == "application/json")
        data = json.loads(body)
        require(isinstance(data, dict) and type(data.get("id")) is int and data["id"] == 1
                and isinstance(data.get("title"), str) and bool(data["title"])
                and isinstance(data.get("author"), dict) and isinstance(data["author"].get("name"), str)
                and bool(data["author"]["name"]))
        code, _, body = client.request("GET", "/api/notices/2")
        require(code == 404 and json.loads(body) == {"error": "notice not found"})
        return "NOTICE_DATABASE_READ_CONFIRMED"
    code, content_type, body = client.request("GET", "/")
    require(code == 200 and content_type == "text/html" and b'action="/login"' in body and b'name="username"' in body)
    data = urlencode({"username": "sentinel_" + nonce, "password": nonce}).encode()
    code, _, body = client.request("POST", "/login", data)
    require(code == 401 and body == b"login failed")
    code, _, body = client.request("GET", "/notes")
    require(code == 403 and body == b"admin only")
    return "DATABASE_LOGIN_AND_ACCESS_RULES_CONFIRMED"


def tcp(profile, client, nonce):
    if profile == "pwn-random6":
        require(client.until(b"\n") == b"random6 ready\n")
        number = int(nonce[:7], 16)
        # R1 = nonce; R1 += 7; EMIT(R1); R0 = 0; EXIT. No memory/session helpers.
        instruction = lambda op, dst, imm: struct.pack("<BBBBi", op, dst, 0, 0, imm)
        code = b"".join([instruction(1, 1, number), instruction(5, 1, 7), instruction(0x30, 0, 4),
                         instruction(1, 0, 0), instruction(0x31, 0, 0)])
        client.send(struct.pack(">I", len(code)) + code + struct.pack(">I", 0))
        require(client.until(b"\n") == f"EMIT {number + 7:016x}\n".encode())
        require(client.until(b"\n") == b"EXIT\n")
        return "VM_NONCE_ARITHMETIC_CONFIRMED"
    if profile == "pwn-note":
        require(b"Simple Note Program" in client.until(b"> "))
        client.send(b"1\n"); client.until(b"Enter your name: ")
        value = ("sla_" + nonce[:12]).encode()
        client.send(value + b"\n"); client.until(b"How many bytes should I display back to you? ")
        client.send(str(len(value)).encode() + b"\n")
        require(b"Hello, " + value + b"!\n" in client.until(b"> "))
        client.send(b"2\n"); client.until(b"Leave a message: ")
        client.send(value + b"\n"); require(b"Message saved." in client.until(b"> "))
        client.send(b"3\n"); require(b"Bye!" in client.until(b"\n"))
        return "NAME_ROUNDTRIP_AND_MESSAGE_CONFIRMED"
    if profile == "pwn-ghost-file":
        require(b"1. Create" in client.until(b"> "))
        value = ("sla_" + nonce).encode().ljust(0x1D8, b".")
        client.send(b"1\n"); client.until(b"index: "); client.send(b"0\n")
        client.until(b"content: "); client.send(value)
        require(b"created" in client.until(b"> "))
        client.send(b"2\n"); client.until(b"index: "); client.send(b"0\n")
        client.until(b"offset: "); client.send(b"0\n")
        client.until(b"patch length (1-6): "); client.send(b"6\n")
        client.until(b"patch: "); client.send(b"SLA_OK")
        require(b"patched (1/8 edits used)" in client.until(b"> "))
        client.send(b"4\n"); client.until(b"index: "); client.send(b"0\n")
        require(client.until(b"> ").startswith(b"SLA_OK" + value[6:] + b"\n"))
        client.send(b"3\n"); client.until(b"index: "); client.send(b"0\n")
        require(b"deleted" in client.until(b"> "))
        client.send(b"7\n")
        return "OWNED_NOTE_CREATE_EDIT_READ_DELETE_CONFIRMED"
    require(b"[0] Product 1 Price: 100 Won" in client.until(b"Enter product index to modify: "))
    client.send(b"0\n"); client.until(b"Enter new price: ")
    client.send(str(100 + int(nonce[:4], 16) % 900).encode() + b"\n")
    require(b"[+] Price updated." in client.until(b"[-] Access denied.\n"))
    return "VALID_PRODUCT_UPDATE_AND_ACCESS_RULE_CONFIRMED"


def check(profile, service_url, expected_host):
    started = time.monotonic()
    result = {"profile": profile if isinstance(profile, str) and profile in PROFILES else "unknown", "scope": "owned_deployment_test",
              "status": "CHECKER_ERROR", "reason_code": "NOT_CHECKED", "source_revision": SOURCE_REVISION}
    client = None
    try:
        host, port = endpoint(profile, service_url, expected_host)
        nonce = secrets.token_hex(12)
        deadline = time.monotonic() + 20
        if PROFILES[profile] == "http":
            reason = web(profile, Http(host, port, deadline), nonce)
        else:
            client = Tcp(host, port, deadline)
            reason = tcp(profile, client, nonce)
        result.update(status="UP", reason_code=reason)
    except ProbeFailure as error:
        result.update(status=error.status, reason_code=error.reason)
    except (TimeoutError, socket.timeout):
        result.update(status="DOWN", reason_code="FUNCTION_TIMEOUT")
    except (OSError, http.client.HTTPException):
        result.update(status="DOWN", reason_code="FUNCTION_UNREACHABLE")
    except (ValueError, TypeError, KeyError):
        result.update(status="CHECKER_ERROR", reason_code="INVALID_FUNCTION_RESPONSE")
    finally:
        if client is not None:
            client.close()
    result.update(checked_at=datetime.now(timezone.utc).isoformat(), elapsed_ms=round((time.monotonic() - started) * 1000))
    return result
