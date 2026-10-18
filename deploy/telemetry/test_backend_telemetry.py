import json
import subprocess
import unittest
from unittest.mock import Mock, patch

import backend_command
import report_backend
import report_service


class BackendTelemetryTests(unittest.TestCase):
    def test_docker_state_is_measured_without_exposing_health_logs(self):
        for status, health, expected in [('running', 'healthy', 'active'),
                                        ('running', 'unhealthy', 'failed'),
                                        ('paused', None, 'failed'),
                                        ('restarting', None, 'activating'),
                                        ('exited', None, 'inactive'),
                                        ('created', None, 'inactive'),
                                        ('dead', None, 'failed')]:
            state = {'Status': status, 'Pid': 123,
                     'Health': {'Status': health, 'Log': [{'Output': 'private-health-details'}]}}
            reply = Mock(stdout=json.dumps({'state': state, 'restart_count': 2}))
            with self.subTest(status=status, health=health), patch.object(backend_command.subprocess, 'run', return_value=reply) as run:
                result = backend_command.container_state()
                self.assertEqual(result['ActiveState'], expected)
                self.assertEqual(result['NRestarts'], 2)
                self.assertNotIn('private-health-details', json.dumps(result))
                self.assertEqual(run.call_args.args[0][-1], 'msg-dev-backend-1')
                self.assertNotIn('shell', run.call_args.kwargs)

    def test_unknown_actions_do_not_contact_docker(self):
        with patch.object(backend_command.subprocess, 'run') as run:
            for action in ['restart', 'stop', 'status; id', '', 'resources\nstart']:
                with self.assertRaises(ValueError):
                    backend_command.execute(action)
            run.assert_not_called()

    def test_running_unhealthy_paused_and_restarting_containers_cannot_be_started(self):
        for state in ['running', 'paused', 'restarting', 'removing', 'dead']:
            with self.subTest(state=state), patch.object(backend_command, 'container_state', return_value={'SubState': state}), patch.object(backend_command.subprocess, 'run') as run:
                with self.assertRaises(ValueError):
                    backend_command.execute('start')
                run.assert_not_called()

    def test_start_targets_only_the_stopped_backend_container(self):
        with patch.object(backend_command, 'container_state', side_effect=[{'SubState': 'exited'}, {'SubState': 'running', 'ActiveState': 'active'}]), patch.object(backend_command.subprocess, 'run') as run:
            self.assertEqual(backend_command.execute('start')['result'], 'start_requested')
            self.assertEqual(run.call_args.args[0], backend_command.DOCKER + ['start', 'msg-dev-backend-1'])
            self.assertNotIn('shell', run.call_args.kwargs)

    def test_container_state_overrides_a_healthy_docker_daemon(self):
        with patch.object(report_service, 'collect', return_value={'service_state': 'active', 'cpu_used_percent': 17.5}), patch.object(backend_command, 'container_state', return_value={'ActiveState': 'inactive', 'NRestarts': 4}):
            result = backend_command.resources()
            self.assertEqual(result['service_state'], 'inactive')
            self.assertEqual(result['cpu_used_percent'], 17.5)
            self.assertEqual(result['restart_count'], 4)

    def test_docker_inspection_failure_cannot_create_a_healthy_report(self):
        with patch.object(backend_command.subprocess, 'run', side_effect=subprocess.CalledProcessError(1, ['docker'])):
            with self.assertRaises(subprocess.CalledProcessError):
                backend_command.execute('status')

    def test_reporter_is_bound_to_the_backend_target_and_fixed_helper(self):
        config = {'endpoint': 'https://sla.msgctf.kr/ingest/heartbeat/backend-host',
                  'service': backend_command.SERVICE, 'token': 'a' * 40}
        observation = {key: 1 for key in report_backend.FIELDS}
        with patch.object(report_backend.subprocess, 'run', return_value=Mock(stdout=json.dumps(observation))) as run, patch.object(report_backend, 'send') as send:
            report_backend.report(config)
            self.assertEqual(run.call_args.args[0], ['/usr/local/libexec/sentinel-asset-command', 'resources'])
            send.assert_called_once_with(config, observation)
        for bad in [{**config, 'service': 'docker:another-container'},
                    {**config, 'endpoint': 'https://sla.msgctf.kr/ingest/heartbeat/runtime'}]:
            with patch.object(report_backend.subprocess, 'run') as run:
                with self.assertRaises(ValueError):
                    report_backend.report(bad)
                run.assert_not_called()

    def test_reporter_rejects_partial_or_unexpected_private_fields(self):
        config = {'endpoint': 'https://sla.msgctf.kr/ingest/heartbeat/backend-host', 'service': backend_command.SERVICE}
        for observation in [{}, {**dict.fromkeys(report_backend.FIELDS, 1), 'private_data': 'not-for-telemetry'}]:
            with patch.object(report_backend.subprocess, 'run', return_value=Mock(stdout=json.dumps(observation))), patch.object(report_backend, 'send') as send:
                with self.assertRaises(ValueError):
                    report_backend.report(config)
                send.assert_not_called()
