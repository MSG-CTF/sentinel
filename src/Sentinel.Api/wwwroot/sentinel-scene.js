import * as THREE from "./vendor/three/three.module.js";
import { HDRLoader } from "./vendor/three/HDRLoader.js";

// A procedural product model, not asset telemetry. Dimensions are scene units.
const TAU = Math.PI * 2;
const clamp = THREE.MathUtils.clamp;
const ease = value => { const x = clamp(value, 0, 1); return x * x * x * (x * (x * 6 - 15) + 10); };

function sequence(time) {
  const t = time % 22;
  return t < 2 ? 0 : t < 7 ? ease((t - 2) / 5) : t < 12 ? 1 : t < 18 ? 1 - ease((t - 12) / 6) : 0;
}

function surfaceTexture() {
  const size = 512, data = new Uint8Array(size * size * 4);
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

function bridgeGeometry() {
  const plate=new THREE.Shape();plate.absarc(0,0,1.90,0,TAU,false);
  const center=new THREE.Path();center.absarc(0,0,.70,0,TAU,true);plate.holes.push(center);
  for(let i=0;i<6;i++){
    const a=i/6*TAU+.12,b=(i+1)/6*TAU-.12;
    const window=new THREE.Path();
    window.moveTo(Math.cos(a)*.99,Math.sin(a)*.99);
    window.absarc(0,0,.99,a,b,false);
    window.lineTo(Math.cos(b)*1.62,Math.sin(b)*1.62);
    window.absarc(0,0,1.62,b,a,true);window.closePath();plate.holes.push(window);
  }
  const geometry=new THREE.ExtrudeGeometry(plate,{depth:.065,bevelEnabled:true,bevelThickness:.022,bevelSize:.022,bevelSegments:3,curveSegments:56});
  geometry.rotateX(-Math.PI/2);geometry.translate(0,-.0325,0);return geometry;
}

function studioEnvironment(renderer) {
  const studio = new THREE.Scene();
  studio.background = new THREE.Color(.006, .009, .014);
  const box = new THREE.Mesh(new THREE.BoxGeometry(32, 24, 32), new THREE.MeshBasicMaterial({color:0x252c33, side:THREE.BackSide}));
  studio.add(box);
  function softbox(position, width, height, color, intensity) {
    const panel = new THREE.Mesh(new THREE.PlaneGeometry(width, height), new THREE.MeshBasicMaterial({color:new THREE.Color(color).multiplyScalar(intensity), side:THREE.DoubleSide}));
    panel.position.set(...position);
    panel.lookAt(0, 0, 0);
    studio.add(panel);
  }
  softbox([-5, 7, 5], 3, 9, 0xffe0aa, 9);
  softbox([6, 3, -4], 1.5, 10, 0x8cb2d0, 8);
  softbox([1, 8, 0], 9, .7, 0xfff4df, 10);
  softbox([-6, -1, -3], 1.5, 7, 0xd99951, 3);
  softbox([0, 1, 8], 6, .6, 0xd9eaff, 4);
  softbox([5, 4, 6], 10, 8, 0xb4c7d5, 1.15);
  const generator = new THREE.PMREMGenerator(renderer);
  const target = generator.fromScene(studio, .04);
  studio.traverse(object => { object.geometry?.dispose(); object.material?.dispose(); });
  generator.dispose();
  return target;
}

async function photographicEnvironment(renderer,element) {
  let texture,generator;
  try {
    const response=await fetch("/assets/studio-small-09.hdr",{signal:AbortSignal.timeout(6000)});
    if(!response.ok)throw new Error("Environment unavailable");
    const data=new HDRLoader().parse(await response.arrayBuffer());
    texture=new THREE.DataTexture(data.data,data.width,data.height,THREE.RGBAFormat,data.type);
    texture.colorSpace=data.colorSpace;texture.flipY=data.flipY;
    texture.minFilter=texture.magFilter=THREE.LinearFilter;
    texture.needsUpdate=true;
    generator=new THREE.PMREMGenerator(renderer);
    const environment=generator.fromEquirectangular(texture);
    element.dataset.environment="photographic-studio";
    return environment;
  } catch {
    element.dataset.environment="procedural-studio";
    return studioEnvironment(renderer);
  } finally {
    texture?.dispose();generator?.dispose();
  }
}

function makeModel() {
  const root = new THREE.Group();
  const grain = surfaceTexture();
  const materials = {
    titanium:new THREE.MeshPhysicalMaterial({color:0x9da3a6, metalness:.94, roughness:.46, roughnessMap:grain, bumpMap:grain, bumpScale:.006, anisotropy:.7, envMapIntensity:1.25}),
    edge:new THREE.MeshStandardMaterial({color:0xc8c0ad, metalness:1, roughness:.16, envMapIntensity:1.5}),
    dark:new THREE.MeshStandardMaterial({color:0x10161d, metalness:.58, roughness:.45, bumpMap:grain, bumpScale:.007}),
    ceramic:new THREE.MeshPhysicalMaterial({color:0x080e15, metalness:.25, roughness:.24, clearcoat:.55, clearcoatRoughness:.13}),
    brass:new THREE.MeshStandardMaterial({color:0xba9054, metalness:1, roughness:.24, roughnessMap:grain}),
    socket:new THREE.MeshStandardMaterial({color:0x06090d, metalness:.3, roughness:.48}),
    light:new THREE.MeshStandardMaterial({color:0xb6f4ff, metalness:.15, roughness:.19, emissive:0x5ad9ff, emissiveIntensity:2.5}),
    status:new THREE.MeshStandardMaterial({color:0xa6d6de, metalness:.2, roughness:.3, emissive:0x63bbc9, emissiveIntensity:1.3}),
    lens:new THREE.MeshPhysicalMaterial({color:0x1d5366, metalness:.18, roughness:.08, clearcoat:1, transmission:.5, thickness:.25, ior:1.55}),
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
  mesh(rotor,bridgeGeometry(),[materials.titanium,materials.edge],0,.018);
  ring(rotor, 1.63, 1.91, .08, 0);
  ring(rotor, .67, .9, .12, .02, materials.edge);
  torus(rotor, 1.83, .022, .065, materials.brass);
  array(rotor, new THREE.BoxGeometry(.48,.012,.025), materials.dark, 6, 1.31, .086);
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
  ring(core,.60,.78,.09,-.024,materials.titanium);
  fasteners(core,8,.70,.04,.025);
  ring(core,.40,.53,.12,-.10,materials.dark);
  torus(core,.47,.035,.092,materials.light);
  mesh(core,new THREE.CylinderGeometry(.398,.398,.075,96),materials.lens,0,.09);
  mesh(core,new THREE.CylinderGeometry(.32,.32,.015,64),materials.ceramic,0,.035);
  array(core,new THREE.BoxGeometry(.009,.004,.073),materials.socket,36,.30,.14);
  const gimbal = new THREE.Group(); core.add(gimbal);
  const outerGimbal = torus(gimbal,.74,.035,0,materials.edge);
  const innerGimbal = torus(gimbal,.64,.023,0,materials.brass);
  innerGimbal.rotation.z = Math.PI/2;
  array(core,new THREE.BoxGeometry(.027,.03,.055),materials.dark,32,.56,.078);
  const light = new THREE.PointLight(0x6ccfee,3,4,2); core.add(light); light.position.y=.2;

  // A machined enclosure gives the mechanism a physical setting and scale.
  // The mechanism opens inside the enclosure; the outer housing stays still.
  const enclosure = new THREE.Group(); root.add(enclosure);
  ring(enclosure,2.23,3.03,.39,-1.77,materials.dark);
  ring(enclosure,2.27,3.08,.08,-1.54,materials.titanium);
  ring(enclosure,2.30,2.43,.035,-1.48,materials.brass);
  ring(enclosure,2.89,3.10,.085,-1.53,materials.edge);
  ring(enclosure,2.2,3.08,.09,-2.01,materials.dark);
  torus(enclosure,3.075,.018,-1.79,materials.brass);
  torus(enclosure,3.045,.012,-1.88,materials.edge);
  array(enclosure,new THREE.BoxGeometry(.052,.29,.031),materials.socket,120,3.048,-1.78);
  array(enclosure,new THREE.BoxGeometry(.026,.008,.10),materials.edge,100,2.81,-1.477);
  fasteners(enclosure,16,2.60,-1.475,.048);
  const braceGeometry=keep(arcGeometry(2.42,2.70,-.20,.20,.095,.018));
  for (let i=0;i<6;i++) {
    const brace=new THREE.Group(); enclosure.add(brace); brace.rotation.y=i/6*TAU;
    mesh(brace,braceGeometry,materials.titanium,0,-1.41);
    const arm=mesh(brace,new THREE.BoxGeometry(.15,.52,.23),materials.dark,2.57,-1.21,0);
    arm.rotation.z=-.24;
    mesh(brace,new THREE.CylinderGeometry(.052,.052,.35,16),materials.edge,2.61,-1.25,0);
    const cableCurve=new THREE.CatmullRomCurve3([new THREE.Vector3(2.54,-1.35,.13),new THREE.Vector3(2.74,-1.07,.18),new THREE.Vector3(2.69,-.64,.24),new THREE.Vector3(2.39,-.42,.25)]);
    mesh(brace,new THREE.TubeGeometry(cableCurve,24,.033,8,false),materials.ceramic);
    mesh(brace,new THREE.TubeGeometry(cableCurve,24,.010,6,false),materials.brass,0,.009,.027);
  }
  // Deep, irregular recesses keep the movement from reading as flat rings.
  for (let i=0;i<4;i++) {
    const bridge=new THREE.Group(); movement.add(bridge); bridge.rotation.y=i/4*TAU+.18;
    const arm=mesh(bridge,new THREE.BoxGeometry(.84,.042,.11),materials.titanium,1.32,.27,0);
    arm.rotation.y=.18;
    mesh(bridge,new THREE.CylinderGeometry(.054,.054,.04,24),materials.brass,.92,.31,0);
    mesh(bridge,new THREE.CylinderGeometry(.027,.027,.008,6),materials.socket,.92,.335,0);
  }
  array(rotor,new THREE.BoxGeometry(.010,.006,.075),materials.socket,120,1.77,.048);
  array(core,new THREE.CylinderGeometry(.012,.012,.008,12),materials.edge,48,.516,.116);

  const spindle=new THREE.Group();root.add(spindle);
  mesh(spindle,new THREE.CylinderGeometry(.23,.23,1,48),materials.dark);
  const springCurve=new class extends THREE.Curve {
    getPoint(t,out=new THREE.Vector3()){const a=t*TAU*9;return out.set(Math.cos(a)*.31,t-.5,Math.sin(a)*.31);}
  }();
  mesh(spindle,new THREE.TubeGeometry(springCurve,320,.026,8,false),materials.brass);
  for(let i=0;i<7;i++) ring(spindle,.23,.265,.032,i/6-.5,materials.edge);

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
    const spindleBottom=chassis.position.y+.38,spindleTop=core.position.y-.12;
    spindle.position.y=(spindleBottom+spindleTop)/2;
    spindle.scale.y=spindleTop-spindleBottom;
    spindle.rotation.y=time*.10;
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
    materials.light.emissiveIntensity = 1.25 + e*.25 + Math.sin(time*1.2)*.1;
    light.intensity = 1.7 + e*.7;
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
    materials.lens.transmission=mobile ? 0 : .5;
    materials.lens.metalness=mobile ? .6 : .18;
    materials.lens.color.setHex(mobile ? 0x275162 : 0x1d5366);
    materials.lens.needsUpdate=true;
  }
  return {root,update,setState,setQuality,dispose};
}

function createFinish(renderer) {
  const target=new THREE.WebGLRenderTarget(1,1,{type:renderer.extensions.has("EXT_color_buffer_float") ? THREE.HalfFloatType : THREE.UnsignedByteType});
  target.depthTexture=new THREE.DepthTexture(1,1,THREE.UnsignedIntType);
  const uniforms={tColor:{value:target.texture},tDepth:{value:target.depthTexture},uPixel:{value:new THREE.Vector2(1,1)},uFocus:{value:10},uNear:{value:.1},uFar:{value:80},uQuality:{value:1},uTime:{value:0}};
  const material=new THREE.ShaderMaterial({uniforms,depthTest:false,depthWrite:false,
    vertexShader:`varying vec2 vUv; void main(){vUv=uv;gl_Position=vec4(position.xy,0.0,1.0);}`,
    fragmentShader:`
      varying vec2 vUv;
      uniform sampler2D tColor,tDepth;
      uniform vec2 uPixel;
      uniform float uFocus,uNear,uFar,uQuality,uTime;
      float noise(vec2 p){return fract(sin(dot(p,vec2(12.9898,78.233)))*43758.5453);}
      vec3 bright(vec2 uv){vec3 c=texture2D(tColor,uv).rgb;return max(c-vec3(1.15),vec3(0.0));}
      void main(){
        vec3 color=texture2D(tColor,vUv).rgb;
        float z=texture2D(tDepth,vUv).x;
        float depth=uNear*uFar/(uFar-(uFar-uNear)*z);
        float blur=min(abs(depth-uFocus)*.42,2.6)*uQuality;
        if(blur>.18){
          vec3 b=vec3(0.0);
          for(int i=0;i<8;i++){
            float a=float(i)*.785398;
            b+=texture2D(tColor,vUv+vec2(cos(a),sin(a))*uPixel*blur).rgb;
          }
          color=mix(color,b*.125,.72);
        }
        vec2 r=uPixel*4.0;
        vec3 glow=bright(vUv+vec2(r.x,r.y))+bright(vUv+vec2(-r.x,r.y))+bright(vUv+vec2(r.x,-r.y))+bright(vUv-r);
        color+=glow*.022;
        vec2 p=vUv-.5;
        color*=1.0-.30*dot(p,p);
        gl_FragColor=vec4(color,1.0);
        #include <tonemapping_fragment>
        #include <colorspace_fragment>
        gl_FragColor.rgb+=(noise(vUv/uPixel+floor(uTime*12.0))-.5)*.009;
      }`});
  const geometry=new THREE.PlaneGeometry(2,2), scene=new THREE.Scene(), camera=new THREE.Camera();
  scene.add(new THREE.Mesh(geometry,material));
  return {
    resize(width,height,mobile){
      const samples=Math.min(mobile ? 2 : 4,renderer.capabilities.maxSamples);
      if(target.samples!==samples){target.samples=samples;target.dispose();}
      target.setSize(width,height);uniforms.uPixel.value.set(1/width,1/height);uniforms.uQuality.value=mobile ? 0 : 1;
    },
    render(source,sourceCamera,time){
      uniforms.uFocus.value=sourceCamera.position.distanceTo(new THREE.Vector3(0,.2,0));
      uniforms.uTime.value=time;
      renderer.setRenderTarget(target);renderer.render(source,sourceCamera);
      renderer.setRenderTarget(null);renderer.render(scene,camera);
    },
    dispose(){target.dispose();target.depthTexture.dispose();material.dispose();geometry.dispose();}
  };
}

function makeBackdrop() {
  const group=new THREE.Group(), geometries=new Set();
  const materials=[new THREE.MeshStandardMaterial({color:0x111820,metalness:.9,roughness:.28}),new THREE.MeshStandardMaterial({color:0x66573b,metalness:1,roughness:.23})];
  function part(geometry,material,position,rotation=[0,0,0]){
    geometries.add(geometry);const mesh=new THREE.Mesh(geometry,material);mesh.position.set(...position);mesh.rotation.set(...rotation);mesh.receiveShadow=true;mesh.castShadow=true;group.add(mesh);return mesh;
  }
  part(new THREE.TorusGeometry(5.5,.13,12,160),materials[0],[-.5,-.7,-4.5],[.25,-.35,.0]);
  part(new THREE.TorusGeometry(5.68,.016,8,160),materials[1],[-.5,-.7,-4.5],[.25,-.35,.0]);
  part(new THREE.TorusGeometry(7.25,.18,12,160),materials[0],[-.5,-.7,-5.3],[.25,-.35,.0]);
  const railGeometry=new THREE.BoxGeometry(.055,.065,20);
  for(const x of [-6,-5.85,4.5,4.65]) part(railGeometry,materials[1],[x,-2.23,-2]);
  for(let i=0;i<7;i++)part(new THREE.BoxGeometry(1.6,.20,.075),materials[0],[-4.9,-2.2,3.5-i*.38],[0,-.35,0]);
  return {group,dispose(){geometries.forEach(g=>g.dispose());materials.forEach(m=>m.dispose());}};
}

export async function createMechanismScene(element, {still=false} = {}) {
  const canvas = document.createElement("canvas");
  canvas.className = "mechanism-canvas";
  canvas.setAttribute("aria-hidden", "true");
  let renderer, model, environment, resizeObserver, finish, backdrop;
  let frame=0, running=false, failed=false, destroyed=false, ready=false, elapsed=still ? 8 : 2.6, last=0, lastPaint=0, frames=0;
  let view="auto", width=1, height=1, narrow=false, displayedState;
  const pointer = {x:0,y:0}, targetPointer = {x:0,y:0};
  const hero=element.dataset.sentinelCore==="access";
  const scene = new THREE.Scene();
  scene.background=new THREE.Color(0x090c10);
  scene.fog=new THREE.FogExp2(0x090c10,.038);
  const camera = new THREE.PerspectiveCamera(36,1,.1,80);
  const shadowGeometry = new THREE.PlaneGeometry(200,200);
  const shadowMaterial = new THREE.MeshStandardMaterial({color:0x05090d,metalness:.45,roughness:.5,envMapIntensity:.2});
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
    finish?.dispose();backdrop?.dispose();
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
    const expansion=model.update(elapsed,delta,view,pointer,snap);
    if(hero && !narrow){
      camera.position.set(3.7+Math.sin(elapsed*.07)*.30+pointer.x*.24,4.45+expansion*.65,8.65+expansion*.65);
      camera.lookAt(1.48,.18,0);
    }
    renderer.info.reset();
    finish.render(scene,camera,elapsed);
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
    width=box.width; height=box.height; narrow=window.innerWidth<(hero ? 901 : 700) || width<420;
    model.setQuality(narrow);
    renderer.setPixelRatio(Math.min(Math.max(window.devicePixelRatio || 1,1.25),1.5));
    renderer.setSize(width,height,false);
    finish.resize(Math.round(width*renderer.getPixelRatio()),Math.round(height*renderer.getPixelRatio()),narrow);
    camera.aspect=width/height;
    if(hero && !narrow){
      camera.position.set(3.7,5.1,9.2);
      camera.lookAt(1.48,.18,0);
    }else if(hero){
      const distance=Math.max(12.6,10.2/camera.aspect);
      camera.position.set(distance*.29,distance*.48,distance*.82);
      camera.lookAt(-.20,.17,0);
    }else{
      const distance=Math.max(13.8,12.1/camera.aspect);
      camera.position.set(distance*.34,distance*.46,distance*.81);
      camera.lookAt(0,-.12,0);
    }
    camera.updateProjectionMatrix();
    draw();
  }
  try {
    renderer=new THREE.WebGLRenderer({canvas,antialias:true,alpha:true,powerPreference:"high-performance"});
    renderer.setClearColor(0x000000,0);
    renderer.outputColorSpace=THREE.SRGBColorSpace;
    renderer.toneMapping=THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure=1.15;
    renderer.info.autoReset=false;
    renderer.shadowMap.enabled=true;
    renderer.shadowMap.type=THREE.PCFShadowMap;
    environment=await photographicEnvironment(renderer,element);
    scene.environment=environment.texture;
    scene.environmentIntensity=.42;
    scene.environmentRotation.set(0,.58,0);
    scene.add(new THREE.HemisphereLight(0xbdd7f1,0x080b10,.42));
    const key=new THREE.DirectionalLight(0xffd79c,4.1);
    key.position.set(-4,8,6); key.castShadow=true;
    key.shadow.mapSize.set(1024,1024);
    Object.assign(key.shadow.camera,{left:-4.5,right:4.5,top:4.5,bottom:-4.5,near:1,far:24});
    key.shadow.normalBias=.022; key.shadow.bias=-.00015;
    key.shadow.radius=3;
    scene.add(key);
    const rim=new THREE.DirectionalLight(0x91b6d5,3.3); rim.position.set(4,3,-5); scene.add(rim);
    const fill=new THREE.DirectionalLight(0xe8b16b,1.1); fill.position.set(-4,-1,-2); scene.add(fill);
    model=makeModel(); scene.add(model.root);
    if(hero){backdrop=makeBackdrop();scene.add(backdrop.group);}
    const shadow=new THREE.Mesh(shadowGeometry,shadowMaterial);
    shadow.rotation.x=-Math.PI/2; shadow.position.y=-2.25; shadow.receiveShadow=true; scene.add(shadow);
    finish=createFinish(renderer);
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
    return {setRunning,setState:state=>{
      if (state===displayedState) return;
      displayedState=state;
      model.setState(state);
      if (!running) draw();
    },setView:mode=>{
      view=["auto","exploded","assembled"].includes(mode) ? mode : "auto";
      if (!running) draw(0,true);
    },dispose};
  } catch(error) {
    element.dataset.renderer="fallback";
    dispose();
    throw error;
  }
}
