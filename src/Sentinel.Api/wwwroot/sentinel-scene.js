import * as THREE from "./vendor/three/three.module.js";

// A procedural product model, not asset telemetry. Dimensions are scene units.
const TAU = Math.PI * 2;
const clamp = THREE.MathUtils.clamp;
const ease = value => { const x = clamp(value, 0, 1); return x * x * x * (x * (x * 6 - 15) + 10); };

function sequence(time) {
  const t = time % 22;
  return t < 2 ? 0 : t < 7 ? ease((t - 2) / 5) : t < 12 ? 1 : t < 18 ? 1 - ease((t - 12) / 6) : 0;
}

function surfaceTexture() {
  const size = 256, data = new Uint8Array(size * size * 4);
  let seed = 713;
  const random = () => { seed = (1664525 * seed + 1013904223) >>> 0; return seed / 4294967296; };
  for (let y = 0; y < size; y++) {
    const line = 174 + random() * 53;
    for (let x = 0; x < size; x++) {
      const offset = (y * size + x) * 4;
      const value = Math.round(line + random() * 13);
      data[offset] = data[offset + 1] = data[offset + 2] = value;
      data[offset + 3] = 255;
    }
  }
  const texture = new THREE.DataTexture(data, size, size);
  texture.wrapS = texture.wrapT = THREE.RepeatWrapping;
  texture.repeat.set(2, 5);
  texture.magFilter = THREE.LinearFilter;
  texture.minFilter = THREE.LinearMipmapLinearFilter;
  texture.generateMipmaps = true;
  texture.needsUpdate = true;
  return texture;
}

function ringGeometry(inner, outer, height, bevel = .025) {
  const h = height / 2, b = Math.min(bevel, height / 3, (outer - inner) / 3);
  const profile = [[inner,-h+b],[inner+b,-h],[outer-b,-h],[outer,-h+b],[outer,h-b],[outer-b,h],[inner+b,h],[inner,h-b],[inner,-h+b]];
  return new THREE.LatheGeometry(profile.map(([x,y]) => new THREE.Vector2(x,y)), 128);
}

function arcGeometry(inner, outer, start, end, height, bevel = .025) {
  const shape = new THREE.Shape();
  shape.moveTo(Math.cos(start) * outer, Math.sin(start) * outer);
  shape.absarc(0, 0, outer, start, end, false);
  shape.lineTo(Math.cos(end) * inner, Math.sin(end) * inner);
  shape.absarc(0, 0, inner, end, start, true);
  shape.closePath();
  const geometry = new THREE.ExtrudeGeometry(shape, {depth:height, bevelEnabled:true, bevelThickness:bevel, bevelSize:bevel, bevelSegments:3, steps:1, curveSegments:32});
  geometry.rotateX(-Math.PI / 2);
  geometry.translate(0, -height / 2, 0);
  return geometry;
}

function gearGeometry(radius, teeth, depth) {
  const shape = new THREE.Shape();
  for (let i = 0; i < teeth * 4; i++) {
    const angle = i / (teeth * 4) * TAU;
    const r = radius * (i % 4 === 0 || i % 4 === 3 ? .86 : 1);
    const x = Math.cos(angle) * r, y = Math.sin(angle) * r;
    i ? shape.lineTo(x, y) : shape.moveTo(x, y);
  }
  shape.closePath();
  const hole = new THREE.Path();
  hole.absarc(0, 0, radius * .33, 0, TAU, true);
  shape.holes.push(hole);
  const geometry = new THREE.ExtrudeGeometry(shape, {depth, bevelEnabled:true, bevelThickness:.009, bevelSize:.009, bevelSegments:2, curveSegments:20});
  geometry.rotateX(-Math.PI / 2);
  geometry.translate(0, -depth / 2, 0);
  return geometry;
}

function studioEnvironment(renderer) {
  const studio = new THREE.Scene();
  studio.background = new THREE.Color(.18, .20, .23);
  const box = new THREE.Mesh(new THREE.BoxGeometry(32, 24, 32), new THREE.MeshBasicMaterial({color:0x596572, side:THREE.BackSide}));
  studio.add(box);
  function softbox(position, width, height, color, intensity) {
    const panel = new THREE.Mesh(new THREE.PlaneGeometry(width, height), new THREE.MeshBasicMaterial({color:new THREE.Color(color).multiplyScalar(intensity), side:THREE.DoubleSide}));
    panel.position.set(...position);
    panel.lookAt(0, 0, 0);
    studio.add(panel);
  }
  softbox([-5, 7, 5], 5, 8, 0xf0f5ff, 7);
  softbox([6, 3, -4], 3, 9, 0x92bed6, 4);
  softbox([1, 8, 0], 8, 1.2, 0xffffff, 5);
  softbox([-6, -1, -3], 2, 6, 0xc8a57f, 1.6);
  softbox([0, 1, 8], 7, 1, 0xffffff, 2);
  const generator = new THREE.PMREMGenerator(renderer);
  const target = generator.fromScene(studio, .04);
  studio.traverse(object => { object.geometry?.dispose(); object.material?.dispose(); });
  generator.dispose();
  return target;
}

