"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
require("../src/Sentinel.Api/wwwroot/asset-model.js");
require("../src/Sentinel.Api/wwwroot/architecture-model.js");
const {buildAssets, summarize} = globalThis.SentinelAssets;
const {catalog, resolveNodes, resolveLink, sourceLink} = globalThis.SentinelArchitecture;
const now = Date.parse("2026-10-07T10:20:00Z");
const target = overrides => ({target_id:"internal-backend", name:"내부 백엔드", role:"BACKEND", track:"INTERNAL", kind:"backend_healthz", enabled:true,
  status:"HEALTHY", reason_code:"BACKEND_READY", checked_at:new Date(now-1000).toISOString(), http_status_code:200, latency_ms:14,
  url:"http://backend-internal:8080/healthz", components:{status:"ok",database:"ok",cache:"ok"}, ...overrides});
const readings = (overrides,options={}) => buildAssets({targets:[target(overrides)],poll_interval_seconds:15},{now,...options});
const dependency = catalog.links.find(link=>link.id==="internal-database");

test("catalog IDs are unique and every connection has two real catalog endpoints",()=>{
  const ids = new Set(catalog.nodes.map(item=>item.id));
  assert.equal(ids.size,catalog.nodes.length);
  assert.equal(new Set(catalog.links.map(item=>item.id)).size,catalog.links.length);
  for (const link of catalog.links) {assert.ok(ids.has(link.from),link.id); assert.ok(ids.has(link.to),link.id);}
});
test("source-only paths do not become verified when both endpoints answer",()=>{
  const assets = readings();
  for (const link of catalog.links.filter(item=>item.stage!=="PROBE")) {const resolved=resolveLink(link,assets); assert.equal(resolved.state,link.stage); assert.equal(resolved.verified,false);}
});
test("four healthz and two explicit Runtime links can claim current connection evidence",()=>{
  assert.equal(catalog.links.filter(item=>item.stage==="PROBE").length,6);
  assert.equal(resolveLink(dependency,readings()).state,"VERIFIED");
});
test("Runtime links require fresh matching typed observations and fail closed offline",()=>{
  const timestamp=new Date().toISOString();
  const parent={asset_id:"runtime",kind:"runtime_status",status:"HEALTHY",reason_code:"RUNTIME_WORKLOAD_READY",checked_at:timestamp};
  for(const [id,kind,reason] of [["runtime-store","runtime_operation","RUNTIME_STORE_QUERY_READY"],["runtime-k3s","runtime_node","K3S_NODE_READY"]]) {
    const link=catalog.links.find(item=>item.id===id);
    const child={asset_id:link.evidence_asset,kind,status:"HEALTHY",reason_code:reason,checked_at:timestamp};
    assert.equal(resolveLink(link,[parent,child]).state,"VERIFIED");
    for(const changed of [{status:"STALE"},{reason_code:"HTTP_REACHABLE"},{checked_at:new Date(Date.now()-90000).toISOString()},{kind:"http"}])
      assert.equal(resolveLink(link,[parent,{...child,...changed}]).state,"UNKNOWN");
    assert.equal(resolveLink(link,[{...parent,status:"DOWN"},child]).state,"UNKNOWN");
  }
});
test("database failure is red while Redis connection stays verified",()=>{
  const assets=readings({status:"DOWN",http_status_code:503,reason_code:"DATABASE_DOWN",components:{status:"down",database:"down",cache:"ok"}});
  assert.equal(resolveLink(dependency,assets).state,"FAILED");
  assert.equal(resolveLink(catalog.links.find(link=>link.id==="internal-cache"),assets).state,"VERIFIED");
});
test("no green connections remain when the browser is offline or the snapshot is stale",()=>{
  for (const assets of [readings({}, {offline:true}),readings({checked_at:new Date(now-90000).toISOString()})]) {
    assert.equal(resolveLink(dependency,assets).state,"UNKNOWN");
    assert.equal(resolveLink(dependency,assets).checked_at,null);
  }
});
test("HTML 200 and timeout cannot prove a database connection",()=>{
  for (const assets of [readings({status:"UNVERIFIED",reason_code:"EXPECTED_JSON_RECEIVED_OTHER",components:{}}),readings({status:"DOWN",reason_code:"TIMEOUT",http_status_code:null,components:{}})]) assert.equal(resolveLink(dependency,assets).state,"UNKNOWN");
});
test("missing parent and mismatched evidence timestamps are never verified",()=>{
  const assets=readings();
  assert.equal(resolveLink(dependency,assets.slice(1)).state,"UNKNOWN");
  assets[1].checked_at=new Date(now-2000).toISOString();
  assert.equal(resolveLink(dependency,assets).state,"UNKNOWN");
});
test("planned nodes cannot inflate monitored asset counts or percentage",()=>{
  const assets=readings(),before=summarize(assets);
  const mapped=resolveNodes(assets);
  assert.ok(mapped.length>assets.length);
  assert.equal(mapped.filter(item=>item.reading).length,assets.length);
  assert.deepEqual(summarize(assets),before);
  assert.ok(mapped.filter(item=>!item.reading).every(item=>!item.status));
});
test("public probes stay PUBLIC and are never assigned an internal or external track",()=>{
  const assets=buildAssets({targets:[target({target_id:"public-backend",track:"PUBLIC"})],poll_interval_seconds:15},{now});
  const mapped=resolveNodes(assets);
  assert.ok(mapped.filter(item=>item.reading).every(item=>item.track==="PUBLIC"));
  assert.equal(mapped.find(item=>item.id==="internal-backend").reading,null);
});
test("blocked integration stays blocked regardless of service health",()=>{
  assert.equal(resolveLink(catalog.links.find(item=>item.id==="internal-create"),readings()).state,"BLOCKED");
  assert.equal(resolveLink(catalog.links.find(item=>item.id==="bundle-poll"),readings()).state,"BLOCKED");
});
test("all source links point at pinned SHA or the supplied Notion page",()=>{
  for (const [key,source] of Object.entries(catalog.sources)) {const link=sourceLink(key); assert.ok(link.url.startsWith("https://")); if (source.sha) {assert.match(source.sha,/^[a-f0-9]{40}$/); assert.ok(link.url.includes(source.sha));}}
  assert.equal(sourceLink("unknown"),null);
});
test("KoTH score and token contracts are recorded for both tracks",()=>{
  for (const track of ["internal","external"]) {assert.ok(catalog.links.some(item=>item.id===`${track}-score-poll`)); assert.ok(catalog.links.some(item=>item.id===`${track}-token`));}
});
