"""Run inside the lab Broker only, with the bounded local observation on stdin."""
import json
import sys
from datetime import datetime, timezone
from sqlalchemy import select
from app.db.session import get_session_factory
from app.db.tables import ProviderAccountTable, ResourceTargetTable
from app.domain.enums import Architecture, Provider, RuntimeType

data = json.load(sys.stdin)
assert data["scope"] == "sentinel_local_functional_fixture" and data["node_ready"] is True
assert data["target_id"] == "sentinel-local-k3s"
account_key = "sentinel-local-functional-fixture-not-cloud"
now = datetime.now(timezone.utc)
with get_session_factory()() as session:
    account = session.scalar(select(ProviderAccountTable).where(ProviderAccountTable.external_account_id == account_key))
    if account is None:
        account = ProviderAccountTable(provider=Provider.AWS, external_account_id=account_key,
            display_name="LOCAL fixture: cloud account and quota unverified", auth_method="LOCAL_FIXTURE",
            provider_config={"local_test_only": True, "cloud_api_verified": False}, enabled=True)
        session.add(account)
        session.flush()
    assert account.auth_method == "LOCAL_FIXTURE" and account.provider_config.get("local_test_only") is True
    target = session.scalar(select(ResourceTargetTable).where(ResourceTargetTable.target_id == data["target_id"]))
    if target is None:
        target = ResourceTargetTable(account_id=account.account_id, provider_instance_id="sentinel-local-k3s-container",
            provider_scope_id="local-fixture-only", instance_name="sentinel-local-functional-fixture", region="local-docker",
            target_id=data["target_id"])
        session.add(target)
    assert target.account_id == account.account_id and target.provider_scope_id == "local-fixture-only"
    target.provider_instance_state = "RUNNING"
    target.provider_machine_type = "LOCAL_DOCKER_CONTAINER"
    target.runtime_type = RuntimeType.KUBERNETES
    target.architecture = Architecture.AMD64
    # Deliberate test budget, not a provider quota or a node-agent measurement
    target.provider_capacity_cpu_millicores = 1000
    target.provider_capacity_memory_mib = 512
    target.provider_capacity_storage_mib = 1024
    target.allocatable_cpu_millicores = 500
    target.allocatable_memory_mib = 256
    target.allocatable_ephemeral_storage_mib = 512
    target.runtime_cpu_usage_millicores = 0
    target.runtime_memory_usage_mib = 0
    target.enabled = True
    target.ready = True
    target.retired_at = None
    target.runtime_observed_at = now
    target.runtime_last_seen_at = now
    target.observed_at = now
    target.last_seen_at = now
    session.commit()
    print(json.dumps({"account_id": str(account.account_id), "resource_target_id": str(target.resource_target_id),
        "target_id": target.target_id, "scope": "local_fixture_capacity", "cloud_verified": False, "node_agent_verified": False}))
