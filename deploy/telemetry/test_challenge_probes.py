import json
import socket
import struct
import threading
import time
import unittest
from unittest.mock import patch

import challenge_probes as probes

HOST = "34.134.53.193"
NONCE = "1234567890abcdef12345678"


class Socket:
    def __init__(self, data, fragment=3):
        self.data, self.sent, self.closed, self.fragment = bytearray(data), bytearray(), False, fragment

    def settimeout(self, value):
        pass

    def recv(self, size):
        count = min(size, self.fragment, len(self.data))
        chunk = bytes(self.data[:count]); del self.data[:count]
        return chunk

    def sendall(self, data):
        self.sent.extend(data)

    def close(self):
        self.closed = True

    def shutdown(self, how):
        pass


class Response:
    def __init__(self, code, body, content_type="text/html", length=None):
        self.status, self.body, self.content_type, self.length = code, bytearray(body), content_type, length

    def getheader(self, key, default=None):
        return {"Content-Type": self.content_type, "Content-Length": self.length}.get(key, default)

    def read1(self, count):
        chunk = bytes(self.body[:min(count, 7)]); del self.body[:len(chunk)]
        return chunk


class Connection:
    def __init__(self, responses):
        self.responses, self.requests, self.closed, self.sock = responses, [], False, Socket(b"")

    def connect(self):
        pass

    def request(self, *args):
        self.requests.append(args)

    def getresponse(self):
        return self.responses.pop(0)

    def close(self):
        self.closed = True


