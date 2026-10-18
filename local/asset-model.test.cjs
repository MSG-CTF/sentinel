"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
require("../src/Sentinel.Api/wwwroot/asset-model.js");
const {buildAssets, summarize} = globalThis.SentinelAssets;
const now = Date.parse("2026-10-07T08:30:00Z");
const target = overrides => ({target_id:"internal-backend", name:"내부 백엔드", role:"BACKEND", track:"INTERNAL",
  kind:"backend_healthz", enabled:true, status:"HEALTHY", reason_code:"BACKEND_READY", checked_at:new Date(now - 1000).toISOString(),
  http_status_code:200, latency_ms:12, url:"http://backend-internal:8080/healthz", components:{status:"ok", database:"ok", cache:"ok"}, ...overrides});
const build = (item, options = {}) => buildAssets({targets:[target(item)], poll_interval_seconds:15}, {now, ...options});

test("cluster reports survive minute polling and stop showing resources after report expiry", () => {
  const item = {kind:"cluster_heartbeat", role:"K3S", components:{cpu_used_percent:"12.5"}, checked_at:new Date(now - 80000).toISOString()};
  const live = build(item)[0];
  assert.equal(live.status, "HEALTHY");
  assert.equal(live.components.cpu_used_percent, "12.5");
  const stale = build({...item, checked_at:new Date(now - 151000).toISOString()})[0];
  assert.equal(stale.status, "STALE");
  assert.equal(stale.risk.level, "UNKNOWN");
});

