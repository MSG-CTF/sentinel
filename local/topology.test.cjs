"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
require("../src/Sentinel.Api/wwwroot/architecture-model.js");
require("../src/Sentinel.Api/wwwroot/topology.js");
const {catalog} = globalThis.SentinelArchitecture;
const {positions,routes,width,height} = globalThis.SentinelTopology;

function onBoundary(point, rect) {
  const [x,y,w,h]=rect, [px,py]=point;
  return (px===x || px===x+w) && py>=y && py<=y+h || (py===y || py===y+h) && px>=x && px<=x+w;
}
function crosses(a,b,rect) {
  const [x,y,w,h]=rect;
  return a[0]===b[0] && a[0]>x+.5 && a[0]<x+w-.5 && Math.max(Math.min(a[1],b[1]),y+.5)<Math.min(Math.max(a[1],b[1]),y+h-.5)
    || a[1]===b[1] && a[1]>y+.5 && a[1]<y+h-.5 && Math.max(Math.min(a[0],b[0]),x+.5)<Math.min(Math.max(a[0],b[0]),x+w-.5);
}

test("all 25 catalog roles and all 29 contracts have a location and a route",()=>{
  assert.equal(catalog.nodes.length,25); assert.equal(catalog.links.length,29);
  for (const definition of catalog.nodes) assert.ok(positions[definition.id],definition.id);
  assert.deepEqual(new Set(Object.keys(routes)),new Set(catalog.links.map(link=>link.id)));
});
test("every arrow starts at its provider and reaches its actual consumer",()=>{
  for (const link of catalog.links) {
    const route=routes[link.id];
    assert.ok(onBoundary(route.p[0],positions[link.from]),`${link.id} start`);
    assert.ok(onBoundary(route.p.at(-1),positions[link.to]),`${link.id} end`);
  }
});
test("orthogonal routes do not cross any unrelated asset box",()=>{
  for (const link of catalog.links) {
    const points=routes[link.id].p;
    for (let i=1;i<points.length;i++) {
      assert.ok(points[i-1][0]===points[i][0] || points[i-1][1]===points[i][1],`${link.id} orthogonal`);
      for (const [id,rect] of Object.entries(positions)) if (![link.from,link.to].includes(id)) assert.ok(!crosses(points[i-1],points[i],rect),`${link.id} crosses ${id}`);
    }
  }
});
test("all nodes fit inside a single pannable canvas and never overlap",()=>{
  const entries=Object.entries(positions);
  for (const [id,[x,y,w,h]] of entries) {
    assert.ok(x>=0 && y>=0 && x+w<=width && y+h<=height,id);
    for (const [other,[ox,oy,ow,oh]] of entries) if (id!==other) assert.ok(!(x<ox+ow && x+w>ox && y<oy+oh && y+h>oy),`${id} overlaps ${other}`);
  }
});
test("only inventory and track-specific token verification are optional lines",()=>{
  assert.deepEqual(Object.entries(routes).filter(([,route])=>route.secondary).map(([id])=>id).sort(),["external-inventory","external-token","internal-inventory","internal-token"]);
});
test("Runtime creation originates at Scheduler, not Broker or a private VM assumption",()=>{
  const creation=catalog.links.find(link=>link.id==="runtime-create");
  assert.equal(creation.from,"scheduler"); assert.equal(creation.to,"runtime");
  assert.ok(!catalog.links.some(link=>link.from==="broker" && link.to==="runtime"));
  assert.ok(!catalog.links.some(link=>link.from==="runtime" && ["koth","windows"].includes(link.to)));
});
