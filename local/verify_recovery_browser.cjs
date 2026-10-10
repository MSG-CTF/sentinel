async (page) => {
  const checks = [], errors = [];
  const suffix = new Date().toISOString().replace(/[:.]/g, "-");
  const onError = error => errors.push(error.name);
  const check = (name, value) => { if (!value) throw new Error(name); checks.push(name); };
  page.on("pageerror", onError);
  try {
    await page.goto("http://127.0.0.1:58240/?view=architecture-1007");
    await page.setViewportSize({width:1600,height:1000});
    await page.waitForFunction(() => document.getElementById("profile").textContent === "LOCAL AUTO" && document.getElementById("healthy-count").textContent === "13");
    await page.getByRole("button", {name:/^내부 트랙 Backend API/}).click();
    check("internal_backend_shows_local_recovery_binding", (await page.locator("#detail-access-state").textContent()).includes("로컬 복구 활성") && (await page.locator("#detail-access").textContent()).includes("Docker named pipe"));
    await page.screenshot({path:`output/playwright/recovery-access-${suffix}.png`,fullPage:true});
    await page.keyboard.press("Escape");
    await page.getByRole("button", {name:/^외부 트랙 Backend API/}).click();
    check("external_backend_has_separate_local_recovery", (await page.locator("#detail-access-state").textContent()).includes("로컬 복구 활성") && (await page.locator("#detail-access").textContent()).includes("backend-external"));
    await page.keyboard.press("Escape");
    await page.getByRole("button", {name:/^문제 실행 Windows VM/}).click();
    check("windows_access_is_explicitly_a_plan", (await page.locator("#detail-access").textContent()).includes("PowerShell") && (await page.locator("#detail-access-state").textContent()).includes("설계"));
    await page.keyboard.press("Escape");
    await page.getByRole("tab", {name:"연결 검토",exact:true}).click();
    await page.getByText("자산별 접속·복구 계획", {exact:true}).click();
    check("all_roles_and_sentinel_have_access_rows", await page.locator("#access-plan-list button").count() === 26);
    check("seven_applications_have_local_recovery_bindings", await page.locator("#access-plan-list button").filter({hasText:"로컬 복구 구현"}).count() === 7);
    await page.getByRole("tab", {name:"자산 상태",exact:true}).click();
    check("functional_recovery_is_visible", (await page.locator("#recovery-feed").textContent()).includes("복구 확인") && (await page.locator("#recovery-mode").textContent()) === "로컬 활성");
    check("recovery_scope_excludes_data_services", (await page.locator("#recovery-scope").textContent()).includes("DB·Redis 자동 기동 제외") && (await page.locator("#operation-mode").textContent()).includes("7개"));
    check("unconnected_roles_are_not_counted_as_healthy", await page.locator("#total-count").textContent() === "25" && await page.locator("#pending-count").textContent() === "12");
    await page.screenshot({path:`output/playwright/recovery-status-${suffix}.png`,fullPage:true});
    try {
      await page.context().setOffline(true);
      await page.getByRole("button", {name:"최신 점검 결과 가져오기",exact:true}).click();
      await page.waitForFunction(() => !document.getElementById("connection-warning").hidden);
      check("offline_recovery_is_not_reported_as_current", await page.locator("#recovery-mode").textContent() === "현재 상태 미확인" && await page.locator("#healthy-count").textContent() === "0");
    } finally { await page.context().setOffline(false); }
    await page.getByRole("button", {name:"최신 점검 결과 가져오기",exact:true}).click();
    await page.waitForFunction(() => document.getElementById("healthy-count").textContent === "13");
    await page.setViewportSize({width:390,height:844});
    check("mobile_page_does_not_overflow", await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 2));
    await page.screenshot({path:`output/playwright/recovery-mobile-${suffix}.png`,fullPage:true});
    await page.getByRole("tab", {name:"전체 구조",exact:true}).click();
    const viewport = page.locator("#map-viewport");
    await viewport.hover();
    await page.mouse.wheel(420,0);
    await page.waitForFunction(() => document.getElementById("map-viewport").scrollLeft >= 300);
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    const pan = await viewport.evaluate(element => element.scrollLeft);
    await page.getByRole("button", {name:/^내부 트랙 Backend API/}).click();
    await page.keyboard.press("Escape");
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    // Focusing the node can bring its border into view by two CSS pixels.
    const restoredPan = await viewport.evaluate(element => element.scrollLeft);
    check("access_details_preserve_mobile_map_pan", restoredPan >= 300 && Math.abs(restoredPan - pan) <= 4);
    check("no_javascript_errors", errors.length === 0);
    return {passed:true, checks, screenshots_suffix:suffix};
  } finally {
    await page.context().setOffline(false);
    await page.keyboard.press("Escape");
    await page.setViewportSize({width:1600,height:1000});
    await page.getByRole("tab", {name:"전체 구조",exact:true}).click();
    await page.getByRole("button", {name:"구조도 기본 배율로 돌아가기",exact:true}).click();
    page.off("pageerror", onError);
  }
}
