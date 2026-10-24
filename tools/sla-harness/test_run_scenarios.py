import contextlib
from http.server import ThreadingHTTPServer
import io
from pathlib import Path
import sys
import tempfile
import threading
import unittest

import receivers
import run_scenarios as runner
import targets


class Output(io.StringIO):
    def reconfigure(self, **kwargs):
        pass


class ScenarioFreshnessTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        old_log = receivers.LOG_PATH
        receivers.LOG_PATH = Path(self.directory.name) / "received.jsonl"
        self.addCleanup(setattr, receivers, "LOG_PATH", old_log)
        for attribute, handler, path in (
            ("CONTROL", targets.ControlHandler, ""),
            ("RECEIVER", receivers.Handler, ""),
            ("BACKEND_TARGET", targets.BackendHandler, "/healthz"),
            ("KOTH_TARGET", targets.KothHandler, "/healthz"),
            ("EVENTS_TARGET", targets.SchedulerHandler, "/api/instances/active"),
        ):
            server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            self.addCleanup(server.server_close)
            self.addCleanup(server.shutdown)
            old = getattr(runner, attribute)
            setattr(runner, attribute, f"http://127.0.0.1:{server.server_port}" + path)
            self.addCleanup(setattr, runner, attribute, old)

    def run_scenarios(self, self_test=False):
        runner.results.clear()
        arguments = ["run_scenarios.py"] + (["--self-test"] if self_test else ["--wait", "0"])
        old_arguments = sys.argv
        try:
            sys.argv = arguments
            with contextlib.redirect_stdout(Output()):
                with self.assertRaises(SystemExit) as stopped:
                    runner.main()
            return stopped.exception.code, list(runner.results)
        finally:
            sys.argv = old_arguments

    def test_old_messages_cannot_replace_a_running_watcher(self):
        exit_code, empty = self.run_scenarios()
        self.assertEqual(exit_code, 1)
        self.assertEqual([passed for _, passed, _ in empty[1:4]], [False, False, False])

        exit_code, positive = self.run_scenarios(self_test=True)
        self.assertEqual(exit_code, 0)
        self.assertEqual(len(positive), 5)
        self.assertTrue(all(passed for _, passed, _ in positive))
        self.assertGreater(len(runner.received()), 0)

        exit_code, stale = self.run_scenarios()
        self.assertEqual(exit_code, 1)
        self.assertEqual([passed for _, passed, _ in stale[1:4]], [False, False, False])


if __name__ == "__main__":
    unittest.main()
