"use strict";
const test=require('node:test'),assert=require('node:assert/strict');
require('../src/Sentinel.Api/wwwroot/asset-model.js');require('../src/Sentinel.Api/wwwroot/architecture-model.js');require('../src/Sentinel.Api/wwwroot/topology.js');
const {resolveNodes,monitoringLinks,resolveLink,catalog}=SentinelArchitecture;
const at=new Date().toISOString();
function target(id,role,track,kind='service_heartbeat',extra={}) {return {target_id:id,name:id,role,track,kind,status:'HEALTHY',checked_at:at,components:{},risk:{level:'NORMAL',signals:[]},...extra};}
function fleet() {return [
 ...Array.from({length:7},(_,i)=>target('koth-host-'+i,'KOTH','KOTH')),
 ...Array.from({length:6},(_,i)=>target('koth-app-'+i,'KOTH','KOTH')),
 ...Array.from({length:7},(_,i)=>target('fleet-windows-'+i,'WINDOWS','RUNTIME')),
 ...Array.from({length:4},(_,i)=>target('fleet-node-'+i,'K3S','RUNTIME','cluster_heartbeat')),
 target('runtime-cluster','K3S','RUNTIME','cluster_heartbeat')];}
const assets=rows=>SentinelAssets.buildAssets({targets:rows,updated_at:at,poll_interval_seconds:15});
const node=(rows,id)=>resolveNodes(assets(rows)).find(n=>n.id===id);

test('the production roster populates the three fixed diagram roles without inflating health counts',()=>{
 const rows=fleet(),readings=assets(rows),before=SentinelAssets.summarize(readings),mapped=resolveNodes(readings);
 for(const [id,count] of [['koth',13],['windows',7],['linux-pool',5]]) {
  const group=mapped.find(n=>n.id===id);assert.equal(group.members.length,count);assert.equal(group.counts.NORMAL,count);
  assert.equal(group.reading.risk.level,'NORMAL');assert.equal(group.reading.evidence_type,'GROUP_SUMMARY');
  for(const member of group.members)assert.ok(mapped.some(n=>n.id===member.asset_id));
 }
 assert.equal(mapped.find(n=>n.id==='koth').summary,'서버 7 · 준비 6');
 assert.deepEqual(SentinelAssets.summarize(readings),before);assert.equal(before.total,25);
});
test('resource registration additions, disabling and deletion change diagram membership immediately',()=>{
 const rows=fleet(),extra=target('fleet-windows-new','WINDOWS','RUNTIME');
 assert.equal(node([...rows,extra],'windows').members.length,8);
 assert.equal(node(rows.filter(r=>r.target_id!=='fleet-windows-0'),'windows').members.length,6);
 assert.equal(node([...rows,extra],'windows').members.filter(a=>a.asset_id===extra.target_id).length,1);
 const empty=node(rows.filter(r=>r.role!=='WINDOWS'),'windows');assert.equal(empty.members.length,0);assert.equal(empty.reading,null);
 assert.equal(monitoringLinks(assets([])).find(l=>l.from==='windows').state,'PENDING');
});
test('one member failure, warning or stale report changes the group and its collection path',()=>{
 for(const [status,level,expected] of [['DOWN','CRITICAL','FAILED'],['HEALTHY','WARNING','UNKNOWN'],['STALE','NORMAL','UNKNOWN'],['PENDING','NORMAL','PENDING']]) {
  const rows=[target('koth-host-ok','KOTH','KOTH'),target('koth-host-bad','KOTH','KOTH','service_heartbeat',{status,risk:{level,signals:[]}})];
  const group=node(rows,'koth'),link=monitoringLinks(assets(rows)).find(l=>l.from==='koth');
  assert.notEqual(group.reading.risk.level,'NORMAL');assert.equal(link.state,expected);assert.equal(link.verified,false);
 }
});
test('offline or frozen source measurements cannot leave green diagram groups or collection arrows',()=>{
 for(const readings of [SentinelAssets.buildAssets({targets:fleet(),updated_at:at},{offline:true}),
  SentinelAssets.buildAssets({targets:fleet(),updated_at:new Date(Date.now()-300000).toISOString()})]) {
  for(const id of ['koth','windows','linux-pool'])assert.equal(resolveNodes(readings).find(n=>n.id===id).reading.risk.level,'UNKNOWN');
  assert.ok(monitoringLinks(readings).every(l=>!l.verified));
 }
});
test('track and probe kind boundaries prevent unrelated assets from filling dedicated pools',()=>{
 const rows=[target('other','KOTH','PUBLIC'),target('other-windows','WINDOWS','EXTERNAL'),target('bad-node','K3S','RUNTIME','http')];
 for(const id of ['koth','windows','linux-pool'])assert.equal(node(rows,id).members.length,0);
});
test('collection evidence never upgrades request, provisioning or scoring contracts',()=>{
 const readings=assets(fleet());assert.ok(monitoringLinks(readings).every(l=>l.verified));
 for(const link of catalog.links.filter(l=>l.stage!=='PROBE')) {assert.equal(resolveLink(link,readings).verified,false);assert.equal(resolveLink(link,readings).state,link.stage);}
 assert.equal(resolveLink(catalog.links.find(l=>l.id==='runtime-k3s'),readings).verified,false);
});
test('control plane diagram roles combine host, API and discovery readings but retain actual member identities',()=>{
 const rows=[target('runtime','RUNTIME','RUNTIME'),target('runtime-api','RUNTIME','RUNTIME','runtime_healthz'),
  target('scheduler','SCHEDULER','CONTROL'),target('scheduler-api','SCHEDULER','CONTROL','scheduler_inventory'),
  target('broker','BROKER','CONTROL','json_fields'),target('broker-console','WEB_UI','CONTROL','http'),target('broker-fleet','BROKER','CONTROL','broker_fleet')];
 for(const [id,count] of [['runtime',2],['scheduler',2],['broker',3]])assert.equal(node(rows,id).members.length,count);
 assert.equal(rows.length,7);
});
test('collection arrows use their own routes and touch the observed role and observer boundaries',()=>{
 const {positions,monitorRoutes}=SentinelTopology;
 const boundary=([px,py],[x,y,w,h])=>(px===x||px===x+w)&&py>=y&&py<=y+h || (py===y||py===y+h)&&px>=x&&px<=x+w;
 for(const link of monitoringLinks(assets(fleet()))) {
  const points=monitorRoutes[link.id].p;assert.ok(boundary(points[0],positions[link.from]));assert.ok(boundary(points.at(-1),positions.observer));
  for(let i=1;i<points.length;i++)assert.ok(points[i-1][0]===points[i][0]||points[i-1][1]===points[i][1]);
 }
});
