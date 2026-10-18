"use strict";
const test = require("node:test"), assert = require("node:assert/strict");
require("../src/Sentinel.Api/wwwroot/asset-model.js");
const {buildAssets, riskCounts, riskText} = globalThis.SentinelAssets;
const now = Date.parse("2026-10-14T10:00:00Z");
const signal = {metric:"memory_used_percent",value:91,unit:"%",level:"CRITICAL",warning:80,critical:90,clear_below:85,recovering:false};
const target = {target_id:"discord-bot",name:"Bot",role:"DISCORD",track:"CONTROL",kind:"service_heartbeat",enabled:true,status:"HEALTHY",checked_at:new Date(now-60000).toISOString(),components:{memory_used_percent:"91"},risk:{level:"CRITICAL",signals:[signal]}};
const snapshot = {updated_at:new Date(now).toISOString(),poll_interval_seconds:15,stale_after_seconds:39,targets:[target]};

test("CPU risk remains visible and never replaces missing host facts with zeros",()=>{
  const [asset]=buildAssets({...snapshot,targets:[{...target,components:{cpu_used_percent:"98",os_name:"Debian 13"},risk:{level:"CRITICAL",signals:[{...signal,metric:"cpu_used_percent",value:98,critical:95}]}}]},{now});
  assert.equal(riskText(asset),"CPU 98% · 위험 ≥95%");
  assert.equal(asset.components.os_name,"Debian 13"); assert.equal(asset.components.memory_total_bytes,undefined);
});
test("one minute heartbeats stay fresh when HTTP probes run every 15 seconds",()=>{
  const [asset] = buildAssets(snapshot,{now});
  assert.equal(asset.status,"HEALTHY"); assert.equal(asset.risk.level,"CRITICAL"); assert.equal(asset.components.memory_used_percent,"91");
  assert.equal(riskCounts([asset]).CRITICAL,1); assert.equal(riskCounts([asset]).NORMAL,0);
  assert.equal(riskText(asset),"메모리 91% · 위험 ≥90%");
});
test("a frozen collector invalidates a still recent heartbeat",()=>{
  const [asset] = buildAssets({...snapshot,updated_at:new Date(now-40000).toISOString()},{now});
  assert.equal(asset.risk.level,"UNKNOWN"); assert.deepEqual(asset.risk.signals,[]);
});
test("offline and overdue heartbeat evidence lose old red and green labels",()=>{
  for (const options of [{now,offline:true},{now:now+151000}]) {
    const [asset] = buildAssets(snapshot,options); assert.equal(asset.risk.level,"UNKNOWN"); assert.deepEqual(asset.risk.signals,[]);
  }
});
test("latency risk on the backend does not turn healthy databases red",()=>{
  const api={...target,target_id:"backend",kind:"backend_healthz",http_status_code:200,checked_at:new Date(now).toISOString(),reason_code:"BACKEND_READY",components:{status:"ok",database:"ok",cache:"ok"},risk:{level:"WARNING",signals:[{...signal,metric:"latency_ms",unit:"ms",value:1200,level:"WARNING",warning:1000,critical:3000}]}};
  const assets=buildAssets({...snapshot,targets:[api]},{now});
  assert.deepEqual(assets.map(a=>a.risk.level),["WARNING","NORMAL","NORMAL"]);
});
test("HTML health responses cannot display inherited healthy risk",()=>{
  const api={...target,kind:"backend_healthz",http_status_code:200,checked_at:new Date(now).toISOString(),components:{},risk:{level:"NORMAL",signals:[]}};
  assert.ok(buildAssets({...snapshot,targets:[api]},{now}).every(a=>a.risk.level==="UNKNOWN"));
});
test("disabled target and derived children are never an active warning",()=>{
  assert.equal(buildAssets({...snapshot,targets:[{...target,enabled:false}]},{now})[0].risk.level,"PENDING");
});
test("recovering measurement explains a deliberately retained warning",()=>{
  const [asset]=buildAssets({...snapshot,targets:[{...target,risk:{level:"CRITICAL",signals:[{...signal,value:84,recovering:true}]}}]},{now});
  assert.equal(riskText(asset),"메모리 84% · 회복 확인 중");
});

test("a brief CPU spike stays visible without a yellow core",()=>{
  const pending={...signal,metric:"cpu_used_percent",value:99,level:"NORMAL",pending_level:"CRITICAL",warning:90,critical:98};
  const [asset]=buildAssets({...snapshot,targets:[{...target,risk:{level:"NORMAL",signals:[pending]}}]},{now});
  assert.equal(asset.risk.level,"NORMAL"); assert.equal(riskCounts([asset]).WARNING,0); assert.equal(riskCounts([asset]).CRITICAL,0);
  assert.equal(riskText(asset),"CPU 99% · 높은 부하 관찰 중");
});

test("an actual warning remains visible while another metric is being observed",()=>{
  const [asset]=buildAssets({...snapshot,targets:[{...target,risk:{level:"WARNING",signals:[
    {...signal,metric:"cpu_used_percent",value:99,level:"NORMAL",pending_level:"CRITICAL"},
    {...signal,metric:"disk_used_percent",value:92,level:"WARNING",warning:90,critical:95}
  ]}}]},{now});
  assert.equal(riskText(asset),"디스크 92% · 주의 ≥90%");
});
