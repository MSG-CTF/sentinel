async (page) => {
  const checks = [], errors = [];
  const check = (name, ok) => {if (!ok) throw new Error(name); checks.push(name);};
  const onError = error => errors.push(error.name);
  const url = "http://127.0.0.1:58240/?view=architecture-1007";
  const refresh = async () => {
    await page.getByRole("button", {name:"최신 점검 결과 가져오기",exact:true}).click();
    await page.waitForFunction(() => !document.getElementById("refresh").disabled);
  };
  page.on("pageerror", onError);
  try {
    await page.goto(url);
    await page.setViewportSize({width:1440,height:1000});
    await page.waitForFunction(() => document.getElementById("data-recovery-state").textContent === "마지막 검증 통과");
    await page.getByRole("tab", {name:"자산 상태",exact:true}).click();
    check("all_six_data_stores_have_restore_evidence", await page.locator("#data-recovery-assets li").count() === 6);
    check("four_real_application_reads_are_distinct_from_live_status", (await page.locator("#data-recovery-caption").textContent()).includes("앱 4개 조회 확인") && await page.locator("#healthy-count").textContent() === "13");
    const text = await page.locator(".data-recovery-panel").textContent();
    check("scope_excludes_cross_service_atomic_restore", text.includes("저장소별 백업 시점은 다를 수 있음") && text.includes("운영 데이터 자동 복원 미연결"));
    check("redis_fixture_is_explicit", text.includes("시험 값은 격리 환경에만 생성"));
    await page.locator(".data-recovery-panel").scrollIntoViewIfNeeded();
    const bounds = await page.locator(".data-recovery-panel").boundingBox();
    await page.screenshot({path:"output/playwright/data-recovery-20261010.png",clip:{x:bounds.x-16,y:bounds.y-12,width:bounds.width+32,height:bounds.height+24}});
    await page.getByRole("tab", {name:"전체 구조",exact:true}).click();
    await page.getByRole("button", {name:/^내부 트랙 PostgreSQL/}).click();
    check("data_asset_detail_has_dated_restore_evidence", (await page.locator("#detail-access").textContent()).includes("마지막 격리 복원"));
    await page.keyboard.press("Escape");
    await page.getByRole("tab", {name:"자산 상태",exact:true}).click();
    const good = await (await page.request.get("http://127.0.0.1:58240/data-recovery-status.json")).json();
    const malformed = {...good, assets:[good.assets[0], ...good.assets.slice(0,5)]};
    await page.route("**/data-recovery-status.json", route=>route.fulfill({json:malformed}));
    await refresh();
    check("duplicate_or_missing_asset_cannot_claim_pass", await page.locator("#data-recovery-state").textContent() === "검증 기록 미확인");
    await page.unroute("**/data-recovery-status.json");
    const failed = {...good,passed:false,assets:good.assets.slice(0,4),applications:[]};
    await page.route("**/data-recovery-status.json", route=>route.fulfill({json:failed}));
    await refresh();
    check("failed_drill_is_not_promoted_to_success", await page.locator("#data-recovery-state").textContent() === "마지막 검증 실패");
    await page.unroute("**/data-recovery-status.json");
    await refresh();
    await page.context().setOffline(true);
    await refresh();
    check("offline_report_is_explicitly_historical", await page.locator("#data-recovery-state").textContent() === "이전 수신 기록" && await page.locator("#healthy-count").textContent() === "0");
    await page.context().setOffline(false);
    await refresh();
    await page.setViewportSize({width:390,height:844});
    check("mobile_panel_does_not_overflow", await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 2));
    await page.locator(".data-recovery-panel").screenshot({path:"output/playwright/data-recovery-mobile-20261010.png"});
    check("no_javascript_errors", errors.length === 0);
    return {passed:true,checks};
  } finally {
    await page.unroute("**/data-recovery-status.json");
    await page.context().setOffline(false);
    await page.keyboard.press("Escape");
    await page.setViewportSize({width:1600,height:1000});
    await page.goto(url);
    page.off("pageerror",onError);
  }
}
