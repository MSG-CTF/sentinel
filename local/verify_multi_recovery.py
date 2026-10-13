"""Stop each allowlisted local app and verify the actual executor restores the same container."""
import datetime as dt
import json
import time

import recovery_agent as agent
from verify_assets import get, snapshot, containers, BASE, LOCAL


def wait_for(predicate, description, timeout=240):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        current = snapshot()
        if predicate(current):
            return current
        time.sleep(2)
    raise AssertionError("Deadline: " + description)


def ready(current):
    return (current["mode"] == "automatic_local" and current["recovery"]["executor_connected"]
            and current["recovery"]["storage_healthy"] and current["event_storage_healthy"]
            and all(item["status"] == "HEALTHY" for item in current["targets"] if item["enabled"]))


def forwarded_responses_match():
    # A known unauthenticated route proves real API forwarding without changing account data.
    endpoint = "/api/v1/challenges/00000000-0000-0000-0000-000000000000"
    for backend, frontend, edge in ((58180, 58184, 58186), (58181, 58185, 58187)):
        expected = get(f"http://127.0.0.1:{backend}" + endpoint)
        assert expected[0] in (401, 404) and expected[1] == "application/json"
        for port in (frontend, edge):
            actual = get(f"http://127.0.0.1:{port}" + endpoint)
            assert actual[0] == expected[0] and actual[1] == expected[1]
            assert json.loads(actual[2]) == json.loads(expected[2])
            page = get(f"http://127.0.0.1:{port}/")
            assert page[0] == 200 and b'<div id="root"></div>' in page[2]


def main():
    initial = wait_for(ready, "healthy local baseline")
    from verify_recovery import response_code
    assert response_code("/requests/next") == 401
    assert response_code("/inspections", {"executorId": agent.EXECUTOR, "assets": []}, True) == 400
    assert response_code("/inspections", {"executor_id": agent.EXECUTOR,
        "assets": [{"asset_id": "scheduler-db", "stopped_eligible": True}]}, True) == 400
    assert set(item["asset_id"] for item in initial["recovery"]["assets"]) == set(agent.BINDINGS)
    history = initial["recovery"]["requests"]
    baseline_ids = {item["request_id"] for item in history}
    baseline_containers = containers()
    required_services = {service for _, service, _ in agent.BINDINGS.values()}
    required_services.update(dependency for _, _, dependencies in agent.BINDINGS.values() for dependency in dependencies)
    assert required_services | {"sentinel"} <= baseline_containers.keys()
    stopped = set()
    results = []
    started = dt.datetime.now(dt.timezone.utc)
    report_path = LOCAL / "artifacts" / ("multi-recovery-" + started.strftime("%Y%m%dT%H%M%SZ") + ".json")
    report = {"passed": False, "started_at": started.isoformat(), "results": results, "inspection_contract_checks": 3,
              "historical_request_count": len(history), "scope": "allowlisted_local_applications",
              "expected_assets": sorted(agent.BINDINGS)}
    try:
        for asset_id, (track, service, dependencies) in agent.BINDINGS.items():
            wait_for(lambda current: ready(current) and next(item for item in current["recovery"]["assets"]
                     if item["asset_id"] == asset_id)["armed"], asset_id + " armed baseline", 420)
            before = containers()
            record = before[service]
            assert record["status"] == "running" and str(record["oneoff"]).lower() == "false"
            inspection = json.loads(agent.docker("inspect", record["id"]))[0]
            labels = inspection["Config"]["Labels"]
            assert labels["com.docker.compose.project"] == agent.PROJECT
            assert labels[agent.SCOPE_LABEL] == "local-" + asset_id
            previous_ids = {item["request_id"] for item in snapshot()["recovery"]["requests"]}
            print("Stopping exact local app: " + asset_id, flush=True)
            stopped.add(record["id"])
            agent.docker("stop", "--time", "5", record["id"])
            done = wait_for(lambda current: any(item["asset_id"] == asset_id and item["request_id"] not in previous_ids
                and item["status"] == "SUCCEEDED" for item in current["recovery"]["requests"]), asset_id + " functional recovery")
            requests = [item for item in done["recovery"]["requests"] if item["request_id"] not in previous_ids]
            assert len(requests) == 1 and requests[0]["asset_id"] == asset_id and requests[0]["track"] == track
            assert requests[0]["execution_result_code"] == "CONTAINER_STARTED"
            assert requests[0]["reason_code"] == "FUNCTION_VERIFIED"
            wait_for(ready, "all enabled probes healthy")
            after = containers()
            assert record["id"] == after[service]["id"] and record["started_at"] != after[service]["started_at"]
            assert after[service]["status"] == "running"
            assert all(before[name] == after[name] for name in before if name != service)
            stopped.remove(record["id"])
            forwarded_responses_match()
            results.append({"asset_id": asset_id, "service": service, "request_id": requests[0]["request_id"],
                "status": "SUCCEEDED", "same_container": True, "other_containers_unchanged": True,
                "both_tracks_http_forwarding_verified": True, "completed_at": requests[0]["completed_at"]})
            report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print("Verified automatic recovery: " + asset_id, flush=True)
        final = wait_for(ready, "final healthy baseline")
        new = [item for item in final["recovery"]["requests"] if item["request_id"] not in baseline_ids]
        assert len(new) == len(agent.BINDINGS) and all(item["status"] == "SUCCEEDED" for item in new)
        assert set(item["asset_id"] for item in new) == set(agent.BINDINGS)
        current = containers()
        data_services = ["postgres-internal", "postgres-external", "redis-internal", "redis-external", "scheduler-db", "broker-db", "runtime-db"]
        assert all(current[name] == baseline_containers[name] for name in data_services)
        report.update(passed=True, new_request_count=len(new), data_services_unchanged=True,
                      final_healthy_direct_probes=sum(t["status"] == "HEALTHY" for t in final["targets"]),
                      completed_at=dt.datetime.now(dt.timezone.utc).isoformat())
    finally:
        for identifier in stopped:
            agent.docker("start", identifier)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "assets": len(results), "report": str(report_path)}), flush=True)


if __name__ == "__main__":
    main()
