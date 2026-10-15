import json
import base64
import os
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

import release


def profile():
    return {"platform_monitor": {"profile": "aws", "event_file": "data/events.jsonl",
                                "targets": [{"target_id": "web", "url": "https://example.com/"}]},
            "platform_recovery": {"enabled": False}, "instance_monitor": {"enabled": False}}


class ConfigurationTests(unittest.TestCase):
    def test_notification_webhook_is_required_only_when_enabled_and_never_accepts_redirect_hosts(self):
        config=profile(); self.assertEqual(release.operations_environment(config, {}),'')
        config['discord_alerts']={'enabled':True,'state_file':'data/discord-alerts.json'}
        value='https://discord.com/api/webhooks/12345678901234567/'+'a'*40
        self.assertEqual(release.operations_environment(config,{'SENTINEL_DISCORD_WEBHOOK_URL':value}),'SENTINEL_DISCORD_WEBHOOK_URL='+value+'\n')
        for bad in ['',value.replace('discord.com','discord.com.evil.test'),value+'\nOTHER=secret']:
            with self.assertRaises(ValueError): release.operations_environment(config,{'SENTINEL_DISCORD_WEBHOOK_URL':bad})

    def test_ssh_key_is_separate_from_binding_and_host_key_is_required(self):
        asset={'asset_id':'discord-bot','host':'example.com','port':22,'username':'sentinel-ops','service':'bot.service',
               'host_key_sha256':base64.b64encode(bytes(32)).decode(),'private_key_env':'SENTINEL_SSH_DISCORD_B64'}
        environment={'SENTINEL_OPERATIONS_JSON':json.dumps([asset]),'SENTINEL_SSH_DISCORD_B64':base64.b64encode(b'-----BEGIN OPENSSH PRIVATE KEY-----\n'+b'a'*120).decode()}
        self.assertIn('SENTINEL_SSH_DISCORD_B64=',release.operations_environment(profile(),environment))
        asset['host_key_sha256']=''; environment['SENTINEL_OPERATIONS_JSON']=json.dumps([asset])
        with self.assertRaises(ValueError): release.operations_environment(profile(),environment)
    def test_heartbeat_uses_only_its_dedicated_strong_credential_without_a_url(self):
        config = profile()
        config["platform_monitor"]["targets"] = [{"target_id": "discord-bot", "kind": "service_heartbeat", "auth_token_env": "BOT_TOKEN"}]
        with self.assertRaises(ValueError):
            release.validate_configuration(config, {"BOT_TOKEN": "short"})
        self.assertEqual(release.validate_configuration(config, {"BOT_TOKEN": "a" * 40}), "BOT_TOKEN=" + "a" * 40 + "\n")
        config["platform_monitor"]["targets"][0]["url"] = "https://example.com/"
        with self.assertRaises(ValueError):
            release.validate_configuration(config, {"BOT_TOKEN": "a" * 40})

    def test_observe_only_without_tokens(self):
        self.assertEqual(release.validate_configuration(profile(), {}), "")

    def test_production_recovery_stays_disabled(self):
        config = profile()
        config["platform_recovery"]["enabled"] = True
        with self.assertRaises(ValueError):
            release.validate_configuration(config, {})

    def test_disallows_credentials_in_url_and_local_targets(self):
        for url in ("http://localhost/", "http://127.0.0.1/", "https://user:pass@example.com/",
                    "https://example.com/?token=secret", "https://example.com/#secret"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                config = profile()
                config["platform_monitor"]["targets"][0]["url"] = url
                release.validate_configuration(config, {})

    def test_authenticated_probes_require_https_and_exact_tokens(self):
        config = profile()
        config["platform_monitor"]["targets"][0]["auth_token_env"] = "PROBE_TOKEN"
        self.assertEqual(release.validate_configuration(config, {"PROBE_TOKEN": "example"}), "PROBE_TOKEN=example\n")
        for credentials in ({}, {"PROBE_TOKEN": "example", "UNUSED_TOKEN": "other"}):
            with self.assertRaises(ValueError):
                release.validate_configuration(config, credentials)
        config["platform_monitor"]["targets"][0]["url"] = "http://example.com/"
        with self.assertRaises(ValueError):
            release.validate_configuration(config, {"PROBE_TOKEN": "example"})

    def test_env_file_cannot_inject_additional_settings(self):
        for name, value in (("PATH", "x"), ("DOTNET_STARTUP_HOOKS", "x"),
                            ("PROBE_TOKEN", "x\nSECOND=y"), ("PROBE_TOKEN", "x\rSECOND=y"),
                            ("PROBE_TOKEN", "x\0y"), ("PROBE_TOKEN", "")):
            with self.subTest(name=name, value=value), self.assertRaises(ValueError):
                config = profile()
                config["platform_monitor"]["targets"][0]["auth_token_env"] = name
                release.validate_configuration(config, {name: value})

    def test_disabled_targets_need_no_authentication(self):
        config = profile()
        config["platform_monitor"]["targets"].append({"enabled": False, "auth_token_env": "LATER_TOKEN"})
        self.assertEqual(release.validate_configuration(config, {}), "")

    def test_private_package_contains_only_expected_members_and_leaves_build_artifact_clean(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source"
            source.mkdir()
            for name in ("image.tar.gz", "release.json", "deploy_host.py"):
                (source / name).write_bytes(b"fixture")
            (source / "release.json").write_text('{"revision":"fixture"}')
            config = profile()
            config["platform_monitor"]["targets"][0]["auth_token_env"] = "PROBE_TOKEN"
            with patch.dict(os.environ, {"SENTINEL_PROFILE_JSON": json.dumps(config),
                                         "SENTINEL_PROBE_SECRETS_JSON": '{"PROBE_TOKEN":"test-only-secret"}',
                                         "SCHEDULER_API_TOKEN": "unused-staged-secret",
                                         "SENTINEL_WEB_CONFIG_JSON": json.dumps({
                                             "domain": "sla.example.com", "username": "admin",
                                             "password_hash": 'pbkdf2-sha256$600000$AAAAAAAAAAAAAAAAAAAAAA==$AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA='})}):
                release.package(source, root / "bundle.tar.gz")
            with tarfile.open(root / "bundle.tar.gz") as archive:
                self.assertEqual(set(archive.getnames()),
                                 {"image.tar.gz", "release.json", "deploy_host.py", "profile.json", "probe.env"})
                self.assertEqual(archive.extractfile("probe.env").read(), b"PROBE_TOKEN=test-only-secret\n")
                self.assertIn("public_web", json.load(archive.extractfile("release.json")))
            self.assertFalse((source / "probe.env").exists())
            self.assertFalse((source / "profile.json").exists())
            self.assertEqual(json.loads((source / "release.json").read_text()), {"revision": "fixture"})


if __name__ == "__main__":
    unittest.main()
