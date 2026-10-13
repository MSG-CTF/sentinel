/* Progressive enhancement: monitoring and authentication do not depend on WebGL. */
(function () {
  "use strict";
  const reduced = matchMedia("(prefers-reduced-motion: reduce)");
  const elements = [...document.querySelectorAll("[data-sentinel-core]")];
  const controllers = new Map(elements.map(element => [element, {scene:null, loading:false, visible:false, view:"auto"}]));
  let paused = false, sceneModule, disposed = false;
  try { paused = localStorage.getItem("sentinel-motion") === "off"; } catch { /* Storage is optional. */ }

  // This facade is available synchronously, before the optional renderer loads.
  window.SentinelCore = {setState(element, state) {
    element.dataset.state = ["standby", "healthy", "attention", "incident", "offline"].includes(state) ? state : "standby";
    controllers.get(element)?.scene?.setState(element.dataset.state);
  }};

  function sync() {
    const stopped = paused || reduced.matches;
    document.documentElement.classList.toggle("motion-paused", stopped);
    document.querySelectorAll("[data-motion-toggle]").forEach(button => {
      button.setAttribute("aria-pressed", String(stopped));
      const label = reduced.matches ? "기기 설정에 따라 모션 꺼짐" : stopped ? "모션 켜기" : "모션 끄기";
      button.setAttribute("aria-label", label);
      button.title = label;
      const text = button.querySelector("span");
      if (text) text.textContent = stopped ? "MOTION OFF" : "MOTION ON";
    });
    controllers.forEach(controller => controller.scene?.setRunning(!stopped && controller.visible && !document.hidden));
  }

  async function load(element, controller) {
    if (controller.loading || controller.scene || disposed) return;
    controller.loading = true;
    try {
      sceneModule ??= import("/sentinel-scene.js");
      const {createMechanismScene} = await sceneModule;
      if (disposed) return;
      const scene = await createMechanismScene(element, {still:paused || reduced.matches});
      if (disposed) { scene.dispose(); return; }
      controller.scene = scene;
      scene.setState(element.dataset.state || "standby");
      scene.setView(controller.view);
      document.querySelectorAll(`[data-core-controls="${element.dataset.sentinelCore}"]`).forEach(controls => controls.hidden = false);
      sync();
    } catch {
      // The original artwork remains visible on devices without a usable GPU.
      element.dataset.renderer = "fallback";
    }
  }

  const observer = new IntersectionObserver(entries => {
    for (const entry of entries) {
      const controller = controllers.get(entry.target);
      controller.visible = entry.isIntersecting;
      if (entry.isIntersecting) load(entry.target, controller);
    }
    sync();
  }, {threshold:0.02});
  elements.forEach(element => observer.observe(element));

  document.querySelectorAll("[data-motion-toggle]").forEach(button => button.addEventListener("click", () => {
    if (reduced.matches) return;
    paused = !paused;
    try { localStorage.setItem("sentinel-motion", paused ? "off" : "on"); } catch { /* Storage is optional. */ }
    sync();
  }));
  document.querySelectorAll("[data-core-view]").forEach(button => button.addEventListener("click", () => {
    const controls = button.closest("[data-core-controls]");
    const element = elements.find(candidate => candidate.dataset.sentinelCore === controls.dataset.coreControls);
    const controller = controllers.get(element);
    if (!controller) return;
    controller.view = button.dataset.coreView;
    controls.querySelectorAll("[data-core-view]").forEach(candidate => candidate.setAttribute("aria-pressed", String(candidate === button)));
    controller.scene?.setView(controller.view);
  }));
  reduced.addEventListener("change", sync);
  document.addEventListener("visibilitychange", sync);
  window.addEventListener("pagehide", event => {
    controllers.forEach(controller => controller.scene?.setRunning(false));
    if (event.persisted) return;
    disposed = true;
    observer.disconnect();
    controllers.forEach(controller => controller.scene?.dispose());
  });
  window.addEventListener("pageshow", sync);
  sync();
})();
