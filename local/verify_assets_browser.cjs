async (page) => {
  const checks = [], errors = [];
  const suffix = new Date().toISOString().replace(/[:.]/g,"-");
  const check = (name,ok) => {if (!ok) throw new Error(name); checks.push(name);};
  const onError = error => errors.push(error.name);
  page.on("pageerror",onError);
  try {
    // The real frontend must boot its built JavaScript through both local Edge ports.
    for (const port of [58186,58187]) {
      await page.goto(`http://127.0.0.1:${port}/`);
      await page.waitForFunction(() => document.getElementById("root")?.childElementCount > 0);
      check(`edge_${port}_renders_react_application`, await page.locator("#root").innerText() !== "");
    }
    await page.goto("http://127.0.0.1:58240/?view=architecture-1007");
    await page.setViewportSize({width:1600,height:1000});
    await page.waitForFunction(() => document.getElementById("healthy-count").textContent === "13");
    check("all_25_roles_are_counted_once", await page.locator("#total-count").textContent() === "25" && await page.locator("#pending-count").textContent() === "12");
    check("nine_direct_and_four_dependency_observations", (await page.locator("#measurement-note").textContent()) === "직접 9 · 간접 4");
    check("sentinel_map_card_includes_added_probes", (await page.locator(".map-observer-reading span").first().textContent()) === "실측: 직접 9 · 구성 요소 4");
    await page.screenshot({path:`output/playwright/assets-architecture-${suffix}.png`,fullPage:true});
    await page.getByRole("button",{name:/^공통 제어 Scheduler ·/}).click();
    check("scheduler_discloses_recent_list_scope", (await page.locator("#detail-reason").textContent()).includes("최근 20건"));
    check("scheduler_has_local_recovery_and_explicit_verification", (await page.locator("#detail-access-state").textContent()).includes("로컬 복구 활성") && (await page.locator("#detail-access").textContent()).includes("DB 연결 정상 2회"));
    await page.keyboard.press("Escape");
    await page.getByRole("button",{name:/^문제 실행 Runtime API/}).click();
    check("runtime_shows_missing_cluster_and_kubeconfig", (await page.locator("#detail-reason").textContent()).includes("kubeconfig"));
    await page.keyboard.press("Escape");
    await page.getByRole("tab",{name:"자산 상태",exact:true}).click();
    check("all_asset_status_rows_are_visible", await page.locator("#asset-groups .asset-row").count() === 25);
    check("absence_is_not_reported_as_failure", await page.locator("#incident-count").textContent() === "0" && await page.locator("#unknown-count").textContent() === "0");
    await page.screenshot({path:`output/playwright/assets-status-${suffix}.png`,fullPage:true});
    try {
      await page.context().setOffline(true);
      await page.getByRole("button",{name:"최신 점검 결과 가져오기",exact:true}).click();
      await page.waitForFunction(() => !document.getElementById("connection-warning").hidden);
      check("offline_clears_all_thirteen_healthy_readings", await page.locator("#healthy-count").textContent() === "0");
    } finally { await page.context().setOffline(false); }
    await page.getByRole("button",{name:"최신 점검 결과 가져오기",exact:true}).click();
    await page.waitForFunction(() => document.getElementById("healthy-count").textContent === "13");
    await page.setViewportSize({width:390,height:844});
    check("all_groups_fit_mobile_width", await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 2));
    await page.screenshot({path:`output/playwright/assets-mobile-${suffix}.png`,fullPage:true});
    check("no_javascript_errors",errors.length === 0);
    return {passed:true,checks,screenshots_suffix:suffix};
  } finally {
    await page.context().setOffline(false);
    await page.keyboard.press("Escape");
    await page.setViewportSize({width:1600,height:1000});
    await page.goto("http://127.0.0.1:58240/?view=architecture-1007");
    page.off("pageerror",onError);
  }
}
