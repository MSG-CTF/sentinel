import stat
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import report_service


class RuntimeTelemetryTests(unittest.TestCase):
    def bridge(self, *, content=b'proxy_pass http://127.0.0.1:18080;\n', mode=0o644,
               owner=0, symlink=False, age=1, active='active', user='provisioner', pid='500'):
        path = Mock()
        path.lstat.return_value = SimpleNamespace(st_uid=owner, st_mode=stat.S_IFREG | mode)
        path.is_file.return_value = True
        path.is_symlink.return_value = symlink
        path.read_bytes.return_value = content
        result = SimpleNamespace(stdout=f'ActiveState={active}\nSubState=running\nMainPID={pid}\nUser={user}\nGroup=provisioner\nActiveEnterTimestampMonotonic={int((1000-age)*1000000)}\n')
        return path, result

    def test_routed_short_lived_bridge_preserves_process_availability(self):
        path, result = self.bridge()
        with patch.object(report_service, 'Path', return_value=path), patch.object(report_service.subprocess, 'run', return_value=result) as run, patch.object(report_service.time, 'monotonic', return_value=1000):
            for state in ('inactive', 'failed', 'activating', 'deactivating'):
                self.assertEqual(report_service.effective_service_state('secure-provisioner.service', state), 'active')
            self.assertEqual(run.call_args.args[0][:3], ['systemctl', 'show', 'secure-provisioner-bridge.service'])
            self.assertNotIn('shell', run.call_args.kwargs)

    def test_unverified_or_expired_bridge_never_hides_primary_failure(self):
        cases = ({'content': b'proxy_pass http://127.0.0.1:8080;\n'}, {'mode': 0o664},
                 {'owner': 1000}, {'symlink': True}, {'age': 121}, {'age': -1},
                 {'active': 'failed'}, {'user': 'root'}, {'pid': '0'}, {'pid': 'bad'})
        for case in cases:
            with self.subTest(case=case):
                path, result = self.bridge(**case)
                with patch.object(report_service, 'Path', return_value=path), patch.object(report_service.subprocess, 'run', return_value=result), patch.object(report_service.time, 'monotonic', return_value=1000):
                    self.assertEqual(report_service.effective_service_state('secure-provisioner.service', 'failed'), 'failed')

    def test_other_services_and_healthy_primary_never_use_bridge_fallback(self):
        with patch.object(report_service, 'Path') as path, patch.object(report_service.subprocess, 'run') as run:
            for service, state in [('mjsec-scheduler.service', 'failed'), ('msg-ticket-bot.service', 'inactive'), ('secure-provisioner.service', 'active')]:
                self.assertEqual(report_service.effective_service_state(service, state), state)
            path.assert_not_called(); run.assert_not_called()

    def test_missing_or_unreadable_state_reports_actual_primary_failure(self):
        with patch.object(report_service, 'Path', side_effect=PermissionError):
            self.assertEqual(report_service.effective_service_state('secure-provisioner.service', 'inactive'), 'inactive')
        path, _ = self.bridge()
        with patch.object(report_service, 'Path', return_value=path), patch.object(report_service.subprocess, 'run', side_effect=TimeoutError):
            self.assertEqual(report_service.effective_service_state('secure-provisioner.service', 'inactive'), 'inactive')


if __name__ == '__main__': unittest.main()
