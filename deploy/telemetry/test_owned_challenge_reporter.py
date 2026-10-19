import copy
from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import Mock

import owned_challenge_reporter as reporter


class ReporterTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 19, 3, tzinfo=timezone.utc)
        self.binding = {"instance_id": "11111111-1111-4111-8111-111111111111",
                        "challenge_id": "22222222-2222-4222-8222-222222222222",
                        "release_id": "33333333-3333-4333-8333-333333333333",
                        "owner_team_id": "44444444-4444-4444-8444-444444444444",
                        "runtime_target_id": "owned-cluster", "runtime_workload_id": "owned-cluster/namespace",
                        "profile": "web-notebook", "source_revision": reporter.probes.SOURCE_REVISION,
                        "expires_at": (self.now + timedelta(minutes=15)).isoformat()}
        self.config = {"target_id": "owned-notebook", "binding": self.binding, "scheduler_origin": "https://scheduler.test",
                       "runtime_origin": "https://runtime.test", "sentinel_origin": "https://sentinel.test",
                       "service_url": "http://8.8.8.8:30080/", "expected_host": "8.8.8.8",
                       "scheduler_token_env": "TEST_SCHEDULER", "runtime_token_env": "TEST_RUNTIME",
                       "reporter_token_env": "TEST_REPORTER"}
        self.row = {"instance_id": self.binding["instance_id"], "challenge_id": self.binding["challenge_id"],
                    "release_id": self.binding["release_id"], "team_id": self.binding["owner_team_id"],
                    "runtime_target_id": "owned-cluster", "runtime_workload_id": "owned-cluster/namespace",
                    "runtime_type": "KUBERNETES", "status": "RUNNING", "expires_at": self.binding["expires_at"],
                    "hard_expires_at": self.binding["expires_at"], "idle_expires_at": None,
                    "endpoints": [{"service_url": self.config["service_url"]}]}
        self.runtime = {"instance_id": self.binding["instance_id"], "target_id": "owned-cluster",
                        "runtime_workload_id": "owned-cluster/namespace", "phase": "READY", "observed_at": self.now.isoformat()}
        self.probe = Mock(return_value={"status": "UP", "reason_code": "DATABASE_LOGIN_AND_ACCESS_RULES_CONFIRMED", "elapsed_ms": 20})
        self.calls = []

    def read(self, base, path, token_env):
        self.calls.append((base, path, token_env))
        if base == self.config["scheduler_origin"]:
            return {"data": self.row}
        self.assertEqual(base, self.config["runtime_origin"])
        return self.runtime

    def observe(self):
        return reporter.observe(self.config, self.read, self.probe, lambda: self.now)

    def test_fresh_owned_identity_is_checked_before_and_after_function(self):
        result = self.observe()
        self.assertEqual(result["status"], "UP")
        self.assertEqual(len(self.calls), 4)
        self.probe.assert_called_once_with("web-notebook", self.config["service_url"], self.config["expected_host"])
        self.assertNotIn("service_url", result)
        self.assertNotIn("expires_at", result)
        self.assertNotIn("token", str(result))

    def test_every_identity_and_lease_mismatch_prevents_workload_requests(self):
        for key in ("instance_id", "challenge_id", "release_id", "team_id", "runtime_target_id", "runtime_workload_id",
                    "runtime_type", "status", "endpoints", "expires_at", "hard_expires_at", "idle_expires_at"):
            with self.subTest(key=key):
                before = copy.deepcopy(self.row)
                self.row[key] = [] if key == "endpoints" else self.now.isoformat() if key.endswith("expires_at") else "other"
                self.assertEqual(self.observe()["status"], "CHECKER_ERROR")
                self.probe.assert_not_called()
                self.row = before

    def test_runtime_identity_staleness_and_not_ready_cannot_restore_an_incident(self):
        for key in ("instance_id", "target_id", "runtime_workload_id", "phase", "observed_at"):
            with self.subTest(key=key):
                before = copy.deepcopy(self.runtime)
                self.runtime[key] = (self.now - timedelta(seconds=61)).isoformat() if key == "observed_at" else "other"
                self.assertEqual(self.observe()["status"], "CHECKER_ERROR")
                self.probe.assert_not_called()
                self.runtime = before

    def test_identity_change_during_probe_discards_success(self):
        def changed(*args):
            self.row["release_id"] = "changed"
            return {"status": "UP", "reason_code": "DATABASE_LOGIN_AND_ACCESS_RULES_CONFIRMED", "elapsed_ms": 20}
        self.probe.side_effect = changed
        self.assertEqual(self.observe()["reason_code"], "OWNERSHIP_BINDING_REJECTED")

    def test_failure_is_reported_without_response_data_and_control_errors_are_unknown(self):
        self.probe.return_value = {"status": "DOWN", "reason_code": "FUNCTION_RESPONSE_MISMATCH", "elapsed_ms": 20,
                                   "body": "must never escape"}
        result = self.observe()
        self.assertEqual(result["status"], "DOWN")
        self.assertNotIn("body", result)
        result = reporter.observe(self.config, Mock(side_effect=ValueError("secret")), self.probe, lambda: self.now)
        self.assertEqual(result["reason_code"], "CONTROL_PLANE_UNVERIFIED")
        self.assertNotIn("secret", str(result))

    def test_expiry_future_lease_and_invalid_config_never_probe(self):
        for delta in (timedelta(seconds=-1), timedelta(hours=25)):
            self.binding["expires_at"] = (self.now + delta).isoformat()
            self.assertEqual(self.observe()["status"], "CHECKER_ERROR")
            self.probe.assert_not_called()
        self.binding["expires_at"] = (self.now + timedelta(minutes=15)).isoformat()
        for key, value in (("service_url", "http://127.0.0.1:30080/"), ("runtime_origin", "http://runtime.test"),
                           ("scheduler_origin", "https://user:password@scheduler.test/"),
                           ("reporter_token_env", "TEST_RUNTIME")):
            with self.subTest(key=key), self.assertRaises((ValueError, reporter.probes.ProbeFailure)):
                reporter.validate({**self.config, key: value})


if __name__ == "__main__":
    unittest.main()
