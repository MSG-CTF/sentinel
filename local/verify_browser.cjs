async (page) => {
  const checks = [];
  const errors = [];
  const onError = error => errors.push(error.name);
  page.on("pageerror", onError);
  const check = (name, ok) => { if (!ok) throw new Error(name); checks.push(name); };
  const state = () => page.evaluate(() => ({
    profile: document.getElementById("profile").textContent,
    healthy: Number(document.getElementById("healthy-count").textContent),
    pending: Number(document.getElementById("pending-count").textContent),
    total: Number(document.getElementById("total-count").textContent),
    verified: Number(document.getElementById("verified-links").textContent)
  }));
  try {
    await page.keyboard.press("Escape");
    await page.getByRole("tab", {name:"자산 상태", exact:true}).click();
    await page.waitForFunction(() => document.getElementById("healthy-count").textContent === "13");
    const live = await state();
    check("local_counts_keep_twelve_unconnected_roles_separate", live.profile === "LOCAL" && live.healthy === 13 && live.pending === 12 && live.total === 25 && live.verified === 4);
    try {
      await page.context().setOffline(true);
      await page.getByRole("button", {name:"최신 점검 결과 가져오기", exact:true}).click();
      await page.waitForFunction(() => !document.getElementById("connection-warning").hidden);
      const offline = await state();
      check("offline_removes_all_healthy_assets_and_verified_links", offline.healthy === 0 && offline.verified === 0 && offline.pending === 12);
      await page.screenshot({path:"output/playwright/offline-20261010.png", fullPage:true});
    } finally {
      await page.context().setOffline(false);
    }
    await page.getByRole("button", {name:"최신 점검 결과 가져오기", exact:true}).click();
    await page.waitForFunction(() => document.getElementById("healthy-count").textContent === "13");
    check("online_restores_only_measured_assets", (await state()).healthy === 13);
    await page.screenshot({path:"output/playwright/status-restored-20261010.png", fullPage:true});
    await page.getByRole("tab", {name:"연결 검토", exact:true}).click();
    const notes = page.locator(".audit-notes").first();
    if (!await notes.getAttribute("open").then(value => value !== null)) await notes.locator("summary").click();
    await page.getByRole("button", {name:"운영 healthz JSON 미확인 프론트 · DevSecOps", exact:true}).click();
    check("public_health_finding_has_no_assigned_track", await page.locator("#detail-group").textContent() === "배포 사이트" && (await page.locator("#detail-metrics").textContent()).includes("미확정"));
    await page.keyboard.press("Escape");
    check("escape_closes_details", !await page.getByRole("dialog").isVisible());
    await page.getByRole("tab", {name:"전체 구조", exact:true}).click();
    await page.setViewportSize({width:1600,height:1000});
    await page.getByRole("button", {name:"구조도 기본 배율로 돌아가기", exact:true}).click();
    const dimensions = () => page.evaluate(() => {
      const canvas = document.getElementById("architecture-map"), viewport = document.getElementById("map-viewport");
      return {transform:getComputedStyle(canvas).transform, width:viewport.clientWidth, scroll_width:viewport.scrollWidth,
        svg_width:canvas.querySelector("svg").getBoundingClientRect().width,
        node_width:canvas.querySelector("button").getBoundingClientRect().width};
    });
    const before = await dimensions();
    check("desktop_map_fits_viewport", before.scroll_width <= before.width + 4);
    await page.getByRole("button", {name:"구조도 확대", exact:true}).click();
    const zoomed = await dimensions();
    check("nodes_and_connections_scale_together", zoomed.node_width > before.node_width && Math.abs(zoomed.node_width / before.node_width - zoomed.svg_width / before.svg_width) < 0.01);
    await page.getByRole("button", {name:"구조도 기본 배율로 돌아가기", exact:true}).click();
    await page.screenshot({path:"output/playwright/architecture-restored-20261010.png", fullPage:true});
    await page.setViewportSize({width:390,height:844});
    await page.getByRole("button", {name:"구조도 기본 배율로 돌아가기", exact:true}).click();
    check("mobile_map_has_pan_space", (await dimensions()).scroll_width > (await dimensions()).width);
    await page.locator("#map-viewport").hover();
    await page.mouse.wheel(420,0);
    await page.waitForFunction(() => document.getElementById("map-viewport").scrollLeft >= 300, null, {timeout:3000});
    await page.getByRole("button", {name:"최신 점검 결과 가져오기", exact:true}).click();
    await page.waitForFunction(() => !document.getElementById("refresh").disabled);
    check("mobile_pan_survives_status_refresh", await page.locator("#map-viewport").evaluate(element => element.scrollLeft) >= 300);
    checks.push("mobile_horizontal_pan_moves_map");
    await page.getByRole("button", {name:"내부 트랙 Backend API · 정상", exact:true}).click();
    await page.keyboard.press("Escape");
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    check("mobile_pan_survives_detail_open_and_close", await page.locator("#map-viewport").evaluate(element => element.scrollLeft) >= 300);
    await page.screenshot({path:"output/playwright/mobile-pan-20261010.png", fullPage:true});
    check("no_javascript_page_errors", errors.length === 0);
    return {passed:true,checks};
  } finally {
    await page.context().setOffline(false);
    await page.setViewportSize({width:1600,height:1000});
    await page.keyboard.press("Escape");
    await page.getByRole("tab", {name:"전체 구조", exact:true}).click();
    await page.getByRole("button", {name:"구조도 기본 배율로 돌아가기", exact:true}).click();
    page.off("pageerror", onError);
  }
}
