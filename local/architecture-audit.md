# 자산 상태와 역할별 연결 검수

## 2026년 10월 11일 로컬 실행 자산과 서비스 전환

Runtime dev 19d2fdff를 별도 PostgreSQL store와 K3s 단일 노드에 연결했다
고정한 연결 시험 인스턴스는 Nginx이며 실제 대회 문제나 Scheduler를 거친 생성 흐름 검증은 아니다
직접 점검 12개와 Backend 구성 요소 4개가 정상이며 자동 정지 복구 대상은 앱 8개다
Windows 문제 VM·KoTH·클라우드·배포 역할 9개는 실행 대상이 준비되지 않아 미연결로 표시한다
아래의 10월 10일 기록은 확장 전 검증 시점의 수치다

Runtime 상태는 인스턴스·target 일치, 60초 이내 관측, READY, 모든 container의 RUNNING·ready, endpoint와 node 상태로 판정한다
Runtime Store는 PostgreSQL에서 저장한 완료 operation의 인증된 조회로 확인한다
Runtime이 중단되면 이 조회 경로만으로 DB 자체 장애를 구분하지 못하므로 실행기가 Docker에서 DB 컨테이너와 healthcheck를 별도로 확인한다
K3s의 ingress·egress 차단과 재허용, 실제 그룹 101, Pod cgroup의 pids.max 128을 확인했다
WEB profile만 사용했고 Pwn·gVisor는 검증하지 않았다

K3s의 관리 API와 NodePort, Runtime API는 loopback에만 공개한다
클러스터는 msg-sla-execution이며 사용자 PC의 Windows를 문제 자산으로 사용하지 않는다
소스 export와 도구·바이너리·이미지 근거는 source-versions.json과 비공개 로컬 receipt에 남겼다
Runtime registry의 AWS 값은 현재 provider enum을 맞추는 로컬 프로토콜 값이며 AWS에 접속하지 않는다

서비스 전환 시험은 로컬 앱의 쓰기를 중지하고 PostgreSQL 4개·Redis 2개를 새로 백업했다
새 볼륨에 복원한 뒤 원래 서비스 이름에 연결하고 Backend 두 개·Scheduler·Broker를 실제로 조회했다
원래 볼륨으로 복귀한 뒤 같은 앱 4개를 다시 확인했고 검증용 볼륨·컨테이너·네트워크를 정리했다
PostgreSQL 102개 테이블·554행·시퀀스 18개의 일치를 확인했으며 Redis 원본은 각각 0개 키다
참가자·문제·진행 중 작업이 들어 있는 업무 데이터의 복구는 아직 검증하지 않았다
Runtime Store는 이 6개 저장소 복원 범위에 포함하지 않는다

전환 동안 호스트 실행기를 중지하고 배타 잠금과 진행 journal로 중복 실행을 막는다
앱 정상 관측 2회 이후 실행기를 다시 연결하며 원래 데이터 볼륨을 유지한다
별도 rollback 명령은 제공하지만 프로세스 강제 종료를 주입한 crash rollback 시험은 수행하지 않았다
운영 데이터 자동 덮어쓰기와 무중단 전환·고가용성 검증은 이 기록에 포함하지 않는다

.NET 시험 107개, Python 시험 27개, 화면 모델 시험 52개, 브라우저 확인 14개를 통과했다
브라우저는 PC·모바일 배치, 로컬 복구 8개, 실측 연결 6개, 전환 기록 누락·오프라인 표시를 확인했다

Runtime 중단·자동 기동, Pod 삭제·controller 재생성, Runtime DB 단절·복귀를 포함한 실측 19개 항목을 통과했다
현재 Runtime worker는 DB 연결 단절로 함께 종료된다
DB가 중단된 동안 재기동을 보류하고 DB healthcheck가 정상으로 돌아온 뒤 같은 Runtime 컨테이너를 자동 기동했으며 저장된 operation을 유지했다
처음에는 직전 복구의 300초 대기 중에 두 번째 장애를 주입해 시험이 실패했다
대기 값을 바꾸지 않고 정상 재준비 후 다시 시험했으며 첫 실패 기록을 별도로 보존했다
대기 중 재장애로 정상 기준을 다시 만들 수 없는 경우에는 운영자 확인이 필요하다
실측 이후 요청 생성 조건을 더 제한해 연결 실패와 시간 초과 모두 호스트의 최신 Runtime 정지·DB 정상 확인을 요구한다
이 추가 조건은 정상 요청·만료된 확인·DB 비정상 확인을 포함한 단위 시험 2개로 확인했으며 실측 이미지와 최종 이미지의 ID를 따로 기록했다

최종 이미지에서는 K3s 노드만 중지·재기동하는 시험 7개 항목을 통과했다
노드 중단과 Runtime Store 상태를 구분했고 같은 노드 컨테이너·operation·서비스 주소를 유지했다
재기동 후 NodePort의 실제 HTTP 응답을 확인했으며 Runtime·DB 재시작이나 추가 호스트 복구 요청은 없었다

