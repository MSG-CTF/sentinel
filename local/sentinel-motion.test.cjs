const test = require('node:test');
const assert = require('node:assert/strict');
const {pathToFileURL} = require('node:url');
const path = require('node:path');

const scene = import(pathToFileURL(path.resolve(__dirname, '../src/Sentinel.Api/wwwroot/sentinel-scene.js')));

test('cold exhaust responds in both directions and dissipates without idle emission', async () => {
  const {advanceCoolant,coolantPressure}=await scene;
  assert.equal(coolantPressure(0),0);
  assert.equal(coolantPressure(.015),0);
  assert.equal(coolantPressure(.3),coolantPressure(-.3));
  assert.equal(coolantPressure(100),1);
  let puff={x:1.9,y:0,z:0,vx:1,vy:-.2,vz:.2,age:0,radius:.16};
  assert.deepEqual(advanceCoolant(puff,0),puff,'paused simulation drifted');
  for(let i=0;i<240;i++)puff=advanceCoolant(puff,1/60);
  assert.ok(puff.x>1.9&&puff.vx<.02,'exhaust must retain then lose momentum');
  assert.equal(puff.y,-1.98,'cold exhaust must settle on the mounting deck');
  assert.ok(puff.age>3.9&&puff.radius<=.74);
  assert.ok(Object.values(puff).every(Number.isFinite));
  assert.deepEqual(advanceCoolant(puff,100),advanceCoolant(puff,.08),'resuming must not teleport vapor');
});

test('camera rail loops without a shot cut, reversal jerk or unsafe dolly', async () => {
  const {cameraRail}=await scene,h=.0001;
  for(const time of [0,11,22,33,44,88]) {
    const before=cameraRail(time-h),at=cameraRail(time),after=cameraRail(time+h);
    for(const key of Object.keys(at)) {
      assert.ok(Math.abs(after[key]-before[key])<.00002,`${time}/${key} position discontinuity`);
      assert.ok(Math.abs((after[key]-2*at[key]+before[key])/(h*h))<.02,`${time}/${key} acceleration`);
    }
  }
  for(let i=0;i<=440;i++){
    const pose=cameraRail(i/10);
    assert.ok(Math.abs(pose.yaw)<=.43&&pose.pitch>=0&&pose.pitch<=.12);
    assert.ok(pose.zoom>=.90&&pose.zoom<=1);
  }
});

test('manual camera limits reject oversized gestures and preserve a valid pose', async () => {
  const {limitCamera}=await scene;
  for(const multiplier of [-1,1]){
    const pose=limitCamera({yaw:multiplier*100,pitch:multiplier*100,zoom:multiplier*100});
    assert.ok(pose.yaw>=-1.10&&pose.yaw<=.88);
    assert.ok(pose.pitch>=-.18&&pose.pitch<=.43);
    assert.ok(pose.zoom>=.84&&pose.zoom<=1.16);
    assert.deepEqual(limitCamera(pose),pose,'clamping repeatedly introduces drift');
  }
});

test('ignition reveals the rim before the optic and settles without a repeated flash', async () => {
  const {ignitionPose}=await scene;
  assert.ok(Object.values(ignitionPose(0)).every(value=>value===0));
  assert.ok(ignitionPose(.6).rim>0);
  assert.equal(ignitionPose(.6).optic,0);
  assert.ok(ignitionPose(1.8).optic>ignitionPose(1.8).key);
  assert.ok(Object.values(ignitionPose(4)).every(value=>value===1));
  assert.deepEqual(ignitionPose(4),ignitionPose(60));
  for(let i=1;i<=400;i++){
    const previous=ignitionPose((i-1)/100),next=ignitionPose(i/100);
    for(const key of Object.keys(next))assert.ok(next[key]>=previous[key]&&next[key]<=1);
  }
});

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

test('each bridge releases its latch before lifting, in a visible stagger', async () => {
  const {bridgePose}=await scene;
  for(let step=0;step<=1000;step++){
    const bridges=bridgePose(step/1000);
    bridges.forEach((bridge,i)=>{
      if(bridge.lift>0)assert.equal(bridge.unlock,1,`bridge ${i} lifts while locked`);
      if(i>0)assert.ok(bridge.lift<=bridges[i-1].lift,`bridge ${i} moves out of order`);
    });
  }
  const midway=bridgePose(.42);
  assert.ok(midway[0].lift-midway[3].lift>.5,'all bridges appear to move as a single rigid part');
});

test('the optical lid stays clear until all six cartridges have reseated', async () => {
  const {interiorPose}=await scene;
  for(let step=1000;step>=0;step--){
    const pose=interiorPose(step/1000);
    if(pose.hood>0)assert.equal(pose.latch,1,'lid moves through a locked catch');
    if(pose.cells.some(cell=>cell.slide>0||cell.lift>0))assert.equal(pose.hood,1,'lid crosses a raised cartridge');
  }
});

test('six cartridges unseat along rails then lift in order instead of moving as a disk', async () => {
  const {interiorPose}=await scene;
  for(let step=0;step<=1000;step++){
    const {cells}=interiorPose(step/1000);
    cells.forEach((cell,i)=>{
      if(cell.lift>0)assert.ok(cell.slide>.7,`cartridge ${i} rises before its rail clears`);
      if(i>0){assert.ok(cell.slide<=cells[i-1].slide);assert.ok(cell.lift<=cells[i-1].lift);}
    });
  }
  const moving=interiorPose(.80).cells;
  assert.ok(moving[0].lift>.9);
  assert.equal(moving[5].lift,0);
});

test('internal and bridge paths remain bounded and seat exactly when reversed', async () => {
  const {interiorPose,bridgePose}=await scene;
  const values=p=>{
    const {cells,...rest}=interiorPose(p);
    return [...Object.values(rest),...cells.flatMap(Object.values),...bridgePose(p).flatMap(Object.values)];
  };
  assert.ok(values(-1).every(value=>value===0));
  assert.ok(values(2).every(value=>value===1));
  let previous=values(1);
  for(let step=999;step>=0;step--){
    const next=values(step/1000);
    next.forEach((value,i)=>assert.ok(value>=0&&value<=previous[i]));previous=next;
  }
  assert.ok(previous.every(value=>value===0));
});

test('new articulated parts have smooth starts and stops at every cam boundary', async () => {
  const {interiorPose,bridgePose}=await scene,h=1e-6;
  const values=p=>{const {cells,...rest}=interiorPose(p);return [...Object.values(rest),...cells.flatMap(Object.values),...bridgePose(p).flatMap(Object.values)];};
  const boundaries=[.34,.44,.45,.63,.73,.91,.81,1];
  for(let i=0;i<6;i++)boundaries.push(.64+i*.026,.735+i*.026,.70+i*.026,.82+i*.026);
  for(let i=0;i<4;i++)boundaries.push(i*.047,.13+i*.047,.15+i*.047,.55+i*.047);
  for(const p of boundaries){
    const before=values(p-h),at=values(p),after=values(p+h);
    after.forEach((value,i)=>{
      const velocityBefore=(at[i]-before[i])/h,velocityAfter=(value-at[i])/h;
      assert.ok(Math.abs(velocityAfter-velocityBefore)<.006,`${p}/${i} velocity discontinuity`);
    });
  }
});
