"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
require("../src/Sentinel.Api/wwwroot/data-recovery.js");
const {valid} = globalThis.SentinelDataRecovery;
const {validCutover} = globalThis.SentinelDataRecovery;
function report() {
  const ids = ["internal-backend-database", "external-backend-database", "scheduler-db", "broker-db", "internal-backend-cache", "external-backend-cache"];
  return {schema_version:1, scope:"isolated_local_restore_drill", automatic_restore_enabled:false, passed:true,
    started_at:new Date(Date.now()-20000).toISOString(), completed_at:new Date(Date.now()-10000).toISOString(),
    source_unchanged:true, lab_removed:true,
    assets:ids.map((asset_id,i)=>({asset_id,passed:true,...(i<4 ? {kind:"postgres",tables:2,rows:3,row_hashes_match:true} : {kind:"redis",snapshot_keys:0,aof_enabled:true,canary:{passed:true,aof_restart_verified:true}})})),
    applications:["backend-internal", "backend-external", "scheduler", "broker"].map(service=>({service,passed:true}))};
}
test("a complete dated drill can be shown as passed", ()=>assert.equal(valid(report()),true));
test("partial success cannot be shown as a completed six-store drill", ()=>{
  const value=report(); value.assets.pop(); assert.equal(valid(value),false);
  value.passed=false; assert.equal(valid(value),true);
});
test("duplicate assets or applications cannot substitute for an untested target", ()=>{
  for (const field of ["assets","applications"]) {const value=report();value[field][0]=value[field][1];assert.equal(valid(value),false);}
});
test("invalid or future timestamps cannot claim current evidence", ()=>{
  for (const time of [null,"unknown",new Date(Date.now()+60000).toISOString()]) {const value=report();value.completed_at=time;assert.equal(valid(value),false);}
});
test("cleanup and source-preservation failures prevent success", ()=>{
  for (const key of ["source_unchanged","lab_removed"]) {const value=report();value[key]=false;assert.equal(valid(value),false);}
});
test("unverified row hashes and AOF restart cannot claim restored data", ()=>{
  const sql=report();sql.assets[0].row_hashes_match=false;assert.equal(valid(sql),false);
  const redis=report();redis.assets[5].canary.aof_restart_verified=false;assert.equal(valid(redis),false);
  const malformed=report();malformed.assets[0]=null;assert.equal(valid(malformed),false);
});
test("an isolated drill cannot be presented as enabled automatic restore", ()=>{
  const value=report();value.automatic_restore_enabled=true;assert.equal(valid(value),false);
});
test("service cutover success requires both restored and original application checks",()=>{
  const sample=report();
  const value={...sample,scope:"local_service_cutover_round_trip",automatic_data_restore_enabled:false,
    rollback_applications:structuredClone(sample.applications),restored_volume_bindings_verified:true,original_volume_bindings_verified:true,restore_lab_removed:true};
  assert.equal(validCutover(value),true);
  for(const key of ["restored_volume_bindings_verified","original_volume_bindings_verified","restore_lab_removed"]) assert.equal(validCutover({...value,[key]:false}),false);
  assert.equal(validCutover({...value,rollback_applications:[]}),false);
  assert.equal(validCutover({...value,automatic_data_restore_enabled:true}),false);
  assert.equal(validCutover({...value,completed_at:new Date(Date.now()+60000).toISOString()}),false);
});
