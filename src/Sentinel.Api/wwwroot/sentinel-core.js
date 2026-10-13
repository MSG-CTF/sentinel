/* The illustrated mechanism carries no telemetry. All readings remain in the DOM. */
(function () {
  "use strict";
  const reduced = matchMedia("(prefers-reduced-motion: reduce)");
  const finePointer = matchMedia("(hover: hover) and (pointer: fine)");
  const mechanisms = [...document.querySelectorAll("[data-sentinel-core]")];
  const frames = new Map();
  let paused = false;
  try { paused = localStorage.getItem("sentinel-motion") === "off"; } catch { /* Optional preference storage. */ }

  function reset(element) {
    cancelAnimationFrame(frames.get(element));
    frames.delete(element);
    element.dataset.interacting = "false";
    for (const property of ["--tilt-x", "--tilt-y", "--shift-x", "--shift-y", "--light-x", "--light-y"])
      element.style.removeProperty(property);
  }

  for (const element of mechanisms) {
    let pointer = null;
    element.addEventListener("pointermove", event => {
      if (paused || reduced.matches || !finePointer.matches || document.hidden) return;
      pointer = {x:event.clientX, y:event.clientY};
      if (frames.has(element)) return;
      frames.set(element, requestAnimationFrame(() => {
        frames.delete(element);
        if (!pointer || paused || reduced.matches || document.hidden) return;
        const box = element.getBoundingClientRect();
        if (!box.width || !box.height) return;
        const x = Math.max(-.5, Math.min(.5, (pointer.x - box.left) / box.width - .5));
        const y = Math.max(-.5, Math.min(.5, (pointer.y - box.top) / box.height - .5));
        element.style.setProperty("--tilt-x", `${(-y * 3).toFixed(2)}deg`);
        element.style.setProperty("--tilt-y", `${(x * 4).toFixed(2)}deg`);
        element.style.setProperty("--shift-x", `${(x * 4).toFixed(2)}px`);
        element.style.setProperty("--shift-y", `${(y * 3).toFixed(2)}px`);
        element.style.setProperty("--light-x", `${((x + .5) * 100).toFixed(1)}%`);
        element.style.setProperty("--light-y", `${((y + .5) * 100).toFixed(1)}%`);
        element.dataset.interacting = "true";
      }));
    });
    element.addEventListener("pointerleave", () => {pointer = null; reset(element);});
    element.addEventListener("pointercancel", () => {pointer = null; reset(element);});
  }

  function syncControls() {
    const stopped = paused || reduced.matches;
    document.documentElement.classList.toggle("motion-paused", stopped);
    document.querySelectorAll("[data-motion-toggle]").forEach(button => {
      button.setAttribute("aria-pressed", String(stopped));
      button.setAttribute("aria-label", reduced.matches ? "기기 설정에 따라 모션 꺼짐" : stopped ? "모션 켜기" : "모션 끄기");
      button.title = reduced.matches ? "기기의 동작 줄이기 설정 적용 중" : stopped ? "모션 켜기" : "모션 끄기";
      const label = button.querySelector("span");
      if (label) label.textContent = stopped ? "MOTION OFF" : "MOTION ON";
    });
    mechanisms.forEach(reset);
  }
  document.querySelectorAll("[data-motion-toggle]").forEach(button => button.addEventListener("click", () => {
    if (reduced.matches) return;
    paused = !paused;
    try { localStorage.setItem("sentinel-motion", paused ? "off" : "on"); } catch { /* Optional preference storage. */ }
    syncControls();
  }));
  reduced.addEventListener("change", syncControls);
  finePointer.addEventListener("change", () => mechanisms.forEach(reset));
  document.addEventListener("visibilitychange", () => {if (document.hidden) mechanisms.forEach(reset);});
  syncControls();
  window.SentinelCore = {setState(element, state) {
    element.dataset.state = ["standby", "healthy", "attention", "incident", "offline"].includes(state) ? state : "standby";
  }};
})();