class ChallengeProbeTests(unittest.TestCase):
    def tcp(self, profile, content):
        connection = Socket(content)
        with patch.object(probes.socket, "create_connection", return_value=connection), patch.object(probes.secrets, "token_hex", return_value=NONCE):
            result = probes.check(profile, f"tcp://{HOST}:30080", HOST)
        self.assertTrue(connection.closed)
        return result, connection

    def http(self, profile, responses):
        connection = Connection(responses)
        with patch.object(probes.http.client, "HTTPConnection", return_value=connection), patch.object(probes.secrets, "token_hex", return_value=NONCE):
            result = probes.check(profile, f"http://{HOST}:30080", HOST)
        self.assertTrue(connection.closed)
        for request in connection.requests:
            self.assertNotIn("Authorization", request[3]); self.assertNotIn("Cookie", request[3])
        return result, connection

    def test_binding_rejects_nonpublic_wrong_host_protocol_ports_and_ambiguous_urls_before_io(self):
        values = ["http://127.0.0.1:30080", "http://169.254.169.254:30080", "http://10.0.0.1:30080",
                  f"http://{HOST}:443", f"http://{HOST}:32768", f"http://{HOST}:30080/?x=1",
                  f"http://{HOST}:30080/#", f"http://admin@{HOST}:30080", f"http://{HOST}:30080/other",
                  f"http://{HOST}:30080\n", "http://example.com:30080", f"tcp://{HOST}:30080",
                  f"\x00http://{HOST}:30080", f"http://{HOST}:30080\x7f"]
        with patch.object(probes.http.client, "HTTPConnection") as factory:
            for value in values:
                with self.subTest(value=value):
                    result = probes.check("web-notebook", value, HOST)
                    self.assertEqual((result["status"], result["reason_code"]), ("CHECKER_ERROR", "ENDPOINT_BINDING_REJECTED"))
            self.assertEqual(probes.check("unknown", f"http://{HOST}:30080", HOST)["status"], "CHECKER_ERROR")
            self.assertEqual(probes.check({}, f"http://{HOST}:30080", HOST)["status"], "CHECKER_ERROR")
            self.assertEqual(probes.check("web-notebook", "http://224.0.0.1:30080", "224.0.0.1")["status"], "CHECKER_ERROR")
            factory.assert_not_called()

    def test_random_vm_requires_fresh_computed_value_and_exit_with_bounded_program(self):
        number = int(NONCE[:7], 16)
        result, connection = self.tcp("pwn-random6", f"random6 ready\nEMIT {number + 7:016x}\nEXIT\n".encode())
        self.assertEqual(result["status"], "UP")
        self.assertEqual(struct.unpack(">I", connection.sent[:4])[0], 40)
        program = connection.sent[4:44]
        calls = [struct.unpack("<BBBBi", program[i:i + 8]) for i in range(0, 40, 8)]
        self.assertEqual([i[4] for i in calls if i[0] == 0x30], [4])
        result, _ = self.tcp("pwn-random6", b"random6 ready\nEMIT 000000000000002a\nEXIT\n")
        self.assertEqual(result["status"], "DOWN")

    def test_simple_note_normal_lengths_and_message_roundtrip(self):
        value = ("sla_" + NONCE[:12]).encode()
        result, connection = self.tcp("pwn-note", b"Simple Note Program> Enter your name: How many bytes should I display back to you? Hello, " + value + b"!\n> Leave a message: Message saved.> Bye!\n")
        self.assertEqual(result["status"], "UP")
        self.assertEqual(connection.sent, b"1\n" + value + b"\n16\n2\n" + value + b"\n3\n")
        result, _ = self.tcp("pwn-note", b"Simple Note Program> Enter your name: How many bytes should I display back to you? Hello, wrong!\n> ")
        self.assertEqual(result["status"], "DOWN")

    def test_ghost_file_uses_only_one_allocated_fully_written_note(self):
        value = ("sla_" + NONCE).encode().ljust(0x1D8, b".")
        data = b"1. Create> index: content: created> index: offset: patch length (1-6): patch: patched (1/8 edits used)> index: SLA_OK" + value[6:] + b"\n> index: deleted> "
        result, connection = self.tcp("pwn-ghost-file", data)
        self.assertEqual(result["status"], "UP")
        self.assertIn(value, connection.sent)
        self.assertTrue(connection.sent.endswith(b"3\n0\n7\n"))
        self.assertNotIn(b"5\n", connection.sent)  # Archive and hint operations are never used.

    def test_price_tag_updates_only_valid_index_without_authorized_branch(self):
        result, connection = self.tcp("pwn-price-tag", b"[0] Product 1 Price: 100 Won\nEnter product index to modify: Enter new price: [+] Price updated.\n[-] Access denied.\n")
        self.assertEqual(result["status"], "UP")
        self.assertTrue(connection.sent.startswith(b"0\n"))
        self.assertNotIn(b"-", connection.sent)

    def test_tcp_sensitive_oversized_closed_and_timeout_results_never_echo_content(self):
        for data, expected in [(b"MSG{do_not_log}", "SENSITIVE_RESPONSE_DISCARDED"),
                               (b"FLAG secret\n", "SENSITIVE_RESPONSE_DISCARDED"),
                               (b"x" * (probes.LIMIT + 1), "RESPONSE_TOO_LARGE"),
                               (b"", "CONNECTION_CLOSED_BEFORE_RESPONSE")]:
            with self.subTest(expected=expected):
                result, _ = self.tcp("pwn-note", data)
                self.assertEqual(result["reason_code"], expected)
                self.assertNotIn("do_not_log", json.dumps(result))
        with patch.object(probes.socket, "create_connection", side_effect=TimeoutError):
            self.assertEqual(probes.check("pwn-note", f"tcp://{HOST}:30080", HOST)["reason_code"], "FUNCTION_TIMEOUT")
        client = probes.Tcp.__new__(probes.Tcp); client.deadline = time.monotonic() - 1
        with self.assertRaises(TimeoutError): client.timeout()

    def test_grade_queries_actual_database_route_and_expected_negative_response(self):
        responses = [Response(200, b'{"status":"ok"}', "application/json"),
                     Response(200, b'{"id":1,"title":"notice","author":{"name":"staff"}}', "application/json"),
                     Response(404, b'{"error":"notice not found"}', "application/json")]
        result, connection = self.http("web-grade-tampering", responses)
        self.assertEqual(result["status"], "UP")
        self.assertEqual(connection.requests[0][1], "/healthz")

    def test_notebook_requires_real_login_query_outcome_and_access_rule(self):
        responses = [Response(200, b'<form action="/login"><input name="username"></form>'),
                     Response(401, b"login failed"), Response(403, b"admin only")]
        result, connection = self.http("web-notebook", responses)
        self.assertEqual(result["status"], "UP")
        self.assertIn(("sentinel_" + NONCE).encode(), connection.requests[1][2])
        self.assertNotIn(b"'", connection.requests[1][2])
        self.assertEqual(self.http("web-notebook", [Response(200, b'<form action="/login"><input name="username"></form>'), Response(500, b"db down")])[0]["status"], "DOWN")

    def test_http_redirect_limits_schema_and_sensitive_response_fail_closed(self):
        cases = [(Response(302, b""), "REDIRECT_BLOCKED"),
                 (Response(200, b"", length="1000000"), "RESPONSE_SIZE_INVALID"),
                 (Response(200, b"", length="not-a-size"), "RESPONSE_SIZE_INVALID"),
                 (Response(200, b'{"status":"ok"}', "application/json", length="100"), "TRUNCATED_RESPONSE"),
                 (Response(200, b"x" * (probes.LIMIT + 1)), "RESPONSE_TOO_LARGE"),
                 (Response(200, b"MSG{do_not_log}"), "SENSITIVE_RESPONSE_DISCARDED"),
                 (Response(200, b"not-json", "application/json"), "INVALID_FUNCTION_RESPONSE")]
        for response, reason in cases:
            with self.subTest(reason=reason):
                result, connection = self.http("web-grade-tampering", [response])
                self.assertEqual(result["reason_code"], reason)
                self.assertEqual(len(connection.requests), 1)
                self.assertNotIn("do_not_log", json.dumps(result))

    def test_absolute_http_deadline_stops_dripping_headers_and_body(self):
        payloads = [b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok",
                    b"HTTP/1.1 200 OK\r\nContent-Length: 100\r\n\r\n" + b"x" * 100]
        for index, payload in enumerate(payloads):
            with self.subTest(part="headers" if index == 0 else "body"):
                listener = socket.socket(); listener.bind(("127.0.0.1", 0)); listener.listen(1); listener.settimeout(1)
                port = listener.getsockname()[1]

                def server():
                    try:
                        with listener.accept()[0] as peer:
                            peer.recv(4096)
                            initial = 0 if index == 0 else payload.index(b"\r\n\r\n") + 4
                            if initial:
                                peer.sendall(payload[:initial])
                            for byte in payload[initial:]:
                                peer.sendall(bytes([byte])); time.sleep(0.1)
                    except OSError:
                        pass

                thread = threading.Thread(target=server, daemon=True); thread.start()
                started = time.monotonic()
                try:
                    client = probes.Http("127.0.0.1", port, started + 0.15)
                    with self.assertRaises(TimeoutError): client.request("GET", "/")
                    self.assertLess(time.monotonic() - started, 2)
                finally:
                    listener.close(); thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
