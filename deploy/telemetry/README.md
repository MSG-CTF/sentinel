# 서버 자원과 SSH 운영

report_service.py는 기존 봇 서비스의 실행 상태와 서버 자원을 60초마다 보고한다
CPU는 /proc/stat의 두 측정값을 1초 간격으로 비교하며 load average를 사용률로 표시하지 않는다
운영체제·아키텍처·CPU 모델·논리 코어 수·가동 시간·메모리와 루트 디스크의 사용량·총량을 함께 보낸다
기존 보고기 형식도 수용하며 빠진 필드는 미수집으로 표시한다

## SSH 연결

웹 상세의 SSH 콘솔은 status·resources·start 세 작업을 제공한다
대상은 서버에 등록한 자산 ID로 선택하며 브라우저가 호스트·계정·서비스·명령을 지정하지 않는다
개인키는 GitHub 환경 Secret에서 런타임 환경으로만 주입하고 SSH 서버의 호스트 키 SHA-256을 검증한다
개인용 SSH 키는 배포하지 않으며 별도로 생성한 sentinel-ops 계정과 전용 키를 사용한다

호스트 설정:

- report_service.py → /opt/sentinel-telemetry/report_service.py · root 소유 0755
- asset_command.py → /usr/local/libexec/sentinel-asset-command · root 소유 0755
- ssh_entry.sh → /usr/local/libexec/sentinel-ssh-entry · root 소유 0755
- sentinel-ops 계정의 authorized_keys에는 restrict, from="SLA 출발지 IP", command="/usr/local/libexec/sentinel-ssh-entry"와 전용 공개키를 등록
- sudoers에는 sentinel-asset-command status, resources, start만 허용
- 다른 자산에 연결할 때 해당 호스트의 고정 서비스와 키·지문을 별도로 검증

스케줄러와 런타임 관리 서버도 같은 보고기와 제한된 SSH 작업을 사용한다
호스트에 설치하는 asset_command.py의 SERVICE는 해당 서버에서 확인한 systemd 서비스 하나로 고정한다
각 서버의 전용 키는 SENTINEL_SSH_SCHEDULER_B64 또는 SENTINEL_SSH_RUNTIME_B64로 분리한다
service_heartbeat는 관리 프로세스와 호스트 자원을 확인하며 문제 컨테이너의 실행 가능 여부를 보장하지 않는다
서비스 HTTP 점검과 스케줄러 전체 목록 조회는 별도의 HTTPS 대상에 연결한다
런타임 클러스터가 등록되지 않았으면 인스턴스 실행 상태를 정상으로 추정하지 않는다

백엔드 VM은 backend_command.py로 Docker 컨테이너 msg-dev-backend-1의 실제 상태를 확인한다
Docker 데몬이 실행 중이어도 백엔드 컨테이너가 정지했으면 정상으로 보고하지 않는다
백엔드 바인딩은 docker:msg-dev-backend-1이며 전용 키는 SENTINEL_SSH_BACKEND_B64를 사용한다
report_backend.py는 60초 주기의 sentinel-backend-report.timer에서 실행하며 backend-host로 자원을 보고한다
보고 서비스는 root로 실행하는 고정된 Docker 조회 도우미이며 Docker 소켓 권한을 별도 사용자에게 부여하지 않는다
DB·Redis는 공개 HTTPS /healthz가 반환하는 실제 연결 점검으로 확인하고 호스트 자원과 분리해 표시한다
start 작업은 created·exited 상태의 백엔드 컨테이너 하나만 기동하며 실행 중·일시 정지·재기동 중인 컨테이너에는 실행하지 않는다
백엔드 컨테이너의 healthcheck 실패를 Docker 재시작으로 자동 처리하지 않는다

이 콘솔은 임의 셸 명령, PTY, 포트 전달, 파일 업로드를 제공하지 않는다
상태 조회는 선택한 서비스의 공개 상태 필드만 반환하고 환경 변수·봇 토큰·서비스 로그 원문은 읽지 않는다
자원 조회는 보고기와 같은 자원 수치를 반환한다

## 수동 복구

