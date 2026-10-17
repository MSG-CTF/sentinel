(function (root) {
  "use strict";
  const byId = id => document.getElementById(id);
  const make = (tag, text, cls) => {const item = document.createElement(tag); if (text !== undefined) item.textContent = text; if (cls) item.className = cls; return item;};
  const text = (id, value) => {const item = byId(id); if (item.textContent !== value) item.textContent = value;};
  const rank = {CRITICAL:0, WARNING:1, UNKNOWN:2, NORMAL:3, PENDING:4};
  let riskFilter = "ALL", search = "", settings, mapSignature = "";
  const mapNodes = new Map();
  function matches(asset) {
    return (riskFilter === "ALL" || asset.risk.level === riskFilter) &&
      `${asset.name} ${asset.asset_id} ${settings.trackLabel(asset.track)}`.toLocaleLowerCase().includes(search);
  }
  function sort(assets) { return [...assets].sort((a,b) => rank[a.risk.level] - rank[b.risk.level]); }
  function tabSet(keys, suffix, change) {
    function select(next) {
      for (const key of keys) {const button = byId(`${key}${suffix}`), active = key === next; button.setAttribute("aria-selected",String(active)); button.tabIndex = active ? 0 : -1; byId(key).hidden = !active;}
      change?.(next);
    }
    for (const key of keys) {
      const button = byId(`${key}${suffix}`);
      button.addEventListener("click", () => select(key));
      button.addEventListener("keydown", event => {
        if (!["ArrowLeft","ArrowRight","Home","End"].includes(event.key)) return;
        event.preventDefault();
        const next = event.key === "Home" ? keys[0] : event.key === "End" ? keys.at(-1) : keys[(keys.indexOf(key)+(event.key === "ArrowRight" ? 1 : -1)+keys.length)%keys.length];
        select(next); byId(`${next}${suffix}`).focus();
      });
    }
  }
  function mount(options) {
    settings = options;
    for (const button of document.querySelectorAll("[data-risk-filter]")) button.addEventListener("click", () => {
      riskFilter = riskFilter === button.dataset.riskFilter ? "ALL" : button.dataset.riskFilter;
      settings.showAssets(); settings.changed();
    });
    byId("asset-filter-clear").addEventListener("click", () => {riskFilter = "ALL"; search = ""; byId("asset-search").value = ""; settings.changed();});
    byId("asset-search").addEventListener("input", event => {search = event.target.value.trim().toLocaleLowerCase(); settings.changed();});
    tabSet(["instances-pane","recovery-pane","events-pane"],"-tab");
    tabSet(["deployment-map","design-map"],"-tab", next => {if (next === "design-map") requestAnimationFrame(SentinelTopology.fit);});
    byId("risk-policy-open").addEventListener("click", () => byId("risk-policy-dialog").showModal());
    byId("risk-policy-close").addEventListener("click", () => byId("risk-policy-dialog").close());
  }
  function renderMap(assets) {
    const signature = JSON.stringify(assets.map(a => [a.asset_id,a.track,a.name]));
    if (signature !== mapSignature) {
      mapSignature = signature; mapNodes.clear(); byId("deployment-map").replaceChildren();
      const tracks = [...new Set(assets.map(a => a.track))];
      for (const track of tracks) {
        const section = make("section",undefined,"deployment-zone"), head = make("div",undefined,"deployment-zone-heading");
        head.append(make("h2",settings.trackLabel(track)),make("span",`${assets.filter(a=>a.track === track).length}개 자산`)); section.append(head);
        for (const asset of assets.filter(a=>a.track === track)) {
          const button = make("button",undefined,"deployment-node"); button.type = "button"; button.dataset.assetId = asset.asset_id;
          const copy = make("span",undefined,"deployment-node-copy"), state = make("span",undefined,"deployment-node-state"), note = make("small");
          copy.append(make("strong",asset.name),note); button.append(settings.icon(asset.role),copy,state);
          button.addEventListener("click", () => settings.inspect({kind:"asset",id:asset.asset_id},button));
          section.append(button); mapNodes.set(asset.asset_id,{button,state,note});
        }
        section.append(make("p", "자산 선택으로 점검 근거 확인", "deployment-zone-note")); byId("deployment-map").append(section);
      }
    }
    for (const asset of assets) {
      const item = mapNodes.get(asset.asset_id);
      item.button.dataset.level = asset.risk.level; item.state.textContent = SentinelAssets.riskNames[asset.risk.level];
      item.note.textContent = SentinelAssets.riskText(asset) || (asset.evidence_type === "HEALTHZ_COMPONENT" ? "백엔드 연결 점검" : asset.kind === "service_heartbeat" ? "서버 상태 보고" : asset.latency_ms != null && !["UNKNOWN","PENDING"].includes(asset.risk.level) ? `HTTP ${asset.http_status_code} · ${Math.round(asset.latency_ms)} ms` : settings.reason(asset));
      item.button.setAttribute("aria-label",`${settings.trackLabel(asset.track)} ${asset.name} · ${item.state.textContent}`);
    }
  }
  function render(snapshot, offline, assets) {
    const counts = SentinelAssets.riskCounts(assets), ordered = sort(assets), first = ordered.find(a => ["CRITICAL","WARNING"].includes(a.risk.level));
    const level = offline ? "UNKNOWN" : counts.CRITICAL ? "CRITICAL" : counts.WARNING ? "WARNING" : counts.UNKNOWN || !assets.length ? "UNKNOWN" : counts.NORMAL ? "NORMAL" : "PENDING";
    byId("risk-ribbon").dataset.level = level;
    text("risk-symbol", ["CRITICAL","WARNING"].includes(level) ? "!" : "○");
    text("risk-title", offline ? "최신 상태 확인 불가" : counts.CRITICAL ? `위험 ${counts.CRITICAL}개 · 조치 필요` : counts.WARNING ? `주의 ${counts.WARNING}개 · 수치 상승` : !assets.length ? "점검 결과 대기" : "확인된 위험 없음");
    text("risk-description", offline ? "최신 수집이 확인되지 않아 이전 수치의 판정을 중지했습니다" : first ? `${first.name} · ${SentinelAssets.riskText(first) || settings.reason(first)}` : `미확인 ${counts.UNKNOWN} · 미연결 ${counts.PENDING}${counts.UNKNOWN ? " · 정상 판정에서 제외" : ""}`);
    byId("asset-filter-note").hidden = riskFilter === "ALL" && !search;
    byId("asset-filter-label").textContent = `${riskFilter === "ALL" ? "전체 상태" : SentinelAssets.riskNames[riskFilter]}${search ? ` · ${search}` : ""}`;
    for (const button of document.querySelectorAll("[data-risk-filter]")) button.setAttribute("aria-pressed",String(button.dataset.riskFilter === riskFilter));
    renderMap(assets);
    const policy = snapshot.risk_policy;
    const table = byId("risk-policy-table"); table.replaceChildren();
    if (!policy) {table.append(make("p","서버의 경고 기준을 아직 받지 못했습니다")); return;}
    const header = make("div",undefined,"policy-row policy-head"); for (const text of ["지표","주의 ≥","위험 ≥"]) header.append(make("span",text)); table.append(header);
    for (const [key,unit] of [["latency_ms","ms"],["cpu_used_percent","%"],["memory_used_percent","%"],["disk_used_percent","%"]]) {
      const value = policy[key]; if (!value) continue;
      const row = make("div",undefined,"policy-row"); row.append(make("strong",SentinelAssets.metricNames[key]), make("span",`${value.warning}${unit}`), make("span",`${value.critical}${unit}`)); table.append(row);
      const note = make("p",`해제: 주의 ${value.warning-value.clear_margin}${unit} 미만 · 위험 ${value.critical-value.clear_margin}${unit} 미만`,"policy-clear"); table.append(note);
    }
    byId("risk-policy-description").textContent = `기준 도달 시 즉시 표시 · 해제 기준 아래의 새 측정 ${policy.clear_samples}회 연속 확인 후 해제 · 같은 보고의 재조회는 제외`;
  }
  function inspector(asset) {
    root.SentinelAssetOps?.render(asset);
    const panel = byId("detail-risk"); panel.replaceChildren(); panel.hidden = !asset?.risk?.signals?.length;
    if (panel.hidden) return;
    panel.append(make("h3","측정값과 경고 기준"));
    for (const signal of asset.risk.signals) {
      const row = make("div",undefined,"detail-risk-row"); row.dataset.level = signal.level;
      row.append(make("strong",`${SentinelAssets.metricNames[signal.metric]} · ${Number(signal.value.toFixed(1))}${signal.unit}`), make("span",`${SentinelAssets.riskNames[signal.level]}${signal.recovering ? " · 회복 확인 중" : ""}`), make("small",`주의 ≥${signal.warning}${signal.unit} / 위험 ≥${signal.critical}${signal.unit}${signal.recovering ? ` · ${signal.clear_below}${signal.unit} 미만 연속 확인 필요` : ""}`)); panel.append(row);
    }
  }
  root.SentinelConsole = {mount,render,inspector,matches,sort};
})(globalThis);
