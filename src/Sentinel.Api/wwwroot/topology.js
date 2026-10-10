/* A deployment map, not a second health model. Planned components never enter health totals. */
(function (root) {
  "use strict";
  const width = 1640, height = 800, ns = "http://www.w3.org/2000/svg";
  const positions = {
    participant:[42,110,144,66], edge:[36,253,150,86],
    "internal-frontend":[238,132,142,78], "internal-backend":[418,132,150,78],
    "internal-backend-database":[620,104,136,68], "internal-backend-cache":[620,208,136,68],
    "external-frontend":[238,320,142,78], "external-backend":[418,320,150,78],
    "external-backend-database":[620,292,136,68], "external-backend-cache":[620,396,136,68],
    scheduler:[837,132,152,78], "scheduler-db":[837,244,152,64],
    broker:[1057,132,152,78], "broker-db":[1057,244,152,64],
    runtime:[1292,132,140,78], "runtime-db":[1292,244,140,64], "linux-pool":[1478,132,140,78],
    "koth-score":[837,412,172,78], "cloud-api":[1057,412,152,78],
    koth:[1292,412,140,78], windows:[1478,412,140,78],
    info:[42,644,148,76], ci:[246,644,172,76], ghcr:[486,596,176,76],
    bundle:[486,706,176,64], registry:[779,706,244,64], observer:[1194,644,394,116]
  };
  const zones = [
    ["access",20,40,182,476,"대회 진입","HTTPS / 트랙별 도메인"],
    ["platform",220,40,564,476,"참가자 플랫폼",null],
    ["control",817,40,414,288,"공통 제어","Active / Standby · 승격 미검증"],
    ["runtime",1272,40,356,288,"문제 실행","Runtime · 격리보안"],
    ["scoring",817,370,210,146,"KoTH 채점",null],
    ["provider",1045,370,186,146,"클라우드 자원",null],
    ["dedicated",1272,370,356,146,"전용 문제 VM",null],
    ["delivery",20,568,1112,220,"문제 발행","info.yaml → 검증 → 이미지와 사양 발행"],
    ["monitor",1170,568,458,220,"SLA 관측","직접 HTTP · 연결 상태 · 변경 기록"]
  ];
  // Points deliberately use the corridors between nodes, never the interiors of unrelated nodes.
  const routes = {
    "edge-internal":{p:[[186,281],[208,281],[208,171],[238,171]]},
    "edge-external":{p:[[186,309],[208,309],[208,359],[238,359]]},
    "internal-proxy":{p:[[380,171],[418,171]], label:[399,123,"/api/v1"]},
    "external-proxy":{p:[[380,359],[418,359]], label:[399,311,"/api/v1"]},
    "internal-database":{p:[[568,157],[598,157],[598,138],[620,138]]},
    "internal-cache":{p:[[568,190],[586,190],[586,242],[620,242]]},
    "external-database":{p:[[568,345],[598,345],[598,326],[620,326]]},
    "external-cache":{p:[[568,378],[586,378],[586,430],[620,430]]},
    "internal-create":{p:[[493,132],[493,73],[797,73],[797,157],[837,157]], label:[690,64,"생성 요청"]},
    "external-create":{p:[[568,355],[580,355],[580,92],[808,92],[808,193],[837,193]]},
    "scheduler-store":{p:[[913,210],[913,244]]},
    "candidate-query":{p:[[989,171],[1057,171]], label:[1023,159,"후보 · 예약"]},
    "broker-store":{p:[[1133,210],[1133,244]]},
    "broker-provider":{p:[[1209,184],[1251,184],[1251,432],[1209,432]], label:[1251,354,"quota"]},
    "internal-inventory":{p:[[507,132],[507,24],[1041,24],[1041,153],[1057,153]], secondary:true},
    "external-inventory":{p:[[522,320],[522,292],[608,292],[608,13],[1049,13],[1049,193],[1057,193]], secondary:true},
    "runtime-create":{p:[[913,132],[913,108],[1260,108],[1260,171],[1292,171]], label:[1134,100,"생성 · 상태 · 삭제"]},
    "runtime-store":{p:[[1362,210],[1362,244]]},
    "runtime-k3s":{p:[[1432,171],[1478,171]], label:[1455,123,"workload"]},
    "scoring-server":{p:[[1009,451],[1035,451],[1035,350],[1260,350],[1260,451],[1292,451]], label:[1145,344,"점수 수집"]},
    "internal-score-poll":{p:[[493,210],[493,282],[797,282],[797,451],[837,451]], label:[798,389,"점수 수집"]},
    "external-score-poll":{p:[[493,398],[493,528],[808,528],[808,473],[837,473]]},
    "internal-token":{p:[[853,412],[853,341],[805,341],[805,288],[480,288],[480,210]], secondary:true},
    "external-token":{p:[[881,490],[881,538],[480,538],[480,398]], secondary:true},
    "ci-source":{p:[[190,682],[246,682]], label:[220,632,"검증 · build"]},
    "image-publish":{p:[[418,663],[454,663],[454,634],[486,634]], label:[454,604,"digest"]},
    "bundle-publish":{p:[[418,700],[454,700],[454,738],[486,738]]},
    "bundle-poll":{p:[[662,738],[779,738]], label:[720,725,"poller 수집"]},
    "image-pull":{p:[[662,634],[735,634],[735,552],[1634,552],[1634,171],[1618,171]], label:[1445,544,"image digest pull"]}
  };
  const names = {"internal-backend":"Backend API", "external-backend":"Backend API", broker:"Broker", "runtime-db":"Runtime Store", "koth-score":"Scoring", ghcr:"GHCR", bundle:"Actions Bundle", registry:"Challenge Registry", "linux-pool":"Linux / K3s"};
  let options, canvas, svg, viewport, size, scale = 1, fitted = true, secondary = false;
  const buttons = new Map(), paths = new Map();
  const element = (tag,text,className) => {const item = document.createElement(tag); if (text !== undefined) item.textContent = text; if (className) item.className = className; return item;};
  const svgElement = (tag,attrs) => {const item = document.createElementNS(ns,tag); for (const [key,value] of Object.entries(attrs || {})) item.setAttribute(key,value); return item;};
  function position(item, rect) {const [x,y,w,h] = rect; Object.assign(item.style,{left:`${x}px`,top:`${y}px`,width:`${w}px`,height:`${h}px`});}
  function outline(rect,id,title,subtitle) {
    const section = element("section",undefined,`map-zone zone-${id}`); position(section,rect); section.setAttribute("aria-label",title);
    const head = element("div",undefined,"map-zone-heading"); head.append(element("h3",title)); if (subtitle) head.append(element("span",subtitle)); section.append(head); canvas.append(section);
  }
  function addButton(id,title,role,context = false) {
    const button = element("button",undefined,`map-node${context ? " map-context" : ""}`); button.type = "button"; button.dataset.nodeId = id; position(button,positions[id]);
    const copy = element("span",undefined,"map-node-copy"), state = element("span",undefined,"map-node-state");
    copy.append(element("strong",title,"map-node-name"),state); button.append(options.icon(role),copy);
    button.addEventListener("click",()=>options.inspect({kind:context ? "context" : "asset",id},button));
    button.addEventListener("mouseenter",()=>focusNode(id)); button.addEventListener("mouseleave",()=>focusNode(null));
    button.addEventListener("focus",()=>focusNode(id)); button.addEventListener("blur",()=>focusNode(null));
    canvas.append(button); buttons.set(id,{button,state,title}); return button;
  }
  function focusNode(id) {
    canvas.classList.toggle("map-focused",Boolean(id));
    for (const [key,item] of paths) item.group.classList.toggle("connected",Boolean(id && (item.link.from === id || item.link.to === id)));
  }
  function buildLine(link,route) {
    const group = svgElement("g",{"data-map-link":link.id,class:`map-link${route.secondary ? " secondary-link" : ""}`});
    const d = route.p.map((point,index)=>`${index ? "L" : "M"}${point.join(" ")}`).join(" ");
    const stroke = svgElement("path",{d,class:"map-link-stroke"}), hit = svgElement("path",{d,class:"map-link-hit",tabindex:"0",role:"button"});
    hit.addEventListener("click",()=>options.inspect({kind:"link",id:link.id},hit));
    hit.addEventListener("keydown",event=>{if (["Enter"," "].includes(event.key)) {event.preventDefault(); options.inspect({kind:"link",id:link.id},hit);}});
    group.append(stroke,hit);
    if (route.label) {const [x,y,text] = route.label, label = svgElement("text",{x,y,class:"map-link-label","text-anchor":"middle"}); label.textContent = text; group.append(label);}
    svg.append(group); paths.set(link.id,{group,stroke,hit,link});
  }
  function applyScale(next) {
    scale = Math.min(1.5,Math.max(.6,next)); canvas.style.transform = `scale(${scale})`; size.style.width = `${width*scale}px`; size.style.height = `${height*scale}px`;
    document.getElementById("map-fit").textContent = `${Math.round(scale*100)}%`;
    document.getElementById("map-minus").disabled = scale <= .6; document.getElementById("map-plus").disabled = scale >= 1.5;
  }
  function fit(resetPosition = true) {
    if (!viewport.clientWidth) return;
    const left = resetPosition === false ? viewport.scrollLeft / scale : 0;
    const top = resetPosition === false ? viewport.scrollTop / scale : 0;
    fitted = true;
    // A phone pans a readable map instead of reducing all labels to a miniature.
    applyScale(viewport.clientWidth < 640 ? 1 : Math.max(.7,Math.min(1,(viewport.clientWidth-2)/width)));
    viewport.scrollTo({left:left*scale,top:top*scale});
  }
  function mount(settings) {
    options = settings; canvas = document.getElementById("architecture-map"); viewport = document.getElementById("map-viewport"); size = document.getElementById("map-size");
    canvas.style.width = `${width}px`; canvas.style.height = `${height}px`;
    zones.forEach(([id,x,y,w,h,title,subtitle])=>outline([x,y,w,h],id,title,subtitle));
    const separator = element("div",undefined,"map-track-separator"); Object.assign(separator.style,{left:"236px",top:"278px",width:"532px"}); canvas.append(separator);
    for (const [text,x,y] of [["내부 트랙",238,96],["외부 트랙",238,284]]) {const tag = element("span",text,"map-track-label"); Object.assign(tag.style,{left:`${x}px`,top:`${y}px`}); canvas.append(tag);}
    svg = svgElement("svg",{viewBox:`0 0 ${width} ${height}`,class:"map-lines","aria-label":"역할 간 연결 경로"});
    const defs = svgElement("defs");
    for (const state of ["verified","failed","unknown","design","code","blocked"]) {
      const marker = svgElement("marker",{id:`arrow-${state}`,viewBox:"0 0 10 10",refX:"9",refY:"5",markerWidth:"6",markerHeight:"6",orient:"auto-start-reverse",class:`map-arrow stage-${state}`});
      marker.append(svgElement("path",{d:"M1 1L9 5L1 9z"})); defs.append(marker);
    }
    svg.append(defs); canvas.append(svg);
    addButton("participant","참가자","USER",true); buttons.get("participant").state.textContent = "HTTPS 접속";
    const entry = svgElement("path",{d:"M114 176V253",class:"map-entry-line","marker-end":"url(#arrow-design)"}); svg.append(entry);
    for (const link of options.catalog.links) {const route = routes[link.id]; if (route) buildLine(link,route);}
    for (const definition of options.catalog.nodes) if (positions[definition.id]) addButton(definition.id,names[definition.id] || definition.name,definition.role);
    const observer = addButton("observer","SLA / Sentinel","MONITOR",true); observer.classList.add("map-observer");
    const reading = element("span",undefined,"map-observer-reading"); reading.append(element("span","실측: 수집 대기"),element("span","알림 · 자동 복구 미연결")); observer.append(reading);
    // Observation is a separate path, not a workload command or an integration success claim.
    const observe = svgElement("g",{class:"map-observation"});
    observe.append(svgElement("path",{d:"M1194 700H1152V537H792V488H784",class:"map-observe-line","marker-end":"url(#arrow-design)"}));
    const observeLabel = svgElement("text",{x:"1090",y:"531",class:"map-link-label","text-anchor":"middle"}); observeLabel.textContent = "백엔드 healthz 점검"; observe.append(observeLabel); svg.append(observe);
    const sourceNote = element("span","백엔드 소유 · 수집 경로 불일치","map-registry-note"); Object.assign(sourceNote.style,{left:"779px",top:"678px"}); canvas.append(sourceNote);
    document.getElementById("map-secondary").addEventListener("click",()=>{secondary = !secondary; canvas.classList.toggle("show-secondary",secondary); document.getElementById("map-secondary").setAttribute("aria-pressed",String(secondary));});
    document.getElementById("map-fit").addEventListener("click",fit);
    for (const [id,delta] of [["map-minus",-.1],["map-plus",.1]]) document.getElementById(id).addEventListener("click",()=>{fitted = false; applyScale(scale+delta);});
    new ResizeObserver(()=>{if (fitted) fit(false);}).observe(viewport);
    requestAnimationFrame(fit);
  }
  function render(definitions, assets, offline, ready = true, recovery = null) {
    for (const definition of definitions) {
      const item = buttons.get(definition.id); if (!item) continue;
      const reading = definition.reading, cls = reading ? options.statusClass(reading.status) : definition.probe_id ? "pending" : "planned";
      item.button.className = `map-node status-${cls}`;
      item.state.textContent = reading ? options.statusNames[reading.status] : definition.probe_id ? ready ? "감시 제외" : "점검 대기" : definition.stage === "CODE" ? "소스 확인" : "설계";
      item.button.setAttribute("aria-label",`${options.trackLabel(definition.track)} ${definition.name} · ${item.state.textContent}`);
      item.button.title = reading ? `${reading.name} · ${item.state.textContent}` : definition.note || "실행 상태 미확인";
    }
    for (const item of paths.values()) {
      const result = SentinelArchitecture.resolveLink(item.link,assets), cls = options.stageClass(result.state);
      item.group.classList.remove(...["verified","failed","unknown","design","code","blocked"].map(state=>`link-${state}`)); item.group.classList.add(`link-${cls}`);
      item.stroke.setAttribute("marker-end",`url(#arrow-${cls})`); item.hit.setAttribute("aria-label",`${item.link.label} · ${options.stageNames[result.state]}`);
    }
    buttons.get("observer").state.textContent = offline ? "최신 수집 확인 불가" : recovery?.enabled ? "로컬 자동 복구 활성" : "관측 전용 · 복구 명령 없음";
    const direct = assets.filter(asset => asset.evidence_type === "DIRECT_PROBE" && !["UNCONFIGURED", "PENDING", "STALE"].includes(asset.status)).length;
    const indirect = assets.filter(asset => asset.evidence_type === "HEALTHZ_COMPONENT" && !["UNCONFIGURED", "PENDING", "STALE"].includes(asset.status)).length;
    buttons.get("observer").button.querySelector(".map-observer-reading span:first-child").textContent = offline ? "실측: 최신 결과 확인 불가" : `실측: 직접 ${direct} · 구성 요소 ${indirect}`;
    buttons.get("observer").button.querySelector(".map-observer-reading span:last-child").textContent = recovery?.enabled ? `복구: 앱 서비스 ${recovery.assets?.length || 0}개` : "자동 복구 비활성";
    buttons.get("observer").button.classList.toggle("status-unknown",offline);
    canvas.dataset.measuredCount = assets.length;
  }
  root.SentinelTopology = {mount,render,fit,positions,routes,width,height};
})(globalThis);
