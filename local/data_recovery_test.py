"""Fail-closed tests for backup selection and the destructive lab cleanup boundary."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import data_recovery as recovery


class BackupGuards(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.folder = self.root / "20261010T000000Z-abcdef12"
        self.folder.mkdir()
        self.manifest = {"schema_version": 1, "complete": True, "backup_id": self.folder.name,
                         "source_project": recovery.PROJECT, "application_images": dict.fromkeys(recovery.APPS, "sha256:abc"), "assets": []}
        for service, spec in recovery.DATA.items():
            path = self.folder / (service + (".dump" if spec["kind"] == "postgres" else ".rdb"))
            path.write_bytes(b"fixture bytes, not an executable dump")
            self.manifest["assets"].append({"service": service, "asset_id": spec["asset_id"], "kind": spec["kind"],
                                            "filename": path.name, "bytes": path.stat().st_size, "sha256": recovery.digest(path)})
        self.patch = patch.object(recovery, "BACKUPS", self.root)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.write()

    def write(self):
        (self.folder / "manifest.json").write_text(json.dumps(self.manifest), encoding="utf-8")

    def reject_before_docker(self, code):
        with patch.object(recovery, "run") as docker:
            with self.assertRaisesRegex(recovery.RecoveryError, code):
                recovery.restore(self.folder.name)
            docker.assert_not_called()

    def test_valid_complete_set(self):
        self.assertEqual(recovery.validate_backup(self.folder.name)[1], self.manifest)

    def test_corrupt_archive_rejected_before_any_docker_call(self):
        path = self.folder / self.manifest["assets"][0]["filename"]
        path.write_bytes(b"X" * path.stat().st_size)
        self.reject_before_docker("BACKUP_CHECKSUM_MISMATCH")

    def test_partial_backup_rejected(self):
        self.manifest["complete"] = False
        self.write()
        self.reject_before_docker("INCOMPLETE_OR_FOREIGN_BACKUP")

    def test_foreign_project_rejected(self):
        self.manifest["source_project"] = "production"
        self.write()
        self.reject_before_docker("INCOMPLETE_OR_FOREIGN_BACKUP")

    def test_archive_path_escape_rejected(self):
        self.manifest["assets"][0]["filename"] = "../postgres-internal.dump"
        self.write()
        self.reject_before_docker("BACKUP_BINDING_MISMATCH")

    def test_duplicate_and_missing_assets_rejected(self):
        self.manifest["assets"][0] = copy.deepcopy(self.manifest["assets"][1])
        self.write()
        self.reject_before_docker("BACKUP_ASSET_SET_MISMATCH")

    def test_wrong_track_rejected(self):
        self.manifest["assets"][0]["asset_id"] = "external-postgres"
        self.write()
        self.reject_before_docker("BACKUP_BINDING_MISMATCH")

    def test_backup_id_cannot_be_path_or_project(self):
        for value in ("../", "msg-sla-real-local", "C:/backup", "20261010T000000Z-abcdef12/.."):
            with self.assertRaisesRegex(recovery.RecoveryError, "INVALID_BACKUP_ID"):
                recovery.validate_backup(value)


class LabGuards(unittest.TestCase):
    def setUp(self):
        self.lab = recovery.Lab.__new__(recovery.Lab)
        self.lab.run_id = "20261010T000000Z-abcdef12"
        self.lab.project = "msg-sla-restore-" + self.lab.run_id.lower()
        self.record = {"Config": {"Labels": {recovery.LABEL: self.lab.run_id, "com.docker.compose.project": self.lab.project}},
                       "HostConfig": {"PortBindings": {}, "Privileged": False},
                       "NetworkSettings": {"Networks": {self.lab.project + "_isolated": {}}},
                       "Mounts": [{"Type": "volume", "Name": self.lab.project + "_postgres_internal"}]}

    def test_source_volume_may_never_be_restore_destination_or_cleanup_target(self):
        self.record["Mounts"][0]["Name"] = recovery.PROJECT + "_internal_postgres"
        with self.assertRaisesRegex(recovery.RecoveryError, "DESTINATION_VOLUME_MISMATCH"):
            self.lab.validate_record(self.record)

    def test_foreign_container_blocked(self):
        self.record["Config"]["Labels"]["com.docker.compose.project"] = recovery.PROJECT
        with self.assertRaisesRegex(recovery.RecoveryError, "DESTINATION_SCOPE_MISMATCH"):
            self.lab.validate_record(self.record)

    def test_ports_and_shared_network_blocked(self):
        exposed = copy.deepcopy(self.record)
        exposed["HostConfig"]["PortBindings"] = {"5432/tcp": [{"HostPort": "5432"}]}
        shared = copy.deepcopy(self.record)
        shared["NetworkSettings"]["Networks"]["bridge"] = {}
        for record, code in ((exposed, "DESTINATION_EXPOSED"), (shared, "DESTINATION_NETWORK_MISMATCH")):
            with self.assertRaisesRegex(recovery.RecoveryError, code):
                self.lab.validate_record(record)

    def test_arbitrary_host_mount_blocked(self):
        self.record["Mounts"] = [{"Type": "bind", "Destination": "/var/run/docker.sock", "RW": True}]
        with self.assertRaisesRegex(recovery.RecoveryError, "DESTINATION_MOUNT_MISMATCH"):
            self.lab.validate_record(self.record)

    def test_expected_isolated_destination_allowed(self):
        self.assertEqual(self.lab.validate_record(self.record), self.record)


if __name__ == "__main__":
    unittest.main()