- [K3s 격리 조건 실측](artifacts/execution-capabilities.json)
- [서비스 전환과 원래 볼륨 복귀](artifacts/data-cutover-20261010T155629Z-9fa4fc56.json)
- [실행 자산 복구 시험](artifacts/execution-verification.json)
- [K3s 중지·재기동 확인](artifacts/execution-lifecycle.json)
- [브라우저 확인](artifacts/execution-browser-20261011.log)
- [실행 방법과 제한](README.md)

## 2026년 10월 10일 데이터 백업과 격리 복원

PostgreSQL 4개와 Redis 2개의 백업 생성, 새 볼륨 복원, 실제 앱 조회를 확인했다
원본과 같은 이미지 ID를 사용하고 고유한 프로젝트의 내부 네트워크에서 호스트 포트 없이 실행했다
최종 복원 검증은 23시 33분 39초부터 23시 34분 49초 KST까지 약 71초 걸렸다
이 수치는 현재 로컬 데이터 크기의 격리 검증 시간이며 운영 복구 목표 시간이 아니다

| 대상 | 원본에서 복원한 데이터 | 확인 결과 |
|---|---|---|
| 내부 PostgreSQL | 45개 테이블·268행·시퀀스 9개 | 행 내용 해시와 시퀀스 일치, Django 사용자·마이그레이션 조회 |
| 외부 PostgreSQL | 45개 테이블·268행·시퀀스 9개 | 행 내용 해시와 시퀀스 일치, Django 사용자·마이그레이션 조회 |
| Scheduler DB | 3개 테이블·17행 | 실제 인증 목록 조회, 현재 항목 0개 |
| Broker DB | 9개 테이블·1행 | 실제 인증 inventory 조회, 현재 항목 0개 |
| 내부·외부 Redis | 원본 키 각각 0개 | RDB 검증, 격리한 DB 1·7의 시험 값과 TTL 보존, AOF 재기동 |

PostgreSQL의 비어 있지 않은 테이블은 마이그레이션 이력과 Django 기본 권한·콘텐츠 유형이다
참가자·문제·인스턴스가 들어 있는 업무 데이터의 복구나 진행 중 작업 재개를 검증한 것은 아니다
각 DB의 행 해시와 논리 백업은 동일한 읽기 스냅샷을 사용하며 시퀀스는 백업 전후와 복원 결과를 비교했다
저장소 간 시점을 맞추는 쓰기 중지나 원본 데이터 덮어쓰기, 자동 연결 전환은 수행하지 않는다

첫 시험에서는 Redis 이미지가 자동으로 불러오는 모듈이 설정 저장 과정에서 중복돼 재기동이 실패했다
복원 전용 최소 설정에 AOF 옵션만 저장하도록 수정했고 재기동을 다시 확인했다
두 번째 시험에서는 Scheduler 복제본에 추가한 읽기 전용 pool 설정이 기동 중 속성 적용과 충돌했다
해당 추가 옵션을 제거하고 내부 네트워크 차단과 외부 작업 주소 미연결을 유지한 상태에서 실제 조회를 확인했다
두 실패와 최종 성공의 검증용 컨테이너·볼륨·네트워크를 모두 정리했다

원본 컨테이너의 ID와 시작 시각, 앱 자동 복구 요청 11건을 유지했다
원본 Redis DB 1·7에는 시험용 키가 없으며 백업과 비공개 실행 설정 경로의 HTTP 응답은 404다
현재 직접 점검 9개와 간접 구성 요소 4개는 정상, 미연결 역할 12개는 그대로다
차단 조건 시험 13개, 화면 모델 시험 50개, 브라우저 확인 22개를 통과했다

- [최종 데이터 복원 결과](artifacts/data-restore-20261010T143338Z-13bf534c.json)
- [원본 유지·공개 경계 최종 확인](artifacts/data-recovery-final-state-20261010.json)
- [Redis 재기동 실패 기록](artifacts/data-restore-20261010T142608Z-caa9a8f7.json)
- [Scheduler 복제 설정 실패 기록](artifacts/data-restore-20261010T143022Z-1c82cc9f.json)
- [백업·복원 실행 방법](README.md)
- [데이터 복구 계약](data-recovery-contract.json)

## 2026년 10월 10일 앱 서비스별 복구 확장

local-recovery에 내부·외부 Backend, Scheduler, Broker, 내부·외부 Frontend, Edge의 정지 컨테이너 기동을 연결했다
서버의 고정 접속 계약과 Windows 실행기의 프로젝트·서비스·라벨 검사를 함께 적용한다
정상 기준과 300초 대기 시간은 자산마다 관리하고 실행 중인 요청은 전체에서 한 개로 제한한다
DB·Redis 6개와 실행 대상이 미등록인 12개 역할은 자동 기동 대상에 포함하지 않는다

Backend는 API·DB·Redis, Scheduler와 Broker는 인증된 조회와 DB 연결을 정상 기준으로 사용한다
Frontend는 정적 화면 응답과 해당 Backend, Edge는 Nginx 상태 계약과 두 트랙의 Frontend·Backend를 확인한다
완료에는 기동 결과를 받은 뒤 각 확인 대상의 서로 다른 정상 관측 2회가 필요하다
Frontend·Edge의 실제 API 전달과 참가자 기능 전체를 이 자동 기준에 포함한 것으로 해석하지 않는다

