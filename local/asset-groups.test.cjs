const test=require('node:test');
const assert=require('node:assert/strict');
require('../src/Sentinel.Api/wwwroot/asset-groups.js');
const {group}=globalThis.SentinelAssetGroups;
const asset=(id,track,level)=>({asset_id:id,name:id,track,risk:{level}});

test('many physical nodes and probes produce one summary for each service group',()=>{
 const assets=[...Array.from({length:13},(_,i)=>asset('koth-'+i,'KOTH','NORMAL')),asset('node','RUNTIME','NORMAL'),asset('windows','RUNTIME','NORMAL')];
 const result=group(assets);
 assert.equal(result.length,2);assert.equal(result.find(g=>g.id==='KOTH').total,13);
 assert.equal(result.find(g=>g.id==='RUNTIME').total,2);
 assert.equal(result.reduce((n,g)=>n+g.total,0),15);
});
test('a critical or unknown member prevents a healthy group summary',()=>{
 for(const level of ['CRITICAL','WARNING','UNKNOWN','PENDING']) {
  const [g]=group([asset('healthy','RUNTIME','NORMAL'),asset('issue','RUNTIME',level)]);
  assert.equal(g.level,level);assert.equal(g.attention[0].asset_id,'issue');
 }
});
test('search and risk filtering keep complete group health instead of hiding an outage',()=>{
 const [g]=group([asset('found','KOTH','NORMAL'),asset('hidden','KOTH','CRITICAL')],a=>a.asset_id==='found');
 assert.equal(g.visible.length,1);assert.equal(g.total,2);assert.equal(g.level,'CRITICAL');assert.equal(g.counts.CRITICAL,1);
});
test('unknown risk values fail closed and issue groups come first',()=>{
 const result=group([asset('healthy','EXTERNAL','NORMAL'),asset('unknown','RUNTIME','NEW_LEVEL')]);
 assert.equal(result[0].id,'RUNTIME');assert.equal(result[0].level,'UNKNOWN');
});
test('grouping does not mutate membership or replace individual evidence',()=>{
 const a=asset('node','RUNTIME','NORMAL'),b=asset('application','RUNTIME','NORMAL');
 const input=[a,b],copy=JSON.stringify(input);const [g]=group(input);
 assert.equal(g.members[0],a);assert.equal(g.members[1],b);assert.equal(JSON.stringify(input),copy);
 assert.notEqual(g.members,input);
});
