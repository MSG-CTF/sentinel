# 실제 서버 기준 SLA 로컬 환경

Django mock은 실행하지 않는다
실제 Backend·Frontend·Scheduler·Broker를 실행하고, 각 상태 점검의 확인 범위를 분리해 보여준다

2026년 10월 13일 재점검 기준 Runtime·Runtime Store·Linux / K3s까지 연결했다
직접 점검 12개와 Backend 구성 요소 4개가 정상이며 미연결 역할은 9개다
자동 기동 대상은 기본 앱 7개와 Runtime이며 아래의 실행 자산 연결 절차로 준비한다
데이터 복원 결과를 실제 서비스에 잠시 연결했다가 원래 볼륨으로 돌아오는 절차는 별도 전환 명령을 사용한다
KoTH 시간 계약과 CI·Registry 미연결 사유, 앱 8개 복구와 Runtime Store 복원 근거는 [10월 13일 추가 자산 점검](sla-other-assets-review-20261013.md)에 기록했다

## 데이터 백업과 격리 복원

PostgreSQL 4개와 Redis 2개의 실제 데이터를 백업하고 새 Docker 프로젝트의 빈 볼륨에 복원한다
복원 환경은 외부 통신이 없는 내부 네트워크를 쓰며 호스트 포트를 공개하지 않는다
원본 서비스의 볼륨과 컨테이너는 교체하지 않는다

Sentinel 저장소 루트에서 아래 명령을 실행한다

    python local/data_recovery.py drill

백업만 만들거나 특정 백업을 다시 검증할 수 있다
두 번째 명령의 ID는 백업 명령이 출력한 값을 사용한다

    python local/data_recovery.py backup
    python local/data_recovery.py restore --backup-id BACKUP_ID
    python -m unittest discover -s local -p data_recovery_test.py -v

| 대상 | 백업 | 복원 완료 조건 |
|---|---|---|
| 내부·외부 PostgreSQL | 같은 읽기 스냅샷에서 논리 백업과 행 내용 해시 수집 | 테이블별 행 수·내용 해시·시퀀스 일치와 Django 모델 조회 |
| Scheduler DB | PostgreSQL 논리 백업 | 테이블·시퀀스 일치와 실제 Scheduler 인증 목록 조회 |
| Broker DB | PostgreSQL 논리 백업 | 테이블·시퀀스 일치와 실제 Broker 인증 inventory 조회 |
| 내부·외부 Redis | 실행 중인 서버에서 RDB 전체 스냅샷 수신 | RDB 체크섬, 적재·만료 키 수, 별도 시험 값과 TTL, AOF 재기동 확인 |

PostgreSQL 백업은 단일 DB 안에서 일관된 스냅샷을 사용한다
시퀀스 값이 백업 중 바뀌면 검증 가능한 백업으로 완료 처리하지 않는다
여섯 저장소를 동시에 정지하지 않으므로 저장소 간 원자적 복구 시점을 보장하지 않는다
백업 이후 만료된 Redis 키는 다시 살리지 않고 만료 수로 기록한다
Redis 시험 값은 복원한 격리 환경의 DB 1과 DB 7에만 만들고 다시 백업·복원해 검증한다
이 시험 값은 원본의 업무 데이터를 뜻하지 않는다

백업 파일과 비공개 manifest는 local/.backups 아래에 저장한다
실행 설정과 시험용 파일은 local/.restore-labs, 결과 리포트는 local/artifacts에 남긴다
백업·인증정보·실행 설정은 Git, Docker 빌드 컨텍스트, HTTP 공개 영역에서 제외한다
대시보드에는 시간·행 수·검증 범위 등 허용한 결과만 게시한다
검증 종료 시 해당 실행 ID의 컨테이너·볼륨·네트워크를 정리하고 원본 컨테이너 ID와 시작 시각을 비교한다
백업 보존 기한이나 주기 실행은 아직 설정하지 않았다

복원은 로컬 Docker Desktop의 고정 프로젝트 설정과 이미지가 남아 있는 경우를 대상으로 한다
원본 DB 컨테이너가 정지해 있어도 저장된 백업으로 격리 복원할 수 있다
원본 볼륨에 덮어쓰기, 서비스 연결 전환, WAL 기반 시점 복원, 별도 DB 역할·권한 이관은 구현 범위 밖이다
기존 앱 7개의 정지 컨테이너 자동 기동과 데이터 복원 검증은 별도 경로다
데이터 장애를 감지했다고 이 도구를 자동 실행하지 않는다