기존 복구 기록과 claim을 유지하며 schema_version 1에서 2로 전환한다
감시기 재시작 시 미인수 요청을 취소하고 정상 기준을 새로 확인한다
다른 자산으로 바뀐 claim 응답, 실행 중인 컨테이너, OOM 종료, 비정상 의존 서비스는 기동을 차단한다

.NET 단위 시험 90개와 진입점 통합 시험 1개, Windows 실행기 시험 8개, 화면 모델 시험 43개를 통과했다
첫 실측에서 Scheduler 정지가 연결 실패 대신 이름 조회 지연에 따른 TIMEOUT으로 관측돼 기존 조건으로 요청이 생기지 않았다
실행기의 15초 이내 정지 확인이 함께 있을 때만 TIMEOUT을 복구 근거로 사용하는 조건을 추가했다
정지 확인은 재시작 시 초기화하며 다른 자산이나 이전 관측을 재사용하지 않는다
앱 서비스 7개를 하나씩 실제로 중지한 시험에서 자동 기동과 정상 확인을 모두 통과했다
각 시험은 해당 컨테이너의 ID 유지와 시작 시각 변경, 같은 프로젝트의 다른 컨테이너 시작 시각 유지, 두 트랙의 화면·API 전달을 확인했다
DB·Redis 6개는 이 복구 시험 동안 같은 컨테이너와 시작 시각을 유지했다
Scheduler의 TIMEOUT 장애 이벤트가 기록된 점검 주기에 복구 요청이 생성됐고 인증 조회와 DB 연결 복귀로 완료됐다

- [앱 7개 실제 복구 결과](artifacts/multi-recovery-20261010T140000Z.json)
- [Scheduler 시간 초과와 정상 복귀 기록](artifacts/scheduler-recovery-evidence-20261010.json)
- [보완 전 Scheduler 복구가 발생하지 않은 시험](artifacts/multi-recovery-20261010T135226Z.json)

Scheduler DB와 Broker DB를 각각 중지한 시험은 복구 감시가 준비된 상태에서 시작했다
연속 장애 판정 기준을 넘겨 관측해도 복구 요청이 추가되지 않았으며 두 앱과 기존 Backend·DB·Redis 6개의 시작 시각이 유지됐다
시험 스크립트가 해당 DB를 다시 기동한 뒤 직접 점검 9개가 정상으로 돌아왔다
접속·인증·장애 오인 방지를 포함한 실측 30개 항목을 통과했다

- [접속과 DB 장애 오인 방지 결과](artifacts/assets-verification-20261010T141205Z.json)

관측 모드 전환 시 실행기 중지, 실행 서비스 유지, 자동 복구 모드 재진입 시 기록 유지와 재실행 방지의 4개 항목을 통과했다
브라우저 검증 25개 항목은 두 트랙의 실제 React 실행, 7개 복구 연결, 미연결 자산 구분, 오프라인 표시, 모바일 폭과 지도 위치 보존을 확인했다
마지막 문구 반영 뒤 실행기 연결과 앱 7개의 장애 감시 상태를 화면에서 다시 확인했다
최종 상태는 직접 점검 9개와 Backend 구성 요소 4개가 정상이며 12개 역할은 연결 대기다
작업 전 성공 기록 2건을 그대로 보존했고 이번 검증으로 추가된 성공 9건을 포함해 복구 기록은 11건이다

- [프로필 전환과 기록 유지](artifacts/recovery-profile-verification-20261010T141308Z.json)
- [최종 상태 확인](artifacts/multi-recovery-final-status-20261010.json)
- [복구 화면](../output/playwright/recovery-seven-services-20261010.png)

### 로컬 실행 환경 복원

시작 당시 Docker Desktop 엔진이 꺼져 있었고 남은 소켓과 종료된 프로세스 확인 오류로 기동이 실패했다
Docker의 run 폴더 두 개와 secrets engine의 소켓 폴더 한 개를 날짜가 붙은 백업 이름으로 보존했다
컨테이너 데이터 초기화 없이 Docker의 unattended 모드로 엔진을 기동했다
빌드 검증용 .NET 8 SDK는 Microsoft 배포 메타데이터의 SHA512를 대조한 뒤 local/.tooling 안에 풀었다
이 도구 폴더는 Git과 Docker 빌드에서 제외한다

## 2026년 10월 10일 다른 자산의 실제 점검 추가

25개 역할을 모두 상태 목록에 등록하고 직접 점검 9개와 Backend 구성 요소 4개를 연결했다
새로 실행한 대상은 내부·외부 Frontend, 로컬 Edge, Scheduler·전용 PostgreSQL, Broker·전용 PostgreSQL이다
최종 정상 자산은 13개, 미연결 역할은 12개이며 상세창에서 점검 범위와 필요한 연결 정보를 확인할 수 있다

Scheduler는 dev 5c695142, Broker는 staging 99ff5b88, Frontend는 로컬 fix/admin-operation-reliability의 c0a85359를 별도 사본으로 빌드했다
원래 체크아웃과 브랜치를 바꾸지 않았다
운영 배포 버전을 확인한 결과는 아니며 실제 provider 계정과 문제 VM은 연결하지 않았다

