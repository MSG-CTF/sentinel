"use strict";
const byId = id => document.getElementById(id);
const catalog = SentinelArchitecture.catalog;
const statusNames = {HEALTHY:"정상", DOWN:"장애", DEGRADED:"연결 저하", UNVERIFIED:"확인 불가", UNCONFIGURED:"미연결", PENDING:"점검 대기", STALE:"확인 불가"};
const stageNames = {VERIFIED:"실측", FAILED:"연결 실패", UNKNOWN:"확인 불가", PENDING:"미연결", CODE:"소스 확인", DESIGN:"설계", BLOCKED:"불일치"};
const groupNames = {PUBLIC:"배포 사이트", INTERNAL:"내부 트랙", EXTERNAL:"외부 트랙", CONTROL:"공통 제어", RUNTIME:"문제 실행", ACCESS:"대회 진입", SCORING:"KoTH 채점", DELIVERY:"문제 발행"};
const groupOrder = ["PUBLIC", "ACCESS", "INTERNAL", "EXTERNAL", "CONTROL", "RUNTIME", "SCORING", "DELIVERY"];
const assetLabels = {"internal-backend":["내부 백엔드","백엔드 API"], "external-backend":["외부 백엔드","백엔드 API"], scheduler:["인스턴스 스케줄러","스케줄러"], broker:["리소스 브로커","리소스 브로커"], runtime:["문제 런타임","런타임 API"], koth:["KoTH 서버","KoTH"], windows:["Windows 문제 서버","Windows"]};
const roleNotes = {SCHEDULER:"생성·만료 관리", BROKER:"자원 후보·예약", RUNTIME:"문제 생성·삭제", KOTH:"전용 문제 서버", WINDOWS:"전용 문제 서버"};
const reasonNames = {
  RUNTIME_WORKLOAD_READY:"연결 시험 컨테이너와 접속 endpoint 준비 완료", RUNTIME_WORKLOAD_NOT_READY:"연결 시험 컨테이너 또는 endpoint 준비 안 됨", RUNTIME_STORE_QUERY_READY:"Runtime의 저장된 작업 기록 조회 정상", K3S_NODE_READY:"로컬 K3s 노드 Ready · pressure 없음", K3S_NODE_NOT_READY:"K3s 노드 Ready 확인 실패", K3S_NODE_PRESSURE:"K3s 노드의 메모리·디스크·프로세스 자원 부족", RUNTIME_IDENTITY_MISMATCH:"등록한 인스턴스·작업과 응답 식별자가 다름", STALE_RUNTIME_OBSERVATION:"Runtime 관측 시각이 유효 범위를 벗어남", INVALID_RUNTIME_STATUS:"Runtime 상태 응답이 계약과 다름", INVALID_RUNTIME_OPERATION:"완료된 연결 시험 작업 기록 확인 실패",
  SCHEDULER_QUERY_READY:"인증된 최근 인스턴스 목록 조회 정상", BROKER_QUERY_READY:"인증된 자산 목록 조회 정상", INVALID_INVENTORY_CONTRACT:"목록 응답 필드·범위·시간이 실제 API 계약과 다름",
  CLUSTER_NOT_CONFIGURED:"실제 K3s 클러스터와 런타임 연결 정보 필요", EXECUTION_TARGET_NOT_CONFIGURED:"실행 중인 문제 서버와 관리 주소 필요", PROVIDER_NOT_CONFIGURED:"클라우드 계정과 VM 연결 필요", SCORING_NOT_CONFIGURED:"채점 서비스와 점수 수집 연결 필요", DELIVERY_NOT_CONNECTED:"배포할 문제·실행 기록·산출물 연결 필요", INGESTION_NOT_IMPLEMENTED:"현재 실행본에 새 산출물 수집 경로 없음",
  BACKEND_READY:"API 응답과 DB·Redis 연결 정상", HTTP_REACHABLE:"사이트 응답 확인 · 내부 기능은 별도 점검", HEALTH_CONTRACT_MATCHED:"설정한 상태 계약과 일치",
  DATABASE_DOWN:"API는 응답하지만 PostgreSQL 연결 실패", CACHE_DOWN:"API는 응답하지만 Redis 연결 실패", DATABASE_AND_CACHE_DOWN:"API는 응답하지만 DB·Redis 연결 실패",
  POSTGRESQL_READY:"백엔드 healthz에서 DB 연결 정상 확인", POSTGRESQL_DOWN:"백엔드 healthz에서 DB 연결 실패 확인", REDIS_READY:"백엔드 healthz에서 Redis 연결 정상 확인", REDIS_DOWN:"백엔드 healthz에서 Redis 연결 실패 확인",
  PARENT_HEALTH_UNVERIFIED:"백엔드 응답으로 구성 요소 상태를 확인하지 못함 · DB·Redis 장애로 단정할 수 없음", EXPECTED_JSON_RECEIVED_OTHER:"healthz JSON이 아닌 응답 · 백엔드 상태 확인 불가", INVALID_HEALTH_CONTRACT:"응답 필드나 상태 값이 백엔드 계약과 다름", INCONSISTENT_HEALTH_CONTRACT:"HTTP 상태와 DB·Redis 판정이 일치하지 않음",
  AUTH_REQUIRED:"점검 API 인증 필요", AUTH_TOKEN_NOT_CONFIGURED:"점검용 인증 토큰 미설정", ENDPOINT_NOT_FOUND:"점검 API 경로 없음", REDIRECT_BLOCKED:"다른 주소로 이동하는 응답 · 점검 주소 확인 필요",
  TIMEOUT:"점검 제한 시간 초과 · 원인 자산은 미확인", CONNECTION_FAILED:"서비스 연결 실패 · VM 전원 상태를 뜻하지는 않음", RESPONSE_READ_FAILED:"응답을 끝까지 읽지 못함", HTTP_ERROR:"예상한 HTTP 상태와 다름", INVALID_JSON:"JSON 응답을 읽을 수 없음", RESPONSE_TOO_LARGE:"점검 응답 크기 제한 초과",
  ENDPOINT_NOT_CONFIGURED:"점검 주소 미설정 · 정상·장애 판정에서 제외", NOT_CHECKED_YET:"실제 서버의 첫 응답 대기", PAGE_IDENTITY_MISMATCH:"대회 사이트 내용과 응답이 일치하지 않음", HEALTH_FIELD_MISMATCH:"상태 필드가 설정과 다름", MONITOR_REFRESH_STALE:"최신 점검 확인 불가 · 이전 결과로 정상 판정하지 않음", INVALID_CONFIGURATION:"점검 주소 설정 확인 필요"
};
const iconPaths = {
  BACKEND:["M5 5h22v9H5zM5 18h22v9H5zM9 9h1m-1 13h1m5-13h8m-8 13h8"],
  FRONTEND:["M3 5h26v20H3zM3 10h26M7 8h.1m3 0h.1M11 29h10m-5-4v4"],
  POSTGRESQL:["M5 7c0-6 22-6 22 0s-22 6-22 0zm0 0v8c0 6 22 6 22 0V7M5 15v9c0 6 22 6 22 0v-9"],
  REDIS:["m16 3 13 6-13 6L3 9zM3 15l13 6 13-6M3 21l13 6 13-6"],
  SCHEDULER:["M9 3v5m14-5v5M4 6h24v23H4zM4 12h24M10 18h4v5h-4zM20 18h2m-2 5h2"],
  BROKER:["M16 3v8m0 0H5v7m11-7h11v7M12 3h8v6h-8zM2 20h7v8H2zm11 0h7v8h-7zm10 0h7v8h-7zM16 11v7"],
  RUNTIME:["m16 2 12 7v14l-12 7L4 23V9zM4 9l12 7 12-7M16 16v14M10 6l12 7m-12 6 6 4 6-4"],
  KOTH:["M6 28V5m0 0h21l-5 6 5 6H6M2 28h9"],
  WINDOWS:["M3 5 15 3v12H3zM18 3l12-2v14H18zM3 18h12v12L3 28zm15 0h12v14l-12-2z"],
  EDGE:["M4 5h24v22H4zM4 11h24M8 8h.1m4 0h.1M9 17h14m-14 5h8"],
  CLOUD:["M7 26a6 6 0 0 1-1-12 10 10 0 0 1 19 0 6 6 0 0 1 0 12z"],
  LINUX:["M3 5h26v22H3zM8 12l5 4-5 4m9 0h6"],
  SCORING:["M5 4h22v24H5zM10 10h12m-12 6h7m-7 6h12M23 16v4"],
  SPEC:["M7 3h12l6 6v20H7zM19 3v7h6M11 15h10m-10 5h7m-7 5h10"],
  CI:["M4 9h7v7H4zM20 4h8v8h-8zM20 20h8v8h-8zM11 12h5V8h4m-4 4v12h4"],
  BUNDLE:["M6 4h15l5 5v20H6zM21 4v6h5M13 9h3m-3 5h3m-3 5h3m-3 5h3"],
  REGISTRY:["M4 5h24v8H4zm0 11h24v11H4zM9 9h1m-1 12h1m5 0h8"],
  USER:["M22 10a6 6 0 1 1-12 0 6 6 0 0 1 12 0zM5 29v-3a11 11 0 0 1 22 0v3"],
  MONITOR:["M3 4h26v20H3zM7 15h4l3-6 4 10 3-4h4M11 29h10m-5-5v5"],
  LINK:["M12 19l8-8M11 23l-3 3a6 6 0 0 1-8-8l6-6m15 9 5-6a6 6 0 0 0-8-8l-3 3"]
};
let latest = null, eventData = [], assets = [], architectureNodes = [], disconnected = false, inFlight = false;
let accessCatalog = null, recoverySignature = "";
const recoveryNames = {QUEUED:"실행 대기", CLAIMED:"실행 중", VERIFYING:"기능 확인 중", SUCCEEDED:"복구 확인", BLOCKED:"조치 중단", CANCELLED:"요청 취소", EXPIRED:"기한 초과"};
const timeoutRecoveryReason = {CONFIRMED_STOPPED_TIMEOUT:"시간 초과 · 실행기가 컨테이너 정지 확인"};
const recoveryReasons = {CONFIRMED_CONNECTION_FAILURE:"연속 연결 실패", EXECUTOR_CLAIMED:"실행기가 요청을 인수함", CONTAINER_STARTED:"기동 완료 · 기능 확인 대기", FUNCTION_VERIFIED:"자산별 정상 조건 연속 확인", PRECONDITION_NOT_MET:"컨테이너 정지 또는 의존 서비스 조건 불충족", EXECUTION_FAILED:"실행 실패", OUTCOME_UNKNOWN:"실행 결과 불명 · 중복 실행 보류", EVIDENCE_CHANGED:"장애 근거 변경", MONITOR_RESTARTED:"감시기 재시작 · 이전 대기 요청 취소", RECOVERY_DEADLINE_EXCEEDED:"요청 또는 기능 확인 기한 초과"};
const viewKeys = ["architecture", "status", "connections"];
let view = "architecture", issuesOnly = false, expandedEvents = false, selection = null, opener = null;
let assetSignature = "", eventSignature = "", detailEventSignature = "";
const assetRows = new Map(), groupRows = new Map(), contractRows = new Map(), relatedRows = new Map();
const detailFields = new Map();
const time = value => value && Number.isFinite(Date.parse(value)) ? new Date(value).toLocaleTimeString("en-GB", {hour12:false, timeZone:"Asia/Seoul"}) : "미확인";
const trackLabel = track => groupNames[track] || track || "미지정";
const label = asset => {const short = assetLabels[asset.asset_id]; return short && short[0] === asset.name ? short[1] : asset.name || asset.asset_id;};
const statusClass = status => status === "HEALTHY" ? "healthy" : status === "DOWN" ? "down" : status === "DEGRADED" ? "degraded" : ["UNCONFIGURED", "PENDING"].includes(status) ? "pending" : "unknown";
const stageClass = state => ({VERIFIED:"verified", FAILED:"failed", UNKNOWN:"unknown", PENDING:"design", CODE:"code", DESIGN:"design", BLOCKED:"blocked"}[state] || "design");
const requiresAttention = asset => ["DOWN", "DEGRADED", "UNVERIFIED", "STALE"].includes(asset.status);
const nodeById = id => architectureNodes.find(item => item.id === id);
const linkById = id => catalog.links.find(item => item.id === id);
const sourceDate = key => catalog.sources[key]?.reviewed_at || "2026-10-07";
const linkDates = link => [...new Set([link.source, link.peer_source].filter(Boolean).map(sourceDate))].sort().join(" · ");
function renderRecovery() {
  const recovery = latest?.recovery, enabled = recovery?.enabled === true;
  const bindings = recovery?.assets || [], armed = bindings.filter(asset => asset.armed).length;
  byId("operation-mode").textContent = disconnected ? "Sentinel 연결 끊김 · 복구 상태 미확인" : enabled ? `로컬 자동 복구 · 앱 서비스 ${bindings.length}개 정지 컨테이너 기동` : "관측 전용 · 서버 변경 없음";
  byId("recovery-mode").textContent = disconnected ? "현재 상태 미확인" : enabled ? "로컬 활성" : "비활성";
  byId("recovery-scope").textContent = `정상 기준 확인 후 연결 실패·시간 초과 ${latest?.failure_threshold || 3}회와 실행기 정지 확인 · 자산별 장애당 최대 1회 · 한 번에 한 서비스 · DB·Redis 자동 기동 제외`;
  byId("recovery-executor").textContent = disconnected ? "최신 실행기 연결과 복구 결과를 확인할 수 없음" : !enabled ? "local-recovery 프로필에서 실행 · 운영 자산은 접속·복구 계획만 등록됨" : !recovery.storage_healthy ? "복구 기록 저장 실패 · 자동 실행 중단" : !recovery.executor_connected ? "실행기 최근 연결 미확인 · 새 복구 요청 대기" : armed === bindings.length ? `실행기 연결됨 · ${armed}개 자산 장애 감시 중` : `실행기 연결됨 · ${armed}/${bindings.length}개 장애 감시 준비 · 나머지는 정상 기준 또는 재실행 대기 시간 확인 중`;
  const signature = JSON.stringify([recovery?.requests, disconnected, enabled]);
  if (signature === recoverySignature) return;
  recoverySignature = signature;
  const feed = byId("recovery-feed"); feed.replaceChildren();
  for (const request of (recovery?.requests || []).slice(0, 10)) {
    const row = node("li", undefined, "recovery-row");
    const name = `${trackLabel(request.track)} · ${catalog.nodes.find(asset => asset.id === request.asset_id)?.name || request.asset_id}`;
    row.append(node("time", time(request.completed_at || request.created_at)), node("strong", `${name} · ${disconnected ? "이전 기록: " : ""}${recoveryNames[request.status] || "미확인"}`), node("span", recoveryReasons[request.reason_code] || timeoutRecoveryReason[request.reason_code] || "결과 확인 필요"));
    feed.append(row);
  }
  if (!feed.children.length) feed.append(node("li", "복구 실행 기록 없음", "empty-message"));
}
function renderAccessDetail() {
  const observer = selection?.kind === "context" && selection?.id === "observer";
  if (observer) byId("detail-status").textContent = disconnected ? "현재 상태 미확인" : latest?.recovery?.enabled ? "로컬 자동 복구" : "관측 전용";
  const id = selection?.kind === "asset" ? selection.id : observer ? "sentinel" : null;
  const plan = accessCatalog?.assets.find(item => item.asset_id === id);
  byId("detail-access-section").hidden = !id;
  if (!id) return;
  const binding = accessCatalog?.local_bindings?.find(item => item.asset_id === id);
  const local = binding && latest?.recovery?.enabled && latest.recovery.assets?.some(item => item.asset_id === id);
  const observation = ["local", "local-recovery"].includes(latest?.profile) ? accessCatalog?.local_observations?.find(item => item.asset_id === id) : null;
  byId("detail-access-state").textContent = !plan ? "접속 계획을 불러오는 중" : local ? disconnected ? "로컬 복구 연결 상태 미확인" : "로컬 복구 활성 · 운영 연결은 미설정" : observation ? disconnected ? "로컬 점검 연결 상태 미확인" : "로컬 점검 연결 · 복구 실행 미연결" : "운영 접속·복구 설계 · 실행 미연결";
  const list = byId("detail-access"); list.replaceChildren();
  if (!plan) return;
  const fields = [["복구 담당", plan.recovery_owner], ["접속 수단", local ? binding.transport : plan.proposed_transport], ["복구 작업", local ? `정지한 ${binding.service} 컨테이너 기동 · 장애당 1회` : plan.candidate_actions.join(" · ") || "자동 실행 작업 미등록"], ["완료 기준", local ? binding.verification : plan.verification], ["호스트 장애 경로", plan.fallback]];
  const restored = SentinelDataRecovery.describe(id);
  if (restored) fields.unshift(["마지막 격리 복원", restored]);
  if (observation) fields.unshift(["현재 점검", `${observation.transport} · ${observation.scope}`]);
  for (const [title, value] of fields) {const box = node("div"); box.append(node("dt", title), node("dd", value)); list.append(box);}
  const inputNames = {host_id:"호스트 ID",management_endpoint:"관리 주소",credential_reference:"인증정보 참조",service_binding:"서비스 매핑",recovery_policy:"복구 정책",repository:"저장소",source_revision:"소스 버전",validation_workflow:"검증 작업",workflow_id:"작업 ID",rerun_policy:"재실행 정책",registry_repository:"이미지 저장소",image_digest:"이미지 digest",pull_probe_binding:"pull 점검 위치",artifact_id:"산출물 ID",ingestion_policy:"수집 정책",backend_endpoint:"백엔드 주소",revision_contract:"revision 계약",provider:"클라우드",account_id:"계정 ID",region:"리전",provider_vm_id:"VM ID",power_action_policy:"전원 조치 정책",scheduler_url:"Scheduler 주소",runtime_url:"Runtime 주소",service_token_reference:"서비스 인증정보 참조",inventory_pagination_contract:"전체 목록 조회 계약",instance_target_binding:"인스턴스·호스트 매핑",healthcheck_contract:"문제 기능 점검 계약",host_management_binding:"노드 관리 연결"};
  byId("detail-access-note").textContent = `${plan.preconditions.join(" · ")} / 운영 연결에 필요한 항목: ${plan.required_inputs.map(key => inputNames[key] || key).join(" · ")}`;
}
async function loadAccessPlan() {
  try {
    const response = await fetch("/asset-access.json", {cache:"no-store", signal:AbortSignal.timeout(5000)});
    if (!response.ok) throw new Error("access plan unavailable");
    const document = await response.json();
    if (document.schema_version !== 1 || !Array.isArray(document.assets)) throw new Error("access plan invalid");
    accessCatalog = document;
    const list = byId("access-plan-list"); list.replaceChildren();
    for (const plan of document.assets) {
      const button = node("button", undefined, "access-row"); button.type = "button";
      const definition = catalog.nodes.find(item => item.id === plan.asset_id);
      button.append(node("span", `${trackLabel(plan.track)} · ${definition?.name || "Sentinel"}`), node("span", plan.proposed_transport), node("span", document.local_bindings?.some(item => item.asset_id === plan.asset_id) ? "로컬 복구 구현 · 운영 미연결" : document.local_observations?.some(item => item.asset_id === plan.asset_id) ? "로컬 점검 연결 · 복구 미연결" : "설계 · 운영 미연결"));
      button.addEventListener("click", () => inspect(plan.asset_id === "sentinel" ? {kind:"context", id:"observer"} : {kind:"asset", id:plan.asset_id}, button));
      list.append(button);
    }
    if (byId("inspector").open) renderAccessDetail();
  } catch {
    byId("access-plan-list").textContent = "접속·복구 계획을 불러오지 못함";
  }
}
function node(tag, text, className) {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = text;
  if (className) element.className = className;
  return element;
}
function icon(role) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 32 32"); svg.setAttribute("aria-hidden", "true"); svg.classList.add("asset-icon");
  for (const d of iconPaths[role] || iconPaths.BACKEND) {const path = document.createElementNS(svg.namespaceURI, "path"); path.setAttribute("d", d); svg.append(path);}
  return svg;
}
function getGroups() {
  const tracks = [...new Set(assets.map(asset => asset.track))];
  tracks.sort((a,b) => (groupOrder.indexOf(a) + 1 || 99) - (groupOrder.indexOf(b) + 1 || 99));
  return new Map(tracks.map(track => [track, assets.filter(asset => asset.track === track)]));
}
function rebuildAssets(groups) {
  const root = byId("asset-groups"); root.replaceChildren(); assetRows.clear(); groupRows.clear();
  for (const [track, members] of groups) {
    const section = node("section", undefined, "asset-group"); section.dataset.track = track;
    const head = node("div", undefined, "group-heading"), title = node("h3", trackLabel(track)), count = node("span", undefined, "group-count");
    title.id = `group-${groupRows.size}`; section.setAttribute("aria-labelledby", title.id); head.append(title, count);
    const list = node("ul", undefined, "asset-list");
    for (const asset of members) {
      const li = node("li"), button = node("button", undefined, "asset-row"), copy = node("span", undefined, "asset-copy");
      const reading = node("span", undefined, "asset-reading"), state = node("span", undefined, "asset-state");
      button.type = "button"; button.dataset.assetId = asset.asset_id;
      copy.append(node("span", label(asset), "asset-name"), reading); button.append(icon(asset.role), copy, state);
      button.addEventListener("click", () => inspect({kind:"asset", id:asset.asset_id}, button)); li.append(button); list.append(li);
      assetRows.set(asset.asset_id, {button, reading, state, li});
    }
    section.append(head, list); root.append(section); groupRows.set(track, {section, count});
  }
}
function render(snapshot, events, offline) {
  latest = snapshot; eventData = events; disconnected = Boolean(offline);
  assets = SentinelAssets.buildAssets(snapshot, {offline:disconnected}); architectureNodes = SentinelArchitecture.resolveNodes(assets);
  SentinelTopology.render(architectureNodes,assets,disconnected,true,snapshot.recovery);
  const groups = getGroups(), signature = JSON.stringify(assets.map(asset => [asset.asset_id, asset.name, asset.role, asset.track]));
  if (signature !== assetSignature) {rebuildAssets(groups); assetSignature = signature;}
  const counts = SentinelAssets.summarize(assets);
  for (const [id, value] of [["total-count",counts.total], ["healthy-count",counts.healthy], ["incident-count",counts.down+counts.degraded], ["unknown-count",counts.unknown], ["pending-count",counts.pending]]) byId(id).textContent = value;
  byId("incident-count").title = `장애 ${counts.down} · 연결 저하 ${counts.degraded} / 영향받은 자산 수`;
  byId("profile").textContent = snapshot.profile === "local" ? "LOCAL" : snapshot.profile === "local-recovery" ? "LOCAL AUTO" : snapshot.profile === "deployed" ? "DEPLOYED" : (snapshot.profile || "미확인");
  renderRecovery();
  byId("last-update").textContent = disconnected ? "Sentinel 연결 끊김" : `최근 점검 ${time(snapshot.updated_at)}`;
  byId("policy").textContent = `${Number(snapshot.poll_interval_seconds) || 15}초 간격`;
  byId("measurement-note").textContent = `직접 ${assets.filter(asset=>asset.evidence_type === "DIRECT_PROBE" && !["UNCONFIGURED", "PENDING"].includes(asset.status)).length} · 간접 ${assets.filter(asset=>asset.evidence_type === "HEALTHZ_COMPONENT").length}`;
  byId("status-caption").textContent = disconnected ? "최신 상태를 확인할 수 없어 이전 결과는 정상에서 제외" : counts.down + counts.degraded ? `장애·저하 ${counts.down+counts.degraded}개 · 자산을 눌러 원인 확인` : counts.unknown ? `상태 확인이 필요한 자산 ${counts.unknown}개` : `${counts.observed}개 자산 판정 완료 · ${counts.pending}개는 감시 연결 대기`;
  for (const asset of assets) {
    const item = assetRows.get(asset.asset_id); item.button.className = `asset-row status-${statusClass(asset.status)}`;
    item.state.textContent = statusNames[asset.status];
    item.reading.textContent = asset.evidence_type === "HEALTHZ_COMPONENT" ? "백엔드 healthz" : asset.status === "STALE" ? "점검 결과 오래됨" : ["UNCONFIGURED", "PENDING"].includes(asset.status) ? (reasonNames[asset.reason_code] || roleNotes[asset.role] || "점검 주소 미설정") : asset.http_status_code != null ? `HTTP ${asset.http_status_code}${asset.latency_ms != null ? ` · ${Math.round(asset.latency_ms)} ms` : ""}` : "응답 없음";
    item.button.setAttribute("aria-label", `${trackLabel(asset.track)} ${label(asset)} · ${statusNames[asset.status]}`);
    item.li.hidden = issuesOnly && !requiresAttention(asset);
  }
  for (const [track, members] of groups) {
    const group = groupRows.get(track), value = SentinelAssets.summarize(members), incidents = value.down+value.degraded;
    group.count.textContent = incidents ? `${incidents}개 장애·저하` : value.unknown ? `${value.unknown}개 확인 불가` : value.observed ? `${value.healthy} / ${value.total} 정상` : `미연결 ${value.pending}`;
    group.count.className = `group-count ${incidents ? "incident" : value.unknown ? "unknown" : value.observed ? "healthy" : "pending"}`;
    group.section.hidden = issuesOnly && !members.some(requiresAttention);
  }
  byId("empty-filter").hidden = !issuesOnly || assets.some(requiresAttention);
  byId("issues-only").textContent = issuesOnly ? "전체 보기" : "문제만 보기";
  byId("issues-only").setAttribute("aria-pressed", String(issuesOnly));
  showWarning(disconnected ? "Sentinel 연결 실패 · 최신 상태 확인 불가" : snapshot.event_storage_healthy === false ? "변경 기록 저장 실패 · 상태 점검은 계속 동작 중" : "");
  renderContracts(); renderEvents();
  if (byId("inspector").open) renderInspector();
}
function renderContracts() {
  const counts = {VERIFIED:0, CODE:0, BLOCKED:0, other:0};
  for (const link of catalog.links) {
    const resolved = SentinelArchitecture.resolveLink(link, assets), item = contractRows.get(link.id);
    if (Object.hasOwn(counts, resolved.state)) counts[resolved.state]++; else counts.other++;
    item.state.textContent = stageNames[resolved.state]; item.state.className = `contract-state stage-${stageClass(resolved.state)}`;
    item.row.dataset.stage = resolved.state; item.row.hidden = !matchesFilter(link);
  }
  for (const [key, id] of [["VERIFIED","verified-links"], ["CODE","code-links"], ["BLOCKED","blocked-links"], ["other","unverified-links"]]) byId(id).textContent = counts[key];
  for (const [id, item] of relatedRows) {const resolved = SentinelArchitecture.resolveLink(linkById(id), assets); item.textContent = stageNames[resolved.state]; item.className = `stage-${stageClass(resolved.state)}`;}
}
function matchesFilter(link) {
  const filter = byId("connection-filter").value;
  if (filter === "ALL") return true;
  const tracks = [nodeById(link.from)?.track, nodeById(link.to)?.track];
  return filter === "RUNTIME" ? tracks.some(track=>["RUNTIME", "SCORING"].includes(track)) : tracks.includes(filter);
}
function rebuildContracts() {
  const root = byId("contracts-list"); root.replaceChildren();
  for (const link of catalog.links) {
    const row = node("button", undefined, "contract-row"), route = node("span", undefined, "contract-route"), state = node("span", undefined, "contract-state");
    const source = catalog.nodes.find(item=>item.id === link.from), target = catalog.nodes.find(item=>item.id === link.to);
    const track = [source?.track, target?.track].find(value=>["INTERNAL", "EXTERNAL"].includes(value));
    if (track) route.append(node("small", track === "INTERNAL" ? "내부" : "외부", "contract-track"));
    route.append(node("strong", source?.name || link.from), node("span", "→", "route-arrow"), node("strong", target?.name || link.to));
    row.type = "button"; row.dataset.linkId = link.id;
    row.append(route, node("span", link.label, "contract-label"), state, node("span", link.owner, "contract-owner"));
    row.addEventListener("click", ()=>inspect({kind:"link", id:link.id}, row)); root.append(row); contractRows.set(link.id, {row, state});
  }
  byId("audit-date").textContent = `소스 대조 ${catalog.verified_at.slice(0,10)}`;
  byId("audit-scope").textContent = catalog.scope;
  byId("finding-count").textContent = catalog.findings.length;
  for (const finding of catalog.findings) {
    const button = node("button", undefined, "finding-button"); button.type = "button";
    button.append(node("span", finding.title), node("small", finding.owner));
    button.addEventListener("click", ()=>inspect({kind:"finding", id:finding.id}, button)); byId("findings-list").append(button);
  }
  byId("gate-count").textContent = catalog.gates.length;
  catalog.gates.forEach((gate, index)=>{
    const button = node("button", undefined, "gate-button"); button.type = "button";
    button.append(node("span", gate.name), node("small", gate.owner));
    button.addEventListener("click", ()=>inspect({kind:"gate", id:index}, button)); byId("gates-list").append(button);
  });
}
function eventInfo(event) {
  const asset = assets.find(item=>item.asset_id === event.target_id);
  const state = event.status === "HEALTHY" ? (event.previous_status === "DOWN" ? "복구" : "정상 확인") : event.status === "DOWN" ? "장애 확인" : (statusNames[event.status] || "상태 변경");
  const className = event.status === "HEALTHY" ? "healthy" : event.status === "DOWN" ? "incident" : "unknown";
  return {target:asset ? `${trackLabel(asset.track)} / ${label(asset)}` : event.target_id, state, className};
}
function eventRow(event) {
  const info = eventInfo(event), row = node("li", undefined, `event-row ${info.className}`), timestamp = node("time", time(event.created_at));
  timestamp.dateTime = event.created_at || ""; timestamp.title = event.created_at || "";
  row.append(timestamp, node("span", info.target, "event-target"), node("span", info.state, "event-state"), node("span", reasonNames[event.reason_code] || event.reason_code || "상태 변경", "event-reason"));
  return row;
}
function renderEvents() {
  const visible = eventData.slice(0, expandedEvents ? 200 : 3), signature = JSON.stringify([visible, assets.map(asset=>[asset.asset_id,asset.name,asset.track]), expandedEvents]);
  if (signature !== eventSignature) {const feed = byId("event-feed"); feed.replaceChildren(); visible.forEach(event=>feed.append(eventRow(event))); if (!visible.length) feed.append(node("li", "기록된 상태 변경 없음", "empty-message")); eventSignature = signature;}
  byId("event-expand").textContent = expandedEvents ? "접기" : `전체 기록${eventData.length ? ` ${eventData.length}` : ""}`;
  byId("event-expand").setAttribute("aria-expanded", String(expandedEvents));
}
function metric(name, value, id) {
  const box = node("div"), dd = node("dd", value); box.append(node("dt", name), dd); byId("detail-metrics").append(box);
  if (id) detailFields.set(id, dd);
}
function sourceAnchor(key, path) {
  const source = SentinelArchitecture.sourceLink(key, path); if (!source) return;
  const anchor = node("a", `${source.name} · ${source.version} ↗`); anchor.href = source.url; anchor.target = "_blank"; anchor.rel = "noopener noreferrer"; byId("detail-links").append(anchor);
}
function inspect(next, button) {
  const dialog = byId("inspector"); const wasOpen = dialog.open;
  if (!wasOpen) opener = button || document.activeElement;
  selection = next; detailFields.clear(); relatedRows.clear(); detailEventSignature = "";
  byId("detail-metrics").replaceChildren(); byId("detail-links").replaceChildren(); byId("related-links").replaceChildren(); byId("detail-icon").replaceChildren();
  byId("related-section").open = false; byId("related-section").hidden = true; byId("detail-events-section").hidden = true;
  if (next.kind === "asset") {
    const definition = nodeById(next.id), asset = definition?.reading;
    if (!definition) return;
    byId("detail-group").textContent = trackLabel(definition.track); byId("detail-title").textContent = asset ? label(asset) : definition.name; byId("detail-icon").append(icon(definition.role));
    if (asset || definition.probe_id) {
      metric("점검 시각", "대기", "checked"); metric("HTTP", "미확인", "http"); metric("응답 시간", "미확인", "latency"); metric("점검 방식", asset ? asset.evidence_type === "HEALTHZ_COMPONENT" ? "백엔드 healthz" : "직접 HTTP 점검" : "점검 결과 대기", "method");
      byId("detail-source").textContent = asset?.source_url || "점검 주소 미확인";
      byId("detail-source-note").textContent = asset?.evidence_type === "HEALTHZ_COMPONENT" ? "백엔드의 연결 결과 · 이 DB나 Redis를 따로 직접 점검한 것은 아님" : "API 응답 점검 · VM 전원이나 문제 풀이 기능 전체 판정은 아님";
    } else {
      metric("담당",definition.owner); metric("구현 근거",definition.stage === "CODE" ? "고정 SHA 소스 확인" : "운영 배치안");
      metric("실행 상태", "미확인"); metric("소스 대조",sourceDate(definition.source));
      byId("detail-source").textContent = definition.source === "notion" ? "운영 아키텍처 본문" : "고정 SHA 기준 소스";
      byId("detail-source-note").textContent = "구조에 포함된 역할 · 실제 점검 미연결이며 정상 자산 수에 포함하지 않음";
    }
    sourceAnchor(definition.source, definition.path);
    const links = catalog.links.filter(link=>link.from === next.id || link.to === next.id);
    if (links.length) {byId("related-section").hidden = false; byId("related-title").textContent = `연결 관계 ${links.length}`;
      for (const link of links) {const button = node("button", undefined, "related-link"), state = node("span"); button.type = "button";
        button.append(node("span", `${nodeById(link.from)?.name} → ${nodeById(link.to)?.name} / ${link.label}`), state);
        button.addEventListener("click", ()=>inspect({kind:"link",id:link.id},button)); byId("related-links").append(button); relatedRows.set(link.id,state);}
    }
    byId("detail-events-section").hidden = !asset;
    byId("detail-events-title").textContent = asset?.parent_target_id ? "백엔드 상태 변경 (간접 점검)" : "이 자산의 상태 변경";
  } else if (next.kind === "link" || next.kind === "finding") {
    const finding = next.kind === "finding" ? catalog.findings.find(item=>item.id === next.id) : null;
    if (finding?.url) {
      byId("detail-group").textContent = trackLabel(finding.track); byId("detail-title").textContent = finding.title; byId("detail-icon").append(icon("LINK"));
      metric("확인 시각", `${finding.checked_at.slice(0,10)} ${time(finding.checked_at)}`); metric("HTTP", "200 · HTML");
      metric("트랙", "미확정"); metric("백엔드 상태", "확인 불가");
      byId("detail-source").textContent = finding.url;
      byId("detail-source-note").textContent = "읽기 전용 조회 결과 · 내부·외부 트랙 배정과 배포 SHA는 미확인";
      const anchor = node("a", "점검 주소 ↗"); anchor.href = finding.url; anchor.target = "_blank"; anchor.rel = "noopener noreferrer"; byId("detail-links").append(anchor);
    } else {
    const link = linkById(finding ? finding.link : next.id); if (!link) return;
    byId("detail-group").textContent = link.owner; byId("detail-title").textContent = finding ? finding.title : link.label; byId("detail-icon").append(icon("LINK"));
    metric("출발", nodeById(link.from)?.name || link.from); metric("도착", nodeById(link.to)?.name || link.to); metric("확인 시각", "", "checked"); metric("실행 검증", "", "execution");
    byId("detail-source").textContent = `${trackLabel(nodeById(link.from)?.track)} · ${nodeById(link.from)?.name} → ${nodeById(link.to)?.name}`;
    byId("detail-source-note").textContent = catalog.scope;
    sourceAnchor(link.source, link.path); if (link.peer_source) sourceAnchor(link.peer_source, link.peer_path);
    }
  } else if (next.kind === "context") {
    const observer = next.id === "observer";
    byId("detail-group").textContent = observer ? "SLA 관측" : "대회 진입"; byId("detail-title").textContent = observer ? "SLA / Sentinel" : "참가자 접속"; byId("detail-icon").append(icon(observer ? "MONITOR" : "USER"));
    byId("detail-status").className = "detail-status status-pending"; byId("detail-status").textContent = observer ? latest?.recovery?.enabled ? "로컬 자동 복구" : "관측 전용" : "접속 경로";
    byId("detail-reason").textContent = observer ? "설정한 API 점검과 백엔드 DB·Redis 결과 수집 · 로컬 실행 자산을 연결하면 Runtime·K3s 관측과 Runtime 정지 복구도 포함" : "참가자는 HTTPS로 해당 트랙에 접속 · 내부·외부 앱과 데이터를 분리하며 다른 트랙으로 자동 전환하지 않음";
    metric("역할",observer ? "상태 수집 · 변경 기록" : "브라우저 접속"); metric("확인 범위",observer ? "현재 프로필의 점검 대상" : "운영 배치안");
    byId("detail-source").textContent = observer ? "GET /api/status · /api/events · /metrics" : "공통 Edge와 트랙별 도메인";
    byId("detail-source-note").textContent = observer ? "SLA가 API에 요청하는 흐름은 workload 생성 경로와 분리 · 소스 확인 구성은 정상으로 집계하지 않음" : "현재 로컬에서는 Edge·Frontend 경유 접속을 실행하지 않음"; sourceAnchor("notion");
  } else if (next.kind === "gate") {
    const gate = catalog.gates[next.id]; if (!gate) return;
    byId("detail-group").textContent = gate.owner; byId("detail-title").textContent = gate.name; byId("detail-icon").append(icon("LINK"));
    byId("detail-status").className = "detail-status status-pending"; byId("detail-status").textContent = "실행 미검증"; byId("detail-reason").textContent = gate.note;
    byId("detail-metrics").hidden = true; byId("detail-source").textContent = "운영 아키텍처와 고정 SHA 소스 기준"; byId("detail-source-note").textContent = "운영 자동 복구는 미연결 · 로컬 실행은 현재 프로필에서 허용한 앱의 정지 컨테이너 기동"; sourceAnchor("notion");
  }
  byId("detail-metrics").hidden = next.kind === "gate";
  if (!wasOpen) {dialog.showModal(); document.body.classList.add("dialog-open");} else byId("detail-close").focus();
  renderInspector(); renderContracts();
}
function renderInspector() {
  renderAccessDetail();
  if (!selection) return;
  if (selection.kind === "asset") {
    const definition = nodeById(selection.id), asset = assets.find(item=>item.asset_id === selection.id);
    if (!asset && definition && !definition.probe_id) {
      byId("detail-status").className = "detail-status stage-design"; byId("detail-status").textContent = definition.stage === "CODE" ? "소스 확인 · 실행 미확인" : "설계 · 실행 미확인";
      byId("detail-reason").textContent = definition.note || "점검 연결 전 · 실제 실행 상태는 아직 확인하지 않음"; return;
    }
    if (!asset) {byId("detail-status").className = "detail-status status-unknown"; byId("detail-status").textContent = latest ? "현재 프로필에서 제외됨" : "점검 결과 대기"; byId("detail-reason").textContent = latest ? "선택한 자산은 더 이상 점검 대상 목록에 없어" : "첫 상태 응답을 아직 받지 못함"; for (const field of detailFields.values()) field.textContent = "미확인"; return;}
    byId("detail-status").className = `detail-status status-${statusClass(asset.status)}`; byId("detail-status").textContent = statusNames[asset.status];
    byId("detail-title").textContent = label(asset); byId("detail-group").textContent = trackLabel(asset.track);
    byId("detail-reason").textContent = [reasonNames[asset.reason_code] || "점검 결과 확인 필요", asset.evidence_scope].filter(Boolean).join(" / ");
    byId("detail-source").textContent = asset.source_url || "점검 주소 미설정";
    detailFields.get("checked").textContent = time(asset.checked_at); detailFields.get("http").textContent = asset.http_status_code != null ? asset.http_status_code : "해당 없음";
    detailFields.get("latency").textContent = asset.latency_ms != null ? `${Math.round(asset.latency_ms)} ms` : "해당 없음";
    detailFields.get("method").textContent = asset.evidence_type === "HEALTHZ_COMPONENT" ? "백엔드 healthz" : "직접 HTTP 점검";
    byId("detail-source-note").textContent = asset.evidence_type === "HEALTHZ_COMPONENT" ? "백엔드의 연결 결과 · 이 DB나 Redis를 따로 직접 점검한 것은 아님" : "API 응답 점검 · VM 전원이나 문제 풀이 기능 전체 판정은 아님";
    byId("detail-events-section").hidden = false; byId("detail-events-title").textContent = asset.parent_target_id ? "백엔드 상태 변경 (간접 점검)" : "이 자산의 상태 변경";
    const events = eventData.filter(event=>event.target_id === (asset.parent_target_id || asset.asset_id)).slice(0,8), signature = JSON.stringify(events);
    if (signature !== detailEventSignature) {const feed = byId("detail-events"); feed.replaceChildren(); events.forEach(event=>feed.append(eventRow(event))); if (!events.length) feed.append(node("li", ["UNCONFIGURED", "PENDING"].includes(asset.status) ? "점검 미연결 · 변경 기록 없음" : "기록된 상태 변경 없음", "empty-message")); detailEventSignature = signature;}
  } else if (selection.kind === "link" || selection.kind === "finding") {
    const finding = selection.kind === "finding" ? catalog.findings.find(item=>item.id === selection.id) : null;
    if (finding?.url) {
      byId("detail-status").className = "detail-status status-unknown"; byId("detail-status").textContent = "확인 불가";
      byId("detail-reason").textContent = finding.detail; return;
    }
    const link = linkById(finding ? finding.link : selection.id), result = SentinelArchitecture.resolveLink(link, assets);
    const routeFinding = finding?.id === "route";
    byId("detail-status").className = `detail-status stage-${stageClass(routeFinding ? "BLOCKED" : result.state)}`;
    byId("detail-status").textContent = routeFinding ? "불일치" : stageNames[result.state];
    byId("detail-reason").textContent = routeFinding ? finding.detail : finding ? `${finding.detail} · ${link.note}` : link.note;
    detailFields.get("checked").textContent = result.checked_at ? time(result.checked_at) : linkDates(link);
    detailFields.get("execution").textContent = result.verified ? "연결 점검 확인" : result.state === "FAILED" ? "연결 실패 확인" : "미검증";
  }
}
function switchView(next) {
  view = next;
  for (const key of viewKeys) {const active = key === next; byId(`${key}-view`).hidden = !active; byId(`${key}-tab`).setAttribute("aria-selected", String(active)); byId(`${key}-tab`).tabIndex = active ? 0 : -1;}
  byId("page-title").textContent = {architecture:"MSG CTF 운영 구조",status:"플랫폼 자산 상태",connections:"역할별 연결 검토"}[next];
  if (next === "architecture") requestAnimationFrame(SentinelTopology.fit);
}
function showWarning(text) {byId("connection-warning").textContent = text; byId("connection-warning").hidden = !text;}
async function refresh() {
  if (inFlight) return;
  inFlight = true; byId("refresh").disabled = true;
  try {
    const responses = await Promise.all([fetch("/api/status", {cache:"no-store",signal:AbortSignal.timeout(5000)}), fetch("/api/events", {cache:"no-store",signal:AbortSignal.timeout(5000)})]);
    if (responses.some(response=>!response.ok)) throw new Error("HTTP_ERROR");
    const [snapshot,feed] = await Promise.all(responses.map(response=>response.json()));
    if (!Array.isArray(snapshot.targets) || !Array.isArray(feed.events)) throw new Error("INVALID_SNAPSHOT");
    render(snapshot,feed.events,false);
  } catch {
    if (latest) render(latest,eventData,true);
    else {showWarning("Sentinel 연결 실패 · 점검 결과를 가져오지 못함"); byId("status-caption").textContent = "점검 결과를 가져오지 못함"; byId("last-update").textContent = "연결 실패";}
  } finally {
    await SentinelDataRecovery.refresh(disconnected || !latest, latest?.profile);
    if (selection) renderAccessDetail();
    inFlight = false; byId("refresh").disabled = false;
  }
}
byId("refresh").addEventListener("click", refresh);
byId("issues-only").addEventListener("click", ()=>{issuesOnly = !issuesOnly; if (latest) render(latest,eventData,disconnected);});
byId("event-expand").addEventListener("click", ()=>{expandedEvents = !expandedEvents; renderEvents();});
byId("connection-filter").addEventListener("change", renderContracts);
byId("detail-close").addEventListener("click", ()=>byId("inspector").close());
byId("inspector").addEventListener("click", event=>{if (event.target !== byId("inspector")) return; const box = byId("inspector").getBoundingClientRect(); if (event.clientX < box.left || event.clientX > box.right || event.clientY < box.top || event.clientY > box.bottom) byId("inspector").close();});
byId("inspector").addEventListener("close", ()=>{document.body.classList.remove("dialog-open"); selection = null; if (opener?.isConnected && !opener.closest("[hidden]")) opener.focus(); opener = null;});
for (const key of viewKeys) {
  const tab = byId(`${key}-tab`); tab.addEventListener("click", ()=>switchView(key));
  tab.addEventListener("keydown", event=>{if (["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) {event.preventDefault(); const index = viewKeys.indexOf(key), next = event.key === "Home" ? viewKeys[0] : event.key === "End" ? viewKeys.at(-1) : viewKeys[(index + (event.key === "ArrowRight" ? 1 : -1) + viewKeys.length) % viewKeys.length]; switchView(next); byId(`${next}-tab`).focus();}});
}
document.addEventListener("visibilitychange", ()=>{if (!document.hidden) refresh();});
setInterval(()=>{if (!document.hidden) refresh();},5000);
architectureNodes = SentinelArchitecture.resolveNodes([]);
rebuildContracts();
SentinelTopology.mount({catalog,icon,inspect,statusNames,stageNames,statusClass,stageClass,trackLabel});
SentinelTopology.render(architectureNodes,[],true,false);
loadAccessPlan();
refresh();
