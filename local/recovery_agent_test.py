import copy
import datetime as dt
import json
import sqlite3
import unittest
from unittest.mock import patch
import uuid

import recovery_agent as agent


class ExecutorTests(unittest.TestCase):
    def container(self, asset_id=agent.ASSET):
        return {"Id": "a" * 64, "Config": {"Labels": {
            "com.docker.compose.project": agent.PROJECT,
            "com.docker.compose.service": agent.BINDINGS[asset_id][1], agent.SCOPE_LABEL: "local-" + asset_id}},
            "State": {"Status": "exited", "Running": False}}

    def request(self):
        return {"request_id": str(uuid.uuid4()), "asset_id": agent.ASSET, "track": "INTERNAL",
                "executor_id": agent.EXECUTOR, "action": agent.ACTION, "status": "QUEUED",
                "expires_at": (dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=90)).isoformat()}

    def test_external_other_project_running_and_oom_containers_are_rejected(self):
        self.assertTrue(agent.eligible_container(self.container()))
        for section, key, value in [("labels", "com.docker.compose.project", "another-project"),
                                    ("labels", "com.docker.compose.service", "backend-external"),
                                    ("labels", agent.SCOPE_LABEL, "other"),
                                    ("state", "Status", "running"), ("state", "OOMKilled", True)]:
            item = copy.deepcopy(self.container())
            (item["Config"]["Labels"] if section == "labels" else item["State"])[key] = value
            self.assertFalse(agent.eligible_container(item))

    def test_wrong_action_scope_and_expired_requests_are_rejected(self):
        self.assertTrue(agent.valid_request(self.request()))
        for field, value in [("action", "restart_vm"), ("asset_id", "external-backend"),
                             ("track", "EXTERNAL"), ("executor_id", "remote"),
                             ("expires_at", "2000-01-01T00:00:00Z"), ("request_id", "../../x")]:
            request = self.request()
            request[field] = value
            self.assertFalse(agent.valid_request(request))

    def test_multiple_container_bindings_are_rejected(self):
        self.assertIsNone(agent.stopped_container(lambda *args: "first\nsecond\n"))

    def test_duplicate_request_executes_once_and_restart_does_not_replay(self):
        connection = sqlite3.connect(":memory:")
        connection.execute("CREATE TABLE executions(request_id TEXT PRIMARY KEY, claim_id TEXT, phase TEXT, result_code TEXT, container_id TEXT, updated_at TEXT)")
        request = self.request()
        starts = []
        def run(*args):
            if args[0] == "ps":
                return "a" * 64
            if args[0] == "inspect":
                return json.dumps([self.container()])
            starts.append(args)
            return "a" * 64
        with patch.object(agent, "api", return_value={"claim_id": str(uuid.uuid4()), "request": {**request, "status": "CLAIMED"}}), patch.object(agent, "stopped_container", return_value="a" * 64):
            agent.execute(request, connection, run)
            agent.execute(request, connection, run)
            self.assertEqual(starts, [("start", "a" * 64)])
            connection.execute("UPDATE executions SET phase='EXECUTING'")
            connection.commit()
            agent.recover_interrupted(connection)
            agent.execute(request, connection, run)
            self.assertEqual(len(starts), 1)
            self.assertEqual(connection.execute("SELECT result_code FROM executions").fetchone()[0], "OUTCOME_UNKNOWN")
        connection.close()

    def test_every_binding_enforces_its_own_track_service_and_label(self):
        for asset_id, (track, service, dependencies) in agent.BINDINGS.items():
            request = {**self.request(), "asset_id": asset_id, "track": track}
            self.assertTrue(agent.valid_request(request))
            self.assertTrue(agent.eligible_container(self.container(asset_id), asset_id))
            self.assertFalse(agent.valid_request({**request, "track": "WRONG"}))
            other = self.container(asset_id)
            other["Config"]["Labels"][agent.SCOPE_LABEL] = "local-other"
            self.assertFalse(agent.eligible_container(other, asset_id))
        for asset_id in ("scheduler-db", "broker-db", "postgres-internal", "redis-internal", "runtime", "windows"):
            self.assertFalse(agent.valid_request({**self.request(), "asset_id": asset_id}))

    def test_stopped_or_unhealthy_dependencies_block_without_any_start(self):
        for asset_id, (_, _, dependencies) in agent.BINDINGS.items():
            target = self.container(asset_id)
            records = {}
            for service in dependencies:
                records[service] = {"Config": {"Labels": {"com.docker.compose.project": agent.PROJECT,
                    "com.docker.compose.service": service}}, "State": {"Status": "running", "Running": True}}
            def run(*args):
                if args[0] == "ps":
                    return args[-1].rsplit("=", 1)[1]
                if args[0] == "inspect":
                    return json.dumps([records.get(args[1], target)])
                self.fail("Inspection may not mutate containers")
            self.assertEqual(agent.stopped_container(run, asset_id), target["Id"])
            for state in ({"Status": "exited", "Running": False},
                          {"Status": "running", "Running": True, "Health": {"Status": "unhealthy"}}):
                records[dependencies[0]]["State"] = state
                self.assertIsNone(agent.stopped_container(run, asset_id))

    def test_claim_cannot_change_asset_or_track(self):
        connection = sqlite3.connect(":memory:")
        connection.execute("CREATE TABLE executions(request_id TEXT PRIMARY KEY, claim_id TEXT, phase TEXT, result_code TEXT, container_id TEXT, updated_at TEXT)")
        request = self.request()
        response = {"claim_id": str(uuid.uuid4()), "request": {**request, "status": "CLAIMED", "asset_id": "external-backend", "track": "EXTERNAL"}}
        with patch.object(agent, "api", return_value=response):
            with self.assertRaises(RuntimeError):
                agent.execute(request, connection, lambda *args: self.fail("Changed claim must not reach Docker"))
        connection.close()

    def test_inspection_reports_only_exact_stopped_bindings_without_metadata(self):
        stopped = self.container("scheduler")
        stopped["Config"]["Env"] = ["SECRET=not-for-public-output"]
        dependency = {"Id": "b" * 64, "Config": {"Labels": {"com.docker.compose.project": agent.PROJECT,
            "com.docker.compose.service": "scheduler-db"}}, "State": {"Status": "running", "Running": True}}
        records = [stopped, dependency]
        def run(*args):
            return "a b" if args[0] == "ps" else json.dumps(records)
        report = agent.inspect_bindings(run)
        self.assertEqual([item["asset_id"] for item in report["assets"] if item["stopped_eligible"]], ["scheduler"])
        self.assertNotIn("SECRET", json.dumps(report))
        records.append(stopped)
        self.assertFalse(any(item["stopped_eligible"] for item in agent.inspect_bindings(run)["assets"]))
        records.pop()
        dependency["State"] = {"Status": "exited", "Running": False}
        self.assertFalse(any(item["stopped_eligible"] for item in agent.inspect_bindings(run)["assets"]))


if __name__ == "__main__":
    unittest.main()