### 실행으로 확인한 범위

- Scheduler의 인증된 recent 조회와 전용 PostgreSQL 연결
- Broker의 인증된 inventory 첫 페이지와 전용 PostgreSQL SELECT 1
- 인증 누락 차단과 Broker inventory 토큰의 관리자 API 접근 차단
- 두 Frontend의 실제 React 실행과 Edge를 거친 트랙별 Backend 응답 전달
- Scheduler DB·Broker DB·외부 Frontend 중지 시 장애 감지와 시험 스크립트의 재기동 뒤 정상 복귀
- 위 장애에서 내부 Backend 자동 복구 요청이 추가되지 않음
- 프로필 전환 뒤 실행 서비스 13개의 컨테이너 ID·시작 시각과 기존 복구 기록 유지

Scheduler recent와 Broker inventory는 모두 빈 목록이었으며 API 조회 정상으로만 판정했다
문제 인스턴스나 클라우드 VM의 정상 상태를 만들지 않았다
Broker DB 중지 시 API와 readiness 점검은 TIMEOUT으로 표시됐다
DB 중지는 시험 스크립트로 알고 있지만 Sentinel 응답만으로 원인을 DB로 특정한 것은 아니다

### 연결 대기 자산

- Runtime·Runtime Store·Linux / K3s는 실제 cluster registry·kubeconfig·인스턴스 매핑 필요
- KoTH 서비스·채점은 실행 대상과 점수 API·토큰·period 연결 필요
- Windows는 실제 문제 VM과 관리 접속 필요
- Cloud API는 계정·리전·VM 매핑 필요
- info.yaml·CI·GHCR·bundle은 배포 revision·workflow run·digest·artifact ID 필요
- Challenge Registry는 현재 로컬 Backend에 새 bundle 수집 경로가 없음

Runtime의 LoadRegistry는 빈 clusters 배열을 거절하므로 실제 클러스터 연결을 기다린다
KoTH 템플릿의 관리·점수 계약은 확인했지만 템플릿을 실행 서비스로 계산하지 않았다
추가 자산의 자동 복구는 비활성이며 기존 내부 Backend 한 개의 정지 컨테이너 기동만 유지한다

### 검증과 수정 근거

실환경 31개 검증은 artifacts/assets-verification-20261009T181601Z.json에 기록했다
단위 테스트 77개와 진입점 테스트 1개, 대시보드 모델 테스트 43개, 실제 브라우저 검증 13개가 통과했다
프로필 전환 4개 검증은 artifacts/recovery-profile-verification-20261009T181924Z.json에 기록했다
최종 이미지와 로그 경로는 source-versions.json의 asset_monitor_update에서 확인할 수 있다

Spring Actuator의 기본 vendor JSON 응답을 관측한 뒤 Accept 헤더로 application/json을 요청하도록 수정했다
Docker Desktop에서 내부 전용 네트워크에만 연결된 서비스의 호스트 포트가 열리지 않아 loopback 게시용 local_access 브리지를 추가했다
Docker 자동 주소 풀 소진 뒤 기존 서브넷과 겹치지 않는 10.246.244.0/24를 Scheduler 데이터망으로 지정했다
프로필 검증은 매번 다시 실행하는 Alembic 작업의 정상 종료를 별도로 확인하고 실행 서비스 13개의 유지 여부를 비교하도록 수정했다
이전 보고서는 아래에 보존한다


## 2026년 10월 10일 접속 계획과 로컬 자동 복구 추가

아래 이전 관측 검수와 구분되는 후속 구현이다
최종 실행 프로필은 local-recovery이며 내부 Backend 정지 컨테이너 기동만 자동으로 수행한다
기존 local·deployed 프로필은 관측 전용을 유지한다

25개 구성 역할과 Sentinel 자체의 감시·관리 접속·복구 담당·전제 조건·완료 기준을 asset-access.json에 등록했다
운영 호스트·관리 주소·인증정보 참조·서비스 매핑은 미설정이며 SSH·Windows·클라우드 실행 어댑터는 미연결이다
GitHub·GHCR·사양·산출물은 VM 접속 대상과 구분해 저장소·digest·작업·산출물 ID를 연결 입력으로 기록한다
기존 Scheduler reset은 인스턴스 교체이므로 문제 상태를 보존하는 복구 API와 구분한다

### 구현한 실행 경로

Sentinel이 정상 기준과 연속 연결 실패를 확인한 뒤 UUID 요청을 영속 저장한다
Windows 실행기가 인증된 loopback API에서 요청을 인수하고 로컬 Docker named pipe로 대상 컨테이너를 확인한다
프로젝트·서비스·전용 라벨·정지 상태를 모두 검사하며 실행 중이거나 OOM으로 종료한 대상에는 기동 명령을 보내지 않는다
실행 전 SQLite에 기록하고 같은 요청을 다시 실행하지 않는다
Sentinel 컨테이너에는 Docker socket을 연결하지 않는다

