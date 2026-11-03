(function(root) {
  "use strict";
  const labels={EXTERNAL:"외부 트랙",INTERNAL:"내부 트랙",CONTROL:"공통 제어",RUNTIME:"실행 인프라",KOTH:"KoTH",PUBLIC:"대회 사이트"};
  const order=["EXTERNAL","INTERNAL","CONTROL","RUNTIME","KOTH","PUBLIC"];
  const rank={CRITICAL:0,WARNING:1,UNKNOWN:2,PENDING:3,NORMAL:4};
  function group(assets, matches=()=>true) {
    const groups=new Map();
    for(const asset of assets) {
      const id=asset.track || "UNMAPPED";
      if(!groups.has(id)) groups.set(id,{id,name:labels[id] || id,members:[],visible:[]});
      const value=groups.get(id);value.members.push(asset);if(matches(asset)) value.visible.push(asset);
    }
    return [...groups.values()].map(value=>{
      const counts={NORMAL:0,WARNING:0,CRITICAL:0,UNKNOWN:0,PENDING:0};
      for(const asset of value.members) counts[Object.hasOwn(counts,asset.risk?.level)?asset.risk.level:"UNKNOWN"]++;
      const level=Object.keys(rank).find(key=>counts[key]>0);
      const attention=value.members.filter(a=>a.risk?.level!=="NORMAL").sort((a,b)=>(rank[a.risk?.level]??2)-(rank[b.risk?.level]??2));
      return {...value,counts,level,total:value.members.length,attention};
    }).sort((a,b)=>rank[a.level]-rank[b.level] || (order.indexOf(a.id)<0?99:order.indexOf(a.id))-(order.indexOf(b.id)<0?99:order.indexOf(b.id)));
  }
  root.SentinelAssetGroups={group};
})(globalThis);
