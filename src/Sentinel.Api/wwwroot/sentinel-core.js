/* Progressive enhancement: monitoring and authentication do not depend on WebGL. */
(function () {
  "use strict";
  const reduced = matchMedia("(prefers-reduced-motion: reduce)");
  const elements = [...document.querySelectorAll("[data-sentinel-core]")];
  const controllers = new Map(elements.map(element => [element, {scene:null, loading:false, visible:false, view:"auto", abort:null}]));
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
    const started=performance.now(),timings={};
    const startup=document.createElement('div');startup.className='mechanism-startup';startup.setAttribute('aria-hidden','true');
    startup.innerHTML=`<div class="startup-instrument"><svg viewBox="0 0 200 200" aria-hidden="true">
      <g class="startup-index"><path d="M100 8v9m92 83h-9m-83 92v-9M8 100h9M35 35l6 6m124-6-6 6m6 124-6-6M35 165l6-6"/></g>
      <g class="startup-bridges"><path pathLength="1" d="M42 63A69 69 0 0 1 137 42"/><path pathLength="1" d="M158 63A69 69 0 0 1 137 158"/><path pathLength="1" d="M137 158A69 69 0 0 1 42 137"/><path pathLength="1" d="M42 137A69 69 0 0 1 42 63"/></g>
      <circle class="startup-lens" cx="100" cy="100" r="45" pathLength="1"/>
      <path class="startup-aperture" d="m100 66 30 17v34l-30 17-30-17V83Z"/>
    </svg><span>S<small>/ 01</small></span></div><div class="startup-copy"><span>OPTICAL SYSTEM</span><span data-startup-label>윤곽 준비</span></div><div class="startup-stages"><i></i><i></i><i></i><i></i></div>`;
    element.append(startup);element.dataset.boot='loading';
    const stage=name=>{
      timings[name]=Math.round(performance.now()-started);
      element.dataset.loadStage=name;element.dataset.loadTimings=JSON.stringify(timings);
      startup.querySelector('[data-startup-label]').textContent={modules:'윤곽 준비',geometry:'구조 정렬',environment:'조명 준비',compile:'장면 여는 중',ready:'준비 완료',fallback:'정적 화면'}[name]||'시각화 준비';
    };
    stage('modules');
    // Fetch the environment alongside the module graph, not after it. Failure
    // is consumed immediately, including when WebGL or the module import fails.
    const abort=controller.abort=new AbortController(),download=new AbortController();
    abort.signal.addEventListener('abort',()=>download.abort(),{once:true});
    const deadline=setTimeout(()=>download.abort(),6000);
    const environmentData=fetch('/assets/studio-small-09.hdr',{signal:download.signal})
      .then(response=>response.ok?response.arrayBuffer():null).catch(()=>null).finally(()=>clearTimeout(deadline));
    try {
      sceneModule ??= import("/sentinel-scene.js");
      const {createMechanismScene} = await sceneModule;
      if (disposed) return;
      const scene = await createMechanismScene(element, {still:paused || reduced.matches, environmentData, onProgress:stage, signal:abort.signal});
      if (disposed) { scene.dispose(); return; }
      controller.scene = scene;
      scene.setState(element.dataset.state || "standby");
      scene.setView(controller.view);
      stage('ready');element.dataset.boot='ready';
      document.querySelectorAll(`[data-core-controls="${element.dataset.sentinelCore}"]`).forEach(controls => controls.hidden = false);
      sync();
    } catch {
      // The original artwork remains visible on devices without a usable GPU.
      element.dataset.renderer = "fallback";
      stage('fallback');element.dataset.boot='fallback';
    } finally {
      clearTimeout(deadline);abort.abort();controller.abort=null;
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
    controllers.forEach(controller => {controller.abort?.abort();controller.scene?.dispose();});
  });
  window.addEventListener("pageshow", sync);
  sync();
})();
