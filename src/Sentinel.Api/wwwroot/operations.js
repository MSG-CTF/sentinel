(function (root) {
  "use strict";
  const byId = id => document.getElementById(id);
  let lastHistoryAt = null;
  let historyBusy = false;
  let historyOffline = false;
  const formatTime = value => new Date(value).toLocaleTimeString("ko-KR", {hour:"2-digit", minute:"2-digit", hour12:false});
  function clearChart(message) {
    byId("latency-path").setAttribute("d", "");
    byId("latency-point").setAttribute("visibility", "hidden");
    byId("chart-note").textContent = message;
    byId("latency-current").textContent = "—";
  }
  async function refreshHistory(snapshot) {
    if (historyBusy || !snapshot.updated_at || snapshot.updated_at === lastHistoryAt) return;
    historyBusy = true;
    try {
      const response = await fetch("/api/history", {cache:"no-store", signal:AbortSignal.timeout(5000)});
      if (response.status === 401) { location.replace("/login"); return; }
      if (!response.ok) throw new Error("HISTORY_UNAVAILABLE");
      const {samples} = await response.json();
      if (historyOffline) return;
      const values = samples.map(sample => {
        const measurements = sample.targets.filter(t => t.latency_ms != null && Number.isFinite(t.latency_ms) && t.status !== "STALE");
        return {at:sample.checked_at, value:measurements.length ? measurements.reduce((sum,t) => sum + t.latency_ms, 0) / measurements.length : null};
      });
      const measured = values.filter(sample => sample.value != null);
      if (!measured.length) { clearChart("첫 번째 HTTP 점검을 기다리는 중"); return; }
      const maximum = Math.max(100, ...measured.map(sample => sample.value)) * 1.15;
      const width = 760, height = 102;
      let path = "", gap = true;
      for (let i = 0; i < values.length; i++) {
        if (values[i].value == null) { gap = true; continue; }
        const x = values.length > 1 ? i / (values.length - 1) * width : width / 2;
        const y = height - values[i].value / maximum * height + 8;
        path += `${gap ? "M" : "L"}${x.toFixed(1)} ${y.toFixed(1)} `;
        gap = false;
      }
      byId("latency-path").setAttribute("d", path);
      byId("latency-point").setAttribute("cx", values.length === 1 ? "380" : "760");
      byId("latency-point").setAttribute("cy", String(height - measured.at(-1).value / maximum * height + 8));
      byId("latency-point").setAttribute("visibility", values.at(-1).value == null ? "hidden" : "visible");
      byId("latency-current").textContent = values.at(-1).value == null ? "—" : Math.round(values.at(-1).value).toLocaleString();
      byId("chart-start").textContent = formatTime(values[0].at);
      byId("chart-end").textContent = formatTime(values.at(-1).at);
      byId("chart-note").textContent = `HTTP 감시 대상 평균 · ${values.length}회 실측`;
      byId("latency-chart").setAttribute("aria-label", `최근 ${values.length}회 HTTP 응답 시간 · 마지막 평균 ${Math.round(measured.at(-1).value)}밀리초`);
      lastHistoryAt = snapshot.updated_at;
    } catch { clearChart("응답 시간 기록을 가져오지 못했습니다"); }
    finally { historyBusy = false; }
  }
  function render(snapshot, offline, assets) {
    historyOffline = offline;
    const counts = SentinelAssets.summarize(assets), risks = SentinelAssets.riskCounts(assets);
    const state = offline ? "offline" : risks.CRITICAL ? "incident" : risks.WARNING ? "attention" : risks.UNKNOWN ? "offline" : risks.NORMAL ? "healthy" : "standby";
    byId("core-healthy").textContent = risks.NORMAL;
    byId("core-total").textContent = counts.total;
    byId("core-condition").textContent = offline ? "연결 끊김 · 최신 상태 미확인" : !counts.total ? "등록된 자산 없음" : risks.CRITICAL ? `위험 ${risks.CRITICAL}개 · 즉시 확인` : risks.WARNING ? `주의 ${risks.WARNING}개 · 수치 상승` : counts.unknown ? `${counts.unknown}개 자산 상태 확인 필요` : counts.pending ? `${counts.pending}개 자산 연결 대기` : "연결된 자산 정상";
    byId("operations-core").closest(".core-panel").dataset.state = state;
    SentinelCore.setState(byId("operations-core"), state);
    byId("session-label").textContent = offline ? "DATA UNAVAILABLE" : "OBSERVATION ACTIVE";
    byId("session-label").classList.toggle("offline", offline);
    byId("environment-label").textContent = snapshot.profile === "aws" ? "AWS · SEOUL" : "LOCAL ENVIRONMENT";
    const bot = assets.find(target => target.asset_id === "discord-bot");
    const fresh = bot && !offline && !["UNKNOWN","PENDING"].includes(bot.risk.level);
    const parts = bot?.components || {};
    byId("bot-status").textContent = offline || bot?.status === "STALE" ? "수신 지연" : bot?.status === "HEALTHY" ? "서비스 실행 중" : bot?.status === "DOWN" ? "서비스 중지" : "연결 대기";
    byId("bot-status").className = !offline && bot?.status === "HEALTHY" ? "telemetry-state healthy" : "telemetry-state";
    byId("bot-memory").textContent = fresh && parts.memory_used_percent != null ? `${parts.memory_used_percent}%` : "—";
    byId("bot-disk").textContent = fresh && parts.disk_used_percent != null ? `${parts.disk_used_percent}%` : "—";
    byId("bot-restarts").textContent = fresh ? parts.restart_count ?? "—" : "—";
    for (const key of ["memory", "disk"]) {
      const value = Number.parseFloat(parts[`${key}_used_percent`]);
      const meter = byId(`bot-${key}-meter`);
      meter.hidden = !fresh || !Number.isFinite(value);
      const signal = bot?.risk.signals.find(s=>s.metric === `${key}_used_percent`);
      byId(`bot-${key}`).parentElement.dataset.level = fresh ? signal?.level || "UNKNOWN" : "UNKNOWN";
      meter.dataset.level = fresh ? signal?.level || "UNKNOWN" : "UNKNOWN";
      if (!meter.hidden) meter.value = Math.max(0, Math.min(100, value));
    }
    byId("bot-seen").textContent = bot?.checked_at ? `${formatTime(bot.checked_at)} 수신 · 1분 보고 · Discord 연결 별도 확인` : "서버의 첫 상태 보고를 기다리는 중";
    byId("bot-status").dataset.level = bot?.risk.level || "UNKNOWN";
    if (fresh && ["CRITICAL","WARNING"].includes(bot.risk.level)) byId("bot-status").textContent = SentinelAssets.riskNames[bot.risk.level];
    if (offline) clearChart("연결이 끊겨 최신 응답 시간을 확인할 수 없습니다");
    else refreshHistory(snapshot);
    // Local recovery evidence is retained in the local profile only.
    document.querySelectorAll(".data-recovery-panel").forEach(panel => panel.hidden = snapshot.profile === "aws");
  }
  document.addEventListener("DOMContentLoaded", () => {
    byId("core-map").addEventListener("click", () => {byId("architecture-tab").click(); byId("architecture-tab").focus();});
    fetch("/auth/session", {cache:"no-store", signal:AbortSignal.timeout(5000)})
      .then(response => response.ok ? response.json() : null)
      .then(session => { if (session?.authentication_enabled === false) byId("logout").hidden = true; })
      .catch(() => {});
    byId("logout").addEventListener("click", async () => {
      const button = byId("logout"); button.disabled = true;
      try {
        const csrf = await fetch("/auth/csrf", {cache:"no-store", signal:AbortSignal.timeout(5000)});
        const token = (await csrf.json()).request_token;
        const response = await fetch("/auth/logout", {method:"POST",headers:{"X-CSRF-TOKEN":token},signal:AbortSignal.timeout(5000)});
        if (response.ok || response.status === 401) location.replace("/login");
        else throw new Error("LOGOUT_FAILED");
      } catch { button.textContent = "다시 로그아웃"; button.disabled = false; }
    });
  });
  root.SentinelOperations = {render};
})(globalThis);
