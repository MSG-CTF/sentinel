async (page) => {
  const checks = [], errors = [];
  const check = (name, value) => {if (!value) throw new Error(name); checks.push(name);};
  const onError = error => errors.push(error.name);
  const url = "http://127.0.0.1:58240/?view=architecture-1007";
  const refresh = async () => {
    await page.getByRole("button", {name:"최신 점검 결과 가져오기",exact:true}).click();
    await page.waitForFunction(() => !document.getElementById("refresh").disabled);
  };
  page.on("pageerror", onError);
  try {
    await page.setViewportSize({width:1600,height:1000});
    await page.goto(url);
    await page.waitForFunction(() => document.getElementById("healthy-count").textContent === "16");
    check("only_sixteen_connected_assets_are_healthy", await page.locator("#total-count").textContent() === "25" && await page.locator("#pending-count").textContent() === "9");
    await page.getByRole("button", {name:/^문제 실행 Runtime API/}).click();
    check("runtime_has_live_local_recovery_binding", (await page.locator("#detail-access-state").textContent()).includes("로컬 복구 활성"));
    check("runtime_scope_identifies_connection_fixture", (await page.locator("#detail-access").textContent()).includes("연결 시험"));
    await page.screenshot({path:"output/playwright/execution-runtime-20261011.png",fullPage:true});
    await page.keyboard.press("Escape");
    await page.getByRole("button", {name:/^문제 실행 Windows VM/}).click();
    check("windows_remains_a_plan", (await page.locator("#detail-access-state").textContent()).includes("설계"));
    await page.keyboard.press("Escape");
    await page.getByRole("tab", {name:"연결 검토",exact:true}).click();
    check("runtime_connections_have_live_evidence", await page.locator("#verified-links").textContent() === "6");
    await page.getByText("자산별 접속·복구 계획", {exact:true}).click();
    check("eight_application_recovery_bindings", await page.locator("#access-plan-list button").filter({hasText:"로컬 복구 구현"}).count() === 8);
    await page.getByRole("tab", {name:"자산 상태",exact:true}).click();
    await page.waitForFunction(() => document.getElementById("service-cutover-caption").textContent.includes("원래 볼륨 복귀 확인"));
    check("six_store_restore_evidence_is_preserved", await page.locator("#data-recovery-assets li").count() === 6);
    check("service_cutover_has_separate_dated_evidence", (await page.locator("#service-cutover-caption").textContent()).includes("KST") && (await page.locator("#service-cutover-caption").textContent()).includes("앱 4개 조회"));
    check("runtime_recovery_is_visible", (await page.locator("#recovery-feed").textContent()).includes("복구 확인"));
    await page.locator(".data-recovery-panel").scrollIntoViewIfNeeded();
    await page.locator(".data-recovery-panel").screenshot({path:"output/playwright/execution-cutover-20261011.png"});
    const valid = await (await page.request.get("http://127.0.0.1:58240/service-cutover-status.json")).json();
    await page.route("**/service-cutover-status.json", route=>route.fulfill({json:{...valid,rollback_applications:[]}}));
    await refresh();
    check("missing_rollback_evidence_cannot_claim_pass", await page.locator("#service-cutover-caption").textContent() === "서비스 전환 검증 기록 미확인");
    await page.unroute("**/service-cutover-status.json");
    await refresh();
    await page.context().setOffline(true);
    await refresh();
    check("offline_live_counts_are_not_green", await page.locator("#healthy-count").textContent() === "0");
    check("offline_cutover_record_is_explicitly_historical", (await page.locator("#service-cutover-caption").textContent()).startsWith("이전 수신 기록"));
    await page.context().setOffline(false);
    await refresh();
    await page.setViewportSize({width:390,height:844});
    check("mobile_has_no_page_overflow", await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth+2));
    await page.locator(".data-recovery-panel").screenshot({path:"output/playwright/execution-cutover-mobile-20261011.png"});
    check("no_javascript_errors", errors.length === 0);
    return {passed:true,checks};
  } finally {
    await page.unroute("**/service-cutover-status.json");
    await page.context().setOffline(false);
    await page.keyboard.press("Escape");
    await page.setViewportSize({width:1600,height:1000});
    await page.goto(url);
    page.off("pageerror",onError);
  }
}
