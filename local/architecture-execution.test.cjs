"use strict";
const test=require("node:test"),assert=require("node:assert/strict"),fs=require("node:fs"),path=require("node:path"),vm=require("node:vm");
const folder=path.join(__dirname,"../src/Sentinel.Api/wwwroot");
const evidence=JSON.parse(fs.readFileSync(path.join(folder,"architecture-execution-evidence.json"),"utf8"));
const source=fs.readFileSync(path.join(folder,"architecture-model.js"),"utf8");
const copy=value=>JSON.parse(JSON.stringify(value));
function load(proof=evidence){const context={SentinelExecutionEvidence:copy(proof)};vm.runInNewContext(source,context);return context.SentinelArchitecture;}
function link(api,id){return api.catalog.links.find(l=>l.id===id);}

test("published records bind twelve actual connections and contain full cleanup and source provenance",()=>{
  const api=load(),ids=evidence.runs.flatMap(r=>r.connections.map(l=>l.id));
  assert.equal(ids.length,12);assert.equal(new Set(ids).size,12);
  assert.equal(evidence.cleanup.namespaces_removed,evidence.cleanup.temporary_instances_created);
  assert.equal(evidence.cleanup.reservations_released,evidence.cleanup.temporary_instances_created);
  assert.equal(evidence.cleanup.participant_points_unchanged,true);
  assert.match(evidence.backend_source_sha,/^[a-f0-9]{40}$/);assert.match(evidence.runtime_artifact_sha256,/^[a-f0-9]{64}$/);
  for(const id of ids){const result=api.resolveLink(link(api,id),[]);assert.equal(result.state,"EXECUTED",id);assert.equal(result.verified,false,id);assert.equal(result.executed,true,id);assert.ok(result.checked_at);}
  assert.equal(evidence.runs.find(r=>r.run_id==="score-collection").checks.positive_award_count,1);
  assert.equal(evidence.runs.find(r=>r.run_id==="game-backend-token-verifier").checks.native_game_handlers_verified,6);
});

test("a dated execution does not claim current reachability or invent a monitored asset",()=>{
  const api=load(),assets=[];
  assert.equal(api.resolveLink(link(api,"external-create"),assets).verified,false);
  assert.equal(api.resolveNodes(assets).filter(n=>n.reading).length,0);
  assert.equal(api.monitoringLinks(assets).every(l=>l.members.length===0),true);
  assert.ok(api.resolveLink(link(api,"external-token"),assets).execution.limits.includes("로그인"));
});

test("internal evidence stays scoped to its six games and does not credit ordinary instance creation",()=>{
  const api=load();
  for(const id of ["internal-create","internal-inventory"]){const result=api.resolveLink(link(api,id),[]);assert.equal(result.executed,undefined,id);assert.equal(result.verified,false,id);}
  for(const id of ["internal-score-poll","internal-token"]){const result=api.resolveLink(link(api,id),[]);assert.equal(result.state,"EXECUTED",id);assert.equal(result.execution.track,"INTERNAL");assert.equal(result.execution.game_results.length,6);}
  const changed=copy(evidence),record=changed.runs.find(r=>r.run_id==="backend-broker-inventory");record.connections[0].id="internal-inventory";
  const altered=load(changed);assert.equal(altered.executionFor(link(altered,"internal-inventory")),null);
});

test("wrong track, external origin, missing or duplicated internal game results fail closed",()=>{
  for(const patch of [{track:"EXTERNAL"},{backend_origin:"https://msgctf.kr"},{game_results:[]},{cleanup_verified:false}]){
    const proof=copy(evidence),record=proof.runs.find(r=>r.run_id==="internal-koth-native-games");Object.assign(record,patch);const api=load(proof);
    assert.equal(api.executionFor(link(api,"internal-token")),null);
  }
  for(const change of [games=>games[0].passed=false,games=>games[0].name=games[1].name]){
    const proof=copy(evidence);change(proof.runs.find(r=>r.run_id==="internal-koth-native-games").game_results);const api=load(proof);
    assert.equal(api.executionFor(link(api,"internal-token")),null);
  }
});

