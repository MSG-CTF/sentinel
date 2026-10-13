async (page) => {
  const checks = [], errors = [];
  const onError = error => errors.push(error.name);
  const check = (name, passed) => { if (!passed) throw new Error(name); checks.push(name); };
  page.on("pageerror", onError);
  const refresh = async () => {
    await page.waitForFunction(() => !document.getElementById("refresh").disabled);
    await page.getByRole("button", {name:"최신 점검 결과 가져오기",exact:true}).click();
    await page.waitForFunction(() => !document.getElementById("refresh").disabled);
  };
  const path = "**/api/instances";
  try {
    await page.getByRole("tab", {name:"자산 상태",exact:true}).click();
    await refresh();
    const live = await (await page.request.get("http://127.0.0.1:58240/api/instances")).json();
    check("live_recent_window_remains_incomplete", live.complete_inventory === false
      && live.coverage === "recent_20" && await page.locator("#instance-list li").count() === live.instances.length);
    check("existing_assets_remain_sixteen_healthy", await page.locator("#healthy-count").textContent() === "16");
    await page.setViewportSize({width:1440,height:1000});
    await page.locator(".instance-panel").screenshot({path:"output/playwright/instance-live-current-20261013.png"});
    const fixture = {status:"PARTIAL",reason_code:"RECENT_WINDOW_ONLY",coverage:"recent_20",complete_inventory:false,
      checked_at:new Date().toISOString(),instances:[{instance_id:"11111111-1111-4111-8111-111111111111",scheduler_status:"RUNNING",
        track:"UNMAPPED",status:"READY",reason_code:"RUNTIME_READY_HEALTHCHECK_UNVERIFIED",runtime_phase:"READY",
        runtime_observed_at:new Date().toISOString(),expires_at:new Date(Date.now()+3600000).toISOString(),
        ttl_expired:false,challenge_health:"UNVERIFIED",recovery_action:"none"}]};
    let failed = false;
    await page.route(path, route => failed ? route.fulfill({status:503,body:"unavailable"}) : route.fulfill({json:fixture}));
    await refresh();
    check("synthetic_ready_row_does_not_claim_problem_health", (await page.locator("#instance-list").textContent()).includes("문제 기능 미확인"));
    check("no_instance_reset_control", await page.locator(".instance-panel button").count() === 0);
    await page.setViewportSize({width:390,height:844});
    check("mobile_has_no_page_overflow", await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    await page.locator(".instance-panel").screenshot({path:"output/playwright/instance-synthetic-mobile-20261013.png"});
    failed = true;
    await refresh();
    check("failed_inventory_clears_ready_rows", await page.locator("#instance-list li").count() === 0 && await page.locator("#instance-state").textContent() === "조회 실패");
    check("inventory_failure_does_not_remove_asset_results", await page.locator("#healthy-count").textContent() === "16");
    await page.unroute(path);
    await page.context().setOffline(true);
    await refresh();
    check("offline_clears_instance_and_asset_readiness", await page.locator("#instance-list li").count() === 0 && await page.locator("#healthy-count").textContent() === "0");
    await page.context().setOffline(false);
    await refresh();
    check("reconnection_restores_real_window", await page.locator("#instance-state").textContent() === `최근 ${live.instances.length}개 조회`);
    check("no_javascript_errors", errors.length === 0);
    return {passed:true, checks, synthetic_ui_rows:true};
  } finally {
    await page.unroute(path);
    await page.context().setOffline(false);
    await page.setViewportSize({width:1440,height:1000});
    page.off("pageerror", onError);
  }
}