관리자 세션과 CSRF 토큰이 필요하며 최신 heartbeat에서 inactive 또는 failed가 확인돼야 한다
사용자가 자산 상세의 서비스 복구 → 기동 실행을 누르면 등록된 서비스를 systemctl start로 기동한다
서버가 실행 직전에 상태를 다시 확인하며 active·activating·deactivating이면 실행하지 않는다
동시 작업은 1개이고 기동 간격은 5분 이상이며 작업 ID를 실행 전에 기록한다
시간 초과·연결 끊김·프로세스 재시작 후에는 같은 요청을 자동으로 재실행하지 않는다
기동 요청 성공을 기능 복구로 표시하지 않으며 이후 heartbeat로 정상 여부를 확인한다
최근 200개 작업을 유지하며 디스코드 봇 gateway·티켓 처리 정상 여부는 별도 검증이다

## 디스코드 경고

discord_alerts.enabled=true, state_file=data/discord-alerts.json, dashboard_url=관리자 화면 HTTPS 주소를 설정한다
서버의 risk 판정으로 주의·위험·단계 완화·정상 복귀를 보낸다
정상 시작에는 알림을 보내지 않고 미확인·수집 지연은 복구로 처리하지 않는다
정상 복귀는 서로 다른 최신 정상 측정 2회로 확인하며 열린 경고는 해제 여유값도 적용한다
메시지 대기열과 발송 상태를 데이터 디렉터리에 저장해 배포 후 같은 상태를 다시 알리지 않는다
전송 실패는 대기열에 남겨 재시도하고 429의 retry_after를 따른다
전송 직후 응답이나 저장이 유실되면 중복 전달될 수 있으므로 메시지 footer에 고유 이벤트 ID를 포함한다
기록 저장 실패 시 알림 전송과 수동 복구는 각각 중단되고 UI에 상태가 표시된다
배포 후보 컨테이너는 알림·SSH 운영을 실행하지 않는다

