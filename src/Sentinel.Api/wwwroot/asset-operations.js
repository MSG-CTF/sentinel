(function (root) {
  "use strict";
  const byId = id => document.getElementById(id);
  const node = (tag, text, cls) => {const el = document.createElement(tag); if (text !== undefined) el.textContent = text; if (cls) el.className = cls; return el;};
  let asset, config, busy = false, generation = 0, manual = null, mounted = false;
  const codes = {SSH_NOT_CONFIGURED:"SSH 접속 정보를 등록하면 사용할 수 있습니다", AUDIT_STORAGE_UNAVAILABLE:"작업 기록을 저장할 수 없어 실행을 중단했습니다", FRESH_STOPPED_SERVICE_REQUIRED:"최신 보고에서 서비스 중지가 확인될 때 복구할 수 있습니다", ACTION_IN_PROGRESS:"다른 작업이 진행 중입니다", ACTION_COOLDOWN:"같은 작업의 재실행 대기 시간입니다", SSH_RESULT_UNCONFIRMED_NO_RETRY:"SSH 결과를 확인하지 못했습니다. 중복 실행하지 말고 상태를 다시 조회하세요", REMOTE_ACTION_REJECTED:"서버가 요청을 거부했습니다. 현재 상태를 다시 확인하세요", START_REQUESTED_AWAITING_HEARTBEAT:"서비스 기동 요청 완료 · 다음 상태 보고에서 복구 여부를 확인합니다", SSH_VERIFIED:"SSH 연결과 조회를 확인했습니다", PROCESS_RESTARTED_NO_RETRY:"이전 실행 결과 미확인 · 자동 재실행하지 않음"};
  function bytes(value) {const n = Number(value); if (value == null || !Number.isFinite(n)) return "—"; return `${(n/1073741824).toLocaleString("ko-KR",{maximumFractionDigits:1})} GiB`;}
  function mount() {
    if (mounted) return; mounted = true;
    const panel = node("section",undefined,"asset-overview"); panel.id = "asset-overview";
    // Static structure only. Every server value is assigned with textContent.
    panel.innerHTML = '<section class="resource-panel"><div class="section-heading"><h3>서버 자원</h3><span id="asset-resource-source"></span></div><div class="host-identity"><strong id="asset-os">운영체제 미수집</strong><span id="asset-architecture"></span></div><p id="asset-hostname" class="host-caption"></p><div id="asset-resource-cards" class="resource-cards"></div><p id="asset-cpu-model" class="host-caption"></p><p id="asset-resource-note" class="host-caption"></p></section><section class="asset-action-panel"><div class="section-heading"><h3>SSH 접속·복구</h3><span id="asset-access-badge">확인 중</span></div><p id="asset-ssh-host"></p><div class="asset-action-buttons"><button id="asset-ssh-status" type="button">SSH 상태 조회</button><button id="asset-ssh-resources" type="button">자원 조회</button><button id="asset-ssh-start" class="recovery-button" type="button">서비스 복구</button></div><p id="asset-action-note" class="host-caption"></p><div id="asset-start-confirm" hidden><p>중지된 서비스를 기동합니다. 정상 판정과 복구 알림은 이후 측정 결과로 확인합니다.</p><button id="asset-start-accept" type="button">기동 실행</button><button id="asset-start-cancel" type="button">취소</button></div><p id="asset-action-result" role="status"></p><pre id="asset-ssh-output" hidden tabindex="0" aria-label="SSH 조회 결과"></pre><details class="asset-action-history"><summary>최근 작업 기록</summary><ol id="asset-action-history"></ol></details></section>';
    byId("detail-risk").before(panel);
    byId("asset-ssh-status").addEventListener("click",()=>execute("status"));
    byId("asset-ssh-resources").addEventListener("click",()=>execute("resources"));
    byId("asset-ssh-start").addEventListener("click",()=>{byId("asset-start-confirm").hidden=false; byId("asset-start-accept").focus();});
    byId("asset-start-cancel").addEventListener("click",()=>{byId("asset-start-confirm").hidden=true; byId("asset-ssh-start").focus();});
    byId("asset-start-accept").addEventListener("click",()=>execute("start"));
    byId("inspector").addEventListener("close",()=>{generation++; asset=null; config=null; manual=null; busy=false;});
  }
  async function request(path, body) {
    const headers = {};
    if (body) {
      const response = await fetch("/auth/csrf",{cache:"no-store",signal:AbortSignal.timeout(5000)});
      if (!response.ok) throw new Error("인증 상태를 확인할 수 없습니다");
      headers["X-CSRF-TOKEN"] = (await response.json()).request_token; headers["Content-Type"]="application/json";
    }
    const response = await fetch(path,{method:body?"POST":"GET",headers,body:body?JSON.stringify(body):undefined,cache:"no-store",signal:AbortSignal.timeout(body?32000:5000)});
    if (response.status === 401) {location.replace("/login"); throw new Error("로그인이 필요합니다");}
    const value = response.status === 202 ? {} : await response.json();
    if (!response.ok) throw new Error(codes[value.code] || "요청을 처리하지 못했습니다");
    return value;
  }
  function state() {
    if (!asset) return;
    const connected = config?.configured && config?.storage_healthy;
    const stopped = asset.status === "DOWN" && ["inactive","failed"].includes(asset.components?.service_state);
    for (const id of ["asset-ssh-status","asset-ssh-resources"]) byId(id).disabled = busy || !connected;
    byId("asset-ssh-start").disabled = busy || !connected || !stopped || !config?.can_start;
    byId("asset-start-accept").disabled=busy || !connected || !stopped;
    byId("asset-access-badge").textContent = config?.configured ? "SSH 등록됨" : config ? "미연결" : "확인 중";
    byId("asset-ssh-host").textContent = config?.configured ? `${config.username}@${config.host}:${config.port}` : "관리 접속 정보 미등록";
    byId("asset-action-note").textContent = config?.configured ? `${config.service} · 상태·자원 조회 / 정지 서비스 기동${stopped ? "" : " · 실행 중인 서비스는 재시작하지 않음"}` : "이 자산의 SSH 계정·인증 키·호스트 키·서비스 매핑이 필요합니다";
    const history=byId("asset-action-history"); history.replaceChildren();
    for (const entry of config?.history || []) history.append(node("li",`${new Date(entry.created_at).toLocaleTimeString("ko-KR")} · ${{status:"상태 조회",resources:"자원 조회",start:"서비스 기동"}[entry.action] || entry.action} · ${codes[entry.code] || entry.status}`));
    if (!history.children.length) history.append(node("li","기록된 작업 없음"));
  }
  async function loadConfig(id, version) {
    try {const result=await request(`/api/operations/assets/${encodeURIComponent(id)}`); if(version!==generation || asset?.asset_id!==id) return; config=result; state();}
    catch(error) {if(version!==generation)return; config={configured:false}; state(); byId("asset-action-result").textContent=error.message;}
  }
  function resources() {
    if (!asset) return;
    const fresh = !["UNKNOWN","PENDING"].includes(asset.risk.level);
    const direct = manual && Date.now()-Date.parse(manual.checked_at)<150000 && Date.parse(manual.checked_at)>Date.parse(asset.checked_at || 0);
    const parts = direct ? manual : fresh ? asset.components || {} : {};
    const cards=byId("asset-resource-cards"); cards.replaceChildren();
    byId("asset-os").textContent=parts.os_name || "운영체제 미수집";
    byId("asset-architecture").textContent=parts.architecture || "";
    byId("asset-hostname").textContent=parts.hostname || (asset.parent_target_id ? "호스트 자원은 관리 에이전트 연결 후 표시" : "서버 식별 정보 미수집");
    for (const [key,title,caption] of [["cpu","CPU",parts.cpu_logical_count?`${parts.cpu_logical_count} vCPU · ${asset.kind==="cluster_heartbeat"?"Kubernetes metrics":"1초 표본"}`:"코어 수 미수집"],["memory","메모리",`${bytes(parts.memory_used_bytes)} / ${bytes(parts.memory_total_bytes)}`],["disk","디스크",`${bytes(parts.disk_used_bytes)} / ${bytes(parts.disk_total_bytes)} · /`]]) {
      const card=node("div",undefined,"resource-card"), value=parts[`${key}_used_percent`], signal=asset.risk.signals.find(s=>s.metric===`${key}_used_percent`);
      card.dataset.level = direct ? "UNKNOWN" : signal?.level || "UNKNOWN";
      card.append(node("span",title),node("strong",value!=null?`${Number(value).toFixed(1)}%`:"—"),node("small",!direct&&signal?.pending_level?`${caption} · 부하 관찰 중`:caption));
      const meter=node("meter"); meter.min=0;meter.max=100;meter.value=Number(value)||0;meter.hidden=value==null;meter.setAttribute("aria-label",`${title} 사용률`);card.append(meter);cards.append(card);
    }
    byId("asset-cpu-model").textContent=parts.cpu_model || "CPU 모델 미수집";
    byId("asset-resource-source").textContent=direct?"SSH 실측":fresh&&parts.os_name?"서버 보고":"수집 대기";
    byId("asset-resource-note").textContent=asset.kind==="cluster_heartbeat"&&fresh?`${parts.ready_node_count || 0}/${parts.node_count || 0} 노드 Ready · 배치 가능 ${parts.schedulable_node_count || 0} · 메모리는 Kubernetes working set · 디스크 사용률 미수집`:
      direct?"SSH 조회 시점의 값 · 경고 판정은 정기 수집 결과를 사용합니다":parts.os_name?`${new Date(asset.checked_at).toLocaleTimeString("ko-KR")} 수신 · 가동 ${Math.floor(Number(parts.uptime_seconds || 0)/86400)}일`:
      asset.status==="STALE"?"오래된 자원 수치는 현재 값으로 표시하지 않습니다":"HTTP 응답만으로 운영체제·서버 자원을 알 수 없어 수집기 연결이 필요합니다";
  }
  async function execute(action) {
    if (!asset || busy) return;
    const id=asset.asset_id, version=generation; busy=true; state(); byId("asset-start-confirm").hidden=true;
    byId("asset-action-result").textContent=action==="start"?"서비스 기동 요청 중…":"SSH로 연결 중…";
    try {
      const result=await request(`/api/operations/assets/${encodeURIComponent(id)}/actions`,{action,request_id:crypto.randomUUID(),confirm_asset_id:action==="start"?id:null});
      if(version!==generation)return;
      byId("asset-action-result").textContent=codes[result.code] || result.status;
      byId("asset-ssh-output").hidden=!result.output; byId("asset-ssh-output").textContent=result.output?JSON.stringify(JSON.parse(result.output),null,2):"";
      if(action==="resources"&&result.status==="SUCCEEDED") {manual=JSON.parse(result.output); resources();}
      await loadConfig(id,version);
    } catch(error) {if(version===generation) byId("asset-action-result").textContent=error.message || "결과 미확인 · 상태 조회 후 확인하세요";}
    finally {if(version===generation){busy=false;state();}}
  }
  function render(value) {
    mount(); byId("asset-overview").hidden=!value;
    if(!value) {if(asset)generation++;asset=null;return;}
    const changed=asset?.asset_id!==value.asset_id, updated=asset?.checked_at!==value.checked_at; asset=value;
    if(changed){generation++;config=null;manual=null;busy=false;byId("asset-action-result").textContent="";byId("asset-ssh-output").hidden=true;byId("asset-start-confirm").hidden=true;loadConfig(asset.asset_id,generation);}
    else if(updated && !busy) loadConfig(asset.asset_id,generation);
    resources(); state();
  }
  async function refreshAlerts() {
    try {const value=await request("/api/alerts"); byId("discord-alert-state").textContent=!value.enabled?"알림 미연결":!value.storage_healthy?"알림 기록 오류":value.failure_code?`전송 재시도 대기 · ${value.failure_code}`:value.pending_count?`${value.pending_count}건 전송 대기`:value.last_sent_at?`마지막 전송 ${new Date(value.last_sent_at).toLocaleTimeString("ko-KR")}`:"주의·위험·복구 알림 연결됨"; byId("discord-alert-test").disabled=!value.enabled || !value.storage_healthy;}
    catch {byId("discord-alert-state").textContent="알림 연결 확인 불가";}
  }
  document.addEventListener("DOMContentLoaded",()=>{
    byId("discord-alert-test").addEventListener("click",async()=>{const button=byId("discord-alert-test");button.disabled=true;try{await request("/api/alerts/test",{});byId("discord-alert-state").textContent="시험 알림 전송 대기 · 실제 장애 알림 아님";}catch(error){byId("discord-alert-state").textContent=error.message;}finally{button.disabled=false;}});
    refreshAlerts(); setInterval(()=>{if(!document.hidden)refreshAlerts();},15000);
  });
  root.SentinelAssetOps={render};
})(globalThis);
