/* Decorative projection only; operational readings remain in the DOM. */
(function () {
  "use strict";
  const reduced = matchMedia("(prefers-reduced-motion: reduce)");
  let paused = false;
  try { paused = localStorage.getItem("sentinel-motion") === "off"; } catch { /* Optional storage. */ }
  const instances = [], tau = Math.PI * 2;
  const palettes = {standby:[112,219,245],healthy:[101,227,218],attention:[237,183,103],incident:[255,119,112],offline:[123,151,169]};
  function createCore(canvas) {
    const ctx = canvas.getContext("2d", {alpha:true});
    if (!ctx) return;
    let width=0, height=0, frame=0, last=0, phase=0, visible=true;
    let pointerX=0, pointerY=0, tiltX=0, tiltY=0, color=palettes.standby, signalColor=palettes.standby;
    const large = canvas.dataset.sentinelCore === "access";
    const rgba = (alpha, rgb=color) => `rgba(${rgb.join(",")},${alpha})`;
    function line(points, alpha, thickness=1) {
      ctx.beginPath(); points.forEach((p,i)=>i?ctx.lineTo(p[0],p[1]):ctx.moveTo(p[0],p[1]));
      ctx.strokeStyle=rgba(alpha); ctx.lineWidth=thickness; ctx.stroke();
    }
    function arc(radius,start,end,alpha,thickness=1) {
      ctx.beginPath(); ctx.arc(0,0,radius,start,end); ctx.strokeStyle=rgba(alpha); ctx.lineWidth=thickness; ctx.stroke();
    }
    function project(x,y,z,rotation=phase*.075) {
      const a=rotation+tiltX,b=-.3+tiltY,xx=x*Math.cos(a)+z*Math.sin(a),zz=z*Math.cos(a)-x*Math.sin(a);
      const yy=y*Math.cos(b)-zz*Math.sin(b),depth=y*Math.sin(b)+zz*Math.cos(b),p=650/(650-depth);
      return [xx*p,yy*p,depth];
    }
    function draw() {
      if (!width || !height) return;
      color=palettes.standby;
      ctx.clearRect(0,0,width,height); ctx.save(); ctx.translate(width/2,height/2);
      const scale=Math.min(width/720,height/660); ctx.scale(scale,scale);
      const light=ctx.createRadialGradient(0,0,8,0,0,285);
      light.addColorStop(0,rgba(.12)); light.addColorStop(.44,rgba(.035)); light.addColorStop(1,rgba(0));
      ctx.fillStyle=light; ctx.fillRect(-360,-330,720,660);
      for(let i=-4;i<=4;i++){line([[i*60,-295],[i*60,295]],.04,.7);line([[-320,i*60],[320,i*60]],.04,.7);}
      for(let i=0;i<120;i++){
        const a=i/120*tau,major=i%5===0,r=266;
        line([[Math.cos(a)*r,Math.sin(a)*r],[Math.cos(a)*(r+(major?10:3)),Math.sin(a)*(r+(major?10:3))]],major?.66:.29,major?1.1:.7);
      }
      arc(287,-.13,.72,.27);arc(287,1.1,2.96,.14);arc(287,3.28,4.5,.3);arc(287,4.78,5.74,.14);
      arc(250,0,tau,.24,.75);arc(236,0,tau,.09);
      for(let i=0;i<12;i++){const start=i*tau/12+phase*.016;arc(224,start,start+.32,i%3?.13:.5,i%3?3:4);}
      const r=phase*.075;
      color=signalColor;
      arc(204,r-.5,r+.65,.9,1.8);arc(204,r+2.3,r+3.55,.6,1.3);arc(199,r+2.3,r+2.88,.24);
      arc(187,-r*.75+.2,-r*.75+1.62,.5,.8);arc(187,-r*.75+3.34,-r*.75+4.6,.3,.8);
      color=palettes.standby;
      ctx.setLineDash([1,7]);arc(176,0,tau,.28,1.6);ctx.setLineDash([]);
      const inner=ctx.createRadialGradient(-22,-24,0,0,0,137);
      inner.addColorStop(0,rgba(.19));inner.addColorStop(.4,rgba(.06));inner.addColorStop(1,rgba(0));
      ctx.fillStyle=inner;ctx.fillRect(-150,-150,300,300);
      // Triangulated sphere, ordered by depth.
      const mesh=[];
      for(let row=0;row<=12;row++){
        const lat=-Math.PI/2+row*Math.PI/12,band=[];
        for(let col=0;col<24;col++){const lon=col*tau/24;band.push(project(119*Math.cos(lat)*Math.cos(lon),119*Math.sin(lat),119*Math.cos(lat)*Math.sin(lon)));}
        mesh.push(band);
      }
      const edges=[];
      for(let row=0;row<12;row++)for(let col=0;col<24;col++){
        const a=mesh[row][col],b=mesh[row][(col+1)%24],c=mesh[row+1][col];edges.push([a,b],[a,c]);
        if(row>1&&row<10&&(row+col)%2===0)edges.push([a,mesh[row+1][(col+1)%24]]);
      }
      edges.sort((a,b)=>a[0][2]+a[1][2]-b[0][2]-b[1][2]);
      for(const edge of edges){const front=(edge[0][2]+edge[1][2])/238;line(edge,.09+Math.max(0,front)*.64,front>.4?.95:.7);}
      for(let orbit=0;orbit<3;orbit++){
        const points=[],angle=orbit*Math.PI/3+.55;
        for(let j=0;j<=90;j++){const t=j/90*tau,x=Math.cos(t)*154,z=Math.sin(t)*154;points.push(project(x*Math.cos(angle),x*Math.sin(angle),z,phase*.045));}
        for(let j=1;j<points.length;j++)line([points[j-1],points[j]],points[j][2]>0?.82:.12,points[j][2]>0?1:.7);
        const t=phase*.12+orbit*2.1,x=Math.cos(t)*154,z=Math.sin(t)*154,p=project(x*Math.cos(angle),x*Math.sin(angle),z,phase*.045);
        ctx.shadowColor=rgba(.7);ctx.shadowBlur=12;ctx.fillStyle=rgba(.95);ctx.beginPath();ctx.arc(p[0],p[1],2.3,0,tau);ctx.fill();ctx.shadowBlur=0;
      }
      for(const [x,y,d] of [[-198,-190,-1],[198,190,1]]){
        line([[x,y],[x+d*30,y-28*d],[x+d*90,y-28*d]],.33,.8);ctx.fillStyle=rgba(.7);ctx.fillRect(x-2,y-2,4,4);
      }
      for(const sign of [-1,1]){
        line([[sign*304,-65],[sign*316,-65],[sign*316,65],[sign*304,65]],.31,.8);
        line([[0,sign*303],[0,sign*288]],.6);line([[sign*298,0],[sign*286,0]],.6);
      }
      ctx.font='8px Consolas, monospace';ctx.textAlign="center";ctx.fillStyle=rgba(.52);
      for(let i=0;i<8;i++){const a=i*tau/8;ctx.fillText(String(i*45).padStart(3,"0"),Math.cos(a)*304,Math.sin(a)*304+3);}
      ctx.shadowColor=rgba(.7);ctx.shadowBlur=13;ctx.fillStyle="rgba(211,249,255,.9)";ctx.fillRect(-1.5,-1.5,3,3);ctx.shadowBlur=0;
      if(large){ctx.textAlign="left";ctx.font='9px Consolas, monospace';ctx.fillStyle=rgba(.52);ctx.fillText("SNTL / CORE",-308,-242);ctx.fillText("O B S E R V E",205,240);}
      ctx.restore();
    }
    function animate(now){
      frame=0;
      if(now-last>=1000/30){phase+=Math.min((now-(last||now))/1000,.05);last=now;tiltX+=(pointerX-tiltX)*.035;tiltY+=(pointerY-tiltY)*.035;draw();}
      if(!paused&&!reduced.matches&&visible&&!document.hidden)frame=requestAnimationFrame(animate);
    }
    function sync(){cancelAnimationFrame(frame);frame=0;last=0;draw();if(!paused&&!reduced.matches&&visible&&!document.hidden)frame=requestAnimationFrame(animate);}
    new ResizeObserver(()=>{
      const box=canvas.getBoundingClientRect(),ratio=Math.min(devicePixelRatio||1,1.5);width=box.width;height=box.height;
      canvas.width=Math.round(width*ratio);canvas.height=Math.round(height*ratio);ctx.setTransform(ratio,0,0,ratio,0,0);sync();
    }).observe(canvas);
    new IntersectionObserver(entries=>{visible=entries[0].isIntersecting;sync();}).observe(canvas);
    canvas.addEventListener("pointermove",event=>{const box=canvas.getBoundingClientRect();pointerX=(event.clientX-box.left-box.width/2)/box.width*.3;pointerY=(event.clientY-box.top-box.height/2)/box.height*.2;});
    canvas.addEventListener("pointerleave",()=>{pointerX=0;pointerY=0;});
    instances.push({canvas,sync,setState(state){signalColor=palettes[state]||palettes.standby;if(paused||reduced.matches)draw();}});
  }
  function syncControls(){
    const stopped=paused||reduced.matches;document.documentElement.classList.toggle("motion-paused",stopped);
    document.querySelectorAll("[data-motion-toggle]").forEach(button=>{
      button.setAttribute("aria-pressed",String(stopped));button.setAttribute("aria-label",reduced.matches?"기기 설정에 따라 모션 꺼짐":stopped?"모션 켜기":"모션 끄기");
      button.title=reduced.matches?"기기의 동작 줄이기 설정 적용 중":stopped?"모션 켜기":"모션 끄기";
      const label=button.querySelector("span");if(label)label.textContent=stopped?"MOTION OFF":"MOTION ON";
    });instances.forEach(instance=>instance.sync());
  }
  document.querySelectorAll("canvas[data-sentinel-core]").forEach(createCore);
  document.querySelectorAll("[data-motion-toggle]").forEach(button=>button.addEventListener("click",()=>{
    if(reduced.matches)return;paused=!paused;try{localStorage.setItem("sentinel-motion",paused?"off":"on");}catch{/* Optional storage. */}syncControls();
  }));
  reduced.addEventListener("change",syncControls);document.addEventListener("visibilitychange",()=>instances.forEach(instance=>instance.sync()));syncControls();
  window.SentinelCore={setState(canvas,state){instances.find(instance=>instance.canvas===canvas)?.setState(state);}};
})();
