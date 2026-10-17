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
