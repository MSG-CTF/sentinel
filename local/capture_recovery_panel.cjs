async (page) => {
  await page.goto("http://127.0.0.1:58240/?view=architecture-1007");
  await page.setViewportSize({width:1440,height:1000});
  await page.waitForFunction(() => document.getElementById("healthy-count").textContent === "13");
  await page.getByRole("tab", {name:"자산 상태",exact:true}).click();
  const scope = await page.locator("#recovery-scope").textContent();
  if (!scope.includes("시간 초과 3회")) throw new Error("Final recovery policy copy is stale");
  await page.locator(".recovery-panel").screenshot({path:"output/playwright/recovery-seven-services-20261010.png"});
  const result = {scope,executor:await page.locator("#recovery-executor").textContent(),rows:await page.locator("#recovery-feed li").count()};
  await page.setViewportSize({width:1600,height:1000});
  await page.getByRole("tab", {name:"전체 구조",exact:true}).click();
  return result;
}
