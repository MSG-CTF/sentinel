const test=require('node:test');const assert=require('node:assert/strict');
require('../src/Sentinel.Api/wwwroot/asset-model.js');require('../src/Sentinel.Api/wwwroot/asset-groups.js');
const now=Date.now();
const target=(id,kind='cluster_heartbeat')=>({target_id:id,name:id,role:kind==='service_heartbeat'?'WINDOWS':'K3S',track:'RUNTIME',kind,
 status:'HEALTHY',reason_code:'K3S_NODE_READY',checked_at:new Date(now-1000).toISOString(),enabled:true,components:{},risk:{level:'NORMAL',signals:[]}});

test('dynamic additions and removals change actual asset counts inside the same group',()=>{
 const a=target('fleet-node-a'),b=target('fleet-windows-b','service_heartbeat');
 const assets=targets=>SentinelAssets.buildAssets({targets,updated_at:new Date(now).toISOString(),poll_interval_seconds:15},{now});
 assert.equal(SentinelAssetGroups.group(assets([a,b]))[0].total,2);
 assert.equal(SentinelAssetGroups.group(assets([b]))[0].total,1);
 assert.equal(SentinelAssetGroups.group(assets([])).length,0);
});
test('source roster freshness does not expire each minute or revive stale child measurements',()=>{
 const source={...target('broker-fleet','broker_fleet'),role:'BROKER',track:'CONTROL',checked_at:new Date(now-90000).toISOString(),components:{node_count:'4',windows_count:'7'}};
 const node={...target('fleet-node-a'),checked_at:new Date(now-151000).toISOString()};
 const snapshot={targets:[source,node],updated_at:new Date(now).toISOString(),poll_interval_seconds:15};
 const assets=SentinelAssets.buildAssets(snapshot,{now});assert.equal(assets[0].status,'HEALTHY');assert.equal(assets[1].status,'STALE');
 source.checked_at=new Date(now-181000).toISOString();assert.equal(SentinelAssets.buildAssets(snapshot,{now})[0].status,'STALE');
 const offline=SentinelAssets.buildAssets({...snapshot,targets:[{...source,checked_at:new Date(now).toISOString()}]},{now,offline:true});
 assert.equal(offline[0].risk.level,'UNKNOWN');
});