운영·기록의 시험 알림은 실제 장애와 구분되는 메시지를 보내며 1분당 1회로 제한한다
Discord의 [웹후크 실행 계약](https://docs.discord.com/developers/resources/webhook#execute-webhook)에 따라 wait=true와 비어 있는 allowed_mentions를 사용한다
SSH 연결은 [SSH.NET](https://sshnet.github.io/SSH.NET/)의 호스트 키 검증과 비동기 명령 실행을 사용한다

## 백엔드 과부하와 알림 경로

SLA 서버가 백엔드를 외부에서 점검하고 Discord 웹후크에 직접 전송한다
백엔드 애플리케이션과 디스코드 봇 프로세스는 알림 발송 경로에 포함되지 않는다
HTTP 지연은 risk_policy 기준으로 주의·위험을 판정하고 연결 실패·시간 초과·5xx는 위험으로 처리한다
429는 요청 제한이 확인된 주의 상태이며 서버 CPU 과부하로 단정하지 않는다
503에서 HTML·잘못된 JSON을 받아도 서비스 이용 불가를 알리되 DB·Redis 장애를 추정하지 않는다
알림에는 HTTP 상태와 감지 근거를 포함하고 응답 본문이나 인증 정보를 넣지 않는다

외부 응답 점검만으로 CPU·메모리·DB 풀 포화나 요청 실패율을 측정할 수는 없다
백엔드 관리 접속 후 호스트 보고기와 정상 healthz 경로, 애플리케이션 지표를 연결해야 한다
현재 인증 API 진입 점검은 DB 작업·실제 사용자 요청 성공을 보장하지 않는다
SLA 자체가 정지하면 내부 알림도 멈추므로 별도 호스트나 외부 서비스의 감시가 추가로 필요하다
Discord 전체 장애에는 저장 후 재시도만 가능하며 별도 수신 채널은 아직 연결하지 않았다

## 실행 클러스터 노드 보고

report_clusters.py는 런타임 서비스 계정으로 기존 registry의 명시된 클러스터만 읽는다
kubectl get nodes와 GET metrics API만 호출하며 인스턴스 생성·삭제·재시작이나 노드 설정 변경을 실행하지 않는다
registry와 kubeconfig는 기존 서버에 유지하고 SLA로 전달하지 않는다
sentinel-cluster-report.service와 timer는 60초마다 실행하며 각 클러스터의 전용 토큰으로 /ingest/cluster/{target_id}에 보고한다

보고 설정 파일은 root 전용 /etc/sentinel-telemetry/clusters.json이며 systemd LoadCredential로 읽는다
bindings 항목마다 endpoint, cluster_target_id, token을 등록하고 플랫폼 대상에도 같은 cluster_target_id를 expected_fields로 고정한다
클러스터마다 별도 token을 사용하며 대상·클러스터 식별자가 다르거나 재전송·오래된 보고이면 수신을 거절한다

단일 노드 실행 계약에 따라 노드 수·Ready·스케줄 가능 여부와 MemoryPressure·DiskPressure·PIDPressure를 별도로 판정한다
필수 condition의 Unknown·누락, 조회 실패·클러스터 등록 해제는 미확인으로 표시하고 정상 복구에 포함하지 않는다
수신이 150초 넘게 끊기면 오래된 측정값과 위험 수치를 현재 값으로 표시하지 않는다

CPU·메모리는 같은 노드 식별자와 60초 이내의 Kubernetes metrics 응답이 있을 때만 보고한다
메모리는 working set이며 Linux MemAvailable 기반 호스트 수치와 수집 기준이 다르다
디스크 사용률은 수집하지 않으며 지표가 없거나 오래됐을 때 0으로 채우지 않는다
노드 정상은 개별 문제 컨테이너 준비나 문제 기능 성공을 뜻하지 않는다
개별 실행 상태는 instance_monitor.runtime_targets의 명시된 HTTPS 런타임 주소와 인스턴스 식별자 계약으로 따로 조회한다

[Kubernetes resource metrics 계약](https://kubernetes.io/docs/tasks/debug/debug-cluster/resource-metrics-pipeline/)과 [NodeMetrics 명세](https://kubernetes.io/docs/reference/external-api/metrics.v1/)를 기준으로 수집한다
## 관리자 시험 문제 기능 점검

`challenge_probes.py`는 직접 소유한 일회성 관리자 배포 시험에 쓰는 점검기다
운영 인스턴스 목록의 준비 상태와 별도로 애플리케이션의 정상 입력·출력을 검증한다
현재 `instance_monitor.functional_checks`는 로컬 시험 서비스 전용이며 이 모듈을 추가해도 운영 상시 감시나 Discord 알림은 활성화되지 않는다

| 점검 프로필 | 확인하는 기능 |
| --- | --- |
| web-grade-tampering | healthz, DB를 읽는 공지 조회, 없는 공지의 404 응답 |
| web-notebook | 로그인 폼, 임의의 일반 계정에 대한 실제 DB 로그인 조회와 401, 미인증 노트 접근의 403 |
| pwn-random6 | 매번 새 숫자에 7을 더하는 정상 VM 명령의 EMIT 결과와 EXIT |
| pwn-note | 짧은 이름의 정확한 왕복 응답, 버퍼 크기 이내 메시지 저장, 정상 종료 |
| pwn-ghost-file | 새 메모 1개의 전체 내용 기록, 유효 범위의 수정·읽기·삭제 |
| pwn-price-tag | 유효한 상품 번호의 가격 변경과 관리자 접근 거절 |

호출자는 다음 조건을 모두 확인한 후 `check(profile, service_url, expected_host)`를 호출한다

1. 시험 요청을 보낸 관리자와 해당 시험의 ID·문제·릴리스·팀을 확인한다
2. 인증된 Scheduler와 Runtime에서 동일한 instance_id·target_id·runtime_workload_id를 확인한다
3. 60초 이내의 READY 관측, 실제 Pod Ready, 릴리스의 고정 digest와 실행 이미지 일치 여부를 확인한다
4. 등록된 클러스터의 공개 IPv4와 실제 Service의 NodePort를 대조한다
5. 시험이 끝나거나 점검에 실패하면 해당 시험만 종료하고 Namespace 제거와 Broker 예약 반납을 확인한다

점검기는 별도의 HTTP·TCP 연결을 사용하며 제어 서비스의 인증·쿠키를 전송하지 않는다
HTTP 요청과 경로, TCP 명령은 프로필에 고정돼 있으며 리다이렉트·호스트 불일치·사설 주소·멀티캐스트·NodePort 범위 밖 주소를 거부한다
연결과 개별 읽기에는 최대 4초 제한을 적용하고 전체 응답은 32 KiB 이내로 제한한다
결과에는 원문 응답·접속 주소·토큰·플래그를 포함하지 않으며 형식은 `challenge-functional-observation.schema.json`에 고정한다

검증 입력은 [고정된 문제 소스](https://github.com/MSG-CTF/2026_MSG_CTF/tree/1dea73b652f9eeb98204c1fda7f66b9afb89505d)에 있는 정상 기능에서 가져왔다
공식 풀이·플래그 획득·채점·부하 시험을 검증한 결과로 사용하지 않는다
정식 상시 감시로 전환하려면 승인된 문제 ID·릴리스·점검 규약 연결과 결과 수집·알림 정책이 추가로 필요하다