test("incomplete cleanup, simulated tests, missing digest or future evidence fail closed",()=>{
  for(const patch of [{cleanup_verified:false},{passed:false},{simulated:true},{verification_mode:"mock"},{proof_sha256:""},{checked_at:new Date(Date.now()+600000).toISOString()},{checked_at:"bad"},{summary:""},{limits:""}]){
    const proof=copy(evidence);Object.assign(proof.runs[0],patch);const api=load(proof);
    assert.equal(api.resolveLink(link(api,"external-create"),[]).executed,undefined,JSON.stringify(patch));
  }
});

test("malformed, absent and ambiguous records leave the declared verification stage",()=>{
  for(const proof of [{},{schema_version:2,runs:evidence.runs},{schema_version:1,runs:null},{schema_version:1,runs:[null,{}, {connections:[null]}]}]){
    const api=load(proof);assert.equal(api.resolveLink(link(api,"external-create"),[]).executed,undefined);
  }
  const proof=copy(evidence);proof.runs.push(copy(proof.runs[0]));const api=load(proof);assert.equal(api.executionFor(link(api,"external-create")),null);
});

function databaseAssets(status="HEALTHY"){
  const at=new Date().toISOString();return [{asset_id:"internal-backend",kind:"backend_healthz",status:"HEALTHY",checked_at:at},
    {asset_id:"internal-backend-database",status,evidence_type:"HEALTHZ_COMPONENT",parent_target_id:"internal-backend",checked_at:at}];
}
test("historical evidence never hides a measured connection failure or replaces a live success",()=>{
  const proof=copy(evidence);proof.runs[0].connections.push({id:"internal-database",from:"internal-backend",to:"internal-backend-database"});const api=load(proof);
  assert.equal(api.resolveLink(link(api,"internal-database"),databaseAssets()).state,"VERIFIED");
  assert.equal(api.resolveLink(link(api,"internal-database"),databaseAssets("DOWN")).state,"FAILED");
});

test("fresh typed Runtime measurements take precedence over a dated gVisor creation record",()=>{
  const api=load(),at=new Date().toISOString(),runtime={asset_id:"runtime",kind:"runtime_status",status:"HEALTHY",reason_code:"RUNTIME_WORKLOAD_READY",checked_at:at};
  const node={asset_id:"linux-pool",kind:"runtime_node",status:"HEALTHY",reason_code:"K3S_NODE_READY",checked_at:at};
  assert.equal(api.resolveLink(link(api,"runtime-k3s"),[runtime,node]).state,"VERIFIED");
  const historical=api.resolveLink(link(api,"runtime-k3s"),[{...runtime,status:"STALE"},node]);assert.equal(historical.state,"EXECUTED");assert.equal(historical.verified,false);
});

test("unrelated provisioning, provider, publishing and HA connections receive no execution credit",()=>{
  const api=load();
  for(const id of ["broker-provider","image-publish","bundle-publish","bundle-poll","image-pull","internal-proxy","external-proxy"]){const resolved=api.resolveLink(link(api,id),[]);assert.equal(resolved.executed,undefined,id);}
  assert.ok(api.catalog.gates.some(g=>g.note.includes("미검증")));
});

test("delivered execution module matches the public JSON and loads before the model",()=>{
  const context={};vm.runInNewContext(fs.readFileSync(path.join(folder,"architecture-evidence.js"),"utf8"),context);
  assert.deepEqual(JSON.parse(JSON.stringify(context.SentinelExecutionEvidence)),evidence);
  const html=fs.readFileSync(path.join(folder,"index.html"),"utf8");assert.ok(html.indexOf('/architecture-evidence.js')<html.indexOf('/architecture-model.js'));
  for(const id of ["executed-links","verified-links"])assert.ok(html.includes(`id="${id}"`));
});

test("internal proof digest binds the published UTF-8 LF artifact on Windows and Linux",()=>{
  for(const record of evidence.runs.filter(r=>r.track==="INTERNAL")){
    const canonical=fs.readFileSync(path.join(__dirname,"..",record.proof_path),"utf8").replace(/\r\n/g,"\n");
    assert.equal(record.proof_encoding,"utf-8-lf");
    assert.equal(require("node:crypto").createHash("sha256").update(canonical,"utf8").digest("hex"),record.proof_sha256);
  }
});
