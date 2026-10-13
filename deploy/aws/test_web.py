"""Authentication regressions, including the real pinned proxy in Docker."""

import base64
import http.client
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
import uuid

import deploy_host as host


def fixture():
    return {"domain": "sla.example.com", "username": "admin", "password_hash": "$2a$12$" + "a" * 53}


class WebConfigurationTests(unittest.TestCase):
    def test_missing_or_plaintext_credentials_refuse_deployment(self):
        for value in (None, {}, {**fixture(), "password_hash": "plaintext-password"},
                      {**fixture(), "password_hash": "$2a$04$" + "a" * 53}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                host.web_caddyfile(value)

    def test_config_cannot_inject_proxy_routes_or_accept_nonpublic_hosts(self):
        cases = [("domain", value) for value in (
            "http://sla.example.com", "127.0.0.1", "localhost", "site.local", "site.internal",
            "site.example.com\n}", "site.example.com:8080", "site..com", "-site.example.com")]
        cases += [("username", "admin\nrespond 200"), ("password_hash", "$2a$12$" + "a" * 53 + "\n}")]
        for key, value in cases:
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                host.web_caddyfile({**fixture(), key: value})


@unittest.skipUnless(os.environ.get("SENTINEL_DOCKER_TESTS") == "1", "Set SENTINEL_DOCKER_TESTS=1 for proxy integration")
class WebProxyIntegrationTests(unittest.TestCase):
    def test_authentication_covers_all_routes_and_credentials_never_reach_backend(self):
        suffix = uuid.uuid4().hex[:12]
        proxy = "sentinel-web-test-" + suffix
        created = []

        def docker(args, *, input=None, required=True):
            # Bytes preserve LF on Windows; CRLF would change a stdin password.
            result = subprocess.run(["docker", *args], input=input.encode() if input is not None else None,
                                    capture_output=True, timeout=180)
            result.stdout = result.stdout.decode()
            result.stderr = result.stderr.decode()
            if required and result.returncode:
                # Fixture-only diagnostics; these tests never read deployment secrets.
                self.fail(result.stderr)
            return result

        password = "fixture-only-long-administrator-password"
        hashed = docker(["run", "--rm", "-i", "--network", "none", host.WEB_IMAGE, "caddy",
                         "hash-password", "--algorithm", "bcrypt", "--bcrypt-cost", "12"], input=password + "\n").stdout.strip()
        config = {**fixture(), "password_hash": hashed}
        production = host.web_caddyfile(config)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            # Run the exact deployment preflight, including its restricted capabilities.
            host.prepare_web(root, config)
            proxy_config = production.replace("https://sla.example.com", "http://:8088") + (
                '\nhttp://127.0.0.1:8080 {\n respond "upstream:{http.request.header.Authorization}|'
                '{http.request.header.Proxy-Authorization}" 200\n}\n')
            (root / "Caddyfile").write_text(proxy_config, encoding="utf-8")
            try:
                docker(["run", "-d", "--name", proxy, "--network", "bridge",
                        "--read-only", "--cap-drop", "ALL", "--cap-add", "NET_BIND_SERVICE",
                        "--security-opt", "no-new-privileges:true",
                        "--tmpfs", "/config", "--tmpfs", "/data", "--publish", "127.0.0.1::8088",
                        "--mount", "type=bind,src=" + str(root / "Caddyfile") + ",dst=/etc/caddy/Caddyfile,readonly",
                        host.WEB_IMAGE, "caddy", "run", "--config", "/etc/caddy/Caddyfile"])
                created.append(proxy)
                info = json.loads(docker(["inspect", proxy]).stdout)[0]
                if not info["State"]["Running"]:
                    logs = docker(["logs", proxy], required=False)
                    self.fail(logs.stdout + logs.stderr)
                port = int(info["NetworkSettings"]["Ports"]["8088/tcp"][0]["HostPort"])

                def request(path, user=None, pwd=None):
                    headers = {}
                    if user is not None:
                        headers["Authorization"] = "Basic " + base64.b64encode((user + ":" + pwd).encode()).decode()
                        headers["Proxy-Authorization"] = "must-not-reach-upstream"
                    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
                    try:
                        conn.request("GET", path, headers=headers)
                        response = conn.getresponse()
                        return response.status, {k.lower(): v for k, v in response.getheaders()}, response.read()
                    finally:
                        conn.close()

                for attempt in range(30):
                    try:
                        request("/")
                        break
                    except (OSError, http.client.HTTPException):
                        if attempt == 29:
                            raise
                        time.sleep(0.2)
                for path in ("/", "/api/status", "/api/instances", "/api/events", "/api/recovery", "/metrics",
                             "/healthz", "/internal/recovery", "/not-a-real-route"):
                    with self.subTest(path=path):
                        status, headers, body = request(path)
                        self.assertEqual(status, 401)
                        self.assertIn("www-authenticate", headers)
                        self.assertEqual(headers.get("cache-control"), "no-store")
                        self.assertNotIn(b"upstream:", body)
                self.assertEqual(request("/api/status", "admin", "wrong-password")[0], 401)
                self.assertEqual(request("/api/status", "other", password)[0], 401)
                for path in ("/", "/api/status", "/metrics", "/healthz"):
                    status, headers, body = request(path, "admin", password)
                    self.assertEqual(status, 200)
                    self.assertEqual(body, b"upstream:|")
                    self.assertEqual(headers.get("cache-control"), "no-store")
                    self.assertNotIn("server", headers)
                self.assertEqual(request("/internal/recovery", "admin", password)[0], 404)
                self.assertEqual(request("/internal", "admin", password)[0], 404)
            finally:
                for name in reversed(created):
                    docker(["rm", "-f", name], required=False)


if __name__ == "__main__":
    unittest.main()
