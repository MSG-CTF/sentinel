"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
require("../src/Sentinel.Api/wwwroot/architecture-model.js");
const {catalog} = globalThis.SentinelArchitecture;
const plan = require("../src/Sentinel.Api/wwwroot/asset-access.json");
test("every architecture role has a distinct access plan with the same track", () => {
  assert.equal(new Set(plan.assets.map(asset => asset.asset_id)).size, plan.assets.length);
  for (const node of catalog.nodes) {
    const asset = plan.assets.find(asset => asset.asset_id === node.id);
    assert.ok(asset, node.id);
    assert.equal(asset.track, node.track);
  }
});
test("production routes cannot be mistaken for configured credentials or enabled actions", () => {
  for (const asset of plan.assets) {
    assert.equal(asset.production.enabled, false);
    for (const key of ["host_id", "management_endpoint", "credential_reference", "service_binding"])
      assert.equal(asset.production[key], null);
  }
  assert.equal(plan.local_binding.asset_id, "internal-backend");
  assert.equal(plan.local_binding.service, "backend-internal");
  assert.equal(plan.local_binding.production_enabled, false);
  assert.equal(plan.local_bindings.length, 8);
  assert.equal(new Set(plan.local_bindings.map(binding => binding.asset_id)).size, 8);
  for (const binding of plan.local_bindings) {
    assert.equal(binding.project, "msg-sla-real-local");
    assert.equal(binding.production_enabled, false);
    assert.equal(binding.action, "start_stopped_service");
    assert.ok(binding.verification.includes("2회"));
    assert.ok(!binding.asset_id.endsWith("-db") && !binding.service.includes("redis"));
  }
});
