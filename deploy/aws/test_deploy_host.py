import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import deploy_host as host


class ActivationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.old = self.root / "old"
        self.old.mkdir()
        (self.old / "profile.json").write_text(json.dumps({"platform_monitor": {"profile": "old-profile"}}))
        self.new = self.root / "new"
        self.new.mkdir()
        self.containers = {host.CURRENT: self.info("old-image", self.old, True)}
        self.fail_candidate = False
        self.fail_current = False
        self.fail_rename = False
        self.health_profiles = []
        for name, replacement in (("BASE", self.root), ("container", self.containers.get),
                                  ("remove_container", self.remove), ("start", self.start),
                                  ("command", self.command), ("healthy", self.healthy)):
            patcher = patch.object(host, name, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)

    @staticmethod
    def info(image, path, running):
        return {"Image": image, "State": {"Running": running},
                "Mounts": [{"Source": str(path / "profile.json"), "Destination": "/config/profile.json"}]}

    def remove(self, name):
        self.containers.pop(name, None)

    def start(self, name, image, path, data, port):
        self.assertNotIn(name, self.containers)
        self.containers[name] = self.info(image, path, True)

    def command(self, args):
        if args[1] == "rename":
            if self.fail_rename and args[2] == host.CURRENT:
                raise host.DeploymentError("rename failed")
            self.containers[args[3]] = self.containers.pop(args[2])
        elif args[1] in {"stop", "start"}:
            self.containers[args[-1]]["State"]["Running"] = args[1] == "start"
        else:
            self.fail("Unexpected Docker operation")

    def healthy(self, name, profile):
        self.health_profiles.append((name, profile))
        if name == host.CANDIDATE:
            return not self.fail_candidate
        return not (self.fail_current and self.containers[name]["Image"] == "new-image")

    def test_success_keeps_previous_image_and_configuration(self):
        host.activate("new-image", self.new, "aws")
        self.assertEqual(self.containers[host.CURRENT]["Image"], "new-image")
        self.assertEqual(self.containers[host.PREVIOUS]["Image"], "old-image")
        self.assertFalse(self.containers[host.PREVIOUS]["State"]["Running"])
        self.assertNotIn(host.CANDIDATE, self.containers)

    def test_failed_candidate_leaves_current_running(self):
        self.fail_candidate = True
        with self.assertRaisesRegex(host.DeploymentError, "current version unchanged"):
            host.activate("new-image", self.new, "aws")
        self.assertEqual(self.containers[host.CURRENT]["Image"], "old-image")
        self.assertTrue(self.containers[host.CURRENT]["State"]["Running"])
        self.assertNotIn(host.CANDIDATE, self.containers)

    def test_failed_switch_restores_old_configuration_and_checks_it(self):
        self.fail_current = True
        with self.assertRaisesRegex(host.DeploymentError, "restored and verified"):
            host.activate("new-image", self.new, "aws")
        self.assertEqual(self.containers[host.CURRENT]["Image"], "old-image")
        self.assertTrue(self.containers[host.CURRENT]["State"]["Running"])
        self.assertEqual(self.health_profiles[-1], (host.CURRENT, "old-profile"))

    def test_rename_failure_restarts_current_without_deleting_it(self):
        self.fail_rename = True
        with self.assertRaisesRegex(host.DeploymentError, "restored and verified"):
            host.activate("new-image", self.new, "aws")
        self.assertEqual(self.containers[host.CURRENT]["Image"], "old-image")
        self.assertTrue(self.containers[host.CURRENT]["State"]["Running"])

    def test_failed_initial_deployment_removes_failed_container(self):
        self.containers.clear()
        self.fail_current = True
        with self.assertRaisesRegex(host.DeploymentError, "no previous version"):
            host.activate("new-image", self.new, "aws")
        self.assertFalse(self.containers)

    def test_proxy_activation_failure_also_restores_previous_application(self):
        def fail_proxy():
            raise host.DeploymentError("HTTPS activation failed")
        with self.assertRaisesRegex(host.DeploymentError, "restored and verified"):
            host.activate("new-image", self.new, "aws", finalize=fail_proxy)
        self.assertEqual(self.containers[host.CURRENT]["Image"], "old-image")
        self.assertTrue(self.containers[host.CURRENT]["State"]["Running"])


class WebActivationTests(unittest.TestCase):
    def test_failed_proxy_restores_previous_configuration_and_checks_old_domain(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            old = root / "old"
            old.mkdir()
            (old / "release.json").write_text(json.dumps({"public_web": {"domain": "old.example.com"}}))
            containers = {host.WEB_CURRENT: {
                "State": {"Running": True}, "Image": "old",
                "Mounts": [{"Destination": "/etc/caddy/Caddyfile", "Source": str(old / "Caddyfile")}],
            }}

            def command(args):
                if args[1] == "rename":
                    containers[args[3]] = containers.pop(args[2])
                elif args[1] in {"stop", "start"}:
                    containers[args[-1]]["State"]["Running"] = args[1] == "start"
                else:
                    self.fail("Unexpected proxy operation")

            def start(release):
                containers[host.WEB_CURRENT] = {"State": {"Running": True}, "Image": "new"}

            checked_domains = []

            def ready(domain):
                checked_domains.append(domain)
                return containers[host.WEB_CURRENT]["Image"] == "old"

            with patch.object(host, "container", containers.get), \
                    patch.object(host, "remove_container", lambda name: containers.pop(name, None)), \
                    patch.object(host, "command", command), patch.object(host, "start_web", start), \
                    patch.object(host, "web_ready", ready):
                with self.assertRaisesRegex(host.DeploymentError, "previous proxy restored"):
                    host.activate_web(root / "new", {"domain": "new.example.com"})
            self.assertEqual(containers[host.WEB_CURRENT]["Image"], "old")
            self.assertEqual(checked_domains, ["new.example.com", "old.example.com"])


if __name__ == "__main__":
    unittest.main()
