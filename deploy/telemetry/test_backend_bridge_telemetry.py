import json
import stat
import subprocess
import unittest
from unittest.mock import Mock, patch

import backend_command
import report_service


class BackendBridgeTelemetryTests(unittest.TestCase):
    def setUp(self):
        self.record = {'candidate': 'a' * 64, 'candidate_name': 'msg-dev-backend-candidate-12345', 'phase': 'serving'}
        self.path = Mock()
        self.path.lstat.return_value = Mock(st_mode=stat.S_IFREG | 0o600, st_uid=0, st_size=200)
        self.path.parent.lstat.return_value = Mock(st_mode=stat.S_IFDIR | 0o700, st_uid=0)
        self.path.read_text.side_effect = lambda: json.dumps(self.record)
        self.candidate = {'id': 'a' * 64, 'name': '/msg-dev-backend-candidate-12345', 'running': True, 'restart_count': 0}
        self.run = Mock(side_effect=lambda *args, **kwargs: Mock(stdout=json.dumps(self.candidate)))
        self.response = Mock(status=200)
        self.response.getheader.return_value = 'application/json; charset=utf-8'
        self.response.read.return_value = b'{"status":"ok","database":"ok","cache":"ok"}'
        self.connection = Mock()
        self.connection.getresponse.return_value = self.response
        self.addCleanup(patch.stopall)
        patch.object(backend_command, 'BRIDGE_STATE', self.path).start()
        patch.object(backend_command.subprocess, 'run', self.run).start()
        patch.object(backend_command.http.client, 'HTTPConnection', return_value=self.connection).start()

    def test_serving_candidate_requires_container_and_real_proxy_health(self):
        self.assertEqual(backend_command.verified_bridge(), {'ActiveState': 'active', 'NRestarts': 0})
        self.assertEqual(self.run.call_args.args[0][-1], 'a' * 64)
        self.assertEqual(self.connection.request.call_args.args[:2], ('GET', '/healthz'))
        self.connection.close.assert_called_once()

    def test_stopped_or_missing_primary_is_covered_by_verified_bridge(self):
        for missing in (False, True):
            with self.subTest(missing=missing), patch.object(report_service, 'collect', return_value={'service_state': 'active', 'cpu_used_percent': 7.2}):
                with patch.object(backend_command, 'container_state', **({'side_effect': subprocess.CalledProcessError(1, ['docker'])} if missing else {'return_value': {'ActiveState': 'inactive', 'NRestarts': 0}})):
                    result = backend_command.resources()
                    self.assertEqual(result['service_state'], 'active')
                    self.assertEqual(result['cpu_used_percent'], 7.2)

    def test_primary_status_and_start_authorization_are_not_overridden(self):
        state = {'SubState': 'exited', 'ActiveState': 'inactive', 'NRestarts': 2}
        with patch.object(backend_command, 'container_state', return_value=state):
            self.assertEqual(backend_command.execute('status'), state)
        self.connection.request.assert_not_called()

    def test_normal_primary_does_not_consult_bridge(self):
        with patch.object(report_service, 'collect', return_value={}), patch.object(backend_command, 'container_state', return_value={'ActiveState': 'active', 'NRestarts': 2}):
            self.assertEqual(backend_command.resources()['restart_count'], 2)
        self.run.assert_not_called()

    def test_untrusted_files_and_partial_deployment_never_report_healthy(self):
        cases = [
            ('owner', lambda: setattr(self.path.lstat.return_value, 'st_uid', 1000)),
            ('writable', lambda: setattr(self.path.lstat.return_value, 'st_mode', stat.S_IFREG | 0o622)),
            ('symlink', lambda: setattr(self.path.lstat.return_value, 'st_mode', stat.S_IFLNK | 0o777)),
            ('parent', lambda: setattr(self.path.parent.lstat.return_value, 'st_mode', stat.S_IFDIR | 0o777)),
            ('large', lambda: setattr(self.path.lstat.return_value, 'st_size', 5000)),
            ('routing', lambda: self.record.update(phase='routing')),
            ('identity', lambda: self.record.update(candidate='another-container')),
            ('name', lambda: self.record.update(candidate_name='msg-dev-postgres-1')),
        ]
        for name, mutate in cases:
            with self.subTest(name=name):
                before = dict(self.record)
                mutate()
                self.assertIsNone(backend_command.verified_bridge())
                self.record = before
                self.path.lstat.return_value = Mock(st_mode=stat.S_IFREG | 0o600, st_uid=0, st_size=200)
                self.path.parent.lstat.return_value = Mock(st_mode=stat.S_IFDIR | 0o700, st_uid=0)
        self.run.assert_not_called()
        self.connection.request.assert_not_called()

    def test_missing_mismatched_and_stopped_candidate_cannot_cover_primary(self):
        for changes in [{'id': 'b' * 64}, {'name': '/other'}, {'running': False}]:
            with self.subTest(changes=changes):
                old = dict(self.candidate)
                self.candidate.update(changes)
                self.assertIsNone(backend_command.verified_bridge())
                self.candidate = old
        self.connection.request.assert_not_called()
        self.run.side_effect = subprocess.CalledProcessError(1, ['docker'])
        self.assertIsNone(backend_command.verified_bridge())

    def test_bad_http_html_redirect_partial_health_and_timeouts_do_not_cover_failure(self):
        for status, content, body in [
            (503, 'application/json', b'{}'), (302, 'application/json', b'{}'),
            (200, 'text/html', b'<html>login</html>'),
            (200, 'application/json', b'{"status":"ok","database":"error","cache":"ok"}'),
            (200, 'application/json', b'{"status":"ok"}'),
            (200, 'application/json', b'not-json'),
            (200, 'application/json', b'a' * 4097),
        ]:
            with self.subTest(status=status, body=body[:50]):
                self.response.status = status
                self.response.getheader.return_value = content
                self.response.read.return_value = body
                self.assertIsNone(backend_command.verified_bridge())
        self.connection.request.side_effect = TimeoutError()
        self.assertIsNone(backend_command.verified_bridge())

    def test_real_primary_failure_stays_failed_without_verified_bridge(self):
        self.response.status = 503
        with patch.object(report_service, 'collect', return_value={}), patch.object(backend_command, 'container_state', return_value={'ActiveState': 'inactive', 'NRestarts': 0}):
            self.assertEqual(backend_command.resources()['service_state'], 'inactive')
        self.path.lstat.side_effect = FileNotFoundError()
        with patch.object(report_service, 'collect', return_value={}), patch.object(backend_command, 'container_state', side_effect=subprocess.CalledProcessError(1, ['docker'])):
            with self.assertRaises(subprocess.CalledProcessError):
                backend_command.resources()


if __name__ == '__main__':
    unittest.main()