기동 결과 수신은 기능 확인 단계로만 전환한다
수신 이후 새로 관측한 Backend 정상 결과가 두 차례 있어야 복구 완료로 전환한다
DB·Redis 장애·시간 초과·인증 오류·오래된 관측은 자동 기동 근거에서 제외한다
장애당 최대 1회, 요청과 기능 확인 각각 120초, 직전 요청 시각 기준 재실행 대기 300초와 정상 복귀 조건을 적용한다
기록 저장 실패·기한 초과·실행 결과 불명 때 같은 요청을 재실행하지 않는다

### 독립 검증 결과

| 검증 | 결과 | 확인 범위 |
|---|---|---|
| .NET | 단위 63개 · 진입점 1개 통과 | 중복 인수·잘못된 실행기·기한·저장 실패·재시작·실행 이전 관측 배제 |
| Windows 실행기 단위 | 4개 통과 | 다른 프로젝트·외부 트랙·실행 중·OOM·중복 실행 제외 |
| 실제 로컬 장애 시험 | 18개 통과 | DB 장애 오조치 방지와 Backend 자동 기동·기능 정상 복귀 |
| 실제 모드 전환 | 4개 통과 | 실행기 중지·기록 보존·완료 요청 재실행 방지·컨테이너 유지 |
| 화면 모델 | 41개 통과 | 기존 상태·연결 판정과 전체 자산 접속 계획 대응 |
| 실제 브라우저 | 10개 통과 | 복구 기록·내외부 구분·Windows 계획·연결 끊김·모바일 |
| 공유 계약 | 574개 필드 정적 검사 통과 | snake_case · 미지정 JSON 필드 거부는 API 시험에서도 확인 |

최종 실제 시험은 먼저 내부 PostgreSQL을 중지해 Backend 프로세스와 복구 요청 수가 유지되는지 확인했다
이어서 내부 Backend를 중지했고 Sentinel과 실행기가 같은 컨테이너를 기동해 기능 정상 복귀까지 확인했다
Backend 기동 단계에서 외부 트랙과 다른 컨테이너의 ID·시작 시각은 바뀌지 않았다
시험용 PostgreSQL은 검증 스크립트가 복원했으며 DB 자동 복구 구현으로 계산하지 않는다

모바일 첫 검증은 클릭할 노드를 화면 안에 맞추는 2픽셀 이동 때문에 실패했다
지도 위치가 초기화되지 않는 것을 직접 확인하고 애니메이션 프레임 대기와 4픽셀 오차 범위로 검증을 수정했다
실패 로그는 artifacts/browser-recovery-pan-before-20261010.log에 보존했다
모드 전환 검증기의 Windows 하위 프로세스 출력 pipe 대기도 파일 출력으로 수정한 뒤 재검증했다

최종 실측 근거는 artifacts/recovery-verification-20261009T173936Z.json이다
모드 전환 근거는 artifacts/recovery-profile-verification-20261009T173846Z.json이다
브라우저 근거는 artifacts/browser-recovery-final-20261010.log이며 날짜가 있는 화면 캡처도 보존했다
세부 버전·시험 목록은 source-versions.json의 recovery_update에 기록했다
기존 관측 프로필의 기본 점검 9개와 deployed 왕복도 다시 통과했다

운영 호스트 연결, 문제별 상태 보존, VM 전원 복구, DB 복원과 다중 실행기 리더 선출은 후속 계약과 구현 범위다
현재 Windows 실행기는 시작 스크립트가 띄운 프로세스이며 PC 재부팅 뒤 시작 명령을 다시 실행해야 한다

## 2026년 10월 10일 재확인

기존 checkout과 로컬 데이터를 유지하고 원격 fetch 뒤 차이를 읽었다
아래 10월 7일 기록은 과거 기준이며 오늘 실행 결과와 합치지 않는다

| 역할 | 로컬 checkout | 이번에 읽은 원격 소스 | 차이 |
|---|---|---|---|
| Sentinel | feat/real-platform-monitoring · e17e94d + 기존 로컬 변경 | dev · e17e94d | 기본 브랜치 변경 없음 |
| Backend | main · a045cef | main · 5896bb5 | CD workflow만 변경 · healthz와 생성 payload 변경 없음 |
| Scheduler | dev · 37ad2a0 | dev · 5c69514 | healthcheck 검증·저장·전달 추가 · 전달 옵션 기본 false |
| Broker | dev · bde6361 | dev · bde6361, staging · 99ff5b8 | dev는 README뿐 · staging에 후보·예약·inventory·DB 구현 존재 |
| Runtime | dev · 19d2fdf | dev · 19d2fdf, 기본 main · 738419f | dev 변경 없음 · 기본 main은 README 기준 |

Broker staging은 소스 확인용으로 읽었고 실행 기준으로 승인되거나 로컬에서 실행된 상태는 아니다
프론트·DevSecOps·문제 발행 소스는 이번 범위에서 새로 fetch하지 않아 10월 7일 고정 SHA 근거를 유지한다
대시보드의 각 소스 확인 시각도 이 차이를 반영한다

