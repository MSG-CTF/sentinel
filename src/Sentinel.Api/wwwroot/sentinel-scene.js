import * as THREE from "./vendor/three/three.module.js";
import { HDRLoader } from "./vendor/three/HDRLoader.js";

// A procedural product model, not asset telemetry. Dimensions are scene units.
const TAU = Math.PI * 2;
const clamp = THREE.MathUtils.clamp;
const ease = value => { const x = clamp(value, 0, 1); return x * x * x * (x * (x * 6 - 15) + 10); };

export function cycleProgress(time) {
  const t = ((time % 26) + 26) % 26;
  return t < 2 ? 0 : t < 9 ? (t - 2) / 7 : t < 13 ? 1 : t < 22 ? 1 - (t - 13) / 9 : 0;
}

// One reversible cam path: a cover clears its seat before the carriage rises.
// All channels have zero endpoint velocity and acceleration. Reversing the
// master travel therefore seats the internals before the outer locks engage.
export function mechanicalPose(progress) {
  const p = clamp(progress, 0, 1);
  const travel = (start, end) => ease((p - start) / (end - start));
  return {
    unlock:travel(0, .17), cover:travel(.10, .42), iris:travel(.24, .55),
    lift:travel(.30, .72), carrier:travel(.46, .88), optic:travel(.58, 1),
    presentation:travel(0, 1),
  };
}

// Exact critically damped step, including velocity. Unlike resetting an ease
// on every click, changing direction mid-travel does not introduce a jerk.
export function advanceTravel(state, target, delta) {
  target = clamp(target, 0, 1);
  const dt = clamp(delta, 0, .1), omega = 7;
  const offset = state.position - clamp(target, 0, 1);
  const impulse = state.velocity + omega * offset, decay = Math.exp(-omega * dt);
  return {position:clamp(target + (offset + impulse * dt) * decay, 0, 1), velocity:(state.velocity - omega * impulse * dt) * decay};
}

function surfaceTexture() {
  const size = 512, data = new Uint8Array(size * size * 4);
  let seed = 713;
  const random = () => { seed = (1664525 * seed + 1013904223) >>> 0; return seed / 4294967296; };
  for (let y = 0; y < size; y++) {
    const line = 155 + random() * 74;
    for (let x = 0; x < size; x++) {
      const offset = (y * size + x) * 4;
      const value = Math.round(line + random() * 20);
      data[offset] = data[offset + 1] = data[offset + 2] = value;
      data[offset + 3] = 255;
    }
  }
  const texture = new THREE.DataTexture(data, size, size);
  texture.wrapS = texture.wrapT = THREE.RepeatWrapping;
  texture.anisotropy = 8;
  texture.magFilter = THREE.LinearFilter;
  texture.minFilter = THREE.LinearMipmapLinearFilter;
  texture.generateMipmaps = true;
  texture.needsUpdate = true;
  return texture;
}