function makeModel() {
  const root = new THREE.Group();
  const grain = surfaceTexture();
  const materials = {
    titanium:new THREE.MeshPhysicalMaterial({color:0xb6bec7, metalness:.96, roughness:.40, roughnessMap:grain, bumpMap:grain, bumpScale:.006, anisotropy:.5, envMapIntensity:1.1}),
    edge:new THREE.MeshStandardMaterial({color:0xd4dce4, metalness:1, roughness:.20, envMapIntensity:1.15}),
    dark:new THREE.MeshStandardMaterial({color:0x202933, metalness:.7, roughness:.37}),
    ceramic:new THREE.MeshPhysicalMaterial({color:0x080e15, metalness:.25, roughness:.24, clearcoat:.55, clearcoatRoughness:.13}),
    brass:new THREE.MeshStandardMaterial({color:0x9e8259, metalness:1, roughness:.27, roughnessMap:grain}),
    socket:new THREE.MeshStandardMaterial({color:0x06090d, metalness:.3, roughness:.48}),
    light:new THREE.MeshStandardMaterial({color:0xb6f4ff, metalness:.15, roughness:.19, emissive:0x5ad9ff, emissiveIntensity:2.5}),
    status:new THREE.MeshStandardMaterial({color:0xa6d6de, metalness:.2, roughness:.3, emissive:0x63bbc9, emissiveIntensity:1.3}),
    lens:new THREE.MeshPhysicalMaterial({color:0x79b7d1, metalness:.12, roughness:.06, clearcoat:1, transmission:.72, thickness:.25, ior:1.55}),
  };
  const resources = new Set();
  const keep = geometry => {resources.add(geometry); return geometry;};
  function mesh(parent, geometry, material, x=0, y=0, z=0, shadow=true) {
    keep(geometry);
    const object = new THREE.Mesh(geometry, material);
    object.position.set(x, y, z);
    object.castShadow = shadow;
    object.receiveShadow = true;
    parent.add(object);
    return object;
  }
  function ring(parent, inner, outer, height, y, material=materials.titanium) {
    return mesh(parent, ringGeometry(inner, outer, height), material, 0, y);
  }
  function torus(parent, radius, tube, y, material=materials.edge) {
    const object = mesh(parent, new THREE.TorusGeometry(radius, tube, 8, 128), material, 0, y);
    object.rotation.x = Math.PI / 2;
    return object;
  }
  function array(parent, geometry, material, count, radius, y, angleOffset=0, orient=true) {
    keep(geometry);
    const object = new THREE.InstancedMesh(geometry, material, count);
    const transform = new THREE.Object3D();
    for (let i=0; i<count; i++) {
      const angle = angleOffset + i / count * TAU;
      transform.position.set(Math.cos(angle) * radius, y, Math.sin(angle) * radius);
      transform.rotation.y = orient ? -angle : 0;
      transform.updateMatrix();
      object.setMatrixAt(i, transform.matrix);
    }
    object.instanceMatrix.needsUpdate = true;
    object.castShadow = object.receiveShadow = true;
    parent.add(object);
    return object;
  }
  function fasteners(parent, count, radius, y, size=.055) {
    array(parent, new THREE.CylinderGeometry(size*1.45,size*1.45,.018,24), materials.dark, count, radius, y);
    array(parent, new THREE.CylinderGeometry(size,size,.035,24), materials.edge, count, radius, y+.019);
    array(parent, new THREE.CylinderGeometry(size*.48,size*.48,.003,6), materials.socket, count, radius, y+.038);
  }
  const chassis = new THREE.Group(); root.add(chassis);
  ring(chassis, .79, 2.21, .25, -.10);
  ring(chassis, 1.94, 2.23, .08, .072, materials.edge);
  ring(chassis, .8, 2.10, .055, .137, materials.dark);
  ring(chassis, 1.9, 2.18, .06, -.285, materials.dark);
  ring(chassis, .86, 2.20, .10, -.365);
  torus(chassis, 2.11, .018, -.224);
  for (const y of [-.19,-.155,-.12]) torus(chassis, 2.216, .004, y, materials.edge);
  torus(chassis, 1.85, .015, .174, materials.light);
  array(chassis, new THREE.BoxGeometry(.06,.19,.023), materials.dark, 96, 2.19, -.09);
  array(chassis, new THREE.BoxGeometry(.032,.02,.105), materials.edge, 72, 2.055, .18);
  fasteners(chassis, 12, 1.94, .18);
  ring(chassis, .59, .80, .25, .17, materials.brass);
  ring(chassis, .58, .67, .1, .35, materials.dark);
  array(chassis, new THREE.CylinderGeometry(.032,.032,1.15,12), materials.edge, 6, 1.73, .62);
  array(chassis, new THREE.CylinderGeometry(.077,.077,.33,20), materials.dark, 6, 1.73, .22);

  const movement = new THREE.Group(); root.add(movement);
  ring(movement, .69, 1.95, .085, 0, materials.dark);
  ring(movement, 1.81, 1.97, .035, .075, materials.brass);
  ring(movement, .57, .79, .12, .06);
  fasteners(movement, 8, 1.78, .07, .037);
  const gears = [];
  const largeGear = keep(gearGeometry(.36, 22, .075));
  const smallGear = keep(gearGeometry(.21, 16, .08));
  const bearing = keep(ringGeometry(.056,.115,.07,.01));
  const jewel = keep(new THREE.CylinderGeometry(.051,.051,.03,16));
  for (let i=0; i<8; i++) {
    const angle = i / 8 * TAU;
    const group = new THREE.Group();
    group.position.set(Math.cos(angle)*1.29,.15,Math.sin(angle)*1.29);
    movement.add(group);
    const wheel = mesh(group, largeGear, i%2 ? materials.titanium : materials.brass);
    gears.push({wheel,speed:i%2 ? .33 : -.33});
    mesh(group,bearing,materials.edge,0,.06);
    mesh(group,jewel,materials.ceramic,0,.10);
    const a = angle + Math.PI / 8;
    const small = mesh(movement,smallGear,materials.edge,Math.cos(a)*1.41,.17,Math.sin(a)*1.41);
    gears.push({wheel:small,speed:i%2 ? -.48 : .48});
  }

  const rotor = new THREE.Group(); root.add(rotor);
  ring(rotor, 1.63, 1.91, .08, 0);
  ring(rotor, .67, .9, .12, .02, materials.edge);
  torus(rotor, 1.83, .022, .065, materials.brass);
  array(rotor, new THREE.BoxGeometry(.78,.055,.10), materials.titanium, 6, 1.27, .028);
  array(rotor, new THREE.BoxGeometry(.57,.012,.035), materials.dark, 6, 1.3, .063);
  fasteners(rotor, 6, 1.77, .075, .046);
  fasteners(rotor, 6, .81, .102, .038);
  array(rotor, new THREE.BoxGeometry(.04,.035,.018), materials.brass, 84, 1.93, -.015);

  // Articulated rods keep the separated layers visually connected. Each rod
  // follows two moving anchor points instead of stretching along a fixed axis.
  const actuators = [];
  const rodGeometry = keep(new THREE.CylinderGeometry(.027,.027,1,12));
  const sleeveGeometry = keep(new THREE.CylinderGeometry(.060,.060,1,20));
  const jointGeometry = keep(new THREE.SphereGeometry(.072,16,12));
  for (let i=0; i<6; i++) {
    actuators.push({angle:i/6*TAU,rod:mesh(root,rodGeometry,materials.edge),sleeve:mesh(root,sleeveGeometry,materials.dark),a:mesh(root,jointGeometry,materials.brass),b:mesh(root,jointGeometry,materials.brass)});
  }
  const up = new THREE.Vector3(0,1,0), direction = new THREE.Vector3();

  const crown = new THREE.Group(); root.add(crown);
  ring(crown, 1.79, 1.93, .07, -.08, materials.dark);
  torus(crown, 1.87, .019, -.031, materials.status);
  const armor = [];
  const armorGeometry = keep(arcGeometry(1.94,2.32,-.48,.48,.19,.035));
  const inlayGeometry = keep(arcGeometry(2.065,2.20,-.35,.35,.015,.008));
  const fastenerGeometry = keep(new THREE.CylinderGeometry(.054,.054,.028,24));
  const socketGeometry = keep(new THREE.CylinderGeometry(.022,.022,.004,6));
  for (let i=0; i<6; i++) {
    const pivot = new THREE.Group();
    pivot.rotation.y = i / 6 * TAU;
    const part = new THREE.Group(); pivot.add(part); crown.add(pivot);
    mesh(part,armorGeometry,[materials.titanium,materials.edge]);
    mesh(part,inlayGeometry,materials.ceramic,0,.136);
    mesh(part,new THREE.BoxGeometry(.21,.012,.019),materials.edge,2.13,.158,0);
    for (const sign of [-1,1]) {
      const a = sign*.36;
      mesh(part,fastenerGeometry,materials.edge,Math.cos(a)*2.12,.143,Math.sin(a)*2.12);
      mesh(part,socketGeometry,materials.socket,Math.cos(a)*2.12,.159,Math.sin(a)*2.12);
    }
    armor.push({part,index:i});
  }

  const iris = new THREE.Group(); root.add(iris);
  const petals = [];
  const petalGeometry = keep(arcGeometry(.64,1.63,-.315,.315,.09,.024));
  const petalInlay = keep(arcGeometry(.97,1.51,-.23,.23,.012,.008));
  for (let i=0; i<8; i++) {
    const pivot = new THREE.Group(); pivot.rotation.y = i / 8 * TAU;
    const part = new THREE.Group(); pivot.add(part); iris.add(pivot);
    mesh(part,petalGeometry,[materials.titanium,materials.edge]);
    mesh(part,petalInlay,materials.dark,0,.074);
    mesh(part,new THREE.BoxGeometry(.32,.016,.017),materials.brass,1.25,.098);
    const pin = mesh(part,new THREE.CylinderGeometry(.04,.04,.18,20),materials.edge,1.56,-.01,0);
    pin.rotation.x = Math.PI/2;
    mesh(part,fastenerGeometry,materials.edge,.82,.093,0);
    mesh(part,socketGeometry,materials.socket,.82,.109,0);
    petals.push({part,index:i});
  }

  const core = new THREE.Group(); root.add(core);
  ring(core,.37,.6,.11,0,materials.edge);
  ring(core,.40,.53,.12,-.10,materials.dark);
  torus(core,.47,.035,.092,materials.light);
  const lens = mesh(core,new THREE.SphereGeometry(.4,48,32),materials.lens,0,.08);
  lens.scale.y = .56;
  mesh(core,new THREE.SphereGeometry(.29,32,24),materials.light,0,.04);
  const gimbal = new THREE.Group(); core.add(gimbal);
  const outerGimbal = torus(gimbal,.74,.035,0,materials.edge);
  const innerGimbal = torus(gimbal,.64,.023,0,materials.brass);
  innerGimbal.rotation.z = Math.PI/2;
  array(core,new THREE.BoxGeometry(.027,.03,.055),materials.dark,32,.56,.078);
  const light = new THREE.PointLight(0x6ccfee,3,4,2); core.add(light); light.position.y=.2;

  let explosion = .25;
  function update(time, delta, view, pointer, snap=false) {
    const target = view === "exploded" ? 1 : view === "assembled" ? 0 : sequence(time);
    explosion = snap ? target : THREE.MathUtils.damp(explosion,target,3.6,delta);
    const e = explosion;
    root.rotation.set(.035 + pointer.y*.09, -.30 + Math.sin(time*.115)*.18 + pointer.x*.22, -.11 + Math.sin(time*.09)*.025);
    root.position.y = .02 + Math.sin(time*.43)*.035;
    chassis.position.y = -.52 - e*.85;
    chassis.rotation.y = -.025*time;
    movement.position.y = -.12 - e*.34;
    movement.rotation.y = .042*time;
    rotor.position.y = .18 + e*.40;
    rotor.rotation.y = -.055*time + e*.24;
    crown.position.y = .48 + e*1.19;
    crown.rotation.y = .022*time - e*.13;
    iris.position.y = .40 + e*.70;
    iris.rotation.y = -.018*time + e*.26;
    core.position.y = .53 + e*1.03;
    gimbal.rotation.set(e*(.4+time*.14),0,e*(.38+time*.18));
    outerGimbal.visible = innerGimbal.visible = e > .03;
    armor.forEach(({part,index}) => {
      const stagger = ease((e - index*.016) / .90);
      part.position.set(stagger*.38,Math.sin(index*.8)*stagger*.05,0);
      part.rotation.z = -stagger*.13;
    });
    petals.forEach(({part,index}) => {
      const stagger = ease((e - (index%4)*.025) / .90);
      part.position.set(stagger*.54,stagger*.16,0);
      part.rotation.z = -stagger*.35;
    });
    gears.forEach(({wheel,speed}) => wheel.rotation.y = time*speed);
    actuators.forEach(({angle,rod,sleeve,a,b}) => {
      const lowerAngle=angle-chassis.rotation.y, upperAngle=angle-rotor.rotation.y;
      a.position.set(Math.cos(lowerAngle)*1.72,chassis.position.y+.24,Math.sin(lowerAngle)*1.72);
      b.position.set(Math.cos(upperAngle)*1.72,rotor.position.y-.07,Math.sin(upperAngle)*1.72);
      direction.subVectors(b.position,a.position);
      const length=direction.length();
      direction.normalize();
      rod.position.copy(a.position).lerp(b.position,.5);
      rod.quaternion.setFromUnitVectors(up,direction); rod.scale.y=length;
      sleeve.position.copy(a.position).lerp(b.position,.22);
      sleeve.quaternion.copy(rod.quaternion); sleeve.scale.y=length*.42;
    });
    materials.light.emissiveIntensity = 2.2 + e*.45 + Math.sin(time*1.2)*.16;
    light.intensity = 2.8 + e*1.4;
    return e;
  }
  function setState(state) {
    const color = {incident:0xe98576,attention:0xd4b283,offline:0x667b8e,healthy:0x8ecac7,standby:0x74afbd}[state] || 0x74afbd;
    materials.status.color.setHex(color);
    materials.status.emissive.setHex(color);
  }
  function dispose() {
    resources.forEach(geometry => geometry.dispose());
    Object.values(materials).forEach(material => material.dispose());
    root.traverse(object => {if (object.isInstancedMesh) object.dispose();});
    grain.dispose();
  }
  let lowPower=false;
  function setQuality(mobile) {
    if (lowPower===mobile) return;
    lowPower=mobile;
    // Transmission renders the entire scene twice; the mobile lens uses a
    // reflective coating instead, retaining depth without the extra pass.
    materials.lens.transmission=mobile ? 0 : .72;
    materials.lens.metalness=mobile ? .6 : .12;
    materials.lens.color.setHex(mobile ? 0x53859b : 0x79b7d1);
    materials.lens.needsUpdate=true;
  }
  return {root,update,setState,setQuality,dispose};
}