노션 도구는 10월 10일 재조회에서 10월 7일 16시 6분 기준 본문을 반환했다
이 반환본에는 공유 데이터 VM 설계와 트랙별 PostgreSQL·Redis 분리안이 함께 있다
첨부 그림 4개는 파일 참조만 받았고 이미지 내용은 확인하지 못했다
반환 시점 이후 편집 여부와 최신 도면 일치 여부는 확인 불가다

운영 주소는 10월 10일 01시 21분에 GET으로 다시 확인했다
루트와 healthz 모두 HTTP 200, text/html, 2201바이트를 반환했다
사이트 문구는 일치했지만 백엔드 JSON 계약을 충족하지 않았다
원문 응답은 저장하지 않았고 내부·외부 어느 트랙에도 배정하지 않았다
배포 프록시 내부 설정이나 배포 image digest는 이 응답만으로 확인할 수 없다

### inventory 연결 준비

[조회 계약과 미확정 입력](inventory-contracts.json)에 읽기 전용 연동 경계를 기록했다

| 신호 | 확인한 코드 계약 | 연결 전에 필요한 것 |
|---|---|---|
| Scheduler 요청 상태 | Bearer 인증 GET /api/instances/recent · SUCCESS와 data 목록 | 실제 base URL·서비스 토큰 참조·트랙 매핑 |
| 전체 inventory | 최근 생성 20건만 반환 · pagination 없음 | 전체 활성 목록 또는 pagination 계약 |
| Runtime 상태 | Bearer 인증 runtime-status · instance·target·workload 결합 | 실제 Runtime 주소·서비스 토큰 참조·K3s registry |
| Runtime operation | GET /internal/v1/operations/{operation_id} | Scheduler 조회 응답에 없는 operation ID를 연결하는 계약 |
| Pod Ready | container ready와 endpoint_ready만 응답 | Pod Ready condition과 관측 시각을 읽는 계약 |
| 문제 healthcheck | 최신 Scheduler는 요청 사양을 저장 · Runtime 전달 기본 false | Runtime 지원 버전·전달 옵션·실제 점검 결과 계약 |
| 자동 복구 | 수신자 미확정 · observe_only | 인증·멱등성·횟수 제한·만료·소유권·결과 회신 계약 |

Scheduler RUNNING, Runtime READY, 컨테이너 ready를 문제 정상으로 치환하지 않는다
HTTP 수신 시각과 Runtime observed_at을 따로 검증하고 오래된 관측은 STALE로 표시해야 한다
API 주소나 토큰이 없으면 요청을 보내지 않고 미연결로 유지한다
현재 inventory client와 workload 생성·삭제·복구 E2E는 연결하지 않았다

### 검토 판단

계획 검토는 전체 구조와 실제 감시 범위가 다름을 확인했다
연동 검토는 백엔드 생성 payload 불일치와 inventory의 누락 신호를 확인했다
보안·운영 검토는 공개 응답 원문·토큰 저장 금지와 트랙·Compose 범위 고정을 유지했다
반론 검토에서는 Broker 구현이 없다는 과거 판단을 staging 소스 근거로 좁혔다
실행 기준·주소·인증이 미확정이라는 제한은 남는다
팀별 점수나 전체 MVP 완료율은 이번 범위에서 산정하지 않았다

### 오늘 재현한 실행

| 항목 | 결과 | 근거 |
|---|---|---|
| 대시보드 모델 | 39개 통과 | Node 기본 테스트 · 자산·아키텍처·좌표계 |
| .NET 단위 테스트 | 45개 통과 | artifacts/dotnet-tests-20261010.log |
| 진입점 테스트 | 1개 통과 | 같은 .NET 로그 · 전체 E2E를 뜻하지 않음 |
| 실제 백엔드 기본 검증 | 9개 검사 통과 | artifacts/actual-verification-20261009T163320128246Z.json |
| 내부 PostgreSQL·외부 Redis 장애 시험 | 23개 검사 통과 | artifacts/actual-fault-verification-20261009T163445540390Z.json |
| local·deployed 왕복 전환 | 6개 서비스 ID·시작 시각 유지 | artifacts/profile-verification-20261009T163842488457Z.json |
| 실제 브라우저 검증 | 12개 검사 통과 | artifacts/browser-verification-20261010.log |
| 프로필·연동 준비 JSON 이름 검사 | 143개 필드 정적 검사 통과 | 상대 서비스 실행 호환성을 뜻하지 않음 |
| 한국어 문구 검사 | 통과 | README·이 문서·대시보드 catalog 문구 |

장애 시험은 10월 10일 01시 34분부터 01시 37분까지 로컬에서 실행했다
내부 PostgreSQL 장애는 DATABASE_DOWN으로 확인하고 외부 트랙 정상을 유지했다
외부 Redis 장애 응답은 실제 백엔드에서 약 5초 후 503으로 왔다
Sentinel의 4초 제한에 걸려 TIMEOUT으로 기록했으므로 원인 구성 요소를 확인했다고 표시하지 않았다
각 장애의 이벤트 중복 억제와 복구 이벤트를 확인했고 두 트랙 모두 정상으로 돌아왔다
서비스 재시작은 시험 스크립트가 수행했으며 Sentinel 자동 복구가 아니다