test("backend healthz creates two evidence-backed dependency assets", () => {
  const assets = build();
  assert.deepEqual(assets.map(asset => asset.status), ["HEALTHY", "HEALTHY", "HEALTHY"]);
  assert.equal(assets[1].parent_target_id, "internal-backend");
  assert.equal(assets[1].evidence_type, "HEALTHZ_COMPONENT");
  assert.equal(assets[1].latency_ms, null);
  assert.equal(assets[1].http_status_code, null);
});
test("database failure marks API degraded, database down, Redis healthy", () => {
  const assets = build({status:"DOWN", http_status_code:503, reason_code:"DATABASE_DOWN", components:{status:"down", database:"down", cache:"ok"}});
  assert.deepEqual(assets.map(asset => asset.status), ["DEGRADED", "DOWN", "HEALTHY"]);
});
test("cache failure does not invent a database failure", () => {
  const assets = build({status:"DOWN", http_status_code:503, reason_code:"CACHE_DOWN", components:{status:"down", database:"ok", cache:"down"}});
  assert.deepEqual(assets.map(asset => asset.status), ["DEGRADED", "HEALTHY", "DOWN"]);
});
test("both dependencies down still proves API answered", () => {
  const assets = build({status:"DOWN", http_status_code:503, reason_code:"DATABASE_AND_CACHE_DOWN", components:{status:"down", database:"down", cache:"down"}});
  assert.deepEqual(assets.map(asset => asset.status), ["DEGRADED", "DOWN", "DOWN"]);
});
test("timeout cannot determine a dependency failure", () => {
  const assets = build({status:"DOWN", http_status_code:null, reason_code:"TIMEOUT", components:{}});
  assert.deepEqual(assets.map(asset => asset.status), ["DOWN", "UNVERIFIED", "UNVERIFIED"]);
  assert.equal(assets[1].checked_at, null);
});
test("HTML 200 cannot become a healthy backend or database", () => {
  const assets = build({status:"UNVERIFIED", reason_code:"EXPECTED_JSON_RECEIVED_OTHER", components:{}});
  assert.deepEqual(assets.map(asset => asset.status), ["UNVERIFIED", "UNVERIFIED", "UNVERIFIED"]);
});
test("unverified parent cannot reuse old ok components", () => {
  const assets = build({status:"UNVERIFIED", reason_code:"INCONSISTENT_HEALTH_CONTRACT"});
  assert.deepEqual(assets.map(asset => asset.status), ["UNVERIFIED", "UNVERIFIED", "UNVERIFIED"]);
});
test("stale parent removes healthy dependency display", () => {
  assert.deepEqual(build({status:"STALE"}).map(asset => asset.status), ["STALE", "STALE", "STALE"]);
});
test("offline browser invalidates measured evidence", () => {
  assert.deepEqual(build({}, {offline:true}).map(asset => asset.status), ["STALE", "STALE", "STALE"]);
});
test("disabled assets never inherit a prior check timestamp or healthy state", () => {
  const assets = build({enabled:false});
  assert.deepEqual(assets.map(asset => asset.status), ["UNCONFIGURED", "UNCONFIGURED", "UNCONFIGURED"]);
  assert.ok(assets.every(asset => asset.checked_at === null));
});
test("pending assets stay pending", () => {
  assert.deepEqual(build({status:"PENDING", checked_at:null}).map(asset => asset.status), ["PENDING", "PENDING", "PENDING"]);
});
test("missing health contract cannot claim healthy", () => {
  assert.ok(build({components:{}}).every(asset => asset.status === "UNVERIFIED"));
});
test("wrong dependency reason does not provide component evidence", () => {
  const assets = build({status:"DOWN", http_status_code:503, reason_code:"DATABASE_DOWN", components:{status:"down", database:"ok", cache:"down"}});
  assert.ok(assets.every(asset => asset.status === "UNVERIFIED"));
});
test("HTTP 200 cannot validate a down contract", () => {
  const assets = build({status:"DOWN", http_status_code:200, reason_code:"DATABASE_DOWN", components:{status:"down", database:"down", cache:"ok"}});
  assert.ok(assets.every(asset => asset.status === "UNVERIFIED"));
});
test("generic HTTP site has no implied DB or Redis assets", () => {
  assert.equal(build({kind:"http", role:"FRONTEND", reason_code:"HTTP_REACHABLE", components:{}}).length, 1);
});
test("frozen snapshot ages out even when browser can fetch it", () => {
  assert.ok(build({checked_at:new Date(now - 70000).toISOString()}).every(asset => asset.status === "STALE"));
});
test("missing or future check timestamp cannot prove fresh health", () => {
  for (const at of [null, "invalid", new Date(now + 31000).toISOString()]) {
    assert.ok(build({checked_at:at}).every(asset => asset.status === "STALE"));
  }
});
test("unknown status is not healthy", () => {
  assert.ok(build({status:"NEW_STATUS"}).every(asset => asset.status === "UNVERIFIED"));
});
test("coverage separates unknown and unconfigured from assessed health", () => {
  const counts = summarize([{status:"HEALTHY"}, {status:"DOWN"}, {status:"DEGRADED"}, {status:"UNCONFIGURED"}, {status:"UNVERIFIED"}]);
  assert.equal(counts.observed, 3);
  assert.equal(counts.coverage_percent, 60);
  assert.equal(counts.healthy_percent, 33.3);
});
test("no measured assets means no healthy percentage", () => {
  assert.equal(summarize([{status:"UNCONFIGURED"}]).healthy_percent, null);
  assert.equal(summarize([]).coverage_percent, null);
});
test("local profile has six healthy assets, not eleven direct probes", () => {
  const items = [target(), target({target_id:"external-backend", track:"EXTERNAL"}), ...["scheduler", "broker", "runtime", "koth", "windows"].map(id => target({target_id:id, kind:"http", enabled:false, role:id.toUpperCase()}))];
  const assets = buildAssets({targets:items, poll_interval_seconds:15}, {now});
  const counts = summarize(assets);
  assert.equal(counts.total, 11);
  assert.equal(counts.healthy, 6);
  assert.equal(counts.pending, 5);
  assert.equal(counts.coverage_percent, 54.5);
  assert.equal(assets.filter(asset => asset.evidence_type === "DIRECT_PROBE" && asset.enabled).length, 2);
});
