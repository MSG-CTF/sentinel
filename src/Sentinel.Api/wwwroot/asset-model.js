/* The map describes probe evidence, not VM power state or live network discovery. */
(function (root) {
  "use strict";
  const knownStatuses = new Set(["HEALTHY", "DOWN", "DEGRADED", "UNVERIFIED", "UNCONFIGURED", "PENDING", "STALE"]);
  const dependencyReasons = new Set(["DATABASE_DOWN", "CACHE_DOWN", "DATABASE_AND_CACHE_DOWN"]);
  function trustedComponents(target) {
    const parts = target.components || {};
    if (![parts.status, parts.database, parts.cache].every(value => value === "ok" || value === "down") || target.kind !== "backend_healthz") return false;
    const ready = parts.database === "ok" && parts.cache === "ok";
    return ready
      ? target.status === "HEALTHY" && target.http_status_code === 200 && parts.status === "ok" && target.reason_code === "BACKEND_READY"
      : target.status === "DOWN" && target.http_status_code === 503 && parts.status === "down" && dependencyReasons.has(target.reason_code)
        && target.reason_code === (parts.database === "down" && parts.cache === "down" ? "DATABASE_AND_CACHE_DOWN" : parts.database === "down" ? "DATABASE_DOWN" : "CACHE_DOWN");
  }
  function buildAssets(snapshot, options) {
    const settings = options || {};
    const now = settings.now === undefined ? Date.now() : settings.now;
    // The API has a tighter deadline. This guards frozen snapshots using the maximum probe timeout.
    const maxAge = (Number(snapshot.stale_after_seconds) || 2 * (Number(snapshot.poll_interval_seconds) || 15) + 30 + 5) * 1000;
    const updated = Date.parse(snapshot.updated_at);
    const frozen = Number.isFinite(updated) && (now - updated > maxAge || updated - now > 30000);
    const assets = [];
    for (const original of snapshot.targets || []) {
      if (!original || typeof original.target_id !== "string") continue;
      const target = { ...original };
      target.status = knownStatuses.has(target.status) ? target.status : "UNVERIFIED";
      if (target.enabled === false) {
        target.status = "UNCONFIGURED"; target.checked_at = null; target.http_status_code = null; target.latency_ms = null;
      } else if (!["UNCONFIGURED", "PENDING"].includes(target.status)) {
        const checked = Date.parse(target.checked_at);
        if (settings.offline || frozen || !Number.isFinite(checked) || now - checked > (target.kind === "service_heartbeat" ? 150000 : maxAge) || checked - now > 30000) {
          target.status = "STALE"; target.reason_code = "MONITOR_REFRESH_STALE";
        }
      }
      const hasComponents = trustedComponents(target);
      if (target.kind === "backend_healthz" && !hasComponents &&
          (target.status === "HEALTHY" || target.status === "DOWN" && dependencyReasons.has(target.reason_code))) {
        target.status = "UNVERIFIED";
        target.reason_code = "INVALID_HEALTH_CONTRACT";
      }
      const status = hasComponents && target.status === "DOWN" ? "DEGRADED" : target.status;
      assets.push({asset_id:target.target_id, parent_target_id:null, name:target.name, role:target.role, track:target.track,
        kind:target.kind, status, reason_code:target.reason_code, checked_at:target.checked_at,
        http_status_code:target.http_status_code, latency_ms:target.latency_ms, source_url:target.url || null,
        evidence_type:"DIRECT_PROBE", enabled:target.enabled, evidence_scope:target.evidence_scope || null,
        components:target.components || {}, risk:resolveRisk(status, target.risk)});
      if (target.kind !== "backend_healthz") continue;
      for (const [key, role, name] of [["database", "POSTGRESQL", "PostgreSQL"], ["cache", "REDIS", "Redis"]]) {
        let childStatus = ["UNCONFIGURED", "PENDING", "STALE"].includes(target.status) ? target.status : "UNVERIFIED";
        if (hasComponents) childStatus = target.components[key] === "ok" ? "HEALTHY" : "DOWN";
        const reason = hasComponents ? `${role}_${childStatus === "HEALTHY" ? "READY" : "DOWN"}`
          : childStatus === "UNCONFIGURED" ? "ENDPOINT_NOT_CONFIGURED" : childStatus === "PENDING" ? "NOT_CHECKED_YET"
          : childStatus === "STALE" ? "MONITOR_REFRESH_STALE" : "PARENT_HEALTH_UNVERIFIED";
        assets.push({asset_id:`${target.target_id}-${key}`, parent_target_id:target.target_id, name, role,
          track:target.track, kind:"healthz_component", status:childStatus, reason_code:reason,
          checked_at:hasComponents ? target.checked_at : null, http_status_code:null, latency_ms:null,
          source_url:target.url || null, evidence_type:"HEALTHZ_COMPONENT", enabled:target.enabled, risk:resolveRisk(childStatus)});
      }
    }
    return assets;
  }
  function summarize(assets) {
    const counts = {total:assets.length, healthy:0, down:0, degraded:0, unknown:0, pending:0, observed:0};
    for (const asset of assets) {
      if (asset.status === "HEALTHY") counts.healthy++;
      else if (asset.status === "DOWN") counts.down++;
      else if (asset.status === "DEGRADED") counts.degraded++;
      else if (["UNCONFIGURED", "PENDING"].includes(asset.status)) counts.pending++;
      else counts.unknown++;
    }
    counts.observed = counts.healthy + counts.down + counts.degraded;
    counts.coverage_percent = counts.total ? Math.round(counts.observed / counts.total * 1000) / 10 : null;
    counts.healthy_percent = counts.observed ? Math.round(counts.healthy / counts.observed * 1000) / 10 : null;
    return counts;
  }
  const riskNames = {NORMAL:"정상", WARNING:"주의", CRITICAL:"위험", UNKNOWN:"미확인", PENDING:"미연결"};
  const metricNames = {latency_ms:"응답 시간", cpu_used_percent:"CPU", memory_used_percent:"메모리", disk_used_percent:"디스크"};
  function resolveRisk(status, reported) {
    const base = status === "DOWN" ? "CRITICAL" : status === "DEGRADED" ? "WARNING" : status === "HEALTHY" ? "NORMAL" : ["PENDING","UNCONFIGURED"].includes(status) ? "PENDING" : "UNKNOWN";
    if (["UNKNOWN","PENDING"].includes(base)) return {level:base, signals:[]};
    const signals = Array.isArray(reported?.signals) ? reported.signals.filter(s => metricNames[s.metric] && Number.isFinite(s.value) && ["NORMAL","WARNING","CRITICAL"].includes(s.level)) : [];
    const level = base === "CRITICAL" || reported?.level === "CRITICAL" ? "CRITICAL" : base === "WARNING" || reported?.level === "WARNING" ? "WARNING" : base;
    return {level, signals};
  }
  function riskCounts(assets) {
    const counts = {NORMAL:0, WARNING:0, CRITICAL:0, UNKNOWN:0, PENDING:0};
    for (const asset of assets) counts[(asset.risk || resolveRisk(asset.status)).level]++;
    return counts;
  }
  function riskText(asset) {
    const signals = asset.risk?.signals || [];
    const signal = signals.filter(s => s.level !== "NORMAL").sort((a,b) => (b.level === "CRITICAL") - (a.level === "CRITICAL"))[0];
    if (!signal) return "";
    return `${metricNames[signal.metric]} ${Number(signal.value.toFixed(1))}${signal.unit} · ${signal.recovering ? "회복 확인 중" : `${signal.level === "CRITICAL" ? "위험" : "주의"} ≥${signal.level === "CRITICAL" ? signal.critical : signal.warning}${signal.unit}`}`;
  }
  const api = {buildAssets, summarize, trustedComponents, resolveRisk, riskCounts, riskNames, metricNames, riskText};
  root.SentinelAssets = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