export async function createMechanismScene(element, {still=false} = {}) {
  const canvas = document.createElement("canvas");
  canvas.className = "mechanism-canvas";
  canvas.setAttribute("aria-hidden", "true");
  let renderer, model, environment, resizeObserver;
  let frame=0, running=false, failed=false, destroyed=false, ready=false, elapsed=still ? 8 : 2.6, last=0, lastPaint=0, frames=0;
  let view="auto", width=1, height=1, narrow=false;
  const pointer = {x:0,y:0}, targetPointer = {x:0,y:0};
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(34,1,.1,80);
  const shadowGeometry = new THREE.PlaneGeometry(30,30);
  const shadowMaterial = new THREE.ShadowMaterial({opacity:.065});
  function removeListeners() {
    element.removeEventListener("pointermove", movePointer);
    element.removeEventListener("pointerleave", leavePointer);
    canvas.removeEventListener("webglcontextlost", contextLost);
    resizeObserver?.disconnect();
  }
  function dispose() {
    if (destroyed) return;
    destroyed=true;
    running=false;
    cancelAnimationFrame(frame);
    removeListeners();
    model?.dispose();
    environment?.dispose();
    shadowGeometry.dispose(); shadowMaterial.dispose();
    renderer?.dispose();
    canvas.remove();
  }
  function contextLost(event) {
    event.preventDefault();
    failed=true;
    element.dataset.renderer="fallback";
    document.querySelectorAll(`[data-core-controls="${element.dataset.sentinelCore}"]`).forEach(controls => controls.hidden=true);
    dispose();
  }
  function movePointer(event) {
    if (event.pointerType !== "mouse" || !running) return;
    const box=element.getBoundingClientRect();
    targetPointer.x=clamp((event.clientX-box.left)/box.width-.5,-.5,.5);
    targetPointer.y=clamp((event.clientY-box.top)/box.height-.5,-.5,.5);
  }
  function leavePointer() {targetPointer.x=targetPointer.y=0;}
  function draw(delta=0, snap=false) {
    if (destroyed || failed || !ready) return;
    pointer.x=THREE.MathUtils.damp(pointer.x,targetPointer.x,3,delta);
    pointer.y=THREE.MathUtils.damp(pointer.y,targetPointer.y,3,delta);
    model.update(elapsed,delta,view,pointer,snap);
    renderer.render(scene,camera);
    frames++;
    // DOM diagnostics allow smoke tests to verify that the GPU surface advances.
    if (frames%20===1 || !running) {
      canvas.dataset.frames=String(frames);
      canvas.dataset.drawCalls=String(renderer.info.render.calls);
      canvas.dataset.triangles=String(renderer.info.render.triangles);
    }
  }
  function tick(now) {
    if (!running || destroyed || failed) return;
    frame=requestAnimationFrame(tick);
    const interval=narrow ? 1000/30 : 1000/45;
    if (now-lastPaint < interval-1) return;
    const delta=Math.min((now-last)/1000,.08);
    last=lastPaint=now;
    elapsed+=delta;
    draw(delta);
  }
  function setRunning(value) {
    if (destroyed || failed || running===value) return;
    running=value;
    canvas.dataset.motion=running ? "running" : "paused";
    cancelAnimationFrame(frame);
    if (running) { last=lastPaint=performance.now(); frame=requestAnimationFrame(tick); }
    else {leavePointer(); draw();}
  }
  function resize() {
    if (destroyed || !renderer) return;
    const box=element.getBoundingClientRect();
    if (!box.width || !box.height) return;
    width=box.width; height=box.height; narrow=window.innerWidth<700 || width<420;
    model.setQuality(narrow);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1,narrow ? 1.35 : 1.6));
    renderer.setSize(width,height,false);
    camera.aspect=width/height;
    // Fit the fully extended assembly as well as the closed body.
    const distance=Math.max(11.2,10.7/camera.aspect);
    camera.position.set(distance*.34,distance*.48,distance*.81);
    camera.lookAt(0,.22,0);
    camera.updateProjectionMatrix();
    draw();
  }
  try {
    renderer=new THREE.WebGLRenderer({canvas,antialias:true,alpha:true,powerPreference:"high-performance"});
    renderer.setClearColor(0x000000,0);
    renderer.outputColorSpace=THREE.SRGBColorSpace;
    renderer.toneMapping=THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure=1.05;
    renderer.shadowMap.enabled=true;
    renderer.shadowMap.type=THREE.PCFShadowMap;
    environment=studioEnvironment(renderer);
    scene.environment=environment.texture;
    scene.add(new THREE.HemisphereLight(0xe4f0ff,0x101822,1.1));
    const key=new THREE.DirectionalLight(0xf6f2e9,3.1);
    key.position.set(-4,8,6); key.castShadow=true;
    key.shadow.mapSize.set(1024,1024);
    Object.assign(key.shadow.camera,{left:-4.5,right:4.5,top:4.5,bottom:-4.5,near:1,far:24});
    key.shadow.normalBias=.022; key.shadow.bias=-.00015;
    key.shadow.radius=3;
    scene.add(key);
    const rim=new THREE.DirectionalLight(0x8ac9f4,1.5); rim.position.set(4,3,-5); scene.add(rim);
    const fill=new THREE.DirectionalLight(0xffd3ac,.55); fill.position.set(-4,-1,-2); scene.add(fill);
    model=makeModel(); scene.add(model.root);
    const shadow=new THREE.Mesh(shadowGeometry,shadowMaterial);
    shadow.rotation.x=-Math.PI/2; shadow.position.y=-2.03; shadow.receiveShadow=true; scene.add(shadow);
    element.append(canvas);
    resize();
    model.update(elapsed,0,view,pointer,true);
    await renderer.compileAsync(scene,camera);
    ready=true;
    draw();
    element.dataset.renderer="webgl";
    canvas.dataset.motion="paused";
    canvas.addEventListener("webglcontextlost",contextLost);
    element.addEventListener("pointermove",movePointer);
    element.addEventListener("pointerleave",leavePointer);
    resizeObserver=new ResizeObserver(resize); resizeObserver.observe(element);
    return {setRunning,setState:state=>{model.setState(state); if(!running) draw();},setView:mode=>{
      view=["auto","exploded","assembled"].includes(mode) ? mode : "auto";
      if (!running) draw(0,true);
    },dispose};
  } catch(error) {
    element.dataset.renderer="fallback";
    dispose();
    throw error;
  }
}
