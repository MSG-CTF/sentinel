import * as THREE from "./vendor/three/three.module.js";
import { HDRLoader } from "./vendor/three/HDRLoader.js";
import { RectAreaLightUniformsLib } from "./vendor/three/RectAreaLightUniformsLib.js";

// A procedural product model, not asset telemetry. Dimensions are scene units.
const TAU = Math.PI * 2;
const clamp = THREE.MathUtils.clamp;
const ease = value => { const x = clamp(value, 0, 1); return clamp(x * x * x * (x * (x * 6 - 15) + 10), 0, 1); };

export function cycleProgress(time) {
  const t = ((time % 26) + 26) % 26;
  return t < 2 ? 0 : t < 9 ? (t - 2) / 7 : t < 13 ? 1 : t < 22 ? 1 - (t - 13) / 9 : 0;
}

// A short, single light cue after the first GPU frame. It does not delay
// authentication or pretend that the decorative core is server telemetry.
export function ignitionPose(time) {
  return {rim:ease((time-.10)/1.05),optic:ease((time-.70)/1.25),key:ease((time-.55)/1.85),travel:ease(time/3.8)};
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

// Every internal stage follows the same reversible cam coordinate. The cover
// clears first, six guided modules follow in order, then the shutter opens.
export function interiorPose(progress) {
  const p=clamp(progress,0,1),travel=(start,end)=>ease((p-start)/(end-start));
  return {latch:travel(.34,.44),hood:travel(.45,.63),emitter:travel(.73,.91),shutter:travel(.81,1),
    cells:Array.from({length:6},(_,i)=>({slide:travel(.64+i*.026,.735+i*.026),lift:travel(.70+i*.026,.82+i*.026)}))};
}

export function bridgePose(progress) {
  const p=clamp(progress,0,1);
  return Array.from({length:4},(_,i)=>({
    unlock:ease((p-i*.047)/.13),lift:ease((p-.15-i*.047)/.40)
  }));
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
  const size = 1024, data = new Uint8Array(size * size * 4);
  let seed = 713;
  const random = () => { seed = (1664525 * seed + 1013904223) >>> 0; return seed / 4294967296; };
  for (let y = 0; y < size; y++) {
    const line = 164 + random() * 54;
    for (let x = 0; x < size; x++) {
      const offset = (y * size + x) * 4;
      const value = Math.round(line + random() * 12 + Math.sin(x * .011 + y * .03) * 5);
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
        float cut = filteredToolLine(along * 118.0);
        float grain = texture2D(uMachining, vec2(p.x * .21 + p.z * .17, along * 5.0)).r - .72;
        float variation = texture2D(uMachining, p.xz * .07 + .31).r - .72;
        float score = pow(max(0.0, filteredToolLine(along * 21.0 + p.x * .035)), 20.0);
        return vec3((fine * .12 + cut * .10 + grain * .25 - score * .18) * .00038,
          grain * .24 + fine * .028 + cut * .032 + variation * .08, score);
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
  material.customProgramCacheKey = () => 'sentinel-machining-v3';
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
  return new THREE.LatheGeometry(profile, outer > 1 ? 128 : outer > .3 ? 64 : 24);
}

function arcGeometry(inner, outer, start, end, height, bevel = .025) {
  const shape = new THREE.Shape();
  shape.moveTo(Math.cos(start) * outer, Math.sin(start) * outer);
  shape.absarc(0, 0, outer, start, end, false);
  shape.lineTo(Math.cos(end) * inner, Math.sin(end) * inner);
  shape.absarc(0, 0, inner, end, start, true);
  shape.closePath();
  const geometry = new THREE.ExtrudeGeometry(shape, {depth:height, bevelEnabled:true, bevelThickness:bevel, bevelSize:bevel, bevelSegments:3, steps:1, curveSegments:32});
  geometry.rotateX(Math.PI / 2);
  geometry.translate(0, height / 2, 0);
  return geometry;
}

function milledSlab(width, depth, height, radius, bevel=.02) {
  const x=width/2,z=depth/2,r=radius,shape=new THREE.Shape();
  shape.moveTo(-x+r,-z);shape.lineTo(x-r,-z);shape.quadraticCurveTo(x,-z,x,-z+r);
  shape.lineTo(x,z-r);shape.quadraticCurveTo(x,z,x-r,z);
  shape.lineTo(-x+r,z);shape.quadraticCurveTo(-x,z,-x,z-r);
  shape.lineTo(-x,-z+r);shape.quadraticCurveTo(-x,-z,-x+r,-z);
  const geometry=new THREE.ExtrudeGeometry(shape,{depth:height,bevelEnabled:true,bevelThickness:bevel,bevelSize:bevel,bevelSegments:3,steps:1,curveSegments:12});
  geometry.rotateX(Math.PI/2);geometry.translate(0,height/2,0);
  return geometry;
}

// A physical set gives the instrument a scale, contact shadow and a place to
// sit. No background image or screen-space light streaks are composited here.
function makeStudio(grain) {
  const root=new THREE.Group(),geometries=new Set(),textures=new Set();
  const materials={
    floor:new THREE.MeshStandardMaterial({color:0x171c20,metalness:.18,roughness:.62,envMapIntensity:.18}),
    wall:new THREE.MeshStandardMaterial({color:0x192126,metalness:.34,roughness:.57,envMapIntensity:.24}),
    reveal:new THREE.MeshStandardMaterial({color:0x070b0e,metalness:.35,roughness:.65}),
    edge:machinedMaterial({color:0x495860,metalness:1,roughness:.32,envMapIntensity:.7},grain,{style:1,strength:.5}),
    base:machinedMaterial({color:0x252f36,metalness:.88,roughness:.34,envMapIntensity:.65},grain,{style:1,strength:.6}),
    inset:machinedMaterial({color:0x11191e,metalness:.68,roughness:.45,envMapIntensity:.42},grain,{style:1,strength:.6}),
    steel:machinedMaterial({color:0x8c979b,metalness:1,roughness:.23,envMapIntensity:.7},grain,{strength:.4}),
    light:new THREE.MeshStandardMaterial({color:0xcbdde3,emissive:0xc0dce6,emissiveIntensity:.95,roughness:.6}),
  };
  function add(parent,geometry,material,position,shadow=false){
    geometries.add(geometry);const object=new THREE.Mesh(geometry,material);
    object.position.set(...position);object.castShadow=shadow;object.receiveShadow=true;parent.add(object);return object;
  }
  const floor=add(root,new THREE.PlaneGeometry(120,120),materials.floor,[0,-2.48,0]);floor.rotation.x=-Math.PI/2;
  // Recessed wall panels and a strip diffuser live behind the focus plane.
  // Their highlights continue onto the metal, rather than floating in the air.
  add(root,new THREE.BoxGeometry(27,14,.3),materials.reveal,[0,3.5,-5.5]);
  for(const x of [-9.8,-4.8,.2,5.2,10.2]){
    add(root,new THREE.BoxGeometry(4.96,13,.18),materials.wall,[x,3.8,-5.28]);
    add(root,new THREE.BoxGeometry(.018,13,.025),materials.edge,[x-2.45,3.8,-5.17]);
  }
  const ribs=new THREE.InstancedMesh(new THREE.BoxGeometry(.042,8,.11),materials.reveal,32);
  geometries.add(ribs.geometry);const dummy=new THREE.Object3D();
  for(let i=0;i<32;i++){dummy.position.set(-6.6+i*.11,1.6,-5.1);dummy.updateMatrix();ribs.setMatrixAt(i,dummy.matrix);}
  ribs.instanceMatrix.needsUpdate=true;root.add(ribs);
  add(root,new THREE.BoxGeometry(.14,5.8,.07),materials.reveal,[1.9,.5,-5.1]);
  add(root,new THREE.BoxGeometry(.026,5.6,.012),materials.light,[1.9,.5,-5.045]);
  const wallWash=new THREE.RectAreaLight(0xb3ccda,2.6,.14,5.6);
  wallWash.position.set(1.9,.5,-4.99);wallWash.lookAt(0,-.5,0);root.add(wallWash);
  // Oversized deck joints establish perspective without a decorative grid.
  for(const x of [-8,-3.8,4.3])add(root,new THREE.BoxGeometry(.015,.005,26),materials.reveal,[x,-2.474,0]);
  for(const z of [-3.9,3.7])add(root,new THREE.BoxGeometry(28,.005,.015),materials.reveal,[0,-2.474,z]);

  const base=new THREE.Group();base.rotation.y=-.16;root.add(base);
  add(base,milledSlab(6.5,5.65,.21,.20,.035),materials.base,[0,-2.28,0],true);
  add(base,milledSlab(6.39,5.54,.035,.19,.012),materials.steel,[0,-2.146,0],true);
  add(base,milledSlab(6.31,5.46,.07,.18,.016),materials.inset,[0,-2.075,0],true);
  for(const x of [-2.65,2.65])for(const z of [-2.25,2.25])add(base,new THREE.CylinderGeometry(.18,.19,.10,24),materials.reveal,[x,-2.43,z],true);
  // The lens rests on a ball seat, allowing the housing's small gimbal motion.
  const profile=[[0,-2.03],[.87,-2.03],[.94,-1.98],[.94,-1.92],[.62,-1.87],[.54,-1.68],[0,-1.68]];
  add(root,new THREE.LatheGeometry(profile.map(p=>new THREE.Vector2(...p)),96),materials.base,[0,0,0],true);
  add(root,ringGeometry(.56,.69,.045,.008),materials.steel,[0,-1.82,0],true);
  add(root,new THREE.SphereGeometry(.43,48,24),materials.inset,[0,-1.58,0],true);
  for(const x of [-2.94,2.94])for(const z of [-2.52,2.52]){
    add(base,ringGeometry(.041,.081,.022,.004),materials.steel,[x,-2.025,z],true);
    add(base,new THREE.CylinderGeometry(.026,.026,.008,6),materials.reveal,[x,-2.018,z]);
  }
  // A small physical maker's plate and fine indexing cuts on the deck edge.
  const canvas=document.createElement('canvas');canvas.width=1024;canvas.height=128;
  const ctx=canvas.getContext('2d');ctx.fillStyle='#a5b3ba';ctx.font='500 29px monospace';
  ctx.fillText('SENTINEL',36,52);ctx.fillStyle='#6d7b82';ctx.font='18px monospace';ctx.fillText('OPTICAL INSTRUMENT     /     S—01',36,88);
  const plateMap=new THREE.CanvasTexture(canvas);plateMap.colorSpace=THREE.SRGBColorSpace;plateMap.anisotropy=8;textures.add(plateMap);
  materials.plate=new THREE.MeshStandardMaterial({map:plateMap,transparent:true,depthWrite:false,roughness:.65,metalness:.25});
  const plate=add(base,new THREE.PlaneGeometry(1.68,.21),materials.plate,[-1.75,-2.021,2.48]);plate.rotation.x=-Math.PI/2;
  const cuts=new THREE.InstancedMesh(new THREE.BoxGeometry(.008,.004,.065),materials.edge,19);geometries.add(cuts.geometry);
  for(let i=0;i<19;i++){dummy.position.set(1.13+i*.067,-2.025,2.49);dummy.updateMatrix();cuts.setMatrixAt(i,dummy.matrix);}
  cuts.instanceMatrix.needsUpdate=true;base.add(cuts);
  // Batch the fixed set by finish. Keep turned steel in object space so its
  // radial machining still follows each fastener's own centre.
  root.updateMatrixWorld(true);
  const batches=new Map();
  root.traverse(object=>{
    if(!object.isMesh||object.isInstancedMesh||object.material===materials.steel||object.material.transparent)return;
    const key=object.material.uuid+String(object.castShadow);
    if(!batches.has(key))batches.set(key,[]);batches.get(key).push(object);
  });
  for(const objects of batches.values()){
    if(objects.length<2)continue;
    const pieces=objects.map(object=>{
      const geometry=object.geometry.index?object.geometry.toNonIndexed():object.geometry.clone();
      return geometry.applyMatrix4(object.matrixWorld);
    });
    const geometry=new THREE.BufferGeometry();
    for(const name of ['position','normal','uv']){
      const count=pieces.reduce((total,piece)=>total+piece.getAttribute(name).array.length,0),array=new Float32Array(count);
      let offset=0;for(const piece of pieces){const source=piece.getAttribute(name).array;array.set(source,offset);offset+=source.length;}
      geometry.setAttribute(name,new THREE.BufferAttribute(array,name==='uv'?2:3));
    }
    add(root,geometry,objects[0].material,[0,0,0],objects[0].castShadow);
    objects.forEach(object=>object.removeFromParent());pieces.forEach(piece=>piece.dispose());
  }
  return {root,setIllumination(power){materials.light.emissiveIntensity=.12+power*.83;wallWash.intensity=.3+power*2.3;},dispose(){geometries.forEach(g=>g.dispose());textures.forEach(t=>t.dispose());Object.values(materials).forEach(m=>m.dispose());root.traverse(o=>{if(o.isInstancedMesh)o.dispose();});}};
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

async function photographicEnvironment(renderer,element,source) {
  let texture,generator;
  try {
    const buffer=source?await source:await fetch("/assets/studio-small-09.hdr",{signal:AbortSignal.timeout(6000)}).then(response=>response.ok?response.arrayBuffer():null);
    if(!buffer)throw new Error("Environment unavailable");
    const data=new HDRLoader().parse(buffer);
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

function makeModel(grain) {
  const root=new THREE.Group(), resources=new Set(), textures=new Set();
  const materials={
    body:machinedMaterial({color:0x666965,metalness:1,roughness:.31,anisotropy:.35,envMapIntensity:1.0},grain,{strength:.65}),
    face:machinedMaterial({color:0x909ba1,metalness:1,roughness:.29,anisotropy:.48,envMapIntensity:1.05},grain,{style:1,strength:.8}),
    edge:machinedMaterial({color:0xc5cbd0,metalness:1,roughness:.16,envMapIntensity:1.15},grain,{strength:.35}),
    bore:machinedMaterial({color:0x242b2d,metalness:1,roughness:.37,anisotropy:.18},grain,{strength:.55}),
    nickel:machinedMaterial({color:0x999187,metalness:1,roughness:.29,anisotropy:.38},grain,{style:2,strength:.72}),
    ceramic:new THREE.MeshPhysicalMaterial({color:0x080c10,metalness:.25,roughness:.27,clearcoat:.24,clearcoatRoughness:.18}),
    rubber:new THREE.MeshStandardMaterial({color:0x080b0e,roughness:.76,metalness:0}),
    copper:machinedMaterial({color:0x786249,metalness:1,roughness:.3},grain,{strength:.55}),
    // A thin coated cover keeps the emitter visible without a second scene
    // transmission pass. The dark optical well supplies the depth behind it.
    glass:new THREE.MeshPhysicalMaterial({color:0x789ba5,metalness:0,roughness:.065,envMapIntensity:.55,clearcoat:1,clearcoatRoughness:.06,transparent:true,opacity:.15,depthWrite:false,iridescence:.10,iridescenceIOR:1.3,iridescenceThicknessRange:[180,260]}),
    emitter:new THREE.MeshStandardMaterial({color:0xe9f6ff,emissive:0xc3eaff,emissiveIntensity:5.0,roughness:.32,metalness:0}),
    lightGuide:new THREE.MeshStandardMaterial({color:0x254e59,emissive:0x199dbf,emissiveIntensity:1.6,roughness:.33,metalness:.08}),
    seam:new THREE.MeshStandardMaterial({color:0xeee6d5,emissive:0xd8c398,emissiveIntensity:.9,roughness:.36,metalness:0}),
    status:new THREE.MeshStandardMaterial({color:0xbee9ff,emissive:0x73c9ed,emissiveIntensity:2.6,roughness:.25}),
  };
  const keep=g=>{resources.add(g);return g;};
  function mesh(parent,geometry,material,x=0,y=0,z=0,shadow=true){
    keep(geometry);const object=new THREE.Mesh(geometry,material);
    object.position.set(x,y,z);object.castShadow=shadow;object.receiveShadow=true;parent.add(object);return object;
  }
  function ring(parent,inner,outer,height,y,material=materials.body){return mesh(parent,ringGeometry(inner,outer,height,.018),material,0,y);}
  function torus(parent,radius,tube,y,material=materials.edge){
    const object=mesh(parent,new THREE.TorusGeometry(radius,tube,8,128),material,0,y);
    object.rotation.x=Math.PI/2;
    if(material===materials.emitter||material===materials.seam||material===materials.lightGuide){object.castShadow=false;object.receiveShadow=false;}
    return object;
  }
  function instance(parent,geometry,material,transforms){
    keep(geometry);const object=new THREE.InstancedMesh(geometry,material,transforms.length),dummy=new THREE.Object3D();
    transforms.forEach((transform,index)=>{
      dummy.position.set(...transform.position);dummy.rotation.set(...(transform.rotation||[0,0,0]));dummy.scale.set(1,1,1);dummy.updateMatrix();object.setMatrixAt(index,dummy.matrix);
    });object.instanceMatrix.needsUpdate=true;object.castShadow=object.receiveShadow=true;parent.add(object);return object;
  }
  function radial(parent,geometry,material,count,radius,y,offset=0){
    return instance(parent,geometry,material,Array.from({length:count},(_,i)=>{
      const a=i/count*TAU+offset;return {position:[Math.cos(a)*radius,y,Math.sin(a)*radius],rotation:[0,-a,0]};
    }));
  }
  function repeatAssembly(template,count,parent){
    // Batch each finish once, then instance the complete cartridge. Tiny screws
    // and coil windings do not each become a draw call for every moving module.
    template.updateMatrixWorld(true);
    const finishes=new Map(),batches=[];
    template.traverse(object=>{if(!object.isMesh)return;if(!finishes.has(object.material))finishes.set(object.material,[]);finishes.get(object.material).push(object);});
    for(const [material,objects] of finishes){
      const pieces=objects.map(object=>(object.geometry.index?object.geometry.toNonIndexed():object.geometry.clone()).applyMatrix4(object.matrixWorld));
      const geometry=new THREE.BufferGeometry();
      for(const name of ['position','normal','uv']){
        const array=new Float32Array(pieces.reduce((sum,piece)=>sum+piece.getAttribute(name).array.length,0));
        let offset=0;for(const piece of pieces){const source=piece.getAttribute(name).array;array.set(source,offset);offset+=source.length;}
        geometry.setAttribute(name,new THREE.BufferAttribute(array,name==='uv'?2:3));
      }
      pieces.forEach(piece=>piece.dispose());
      const batch=instance(parent,geometry,material,Array.from({length:count},()=>({position:[0,0,0]})));
      if(material===materials.lightGuide)batch.castShadow=batch.receiveShadow=false;
      batches.push(batch);
    }
    return batches;
  }
  function extrude(shape,depth,bevel=.025,segments=40){
    const geometry=new THREE.ExtrudeGeometry(shape,{depth,bevelEnabled:true,bevelThickness:bevel,bevelSize:bevel,bevelSegments:4,curveSegments:segments,steps:1});
    geometry.rotateX(Math.PI/2);geometry.translate(0,depth/2,0);return geometry;
  }
  function arcPath(path,inner,outer,start,end,clockwise=false){
    path.moveTo(Math.cos(start)*outer,Math.sin(start)*outer);
    path.absarc(0,0,outer,start,end,clockwise);
    path.absarc(0,0,inner,end,start,!clockwise);path.closePath();return path;
  }
  function quarterGeometry(){
    const shape=arcPath(new THREE.Shape(),1.49,2.36,.045,Math.PI/2-.045);
    // Through-pockets leave a narrow load path between each pair of guide seats.
    // The bevel reveals cut metal inside the opening, not a painted black slot.
    shape.holes.push(arcPath(new THREE.Path(),1.72,2.14,1.24,.32,true));
    for(const a of [.16,Math.PI/2-.16]){
      const hole=new THREE.Path();hole.absarc(Math.cos(a)*1.94,Math.sin(a)*1.94,.084,0,TAU,true);shape.holes.push(hole);
    }
    return extrude(shape,.105,.018);
  }
  function socketGeometry(){
    const shape=new THREE.Shape();shape.absarc(0,0,.062,0,TAU,false);
    const hole=new THREE.Path();
    for(let i=0;i<6;i++){
      const a=-i/6*TAU,x=Math.cos(a)*.029,y=Math.sin(a)*.029;
      if(i===0)hole.moveTo(x,y);else hole.lineTo(x,y);
    }
    hole.closePath();shape.holes.push(hole);return extrude(shape,.038,.0035,12);
  }
  const socket=keep(socketGeometry());
  function fasteners(parent,positions){
    instance(parent,ringGeometry(.067,.095,.016,.004),materials.body,positions);
    instance(parent,socket,materials.edge,positions.map(t=>({position:[t.position[0],t.position[1]+.014,t.position[2]]})));
    instance(parent,new THREE.CylinderGeometry(.034,.034,.007,6),materials.bore,positions.map(t=>({position:[t.position[0],t.position[1]-.008,t.position[2]]})));
  }
  function teethGeometry(inner,outer,count,height){
    const shape=new THREE.Shape(),hole=new THREE.Path();
    for(let i=0;i<count*4;i++){
      const a=i/(count*4)*TAU,r=i%4===1||i%4===2?outer:outer-.032;
      if(i===0)shape.moveTo(Math.cos(a)*r,Math.sin(a)*r);else shape.lineTo(Math.cos(a)*r,Math.sin(a)*r);
    }
    shape.closePath();hole.absarc(0,0,inner,0,TAU,true);shape.holes.push(hole);
    return extrude(shape,height,.0025,16);
  }

  // Thin pocketed bridges, bearing races and guide seats make the assembly
  // legible as manufactured parts. Finishes follow those parts' functions.
  const chassis=new THREE.Group();root.add(chassis);chassis.position.y=-1.08;
  const caseProfile=[[1.18,-.30],[2.20,-.30],[2.31,-.26],[2.38,-.18],[2.38,.16],[2.32,.22],[2.16,.22],[2.12,.10],[1.47,.10],[1.41,.24],[1.18,.24],[1.18,-.30]];
  mesh(chassis,new THREE.LatheGeometry(caseProfile.map(p=>new THREE.Vector2(...p)),160),materials.bore);
  ring(chassis,.38,1.25,.10,-.315,materials.bore);
  ring(chassis,2.30,2.415,.045,-.20,materials.edge);
  ring(chassis,2.33,2.415,.045,.155,materials.edge);
  ring(chassis,2.33,2.386,.025,-.07,materials.rubber);
  ring(chassis,1.45,2.17,.035,.125,materials.nickel);
  ring(chassis,1.18,1.44,.39,.04,materials.bore);
  ring(chassis,1.12,1.23,.055,.27,materials.edge);
  const coolingFins=[],caseBolts=[];
  for(let i=0;i<128;i++) {
    const a=i/128*TAU;
    coolingFins.push({position:[Math.cos(a)*2.38,-.015,Math.sin(a)*2.38],rotation:[0,-a,0]});
  }
  instance(chassis,new THREE.BoxGeometry(.019,.24,.017),materials.bore,coolingFins);
  for(let i=0;i<12;i++){const a=i/12*TAU;caseBolts.push({position:[Math.cos(a)*2.255,.24,Math.sin(a)*2.255]});}
  fasteners(chassis,caseBolts);

  const cover=new THREE.Group();root.add(cover);
  const covers=[],quarter=keep(quarterGeometry());
  for(let i=0;i<4;i++){
    const pivot=new THREE.Group();cover.add(pivot);pivot.rotation.y=i/4*TAU;
    const panel=new THREE.Group();pivot.add(panel);
    mesh(panel,quarter,[materials.face,materials.edge]);
    // Inset black lands separate broad satin faces from the polished bevels.
    const trim=new THREE.Group();
    mesh(trim,arcGeometry(2.19,2.28,.15,Math.PI/2-.15,.007,.002),materials.bore,0,.078);
    mesh(trim,arcGeometry(1.545,1.564,.17,Math.PI/2-.17,.009,.002),materials.edge,0,.079);
    mesh(trim,arcGeometry(2.288,2.305,.15,Math.PI/2-.15,.008,.002),materials.edge,0,.079);
    mesh(trim,arcGeometry(2.24,2.34,.075,Math.PI/2-.075,.035,.008),materials.bore,0,-.094);
    mesh(trim,arcGeometry(1.52,1.61,.055,Math.PI/2-.055,.025,.005),materials.bore,0,-.078);
    // An inset bridge and transverse ribs sit below the machined opening.
    mesh(trim,arcGeometry(1.87,2.00,.38,1.18,.028,.006),materials.nickel,0,-.13);
    repeatAssembly(trim,1,panel);
    const ribs=[];
    for(let n=0;n<9;n++){
      const a=.40+n*.095;ribs.push({position:[Math.cos(a)*1.94,-.097,Math.sin(a)*1.94],rotation:[0,-a,0]});
    }
    instance(panel,new THREE.BoxGeometry(.31,.014,.013),materials.body,ribs);
    fasteners(panel,[.16,Math.PI/2-.16].map(a=>({position:[Math.cos(a)*1.94,.055,Math.sin(a)*1.94]})));
    const seat=mesh(panel,ringGeometry(.056,.12,.12,.009),materials.body,1.527,-.11,1.527);
    const latch=mesh(panel,new THREE.CylinderGeometry(.044,.044,.16,24),materials.edge,seat.position.x,-.19,seat.position.z);
    covers.push({panel,latch});
  }

  // A reduction train shares the chassis with the optical carriage. Small
  // teeth, spokes and recessed hubs reveal scale when the bridges clear it.
  const gears=[];
  for(let i=0;i<4;i++){
    const a=i/4*TAU+.34,g=new THREE.Group();g.position.set(Math.cos(a)*1.78,.23,Math.sin(a)*1.78);chassis.add(g);
    mesh(g,teethGeometry(.20,.365,28,.035),i%2?materials.nickel:materials.body);
    ring(g,.064,.115,.046,.025,materials.edge);
    radial(g,new THREE.BoxGeometry(.22,.028,.038),materials.nickel,5,.17,.007);
    mesh(g,new THREE.CylinderGeometry(.036,.036,.07,24),materials.bore,0,.035);
    gears.push(g);
  }

  const cartridge=new THREE.Group();root.add(cartridge);
  const barrelProfile=[[1.15,-.40],[1.37,-.40],[1.43,-.35],[1.43,-.14],[1.36,-.10],[1.34,.09],[1.26,.16],[1.19,.16],[1.15,.10],[1.15,-.40]];
  mesh(cartridge,new THREE.LatheGeometry(barrelProfile.map(p=>new THREE.Vector2(...p)),160),materials.bore);
  ring(cartridge,1.33,1.42,.035,-.10,materials.edge);
  ring(cartridge,1.25,1.35,.035,.125,materials.face);
  radial(cartridge,new THREE.BoxGeometry(.032,.17,.024),materials.body,80,1.403,-.245);
  // Recessed light-guide windows stay attached to the barrel and are exposed
  // by its mechanical travel. No floating rings or new postprocessing passes.
  const lightWindows=radial(cartridge,new THREE.BoxGeometry(.016,.078,.054),materials.lightGuide,16,1.437,-.27,Math.PI/16);
  lightWindows.castShadow=lightWindows.receiveShadow=false;
  torus(cartridge,1.265,.006,.15,materials.seam);
  radial(cartridge,new THREE.BoxGeometry(.013,.007,.053),materials.bore,60,1.31,.149);
  for(const y of [-.085,-.045,-.005])ring(cartridge,1.32,1.351,.010,y,materials.edge);
  // Exposed ball race under the lens: a real gap between two polished tracks.
  ring(cartridge,1.13,1.19,.032,.185,materials.edge);
  ring(cartridge,1.25,1.30,.032,.185,materials.edge);
  radial(cartridge,new THREE.SphereGeometry(.037,16,10),materials.edge,32,1.22,.181);
  ring(cartridge,1.192,1.246,.010,.171,materials.copper);

  const optic=new THREE.Group();root.add(optic);
  ring(optic,.89,1.12,.29,-.035,materials.bore);
  const hood=new THREE.Group();optic.add(hood);
  ring(hood,.91,1.13,.055,.15,materials.face);
  ring(hood,.93,.968,.029,.180,materials.edge);
  torus(hood,.895,.007,.183,materials.emitter);
  for(const y of [-.16,-.12,-.08])ring(optic,1.08,1.145,.012,y,materials.edge);
  radial(optic,new THREE.BoxGeometry(.025,.11,.024),materials.body,72,1.115,-.018);
  ring(hood,.968,1.03,.005,.182,materials.bore);
  radial(hood,new THREE.BoxGeometry(.025,.006,.008),materials.face,60,1.068,.184);
  const engraving=document.createElement('canvas');engraving.width=engraving.height=1024;
  const etch=engraving.getContext('2d');etch.translate(512,512);etch.fillStyle='#1c282d';
  etch.font='500 22px monospace';etch.textAlign='center';etch.textBaseline='middle';
  for(let i=0;i<12;i++){
    const a=i/12*TAU;etch.save();etch.rotate(a);etch.fillText(String(i*30).padStart(3,'0'),0,-453);etch.restore();
  }
  const etching=new THREE.CanvasTexture(engraving);etching.colorSpace=THREE.SRGBColorSpace;etching.anisotropy=8;textures.add(etching);
  materials.etching=new THREE.MeshStandardMaterial({map:etching,transparent:true,depthWrite:false,roughness:.65,metalness:.2});
  const scale=mesh(hood,new THREE.PlaneGeometry(2.28,2.28),materials.etching,0,.186,0,false);scale.rotation.x=-Math.PI/2;
  // A frosted aperture sits in a machined optical well. A radial emission map
  // rolls off inside the physical surface rather than drawing a screen halo.
  const phosphor=document.createElement('canvas');phosphor.width=phosphor.height=256;
  const phosphorContext=phosphor.getContext('2d'),gradient=phosphorContext.createRadialGradient(128,128,6,128,128,128);
  gradient.addColorStop(0,'#efffff');gradient.addColorStop(.42,'#a5e7ed');gradient.addColorStop(.72,'#3b9fae');gradient.addColorStop(1,'#073340');
  phosphorContext.fillStyle=gradient;phosphorContext.fillRect(0,0,256,256);
  const phosphorMap=new THREE.CanvasTexture(phosphor);phosphorMap.colorSpace=THREE.SRGBColorSpace;textures.add(phosphorMap);
  materials.aperture=new THREE.MeshStandardMaterial({color:0xb3d8dc,roughness:.48,metalness:0,emissive:0xffffff,emissiveMap:phosphorMap,emissiveIntensity:3});
  materials.lightWell=new THREE.MeshStandardMaterial({color:0x091418,roughness:.42,metalness:.3,emissive:0x176879,emissiveMap:phosphorMap,emissiveIntensity:.65});
  mesh(optic,new THREE.CylinderGeometry(.89,.89,.026,96),materials.lightWell,0,-.12,0,false);
  ring(optic,.29,.35,.028,-.09,materials.bore);
  ring(optic,.79,.85,.018,-.077,materials.nickel);
  const lightSegments=radial(optic,arcGeometry(.81,.835,.045,TAU/12-.045,.010,.002),materials.lightGuide,12,0,-.063);
  lightSegments.castShadow=lightSegments.receiveShadow=false;
  const glass=mesh(hood,new THREE.SphereGeometry(2.42,96,36,0,TAU,0,.384),materials.glass,0,-2.228);
  glass.castShadow=false;glass.receiveShadow=false;
  radial(hood,new THREE.CylinderGeometry(.018,.018,.009,6),materials.bore,6,1.091,.19);

  const hoodLocks=[];
  const bank=new THREE.Group();
  mesh(bank,milledSlab(.235,.205,.026,.019,.003),materials.nickel);
  mesh(bank,new THREE.BoxGeometry(.174,.024,.14),materials.ceramic,0,.021);
  const bobbin=mesh(bank,new THREE.CylinderGeometry(.043,.043,.14,20),materials.ceramic,0,.046);bobbin.rotation.z=Math.PI/2;
  for(let i=0;i<10;i++){
    const winding=mesh(bank,new THREE.TorusGeometry(.047,.0055,6,24),materials.copper,-.064+i*.014,.046);winding.rotation.y=Math.PI/2;
  }
  for(const sign of [-1,1]){
    const cap=mesh(bank,new THREE.CylinderGeometry(.052,.052,.012,20),materials.bore,sign*.081,.046);cap.rotation.z=Math.PI/2;
    mesh(bank,new THREE.BoxGeometry(.19,.012,.016),materials.edge,0,.033,sign*.085);
    for(const x of [-.095,.095])mesh(bank,new THREE.CylinderGeometry(.011,.011,.013,6),materials.edge,x,.026,sign*.078);
  }
  for(let i=0;i<6;i++)mesh(bank,new THREE.BoxGeometry(.009,.052,.033),materials.body,-.075+i*.03,.025,-.083);
  mesh(bank,new THREE.BoxGeometry(.055,.036,.027),materials.bore,.061,.059,.071);
  mesh(bank,new THREE.BoxGeometry(.038,.006,.008),materials.lightGuide,.061,.080,.074,false);
  for(let i=0;i<3;i++)mesh(bank,new THREE.BoxGeometry(.006,.012,.019),materials.nickel,.044+i*.014,.041,.087);
  const banks=repeatAssembly(bank,6,optic);
  const guideTemplate=new THREE.Group();
  for(const sign of [-1,1]){
    mesh(guideTemplate,new THREE.BoxGeometry(.36,.018,.025),materials.bore,.55,-.087,sign*.073);
    mesh(guideTemplate,new THREE.BoxGeometry(.30,.008,.007),materials.edge,.55,-.073,sign*.073);
  }
  mesh(guideTemplate,new THREE.BoxGeometry(.105,.010,.025),materials.copper,.33,-.08);
  // Pocketed interstitial bridges carry control packages and copper contacts.
  // These sit between the guided cartridges, below their moving envelope.
  const support=new THREE.Group();support.rotation.y=-Math.PI/6;guideTemplate.add(support);
  const bridge=arcPath(new THREE.Shape(),.37,.79,-.19,.19);
  bridge.holes.push(arcPath(new THREE.Path(),.47,.69,.105,-.105,true));
  mesh(support,extrude(bridge,.024,.003,16),materials.nickel,0,-.063);
  mesh(support,milledSlab(.13,.12,.028,.008,.002),materials.ceramic,.745,-.031);
  for(const sign of [-1,1]){
    for(let i=0;i<5;i++)mesh(support,new THREE.BoxGeometry(.011,.008,.039),materials.copper,.696+i*.024,-.033,sign*.067);
    mesh(support,new THREE.BoxGeometry(.31,.009,.008),materials.copper,.57,-.045,sign*.105);
    mesh(support,new THREE.CylinderGeometry(.015,.015,.014,6),materials.edge,.414,-.041,sign*.051);
  }
  const staticGuides=repeatAssembly(guideTemplate,6,optic),guideDummy=new THREE.Object3D();
  for(let i=0;i<6;i++){
    guideDummy.rotation.y=-i/6*TAU;guideDummy.updateMatrix();
    staticGuides.forEach(batch=>batch.setMatrixAt(i,guideDummy.matrix));
  }
  staticGuides.forEach(batch=>{batch.instanceMatrix.needsUpdate=true;batch.computeBoundingSphere();});
  const rodPositions=Array.from({length:12},()=>({position:[0,0,0]}));
  const internalRods=instance(optic,new THREE.CylinderGeometry(.008,.008,1,12),materials.edge,rodPositions);
  const internalCollars=instance(optic,new THREE.CylinderGeometry(.016,.016,.025,12),materials.bore,rodPositions);
  const hoodRodPositions=Array.from({length:3},(_,i)=>{const a=i/3*TAU+.3;return {position:[Math.cos(a)*1.045,0,Math.sin(a)*1.045]};});
  const hoodRods=instance(optic,new THREE.CylinderGeometry(.013,.013,1,16),materials.edge,hoodRodPositions);
  instance(optic,new THREE.CylinderGeometry(.037,.037,.11,20),materials.bore,hoodRodPositions.map(t=>({position:[t.position[0],.017,t.position[2]]})));
  instance(hood,new THREE.CylinderGeometry(.029,.029,.10,20),materials.bore,hoodRodPositions.map(t=>({position:[t.position[0],.098,t.position[2]]})));
  radial(optic,new THREE.BoxGeometry(.25,.014,.038),materials.bore,3,1.06,.102,.3);
  for(let i=0;i<3;i++){
    const a=i/3*TAU+.3;
    const latch=new THREE.Group();latch.position.set(Math.cos(a)*.985,.122,Math.sin(a)*.985);latch.rotation.y=-a;optic.add(latch);
    mesh(latch,new THREE.BoxGeometry(.075,.025,.056),materials.body,.016,0);
    mesh(latch,new THREE.CylinderGeometry(.016,.016,.035,6),materials.edge,-.019,.009);
    hoodLocks.push(latch);
  }
  const tower=new THREE.Group();optic.add(tower);
  ring(tower,.13,.265,.038,0,materials.bore);
  ring(tower,.173,.208,.030,.032,materials.edge);
  const aperture=mesh(tower,new THREE.CylinderGeometry(.166,.166,.042,48),materials.aperture,0,.042,0,false);aperture.receiveShadow=false;
  ring(tower,.153,.178,.018,.070,materials.face);
  torus(tower,.18,.004,.053,materials.lightGuide);
  radial(tower,new THREE.BoxGeometry(.055,.055,.024),materials.nickel,6,.238,.025);
  const petals=[],petalGeometry=keep(arcGeometry(.075,.27,-Math.PI/6+.04,Math.PI/6-.04,.007,.001));petalGeometry.translate(-.29,0,0);
  for(let i=0;i<6;i++){
    const pivot=new THREE.Group();pivot.rotation.y=-i/6*TAU;tower.add(pivot);
    const hinge=new THREE.Group();hinge.position.set(.29,.081+i*.003,0);pivot.add(hinge);
    mesh(hinge,petalGeometry,materials.bore);
    petals.push(hinge);
  }
  radial(tower,new THREE.CylinderGeometry(.012,.012,.042,16),materials.edge,6,.29,.081);
  const towerPosts=radial(optic,new THREE.CylinderGeometry(.010,.010,1,12),materials.edge,3,.22,0);
  const cellDummy=new THREE.Object3D();

  const iris=new THREE.Group();cartridge.add(iris);iris.position.y=.24;
  const blades=[],bladeGeometry=keep(arcGeometry(.38,1.08,-.31,.33,.012,.004));
  for(let i=0;i<10;i++){
    const pivot=new THREE.Group();pivot.rotation.y=i/10*TAU;iris.add(pivot);
    const blade=mesh(pivot,bladeGeometry,[materials.bore,materials.body]);blades.push(blade);
  }
  const lowerAperture=ring(cartridge,.40,.49,.018,.215,materials.lightGuide);lowerAperture.castShadow=lowerAperture.receiveShadow=false;
  const guidePosts=[];
  for(let i=0;i<4;i++){
    const a=Math.PI/4+i/4*TAU,r=2.16,x=Math.cos(a)*r,z=Math.sin(a)*r;
    mesh(chassis,new THREE.CylinderGeometry(.103,.103,.21,32),materials.bore,x,.24,z);
    const rod=mesh(root,new THREE.CylinderGeometry(.045,.045,1,20),materials.edge,x,0,z);
    const sleeve=mesh(root,new THREE.CylinderGeometry(.077,.077,.14,28),materials.body,x,0,z);
    const collar=mesh(root,ringGeometry(.047,.083,.035,.006),materials.copper,x,0,z);
    guidePosts.push({rod,sleeve,collar,x,z});
  }
  // Sparse harnesses and connectors give the hidden side a mechanical purpose.
  for(const sign of [-1,1]){
    const curve=new THREE.CatmullRomCurve3([new THREE.Vector3(sign*1.10,-.90,-1.25),new THREE.Vector3(sign*1.50,-.77,-1.55),new THREE.Vector3(sign*1.74,-.38,-1.47),new THREE.Vector3(sign*1.81,-.34,-1.30)]);
    mesh(root,new THREE.TubeGeometry(curve,40,.051,10,false),materials.rubber);
    const connector=mesh(chassis,new THREE.CylinderGeometry(.094,.094,.20,32),materials.bore,sign*1.83,.39,-1.56);connector.rotation.x=Math.PI/2;
    mesh(chassis,new THREE.BoxGeometry(.22,.07,.19),materials.body,sign*1.80,.32,-1.66);
  }

  const rotor=new THREE.Group();chassis.add(rotor);rotor.position.y=.40;
  const turbineBlade=keep(arcGeometry(.85,1.41,-.055,.10,.014,.003));
  radial(rotor,turbineBlade,materials.bore,32,0,0,.1);
  ring(rotor,.86,.92,.034,.018,materials.copper);
  // A low-power diffuser faces the carriage below the optic. Point sources
  // almost touching the polished rim produced sharp, moving white hotspots.
  const diffuserLight=new THREE.RectAreaLight(0x8dd5e2,1.8,1.25,1.25);
  diffuserLight.position.y=-.185;diffuserLight.rotation.x=-Math.PI/2;optic.add(diffuserLight);

  // An actual decal on one panel, rather than surrounding HUD decoration.
  const label=document.createElement('canvas');label.width=1024;label.height=256;
  const context=label.getContext('2d');context.fillStyle='#889499';context.font='42px monospace';
  context.fillText('S / 01',45,83);context.font='21px monospace';context.fillStyle='#586971';context.fillText('OPTICAL ASSEMBLY   ·   SENTINEL',45,133);
  context.fillRect(45,165,290,2);
  const decal=new THREE.CanvasTexture(label);decal.colorSpace=THREE.SRGBColorSpace;decal.anisotropy=8;textures.add(decal);
  materials.decal=new THREE.MeshStandardMaterial({map:decal,transparent:true,depthWrite:false,metalness:.75,roughness:.46,polygonOffset:true,polygonOffsetFactor:-1});
  const plate=mesh(covers[0].panel,new THREE.PlaneGeometry(.60,.15),materials.decal,1.02,.073,2.04,false);plate.rotation.set(-Math.PI/2,0,.45);

  let travel={position:0,velocity:0};
  function update(time,delta,view,pointer,snap=false,power=1){
    const target=view==='exploded'||view==='detail'?1:view==='assembled'?0:cycleProgress(time);
    travel=snap?{position:target,velocity:0}:advanceTravel(travel,target,delta);
    const pose=mechanicalPose(travel.position);
    const inside=interiorPose(travel.position);
    const bridges=bridgePose(travel.position);
    root.rotation.set(.08+pointer.y*.022,-.38+pointer.x*.045,-.16);
    cover.position.y=-.62;
    cartridge.position.y=-.63+pose.carrier*.47;
    optic.position.y=-.31+pose.lift*.19+pose.optic*1.10;
    hood.position.y=inside.hood*.45;
    hoodRodPositions.forEach(({position},i)=>{const bottom=.023,top=.14+hood.position.y;cellDummy.position.set(position[0],(bottom+top)/2,position[2]);cellDummy.rotation.set(0,0,0);cellDummy.scale.set(1,top-bottom,1);cellDummy.updateMatrix();hoodRods.setMatrixAt(i,cellDummy.matrix);});
    hoodRods.instanceMatrix.needsUpdate=true;hoodRods.computeBoundingSphere();
    hoodLocks.forEach(lock=>{lock.children[0].position.x=.016+inside.latch*.18;});
    inside.cells.forEach((cell,i)=>{
      const a=i/6*TAU,r=.49+cell.slide*.16;
      cellDummy.position.set(Math.cos(a)*r,-.045+cell.lift*.23,Math.sin(a)*r);cellDummy.rotation.set(0,-a,0);cellDummy.scale.set(1,1,1);cellDummy.updateMatrix();
      banks.forEach(batch=>batch.setMatrixAt(i,cellDummy.matrix));
      for(let side=0;side<2;side++){
        const z=(side?1:-1)*.073,top=-.057+cell.lift*.23,bottom=-.081;
        cellDummy.position.set(Math.cos(a)*r-Math.sin(a)*z,(top+bottom)/2,Math.sin(a)*r+Math.cos(a)*z);
        cellDummy.scale.set(1,top-bottom,1);cellDummy.updateMatrix();internalRods.setMatrixAt(i*2+side,cellDummy.matrix);
        cellDummy.position.y=-.061;cellDummy.scale.set(1,1,1);cellDummy.updateMatrix();internalCollars.setMatrixAt(i*2+side,cellDummy.matrix);
      }
    });
    banks.forEach(batch=>{batch.instanceMatrix.needsUpdate=true;batch.computeBoundingSphere();});
    for(const batch of [internalRods,internalCollars]){batch.instanceMatrix.needsUpdate=true;batch.computeBoundingSphere();}
    tower.position.y=-.055+inside.emitter*.20;
    // Thin overlapping shutters swivel on vertical pivots, exposing the source
    // without forming an opaque cone or crossing the coil cartridges.
    petals.forEach(petal=>{petal.rotation.y=inside.shutter*1.62;});
    for(let i=0;i<3;i++){
      const a=i/3*TAU,bottom=-.096,top=tower.position.y;
      cellDummy.position.set(Math.cos(a)*.22,(bottom+top)/2,Math.sin(a)*.22);cellDummy.rotation.set(0,0,0);cellDummy.scale.set(1,top-bottom,1);cellDummy.updateMatrix();towerPosts.setMatrixAt(i,cellDummy.matrix);
    }
    towerPosts.instanceMatrix.needsUpdate=true;towerPosts.computeBoundingSphere();
    covers.forEach(({panel,latch},i)=>{
      panel.position.y=bridges[i].lift*1.20;
      latch.position.y=-.19+bridges[i].unlock*.10;
    });
    blades.forEach((blade,i)=>{blade.position.x=pose.iris*.09;blade.rotation.y=-pose.iris*.16;blade.position.y=i*.002;});
    guidePosts.forEach(({rod,sleeve,collar},i)=>{
      const bottom=-.96,top=cover.position.y+covers[(4-i)%4].panel.position.y-.18,length=top-bottom;
      rod.position.y=(bottom+top)/2;rod.scale.y=Math.max(.08,length);
      sleeve.position.y=bottom+.055;collar.position.y=bottom+.13;
    });
    rotor.rotation.y=pose.carrier*.18;
    gears.forEach((gear,i)=>{gear.rotation.y=(i%2?1:-1)*(bridges[i].lift*.8+pose.carrier*.2);});
    // Illumination rises slowly as the optical cartridge clears the housing.
    // No flashing, scanning HUD or independent decorative rings.
    const breath=1+Math.sin(time*TAU/12)*.035;
    materials.emitter.emissiveIntensity=(2.1+pose.optic*.6)*power;
    materials.aperture.emissiveIntensity=(2.8+pose.optic*.9)*power*breath;
    materials.lightGuide.emissiveIntensity=(1.15+pose.optic*.75)*power*breath;
    materials.lightWell.emissiveIntensity=(.42+pose.optic*.22)*power;
    materials.seam.emissiveIntensity=(.65+pose.carrier*.3)*power;
    diffuserLight.intensity=(.8+pose.optic*1.0)*power*breath;
    return pose.presentation;
  }
  function setState(state){
    const color={incident:0xe68b79,attention:0xd2b480,offline:0x526774,healthy:0x93c9bd,standby:0x85cbe7}[state]||0x85cbe7;
    materials.status.color.setHex(color);materials.status.emissive.setHex(color);
  }
  const focusPosition=new THREE.Vector3();
  function focusPoint(){
    optic.updateWorldMatrix(true,false);
    return focusPosition.set(0,.24,0).applyMatrix4(optic.matrixWorld);
  }
  function dispose(){resources.forEach(g=>g.dispose());Object.values(materials).forEach(m=>m.dispose());textures.forEach(t=>t.dispose());root.traverse(o=>{if(o.isInstancedMesh)o.dispose();});}
  return {root,update,setState,focusPoint,dispose};
}


function createFinish(renderer) {
  const type=renderer.extensions.has("EXT_color_buffer_float") ? THREE.HalfFloatType : THREE.UnsignedByteType;
  const target=new THREE.WebGLRenderTarget(1,1,{type});
  target.depthTexture=new THREE.DepthTexture(1,1,THREE.UnsignedIntType);
  const bloomA=new THREE.WebGLRenderTarget(1,1,{type,depthBuffer:false});
  const bloomB=bloomA.clone();
  const vertexShader=`varying vec2 vUv; void main(){vUv=uv;gl_Position=vec4(position.xy,0.0,1.0);}`;
  const bright=new THREE.ShaderMaterial({depthTest:false,depthWrite:false,toneMapped:false,
    uniforms:{tColor:{value:target.texture},uSourcePixel:{value:new THREE.Vector2()},uThreshold:{value:type===THREE.HalfFloatType ? 2.0 : .9}},vertexShader,
    fragmentShader:`varying vec2 vUv;uniform sampler2D tColor;uniform vec2 uSourcePixel;uniform float uThreshold;
      vec3 highlight(vec2 uv){
        vec3 c=texture2D(tColor,uv).rgb;
        float luminance=dot(c,vec3(.2126,.7152,.0722));
        float knee=clamp(luminance-uThreshold+.3,0.0,.6);knee=knee*knee/1.2;
        float excess=max(luminance-uThreshold,knee);
        // Bound only the bloom energy; retain the original sharp reflection.
        // A tiny specular spike must not become a large white disk.
        float energy=excess/(1.0+excess*.55);
        return c*energy/max(luminance,.0001);
      }
      void main(){
        vec2 d=uSourcePixel*.5;
        vec3 c=highlight(vUv+vec2(-d.x,-d.y))+highlight(vUv+vec2(d.x,-d.y))
          +highlight(vUv+vec2(-d.x,d.y))+highlight(vUv+d);
        gl_FragColor=vec4(c*.25,1.0);
      }`});
  const blur=new THREE.ShaderMaterial({depthTest:false,depthWrite:false,toneMapped:false,
    uniforms:{tColor:{value:bloomA.texture},uStep:{value:new THREE.Vector2()}},vertexShader,
    fragmentShader:`varying vec2 vUv;uniform sampler2D tColor;uniform vec2 uStep;
      void main(){vec3 c=texture2D(tColor,vUv).rgb*.227027;
      c+=(texture2D(tColor,vUv+uStep*1.384615).rgb+texture2D(tColor,vUv-uStep*1.384615).rgb)*.316216;
      c+=(texture2D(tColor,vUv+uStep*3.230769).rgb+texture2D(tColor,vUv-uStep*3.230769).rgb)*.070270;
      gl_FragColor=vec4(c,1.0);}`});
  const uniforms={tColor:{value:target.texture},tDepth:{value:target.depthTexture},tBloom:{value:bloomA.texture},
    uPixel:{value:new THREE.Vector2(1,1)},uProjectionInverse:{value:new THREE.Matrix4()},
    uFocus:{value:10},uNear:{value:.1},uFar:{value:80},uQuality:{value:1},uTime:{value:0}};
  const material=new THREE.ShaderMaterial({uniforms,depthTest:false,depthWrite:false,vertexShader,
    fragmentShader:`
      varying vec2 vUv;
      uniform sampler2D tColor,tDepth,tBloom;
      uniform vec2 uPixel;
      uniform mat4 uProjectionInverse;
      uniform float uFocus,uNear,uFar,uQuality,uTime;
      float noise(vec2 p){return fract(sin(dot(p,vec2(12.9898,78.233)))*43758.5453);}
      vec3 viewPosition(vec2 uv){
        vec4 p=uProjectionInverse*vec4(uv*2.0-1.0,texture2D(tDepth,uv).x*2.0-1.0,1.0);
        return p.xyz/p.w;
      }
      float contactShade(vec3 position){
        vec3 n=normalize(cross(dFdx(position),dFdy(position)));
        float shade=0.0;
        for(int i=0;i<12;i++){
          if(uQuality<.5 && i>=6)break;
          float a=float(i)*2.399963,radius=3.0+float(i)*1.1;
          vec3 delta=viewPosition(vUv+vec2(cos(a),sin(a))*uPixel*radius)-position;
          float distance=length(delta);
          shade+=max(dot(n,delta/max(distance,.0001))-.12,0.0)*(1.0-smoothstep(.06,.48,distance));
        }
        return clamp(1.0-shade*(uQuality<.5?.23:.115),.62,1.0);
      }
      void main(){
        vec3 color=texture2D(tColor,vUv).rgb;
        float z=texture2D(tDepth,vUv).x;
        float depth=uNear*uFar/(uFar-(uFar-uNear)*z);
        vec3 position=viewPosition(vUv);
        float occlusion=contactShade(position);
        color*=occlusion;
        float blur=min(max(depth-uFocus-1.8,0.0)*.48+abs(min(depth-uFocus,0.0))*.12,3.0)*uQuality;
        if(blur>.18){
          vec3 b=vec3(0.0);
          for(int i=0;i<6;i++){
            float a=float(i)*1.047198;
            b+=texture2D(tColor,vUv+vec2(cos(a),sin(a))*uPixel*blur).rgb;
          }
          color=mix(color,b/6.0*occlusion,.55);
        }
        // Clear studio air: light is visible on surfaces. Keep just a narrow
        // optical fringe at very bright edges, without an added light cone.
        color+=texture2D(tBloom,vUv).rgb*.12;
        vec2 p=vUv-.5;
        color*=1.0-.24*dot(p,p);
        gl_FragColor=vec4(color,1.0);
        #include <tonemapping_fragment>
        #include <colorspace_fragment>
        gl_FragColor.rgb+=(noise(vUv/uPixel+floor(uTime*12.0))-.5)*.002;
      }`});
  const geometry=new THREE.PlaneGeometry(2,2),scene=new THREE.Scene(),camera=new THREE.Camera();
  const quad=new THREE.Mesh(geometry,material);scene.add(quad);
  let bw=1,bh=1;
  const focusInView=new THREE.Vector3();
  function pass(output,shader){quad.material=shader;renderer.setRenderTarget(output);renderer.render(scene,camera);}
  return {
    resize(width,height,mobile){
      const samples=Math.min(mobile ? 2 : 4,renderer.capabilities.maxSamples);
      if(target.samples!==samples){target.samples=samples;target.dispose();}
      target.setSize(width,height);uniforms.uPixel.value.set(1/width,1/height);uniforms.uQuality.value=mobile ? 0 : 1;
      bright.uniforms.uSourcePixel.value.set(1/width,1/height);
      bw=Math.max(1,Math.round(width/2));bh=Math.max(1,Math.round(height/2));bloomA.setSize(bw,bh);bloomB.setSize(bw,bh);
    },
    render(source,sourceCamera,time,focus){
      renderer.setRenderTarget(target);renderer.render(source,sourceCamera);
      uniforms.uProjectionInverse.value.copy(sourceCamera.projectionMatrixInverse);
      focusInView.copy(focus).applyMatrix4(sourceCamera.matrixWorldInverse);
      uniforms.uFocus.value=-focusInView.z;
      uniforms.uTime.value=time;
      pass(bloomA,bright);
      // Bilinear Gaussian offsets assume adjacent texels; widening the step
      // skips texels and turns a small highlight into a visible grid of dots.
      blur.uniforms.tColor.value=bloomA.texture;blur.uniforms.uStep.value.set(1/bw,0);pass(bloomB,blur);
      blur.uniforms.tColor.value=bloomB.texture;blur.uniforms.uStep.value.set(0,1/bh);pass(bloomA,blur);
      pass(null,material);
    },
    dispose(){target.depthTexture.dispose();target.dispose();bloomA.dispose();bloomB.dispose();bright.dispose();blur.dispose();material.dispose();geometry.dispose();}
  };
}

let areaLightsReady=false;


export async function createMechanismScene(element, {still=false,environmentData,onProgress=()=>{},signal} = {}) {
  const canvas = document.createElement("canvas");
  canvas.className = "mechanism-canvas";
  canvas.setAttribute("aria-hidden", "true");
  let renderer, model, studio, grain, environment, resizeObserver, finish, lighting;
  const hero=element.dataset.sentinelCore==="access";
  let frame=0, running=false, failed=false, destroyed=false, ready=false, elapsed=still ? 10 : hero ? 0 : 4.8, last=0, lastPaint=0, frames=0;
  let ignition=still||!hero?4:0;
  let view="auto", width=1, height=1, narrow=false, displayedState;
  let framing={position:0,velocity:0};
  const pointer = {x:0,y:0}, targetPointer = {x:0,y:0};
  const scene = new THREE.Scene();
  scene.background=new THREE.Color(0x090c10);
  scene.fog=new THREE.FogExp2(0x090c10,.024);
  const camera = new THREE.PerspectiveCamera(36,1,.1,80);
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
    studio?.dispose();
    grain?.dispose();
    environment?.dispose();
    finish?.dispose();
    renderer?.dispose();
    canvas.remove();
  }
  function useFallback() {
    failed=true;
    element.dataset.renderer="fallback";
    element.dataset.boot="fallback";
    onProgress('fallback');
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
    const cue=ignitionPose(ignition);
    lighting.softbox.intensity=8.5*(.12+.88*cue.key);
    lighting.strip.intensity=16*(.08+.92*cue.rim);
    lighting.bounce.intensity=6.5*(.1+.9*cue.key);
    lighting.key.intensity=1.35*(.15+.85*cue.key);
    lighting.strip.position.set(-2+cue.travel*6,4-cue.travel*2,-1-cue.travel*2);
    lighting.strip.lookAt(0,0,0);
    scene.environmentIntensity=.52*(.12+.88*cue.key);
    studio.setIllumination(cue.rim);
    const expansion=model.update(elapsed,delta,view,pointer,snap,cue.optic);
    const targetFrame=view==='detail'?1:0;
    framing=snap?{position:targetFrame,velocity:0}:advanceTravel(framing,targetFrame,delta);
    const close=framing.position;
    if(hero && !narrow){
      const dolly=.94+.06*cue.travel;
      camera.position.set(3.9-close*1.45+Math.sin(elapsed*.055)*.04+pointer.x*.12,(4.5+expansion*.22+close*1.10)*dolly,(10.6+expansion*.32-close*4.8)*dolly);
      camera.lookAt(1.55-close*.52,-.28+expansion*.15+close*.98,0);
    }else{
      const base=hero?Math.max(12.6,10.2/camera.aspect):Math.max(12.9,11.4/camera.aspect),distance=base*(1-close*.31);
      camera.position.set(distance*(hero?.29:.34),distance*(hero?.48:.46),distance*(hero?.82:.81));
      camera.lookAt(hero?-.20:0,(hero?.17:-.12)+close*.65,0);
    }
    renderer.info.reset();
    finish.render(scene,camera,elapsed,model.focusPoint());
    if(failed){useFallback();return;}
    frames++;
    // DOM diagnostics allow smoke tests to verify that the GPU surface advances.
    if (frames%20===1 || !running) {
      canvas.dataset.frames=String(frames);
      canvas.dataset.drawCalls=String(renderer.info.render.calls);
      canvas.dataset.triangles=String(renderer.info.render.triangles);
      canvas.dataset.ignition=ignition<3.8?'lighting':'complete';
      canvas.dataset.view=view;
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
    ignition=Math.min(4,ignition+delta);
    draw(delta);
  }
  function setRunning(value) {
    if (destroyed || failed) return;
    // Reduced motion, a saved pause or a pause during loading gets a complete
    // static composition rather than a dim, half-started light cue.
    if(!value && ignition<4){ignition=4;draw();}
    if(running===value)return;
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
    signal?.throwIfAborted();
    renderer=new THREE.WebGLRenderer({canvas,antialias:true,alpha:true,powerPreference:"high-performance"});
    renderer.debug.onShaderError=()=>{failed=true;};
    renderer.setClearColor(0x000000,0);
    renderer.outputColorSpace=THREE.SRGBColorSpace;
    renderer.toneMapping=THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure=.92;
    renderer.info.autoReset=false;
    renderer.shadowMap.enabled=true;
    renderer.shadowMap.type=THREE.PCFShadowMap;
    if(!areaLightsReady){RectAreaLightUniformsLib.init();areaLightsReady=true;}
    scene.add(new THREE.HemisphereLight(0xb7c9db,0x030609,.12));
    // Broad softboxes give the machined faces a continuous reflected highlight.
    const softbox=new THREE.RectAreaLight(0xf5f1e8,8.5,5,6);
    softbox.position.set(-3,6,4);softbox.lookAt(0,0,0);scene.add(softbox);
    const strip=new THREE.RectAreaLight(0xc9e1ec,16,.7,5);
    strip.position.set(4,2,-3);strip.lookAt(0,0,0);scene.add(strip);
    const bounce=new THREE.RectAreaLight(0xf6d6a3,6.5,3,1);
    bounce.position.set(-4,0,-2);bounce.lookAt(0,-.3,0);scene.add(bounce);
    // Rectangular lights do not cast shadows, so one neutral key supplies depth.
    const key=new THREE.DirectionalLight(0xf4f0e9,1.4);
    key.position.set(-4,8,6);key.castShadow=true;
    key.shadow.mapSize.set(2048,2048);
    Object.assign(key.shadow.camera,{left:-4.5,right:4.5,top:4.5,bottom:-4.5,near:1,far:24});
    key.shadow.normalBias=.012;key.shadow.bias=-.00010;key.shadow.radius=3;
    scene.add(key);
    lighting={softbox,strip,bounce,key};
    onProgress('geometry');
    await new Promise(resolve=>setTimeout(resolve,0));signal?.throwIfAborted();
    grain=surfaceTexture();
    model=makeModel(grain); scene.add(model.root);
    await new Promise(resolve=>setTimeout(resolve,0));signal?.throwIfAborted();
    studio=makeStudio(grain);scene.add(studio.root);
    onProgress('environment');
    environment=await photographicEnvironment(renderer,element,environmentData);signal?.throwIfAborted();
    scene.environment=environment.texture;
    scene.environmentIntensity=.52;
    scene.environmentRotation.set(0,1.35,0);
    finish=createFinish(renderer);
    element.append(canvas);
    resize();
    model.update(elapsed,0,view,pointer,true);
    onProgress('compile');
    await renderer.compileAsync(scene,camera);signal?.throwIfAborted();
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
      view=["auto","exploded","assembled","detail"].includes(mode) ? mode : "auto";
      element.dataset.view=view;
      if(view!=="auto")ignition=4;
      if (!running) draw(0,true);
    },dispose};
  } catch(error) {
    element.dataset.renderer="fallback";
    dispose();
    throw error;
  }
}
