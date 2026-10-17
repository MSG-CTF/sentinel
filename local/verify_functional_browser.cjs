async (page) => {
  const checks = [], errors = [];
  const check = (name, passed) => { if (!passed) throw new Error(name); checks.push(name); };
  const onError = error => errors.push(error.name);
  page.on("pageerror", onError);
  try {
    await page.getByRole("tab", {name:"자산 상태",exact:true}).click();
    await page.waitForFunction(() => !document.getElementById("refresh").disabled);
    await page.getByRole("button", {name:"최신 점검 결과 가져오기",exact:true}).click();
    await page.waitForFunction(() => !document.getElementById("refresh").disabled);
    const live = await (await page.request.get("http://127.0.0.1:58240/api/instances")).json();
    check("real_functional_pass_present", live.instances.some(row => row.challenge_health === "UP"
      && row.functional_check?.scope === "local_functional_fixture"));
    check("real_deleted_instances_visible", live.instances.filter(row => row.status === "REMOVED").length >= 2);
    check("all_recent_rows_rendered", await page.locator("#instance-list li").count() === live.instances.length);
    check("local_fixture_scope_visible", (await page.locator("#instance-list").textContent()).includes("로컬 기능 UP · 대회 문제 기능과 별도"));
    check("sixteen_assets_still_healthy", await page.locator("#healthy-count").textContent() === "16");
    check("no_automatic_instance_reset_control", await page.locator(".instance-panel button").count() === 0);
    await page.setViewportSize({width:1440,height:1000});
    await page.locator(".instance-panel").screenshot({path:"output/playwright/functional-live-desktop-20261013.png"});
    await page.setViewportSize({width:390,height:844});
    check("mobile_no_overflow", await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    await page.locator(".instance-panel").screenshot({path:"output/playwright/functional-live-mobile-20261013.png"});
    check("no_javascript_errors", errors.length === 0);
    return {passed:true, checks, real_runtime_rows:true, synthetic_ui_rows:false};
  } finally {
    await page.setViewportSize({width:1440,height:1000});
    page.off("pageerror", onError);
  }
}
