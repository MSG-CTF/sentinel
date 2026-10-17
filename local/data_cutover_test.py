import copy
import unittest

import data_cutover as cutover
import data_recovery as recovery


class CutoverGuardTests(unittest.TestCase):
    def setUp(self):
        self.volumes = {name: recovery.PROJECT + "_" + spec["volume"] for name, spec in recovery.DATA.items()}
        self.config = {"name": recovery.PROJECT, "services": {}, "volumes": {}}
        for name, spec in recovery.DATA.items():
            self.config["services"][name] = {"volumes": [{"type": "volume", "source": spec["volume"],
                "target": "/data" if spec["kind"] == "redis" else "/var/lib/postgresql/data"}]}
            self.config["volumes"][spec["volume"]] = {"name": self.volumes[name]}

    def test_expected_fixed_service_bindings_are_accepted(self):
        cutover.scoped_config(self.config, self.volumes)

    def test_foreign_project_is_rejected(self):
        self.config["name"] = "another-project"
        with self.assertRaisesRegex(recovery.RecoveryError, "PROJECT_MISMATCH"):
            cutover.scoped_config(self.config, self.volumes)

    def test_host_bind_cannot_replace_a_data_volume(self):
        self.config["services"]["postgres-internal"]["volumes"][0]["type"] = "bind"
        with self.assertRaisesRegex(recovery.RecoveryError, "BINDING_INVALID"):
            cutover.scoped_config(self.config, self.volumes)

    def test_foreign_volume_is_rejected(self):
        self.config["volumes"]["internal_postgres"]["name"] = "another-project-db"
        with self.assertRaisesRegex(recovery.RecoveryError, "VOLUME_INVALID"):
            cutover.scoped_config(self.config, self.volumes)

    def test_published_database_port_is_rejected(self):
        self.config["services"]["postgres-internal"]["ports"] = [{"target": 5432, "published": 5432}]
        with self.assertRaisesRegex(recovery.RecoveryError, "DATA_EXPOSED"):
            cutover.scoped_config(self.config, self.volumes)

    def test_ambiguous_volume_mount_is_rejected(self):
        mounts = self.config["services"]["redis-internal"]["volumes"]
        mounts.append(copy.deepcopy(mounts[0]))
        with self.assertRaisesRegex(recovery.RecoveryError, "BINDING_INVALID"):
            cutover.scoped_config(self.config, self.volumes)


if __name__ == "__main__":
    unittest.main()
