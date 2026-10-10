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
    const maxAge = (2 * (Number(snapshot.poll_interval_seconds) || 15) + 30 + 5) * 1000;
    const assets = [];
    for (const original of snapshot.targets || []) {
      if (!original || typeof original.target_id !== "string") continue;
      const target = { ...original };
      target.status = knownStatuses.has(target.status) ? target.status : "UNVERIFIED";
      if (target.enabled === false) {
        target.status = "UNCONFIGURED"; target.checked_at = null; target.http_status_code = null; target.latency_ms = null;
      } else if (!["UNCONFIGURED", "PENDING"].includes(target.status)) {
        const checked = Date.parse(target.checked_at);
        if (settings.offline || !Number.isFinite(checked) || now - checked > maxAge || checked - now > 30000) {
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
        evidence_type:"DIRECT_PROBE", enabled:target.enabled, evidence_scope:target.evidence_scope || null});
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
          source_url:target.url || null, evidence_type:"HEALTHZ_COMPONENT", enabled:target.enabled});
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
  const api = {buildAssets, summarize, trustedComponents};
  root.SentinelAssets = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
