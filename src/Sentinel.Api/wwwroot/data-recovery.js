"use strict";
// This is a dated drill record, never a live availability or auto-restore signal.
const SentinelDataRecovery = globalThis.SentinelDataRecovery = (() => {
  const expected = ["internal-backend-database", "external-backend-database", "scheduler-db", "broker-db", "internal-backend-cache", "external-backend-cache"];
  const apps = ["backend-internal", "backend-external", "scheduler", "broker"];
  const names = {"internal-backend-database":"내부 PostgreSQL", "external-backend-database":"외부 PostgreSQL", "scheduler-db":"Scheduler DB", "broker-db":"Broker DB", "internal-backend-cache":"내부 Redis", "external-backend-cache":"외부 Redis"};
  let record = null, unavailable = false, offline = false, profile = null;
  let cutover = null, cutoverUnavailable = false;
  const date = value => new Date(value).toLocaleString("ko-KR", {timeZone:"Asia/Seoul", hour12:false});
  const exactSet = (rows, key, allowed) => rows.length === allowed.length && new Set(rows.map(row => row[key])).size === allowed.length && rows.every(row => allowed.includes(row[key]));
  function valid(value) {
    if (!value || value.schema_version !== 1 || value.scope !== "isolated_local_restore_drill" || value.automatic_restore_enabled !== false || typeof value.passed !== "boolean") return false;
    if (![value.started_at, value.completed_at].every(time => typeof time === "string" && Number.isFinite(Date.parse(time)))) return false;
    if (Date.parse(value.completed_at) < Date.parse(value.started_at) || Date.parse(value.completed_at) > Date.now() + 30000) return false;
    if (!Array.isArray(value.assets) || !Array.isArray(value.applications) || value.assets.some(a => !a || !expected.includes(a.asset_id) || typeof a.passed !== "boolean") || value.applications.some(a => !a || !apps.includes(a.service) || typeof a.passed !== "boolean")) return false;
    if (value.assets.some(a => a.passed && (a.kind === "postgres" ? !(a.row_hashes_match === true && Number.isInteger(a.tables) && a.tables >= 0 && Number.isInteger(a.rows) && a.rows >= 0) : a.kind === "redis" ? !(Number.isInteger(a.snapshot_keys) && a.snapshot_keys >= 0 && a.aof_enabled === true && a.canary?.passed === true && a.canary?.aof_restart_verified === true) : true))) return false;
    if (value.passed && (!exactSet(value.assets,"asset_id",expected) || !exactSet(value.applications,"service",apps) || !value.assets.every(a=>a.passed) || !value.applications.every(a=>a.passed) || value.source_unchanged !== true || value.lab_removed !== true)) return false;
    return true;
  }
  const local = () => ["local", "local-recovery"].includes(profile);
  function validCutover(value) {
    if (!value || value.schema_version !== 1 || value.scope !== "local_service_cutover_round_trip" || value.automatic_data_restore_enabled !== false || typeof value.passed !== "boolean") return false;
    if (![value.started_at,value.completed_at].every(time=>typeof time === "string" && Number.isFinite(Date.parse(time))) || Date.parse(value.completed_at)<Date.parse(value.started_at) || Date.parse(value.completed_at)>Date.now()+30000) return false;
    if (![value.assets,value.applications,value.rollback_applications].every(Array.isArray)) return false;
    if (value.assets.some(item=>!item || !expected.includes(item.asset_id) || typeof item.passed !== "boolean") || [...value.applications,...value.rollback_applications].some(item=>!item || !apps.includes(item.service) || typeof item.passed !== "boolean")) return false;
    return !value.passed || (exactSet(value.assets,"asset_id",expected) && exactSet(value.applications,"service",apps) && exactSet(value.rollback_applications,"service",apps)
      && [...value.assets,...value.applications,...value.rollback_applications].every(item=>item.passed)
      && value.restored_volume_bindings_verified === true && value.original_volume_bindings_verified === true && value.restore_lab_removed === true);
  }
  function renderCutover() {
    const caption = document.getElementById("service-cutover-caption");
    if (!caption) return;
    caption.textContent = !local() ? "서비스 전환 · 로컬 검증 전용" : !cutover || cutoverUnavailable ? "서비스 전환 검증 기록 미확인" :
      `${offline ? "이전 수신 기록 · " : ""}${date(cutover.completed_at)} KST · ${cutover.passed ? "복원 볼륨으로 서비스 전환 → 앱 4개 조회 → 원래 볼륨 복귀 확인" : "서비스 전환 검증 통과 조건을 충족하지 못함"}`;
  }
  function describe(id) {
    const asset = record?.assets.find(a=>a.asset_id === id);
    if (!local() || unavailable || !record?.passed || !asset?.passed) return null;
    return `${date(record.completed_at)} KST · 백업에서 새 볼륨으로 복원 확인 · 자동 덮어쓰기 미연결`;
  }
  function render(isOffline = offline, activeProfile = profile) {
    offline = isOffline; profile = activeProfile;
    renderCutover();
    const status = document.getElementById("data-recovery-state");
    const caption = document.getElementById("data-recovery-caption");
    const rows = document.getElementById("data-recovery-assets");
    rows.replaceChildren();
    if (!local()) {status.textContent = "로컬 검증 전용"; caption.textContent = "현재 프로필의 복원 결과는 등록되지 않음"; return;}
    if (!record || unavailable) {status.textContent = "검증 기록 미확인"; caption.textContent = "백업·복원 검증 결과를 가져오지 못함 · 실시간 자산 상태와 별도"; return;}
    status.textContent = offline ? "이전 수신 기록" : record.passed ? "마지막 검증 통과" : "마지막 검증 실패";
    caption.textContent = `${date(record.completed_at)} KST · ${record.passed ? "6개 저장소 복원과 앱 4개 조회 확인" : "통과 조건을 충족하지 못함"} · ${Number.isFinite(record.duration_seconds) ? record.duration_seconds + "초" : "소요 시간 미확인"}`;
    for (const id of expected) {
      const asset = record.assets.find(a=>a.asset_id === id);
      const row = document.createElement("li"), name = document.createElement("strong"), result = document.createElement("span");
      name.textContent = names[id];
      result.textContent = !asset?.passed ? "복원 확인 안 됨" : asset.kind === "postgres" ? `${asset.tables}개 테이블 · ${asset.rows}행 내용 일치` : `원본 ${asset.snapshot_keys}개 키 · 값·만료 시간·재기동 ${asset.canary?.passed ? "시험 통과" : "미확인"}`;
      row.append(name, result); rows.append(row);
    }
  }
  async function refresh(isOffline, activeProfile) {
    if (!isOffline) {
      try {
        const response = await fetch("/data-recovery-status.json", {cache:"no-store", signal:AbortSignal.timeout(2500)});
        if (!response.ok) throw new Error("NO_REPORT");
        const value = await response.json();
        if (!valid(value)) throw new Error("INVALID_REPORT");
        record = value; unavailable = false;
      } catch {unavailable = true;}
      try {
        const response = await fetch("/service-cutover-status.json", {cache:"no-store", signal:AbortSignal.timeout(2500)});
        if (!response.ok) throw new Error("NO_CUTOVER_REPORT");
        const value = await response.json();
        if (!validCutover(value)) throw new Error("INVALID_CUTOVER_REPORT");
        cutover = value; cutoverUnavailable = false;
      } catch {cutoverUnavailable = true;}
    }
    render(isOffline, activeProfile);
  }
  return {refresh, render, describe, valid, validCutover};
})();
if (typeof module !== "undefined") module.exports = SentinelDataRecovery;