논리 백업 형식과 RDB 전송은 [PostgreSQL 문서](https://www.postgresql.org/docs/17/app-pgdump.html)와 [Redis CLI 문서](https://redis.io/docs/latest/develop/tools/cli/#remote-backups-of-rdb-files)를 기준으로 구현했다

## 다른 자산의 로컬 점검

25개 구성 역할을 상태 목록에 모두 등록했다
Backend 2개와 그 응답으로 확인하는 DB·Redis 4개에 Edge·Frontend 2개·Scheduler·Scheduler DB·Broker·Broker DB를 추가했다
실행 자산 연결 전에는 직접 점검 9개, Backend를 통한 구성 요소 점검 4개, 미연결 역할 12개다
Runtime·Runtime Store·Linux / K3s를 연결한 현재 구성은 직접 점검 12개, 구성 요소 점검 4개, 미연결 역할 9개다
서비스 API의 정상 응답은 문제 생성·참가자 기능 전체나 운영 VM 가용성을 보장하지 않는다

| 대상 | 접속과 점검 | 현재 확인 범위 |
|---|---|---|
| 로컬 Edge | 127.0.0.1의 58186 내부·58187 외부 포트 | Nginx 응답, 실제 프론트 정적 파일과 API 전달 |
| 내부·외부 Frontend | 58184·58185 포트 | 고정 revision의 React 빌드, 각 Backend로 API 전달 |
| Scheduler | 58182의 인증된 /api/instances/recent | 실제 DB에서 최근 20건까지 조회 |
| Scheduler DB | /actuator/health/db | 전용 PostgreSQL 연결 |
| Broker | 58183의 인증된 /v1/inventory/resource-targets | 실제 inventory 첫 페이지와 total 확인 |
| Broker DB | /health/ready | 전용 PostgreSQL SELECT 1 |

Broker 자산 목록이 비어 있어도 조회 API 자체는 정상일 수 있다
이 상태에서 클라우드 VM이나 문제 인스턴스를 정상으로 만들지 않는다
Scheduler recent는 전체 목록 API가 아니므로 전체 문제 감시로 계산하지 않는다
Scheduler의 unused Redis 자동 상태 점검은 끄며 실제 PostgreSQL 점검은 유지한다

Runtime은 빈 cluster registry로 시작할 수 없다
실제 K3s 대상·kubeconfig·인스턴스 매핑이 필요하며 실행 자산 연결 절차를 마치면 Runtime Store와 Linux / K3s도 함께 점검한다
2026_MSG_CTF에는 실제 KoTH 서비스 코드가 있고 Backend에는 점수 수집 명령이 있다
현재 로컬 구성에는 KoTH 서비스가 없어 채점 URL·토큰·문제 ID·period 연결이 필요하다
Windows는 실제 문제 VM과 관리 접속이 필요하며 사용자 PC의 Windows를 대체 대상으로 삼지 않는다
Cloud API는 계정·리전·VM 매핑, 문제 발행 자산은 revision·workflow run·image digest·artifact ID가 필요하다
Challenge Registry의 새 bundle 수집 경로는 현재 로컬 Backend에 없다

prepare_assets.py는 확인한 Git object를 .sources에 내보내며 원래 체크아웃과 브랜치를 바꾸지 않는다
Scheduler 5c695142, Broker staging 99ff5b88, Frontend c0a85359를 사용한다
Frontend 사본은 로컬 fix/admin-operation-reliability 브랜치의 해당 커밋이다
Start-Monitor는 compose.yaml과 compose.assets.yaml을 함께 사용한다
추가 서비스의 인증정보는 .assets에서 만들고 역할별 env 파일로 나눈다
Sentinel에는 Scheduler 조회 토큰과 Broker inventory 토큰만 전달하며 Broker 관리자 인증정보는 전달하지 않는다
Scheduler의 현재 토큰은 서비스 공유 토큰이므로 조회 전용 권한 토큰은 아니다
Sentinel은 GET만 사용하며 Scheduler 쓰기 API를 호출하지 않는다
.sources와 .assets는 Git과 Sentinel 이미지 빌드에서 제외한다

새 서비스의 포트는 127.0.0.1에만 게시한다
Docker Desktop의 내부 전용 네트워크에서는 호스트 포트가 열리지 않아 local_access 브리지를 함께 사용한다
DB에는 호스트 포트를 게시하지 않으며 서비스별 내부 데이터 네트워크와 볼륨을 분리한다

local-recovery는 기본 앱 7개와 추가 연결한 Runtime에 정지 컨테이너 기동을 연결한다
기본 DB·Redis 6개와 Runtime Store는 점검만 수행하며 자동 기동·데이터 복원을 실행하지 않는다

    python local/verify_assets.py --faults
    node --test local/asset-coverage.test.cjs

첫 명령은 Sentinel 저장소 루트에서 local-recovery 프로필을 실행한 상태로 사용한다
Scheduler DB·Broker DB를 차례로 중지해 3회 점검 간격이 지나도 앱 복구 요청이 생기지 않는지 확인한다
데이터 서비스 시험의 재기동은 검증 스크립트가 수행한다
실패해도 finally에서 해당 컨테이너를 기동하며 원래 Backend·DB·Redis 6개의 ID와 시작 시각을 비교한다
결과는 artifacts/assets-verification-날짜.json에 저장한다

## 로컬 자동 복구

관측만 수행하는 local 프로필과 별도로 local-recovery 프로필을 제공한다
Python과 로컬 Docker Desktop이 필요하며 아래 명령이 Sentinel과 Windows 실행기를 함께 시작한다

    .\Start-Monitor.ps1 -Profile local-recovery

자동 실행 대상은 msg-sla-real-local 프로젝트의 고정된 앱 서비스 7개다
연속 정상 2회로 기준을 세운 뒤 연결 실패가 3회 누적되면 요청을 만들고, 실행기가 컨테이너의 프로젝트·서비스·전용 라벨과 정지 상태를 다시 확인한다
실행 작업은 정지 컨테이너 기동이며 실행 중인 컨테이너 재시작·재생성·볼륨 변경을 수행하지 않는다
DB·Redis 연결 실패, HTTP 오류, 인증 오류와 오래된 관측은 기동 근거로 사용하지 않는다
시간 초과는 실행기가 15초 안에 해당 컨테이너의 정지와 의존 서비스 정상 상태를 확인해 보낸 경우에만 장애 횟수에 포함한다
Docker 내부 이름 조회가 지연되는 정지 서비스도 이 조건으로 복구할 수 있으며 실행 중인 서비스의 시간 초과는 기동 근거가 되지 않는다
OOM으로 종료한 컨테이너도 자동 기동 대상에서 제외한다
실행기에서는 해당 서비스의 DB·Redis 또는 upstream 컨테이너가 실행 중이며 healthcheck가 있으면 정상인지도 확인한다
기동 응답 뒤 해당 자산과 확인 대상의 새 정상 관측 2회가 모이면 복구 완료로 기록한다

| 자산 | 고정 서비스 | 자동 완료 기준 |
|---|---|---|
| 내부·외부 Backend | backend-internal, backend-external | 해당 API·DB·Redis 정상 2회 |
| Scheduler | scheduler | 인증된 recent 조회와 scheduler-db 연결 정상 2회 |
| Broker | broker | 인증된 inventory 조회와 broker-db 연결 정상 2회 |
| 내부·외부 Frontend | frontend-internal, frontend-external | 정적 화면 식별 응답과 해당 Backend 정상 2회 |
| 진입 프록시 | edge | Nginx 상태 계약·두 Frontend·두 Backend 정상 2회 |

Frontend와 Edge의 자동 완료 기준에는 참가자 로그인이나 문제 풀이 전체가 포함되지 않는다
실제 두 트랙의 화면과 API 전달은 복구 검증 스크립트에서 별도로 실측한다
Frontend와 Edge는 연결된 Backend·Frontend의 최신 정상 관측이 있어야 기동 요청을 만들 수 있다
여러 장애가 함께 발생해도 요청은 전역에서 하나씩 처리하며 자산별 정상 기준과 재실행 대기 시간은 독립적으로 관리한다

장애당 한 번만 실행하며 정상 복귀와 직전 요청 시각 기준 300초 경과를 모두 확인한 뒤 다음 장애를 처리할 수 있다
요청 유효 시간과 기능 확인 제한 시간은 각각 120초다
실행 실패·결과 불명·기한 초과 뒤 같은 장애에 대한 반복 실행은 없다
요청 기록은 Sentinel 볼륨, 실행 전 기록은 Windows의 .recovery/executor.sqlite에 영속 저장한다
기존 schema_version 1 기록과 소비된 claim은 schema_version 2로 보존한다
감시기 재시작 시 미인수 요청을 취소하고 모든 자산의 정상 기준을 다시 확인한다
실행 도중 프로세스가 종료되면 결과 불명으로 보고하고 같은 요청을 다시 실행하지 않는다
기록 저장 실패 시 자동 실행을 중단하며, 상태 파일을 삭제하는 방식으로 제한을 해제하지 않는다

실행기는 127.0.0.1의 인증된 내부 API를 조회하고 로컬 Docker named pipe에 접속한다
실행기는 고정된 7개 자산의 기동 가능 여부만 인증된 inspections 경로에 보고하며 컨테이너 환경변수와 명령 출력은 전송하지 않는다
Sentinel 컨테이너에는 Docker socket을 연결하지 않는다
인증정보는 .recovery/secrets에서 만들며 Git·이미지 빌드·상태 응답에서 제외한다
현재 인증은 로컬 실행기 하나의 범위이며 운영 자산으로 확장하려면 실행기별 인증과 자산별 권한을 구현해야 한다
실행기는 Start-Monitor가 시작한 Windows 프로세스로 동작하며 Windows 서비스 등록은 하지 않는다
PC 재부팅 뒤에는 시작 명령을 다시 실행한다

관측 모드로 돌아가면 Windows 실행기를 먼저 중지한다

    .\Start-Monitor.ps1 -Profile local -SkipBuild

Stop-Monitor도 실행기를 먼저 중지한 뒤 해당 Compose 프로젝트만 중지한다
대시보드의 자산 상태에는 자동 복구 모드·실행기 연결·요청 결과가 표시된다
공개 복구 응답의 assets 배열이 현재 활성 자산 전체를 나타낸다
이전 클라이언트 호환용 최상위 asset_id와 armed는 내부 Backend에만 해당한다
전체 구조에서 자산을 선택하면 접속 수단·복구 담당·실행 조건·완료 기준을 확인할 수 있다
연결 검토의 자산별 접속·복구 계획은 25개 구성 역할과 Sentinel 자체를 포함한다
운영 주소·인증정보 참조·호스트 ID·서비스 매핑은 모두 미설정이며 운영 자동 실행은 비활성이다

접속 계획의 단일 원본은 ../src/Sentinel.Api/wwwroot/asset-access.json이다
고정 Linux 서비스는 호스트 실행기, 문제 워크로드는 Scheduler와 Runtime, Windows는 제한된 관리 연결, VM 전원은 클라우드 관리 API를 사용하는 설계다
Scheduler·Broker·Runtime 자체 장애는 독립된 호스트 관리 경로로 복구하도록 계획한다
운영 SSH·Windows·클라우드 실행 어댑터와 Scheduler 전용 복구 API는 이번 로컬 구현에 포함되지 않는다
기존 Scheduler reset은 인스턴스를 교체하므로 상태 보존 복구와 구분한다

복구 요청 계약은 recovery-contract.json, Scheduler·Runtime 연결 대기는 inventory-contracts.json에서 관리한다

    python -m unittest discover -s local -p recovery_agent_test.py -v
    python local/verify_recovery.py
    python local/verify_multi_recovery.py
    python local/verify_recovery_profiles.py

복구 검증 명령은 local-recovery 프로필에서만 실행한다
실제 내부 Backend와 내부 PostgreSQL을 순서대로 중지하며 실패 시에도 finally에서 원래 컨테이너를 기동한다
복구 보고서는 artifacts/recovery-verification-날짜.json으로 보존한다
verify_multi_recovery는 앱 7개를 하나씩 실제로 중지한 뒤 실행기의 자동 기동과 정상 확인을 기다린다
서비스별 컨테이너 ID 유지, 다른 컨테이너의 시작 시각 유지, 두 트랙의 화면과 API 전달을 확인한다
DB·Redis 6개의 시작 시각이 시험 전후 같은지도 비교하고 artifacts/multi-recovery-날짜.json에 결과를 남긴다
프로필 검증은 관측 모드 전환 시 실행기 중지, 자동 복구 모드 재진입 시 요청 기록 보존과 재실행 방지를 확인한다
일반 관측 검증인 verify_real.py와 verify_profiles.py는 기존 local·deployed 프로필을 사용한다

## 시작

Docker Desktop이 켜진 상태에서 이 README가 있는 local 폴더로 이동해 PowerShell로 실행한다
시작·중지·검증 명령은 Compose 프로젝트 이름을 msg-sla-real-local로 고정한다

    .\Start-Monitor.ps1 -Profile local

다른 폴더에서 실행할 때는 Start-Monitor.ps1의 전체 경로를 사용한다
대시보드는 http://127.0.0.1:58240 에서 확인한다
실제 백엔드는 내부 http://127.0.0.1:58180/healthz, 외부 http://127.0.0.1:58181/healthz

## 전체 구조

첫 화면은 참가자 접속부터 두 트랙, 공통 제어, 문제 실행, 채점, 문제 발행과 SLA 관측을 연결한 지도다
내부·외부는 Frontend·Backend·PostgreSQL·Redis를 분리하고 Scheduler와 Broker의 역할을 나눈다
생성 요청은 Scheduler에서 Runtime으로 보내며 Broker가 workload를 생성하는 것으로 그리지 않는다
KoTH·Windows 전용 VM은 일반 K3s 생성 경로와 구분한다
문제 발행은 info.yaml에서 CI를 거쳐 GHCR image와 Actions bundle로 갈라진다
CI의 백엔드 직접 POST로 그리지 않으며 bundle 수집 불일치는 빨간 점선으로 유지한다
실측 연결, 소스 확인, 설계와 계약 불일치를 서로 다른 선으로 표시한다
지도는 25개 구성 역할과 참가자·SLA 설명을 표시하며 이 개수를 정상 자산 수로 계산하지 않는다
자산과 연결선을 누르면 확인 범위·담당·소스 근거가 상세창에 나온다
보조 연결을 켜면 관리자 리소스 목록과 트랙별 팀 토큰 검증 경로를 표시한다
데스크톱은 너비에 맞추며 모바일은 읽을 수 있는 배율로 지도 안에서 이동한다
확대·축소는 노드·연결선·영역 경계를 하나의 좌표계로 함께 움직인다
상세창을 열고 닫거나 화면 너비가 자동으로 바뀌어도 보던 지도 위치를 유지한다
맞춤 버튼을 누르면 기본 배율과 처음 위치로 돌아간다
관측선은 SLA의 HTTP 수집 흐름이며 workload 생성이나 복구 요청이 아니다

## 자산 상태

자산 상태 탭은 대회 진입, 내부·외부 트랙, 공통 제어, 문제 실행, 채점과 문제 발행의 등록 대상을 표시한다
정상, 장애·저하, 확인 불가, 미연결을 따로 집계한다
데스크톱은 네 영역을 나란히 놓고, 모바일은 두 열로 표시한다
자산을 누르면 점검 근거와 해당 백엔드의 상태 변경 기록을 상세창에서 확인한다
상세창은 닫기 버튼, Escape 키, 바깥 클릭으로 닫을 수 있다
최근 상태 변경은 기본 세 건만 표시하고 전체 기록을 펼칠 수 있다
문제만 보기는 장애·저하·확인 불가 자산을 표시하며 미연결 자산을 정상으로 취급하지 않는다

연결 검토 탭에는 Edge·프론트·제어·문제 실행·이미지 발행 경로의 양쪽 소스 근거를 남겼다
영역별로 연결을 거르고 행을 누르면 담당, 계약, 고정 SHA를 확인한다
계약 차이와 운영 전 확인 항목은 이 탭의 접힌 목록에 둔다
실측, 소스 확인, 설계, 계약 불일치를 구분하며 실제 통신 경로를 자동 발견한 결과는 아니다
연결되지 않은 역할도 등록 대상 수에는 포함하며 정상 개수와 실측 개수에서 제외한다
[아키텍처 대조와 빠진 연결](architecture-audit.md)에 기준 SHA와 확인 범위를 기록했다

| 표시 | 의미 |
|---|---|
| 정상 | 현재 점검에서 응답 또는 연결을 확인 |
| 장애 | 서비스 응답 실패 또는 healthz의 구성 요소 연결 실패 |
| 연결 저하 | API는 HTTP 503으로 응답했지만 DB·Redis 중 하나 이상 연결 실패 |
| 확인 불가 | 응답 계약 불일치 또는 최신 점검 데이터 없음 |
| 미연결·점검 대기 | 점검 주소·인증 미연결 또는 첫 점검 대기 |

로컬 상태 목록은 25개 역할을 표시하고 직접 점검 9개와 healthz 구성 요소 4개를 구분한다
DB 상태는 Backend healthz, Scheduler Actuator와 Broker readiness의 실제 연결 점검으로 확인한다
DB·Redis 노드의 상태는 백엔드 연결 점검 결과이고 해당 VM의 전원 상태가 아니다
API 응답 시간을 DB·Redis의 개별 지연 시간으로 표시하지 않는다

자산 상태 탭의 수치는 자산 개수이며 누적 SLA나 운영 준비 달성률이 아니다
미연결·확인 불가 자산을 정상 개수에 더하지 않는다
원형 그래프와 상시 상세 패널은 첫 화면에서 제거했다
장애·저하 합계는 영향을 받은 자산 수이며 장애 사건의 개수와 다를 수 있다
브라우저와 Sentinel 사이 연결이 끊기면 이전의 초록색 상태를 유지하지 않는다

모델의 경계 조건은 아래 명령으로 확인한다

    node --test .\local\asset-model.test.cjs .\local\architecture-model.test.cjs .\local\topology.test.cjs

Sentinel 저장소 루트에서 실행하며 외부 라이브러리 설치 없이 Node 내장 테스트를 사용한다
브라우저 검증은 local 프로필이 정상인 상태에서 Sentinel 저장소 루트에서 실행한다

    npx --yes --package @playwright/cli playwright-cli -s=sentinel-local open http://127.0.0.1:58240/ --headed
    npx --yes --package @playwright/cli playwright-cli -s=sentinel-local --raw run-code --filename local/verify_browser.cjs

실제 상태 수치, 브라우저 오프라인, 재연결, 근거 상세, 지도 확대와 모바일 이동을 확인한다
브라우저 네트워크는 finally에서 온라인으로 돌리고 서버 상태는 바꾸지 않는다
캡처는 output/playwright에 남는다

2026-10-07 실제 내부 PostgreSQL 정지 시험에서 API 저하·DB 장애·Redis 정상과 외부 트랙 정상 표시를 확인했다
시험 종료 후 PostgreSQL을 다시 시작하고 백엔드 정상 응답까지 확인했다

## 실행 옵션

이미 빌드했다면 -SkipBuild를 붙인다
native .NET SDK는 필요하지 않으며 Docker 안에서 빌드한다
backend source는 sentinel 옆의 msg-backend 폴더에 있어야 한다

## 배포 서버로 변경

    .\Start-Monitor.ps1 -Profile deployed -SkipBuild

프로필을 바꾸면 Sentinel 컨테이너만 같은 주소로 재생성한다
이미 띄운 로컬 백엔드와 데이터 서버는 그대로 둔다
deployed 프로필은 최초 기동에서도 로컬 DB를 만들 필요가 없다

주소는 src/Sentinel.Api/profiles/local.json 또는 deployed.json에서 변경한다
변경 후 해당 프로필로 Start-Monitor.ps1을 다시 실행한다
대시보드 HTML·CSS·JS는 이 로컬 Compose에서 읽기 전용으로 연결해 새로고침만 하면 반영된다
감시 로직을 바꿨다면 -SkipBuild 없이 다시 실행해 빌드한다
실제 내부·외부 운영 도메인은 확인되지 않아 설정을 비워두었다
msgctf.kr을 임의로 내부 또는 외부 트랙으로 분류하지 않는다

## 실제 판정 기준

| 대상 | 점검 방식 | 정상 조건 |
|---|---|---|
| 백엔드 | backend_healthz | application/json, HTTP 200, status·database·cache 모두 ok |
| 백엔드 DB·Redis 장애 | backend_healthz | HTTP 503과 down 상태가 일치하면 장애로 기록 |
| 프론트 사이트 | http | HTTP 200과 설정한 사이트 문구 일치, DB 정상 여부를 뜻하지 않음 |
| 다른 서비스 | json_fields | 제공팀과 합의한 문자열 필드가 expected_fields와 정확히 일치 |
| 주소 미설정 | enabled false | 연결 대기, 실제 요청 없음 |

HTML 200, 로그인 리다이렉트, 인증 실패, 응답 필드 누락은 정상으로 처리하지 않는다
문제 flag나 외부 응답 본문, 인증 토큰은 상태 API와 장애 로그에 저장하지 않는다
연속 3회 실패 시 기록하고, 같은 장애는 반복 기록하지 않는다
2회 연속 정상 응답이면 복구를 기록한다
오래된 점검 결과는 STALE로 내려서 정상 상태로 보이지 않게 한다
응답 시간 초과는 TIMEOUT으로 표시하며, 응답이 없을 때 특정 DB·Redis 장애라고 단정하지 않는다
백엔드의 Redis 재시도로 장애 응답이 점검 제한 시간보다 늦어질 수 있다

## 데이터 분리

내부와 외부는 PostgreSQL 컨테이너·Redis 컨테이너·volume·계정·서명 키가 모두 다르다
각 데이터 서버는 트랙별 Docker network에만 연결되고 host 포트를 공개하지 않는다
SLA는 백엔드 응답으로 DB·Redis를 판단하며 데이터 서버 계정을 받지 않는다
Compose에 있는 키와 암호는 이 로컬 환경 전용이며 운영에 사용할 수 없다
Docker 컨테이너 분리를 VM 장애 영역 분리나 운영 고가용성 검증으로 보지 않는다
백엔드는 감시 network를 공유하며, 운영 방화벽과 문제 Runtime 격리는 별도 검증이 필요하다
Docker network는 10.246.240.0/24부터 243.0/24까지 사용한다
이미 쓰는 대역이면 compose.yaml의 ipam 설정을 먼저 변경한다

## 실제 장애 시험

    python .\verify_real.py
    python .\verify_real.py --faults

Python 3.12 이상이면 표준 라이브러리만으로 실행된다
--faults는 이 프로젝트의 내부 PostgreSQL과 외부 Redis만 잠시 정지한다
반대 트랙은 정상인지 확인하고, finally에서 해당 서비스를 다시 시작한다
이 관측 프로필의 장애 시험은 스크립트가 서비스를 다시 시작한다
Sentinel 자동 복구는 별도 local-recovery 프로필과 verify_recovery.py로 검증한다
실제 백엔드의 503 응답과 Sentinel의 제한 시간 판정을 함께 기록한다
배포 서버에는 장애를 넣거나 재시작·삭제 요청을 보내지 않는다
검증 결과는 local/artifacts에 실행 시각을 포함한 JSON으로 남기며 과거 리포트를 덮어쓰지 않는다
장애 시험은 로컬 Docker Desktop Linux 엔진에서만 허용한다

프로필 전환 보존 검증은 아래 명령으로 실행한다

    python .\verify_profiles.py

local에서 deployed로 바꿨다가 finally에서 local로 돌아온다
기존 백엔드·PostgreSQL·Redis 6개의 container ID와 시작 시각이 유지되는지 확인한다
deployed 프로필의 공개 주소에는 GET만 보내며 변경 요청은 보내지 않는다
완료 시 두 로컬 백엔드 정상과 관측 전용 모드를 확인한다

## 남은 검증 범위

- Backend 참가자 요청부터 실제 대회 문제 발행·생성·삭제까지의 전체 경로 검증
- Scheduler·Broker·Runtime 복구 신호 전송
- KoTH 점령 상태 복구와 Windows VM 복구
- Active/Standby leader lock 또는 자동 승격
- blue/green 전환과 운영 DB 백업·복원
- 실제 배포 image digest와 로컬 소스 SHA가 같은지 확인

다른 역할의 소스는 옆 폴더에 가져왔지만 실행한 서비스와 구분해서 기록한다
10월 10일 Broker staging에서 후보·예약·inventory 구현을 확인했으며 운영에 사용할 브랜치·주소·인증은 별도로 확정해야 한다
Scheduler 인스턴스 목록은 /api/instances/recent이며 mock의 /api/instances 응답과 다르다
Runtime은 인증된 /internal/v1 경계와 실제 K3s registry가 필요하다
복구 API 계약이 없는 곳에 기존 사용자 reset을 자동 호출하지 않는다
[inventory 조회 계약](inventory-contracts.json)에 최근 20건 제한, operation ID 연결, Pod Ready와 문제 healthcheck의 미확정 입력을 기록했다

## 다음 연결 순서

- DevSecOps가 실제 백엔드 healthz를 프록시 뒤에서 조회할 수 있게 연결
- Scheduler의 최근 20건을 넘어서는 전체 인스턴스 목록 조회 계약 확정
- KoTH 실행 대상·점수 API·주기 수집과 Windows 전용 VM 관리 경로 연결
- Runtime Store 복원 후 실제 K3s 재연결과 진행 중 작업 인계 검증
- 실제 대회 문제의 healthcheck와 Runtime Ready를 함께 판단하는 계약 확정
- 수신팀과 recovery signal의 인증·멱등성·횟수 제한·만료 처리 계약 확정
- CI 발행·GHCR pull·Backend Registry 수집의 동일 revision 경로 검증

2026-10-10 읽기 전용 재조회에서도 msgctf.kr/healthz는 JSON이 아니라 HTML을 200으로 반환했다
이것은 사이트가 응답한다는 증거일 뿐, 배포 백엔드 DB·Redis가 정상이라는 증거가 아니다
내부 backend에 /healthz가 존재하므로 Edge/Frontend 프록시에서 정확한 backend로 연결할 필요가 있다

## 중지

    .\Stop-Monitor.ps1

이 프로젝트의 컨테이너만 정지하고 데이터와 이벤트 volume은 보존한다
운영과 다른 Docker 프로젝트에는 영향을 주지 않는다
## 로컬 실행 자산 연결 · 2026-10-11

Runtime dev 19d2fdff와 전용 PostgreSQL store를 로컬 K3s 단일 노드에 연결했다
연결 시험 인스턴스는 고정 digest의 Nginx이며 대회 문제나 Scheduler의 전체 생성 경로를 대신하지 않는다
대시보드에는 Runtime·Runtime Store·Linux / K3s의 관측을 추가했고 기존 앱 7개에 Runtime 정지 복구를 더했다

| 자산 | 실제 접속 수단 | 확인 범위 |
| --- | --- | --- |
| Runtime | loopback 58188과 Docker 내부망의 인증된 API | 고정 인스턴스의 READY·Pod 준비·endpoint 상태 |
| Runtime Store | Runtime의 인증된 operation 조회 | PostgreSQL store의 완료된 작업 기록 · Runtime 중단 시 DB 단독 판정 불가 |
| Linux / K3s | 전용 kubeconfig의 loopback 58643과 Runtime 경유 상태 조회 | 단일 노드 Ready·pressure·연결 시험 Pod · metrics는 가용 여부를 별도 표시 |
| 연결 시험 서비스 | connection.json에 기록한 loopback NodePort | 실제 Nginx HTTP 응답 |

K3s v1.35.5+k3s1은 별도 msg-sla-execution 클러스터와 3 GiB 메모리 한도를 사용한다
전역 Kubernetes context를 전환하지 않으며 kubeconfig와 인증값은 Git과 이미지 빌드에서 제외한 .execution과 .assets에 둔다
K3s registry의 provider는 현재 Runtime 계약이 AWS·GCP·NCP만 허용하므로 AWS를 로컬 프로토콜 값으로 사용한다
실제 AWS 계정·VM·Cloud API와 연결했다는 의미는 없다

격리 검증은 실제 ingress·egress 차단과 재허용, Strict 그룹 101, Pod cgroup의 pids.max 128을 확인한다
통과하기 전에는 Runtime registry의 해당 capability를 true로 설정하지 않는다
이번 인스턴스는 WEB profile만 사용하며 Pwn·gVisor 실행은 검증하지 않았다
Runtime의 현재 인증 계약에는 별도 조회 토큰이 없어 로컬 모니터도 서비스 토큰을 사용하며 점검 코드는 고정된 GET만 보낸다
운영 연결에는 조회 권한 분리와 별도 관리 경로가 필요하다

최초 연결은 기본 로컬 환경을 준비한 뒤 Sentinel 저장소에서 실행한다

    .\local\Connect-Execution.ps1 -Profile local-recovery

다음 실행부터 Start-Monitor와 Stop-Monitor가 등록된 실행 자산도 함께 처리한다
종료할 때 DB와 K3s 볼륨은 보존한다
클러스터 삭제 뒤 기존 Runtime binding을 재사용하는 복구는 이 절차에 포함하지 않는다

    .\local\Start-Monitor.ps1 -Profile local-recovery -SkipBuild
    .\local\Stop-Monitor.ps1

연결 검증은 Runtime 프로세스의 실제 중단·자동 기동, K3s Pod 삭제·controller 재생성, Runtime DB 중단 표시·재기동 후 operation 보존을 확인한다
현재 Runtime worker는 DB 연결 단절로 종료될 수 있다
실행기는 DB 컨테이너가 실행 중이고 healthcheck를 통과한 뒤에만 정지한 Runtime을 기동한다
독립된 장애 시험 사이에는 정상 관측과 자산별 300초 대기 시간이 필요하다
대기 중 재장애가 발생하면 자동 복구가 다시 준비되지 않으므로 운영자 확인이 필요하다

    python local/verify_execution.py

등록한 K3s 노드의 중지·재기동과 service binding 보존을 별도로 확인할 수 있다

    python local/verify_execution_lifecycle.py

서비스 전환 시험은 쓰는 앱을 중지한 뒤 6개 저장소를 새로 백업한다
새 볼륨에 복원한 데이터를 원래 서비스 이름으로 연결하고 실제 앱 4개를 조회한 뒤 원래 볼륨으로 돌아와 다시 확인한다
Redis 시험용 키는 이 전환 경로에 넣지 않는다
원래 볼륨은 보존하며 앱·실행기 중지와 전환 단계를 private journal에 남긴다

    python local/data_cutover.py drill

작업이 중단되면 Start-Monitor와 자동 복구 실행기가 진행 중인 journal을 확인해 시작을 거부한다
같은 실습의 원래 볼륨으로 돌아가는 명령은 아래와 같다

    python local/data_cutover.py rollback

전환 중 새 업무 요청을 처리하는 무중단 복구나 운영 데이터 자동 덮어쓰기는 구현 범위에 포함하지 않는다
Runtime Store는 위 6개 저장소의 서비스 전환 시험과 분리해 격리 백업·복원을 검증한다
Backend의 예제 checker와 별도로 2026_MSG_CTF에는 실제 KoTH 서비스가 있다
선택한 로컬 구성에는 KoTH 실행 대상과 Windows 문제 VM 이미지·설정이 없다
KoTH·Windows와 클라우드·배포 자산은 해당 실행 대상과 접속 정보가 준비될 때 연결한다

Runtime Store의 별도 검증은 완료된 작업만 있는 상태에서 실행한다
원본 PostgreSQL의 읽기 스냅샷을 새 볼륨에 복원하고 같은 이미지의 Runtime에서 작업 응답을 비교한다
DB와 Runtime을 각각 재기동한 뒤에도 테이블·시퀀스·작업 응답이 같아야 통과한다
원본 컨테이너·시작 시각·볼륨·행 내용은 마지막에 다시 비교하며 시험 자원만 정리한다

    python local/verify_runtime_store.py

실습 환경에는 실제 kubeconfig와 클러스터 연결을 전달하지 않는다
이 검증은 Runtime 작업 기록의 복원과 조회 범위이며 진행 중 작업의 lease 인계, K3s 재연결, 원본 서비스 전환은 포함하지 않는다
백업과 manifest는 비공개 local/.restore-labs에, 인증값과 행 내용을 제외한 결과는 local/artifacts에 남긴다

도구 출처는 [k3d v5.9.0 릴리스](https://github.com/k3d-io/k3d/releases/tag/v5.9.0), [K3s 공식 컨테이너](https://github.com/k3s-io/k3s/pkgs/container/k3s), [Go 공식 다운로드](https://go.dev/dl/)다
k3d 바이너리와 Go 압축 파일은 공식 checksum으로 검증하며 K3s와 도구 이미지는 digest로 고정한다

## 전체 인스턴스 감시

등록된 실행 실습 환경에서 Start-Monitor를 실행하면 instance_monitor를 활성화한다
Scheduler의 GET /api/instances에서 200개씩 읽고 next_cursor가 null일 때까지 다음 페이지를 조회한다
상태·생성 시각으로 걸러내지 않아 오래된 CLEANUP_PENDING과 종료된 인스턴스도 확인한다
명시적으로 등록한 runtime_target_id의 Runtime에 인스턴스 상태를 조회한다
기본 간격은 30초, 요청 제한 시간은 4초, 동시 Runtime 조회는 최대 4개다
목록은 페이지당 2 MiB, 전체 목록 조회는 60초로 제한하며 Runtime 응답은 256 KiB로 제한한다
한도 초과·중간 실패·중복 ID·커서 반복은 UNAVAILABLE로 표시하고 일부 행을 전체 조회 결과로 내보내지 않는다
기존 배포 설정의 /api/instances/recent 주소는 /api/instances로 바꿔 요청하며 최근 목록으로 되돌아가지 않는다

GET /api/instances와 자산 상태 화면의 문제 인스턴스 항목에서 결과를 볼 수 있다
응답은 snake_case이며 전체 페이지 조회가 끝나야 status=COMPLETE, coverage=all_pages, complete_inventory=true가 된다
마지막 페이지까지 비어 있으면 등록된 인스턴스 없음으로 표시한다
조회 도중 새로 생성된 인스턴스나 상태 변경은 다음 주기에 반영될 수 있으며 모든 페이지가 한 시점의 DB 스냅샷은 아니다
Runtime의 READY는 컨테이너·endpoint·노드 준비 상태이며 별도 기능 점검이 등록되지 않으면 문제 기능은 UNVERIFIED로 유지한다
명시적으로 등록한 로컬 시험 서비스만 기능을 점검하며 트랙은 UNMAPPED로 유지한다

인스턴스 ID·target ID·알려진 workload ID가 일치해야 실행 상태를 사용한다
관측 시각이 현재에서 60초를 넘게 벗어나면 STALE로 처리하며 목록 갱신 실패 시 이전 행을 비운다
expires_at·idle_expires_at·hard_expires_at 중 가장 빠른 시각을 사용 기한으로 사용한다
기한 초과나 종료 상태에서 준비된 실행 자원이 남아 있으면 관리자 확인 대상으로 표시한다
Sentinel 감시기는 인스턴스 삭제·reset·복구 요청을 보내지 않는다

2026-10-13 최초 조회 검증 시점의 로컬 Scheduler 최근 목록은 0건이었다
기존 Nginx 연결 시험 인스턴스는 Scheduler가 생성한 인스턴스가 아니므로 목록에 추가하거나 DB에 삽입하지 않는다
별도 검증 호스트에서 시험용 Scheduler 응답을 실제 Runtime에 연결해 조회·불일치·기한·종료 상태 처리를 확인한다
이 검증을 실제 Scheduler 생성부터 문제 기능 복구까지의 통합 성공으로 계산하지 않는다

    dotnet test Sentinel.sln --no-restore
    node --test local/instance-inventory.test.cjs
    python local/verify_instance_inventory.py
    npx --yes --package @playwright/cli playwright-cli -s=sentinel-local --raw run-code --filename local/verify_instance_browser.cjs

Python 검증은 기존 실습 환경과 local/.tooling/dotnet SDK, Debug 빌드 결과가 있어야 실행된다
시험용 조회 서버와 별도 Sentinel 프로세스는 loopback에만 열며 종료 시 정리한다
브라우저 검증의 시험 행은 해당 브라우저에서만 주입하며 마지막에는 실제 전체 목록 화면으로 복귀한다
트랙 매핑, 문제 기능 결과와 인증된 전용 복구 수신 계약은 다음 연동에 필요하다

전체 목록은 별도 PostgreSQL과 실제 Scheduler·Sentinel HTTP 서버로 재현할 수 있다
Scheduler에서 bootJar, Sentinel에서 dotnet build -c Release를 먼저 실행한다

    python local/verify_scheduler_pagination.py --scheduler-jar /path/to/scheduler.jar --output /tmp/sentinel-pagination

Java 24와 .NET 8 경로는 --java·--dotnet으로 지정하고 WSL Docker는 --wsl-distro Ubuntu-24.04를 추가한다
시험 DB에 437개를 넣어 모든 ID·순서를 대조하고 200·200·37개 페이지와 오래된 정리 대기 행을 확인한다
중간 페이지 401·503·커서 반복, 이후 재조회, 삽입·삭제 후 다음 주기 반영도 확인한다
시험 프로세스와 DB는 종료 시 제거하며 결과·로그는 output 경로에 남긴다
Runtime은 연결하지 않으므로 이 시험은 문제 생성이나 실행 상태 확인을 증명하지 않는다

## 로컬 기능 점검과 실제 생성 경로

후속 실습은 실제 Scheduler API로 계산 시험 인스턴스를 생성하고 Broker 예약·Runtime/K3s 실행·종료를 연결한다
Broker에는 실습 전용 계정과 자원 행 하나를 명시적으로 등록한다
이 행의 용량은 제한된 시험 예산이며 실제 클라우드 quota나 노드 에이전트 측정값으로 사용하지 않는다
Runtime의 AWS 값도 로컬 Docker 연결을 위한 기존 프로토콜 표기다

functional-fixture는 입력 두 수의 합과 요청마다 새로 생성한 nonce를 반환한다
실행 자원이 READY이고 instance_id·challenge_id·target_id·runtime_workload_id가 등록값과 일치할 때만 요청한다
정확한 결과는 UP, 오답·HTTP 5xx·응답 제한 시간 초과는 DOWN, 잘못된 JSON·nonce·형식은 CHECKER_ERROR로 표시한다
관측값이 오래되면 기능 결과도 STALE로 바뀌며 이전 UP을 유지하지 않는다

기능 요청 주소는 명시적으로 등록한 loopback 또는 host.docker.internal의 30080~30089 포트와 /calculate 경로로 제한한다
Scheduler가 반환한 service_url을 감시기가 자동으로 따라가지 않는다
기능 요청에는 서비스 인증 토큰을 넣지 않고 리다이렉트·쿠키를 차단하며 응답은 8 KiB까지만 읽는다
기본 요청 제한 시간은 4초이고 Runtime 조회와 기능 요청을 합쳐 인스턴스 4개까지 동시에 점검한다

전용 K3s와 Runtime 실습 환경이 실행 중인 저장소 루트에서 시험 이미지를 빌드한다
아래 Go SDK는 기존 실행 실습에서 공식 checksum으로 검증한 도구다

    New-Item -ItemType Directory -Force local/.execution/functional-build
    $env:GOOS='linux'
    $env:GOARCH='amd64'
    $env:CGO_ENABLED='0'
    $env:GOTOOLCHAIN='local'
    & local/.tooling/go-1.26/go/bin/go.exe build -trimpath -ldflags='-s -w' -o local/.execution/functional-build/functional-fixture local/functional-fixture/main.go
    docker --host npipe:////./pipe/dockerDesktopLinuxEngine build -f local/functional-fixture/Dockerfile -t ghcr.io/msg-ctf/sentinel-local-functional-fixture:local-only local/.execution/functional-build
    python local/functional_lab.py prepare
    python local/functional_lab.py create --ttl 120

이미지는 로컬 containerd로 가져오며 GHCR에 게시하지 않는다
Scheduler의 이미지 정책에 맞는 이름과 digest를 사용하고 실제 Registry 릴리스 경로 검증과 구분한다
생성 ID·팀·문제·예약·점검 바인딩은 Git에서 제외한 local/.execution/functional/ledger.json에 기록한다
생성 요청은 중복 호출 시 새 인스턴스가 생기므로 도구는 결과가 불명확한 POST를 자동 재시도하지 않는다

생성 명령이 출력한 ID로 아래 점검을 실행한다
새 바인딩을 읽으려면 새 코드로 빌드한 Sentinel을 재시작해야 한다
재시작 동안 호스트 실행기를 멈추고 Sentinel 기동 뒤 다시 연결한다

    python local/recovery_agent.py stop
    $env:SLA_PROFILE='local-recovery'
    docker --host npipe:////./pipe/dockerDesktopLinuxEngine compose -p msg-sla-real-local -f local/compose.yaml -f local/compose.assets.yaml -f local/compose.execution.yaml build sentinel
    docker --host npipe:////./pipe/dockerDesktopLinuxEngine compose -p msg-sla-real-local -f local/compose.yaml -f local/compose.assets.yaml -f local/compose.execution.yaml up -d --no-deps sentinel
    Start-Process python -ArgumentList 'recovery_agent.py','run' -WorkingDirectory (Join-Path (Get-Location) 'local') -WindowStyle Hidden -RedirectStandardOutput local/.recovery/stdout.log -RedirectStandardError local/.recovery/stderr.log
    python local/verify_functional_flow.py --instance <생성한 인스턴스 ID>

검증 도구는 등록한 시험 인스턴스의 오답·형식 오류·서비스 실패를 순서대로 적용하고 정상 설정으로 복귀시킨다
이후 해당 인스턴스를 실제 Scheduler로 삭제하고 별도의 1분 TTL 인스턴스도 생성해 정리를 기다린다
두 경우 모두 Scheduler CLEANED, Runtime TERMINATED, Broker RELEASED, 네임스페이스 부재와 Sentinel REMOVED를 확인한다
기능 오류 해제는 실습 실행기의 수동 조치이며 Sentinel의 자동 인스턴스 복구 기능이 아니다
실습용 HTTP API에는 오류 설정 경로가 없고, 실행기는 소유 ID·팀·이미지가 일치하는 Pod에만 명령을 보낸다

별도로 살아 있는 등록 시험 인스턴스에서 Pod 재생성을 확인할 수 있다

    python local/verify_owned_pod_recovery.py --instance <정상 실행 중인 시험 인스턴스 ID>

이 도구는 해당 시험 Pod 하나를 삭제하고 K3s Deployment의 새 Pod와 새 기능 응답을 기다린다
복구 주체는 K3s이며 짧은 교체 구간을 Sentinel이 DOWN으로 관측했는지도 따로 기록한다

공유 가능한 근거는 [후속 검증 요약](verification/2026-10-13-functional.json)에 기록한다
실제 대회 문제별 정상 기능 계약, Runtime 기능 결과 수신 계약, 전체 인스턴스 목록, 트랙과 전용 복구 수신 계약은 남아 있다
