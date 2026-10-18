"""Authentication regressions, including the real pinned proxy in Docker."""

import base64
import gzip
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
    return {"domain": "sla.example.com", "username": "admin", "password_hash": 'pbkdf2-sha256$600000$AAAAAAAAAAAAAAAAAAAAAA==$AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA='}


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
        cases += [("username", "admin\nrespond 200"), ("password_hash", 'pbkdf2-sha256$600000$AAAAAAAAAAAAAAAAAAAAAA==$AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=' + "\n}")]
        for key, value in cases:
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                host.web_caddyfile({**fixture(), key: value})


@unittest.skipUnless(os.environ.get("SENTINEL_DOCKER_TESTS") == "1", "Set SENTINEL_DOCKER_TESTS=1 for proxy integration")
class WebProxyIntegrationTests(unittest.TestCase):
    def test_proxy_hides_internal_routes_and_limits_credential_forwarding(self):
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

        config = fixture()
        production = host.web_caddyfile(config)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            # Run the exact deployment preflight, including its restricted capabilities.
            host.prepare_web(root, config)
            asset_body = '/*' + 'public shader fixture ' * 300 + '*/'
            graphics = {
                '/vendor/three/three.module.js': 'text/javascript',
                '/sentinel-scene.js': 'application/javascript',
                '/login.css': 'text/css',
                '/assets/studio-small-09.hdr': 'image/vnd.radiance',
            }
            graphic_rules = ''.join(
                f' @graphic{index} path {path}\n'
                f' header @graphic{index} Cache-Control "public, max-age=0, must-revalidate"\n'
                f' header @graphic{index} Content-Type "{mime}"\n'
                f' respond @graphic{index} ' + json.dumps(asset_body) + ' 200\n'
                for index, (path, mime) in enumerate(graphics.items()))
            proxy_config = production.replace("https://sla.example.com", "http://:8088") + (
                '\nhttp://127.0.0.1:8080 {\n route {\n'
                ' handle /internal/grafana-auth {\n'
                '  @session header Cookie session=fixture\n'
                '  @anonymous not header Cookie session=fixture\n'
                '  header @session X-Sentinel-User admin\n'
                '  respond @session "" 200\n'
                '  redir @anonymous /login 302\n }\n' + graphic_rules +
                ' header /login.js Content-Type "text/javascript"\n'
                ' respond /login.js ' + json.dumps(asset_body) + ' 401\n'
                ' respond "upstream:{http.request.header.Authorization}|'
                '{http.request.header.Proxy-Authorization}" 200\n}\n}\n'
                'http://127.0.0.1:3000 {\n respond "grafana:{http.request.header.X-Sentinel-User}|'
                '{http.request.header.Cookie}|{http.request.header.Authorization}|'
                '{http.request.header.X-Panel-Title}|{http.request.header.X-Dashboard-Title}" 200\n}\n')
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

                def request(path, user=None, pwd=None, extra_headers=None):
                    headers = dict(extra_headers or {})
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
                # The proxy delegates browser sessions to ASP.NET and still hides internal routes.
                for path in ("/", "/login", "/api/status", "/metrics", "/healthz"):
                    status, headers, body = request(path, "admin", "fixture-unused-password")
                    self.assertEqual(status, 200)
                    self.assertEqual(body, b"upstream:|")
                    self.assertEqual(headers.get("cache-control"), "no-store")
                    self.assertNotIn("server", headers)
                for path, mime in graphics.items():
                    with self.subTest(compressed_asset=path):
                        status, headers, body = request(path, extra_headers={'Accept-Encoding': 'gzip'})
                        self.assertEqual(status, 200)
                        self.assertEqual(headers.get('content-type'), mime)
                        self.assertEqual(headers.get('cache-control'), 'public, max-age=0, must-revalidate')
                        self.assertEqual(headers.get('content-encoding'), 'gzip')
                        self.assertEqual(gzip.decompress(body), asset_body.encode())
                        self.assertLess(len(body), len(asset_body))
                        self.assertIn('Accept-Encoding', headers.get('vary', ''))
                for path in ('/api/status', '/auth/csrf', '/assets/unlisted.hdr', '/login.js'):
                    status, headers, body = request(path, extra_headers={'Accept-Encoding': 'gzip'})
                    self.assertEqual(status, 401 if path == '/login.js' else 200)
                    self.assertEqual(headers.get('cache-control'), 'no-store')
                    self.assertNotIn('content-encoding', headers)
                self.assertEqual(request("/internal/readiness")[0], 404)
                self.assertEqual(request("/internal/recovery")[0], 404)
                self.assertEqual(request("/internal")[0], 404)
                for path in ('/internal/metrics','/internal/grafana-auth'):
                    self.assertEqual(request(path)[0],404)
                for path in ('/grafana/', '/grafana/api/search', '/grafana/public/build/app.js'):
                    status, headers, body = request(path, extra_headers={'X-Sentinel-User':'forged'})
                    self.assertEqual(status,302,(path,headers,body))
                    self.assertEqual(headers['location'],'/login')
                    status, headers, body = request(path, extra_headers={
                        'Cookie':'session=fixture','X-Sentinel-User':'forged','Authorization':'Bearer forged',
                        'X-Panel-Title':'Django · DB · Redis','X-Dashboard-Title':'Sentinel · Operations'})
                    self.assertEqual(status,200)
                    self.assertEqual(body,b'grafana:admin||||')
                for path in ("/ingest/heartbeat/discord-bot", "/ingest/cluster/runtime-cluster"):
                    status, headers, body = request(path, "admin", "fixture-token")
                    self.assertEqual(status, 200)
                    self.assertIn(b"upstream:Basic ", body)
                    self.assertTrue(body.endswith(b"|"))
            finally:
                for name in reversed(created):
                    docker(["rm", "-f", name], required=False)


if __name__ == "__main__":
    unittest.main()
