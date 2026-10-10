"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
require("../src/Sentinel.Api/wwwroot/asset-model.js");
require("../src/Sentinel.Api/wwwroot/architecture-model.js");
const {buildAssets, summarize} = globalThis.SentinelAssets;
const {catalog, resolveNodes} = globalThis.SentinelArchitecture;
const read = name => JSON.parse(fs.readFileSync(path.join(__dirname,"../src/Sentinel.Api/profiles",name+".json"),"utf8"));

test("both local profiles cover all 25 roles exactly once including explicit missing inputs", () => {
  for (const profile of ["local","local-recovery"]) {
    const config = read(profile).platform_monitor;
    const raw = config.targets.map(target => ({...target, status:"UNCONFIGURED", enabled:target.enabled !== false}));
    const assets = buildAssets({...config,targets:raw});
    assert.deepEqual(assets.map(a=>a.asset_id).sort(),catalog.nodes.map(n=>n.id).sort());
    assert.equal(resolveNodes(assets).length,25);
    for (const target of config.targets.filter(t=>t.enabled===false)) {
      assert.ok(target.unconfigured_reason);
      assert.ok(target.evidence_scope);
      assert.equal(assets.find(a=>a.asset_id===target.target_id).checked_at,null);
    }
  }
  assert.deepEqual(read("local").platform_monitor.targets,read("local-recovery").platform_monitor.targets);
});

test("source availability and an empty control inventory cannot turn absent workloads green", () => {
  const config=read("local").platform_monitor;
  const checked_at=new Date().toISOString();
  const raw=config.targets.map(t=>({...t, enabled:t.enabled!==false, status:t.enabled===false?"UNCONFIGURED":"HEALTHY",
    checked_at, http_status_code:200, reason_code:t.kind==="backend_healthz"?"BACKEND_READY":"HEALTH_CONTRACT_MATCHED",
    components:t.kind==="backend_healthz"?{status:"ok",database:"ok",cache:"ok"}:{inventory_total:"0"}}));
  const assets=buildAssets({...config,targets:raw});
  const counts=summarize(assets);
  assert.equal(counts.total,25); assert.equal(counts.healthy,13); assert.equal(counts.pending,12);
  for (const id of ["runtime","runtime-db","linux-pool","koth","windows","cloud-api","koth-score","ghcr"])
    assert.equal(assets.find(a=>a.asset_id===id).status,"UNCONFIGURED");
  assert.equal(summarize(buildAssets({...config,targets:raw},{offline:true})).healthy,0);
});