function machinedMaterial(parameters, grain, {style=0, strength=1} = {}) {
  const material = new THREE.MeshPhysicalMaterial(parameters);
  material.onBeforeCompile = shader => {
    shader.uniforms.uMachining = {value:grain};
    shader.uniforms.uFinishStyle = {value:style};
    shader.uniforms.uFinishStrength = {value:strength};
    shader.vertexShader = shader.vertexShader.replace('#include <common>', `#include <common>
      varying vec3 vMachinedPosition, vMachinedNormal;`)
      .replace('#include <begin_vertex>', `#include <begin_vertex>
      vMachinedPosition = position; vMachinedNormal = normal;`);
    shader.fragmentShader = shader.fragmentShader.replace('#include <common>', `#include <common>
      uniform sampler2D uMachining;
      uniform float uFinishStyle, uFinishStrength;
      varying vec3 vMachinedPosition, vMachinedNormal;
      float filteredToolLine(float coordinate) {
        float footprint = fwidth(coordinate);
        return sin(coordinate * 6.2831853) * (1.0 - smoothstep(.18, .72, footprint));
      }
      vec3 machining(vec3 p) {
        float top = smoothstep(.45, .9, abs(normalize(vMachinedNormal).y));
        float radius = length(p.xz);
        float along = mix(p.y, radius, top);
        if(uFinishStyle > .5 && uFinishStyle < 1.5) along = mix(p.y, p.z + p.x * .12, top);
        if(uFinishStyle > 1.5) {
          vec2 tile = p.xz / .17;
          tile.x += mod(floor(tile.y), 2.0) * .5;
          along = mix(p.y, length(fract(tile) - .5) * .17, top);
        }
        float fine = filteredToolLine(along * 320.0);
        float cut = filteredToolLine(along * 42.0);
        float grain = texture2D(uMachining, vec2(p.x * .21 + p.z * .17, along * 5.0)).r - .72;
        float variation = texture2D(uMachining, p.xz * .07 + .31).r - .72;
        float score = pow(max(0.0, filteredToolLine(along * 21.0 + p.x * .035)), 20.0);
        return vec3((fine * .25 + cut * .38 + grain * .7 - score * .35) * .0008,
          grain * .18 + fine * .022 + cut * .025 + variation * .06, score);
      }
      vec3 machiningNormal(vec3 position, vec3 n, float height) {
        vec3 dx = dFdx(position), dy = dFdy(position);
        vec3 r1 = cross(dy, n), r2 = cross(n, dx);
        float determinant = dot(dx, r1);
        vec3 gradient = sign(determinant) * (dFdx(height) * r1 + dFdy(height) * r2);
        return normalize(abs(determinant) * n - gradient);
      }`)
      .replace('#include <roughnessmap_fragment>', `#include <roughnessmap_fragment>
        vec3 toolFinish = machining(vMachinedPosition) * uFinishStrength;
        roughnessFactor = clamp(roughnessFactor + toolFinish.y, .10, .8);
        diffuseColor.rgb *= 1.0 - toolFinish.z * .026;`)
      .replace('#include <normal_fragment_maps>', `#include <normal_fragment_maps>
        normal = machiningNormal(-vViewPosition, normal, toolFinish.x);`);
  };
  material.customProgramCacheKey = () => 'sentinel-machining-v1';
  return material;
}

