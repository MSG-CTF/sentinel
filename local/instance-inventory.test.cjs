"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
require("../src/Sentinel.Api/wwwroot/instance-inventory.js");
const {present} = globalThis.SentinelInstances;
const snapshot = () => ({status:"COMPLETE",reason_code:"ALL_PAGES_READ",coverage:"all_pages",complete_inventory:true,
  checked_at:"2026-10-13T04:00:00Z",instances:[{instance_id:"11111111-1111-4111-8111-111111111111",scheduler_status:"RUNNING",
    track:"UNMAPPED",status:"READY",reason_code:"RUNTIME_READY_HEALTHCHECK_UNVERIFIED",runtime_phase:"READY",
    runtime_observed_at:"2026-10-13T04:00:00Z",expires_at:"2026-10-13T05:00:00Z",ttl_expired:false,
    challenge_health:"UNVERIFIED",recovery_action:"none"}]});

test("complete inventory does not claim challenge health", () => {
  const model = present(snapshot());
  assert.equal(model.state,"전체 1개 조회");
  assert.match(model.rows[0].title,/문제 기능 미확인/);
  assert.match(model.caption,/문제 기능 점검 미확인/);
});

test("only an explicitly owned challenge report is labelled as real practice functionality", () => {
  const value = snapshot();
  Object.assign(value.instances[0], {challenge_health:"UP",reason_code:"OWNED_CHALLENGE_FUNCTION_CONFIRMED",
    functional_check:{check_id:"owned-notebook",scope:"owned_deployment_test",status:"UP",
      reason_code:"DATABASE_LOGIN_AND_ACCESS_RULES_CONFIRMED",checked_at:value.checked_at}});
  const model = present(value);
  assert.match(model.caption,/허가된 실습 문제만 실제 기능 점검/);
  assert.match(model.rows[0].note,/실습 문제 기능 UP/);
  value.instances[0].functional_check.scope = "participant";
  assert.equal(present(value).rows.length,0);
});
test("empty complete inventory is displayed clearly", () => {
  const value = snapshot(); value.instances = [];
  assert.match(present(value).caption,/등록된 인스턴스 없음/);
});

test("a confirmed deletion shows its original verification and scheduled recheck", () => {
  const value = snapshot();
  Object.assign(value.instances[0], {scheduler_status:"CLEANED", status:"REMOVED", runtime_phase:"TERMINATED",
    reason_code:"RUNTIME_DELETION_CONFIRMED", deletion_verified_at:value.instances[0].runtime_observed_at,
    deletion_recheck_at:"2026-10-13T04:05:00Z"});
  const model = present(value);
  assert.match(model.rows[0].note,/삭제 확인 13:00:00 · 재점검 13:05:00/);
  assert.doesNotMatch(model.rows[0].note,/문제 기능 점검/);
  assert.equal(model.rows[0].status,"REMOVED");
});

test("malformed deletion evidence cannot masquerade as a fresh runtime check", () => {
  for (const fault of ["status","phase","reason","time","observed","deadline"]) {
    const value = snapshot();
    Object.assign(value.instances[0], {scheduler_status:"CLEANED", status:"REMOVED", runtime_phase:"TERMINATED",
      reason_code:"RUNTIME_DELETION_CONFIRMED", deletion_verified_at:"2026-10-13T04:00:00Z", deletion_recheck_at:"2026-10-13T04:05:00Z"});
    if (fault === "status") value.instances[0].status = "READY";
    if (fault === "phase") value.instances[0].runtime_phase = "READY";
    if (fault === "reason") value.instances[0].reason_code = "RUNTIME_READY_HEALTHCHECK_UNVERIFIED";
    if (fault === "time") value.instances[0].deletion_verified_at = "unknown";
    if (fault === "observed") value.instances[0].runtime_observed_at = "2026-10-13T04:01:00Z";
    if (fault === "deadline") value.instances[0].deletion_recheck_at = "2026-10-13T05:00:00Z";
    assert.equal(present(value).rows.length,0,fault);
  }
});

test("all 437 rows are visible, including the oldest cleanup request", () => {
  const value = snapshot(), row = value.instances[0];
  value.instances = Array.from({length:437}, (_, index) => ({...row,
    instance_id:`80000000-0000-4000-8000-${(index+1).toString(16).padStart(12,"0")}`,
    scheduler_status:index === 436 ? "CLEANUP_PENDING" : "RUNNING"}));
  const model = present(value);
  assert.equal(model.state,"전체 437개 조회");
  assert.equal(model.rows.length,437);
  assert.match(model.rows[436].detail,/CLEANUP_PENDING/);
});
test("failure and invalid contracts clear previous ready results", () => {
  for (const fault of ["offline","full_inventory","duplicate","unknown_status","bad_id","false_health","false_stale"]) {
    const value = snapshot();
    if (fault === "full_inventory") value.complete_inventory = false;
    if (fault === "duplicate") value.instances.push({...value.instances[0]});
    if (fault === "unknown_status") value.instances[0].status = "HEALTHY";
    if (fault === "bad_id") value.instances[0].instance_id = "<script>";
    if (fault === "false_health") value.instances[0].challenge_health = "UP";
    if (fault === "false_stale") value.status = "STALE";
    assert.equal(present(fault === "offline" ? null : value).rows.length,0,fault);
    assert.equal(present(fault === "offline" ? null : value).state,"조회 실패",fault);
  }
});
test("aged observations cannot retain a ready label", () => {
  const value = snapshot(); value.status = "STALE"; value.complete_inventory = false; value.instances[0].status = "STALE";
  value.instances[0].reason_code = "INVENTORY_REFRESH_STALE"; value.instances[0].recovery_action = "manual_review";
  const model = present(value);
  assert.equal(model.state,"갱신 지연");
  assert.match(model.rows[0].title,/이전 결과/);
  assert.match(model.rows[0].note,/자동 조치 없음/);
});
test("expired ready resource calls for review instead of offering a reset", () => {
  const value = snapshot(); Object.assign(value.instances[0],{status:"DOWN",ttl_expired:true,
    reason_code:"TTL_EXPIRED_RESOURCE_PRESENT",recovery_action:"manual_review"});
  const model = present(value);
  assert.match(model.rows[0].title,/사용 기한이 지났지만 실행 자원 존재/);
  assert.match(model.rows[0].detail,/사용 기한 초과/);
  assert.match(model.rows[0].note,/관리자 확인 필요/);
});

test("local functional results retain their scope and distinguish checker failures", () => {
  for (const [health,status] of [["UP","READY"],["DOWN","DOWN"],["CHECKER_ERROR","UNVERIFIED"],["STALE","STALE"]]) {
    const value = snapshot(), row = value.instances[0];
    Object.assign(row,{status,challenge_health:health,functional_check:{check_id:"local_calculator",
      scope:"local_functional_fixture",status:health,checked_at:value.checked_at}});
    const model = present(value);
    assert.equal(model.rows.length,1);
    assert.match(model.rows[0].note,new RegExp(`로컬 기능 ${health}`));
    assert.match(model.rows[0].note,/대회 문제 기능과 별도/);
    row.functional_check.scope = "competition";
    assert.equal(present(value).rows.length,0);
  }
});

test("stale rows cannot show an earlier functional pass", () => {
  const value = snapshot(), row = value.instances[0];
  Object.assign(row,{status:"STALE",challenge_health:"UP",functional_check:{check_id:"local_calculator",
    scope:"local_functional_fixture",status:"UP",checked_at:value.checked_at}});
  assert.equal(present(value).rows.length,0);
});
