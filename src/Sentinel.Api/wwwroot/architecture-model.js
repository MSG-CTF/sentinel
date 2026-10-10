/* Source audit and runtime evidence are separate. Endpoint health is never an RPC success proof. */
(function (root) {
  "use strict";
  const sources = {
    notion:{name:"운영 아키텍처 본문", url:"https://app.notion.com/p/3e20f19c72be80d1a2ebd1c9e8f2a894", version:"10월 10일 재조회 · 반환본 10월 7일"},
    backend:{name:"Backend main 소스", repo:"MSG-CTF/msg-backend", sha:"5896bb52e77a4ab2c4b4ace3718c1c9a73f3428f", reviewed_at:"2026-10-10"},
    scheduler:{name:"Scheduler dev 소스", repo:"MSG-CTF/instance-scheduler", sha:"5c695142871a5a926b96d7befcaf0832c691933f", reviewed_at:"2026-10-10"},
    broker:{name:"Broker staging 소스", repo:"MSG-CTF/resource-broker", sha:"99ff5b884e4bf2198dabca84a179c11f5a516785", reviewed_at:"2026-10-10"},
    runtime:{name:"Runtime dev", repo:"MSG-CTF/secure-provisioner", sha:"19d2fdffef753bb9415861c4a6b382b6408455ea", reviewed_at:"2026-10-10"},
    devsecops:{name:"DevSecOps main", repo:"MSG-CTF/msgctf-devsecops", sha:"cd47cb5c738ab612dae57e385368f279d26054aa"},
    frontend:{name:"Frontend 로컬 실행본", repo:"MSG-CTF/front-team", sha:"c0a85359be9878a212ab7b7ac955b898844430c5", reviewed_at:"2026-10-10"},
    challenges:{name:"Challenges main", repo:"MSG-CTF/2026_MSG_CTF", sha:"955027d92d4c0e69202d9709f4a2ea5cf9e96e3d"}
  };
  const nodes = [
    {id:"edge", name:"Edge Nginx", role:"EDGE", track:"ACCESS", scope:"공통 진입", stage:"DESIGN", owner:"DevSecOps", note:"로컬 Nginx의 내부·외부 포트별 프록시 추가 · 운영 Edge 배포와 도메인은 미확인", source:"notion"},
    ...["internal", "external"].flatMap(track => [
      {id:`${track}-frontend`, name:"Frontend", role:"FRONTEND", track:track.toUpperCase(), stage:"CODE", owner:"프론트 · DevSecOps", note:"실제 프론트 빌드를 트랙별 Backend에 연결 · 정적 화면과 API 전달 점검 · blue/green 전환은 별도", source:"frontend", path:"nginx.conf.template"},
      {id:`${track}-backend`, name:"Backend API", role:"BACKEND", track:track.toUpperCase(), probe_id:`${track}-backend`, owner:"백엔드", source:"backend", path:"apps/common/health.py"},
      {id:`${track}-backend-database`, name:"PostgreSQL", role:"POSTGRESQL", track:track.toUpperCase(), probe_id:`${track}-backend-database`, owner:"백엔드 · DevSecOps", source:"backend", path:"apps/common/health.py"},
      {id:`${track}-backend-cache`, name:"Redis", role:"REDIS", track:track.toUpperCase(), probe_id:`${track}-backend-cache`, owner:"백엔드 · DevSecOps", source:"backend", path:"apps/common/health.py"}
    ]),
    {id:"scheduler", name:"Scheduler", role:"SCHEDULER", track:"CONTROL", probe_id:"scheduler", owner:"스케줄러", source:"scheduler", path:"src/main/kotlin/kr/msgctf/scheduler/instance/dto/InstanceApiDtos.kt"},
    {id:"scheduler-db", name:"Scheduler DB", role:"POSTGRESQL", track:"CONTROL", stage:"CODE", owner:"스케줄러 · DevSecOps", note:"로컬 전용 PostgreSQL · Flyway migration과 Actuator DB 연결 점검", source:"scheduler", path:"src/main/kotlin/kr/msgctf/scheduler/instance/repository/InstanceRepository.kt"},
    {id:"broker", name:"Resource Broker", role:"BROKER", track:"CONTROL", probe_id:"broker", owner:"리소스 브로커", source:"broker", path:"app/api/candidates.py", note:"staging 99ff5b88을 별도 로컬 소스 사본으로 실행 · 실제 provider 계정과 VM은 미등록"},
    {id:"broker-db", name:"Broker DB", role:"POSTGRESQL", track:"CONTROL", stage:"CODE", owner:"리소스 브로커 · DevSecOps", note:"로컬 전용 PostgreSQL · Alembic migration과 readiness SELECT 1 점검", source:"broker", path:"app/db/tables.py"},
    {id:"cloud-api", name:"Cloud API", role:"CLOUD", track:"CONTROL", stage:"DESIGN", owner:"리소스 브로커 · DevSecOps", note:"계정별 quota·budget·credit와 VM 전원 상태 · 실제 provider client, 최소 권한과 점검 주소는 미확인", source:"notion"},
    {id:"runtime", name:"Runtime API", role:"RUNTIME", track:"RUNTIME", probe_id:"runtime", owner:"Runtime · 격리보안", source:"runtime", path:"internal/httpapi/http.go"},
    {id:"runtime-db", name:"Runtime Store", role:"POSTGRESQL", track:"RUNTIME", probe_id:"runtime-db", owner:"Runtime · DevSecOps", note:"로컬 PostgreSQL의 작업 기록을 Runtime API로 조회 · Runtime 중단 시 DB 단독 상태는 확인 불가", source:"runtime", path:"cmd/provisioner/main.go"},
    {id:"linux-pool", name:"Linux / K3s", role:"LINUX", track:"RUNTIME", probe_id:"linux-pool", owner:"Runtime · 격리보안", note:"로컬 K3s 단일 노드와 Nginx 연결 시험 인스턴스 · 대회 문제 전체나 운영 클러스터를 나타내지 않음", source:"runtime", path:"internal/k3s/adapter.go"},
    {id:"koth", name:"KoTH VM", role:"KOTH", track:"RUNTIME", probe_id:"koth", owner:"KoTH · DevSecOps", source:"notion", note:"KoTH 전용 VM은 Linux 문제의 일반 생성 경로와 구분 · 현재 점검 주소 미설정"},
    {id:"windows", name:"Windows VM", role:"WINDOWS", track:"RUNTIME", probe_id:"windows", owner:"Runtime · DevSecOps", source:"notion", note:"전용 VM 운영안 · Windows 생성·복구 계약과 실행 주소는 미확인"},
    {id:"koth-score", name:"KoTH Scoring", role:"SCORING", track:"SCORING", stage:"CODE", owner:"KoTH · 백엔드", note:"백엔드의 점수 API 주기 수집과 팀 토큰 검증 경로 확인 · 실제 채점 서버·주기 실행은 미연결", source:"backend", path:"apps/koth/services.py"},
    {id:"info", name:"info.yaml", role:"SPEC", track:"DELIVERY", stage:"CODE", owner:"문제 출제", note:"문제 사양의 입력 · 임의 Kubernetes manifest와 raw NetworkPolicy가 아닌 제한된 DSL", source:"challenges", path:".github/workflows/challenge-validation.yml"},
    {id:"ci", name:"DevSecOps CI", role:"CI", track:"DELIVERY", stage:"CODE", owner:"DevSecOps", note:"validation·보안 검사·SBOM·digest image와 bundle 발행 경로 소스 확인 · 이번 점검에서 Actions를 재실행하지 않음", source:"devsecops", path:"docs/devsecops-runbook.md"},
    {id:"ghcr", name:"GHCR Images", role:"REGISTRY", track:"DELIVERY", stage:"CODE", owner:"DevSecOps", note:"검사한 digest image 발행 경로 · 현재 private image pull과 K3s pull secret은 미검증", source:"devsecops", path:"docs/devsecops-runbook.md"},
    {id:"bundle", name:"Actions Bundle", role:"BUNDLE", track:"DELIVERY", stage:"CODE", owner:"DevSecOps", note:"성공한 publish-bundle의 artifact-v2.json을 백엔드 poller가 읽는 계약 · CI가 백엔드로 직접 POST하지 않음", source:"devsecops", path:"docs/challenge-registry-integration.md"},
    {id:"registry", name:"Challenge Registry", role:"REGISTRY", track:"DELIVERY", stage:"DESIGN", owner:"백엔드", note:"release 등록·중복 수집·active 전환은 백엔드 소유 · 현재 로컬 main에는 이 새 수집 경로가 없음", source:"devsecops", path:"docs/challenge-registry-integration.md"}
  ];
  const links = [
    {id:"edge-internal", from:"edge", to:"internal-frontend", label:"내부 도메인", stage:"DESIGN", note:"공통 Edge → 내부 앱 VM · 실제 도메인과 방화벽 미확정", source:"notion", owner:"DevSecOps"},
    {id:"edge-external", from:"edge", to:"external-frontend", label:"외부 도메인", stage:"DESIGN", note:"공통 Edge → 외부 앱 VM · 한 트랙을 다른 트랙 DB로 자동 전환하지 않음", source:"notion", owner:"DevSecOps"},
    ...["internal", "external"].flatMap(track => [
      {id:`${track}-proxy`, from:`${track}-frontend`, to:`${track}-backend`, label:"/api/v1", stage:"CODE", note:"Frontend Nginx의 BACKEND_URL 프록시 · 코드 확인이며 로컬 프론트 경유 E2E는 미실행", source:"frontend", path:"nginx.conf.template", owner:"프론트 · 백엔드 · DevSecOps"},
      ...[["database", "DB 연결"], ["cache", "Redis 연결"]].map(([key, label]) => ({id:`${track}-${key}`, from:`${track}-backend`, to:`${track}-backend-${key}`, label, stage:"PROBE", evidence_asset:`${track}-backend-${key}`, parent_probe:`${track}-backend`, note:"백엔드 healthz 구성 요소의 실제 연결 점검 · 데이터 VM 전원 상태나 트랜잭션 전체 검증은 아님", source:"backend", path:"apps/common/health.py", owner:"백엔드 · DevSecOps"})),
      {id:`${track}-create`, from:`${track}-backend`, to:"scheduler", label:"인스턴스 생성", stage:"BLOCKED", note:"Backend main은 container_image·container_port를 전송 · Scheduler dev는 containers·registry_revision·isolation_profile 필수 · 두 기준본의 요청 계약이 다름", source:"backend", path:"apps/instances/services.py", peer_source:"scheduler", peer_path:"src/main/kotlin/kr/msgctf/scheduler/instance/dto/InstanceApiDtos.kt", owner:"백엔드 · 스케줄러"}
    ]),
    {id:"scheduler-store", from:"scheduler", to:"scheduler-db", label:"상태 저장", stage:"CODE", note:"DB 노드에서 실제 연결을 감시 · 인스턴스 생성에 따른 상태 저장과 Active/Standby 승격은 별도 검증 필요", source:"scheduler", path:"src/main/kotlin/kr/msgctf/scheduler/instance/repository/InstanceRepository.kt", owner:"스케줄러"},
    {id:"candidate-query", from:"scheduler", to:"broker", label:"후보 · 예약", stage:"CODE", note:"Scheduler 호출자와 Broker staging의 후보·예약 수신 경로 확인 · 서비스 Bearer 인증 필요 · 실제 통신과 전체 계약 호환성 미검증", source:"scheduler", path:"src/main/kotlin/kr/msgctf/scheduler/broker/HttpBrokerClient.kt", peer_source:"broker", peer_path:"docs/SCHEDULER_API.md", owner:"스케줄러 · 리소스 브로커"},
    {id:"broker-store", from:"broker", to:"broker-db", label:"quota · 예약", stage:"CODE", note:"staging migration과 DB 노드의 SELECT 1 점검 · 실제 VM quota·예약과 관측 시각 기반 용량 계산은 별도 검증 필요", source:"broker", path:"app/db/tables.py", owner:"리소스 브로커"},
    {id:"broker-provider", from:"broker", to:"cloud-api", label:"quota · credit · VM", stage:"DESIGN", note:"계정별 리소스 여유·예산과 VM 상태를 provider API로 조회하는 운영안 · 이 로컬 환경에는 클라우드 자격증명이나 전원 조작을 연결하지 않음", source:"notion", owner:"리소스 브로커 · DevSecOps"},
    ...["internal", "external"].map(track=>({id:`${track}-inventory`, from:`${track}-backend`, to:"broker", label:"관리자 리소스 목록", stage:"CODE", note:"GET /v1/inventory/resource-targets의 양쪽 경로와 limit·offset 확인 · Broker는 별도 Inventory Bearer 인증 사용 · 실제 통신 미검증", source:"backend", path:"apps/adminpanel/resource_broker.py", peer_source:"broker", peer_path:"app/api/inventory.py", owner:"백엔드 · 리소스 브로커"})),
    {id:"runtime-create", from:"scheduler", to:"runtime", label:"생성 · 상태 · 삭제", stage:"CODE", note:"Bearer 인증 /internal/v1/instances 생성·삭제, runtime-status와 operations 조회 · 양쪽 경로 소스 확인, 실제 E2E 미실행", source:"scheduler", path:"src/main/kotlin/kr/msgctf/scheduler/runtime/HttpRuntimeClient.kt", peer_source:"runtime", peer_path:"internal/httpapi/http.go", owner:"스케줄러 · Runtime"},
    {id:"runtime-store", from:"runtime", to:"runtime-db", label:"operation · lease", stage:"PROBE", parent_probe:"runtime", evidence_asset:"runtime-db", note:"로컬 PostgreSQL store의 완료된 작업 기록 조회 · 현재 인스턴스 상태 점검과 함께 확인", source:"runtime", path:"cmd/provisioner/main.go", owner:"Runtime"},
    {id:"runtime-k3s", from:"runtime", to:"linux-pool", label:"workload · 격리", stage:"PROBE", parent_probe:"runtime", evidence_asset:"linux-pool", note:"로컬 연결 시험 인스턴스의 Pod·endpoint와 노드 Ready·pressure 조회 · 대회 문제 기능 검증은 별도", source:"runtime", path:"internal/k3s/adapter.go", owner:"Runtime · 격리보안"},
    {id:"scoring-server", from:"koth-score", to:"koth", label:"점수 수집", stage:"DESIGN", note:"채점 주체와 전용 VM의 배치·헬스체크·재시작 후 점수 보존 계약은 추가 확인 필요", source:"notion", owner:"KoTH · SLA"},
    ...["internal", "external"].flatMap(track => [
      {id:`${track}-score-poll`, from:`${track}-backend`, to:"koth-score", label:"period별 점수", stage:"CODE", note:"트랙별 백엔드의 GET score_api_url, period_id·scored_at, X-KOTH-Internal-Token · 실제 채점 서버 설정·주기 실행·마지막 성공 시각 미수집", source:"backend", path:"apps/koth/services.py", owner:"KoTH · 백엔드"},
      {id:`${track}-token`, from:"koth-score", to:`${track}-backend`, label:"팀 토큰 검증", stage:"CODE", note:"POST /api/v1/internal/koth/team_tokens/verify 수신 경로 확인 · 반드시 해당 트랙의 백엔드로 설정 · 지도에서 공통 역할로 그렸지만 팀·토큰·점수를 공유한다는 뜻은 아님", source:"backend", path:"apps/koth/internal_urls.py", owner:"KoTH · 백엔드"}
    ]),
    {id:"ci-source", from:"info", to:"ci", label:"검증 · build", stage:"CODE", note:"문제 저장소 caller → reusable workflow · 이번 조회는 고정 SHA 소스 확인이며 publish 재실행이 아님", source:"challenges", path:".github/workflows/challenge-validation.yml", owner:"문제 출제 · DevSecOps"},
    {id:"image-publish", from:"ci", to:"ghcr", label:"digest 발행", stage:"CODE", note:"검사한 image를 @sha256 참조로 발행 · tag나 latest를 실행 대상으로 사용하지 않음", source:"devsecops", path:"docs/devsecops-runbook.md", owner:"DevSecOps"},
    {id:"bundle-publish", from:"ci", to:"bundle", label:"artifact-v2", stage:"CODE", note:"성공한 publish-bundle Actions artifact에 저장 · 백엔드 주소와 토큰을 CI에 두는 직접 전송 경로가 아님", source:"devsecops", path:"docs/challenge-registry-integration.md", owner:"DevSecOps"},
    {id:"bundle-poll", from:"bundle", to:"registry", label:"poller 수집", stage:"BLOCKED", note:"DevSecOps의 공식 입력 계약은 확인 · 현재 Backend main에 poller·release Registry 구현이 없어 이 로컬 기준본으로는 수집 E2E를 진행하지 못함 · 열린 PR과 구분", source:"devsecops", path:"docs/challenge-registry-integration.md", peer_source:"backend", peer_path:"apps/instances/models.py", owner:"백엔드 · DevSecOps"},
    {id:"image-pull", from:"ghcr", to:"linux-pool", label:"digest pull", stage:"CODE", note:"Runtime의 이미지·imagePullSecrets 경로 소스 확인 · private GHCR cold pull과 pull secret 만료는 실제 시험 필요", source:"runtime", path:"internal/k3s/adapter.go", owner:"DevSecOps · Runtime"}
  ];
  const findings = [
    {id:"payload", title:"생성 요청 계약 불일치", owner:"백엔드 · 스케줄러", detail:"main 단일 컨테이너 요청과 dev 멀티 컨테이너 계약이 다름", link:"internal-create", severity:"BLOCKED"},
    {id:"route", title:"운영 healthz JSON 미확인", owner:"프론트 · DevSecOps", detail:"10월 10일 msgctf.kr/healthz는 HTML 200 · 배포 프록시 내부 설정은 미확인", track:"PUBLIC", url:"https://msgctf.kr/healthz", checked_at:"2026-10-10T01:21:42+09:00", severity:"BLOCKED"},
    {id:"broker-source", title:"Broker 운영 대상 미등록", owner:"리소스 브로커", detail:"staging 99ff5b88 로컬 실행과 인증된 목록 점검 추가 · provider 계정·VM과 운영 실행 기준은 미확정", link:"candidate-query", severity:"PENDING"}
  ];
  const gates = [
    {name:"Worker 단일 실행", owner:"스케줄러 · DevSecOps", note:"팀·행 잠금과 프로세스 leader lock은 다름 · Active/Standby 승격 계약과 fencing 미검증"},
    {name:"백업 · 복원 · rollback", owner:"DevSecOps · 백엔드", note:"로컬 저장소 6개 복원·앱 4개 조회·원래 볼륨 복귀 확인 · 운영 데이터와 무중단 전환·VM HA는 별도 검증 필요"},
    {name:"문제 inventory · readiness", owner:"Runtime · 스케줄러 · SLA", note:"로컬 고정 인스턴스의 operation·Pod Ready·endpoint 확인 · Scheduler recent는 최근 20건이며 전체 문제 목록과 문제 기능 점검은 연결 대기"},
    {name:"운영 복구 연결", owner:"SLA · 각 수신팀", note:"기본 앱 7개와 선택한 로컬 Runtime의 정지 컨테이너 기동 · 운영 호스트 접속·자격 증명·수신 API·상태 보존 계약은 연결 대기"}
  ];
  const catalog = {schema_version:1, verified_at:"2026-10-10T01:24:00+09:00", scope:"10월 10일 Backend·Scheduler·Broker·Runtime·Frontend 소스 확인 · 로컬 점검 범위를 25개 역할로 확장 · 각 점검의 확인 범위와 운영 배포 버전은 별도", sources, nodes, links, findings, gates};
  function resolveNodes(assets) {
    const readings = new Map(assets.map(asset => [asset.asset_id, asset]));
    const result = nodes.map(definition => ({...definition, probe_id:definition.probe_id || (readings.has(definition.id) ? definition.id : undefined), reading:readings.get(definition.probe_id || definition.id) || null}));
    const mapped = new Set(nodes.map(definition => definition.probe_id || definition.id));
    for (const asset of assets) if (!mapped.has(asset.asset_id)) result.push({id:asset.asset_id, name:asset.name, role:asset.role, track:asset.track, probe_id:asset.asset_id, reading:asset, owner:"감시 대상 설정", note:"프로필에 추가한 실제 감시 대상 · 운영 트랙과 임의로 합치지 않음"});
    return result;
  }
  function resolveLink(link, assets) {
    if (link.stage !== "PROBE") return {...link, state:link.stage, verified:false};
    const child = assets.find(asset => asset.asset_id === link.evidence_asset);
    const parent = assets.find(asset => asset.asset_id === link.parent_probe);
    if (["runtime-store", "runtime-k3s"].includes(link.id)) {
      const expectedKind = link.id === "runtime-store" ? "runtime_operation" : "runtime_node";
      const reason = link.id === "runtime-store" ? "RUNTIME_STORE_QUERY_READY" : "K3S_NODE_READY";
      const valid = parent?.kind === "runtime_status" && parent.status === "HEALTHY" && parent.reason_code === "RUNTIME_WORKLOAD_READY"
        && child?.kind === expectedKind && child.status === "HEALTHY" && child.reason_code === reason
        && Number.isFinite(Date.parse(child.checked_at)) && Number.isFinite(Date.parse(parent.checked_at))
        && Math.abs(Date.parse(child.checked_at) - Date.parse(parent.checked_at)) < 10000
        && Math.abs(Date.now() - Date.parse(child.checked_at)) < 60000;
      return {...link, state:valid ? "VERIFIED" : child && ["UNCONFIGURED", "PENDING"].includes(child.status) ? "PENDING" : "UNKNOWN",
        verified:valid, checked_at:valid ? child.checked_at : null};
    }
    // Only this explicit parent-child healthz relation is a live connection reading.
    const valid = child && parent && parent.kind === "backend_healthz" && ["HEALTHY", "DEGRADED"].includes(parent.status)
      && child.evidence_type === "HEALTHZ_COMPONENT" && child.parent_target_id === link.parent_probe
      && child.checked_at && child.checked_at === parent.checked_at;
    const state = valid && child.status === "HEALTHY" ? "VERIFIED" : valid && child.status === "DOWN" ? "FAILED"
      : child && ["UNCONFIGURED", "PENDING"].includes(child.status) ? "PENDING" : "UNKNOWN";
    return {...link, state, verified:state === "VERIFIED", checked_at:valid ? child.checked_at : null};
  }
  function sourceLink(key, path) {
    const source = sources[key];
    if (!source) return null;
    return {name:source.name, version:source.version || source.sha.slice(0,7), url:source.url || `https://github.com/${source.repo}/blob/${source.sha}/${path || "README.md"}`};
  }
  const api = {catalog, resolveNodes, resolveLink, sourceLink};
  root.SentinelArchitecture = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