function ringGeometry(inner, outer, height, bevel = .025) {
  const h = height / 2, b = Math.min(bevel, height / 3, (outer - inner) / 3);
  const profile = [];
  for (const [x,y,start] of [[inner+b,-h+b,Math.PI],[outer-b,-h+b,Math.PI*1.5],[outer-b,h-b,0],[inner+b,h-b,Math.PI*.5]]) {
    for(let i=0;i<=4;i++) {
      const a=start+i/4*Math.PI*.5;
      profile.push(new THREE.Vector2(x+Math.cos(a)*b,y+Math.sin(a)*b));
    }
  }
  profile.push(profile[0].clone());
  return new THREE.LatheGeometry(profile, outer > 1 ? 128 : 48);
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
  const pitch = radius * teeth / (teeth + 2), module = 2 * pitch / teeth;
  const base = pitch * Math.cos(20 * Math.PI / 180), root = pitch - module * 1.15;
  const involute = r => {const a=Math.acos(Math.min(1,base/r));return Math.tan(a)-a;};
  const half = Math.PI/(2*teeth)+involute(pitch);
  let first = true;
  const point = (r,a) => {const x=Math.cos(a)*r,y=Math.sin(a)*r;first ? shape.moveTo(x,y) : shape.lineTo(x,y);first=false;};
  for (let i = 0; i < teeth; i++) {
    const angle = i / teeth * TAU;
    point(root, angle - Math.PI/teeth);
    point(root, angle - half);
    for(let j=0;j<=4;j++){const r=base+(radius-base)*j/4;point(r,angle-half+involute(r));}
    for(let j=4;j>=0;j--){const r=base+(radius-base)*j/4;point(r,angle+half-involute(r));}
    point(root, angle + half);
    point(root, angle + Math.PI/teeth);
  }
  shape.closePath();
  const hole = new THREE.Path();
  hole.absarc(0, 0, radius * .16, 0, TAU, true);
  shape.holes.push(hole);
  for(let i=0;i<5;i++) {
    const a=i/5*TAU+.18, b=(i+1)/5*TAU-.18, cutout=new THREE.Path();
    cutout.moveTo(Math.cos(a)*radius*.35,Math.sin(a)*radius*.35);
    cutout.absarc(0,0,radius*.35,a,b,false);
    cutout.lineTo(Math.cos(b)*radius*.66,Math.sin(b)*radius*.66);
    cutout.absarc(0,0,radius*.66,b,a,true);cutout.closePath();shape.holes.push(cutout);
  }
  const geometry = new THREE.ExtrudeGeometry(shape, {depth, bevelEnabled:true, bevelThickness:.003, bevelSize:.003, bevelSegments:2, curveSegments:16});
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
    titanium:machinedMaterial({color:0xaeb7bd, metalness:1, roughness:.32, anisotropy:.38, envMapIntensity:1.1},grain),
    satin:machinedMaterial({color:0xb7bfc5, metalness:1, roughness:.37, anisotropy:.55, envMapIntensity:1.05},grain,{style:1,strength:1.2}),
    perlage:machinedMaterial({color:0x7f878d, metalness:1, roughness:.36, anisotropy:.3},grain,{style:2,strength:1.5}),
    edge:machinedMaterial({color:0xd8dde0, metalness:1, roughness:.14, envMapIntensity:1.15},grain,{strength:.25}),
    dark:machinedMaterial({color:0x242b33, metalness:1, roughness:.36, envMapIntensity:1.1},grain,{style:1,strength:.7}),
    ceramic:new THREE.MeshPhysicalMaterial({color:0x080e15, metalness:.25, roughness:.24, clearcoat:.55, clearcoatRoughness:.13}),
    brass:machinedMaterial({color:0xb99c64, metalness:1, roughness:.28, anisotropy:.28},grain,{strength:.65}),
    jewel:new THREE.MeshPhysicalMaterial({color:0x4b081c, metalness:.3, roughness:.15, clearcoat:1, clearcoatRoughness:.08}),
    socket:new THREE.MeshStandardMaterial({color:0x06090d, metalness:.3, roughness:.48}),
    light:new THREE.MeshStandardMaterial({color:0xb6f4ff, metalness:.15, roughness:.19, emissive:0x5ad9ff, emissiveIntensity:2.5}),
    status:new THREE.MeshStandardMaterial({color:0xa6d6de, metalness:.2, roughness:.3, emissive:0x63bbc9, emissiveIntensity:1.3}),
    lens:new THREE.MeshPhysicalMaterial({color:0x1f343e, metalness:.1, roughness:.10, clearcoat:1, transmission:.45, thickness:.16, ior:1.52}),
  };
  const resources = new Set();
  const detailGroups = [];
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
  function machiningLines(parent, inner, outer, y, count=9) {
    const group = new THREE.Group(); parent.add(group);
    detailGroups.push(group);
    // Real fine grooves catch a raking light even when texture detail minifies.
    for(let i=0;i<count;i++) torus(group,inner+(outer-inner)*i/(count-1),.0017,y,materials.dark);
    return group;
  }
  function race(parent, radius, y, count=36) {
    ring(parent,radius-.105,radius+.105,.045,y,materials.dark);
    torus(parent,radius-.085,.016,y+.035,materials.edge);
    torus(parent,radius+.085,.016,y+.035,materials.edge);
    array(parent,new THREE.SphereGeometry(.041,12,8),materials.edge,count,radius,y+.025);
    array(parent,new THREE.BoxGeometry(.024,.025,.14),materials.brass,count,radius,y+.018,Math.PI/count);
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
  race(chassis,1.62,.158,42);
  machiningLines(chassis,1.96,2.02,.211,5);

  const movement = new THREE.Group(); root.add(movement);
  ring(movement, .69, 1.95, .085, 0, materials.perlage);
  ring(movement, 1.81, 1.97, .035, .075, materials.brass);
  ring(movement, .57, .79, .12, .06);
  fasteners(movement, 8, 1.78, .07, .037);
  const gears = [];
  // Alternating 30/18-tooth wheels share a module and pitch-circle distance.
  const pitchDistance=Math.sqrt(1.29**2+1.41**2-2*1.29*1.41*Math.cos(Math.PI/8));
  const gearModule=pitchDistance/24;
  const largeGear = keep(gearGeometry(gearModule*16, 30, .062));
  const smallGear = keep(gearGeometry(gearModule*10, 18, .058));
  const bearing = keep(ringGeometry(.056,.115,.07,.01));
  const jewel = keep(new THREE.CylinderGeometry(.051,.051,.03,16));
  for (let i=0; i<8; i++) {
    const angle = i / 8 * TAU;
    const group = new THREE.Group();
    group.position.set(Math.cos(angle)*1.29,.15,Math.sin(angle)*1.29);
    movement.add(group);
    const wheel = mesh(group, largeGear, i%2 ? materials.titanium : materials.brass);
    gears.push({wheel,speed:.19,phase:angle});
    mesh(group,bearing,materials.edge,0,.06);
    mesh(group,jewel,materials.jewel,0,.10);
    ring(group,.112,.135,.014,.069,materials.brass);
    const a = angle + Math.PI / 8;
    const small = mesh(movement,smallGear,materials.edge,Math.cos(a)*1.41,.15,Math.sin(a)*1.41);
    gears.push({wheel:small,speed:-.19*30/18,phase:a+Math.PI/18});
    mesh(movement,bearing,materials.brass,small.position.x,.21,small.position.z);
  }

  const rotor = new THREE.Group(); root.add(rotor);
  mesh(rotor,bridgeGeometry(),[materials.satin,materials.edge],0,.018);
  ring(rotor, 1.63, 1.91, .08, 0);
  ring(rotor, .67, .9, .12, .02, materials.edge);
  torus(rotor, 1.83, .022, .065, materials.brass);
  array(rotor, new THREE.BoxGeometry(.48,.012,.025), materials.dark, 6, 1.31, .086);
  fasteners(rotor, 6, 1.77, .075, .046);
  fasteners(rotor, 6, .81, .102, .038);
  array(rotor, new THREE.BoxGeometry(.04,.035,.018), materials.brass, 84, 1.93, -.015);
  machiningLines(rotor,1.67,1.74,.055,5);
  race(rotor,.99,-.075,28);

  // Articulated rods keep the separated layers visually connected. Each rod
  // follows two moving anchor points instead of stretching along a fixed axis.
  const actuators = [];
  const rodGeometry = keep(new THREE.CylinderGeometry(.027,.027,1,12));
  const sleeveGeometry = keep(new THREE.CylinderGeometry(.060,.060,.29,20));
  const jointGeometry = keep(new THREE.SphereGeometry(.072,16,12));
  for (let i=0; i<6; i++) {
    const collar=mesh(root,ringGeometry(.028,.071,.045,.008),materials.brass);
    actuators.push({angle:i/6*TAU,rod:mesh(root,rodGeometry,materials.edge),sleeve:mesh(root,sleeveGeometry,materials.dark),collar,a:mesh(root,jointGeometry,materials.brass),b:mesh(root,jointGeometry,materials.brass)});
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
    mesh(part,armorGeometry,[materials.satin,materials.edge]);
    mesh(part,inlayGeometry,materials.ceramic,0,.136);
    mesh(part,new THREE.BoxGeometry(.21,.012,.019),materials.edge,2.13,.158,0);
    for (const sign of [-1,1]) {
      const a = sign*.36;
      mesh(part,fastenerGeometry,materials.edge,Math.cos(a)*2.12,.143,Math.sin(a)*2.12);
      mesh(part,socketGeometry,materials.socket,Math.cos(a)*2.12,.159,Math.sin(a)*2.12);
    }
    const lock=new THREE.Group();part.add(lock);
    mesh(lock,new THREE.CylinderGeometry(.040,.04,.105,20),materials.edge,2.12,-.13,0);
    mesh(lock,new THREE.CylinderGeometry(.065,.065,.025,32),materials.brass,2.12,-.19,0);
    mesh(part,arcGeometry(1.985,2.01,-.36,.36,.011,.003),materials.edge,0,.125);
    mesh(part,arcGeometry(2.26,2.265,-.39,.39,.005,.001),materials.socket,0,.128);
    const hinge=mesh(part,new THREE.CylinderGeometry(.052,.052,.26,24),materials.brass,1.995,-.12,0);
    hinge.rotation.x=Math.PI/2;
    armor.push({part,lock,index:i});
  }

  const iris = new THREE.Group(); root.add(iris);
  const petals = [];
  const petalGeometry = keep(arcGeometry(.64,1.63,-.315,.315,.09,.024));
  const petalInlay = keep(arcGeometry(.97,1.51,-.23,.23,.012,.008));
  for (let i=0; i<8; i++) {
    const pivot = new THREE.Group(); pivot.rotation.y = i / 8 * TAU;
    const part = new THREE.Group(); pivot.add(part); iris.add(pivot);
    mesh(part,petalGeometry,[materials.satin,materials.edge]);
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
  const outerGimbal = torus(gimbal,.74,.016,0,materials.edge);
  const innerGimbal = torus(gimbal,.64,.012,0,materials.brass);
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
  machiningLines(enclosure,2.68,2.77,-1.491,10);
  machiningLines(enclosure,2.92,3.045,-1.479,12);
  race(enclosure,2.48,-1.42,56);
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
  machiningLines(core,.61,.66,.04,4);

  // Separate micro-engraving layer, aligned with the curved case top.
  const labelCanvas=document.createElement('canvas');labelCanvas.width=labelCanvas.height=2048;
  const ink=labelCanvas.getContext('2d');
  ink.translate(1024,1024);ink.strokeStyle='#59605c';ink.fillStyle='#6a726f';
  ink.textAlign='center';ink.font='21px monospace';ink.lineWidth=1.3;
  for(let i=0;i<120;i++){
    const a=i/120*TAU;ink.save();ink.rotate(a);ink.beginPath();
    ink.moveTo(0,-947);ink.lineTo(0,i%10===0?-920:-936);ink.stroke();
    if(i%10===0){ink.fillText(String(i*3).padStart(3,'0'),0,-901);}ink.restore();
  }
  function curvedText(text,radius,start,size){
    ink.font=`${size}px monospace`;
    [...text].forEach((letter,i)=>{ink.save();ink.rotate(start+(i-(text.length-1)/2)*size*.65/radius);ink.fillText(letter,0,-radius);ink.restore();});
  }
  curvedText('SENTINEL  /  PRECISION ASSEMBLY',835,.22,21);
  curvedText('S01   ·   TITANIUM / CERAMIC',835,Math.PI+.22,19);
  const engraving=new THREE.CanvasTexture(labelCanvas);engraving.anisotropy=8;engraving.colorSpace=THREE.SRGBColorSpace;
  const engravingMaterial=new THREE.MeshStandardMaterial({map:engraving,transparent:true,depthWrite:false,metalness:.8,roughness:.5,polygonOffset:true,polygonOffsetFactor:-1});
  const engravingPlate=mesh(enclosure,new THREE.PlaneGeometry(6.2,6.2),engravingMaterial,0,-1.463,0,false);
  engravingPlate.rotation.x=-Math.PI/2;

  const spindle=new THREE.Group();root.add(spindle);
  const spindleShaft=mesh(spindle,new THREE.CylinderGeometry(.15,.15,1,32),materials.edge);
  const spindleSleeves=[];
  for(let i=0;i<3;i++) {
    const sleeve=new THREE.Group();root.add(sleeve);
    mesh(sleeve,new THREE.CylinderGeometry(.17+i*.035,.17+i*.035,.36,32),materials.dark);
    ring(sleeve,.155+i*.035,.20+i*.035,.035,.16,materials.edge);
    spindleSleeves.push(sleeve);
  }
  const springCurve=new class extends THREE.Curve {
    getPoint(t,out=new THREE.Vector3()){const a=t*TAU*9;return out.set(Math.cos(a)*.31,t-.5,Math.sin(a)*.31);}
  }();
  const spring=mesh(spindle,new THREE.TubeGeometry(springCurve,320,.015,8,false),materials.brass);

  // Combine stationary hardware inside each moving part. Dynamic wheels and
  // pistons remain independent; extra surface detail does not require one draw
  // call per screw or groove. Reuse indexed attributes rather than expanding
  // every triangle into three additional vertices.
  const animated = new Set([spring,spindleShaft,...gears.map(gear=>gear.wheel)]);
  root.traverse(parent => {
    if(!parent.isGroup || parent===root) return;
    const batches=new Map();
    for(const child of parent.children) {
      if(!child.isMesh || child.isInstancedMesh || animated.has(child) || Array.isArray(child.material)) continue;
      const key=`${child.material.uuid}/${child.castShadow}/${child.receiveShadow}`;
      if(!batches.has(key)) batches.set(key,[]);
      batches.get(key).push(child);
    }
    for(const objects of batches.values()) {
      if(objects.length<2)continue;
      const copies=objects.map(object=>{object.updateMatrix();return object.geometry.clone().applyMatrix4(object.matrix);});
      const count=copies.reduce((sum,g)=>sum+g.attributes.position.count,0);
      const geometry=keep(new THREE.BufferGeometry());
      for(const name of ['position','normal','uv']) {
        const size=name==='uv'?2:3, data=new Float32Array(count*size);let offset=0;
        for(const copy of copies){data.set(copy.attributes[name].array,offset);offset+=copy.attributes[name].array.length;}
        geometry.setAttribute(name,new THREE.BufferAttribute(data,size));
      }
      const indices=[];let offset=0;
      for(const copy of copies) {
        const attribute=copy.attributes.position;
        for(let i=0;i<(copy.index?.count??attribute.count);i++)indices.push(offset+(copy.index?.getX(i)??i));
        offset+=attribute.count;copy.dispose();
      }
      geometry.setIndex(indices);geometry.computeBoundingSphere();
      const combined=new THREE.Mesh(geometry,objects[0].material);
      combined.castShadow=objects[0].castShadow;combined.receiveShadow=objects[0].receiveShadow;
      objects.forEach(object=>parent.remove(object));parent.add(combined);
    }
  });

  let travel = {position:0,velocity:0};
  let previousSpringLength=-1;
  function update(time, delta, view, pointer, snap=false) {
    const target = view === "exploded" ? 1 : view === "assembled" ? 0 : cycleProgress(time);
    travel = snap ? {position:target,velocity:0} : advanceTravel(travel,target,delta);
    const pose=mechanicalPose(travel.position), e=pose.presentation;
    root.rotation.set(.025 + pointer.y*.035, -.30 + pointer.x*.10, -.075);
    root.position.y = 0;
    chassis.position.y = -1.30;
    movement.position.y = -.87 + pose.carrier*.35;
    movement.rotation.y = 0;
    rotor.position.y = -.56 + pose.lift*1.12;
    rotor.rotation.y = pose.unlock*.085;
    crown.position.y = -.17 + pose.cover*.34 + pose.lift*1.50;
    crown.rotation.y = -pose.unlock*.06;
    iris.position.y = -.17 + pose.lift*.92 + pose.iris*.40;
    iris.rotation.y = pose.iris*.18;
    core.position.y = .01 + pose.lift*.45 + pose.optic*1.12;
    gimbal.rotation.set(pose.optic*.65,0,pose.optic*.48);
    // The gimbal folds into the bezel instead of appearing/disappearing.
    gimbal.scale.setScalar(.82 + pose.optic*.18);
    armor.forEach(({part,lock,index}) => {
      const stagger = ease((travel.position - .10 - (index%3)*.012) / .30);
      part.position.set(stagger*.38,stagger*.045,0);
      part.rotation.z = -stagger*.10;
      lock.position.y=pose.unlock*.08;
      lock.rotation.x=pose.unlock*.20;
    });
    petals.forEach(({part,index}) => {
      const stagger = ease((travel.position - .24 - (index%4)*.01) / .28);
      part.position.set(stagger*.48,stagger*.11,0);
      part.rotation.z = -stagger*.25;
    });
    gears.forEach(({wheel,speed,phase}) => wheel.rotation.y = phase + time*speed);
    const spindleBottom=chassis.position.y+.38,spindleTop=core.position.y-.12;
    spindle.position.y=(spindleBottom+spindleTop)/2;
    const spindleLength=spindleTop-spindleBottom;
    spindleShaft.scale.y=spindleLength;
    // Compress the pitch of the spring without stretching its wire diameter.
    if(Math.abs(spindleLength-previousSpringLength)>.00005) {
      const springPositions=spring.geometry.attributes.position;
      if(!spring.geometry.userData.rest) spring.geometry.userData.rest=springPositions.array.slice();
      const rest=spring.geometry.userData.rest;
      for(let i=0;i<springPositions.count;i++) {
        const t=Math.floor(i/9)/320, axial=t-.5;
        springPositions.setY(i,axial*spindleLength+(rest[i*3+1]-axial));
      }
      springPositions.needsUpdate=true;
      spring.geometry.computeVertexNormals();spring.geometry.computeBoundingSphere();
      previousSpringLength=spindleLength;
    }
    spindleSleeves.forEach((sleeve,i)=>sleeve.position.y=spindleBottom+.18+i*(spindleLength-.36)/2);
    actuators.forEach(({angle,rod,sleeve,collar,a,b}) => {
      const lowerAngle=angle+chassis.rotation.y, upperAngle=angle+rotor.rotation.y;
      a.position.set(Math.cos(lowerAngle)*1.72,chassis.position.y+.24,Math.sin(lowerAngle)*1.72);
      b.position.set(Math.cos(upperAngle)*1.72,rotor.position.y-.07,Math.sin(upperAngle)*1.72);
      direction.subVectors(b.position,a.position);
      const length=direction.length();
      direction.normalize();
      rod.position.copy(a.position).lerp(b.position,.5);
      rod.quaternion.setFromUnitVectors(up,direction); rod.scale.y=length;
      sleeve.position.copy(a.position).addScaledVector(direction,.145);
      sleeve.quaternion.copy(rod.quaternion);
      collar.position.copy(a.position).addScaledVector(direction,.29);
      collar.quaternion.copy(rod.quaternion);
    });
    materials.light.emissiveIntensity = .6 + pose.optic*.24;
    light.intensity = .5 + pose.optic*.6;
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
    engraving.dispose();engravingMaterial.dispose();
  }
  let lowPower=false;
  function setQuality(mobile) {
    if (lowPower===mobile) return;
    lowPower=mobile;
    detailGroups.forEach(group=>group.visible=!mobile);
    // Transmission renders the entire scene twice; the mobile lens uses a
    // reflective coating instead, retaining depth without the extra pass.
    materials.lens.transmission=mobile ? 0 : .45;
    materials.lens.metalness=mobile ? .45 : .1;
    materials.lens.color.setHex(mobile ? 0x30434e : 0x1f343e);
    materials.lens.needsUpdate=true;
  }
  return {root,update,setState,setQuality,dispose};
}

function createFinish(renderer) {
  const target=new THREE.WebGLRenderTarget(1,1,{type:renderer.extensions.has("EXT_color_buffer_float") ? THREE.HalfFloatType : THREE.UnsignedByteType});
  target.depthTexture=new THREE.DepthTexture(1,1,THREE.UnsignedIntType);
  const uniforms={tColor:{value:target.texture},tDepth:{value:target.depthTexture},uPixel:{value:new THREE.Vector2(1,1)},uProjectionInverse:{value:new THREE.Matrix4()},uFocus:{value:10},uNear:{value:.1},uFar:{value:80},uQuality:{value:1},uTime:{value:0}};
  const material=new THREE.ShaderMaterial({uniforms,depthTest:false,depthWrite:false,
    vertexShader:`varying vec2 vUv; void main(){vUv=uv;gl_Position=vec4(position.xy,0.0,1.0);}`,
    fragmentShader:`
      varying vec2 vUv;
      uniform sampler2D tColor,tDepth;
      uniform vec2 uPixel;
      uniform mat4 uProjectionInverse;
      uniform float uFocus,uNear,uFar,uQuality,uTime;
      float noise(vec2 p){return fract(sin(dot(p,vec2(12.9898,78.233)))*43758.5453);}
      vec3 bright(vec2 uv){vec3 c=texture2D(tColor,uv).rgb;return max(c-vec3(1.15),vec3(0.0));}
      vec3 viewPosition(vec2 uv){
        vec4 p=uProjectionInverse*vec4(uv*2.0-1.0,texture2D(tDepth,uv).x*2.0-1.0,1.0);
        return p.xyz/p.w;
      }
      float contactShade(vec3 position){
        vec3 n=normalize(cross(dFdx(position),dFdy(position)));
        float shade=0.0;
        // Short world-space range limits dark halos around the silhouette.
        for(int i=0;i<12;i++){
          if(uQuality<.5 && i>=6)break;
          float a=float(i)*2.399963, radius=3.0+float(i)*1.1;
          vec3 delta=viewPosition(vUv+vec2(cos(a),sin(a))*uPixel*radius)-position;
          float distance=length(delta);
          float horizon=max(dot(n,delta/max(distance,.0001))-.12,0.0);
          shade+=horizon*(1.0-smoothstep(.08,.52,distance));
        }
        return clamp(1.0-shade*(uQuality<.5?.21:.105),.65,1.0);
      }
      void main(){
        vec3 color=texture2D(tColor,vUv).rgb;
        float z=texture2D(tDepth,vUv).x;
        float depth=uNear*uFar/(uFar-(uFar-uNear)*z);
        vec3 position=viewPosition(vUv);
        float occlusion=contactShade(position);
        color*=mix(1.0,occlusion,.65+uQuality*.35);
        float blur=min(abs(depth-uFocus)*.24,1.4)*uQuality;
        if(blur>.18){
          vec3 b=vec3(0.0);
          for(int i=0;i<8;i++){
            float a=float(i)*.785398;
            b+=texture2D(tColor,vUv+vec2(cos(a),sin(a))*uPixel*blur).rgb;
          }
          color=mix(color,b*.125*occlusion,.40);
        }
        vec2 r=uPixel*4.0;
        vec3 glow=bright(vUv+vec2(r.x,r.y))+bright(vUv+vec2(-r.x,r.y))+bright(vUv+vec2(r.x,-r.y))+bright(vUv-r);
        color+=glow*.022;
        vec2 p=vUv-.5;
        color*=1.0-.22*dot(p,p);
        gl_FragColor=vec4(color,1.0);
        #include <tonemapping_fragment>
        #include <colorspace_fragment>
        gl_FragColor.rgb+=(noise(vUv/uPixel+floor(uTime*12.0))-.5)*.003;
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
      uniforms.uProjectionInverse.value.copy(sourceCamera.projectionMatrixInverse);
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
  let frame=0, running=false, failed=false, destroyed=false, ready=false, elapsed=still ? 10 : 4.8, last=0, lastPaint=0, frames=0;
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
  function useFallback() {
    failed=true;
    element.dataset.renderer="fallback";
    document.querySelectorAll(`[data-core-controls="${element.dataset.sentinelCore}"]`).forEach(controls => controls.hidden=true);
    dispose();
  }
  function contextLost(event) {event.preventDefault();useFallback();}
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
      camera.position.set(3.5+Math.sin(elapsed*.055)*.12+pointer.x*.16,4.25+expansion*.78,8.5+expansion*.80);
      camera.lookAt(1.43,-.20+expansion*.37,0);
    }
    renderer.info.reset();
    finish.render(scene,camera,elapsed);
    if(failed){useFallback();return;}
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
    renderer.setPixelRatio(Math.min(Math.max(window.devicePixelRatio || 1,narrow ? 1.25 : 1.5),narrow ? 1.5 : 1.75));
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
      const distance=Math.max(12.9,11.4/camera.aspect);
      camera.position.set(distance*.34,distance*.46,distance*.81);
      camera.lookAt(0,-.12,0);
    }
    camera.updateProjectionMatrix();
    draw();
  }
  try {
    renderer=new THREE.WebGLRenderer({canvas,antialias:true,alpha:true,powerPreference:"high-performance"});
    renderer.debug.onShaderError=()=>{failed=true;};
    renderer.setClearColor(0x000000,0);
    renderer.outputColorSpace=THREE.SRGBColorSpace;
    renderer.toneMapping=THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure=1.04;
    renderer.info.autoReset=false;
    renderer.shadowMap.enabled=true;
    renderer.shadowMap.type=THREE.PCFShadowMap;
    environment=await photographicEnvironment(renderer,element);
    scene.environment=environment.texture;
    scene.environmentIntensity=.68;
    scene.environmentRotation.set(0,.88,0);
    scene.add(new THREE.HemisphereLight(0xcbd9e5,0x080b10,.28));
    const key=new THREE.DirectionalLight(0xffefdb,3.2);
    key.position.set(-4,8,6); key.castShadow=true;
    key.shadow.mapSize.set(2048,2048);
    Object.assign(key.shadow.camera,{left:-4.5,right:4.5,top:4.5,bottom:-4.5,near:1,far:24});
    key.shadow.normalBias=.012; key.shadow.bias=-.00010;
    key.shadow.radius=3;
    scene.add(key);
    const rim=new THREE.DirectionalLight(0xb3c7dc,2.5); rim.position.set(4,3,-5); scene.add(rim);
    const fill=new THREE.DirectionalLight(0xe8bf85,.8); fill.position.set(-4,-1,-2); scene.add(fill);
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
    if(failed)throw new Error('Mechanical scene shader unavailable');
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
