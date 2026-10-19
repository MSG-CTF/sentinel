"use strict";
const SentinelInstances = globalThis.SentinelInstances = (() => {
  const reasons = {
    RUNTIME_READY_HEALTHCHECK_UNVERIFIED:"실행 준비 확인 · 문제 기능 미확인",
    RUNTIME_READY_FUNCTIONAL_CHECK_PASSED:"실행 준비·로컬 시험 서비스 기능 확인",
    FUNCTIONAL_CHECK_FAILED:"로컬 시험 서비스 기능 실패",
    FUNCTIONAL_CHECKER_ERROR:"기능 점검 응답 판정 불가",
    FUNCTIONAL_BINDING_MISMATCH:"기능 점검 대상과 실행 자원 정보 불일치",
    OWNED_CHALLENGE_FUNCTION_CONFIRMED:"실습 문제의 실제 기능 확인",
    FUNCTION_RESPONSE_MISMATCH:"실습 문제 기능의 응답 불일치",
    FUNCTION_UNAVAILABLE:"실습 문제 기능에서 HTTP 5xx 오류 발생",
    FUNCTION_TIMEOUT:"실습 문제 기능 응답 시간 초과",
    FUNCTION_UNREACHABLE:"실습 문제 기능 연결 실패",
    FUNCTIONAL_REPORT_STALE:"실습 문제 기능 보고 갱신 지연",
    FUNCTIONAL_REPORT_PENDING:"실습 문제의 첫 기능 보고 대기",
    FUNCTIONAL_LEASE_EXPIRED:"실습 문제의 점검 기한 만료",
    OWNERSHIP_BINDING_REJECTED:"실습 문제 소유·배포 정보 불일치",
    TTL_EXPIRED_RESOURCE_PRESENT:"사용 기한이 지났지만 실행 자원 존재",
    TERMINAL_RESOURCE_PRESENT:"종료 처리된 인스턴스의 실행 자원 존재",
    ACTIVE_WORKLOAD_TERMINATED:"사용 중인 인스턴스의 실행 자원이 삭제됨",
    RUNTIME_DELETION_CONFIRMED:"실행 자원 삭제 확인",
    RUNTIME_TARGET_NOT_CONFIGURED:"실행 환경 연결 정보 없음",
    RUNTIME_IDENTITY_MISMATCH:"인스턴스와 실행 환경 정보 불일치",
    STALE_RUNTIME_OBSERVATION:"Runtime 관측 시각이 오래되거나 올바르지 않음",
    INVENTORY_REFRESH_STALE:"목록 갱신 지연 · 이전 결과",
    DELETION_RECHECK_STALE:"삭제 확인의 재점검 기한 초과",
    K3S_NODE_NOT_READY:"실행 노드 준비 안 됨", K3S_NODE_PRESSURE:"실행 노드 자원 부족",
    RUNTIME_WORKLOAD_NOT_READY:"실행 자원 준비 안 됨",
    AUTH_TOKEN_NOT_CONFIGURED:"서비스 인증 설정 없음", AUTH_REQUIRED:"서비스 인증 실패",
    UPSTREAM_TIMEOUT:"상태 조회 시간 초과", UPSTREAM_UNAVAILABLE:"상태 조회 실패",
    RUNTIME_OR_ENDPOINT_NOT_FOUND:"인스턴스 또는 상태 조회 경로 없음",
    REDIRECT_BLOCKED:"예상하지 않은 조회 경로 응답", EXPECTED_JSON:"상태 응답 형식 불일치",
    INVALID_JSON:"상태 응답을 해석할 수 없음", RESPONSE_TOO_LARGE:"상태 응답 크기 초과",
    INVALID_RUNTIME_PHASE:"실행 상태 값 미확인", INVALID_RUNTIME_STATUS:"실행 상태 응답 불완전",
  };
  const states = {READY:"준비 확인",DOWN:"확인 필요",UNVERIFIED:"미확인",STALE:"갱신 지연",REMOVED:"삭제 확인"};
  const schedulerStates = new Set(["REQUESTED","SCHEDULING","PROVISIONING","RUNNING","RESTARTING","RESETTING","STOPPING","STOPPED","FAILED","EXPIRED","CLEANUP_PENDING","CLEANED"]);
  const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
  const time = value => typeof value === "string" && Number.isFinite(Date.parse(value));
  const clockTime = value => new Intl.DateTimeFormat("en-GB", {timeZone:"Asia/Seoul",hour12:false,
    hour:"2-digit",minute:"2-digit",second:"2-digit"}).format(new Date(value));
  function validDeletion(row) {
    if (row.deletion_verified_at == null && row.deletion_recheck_at == null) return true;
    return row.status === "REMOVED" && row.runtime_phase === "TERMINATED"
      && row.reason_code === "RUNTIME_DELETION_CONFIRMED" && time(row.deletion_verified_at)
      && row.runtime_observed_at === row.deletion_verified_at && time(row.deletion_recheck_at)
      && Date.parse(row.deletion_recheck_at) - Date.parse(row.deletion_verified_at) === 300000;
  }
  function validCheck(row) {
    const check = row.functional_check;
    if (check == null) return row.challenge_health === "UNVERIFIED";
    return ["local_functional_fixture","owned_deployment_test"].includes(check.scope) && /^[a-z][a-z0-9_-]{0,63}$/.test(check.check_id)
      && ["UP","DOWN","CHECKER_ERROR","STALE"].includes(check.status) && row.challenge_health === check.status
      && time(check.checked_at) && (check.status !== "UP" || row.status === "READY")
      && (check.status !== "STALE" || row.status === "STALE")
      && (check.status !== "DOWN" || row.status === "DOWN")
      && (check.status !== "CHECKER_ERROR" || row.status === "UNVERIFIED");
  }
  function valid(value) {
    if (!value || typeof value.complete_inventory !== "boolean" || value.coverage !== "all_pages"
      || !["DISABLED","PENDING","COMPLETE","UNAVAILABLE","STALE"].includes(value.status)
      || !Array.isArray(value.instances)) return false;
    if (value.complete_inventory !== (value.status === "COMPLETE")) return false;
    if (["DISABLED","PENDING","UNAVAILABLE"].includes(value.status) && value.instances.length) return false;
    if (["COMPLETE","STALE"].includes(value.status) && !time(value.checked_at)) return false;
    return new Set(value.instances.map(row => row?.instance_id)).size === value.instances.length && value.instances.every(row =>
      row && uuid.test(row.instance_id) && schedulerStates.has(row.scheduler_status) && Object.hasOwn(states,row.status)
      && row.track === "UNMAPPED" && validCheck(row) && validDeletion(row) && typeof row.ttl_expired === "boolean"
      && time(row.expires_at) && ["none","manual_review"].includes(row.recovery_action)
      && (row.runtime_observed_at === null || time(row.runtime_observed_at))
      && (value.status !== "STALE" || row.status === "STALE"));
  }
  function present(value) {
    if (!valid(value)) return {state:"조회 실패", caption:"인스턴스 상태를 가져오지 못함 · 정상 여부 미확인", rows:[]};
    const labels = {DISABLED:"연결 전",PENDING:"조회 대기",COMPLETE:`전체 ${value.instances.length}개 조회`,UNAVAILABLE:"조회 실패",STALE:"갱신 지연"};
    const captions = {DISABLED:"이 프로필에는 인스턴스 감시가 연결되지 않음", PENDING:"첫 상태 조회를 기다리는 중",
      UNAVAILABLE:"목록 조회 실패 · 정상 여부 미확인", STALE:"목록 갱신 지연 · 이전 결과로 표시",
      COMPLETE:value.instances.length ? (value.instances.some(row => row.functional_check)
        ? value.instances.some(row => row.functional_check?.scope === "owned_deployment_test")
          ? "전체 목록의 실행 상태 · 허가된 실습 문제만 실제 기능 점검"
          : "전체 목록의 실행 상태 · 등록된 로컬 시험 서비스만 기능 점검" : "전체 목록의 실행 상태 · 문제 기능 점검 미확인")
        : "전체 목록 조회 완료 · 등록된 인스턴스 없음"};
    return {state:labels[value.status],caption:captions[value.status],rows:value.instances.map(row => ({
      id:row.instance_id, status:row.status, title:`${states[row.status]} · ${reasons[row.reason_code] || "상태 확인 필요"}`,
      detail:`Scheduler ${row.scheduler_status} · ${row.ttl_expired ? "사용 기한 초과" : "기한 " + new Date(row.expires_at).toLocaleString("ko-KR", {timeZone:"Asia/Seoul",hour12:false})}`,
      note:(row.deletion_verified_at
        ? `삭제 확인 ${clockTime(row.deletion_verified_at)} · 재점검 ${clockTime(row.deletion_recheck_at)}`
        : row.functional_check ? row.functional_check.scope === "owned_deployment_test"
          ? `실습 문제 기능 ${row.functional_check.status} · 소유·배포 정보 확인`
          : `로컬 기능 ${row.functional_check.status} · 대회 문제 기능과 별도` : "문제 기능 점검 미확인")
        + (row.recovery_action === "manual_review" ? " · 관리자 확인 필요 · 자동 조치 없음" : ""),
    }))};
  }
  function render(value) {
    const model = present(value), list = document.getElementById("instance-list");
    document.getElementById("instance-state").textContent = model.state;
    document.getElementById("instance-caption").textContent = model.caption;
    list.replaceChildren();
    for (const row of model.rows) {
      const item = document.createElement("li");
      for (const [tag, text, style] of [["strong",row.id,""],["span",row.title,row.status === "DOWN" ? "instance-down" : row.status === "READY" ? "instance-ready" : ""],["span",row.detail,""],["span",row.note,""]]) {
        const element = document.createElement(tag); element.textContent = text; element.className = style; item.append(element);
      }
      list.append(item);
    }
  }
  async function refresh() {
    try {
      const response = await fetch("/api/instances", {cache:"no-store",signal:AbortSignal.timeout(4000)});
      if (!response.ok) throw new Error("INVENTORY_UNAVAILABLE");
      render(await response.json());
    } catch { render(null); }
  }
  return {refresh,present};
})();
if (typeof module !== "undefined") module.exports = SentinelInstances;