브라우저에서 오프라인일 때 정상 자산 0개, 확인 불가 6개, 미연결 5개와 실측 연결 0개를 확인했다
재연결 뒤 정상 6개와 실측 연결 4개로 돌아왔다
프로필 전환과 의도한 오프라인 시험 중 네트워크 오류는 있었고 JavaScript 실행 오류는 없었다
데스크톱 전체 지도, 같은 좌표계 확대, 모바일 가로 이동을 실제 브라우저로 확인했다
모바일에서 상세창을 열고 닫으면 너비 변화에 따른 자동 맞춤이 이동 위치를 초기화하는 결함을 재현했다
자동 맞춤은 기존 지도 좌표를 유지하도록 수정했고 같은 검사가 통과했다
사용자가 맞춤 버튼을 누를 때는 처음 위치로 돌아간다
수정 전 실패 기록은 artifacts/browser-detail-before-20261010.log에 보존했다
최종 전체 지도와 모바일 이동 캡처는 output/playwright/architecture-restored-20261010.png와 output/playwright/mobile-pan-20261010.png에 있다

Docker Desktop 시작 시 오래된 AF_UNIX 소켓 때문에 Inference manager와 Secrets Engine 초기화가 실패했다
소켓만 있음을 확인한 실행 폴더를 보관 이름으로 변경해 시작했고 데이터 볼륨을 초기화하지 않았다
소켓 보관 경로는 사용자 LocalAppData 아래 Docker/run-stale-sla-20261010-0128, Docker/run-stale-sla-20261010-0127, docker-secrets-engine-stale-sla-20261010, docker-secrets-engine-stale-sla-20261010-0127이다
다른 Compose 프로젝트에는 시작·중지·삭제 명령을 보내지 않았다

기존 local/artifacts의 10월 7일 리포트는 보존했고 오늘 리포트는 UTC 실행 시각이 붙은 별도 파일로 저장했다
source-versions.json은 기존 기록을 남기고 current_review에 오늘 근거를 추가했다

## 2026년 10월 7일 기록

2026-10-07 기준 소스와 로컬 실행을 나눠 확인했다
대시보드는 http://127.0.0.1:58240 에서 볼 수 있다

## 화면에서 구분하는 것

- 감시 상태는 Sentinel의 현재 점검 응답에서 계산
- 소스 확인은 고정 commit에서 호출자 또는 수신 경로를 확인한 상태
- 설계는 운영안에 있지만 이 로컬 실행에서 점검하지 않은 구성
- 계약 불일치는 확인한 두 기준본의 요청 형식 또는 수집 경로가 맞지 않는 상태

서비스가 정상이라는 이유로 다른 역할과의 통신까지 성공한 것으로 표시하지 않는다
설계 노드는 감시 대상 수나 정상률에 더하지 않는다
DB·Redis 연결선만 백엔드 healthz 구성 요소에서 실측 판정을 받는다
이 연결은 VM 전원 상태나 업무 트랜잭션 전체 검증을 뜻하지 않는다

## 소스 기준

| 역할 | 저장소와 기준본 | 확인한 SHA |
|---|---|---|
| 백엔드 | MSG-CTF/msg-backend main | a045ceff2eb09a15a2f48126bd85faa8433dd19b |
| 스케줄러 | MSG-CTF/instance-scheduler dev | 37ad2a08c0c5ea7ab9d96d8bf655b65c7ea699ec |
| 브로커 | MSG-CTF/resource-broker dev | bde6361dd36440c7ee299adf4ded113b978da25d |
| Runtime | MSG-CTF/secure-provisioner dev | 19d2fdffef753bb9415861c4a6b382b6408455ea |
| DevSecOps | MSG-CTF/msgctf-devsecops main | cd47cb5c738ab612dae57e385368f279d26054aa |
| 프론트 | MSG-CTF/front-team main | 516fbc9487fc36a32ccf9b95c98a41e5af244b97 |
| 문제 발행 | MSG-CTF/2026_MSG_CTF main | 955027d92d4c0e69202d9709f4a2ea5cf9e96e3d |

