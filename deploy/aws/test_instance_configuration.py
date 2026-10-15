import json
import os
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

import release


def profile():
    return {
        "platform_monitor": {"profile": "aws", "event_file": "data/events.jsonl", "targets": [
            {"target_id": "web", "url": "https://example.com/"},
            {"target_id": "bot", "kind": "service_heartbeat", "auth_token_env": "BOT_TOKEN"}]},
        "platform_recovery": {"enabled": False},
        "instance_monitor": {"enabled": True, "scheduler_url": "https://scheduler.example.com/api/instances"}}


def credentials():
    return {"BOT_TOKEN": "b" * 40, "SCHEDULER_API_TOKEN": "test-scheduler-token"}


class InventoryConfigurationTests(unittest.TestCase):
    def test_https_inventory_and_existing_monitoring_credentials_are_combined(self):
        config = profile()
        config["platform_monitor"]["targets"].append({
            "target_id": "scheduler-list", "kind": "scheduler_inventory",
            "url": config["instance_monitor"]["scheduler_url"], "auth_token_env": "SCHEDULER_API_TOKEN"})
        self.assertEqual(release.validate_configuration(config, credentials()),
                         "BOT_TOKEN=" + "b" * 40 + "\nSCHEDULER_API_TOKEN=test-scheduler-token\n")

    def test_explicit_runtime_origins_and_custom_credentials_are_supported(self):
        config = profile()
        config["instance_monitor"].update(scheduler_token_env="SCHEDULER_READ_TOKEN", runtime_targets=[
            {"target_id": "k3s-a", "base_url": "https://runtime.example.com:8443/", "auth_token_env": "RUNTIME_TOKEN"},
            {"target_id": "k3s-b", "base_url": "https://runtime-b.example.com", "auth_token_env": "RUNTIME_TOKEN"}])
        secrets = {"BOT_TOKEN": "b" * 40, "SCHEDULER_READ_TOKEN": "scheduler", "RUNTIME_TOKEN": "runtime"}
        self.assertEqual(release.validate_configuration(config, secrets),
                         "BOT_TOKEN=" + "b" * 40 + "\nRUNTIME_TOKEN=runtime\nSCHEDULER_READ_TOKEN=scheduler\n")

    def test_missing_or_unused_credentials_block_enabled_inventory(self):
        for secrets in ({"BOT_TOKEN": "b" * 40}, {**credentials(), "UNUSED_TOKEN": "unused"}):
            with self.subTest(names=sorted(secrets)), self.assertRaises(ValueError):
                release.validate_configuration(profile(), secrets)

    def test_scheduler_must_use_full_inventory_path_over_explicit_remote_https(self):
        for endpoint in (None, 42, "", "http://scheduler.example.com/api/instances",
                         "https://scheduler.example.com/api/instances/recent", "https://scheduler.example.com/",
                         "https://scheduler.example.com/api/instances?limit=20", "https://scheduler.example.com/api/instances#secret",
                         "https://user:secret@scheduler.example.com/api/instances", "https://@scheduler.example.com/api/instances",
                         "https://scheduler.example.com:0/api/instances", "https://scheduler.example.com:65536/api/instances",
                         "https://scheduler.example.com:bad/api/instances", "https://scheduler.example.com\n/api/instances",
                         " https://scheduler.example.com/api/instances", "https://scheduler.example.com/a/../api/instances"):
            with self.subTest(endpoint=endpoint), self.assertRaises(ValueError):
                config = profile()
                config["instance_monitor"]["scheduler_url"] = endpoint
                release.validate_configuration(config, credentials())

    def test_local_metadata_and_ambiguous_ip_spellings_are_rejected(self):
        for host in ("localhost", "LOCALHOST.", "other.localhost", "host.docker.internal", "127.0.0.2",
                     "127.1", "2130706433", "0x7f000001", "[::1]", "[::ffff:127.0.0.1]", "0.0.0.0",
                     "[::]", "169.254.169.254", "[fe80::1]", "invalid_host.example.com"):
            with self.subTest(host=host), self.assertRaises(ValueError):
                config = profile()
                config["instance_monitor"]["scheduler_url"] = "https://" + host + "/api/instances"
                release.validate_configuration(config, credentials())

    def test_bounded_polling_and_explicit_boolean_avoid_binder_coercion(self):
        for field, values in {"enabled": (None, "true", 1, 0), "poll_interval_seconds": (4, 301, "30", True, 30.5),
                              "timeout_seconds": (0, 11, "4", False, 4.5)}.items():
            for value in values:
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    config = profile()
                    config["instance_monitor"][field] = value
                    release.validate_configuration(config, credentials())
        for interval, timeout in ((5, 1), (300, 10)):
            config = profile()
            config["instance_monitor"].update(poll_interval_seconds=interval, timeout_seconds=timeout)
            release.validate_configuration(config, credentials())

    def test_runtime_target_binding_rejects_ambiguous_or_unsafe_destinations(self):
        target = {"target_id": "k3s-a", "base_url": "https://runtime.example.com/", "auth_token_env": "RUNTIME_TOKEN"}
        bad_lists = [None, {}, [target, target], [target] * 17]
        for field, value in (("target_id", "bad/id"), ("target_id", None), ("base_url", "http://runtime.example.com/"),
                             ("base_url", "https://runtime.example.com/api"), ("base_url", "https://127.0.0.1/"),
                             ("base_url", "https://user:secret@runtime.example.com/"),
                             ("base_url", "https://runtime.example.com/?token=secret"), ("auth_token_env", None),
                             ("unknown_field", "typo")):
            bad_lists.append([{**target, field: value}])
        for runtimes in bad_lists:
            with self.subTest(runtimes=runtimes), self.assertRaises(ValueError):
                config = profile()
                config["instance_monitor"]["runtime_targets"] = runtimes
                release.validate_configuration(config, {**credentials(), "RUNTIME_TOKEN": "runtime"})

    def test_credentials_cannot_override_runtime_settings_or_inject_env_lines(self):
        for name in (None, [], "PATH", "AWS_ACCESS_KEY_ID", "DOTNET_STARTUP_HOOKS", "SENTINEL_PROFILE_FILE",
                     "SENTINEL_AUTH_FILE", "SENTINEL_CANDIDATE", "PLATFORM_RECOVERY__ENABLED", "bad-token"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                config = profile()
                config["instance_monitor"]["scheduler_token_env"] = name
                release.validate_configuration(config, credentials())
        for value in ("", " ", "secret\nANOTHER=x", "secret\tvalue", "secret\0", "secret\x7f", "한글", 1):
            with self.subTest(value=value), self.assertRaises(ValueError):
                release.validate_configuration(profile(), {**credentials(), "SCHEDULER_API_TOKEN": value})

    def test_production_rejects_local_functional_fixtures_and_unknown_settings(self):
        for patch_value in ({"functional_checks": [{"url": "http://127.0.0.1:30080/calculate"}]},
                            {"functional_checks": None}, {"scheduler_urll": "typo"}):
            with self.subTest(patch_value=patch_value), self.assertRaises(ValueError):
                config = profile()
                config["instance_monitor"].update(patch_value)
                release.validate_configuration(config, credentials())

    def test_staged_secret_is_injected_only_after_inventory_is_enabled(self):
        config = profile()
        environment = {"SENTINEL_PROBE_SECRETS_JSON": json.dumps({"BOT_TOKEN": "b" * 40}),
                       "SCHEDULER_API_TOKEN": "test-scheduler-token"}
        self.assertEqual(release.probe_credentials(config, environment), credentials())
        config["instance_monitor"]["enabled"] = False
        result = release.probe_credentials(config, environment)
        self.assertEqual(result, {"BOT_TOKEN": "b" * 40})
        release.validate_configuration(config, result)

    def test_conflicting_dedicated_and_json_tokens_fail_without_disclosing_values(self):
        environment = {"SENTINEL_PROBE_SECRETS_JSON": json.dumps(credentials()), "SCHEDULER_API_TOKEN": "different-token"}
        with self.assertRaisesRegex(ValueError, "^Conflicting scheduler credentials$"):
            release.probe_credentials(profile(), environment)
        environment["SCHEDULER_API_TOKEN"] = "test-scheduler-token"
        self.assertEqual(release.probe_credentials(profile(), environment), credentials())

    def test_inventory_credentials_exist_only_in_private_bundle(self):
        config = profile()
        config["instance_monitor"]["runtime_targets"] = [
            {"target_id": "k3s-a", "base_url": "https://runtime.example.com/", "auth_token_env": "RUNTIME_TOKEN"}]
        environment = {"SENTINEL_PROFILE_JSON": json.dumps(config),
                       "SENTINEL_PROBE_SECRETS_JSON": json.dumps({"BOT_TOKEN": "b" * 40, "RUNTIME_TOKEN": "runtime"}),
                       "SCHEDULER_API_TOKEN": "test-scheduler-token",
                       "SENTINEL_WEB_CONFIG_JSON": json.dumps({"domain": "sla.example.com", "username": "admin",
                           "password_hash": "pbkdf2-sha256$600000$AAAAAAAAAAAAAAAAAAAAAA==$AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="})}
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source"
            source.mkdir()
            for name in ("image.tar.gz", "deploy_host.py"):
                (source / name).write_bytes(b"fixture")
            (source / "release.json").write_text('{"revision":"fixture"}')
            with patch.dict(os.environ, environment, clear=True):
                release.package(source, root / "bundle.tar.gz")
            with tarfile.open(root / "bundle.tar.gz") as archive:
                content = archive.extractfile("probe.env").read().decode()
                self.assertIn("SCHEDULER_API_TOKEN=test-scheduler-token\n", content)
                self.assertIn("RUNTIME_TOKEN=runtime\n", content)
                self.assertEqual(archive.getmember("probe.env").mode, 0o600)
                saved_profile = json.load(archive.extractfile("profile.json"))
                self.assertEqual(saved_profile, config)
                self.assertNotIn("test-scheduler-token", json.dumps(saved_profile))
            self.assertEqual({p.name for p in source.iterdir()}, {"image.tar.gz", "deploy_host.py", "release.json"})


if __name__ == "__main__":
    unittest.main()
