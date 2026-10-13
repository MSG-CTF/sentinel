const test = require('node:test');
const assert = require('node:assert/strict');
const {pathToFileURL} = require('node:url');
const path = require('node:path');

const scene = import(pathToFileURL(path.resolve(__dirname, '../src/Sentinel.Api/wwwroot/sentinel-scene.js')));

test('mechanical travel seats every layer and reaches the complete open pose', async () => {
  const {mechanicalPose} = await scene;
  assert.ok(Object.values(mechanicalPose(0)).every(value => value === 0));
  assert.ok(Object.values(mechanicalPose(1)).every(value => value === 1));
  for(let i=1;i<=100;i++) {
    const previous=mechanicalPose((i-1)/100), next=mechanicalPose(i/100);
    for(const key of Object.keys(next)) assert.ok(next[key]>=previous[key] && next[key]<=1, key);
  }
});

test('locks clear before the carriage rises, and the carrier opens before the optic', async () => {
  const {mechanicalPose} = await scene;
  const released=mechanicalPose(.24), lifted=mechanicalPose(.57);
  assert.equal(released.unlock,1);
  assert.ok(released.cover>0);
  assert.equal(released.lift,0);
  assert.equal(released.carrier,0);
  assert.ok(lifted.carrier>0);
  assert.equal(lifted.optic,0);
  // The same cam path in reverse seats the optic before the locks engage.
  assert.equal(mechanicalPose(.16).optic,0);
  assert.equal(mechanicalPose(.16).carrier,0);
});

test('automatic loop has no pose or velocity jump at dwell boundaries', async () => {
  const {mechanicalPose,cycleProgress} = await scene;
  const h=.001;
  for(const time of [0,2,9,13,22,26,52]) {
    const before=mechanicalPose(cycleProgress(time-h));
    const at=mechanicalPose(cycleProgress(time));
    const after=mechanicalPose(cycleProgress(time+h));
    for(const key of Object.keys(at)) {
      assert.ok(Math.abs(after[key]-before[key])<.00001, `${time}/${key} position`);
      assert.ok(Math.abs(after[key]-2*at[key]+before[key])/(h*h)<.1, `${time}/${key} acceleration`);
    }
  }
});

test('reversing travel retains velocity instead of restarting the motion', async () => {
  const {advanceTravel} = await scene;
  let state={position:0,velocity:0};
  for(let i=0;i<18;i++) state=advanceTravel(state,1,1/60);
  const reversed=advanceTravel(state,0,.00001);
  assert.ok(Math.abs(reversed.velocity-state.velocity)<.002);
  assert.ok(reversed.position>state.position);
  for(let i=0;i<180;i++) {
    state=advanceTravel(state,0,1/60);
    assert.ok(state.position>=0 && state.position<=1 && Number.isFinite(state.velocity));
  }
  assert.ok(state.position<.00001);
});

test('travel has the same timing at 30 and 60 frames per second', async () => {
  const {advanceTravel} = await scene;
  const simulate=hz=>{
    let state={position:0,velocity:0};
    for(let i=0;i<hz*2;i++) state=advanceTravel(state,1,1/hz);
    return state;
  };
  const a=simulate(30),b=simulate(60);
  assert.ok(Math.abs(a.position-b.position)<1e-12);
  assert.ok(Math.abs(a.velocity-b.velocity)<1e-12);
  assert.ok(1-a.position<.00002);
});

test('pause and long browser stalls cannot create invalid travel', async () => {
  const {advanceTravel} = await scene;
  const state={position:.4,velocity:.12};
  assert.deepEqual(advanceTravel(state,1,0),state);
  for(const target of [0,1,0,1]) {
    const next=advanceTravel(state,target,60);
    assert.ok(next.position>=0 && next.position<=1 && Number.isFinite(next.velocity));
  }
});
