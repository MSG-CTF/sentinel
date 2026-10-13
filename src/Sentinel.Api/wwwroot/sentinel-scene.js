import * as THREE from "./vendor/three/three.module.js";
import { HDRLoader } from "./vendor/three/HDRLoader.js";
import { RectAreaLightUniformsLib } from "./vendor/three/RectAreaLightUniformsLib.js";

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
        return vec3((fine * .12 + cut * .10 + grain * .25 - score * .18) * .00016,
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
  material.customProgramCacheKey = () => 'sentinel-machining-v2';
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
  const root=new THREE.Group(), resources=new Set(), textures=new Set();
  const grain=surfaceTexture();textures.add(grain);
  const materials={
    body:machinedMaterial({color:0x48515b,metalness:1,roughness:.32,anisotropy:.22,envMapIntensity:.88},grain,{style:1,strength:.38}),
    face:machinedMaterial({color:0x63707b,metalness:1,roughness:.28,anisotropy:.32,envMapIntensity:.95},grain,{style:1,strength:.48}),
    edge:machinedMaterial({color:0xa2adb5,metalness:1,roughness:.20,envMapIntensity:1.1},grain,{strength:.18}),
    bore:machinedMaterial({color:0x252e36,metalness:1,roughness:.40,anisotropy:.18},grain,{strength:.30}),
    ceramic:new THREE.MeshPhysicalMaterial({color:0x080c10,metalness:.25,roughness:.27,clearcoat:.24,clearcoatRoughness:.18}),
    rubber:new THREE.MeshStandardMaterial({color:0x080b0e,roughness:.76,metalness:0}),
    copper:machinedMaterial({color:0x655548,metalness:1,roughness:.29},grain,{strength:.4}),
    glass:new THREE.MeshPhysicalMaterial({color:0x0b2431,metalness:.05,roughness:.12,envMapIntensity:.32,clearcoat:1,clearcoatRoughness:.09,transmission:.25,thickness:.22,ior:1.52}),
    emitter:new THREE.MeshStandardMaterial({color:0xcceeff,emissive:0x7bd7ff,emissiveIntensity:6.5,roughness:.22,metalness:.05}),
    seam:new THREE.MeshStandardMaterial({color:0xc8edff,emissive:0x58bce9,emissiveIntensity:3.0,roughness:.28,metalness:.1}),
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
    if(material===materials.emitter||material===materials.seam){object.castShadow=false;object.receiveShadow=false;}
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
  function extrude(shape,depth,bevel=.025){
    const geometry=new THREE.ExtrudeGeometry(shape,{depth,bevelEnabled:true,bevelThickness:bevel,bevelSize:bevel,bevelSegments:4,curveSegments:40,steps:1});
    geometry.rotateX(-Math.PI/2);geometry.translate(0,-depth/2,0);return geometry;
  }
  function frameGeometry(width,depth,height,holeRadius){
    const x=width/2,z=depth/2,c=.40,shape=new THREE.Shape();
    shape.moveTo(-x+c,-z);shape.lineTo(x-c,-z);shape.lineTo(x,-z+c);shape.lineTo(x,z-c);shape.lineTo(x-c,z);
    shape.lineTo(-x+c,z);shape.lineTo(-x,z-c);shape.lineTo(-x,-z+c);shape.closePath();
    const hole=new THREE.Path();hole.absarc(0,0,holeRadius,0,TAU,true);shape.holes.push(hole);
    return extrude(shape,height,.028);
  }
  function quarterGeometry(){
    const shape=new THREE.Shape(),inner=1.53,edge=2.31;
    shape.moveTo(inner,.045);shape.lineTo(edge-.15,.045);shape.lineTo(edge,.20);shape.lineTo(edge,edge-.42);
    shape.lineTo(edge-.42,edge);shape.lineTo(.20,edge);shape.lineTo(.045,edge-.15);shape.lineTo(.045,inner);
    shape.absarc(0,0,inner,Math.PI/2-.03,.03,true);shape.closePath();return extrude(shape,.27,.034);
  }

  // A serviceable optical module: a solid rear housing, four guide columns,
  // one lens cartridge and a split access cover. The case carries the weight.
  const chassis=new THREE.Group();root.add(chassis);chassis.position.y=-1.08;
  mesh(chassis,frameGeometry(4.82,4.82,.55,1.15),[materials.body,materials.bore]);
  mesh(chassis,frameGeometry(4.91,4.91,.08,1.15),[materials.bore,materials.body],0,-.31);
  mesh(chassis,frameGeometry(4.77,4.77,.055,1.48),materials.rubber,0,.295);
  ring(chassis,1.18,1.44,.39,.04,materials.bore);
  ring(chassis,1.12,1.23,.07,.27,materials.edge);
  const coolingFins=[];
  for(let side=0;side<4;side++) for(let i=0;i<18;i++) {
    const a=side/4*TAU,along=(i-8.5)*.125;
    coolingFins.push({position:[Math.cos(a)*2.425+Math.sin(a)*along,-.045,Math.sin(a)*2.425-Math.cos(a)*along],rotation:[0,-a,0]});
  }
  instance(chassis,new THREE.BoxGeometry(.018,.31,.034),materials.bore,coolingFins);

  const cover=new THREE.Group();root.add(cover);
  const covers=[],quarter=keep(quarterGeometry());
  for(let i=0;i<4;i++){
    const pivot=new THREE.Group();cover.add(pivot);pivot.rotation.y=i/4*TAU;
    const panel=new THREE.Group();pivot.add(panel);
    mesh(panel,quarter,[materials.face,materials.body]);
    const channel=mesh(panel,new THREE.BoxGeometry(.83,.009,.011),materials.bore,1.70,.176,1.89);channel.rotation.y=.0;
    mesh(panel,new THREE.BoxGeometry(.29,.008,.010),i===0?materials.status:materials.bore,1.91,.180,1.89);
    // Countersunk steel hardware stays small relative to the thick billet.
    const bolts=[[1.91,1.61],[1.61,1.91]];
    instance(panel,new THREE.CylinderGeometry(.053,.069,.034,28),materials.bore,bolts.map(([x,z])=>({position:[x,.172,z]})));
    instance(panel,new THREE.CylinderGeometry(.036,.045,.023,28),materials.edge,bolts.map(([x,z])=>({position:[x,.194,z]})));
    instance(panel,new THREE.CylinderGeometry(.016,.016,.003,6),materials.ceramic,bolts.map(([x,z])=>({position:[x,.207,z]})));
    const latch=mesh(panel,new THREE.CylinderGeometry(.036,.036,.16,20),materials.edge,1.68,-.19,1.68);
    covers.push({panel,latch});
  }

  const cartridge=new THREE.Group();root.add(cartridge);
  const barrelProfile=[[1.15,-.40],[1.37,-.40],[1.43,-.35],[1.43,-.14],[1.36,-.10],[1.34,.09],[1.26,.16],[1.19,.16],[1.15,.10],[1.15,-.40]];
  mesh(cartridge,new THREE.LatheGeometry(barrelProfile.map(p=>new THREE.Vector2(...p)),160),materials.bore);
  ring(cartridge,1.33,1.42,.035,-.10,materials.edge);
  ring(cartridge,1.25,1.35,.035,.125,materials.body);
  radial(cartridge,new THREE.BoxGeometry(.038,.17,.025),materials.body,64,1.403,-.245);
  torus(cartridge,1.265,.009,.16,materials.seam);
  radial(cartridge,new THREE.BoxGeometry(.018,.02,.055),materials.ceramic,48,1.31,.149);

  const optic=new THREE.Group();root.add(optic);
  ring(optic,.89,1.14,.35,-.035,materials.body);
  ring(optic,.91,1.08,.055,.19,materials.bore);
  ring(optic,.93,.968,.029,.216,materials.edge);
  torus(optic,.918,.012,.219,materials.emitter);
  mesh(optic,new THREE.CylinderGeometry(.90,.90,.026,96),materials.ceramic,0,-.12);
  ring(optic,.32,.72,.028,-.08,materials.bore);
  torus(optic,.48,.008,-.06,materials.seam);
  mesh(optic,new THREE.CylinderGeometry(.105,.105,.012,48),materials.emitter,0,-.06,0,false);
  const glass=mesh(optic,new THREE.SphereGeometry(2.42,96,36,0,TAU,0,.384),materials.glass,0,-2.185);
  glass.castShadow=false;glass.receiveShadow=false;
  radial(optic,new THREE.CylinderGeometry(.013,.013,.009,12),materials.bore,24,1.055,.20);

  const iris=new THREE.Group();cartridge.add(iris);iris.position.y=.24;
  const blades=[],bladeGeometry=keep(arcGeometry(.38,1.08,-.31,.33,.012,.004));
  for(let i=0;i<10;i++){
    const pivot=new THREE.Group();pivot.rotation.y=i/10*TAU;iris.add(pivot);
    const blade=mesh(pivot,bladeGeometry,[materials.bore,materials.body]);blades.push(blade);
  }
  const guidePosts=[];
  for(let i=0;i<4;i++){
    const a=Math.PI/4+i/4*TAU,r=2.16,x=Math.cos(a)*r,z=Math.sin(a)*r;
    mesh(chassis,new THREE.CylinderGeometry(.103,.103,.21,32),materials.bore,x,.24,z);
    const rod=mesh(root,new THREE.CylinderGeometry(.045,.045,1,20),materials.edge,x,0,z);
    const sleeve=mesh(root,new THREE.CylinderGeometry(.077,.077,.14,28),materials.body,x,0,z);
    const collar=mesh(root,ringGeometry(.047,.083,.035,.006),materials.bore,x,0,z);
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
  const innerLight=new THREE.PointLight(0x81d6ff,6,4.2,2);optic.add(innerLight);innerLight.position.y=-.065;
  const cavityLight=new THREE.PointLight(0x5db9e6,2.5,3.1,2);cartridge.add(cavityLight);cavityLight.position.y=.32;

  // An actual decal on one panel, rather than surrounding HUD decoration.
  const label=document.createElement('canvas');label.width=1024;label.height=256;
  const context=label.getContext('2d');context.fillStyle='#889499';context.font='42px monospace';
  context.fillText('S / 01',45,83);context.font='21px monospace';context.fillStyle='#586971';context.fillText('OPTICAL ASSEMBLY   ·   SENTINEL',45,133);
  context.fillRect(45,165,290,2);
  const decal=new THREE.CanvasTexture(label);decal.colorSpace=THREE.SRGBColorSpace;decal.anisotropy=8;textures.add(decal);
  materials.decal=new THREE.MeshStandardMaterial({map:decal,transparent:true,depthWrite:false,metalness:.75,roughness:.46,polygonOffset:true,polygonOffsetFactor:-1});
  const plate=mesh(covers[0].panel,new THREE.PlaneGeometry(1.20,.30),materials.decal,1.22,.182,1.93,false);plate.rotation.x=-Math.PI/2;

  let travel={position:0,velocity:0};
  let lowPower=false;
  function update(time,delta,view,pointer,snap=false){
    const target=view==='exploded'?1:view==='assembled'?0:cycleProgress(time);
    travel=snap?{position:target,velocity:0}:advanceTravel(travel,target,delta);
    const pose=mechanicalPose(travel.position);
    root.rotation.set(.08+pointer.y*.035,-.38+pointer.x*.075,-.31);
    cover.position.y=-.62+pose.cover*.17+pose.lift*1.03;
    cartridge.position.y=-.63+pose.carrier*.47;
    optic.position.y=-.31+pose.lift*.19+pose.optic*1.10;
    covers.forEach(({panel,latch},i)=>{
      const clearance=ease((travel.position-.10-i*.008)/.30);
      panel.position.set(clearance*.13,0,clearance*.13);
      latch.position.y=-.19+pose.unlock*.10;
    });
    blades.forEach((blade,i)=>{blade.position.x=pose.iris*.09;blade.rotation.y=-pose.iris*.16;blade.position.y=i*.002;});
    guidePosts.forEach(({rod,sleeve,collar})=>{
      const bottom=-.96,top=cover.position.y-.18,length=top-bottom;
      rod.position.y=(bottom+top)/2;rod.scale.y=Math.max(.08,length);
      sleeve.position.y=bottom+.055;collar.position.y=bottom+.13;
    });
    rotor.rotation.y=time*.035;
    // Illumination rises slowly as the optical cartridge clears the housing.
    // No flashing, scanning HUD or independent decorative rings.
    materials.emitter.emissiveIntensity=5.1+pose.optic*2.3;
    materials.seam.emissiveIntensity=1.8+pose.carrier*1.1;
    innerLight.intensity=1.2+pose.optic*.8;
    cavityLight.intensity=.7+pose.lift*.8;
    return pose.presentation;
  }
  function setState(state){
    const color={incident:0xe68b79,attention:0xd2b480,offline:0x526774,healthy:0x93c9bd,standby:0x85cbe7}[state]||0x85cbe7;
    materials.status.color.setHex(color);materials.status.emissive.setHex(color);
  }
  function setQuality(mobile){
    if(lowPower===mobile)return;lowPower=mobile;
    materials.glass.transmission=mobile?0:.25;materials.glass.needsUpdate=true;
  }
  const emitterPosition=new THREE.Vector3(),emitterAxis=new THREE.Vector3();
  function lightFrame(){
    optic.updateWorldMatrix(true,false);
    emitterPosition.set(0,.24,0).applyMatrix4(optic.matrixWorld);
    emitterAxis.set(0,1,0).transformDirection(optic.matrixWorld);
    return {position:emitterPosition,axis:emitterAxis};
  }
  function dispose(){resources.forEach(g=>g.dispose());Object.values(materials).forEach(m=>m.dispose());textures.forEach(t=>t.dispose());root.traverse(o=>{if(o.isInstancedMesh)o.dispose();});}
  return {root,update,setState,setQuality,lightFrame,dispose};
}


function createFinish(renderer) {
  const type=renderer.extensions.has("EXT_color_buffer_float") ? THREE.HalfFloatType : THREE.UnsignedByteType;
  const target=new THREE.WebGLRenderTarget(1,1,{type});
  target.depthTexture=new THREE.DepthTexture(1,1,THREE.UnsignedIntType);
  const bloomA=new THREE.WebGLRenderTarget(1,1,{type,depthBuffer:false});
  const bloomB=bloomA.clone();
  const vertexShader=`varying vec2 vUv; void main(){vUv=uv;gl_Position=vec4(position.xy,0.0,1.0);}`;
  const bright=new THREE.ShaderMaterial({depthTest:false,depthWrite:false,toneMapped:false,
    uniforms:{tColor:{value:target.texture},uThreshold:{value:type===THREE.HalfFloatType ? 1.1 : .78}},vertexShader,
    fragmentShader:`varying vec2 vUv;uniform sampler2D tColor;uniform float uThreshold;
      void main(){vec3 c=texture2D(tColor,vUv).rgb;float b=max(c.r,max(c.g,c.b));
      float knee=clamp(b-uThreshold+.35,0.0,.7);knee=knee*knee/1.4;
      gl_FragColor=vec4(c*max(b-uThreshold,knee)/max(b,.0001),1.0);}`});
  const blur=new THREE.ShaderMaterial({depthTest:false,depthWrite:false,toneMapped:false,
    uniforms:{tColor:{value:bloomA.texture},uStep:{value:new THREE.Vector2()}},vertexShader,
    fragmentShader:`varying vec2 vUv;uniform sampler2D tColor;uniform vec2 uStep;
      void main(){vec3 c=texture2D(tColor,vUv).rgb*.227027;
      c+=(texture2D(tColor,vUv+uStep*1.384615).rgb+texture2D(tColor,vUv-uStep*1.384615).rgb)*.316216;
      c+=(texture2D(tColor,vUv+uStep*3.230769).rgb+texture2D(tColor,vUv-uStep*3.230769).rgb)*.070270;
      gl_FragColor=vec4(c,1.0);}`});
  const uniforms={tColor:{value:target.texture},tDepth:{value:target.depthTexture},tBloom:{value:bloomA.texture},
    uPixel:{value:new THREE.Vector2(1,1)},uProjectionInverse:{value:new THREE.Matrix4()},
    uEmitter:{value:new THREE.Vector3()},uAxis:{value:new THREE.Vector3()},
    uFocus:{value:10},uNear:{value:.1},uFar:{value:80},uQuality:{value:1},uTime:{value:0}};
  const material=new THREE.ShaderMaterial({uniforms,depthTest:false,depthWrite:false,vertexShader,
    fragmentShader:`
      varying vec2 vUv;
      uniform sampler2D tColor,tDepth,tBloom;
      uniform vec2 uPixel;
      uniform vec3 uEmitter,uAxis;
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
      float scatteredLight(vec3 surface){
        // Sample only in front of the depth surface. Housing and glass therefore
        // occlude the small volume originating at the moving optical aperture.
        vec3 ray=normalize(surface);
        float start=max(0.0,length(uEmitter)-3.2),energy=0.0;
        for(int i=0;i<16;i++){
          float distance=start+(float(i)+.5)*.32;
          if(distance>length(surface))break;
          vec3 offset=ray*distance-uEmitter;
          float h=dot(offset,uAxis),radius=.14+max(h,0.0)*.22;
          vec3 radial=offset-uAxis*h;
          energy+=exp(-dot(radial,radial)/(radius*radius))*smoothstep(0.0,.14,h)*(1.0-smoothstep(.2,2.6,h))*.32;
        }
        return energy;
      }
      void main(){
        vec3 color=texture2D(tColor,vUv).rgb;
        float z=texture2D(tDepth,vUv).x;
        float depth=uNear*uFar/(uFar-(uFar-uNear)*z);
        vec3 position=viewPosition(vUv);
        float occlusion=contactShade(position);
        color*=occlusion;
        float blur=min(abs(depth-uFocus)*.12,1.0)*uQuality;
        if(blur>.18){
          vec3 b=vec3(0.0);
          for(int i=0;i<6;i++){
            float a=float(i)*1.047198;
            b+=texture2D(tColor,vUv+vec2(cos(a),sin(a))*uPixel*blur).rgb;
          }
          color=mix(color,b/6.0*occlusion,.25);
        }
        color+=texture2D(tBloom,vUv).rgb*.32;
        color+=vec3(.18,.53,.85)*scatteredLight(position)*.09;
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
  function pass(output,shader){quad.material=shader;renderer.setRenderTarget(output);renderer.render(scene,camera);}
  return {
    resize(width,height,mobile){
      const samples=Math.min(mobile ? 2 : 4,renderer.capabilities.maxSamples);
      if(target.samples!==samples){target.samples=samples;target.dispose();}
      target.setSize(width,height);uniforms.uPixel.value.set(1/width,1/height);uniforms.uQuality.value=mobile ? 0 : 1;
      bw=Math.max(1,Math.round(width/4));bh=Math.max(1,Math.round(height/4));bloomA.setSize(bw,bh);bloomB.setSize(bw,bh);
    },
    render(source,sourceCamera,time,emitter){
      renderer.setRenderTarget(target);renderer.render(source,sourceCamera);
      uniforms.uProjectionInverse.value.copy(sourceCamera.projectionMatrixInverse);
      uniforms.uEmitter.value.copy(emitter.position).applyMatrix4(sourceCamera.matrixWorldInverse);
      uniforms.uAxis.value.copy(emitter.axis).transformDirection(sourceCamera.matrixWorldInverse);
      uniforms.uFocus.value=-uniforms.uEmitter.value.z;
      uniforms.uTime.value=time;
      pass(bloomA,bright);
      // Bilinear Gaussian offsets assume adjacent texels; widening the step
      // skips texels and turns a small highlight into a visible grid of dots.
      for(let i=0;i<2;i++){
        blur.uniforms.tColor.value=bloomA.texture;blur.uniforms.uStep.value.set(1/bw,0);pass(bloomB,blur);
        blur.uniforms.tColor.value=bloomB.texture;blur.uniforms.uStep.value.set(0,1/bh);pass(bloomA,blur);
      }
      pass(null,material);
    },
    dispose(){target.depthTexture.dispose();target.dispose();bloomA.dispose();bloomB.dispose();bright.dispose();blur.dispose();material.dispose();geometry.dispose();}
  };
}

let areaLightsReady=false;


export async function createMechanismScene(element, {still=false} = {}) {
  const canvas = document.createElement("canvas");
  canvas.className = "mechanism-canvas";
  canvas.setAttribute("aria-hidden", "true");
  let renderer, model, environment, resizeObserver, finish;
  let frame=0, running=false, failed=false, destroyed=false, ready=false, elapsed=still ? 10 : 4.8, last=0, lastPaint=0, frames=0;
  let view="auto", width=1, height=1, narrow=false, displayedState;
  const pointer = {x:0,y:0}, targetPointer = {x:0,y:0};
  const hero=element.dataset.sentinelCore==="access";
  const scene = new THREE.Scene();
  scene.background=new THREE.Color(0x090c10);
  scene.fog=new THREE.FogExp2(0x090c10,.024);
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
    finish?.dispose();
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
      camera.position.set(3.3+Math.sin(elapsed*.055)*.06+pointer.x*.12,4.6+expansion*.30,8.4+expansion*.62);
      camera.lookAt(1.45,-.10+expansion*.27,0);
    }
    renderer.info.reset();
    finish.render(scene,camera,elapsed,model.lightFrame());
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
    renderer.toneMappingExposure=.92;
    renderer.info.autoReset=false;
    renderer.shadowMap.enabled=true;
    renderer.shadowMap.type=THREE.PCFShadowMap;
    environment=await photographicEnvironment(renderer,element);
    scene.environment=environment.texture;
    scene.environmentIntensity=.32;
    scene.environmentRotation.set(0,.88,0);
    if(!areaLightsReady){RectAreaLightUniformsLib.init();areaLightsReady=true;}
    scene.add(new THREE.HemisphereLight(0xb7c9db,0x030609,.12));
    // Broad softboxes give the machined faces a continuous reflected highlight.
    const softbox=new THREE.RectAreaLight(0xe4efff,14,5,6);
    softbox.position.set(-3,6,4);softbox.lookAt(0,0,0);scene.add(softbox);
    const strip=new THREE.RectAreaLight(0x9acaf1,24,.7,5);
    strip.position.set(4,2,-3);strip.lookAt(0,0,0);scene.add(strip);
    const bounce=new THREE.RectAreaLight(0xffe2c5,4,3,1);
    bounce.position.set(-4,0,-2);bounce.lookAt(0,-.3,0);scene.add(bounce);
    // Rectangular lights do not cast shadows, so one neutral key supplies depth.
    const key=new THREE.DirectionalLight(0xeaf2ff,1.4);
    key.position.set(-4,8,6);key.castShadow=true;
    key.shadow.mapSize.set(2048,2048);
    Object.assign(key.shadow.camera,{left:-4.5,right:4.5,top:4.5,bottom:-4.5,near:1,far:24});
    key.shadow.normalBias=.012;key.shadow.bias=-.00010;key.shadow.radius=3;
    scene.add(key);
    model=makeModel(); scene.add(model.root);
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
