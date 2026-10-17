import unittest
from unittest.mock import patch, Mock
import report_service
import asset_command


class TelemetryTests(unittest.TestCase):
    def test_cpu_is_delta_not_load_average(self):
        self.assertEqual(report_service.cpu_percent((1000, 600), (1200, 700)), 50)
        self.assertEqual(report_service.cpu_percent((1000, 600), (1200, 800)), 0)
        self.assertEqual(report_service.cpu_percent((1000, 600), (1200, 600)), 100)

    def test_invalid_cpu_samples_are_rejected(self):
        for before, after in [((100, 50), (100, 50)), ((100, 50), (90, 40)), ((100, 50), (200, 300))]:
            with self.assertRaises(ValueError): report_service.cpu_percent(before, after)

    def test_unknown_actions_never_run_commands(self):
        with patch.object(asset_command.subprocess, 'run') as run:
            for action in ['whoami', 'start; id', '', 'status\nstart']:
                with self.assertRaises(ValueError): asset_command.execute(action)
            run.assert_not_called()

    def test_active_service_is_never_restarted(self):
        with patch.object(asset_command, 'service_state', return_value={'ActiveState':'active'}), patch.object(asset_command.subprocess, 'run') as run:
            with self.assertRaises(ValueError): asset_command.execute('start')
            run.assert_not_called()

    def test_start_uses_only_fixed_service_without_shell(self):
        with patch.object(asset_command, 'service_state', side_effect=[{'ActiveState':'failed'},{'ActiveState':'active'}]), patch.object(asset_command.subprocess, 'run') as run:
            result=asset_command.execute('start')
            self.assertEqual(result['result'],'start_requested')
            self.assertEqual(run.call_args.args[0],['/usr/bin/systemctl','start','msg-ticket-bot.service'])
            self.assertNotIn('shell',run.call_args.kwargs)