운영 구조는 [서버 아키텍처 설계 본문](https://app.notion.com/p/3e20f19c72be80d1a2ebd1c9e8f2a894)을 대조했다
본문에 예전 데이터 VM 1대 안과 PostgreSQL·Redis를 트랙별 4대로 나누는 수정안이 함께 남아 있어 수정안으로 표시했다
첨부된 4개 그림은 연결 도구에서 파일 참조만 받아 그림 내용까지 확인하지 못했다
이 보고서는 본문과 고정 SHA 소스 기준이며 그림까지 대조한 결과는 아니다
Runtime 기본 main은 738419f6045680b175fbc744f71108ad37d76439의 README뿐이므로 구현 확인은 별도로 확인한 dev 기준이다
열린 PR이나 배포된 image digest를 위 SHA와 같다고 간주하지 않는다

## 바로 맞춰야 할 연결

### 백엔드와 스케줄러 생성 요청

백엔드 apps/instances/services.py는 container_image와 container_port를 보낸다
스케줄러 InstanceApiDtos.kt는 containers, registry_revision, isolation_profile을 필수로 받는다
현재 두 기준본은 요청 계약이 맞지 않는다
백엔드 Registry 연동 PR과 실제 배포 버전을 확인한 뒤 같은 계약으로 로컬 E2E를 진행해야 한다

### 운영 healthz 프록시

프론트 nginx.conf.template은 /api/v1 경로만 백엔드로 전달한다
/healthz는 SPA fallback으로 HTML을 반환할 수 있다
앞 단계에서 msgctf.kr/healthz의 HTML 200도 확인했지만 이번에는 운영 서버 장애 시험을 하지 않았다
Edge 또는 Frontend에서 점검 경로를 정확한 백엔드 JSON 경로로 전달해야 한다
로컬 백엔드 healthz가 정상이라고 운영 프록시까지 검증된 것으로 표시하지 않는다

### 브로커 구현 기준본

Scheduler에는 후보 조회와 예약 생성·확정·반납 HTTP client가 있다
확인한 Broker dev는 README만 있어 상대 구현과 DB를 확인하지 못했다
구현이 있는 승인 브랜치, 실제 base URL과 서비스 인증 계약부터 정해야 한다

## 연결 검토에 기록한 역할 경계

- 공통 Edge와 내부·외부 Frontend
- 내부·외부 Backend와 각각의 PostgreSQL·Redis
- Scheduler DB, Broker DB와 Runtime operation store
- Scheduler에서 Broker 후보·예약을 받는 경로
- Backend 관리자 화면의 Broker resource-targets 목록 조회와 Cloud quota·credit·VM 상태 경계
- Scheduler에서 Runtime 생성·상태·삭제를 처리하는 경로
- Runtime에서 K3s workload와 격리 정책을 적용하는 경로
- KoTH 채점의 period별 점수 수집과 팀 토큰 검증
- info.yaml, CI, GHCR image, Actions bundle, Backend Registry 수집

KoTH 채점은 공통 역할로 분류했지만 내부·외부 팀·토큰·점수를 공유한다는 뜻은 아니다
반드시 트랙별 백엔드와 채점 설정으로 연결해야 한다
Windows VM 생성·복구를 K3s 컨테이너 생성과 같은 구현으로 취급하지 않는다

## 아직 필요한 운영 검증

| 항목 | 확인할 것 | 담당 |
|---|---|---|
| Active/Standby | leader lock, fencing, 이전 worker 정지, 승격 후 중복 작업 방지 | 스케줄러 · DevSecOps |
| 데이터 복원 | 내부·외부 개별 백업과 복원, 복구시간, 다른 트랙으로 우회하지 않기 | DevSecOps · 백엔드 |
| blue/green | migration 실패, smoke 실패, traffic 전환 실패, rollback | DevSecOps · 백엔드 · 프론트 |
| 문제 inventory | Scheduler 상태, Runtime operation, Pod Ready, 문제 healthcheck 대조 | 스케줄러 · Runtime · SLA |
| GHCR cold pull | digest 일치, private package 권한, pull secret 만료 | DevSecOps · Runtime |
| KoTH | 마지막 수집 성공, 중복 period, 채점 서버 재시작 후 기록 보존 | KoTH · 백엔드 · SLA |
| 장애 알림 | 알림 수신 확인, 중복 억제, 모니터 자체 장애 경고 | SLA |
| 자동 복구 | 수신팀의 인증·멱등성·재시도 상한·승인 조건 | SLA · 각 수신팀 |

팀 또는 인스턴스의 행 잠금만으로 Scheduler 프로세스의 leader 전환까지 검증했다고 보지 않는다
blue/green 앱 슬롯을 앱 VM 고가용성과 같은 것으로 보지 않는다
SLA는 장애를 판단하고 복구를 요청하되 실제 자산 복구 동작은 각 수신 역할의 계약을 따라야 한다
계약이 없는 곳에 사용자 reset이나 VM 재시작을 자동 호출하지 않는다

## 다음에 업데이트할 때

감시 주소와 응답 계약은 src/Sentinel.Api/profiles/local.json 또는 deployed.json을 수정한다
역할·연결·소스 SHA와 불일치 기록은 src/Sentinel.Api/wwwroot/architecture-model.js를 수정한다
두 설정은 독립적이며 새로고침 버튼이 GitHub 소스 검수를 자동으로 다시 수행하지 않는다

소스 기준을 바꾸면 양쪽 호출자·수신자를 다시 확인하고 source-versions.json도 갱신한다
실제 통신을 실행하지 않았다면 CODE나 DESIGN을 VERIFIED로 바꾸지 않는다
자산 상태는 실제 점검 응답에서 계산하고 연결 검토는 catalog에 그 점검 근거를 합쳐 표시한다
전체 구조 화면에는 설계 역할도 표시하지만 자산 상태 수치에는 실제 감시 대상만 포함한다
접힌 검수 항목이나 상세창으로 옮긴 근거를 삭제한 것은 아니다

검증 명령은 Sentinel 저장소 루트에서 실행한다

    node --test local/asset-model.test.cjs local/architecture-model.test.cjs
    python local/verify_real.py

이 문서는 로컬 구현과 연결 검수 기록이며 운영 배포 완료 보고서가 아니다
