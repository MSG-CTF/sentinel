/* Source audit and runtime evidence are separate. Endpoint health is never an RPC success proof. */
(function (root) {
  "use strict";
  const sources = {
    production_audit:{name:"운영 실행 검증 기록",url:"https://sla.msgctf.kr/architecture-execution-evidence.json",version:"2026년 11월 3일 · 검증 범위와 정리 결과"},
    notion:{name:"운영 아키텍처 본문", url:"https://app.notion.com/p/3e20f19c72be80d1a2ebd1c9e8f2a894", version:"10월 10일 재조회 · 반환본 10월 7일"},
    backend:{name:"Backend main 소스", repo:"MSG-CTF/msg-backend", sha:"5896bb52e77a4ab2c4b4ace3718c1c9a73f3428f", reviewed_at:"2026-10-10"},
    backend_review:{name:"Backend KoTH·Registry 재검토", repo:"MSG-CTF/msg-backend", sha:"08be84d46f49c6f51947a871cb0003c49ed531ed", reviewed_at:"2026-10-13"},
    scheduler:{name:"Scheduler dev 소스", repo:"MSG-CTF/instance-scheduler", sha:"5c695142871a5a926b96d7befcaf0832c691933f", reviewed_at:"2026-10-10"},
    broker:{name:"Broker staging 소스", repo:"MSG-CTF/resource-broker", sha:"99ff5b884e4bf2198dabca84a179c11f5a516785", reviewed_at:"2026-10-10"},
    runtime:{name:"Runtime dev", repo:"MSG-CTF/secure-provisioner", sha:"19d2fdffef753bb9415861c4a6b382b6408455ea", reviewed_at:"2026-10-10"},
    devsecops:{name:"DevSecOps main", repo:"MSG-CTF/msgctf-devsecops", sha:"cd47cb5c738ab612dae57e385368f279d26054aa"},
    devsecops_review:{name:"DevSecOps Registry 계약 재검토", repo:"MSG-CTF/msgctf-devsecops", sha:"40513968fa9e4a8a624dc7c11a6e0a399aada323", reviewed_at:"2026-10-13"},
    frontend:{name:"Frontend 로컬 실행본", repo:"MSG-CTF/front-team", sha:"c0a85359be9878a212ab7b7ac955b898844430c5", reviewed_at:"2026-10-10"},
    challenges:{name:"Challenges main", repo:"MSG-CTF/2026_MSG_CTF", sha:"3a961c945e544c9a50fd7938868d67f07fe78828", reviewed_at:"2026-10-13"}
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
    {id:"broker", name:"Resource Broker", role:"BROKER", track:"CONTROL", probe_id:"broker", owner:"리소스 브로커", source:"broker", path:"app/api/candidates.py", note:"staging 99ff5b88과 실습 전용 자원 1개로 예약·반납 검증 · 실제 Cloud API와 VM은 미연결"},
    {id:"broker-db", name:"Broker DB", role:"POSTGRESQL", track:"CONTROL", stage:"CODE", owner:"리소스 브로커 · DevSecOps", note:"로컬 전용 PostgreSQL · Alembic migration과 readiness SELECT 1 점검", source:"broker", path:"app/db/tables.py"},
    {id:"cloud-api", name:"Cloud API", role:"CLOUD", track:"CONTROL", stage:"DESIGN", owner:"리소스 브로커 · DevSecOps", note:"계정별 quota·budget·credit와 VM 전원 상태 · 실제 provider client, 최소 권한과 점검 주소는 미확인", source:"notion"},
    {id:"runtime", name:"Runtime API", role:"RUNTIME", track:"RUNTIME", probe_id:"runtime", owner:"Runtime · 격리보안", source:"runtime", path:"internal/httpapi/http.go"},
    {id:"runtime-db", name:"Runtime Store", role:"POSTGRESQL", track:"RUNTIME", probe_id:"runtime-db", owner:"Runtime · DevSecOps", note:"로컬 PostgreSQL의 작업 기록을 Runtime API로 조회 · Runtime 중단 시 DB 단독 상태는 확인 불가", source:"runtime", path:"cmd/provisioner/main.go"},
    {id:"linux-pool", name:"Linux / K3s", role:"LINUX", track:"RUNTIME", probe_id:"linux-pool", owner:"Runtime · 격리보안", note:"로컬 K3s 단일 노드와 Nginx 연결 시험 인스턴스 · 대회 문제 전체나 운영 클러스터를 나타내지 않음", source:"runtime", path:"internal/k3s/adapter.go"},
    {id:"koth", name:"KoTH VM", role:"KOTH", track:"RUNTIME", probe_id:"koth", owner:"KoTH · DevSecOps", source:"challenges", path:"koth-card-battle-arena/prob/for_organizer/service/main.py", note:"실제 KoTH 서비스의 healthz·점수 API 구현 확인 · 로컬 실행 대상과 점검 주소 미등록"},
    {id:"windows", name:"Windows VM", role:"WINDOWS", track:"RUNTIME", probe_id:"windows", owner:"Runtime · DevSecOps", source:"notion", note:"전용 VM 운영안 · Windows 생성·복구 계약과 실행 주소는 미확인"},
    {id:"koth-score", name:"KoTH Scoring", role:"SCORING", track:"SCORING", stage:"CODE", owner:"KoTH · 백엔드", note:"Backend 점수 수집 명령과 같은 period 재시도 코드 확인 · 로컬 채점 서버·주기 실행·마지막 성공 period는 미연결", source:"backend_review", path:"apps/koth/services.py"},
    {id:"info", name:"info.yaml", role:"SPEC", track:"DELIVERY", stage:"CODE", owner:"문제 출제", note:"문제 사양의 입력 · 임의 Kubernetes manifest와 raw NetworkPolicy가 아닌 제한된 DSL", source:"challenges", path:".github/workflows/challenge-validation.yml"},
    {id:"ci", name:"DevSecOps CI", role:"CI", track:"DELIVERY", stage:"CODE", owner:"DevSecOps", note:"10월 13일 조회 · main 3a961c9의 pwn-random6 발행은 Trivy 검사 단계 실패 · 별도 승인 WEB 실행 점검 성공과 구분", source:"challenges", path:".github/workflows/challenge-validation.yml"},
    {id:"ghcr", name:"GHCR Images", role:"REGISTRY", track:"DELIVERY", stage:"CODE", owner:"DevSecOps", note:"검사한 digest image 발행 경로 · 현재 private image pull과 K3s pull secret은 미검증", source:"devsecops", path:"docs/devsecops-runbook.md"},
    {id:"bundle", name:"Actions Bundle", role:"BUNDLE", track:"DELIVERY", stage:"CODE", owner:"DevSecOps", note:"Backend poller는 성공한 publish-bundle의 artifact-v2.json을 수집 · 이번 main 발행 run에는 publish-bundle 산출물 없음", source:"devsecops_review", path:"docs/challenge-registry-integration.md"},
    {id:"registry", name:"Challenge Registry", role:"REGISTRY", track:"DELIVERY", stage:"DESIGN", owner:"백엔드", note:"최신 Backend main에도 release Registry 수신 경로 없음 · PR 26은 열림 · 로컬 수집 연결 대기", source:"backend_review", path:"config/urls.py"}
  ];
  const links = [
    {id:"edge-internal", from:"edge", to:"internal-frontend", label:"내부 도메인", stage:"DESIGN", note:"공통 Edge → 내부 앱 VM · 실제 도메인과 방화벽 미확정", source:"notion", owner:"DevSecOps"},
    {id:"edge-external", from:"edge", to:"external-frontend", label:"외부 도메인", stage:"DESIGN", note:"공통 Edge → 외부 앱 VM · 한 트랙을 다른 트랙 DB로 자동 전환하지 않음", source:"notion", owner:"DevSecOps"},
    ...["internal", "external"].flatMap(track => [
      {id:`${track}-proxy`, from:`${track}-frontend`, to:`${track}-backend`, label:"/api/v1", stage:"CODE", note:"로컬 두 트랙에서 Frontend·Edge 경유 응답과 실제 Backend 응답 일치 확인 · 로그인·참가자 전체 흐름은 별도", source:"frontend", path:"nginx.conf.template", owner:"프론트 · 백엔드 · DevSecOps"},
      ...[["database", "DB 연결"], ["cache", "Redis 연결"]].map(([key, label]) => ({id:`${track}-${key}`, from:`${track}-backend`, to:`${track}-backend-${key}`, label, stage:"PROBE", evidence_asset:`${track}-backend-${key}`, parent_probe:`${track}-backend`, note:"백엔드 healthz 구성 요소의 실제 연결 점검 · 데이터 VM 전원 상태나 트랜잭션 전체 검증은 아님", source:"backend", path:"apps/common/health.py", owner:"백엔드 · DevSecOps"})),
      {id:`${track}-create`, from:`${track}-backend`, to:"scheduler", label:"인스턴스 생성", stage:"BLOCKED", note:"Backend main은 container_image·container_port를 전송 · Scheduler dev는 containers·registry_revision·isolation_profile 필수 · 두 기준본의 요청 계약이 다름", source:"backend", path:"apps/instances/services.py", peer_source:"scheduler", peer_path:"src/main/kotlin/kr/msgctf/scheduler/instance/dto/InstanceApiDtos.kt", owner:"백엔드 · 스케줄러"}
    ]),
    {id:"scheduler-store", from:"scheduler", to:"scheduler-db", label:"상태 저장", stage:"CODE", note:"로컬 시험 인스턴스 생성·TTL 종료 상태 저장 확인 · Active/Standby 승격은 별도 검증 필요", source:"scheduler", path:"src/main/kotlin/kr/msgctf/scheduler/instance/repository/InstanceRepository.kt", owner:"스케줄러"},
    {id:"candidate-query", from:"scheduler", to:"broker", label:"후보 · 예약", stage:"CODE", note:"실제 로컬 Scheduler → Broker 예약·확정·반납 검증 · 수동 등록한 실습 자원 1개이며 provider 수집과 전체 계약은 별도", source:"scheduler", path:"src/main/kotlin/kr/msgctf/scheduler/broker/HttpBrokerClient.kt", peer_source:"broker", peer_path:"docs/SCHEDULER_API.md", owner:"스케줄러 · 리소스 브로커"},
    {id:"broker-store", from:"broker", to:"broker-db", label:"quota · 예약", stage:"CODE", note:"staging migration과 DB 노드의 SELECT 1 점검 · 실제 VM quota·예약과 관측 시각 기반 용량 계산은 별도 검증 필요", source:"broker", path:"app/db/tables.py", owner:"리소스 브로커"},
    {id:"broker-provider", from:"broker", to:"cloud-api", label:"quota · credit · VM", stage:"DESIGN", note:"계정별 리소스 여유·예산과 VM 상태를 provider API로 조회하는 운영안 · 이 로컬 환경에는 클라우드 자격증명이나 전원 조작을 연결하지 않음", source:"notion", owner:"리소스 브로커 · DevSecOps"},
    ...["internal", "external"].map(track=>({id:`${track}-inventory`, from:`${track}-backend`, to:"broker", label:"관리자 리소스 목록", stage:"CODE", note:"GET /v1/inventory/resource-targets의 양쪽 경로와 limit·offset 확인 · Broker는 별도 Inventory Bearer 인증 사용 · 실제 통신 미검증", source:"backend", path:"apps/adminpanel/resource_broker.py", peer_source:"broker", peer_path:"app/api/inventory.py", owner:"백엔드 · 리소스 브로커"})),
    {id:"runtime-create", from:"scheduler", to:"runtime", label:"생성 · 상태 · 삭제", stage:"CODE", note:"로컬 계산 시험 이미지로 Scheduler → Runtime/K3s 생성·기능 조회·삭제·TTL 종료 검증 · 실제 대회 문제와 발행 경로는 별도", source:"scheduler", path:"src/main/kotlin/kr/msgctf/scheduler/runtime/HttpRuntimeClient.kt", peer_source:"runtime", peer_path:"internal/httpapi/http.go", owner:"스케줄러 · Runtime"},
    {id:"runtime-store", from:"runtime", to:"runtime-db", label:"operation · lease", stage:"PROBE", parent_probe:"runtime", evidence_asset:"runtime-db", note:"로컬 PostgreSQL store의 완료된 작업 기록 조회 · 현재 인스턴스 상태 점검과 함께 확인", source:"runtime", path:"cmd/provisioner/main.go", owner:"Runtime"},
    {id:"runtime-k3s", from:"runtime", to:"linux-pool", label:"workload · 격리", stage:"PROBE", parent_probe:"runtime", evidence_asset:"linux-pool", note:"로컬 연결 시험 인스턴스의 Pod·endpoint와 노드 Ready·pressure 조회 · 대회 문제 기능 검증은 별도", source:"runtime", path:"internal/k3s/adapter.go", owner:"Runtime · 격리보안"},
    {id:"scoring-server", from:"koth-score", to:"koth", label:"점수 수집", stage:"CODE", note:"card-battle 서비스의 GET /internal/koth/scores와 Backend 수집 코드 확인 · 실제 로컬 수집·재시작 후 점수 보존은 미검증", source:"challenges", path:"koth-card-battle-arena/prob/for_organizer/service/main.py", owner:"KoTH · SLA"},
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
  const currentNotes = {
    "external-create":"운영 Backend의 비공개 관리자 시험으로 생성·요청 중복 방지·gVisor Pod Ready·삭제·예약 반납 확인 · 참가자 로그인과 게이트웨이 게임 실행은 별도",
    "internal-create":"외부용 검증 계정으로 내부 트랙 로그인 불가 · 내부 관리자 접근으로 생성 요청을 실행한 근거 필요",
    "candidate-query":"실제 Backend 생성 요청에 따른 Broker 예약과 Runtime 배치, 종료 후 동일 예약 RELEASED 확인",
    "scheduler-store":"같은 시험 ID의 PROVISIONING·RUNNING·STOPPING·CLEANED 조회와 종료 후 저장된 상태 확인 · 재시작·HA 승격은 별도",
    "broker-store":"실습 인스턴스에 귀속된 실제 예약 행과 자원량, 삭제 완료 후 RELEASED 확인 · 다른 예약 변경 없음",
    "runtime-create":"현재 운영 Scheduler → Runtime → 등록된 실제 K3s 노드의 gVisor Pod 생성·Ready·삭제 확인",
    "runtime-k3s":"Broker 인증서와 Node UID를 대조한 실제 노드에서 실습 Pod UID·승인 digest·gVisor·Ready·namespace 제거 확인",
    "external-inventory":"실제 외부 Backend 관리자 리소스 API에서 운영 Broker 목록 조회 확인 · 참가자 인스턴스 변경 없음",
    "scoring-server":"배포된 Backend 수집기로 KoTH 점수 서버 6곳 HTTPS 조회·응답 검증·DB 적용·같은 period 중복 방지 확인 · 검증 쓰기는 전부 되돌림",
    "external-score-poll":"운영 수집기와 KoTH 서버 6곳의 실제 왕복, 실제 점수가 있는 구간의 적용과 중복 차단 확인 · 공개 예정 상태와 참가자 점수 유지",
    "external-token":"배포된 게임의 실제 로그인 처리기에서 Backend 토큰 검증 호출과 유효하지 않은 토큰 거절 확인 · 참가자의 정상 로그인 성공은 별도",
    "internal-inventory":"내부 트랙 관리자 리소스 조회 실행 근거 필요 · 외부 트랙 조회 성공을 공유하지 않음",
    "internal-score-poll":"내부 트랙의 실제 채점 설정·수집 실행 근거 필요 · 외부 Backend 수집 성공을 공유하지 않음",
    "internal-token":"내부 트랙의 실제 게임 → Backend 토큰 검증 실행 근거 필요 · 외부 트랙 토큰을 재사용하지 않음"
  };
  for (const link of links) if (currentNotes[link.id]) {
    link.note=currentNotes[link.id];
    if (link.id==="internal-create") link.stage="CODE";
  }
  const findings = [
    {id:"internal-execution",title:"내부 트랙 업무 연결 실행 대기",owner:"내부 트랙 · SLA",detail:"외부용 검증 계정은 내부 로그인이 거부돼 기존 내부 관리 접근이 필요함 · 생성·목록·채점·토큰 결과를 외부 검증으로 대체하지 않음",link:"internal-create",severity:"PENDING"}
  ];
  const gates = [
    {name:"Worker 단일 실행", owner:"스케줄러 · DevSecOps", note:"팀·행 잠금과 프로세스 leader lock은 다름 · Active/Standby 승격 계약과 fencing 미검증"},
    {name:"백업 · 복원 · rollback", owner:"DevSecOps · 백엔드", note:"기존 저장소 6개 복원·원래 볼륨 복귀와 별도로 Runtime Store 격리 복원·작업 6건 재조회 확인 · 운영 전환·VM HA는 미검증"},
    {name:"문제 inventory · readiness", owner:"Runtime · 스케줄러 · SLA", note:"Scheduler 전체 목록과 등록된 실행 환경의 상태 조회 · 트랙 매핑·대회 문제 기능 점검은 연결 대기"},
    {name:"운영 복구 연결", owner:"SLA · 각 수신팀", note:"기본 앱 7개와 선택한 로컬 Runtime의 정지 컨테이너 기동 · 운영 호스트 접속·자격 증명·수신 API·상태 보존 계약은 연결 대기"}
  ];
  const catalog = {schema_version:1, verified_at:"2026-10-13T14:34:00+09:00", scope:"기존 고정 SHA 소스 검토와 날짜가 있는 운영 실행 기록을 구분 · 실행 검증은 해당 시험의 범위와 정리 결과이며 현재 연결 상태나 전체 경기 완주를 뜻하지 않음", sources, nodes, links, findings, gates};
  const externalProbeIds = {
    "external-frontend":"public-site", "external-backend":"public-backend",
    "external-backend-database":"public-backend-database", "external-backend-cache":"public-backend-cache"
  };
  function probeId(id, readings) {
    if (readings.has(id)) return id;
    const alias = externalProbeIds[id];
    return alias && readings.get(alias)?.track === "EXTERNAL" ? alias : id;
  }
  const groupSelectors = {
    scheduler:a=>a.track === "CONTROL" && ["scheduler","scheduler-api"].includes(a.asset_id),
    broker:a=>a.track === "CONTROL" && ["broker","broker-console","broker-fleet"].includes(a.asset_id),
    runtime:a=>a.track === "RUNTIME" && ["runtime","runtime-api"].includes(a.asset_id),
    "linux-pool":a=>a.track === "RUNTIME" && a.role === "K3S" && a.kind === "cluster_heartbeat",
    koth:a=>a.track === "KOTH" && a.role === "KOTH" && ["service_heartbeat","challenge_heartbeat"].includes(a.kind),
    windows:a=>a.track === "RUNTIME" && a.role === "WINDOWS" && a.kind === "service_heartbeat"
  };
  const rank = {CRITICAL:0,WARNING:1,UNKNOWN:2,PENDING:3,NORMAL:4};
  function groupedNode(definition, assets) {
    const select = groupSelectors[definition.id];
    if (!select) return definition;
    const members = assets.filter(select).sort((a,b)=>a.asset_id.localeCompare(b.asset_id));
    // Keep explicit local probes and their typed connection evidence intact.
    if ((!members.length && definition.reading) || (members.length === 1 && members[0].asset_id === definition.id)) return definition;
    if (!members.length && !["linux-pool","koth","windows"].includes(definition.id)) return definition;
    const worst = [...members].sort((a,b)=>(rank[a.risk?.level] ?? 2)-(rank[b.risk?.level] ?? 2))[0];
    const counts = {NORMAL:0,WARNING:0,CRITICAL:0,UNKNOWN:0,PENDING:0};
    for (const member of members) counts[Object.hasOwn(counts,member.risk?.level) ? member.risk.level : "UNKNOWN"]++;
    const timestamps = members.map(a=>a.checked_at).filter(at=>Number.isFinite(Date.parse(at))).sort((a,b)=>Date.parse(a)-Date.parse(b));
    const summary = definition.id === "koth" ? `서버 ${members.filter(a=>a.asset_id.startsWith("koth-host-")).length} · 준비 ${members.filter(a=>a.asset_id.startsWith("koth-app-")).length}`
      : ["linux-pool","windows"].includes(definition.id) ? `${members.length}대` : `${members.length}개 감시`;
    const reading = worst ? {asset_id:definition.id,name:definition.name,role:definition.role,track:definition.track,
      kind:"monitor_group",evidence_type:"GROUP_SUMMARY",status:worst.status,risk:{level:worst.risk?.level || "UNKNOWN",signals:[]},
      reason_code:"MONITOR_GROUP",checked_at:timestamps.length === members.length ? timestamps[0] : null,
      components:{member_count:String(members.length)},enabled:true} : null;
    return {...definition,grouped:true,members,counts,summary,reading,
      note:"현재 감시 목록의 실제 관측을 집계 · 상자를 눌러 개별 자산 확인 · 자산 간 요청과 채점 성공은 각 연결 근거에서 확인"};
  }
  function resolveNodes(assets) {
    const readings = new Map(assets.map(asset => [asset.asset_id, asset]));
    const result = nodes.map(definition => {
      const id = probeId(definition.probe_id || definition.id, readings);
      return groupedNode({...definition, probe_id:(definition.probe_id || readings.has(id)) ? id : undefined, reading:readings.get(id) || null}, assets);
    });
    const mapped = new Set(result.map(definition => definition.probe_id || definition.id));
    for (const asset of assets) if (!mapped.has(asset.asset_id)) result.push({id:asset.asset_id, name:asset.name, role:asset.role, track:asset.track, probe_id:asset.asset_id, reading:asset, owner:"감시 대상 설정", note:"프로필에 추가한 실제 감시 대상 · 운영 트랙과 임의로 합치지 않음"});
    return result;
  }
  function monitoringLinks(assets) {
    const definitions = resolveNodes(assets);
    return ["linux-pool","koth","windows"].map(id=>{
      const node = definitions.find(n=>n.id === id), members = node.members || (node.reading ? [node.reading] : []);
      const level = node.reading?.risk?.level || "PENDING";
      const state = level === "NORMAL" ? "VERIFIED" : level === "CRITICAL" ? "FAILED" : level === "PENDING" ? "PENDING" : "UNKNOWN";
      return {id:`monitor-${id}`,kind:"monitoring",from:id,to:"observer",label:`${node.name} 감시`,state,
        verified:state === "VERIFIED",checked_at:node.reading?.checked_at || null,members,
        note:"각 감시 자산의 최신 보고 상태 · 실제 자산 추가·삭제를 자동 반영 · 서비스 간 RPC나 문제 실행 성공의 근거로 사용하지 않음"};
    });
  }
  function resolveProbeLink(link, assets) {
    if (link.stage !== "PROBE") return {...link, state:link.stage, verified:false};
    const readings = new Map(assets.map(asset => [asset.asset_id, asset]));
    const parentId = probeId(link.parent_probe, readings);
    const child = readings.get(probeId(link.evidence_asset, readings));
    const parent = readings.get(parentId);
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
      && child.evidence_type === "HEALTHZ_COMPONENT" && child.parent_target_id === parentId
      && child.checked_at && child.checked_at === parent.checked_at;
    const state = valid && child.status === "HEALTHY" ? "VERIFIED" : valid && child.status === "DOWN" ? "FAILED"
      : child && ["UNCONFIGURED", "PENDING"].includes(child.status) ? "PENDING" : "UNKNOWN";
    return {...link, state, verified:state === "VERIFIED", checked_at:valid ? child.checked_at : null};
  }
  function executionFor(link) {
    const evidence=root.SentinelExecutionEvidence;
    if (evidence?.schema_version!==1 || !Array.isArray(evidence.runs)) return null;
    const matches=evidence.runs.filter(run=>run && run.passed===true && run.cleanup_verified===true && run.simulated===false
      && run.verification_mode==="one_time_production" && /^[a-f0-9]{64}$/.test(run.proof_sha256 || "")
      && /^\d{4}-\d{2}-\d{2}T/.test(run.checked_at || "") && Number.isFinite(Date.parse(run.checked_at)) && Date.parse(run.checked_at)<=Date.now()+300000
      && typeof run.summary==="string" && run.summary.length>0 && typeof run.limits==="string" && run.limits.length>0
      && Array.isArray(run.connections) && run.connections.some(item=>item && item.id===link.id && item.from===link.from && item.to===link.to));
    return matches.length===1 ? matches[0] : null;
  }
  function resolveLink(link, assets) {
    const current=resolveProbeLink(link,assets),execution=executionFor(link);
    // A dated execution record never asserts current reachability and never hides a measured failure.
    if (!execution || ["VERIFIED","FAILED"].includes(current.state)) return current;
    return {...current,state:"EXECUTED",verified:false,executed:true,checked_at:execution.checked_at,execution};
  }
  function sourceLink(key, path) {
    const source = sources[key];
    if (!source) return null;
    return {name:source.name, version:source.version || source.sha.slice(0,7), url:source.url || `https://github.com/${source.repo}/blob/${source.sha}/${path || "README.md"}`};
  }
  const api = {catalog, resolveNodes, resolveLink, sourceLink, monitoringLinks, executionFor};
  root.SentinelArchitecture = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
