# Sentinel

MSG CTF 실제 서버의 응답을 확인하고 로컬 실습 자산의 제한된 복구를 수행하는 SLA 서비스

기본 local·deployed 프로필은 감시만 수행한다
local-recovery 프로필은 Windows 호스트 실행기를 통해 등록한 앱의 정지 컨테이너만 기동한다
감시 서비스는 인스턴스 생성·삭제를 자동 호출하지 않으며 로컬 검증 도구에서만 시험 인스턴스를 생성하고 정리한다

## 현재 동작

- 실제 msg-backend의 healthz 계약으로 HTTP 상태와 DB·Redis 상태를 함께 확인
- 내부·외부 대회의 상태를 따로 기록
- HTML 200, 인증 실패, 잘못된 JSON은 정상으로 처리하지 않음
- 제한 시간 안에 응답이 없으면 TIMEOUT으로 기록
- 연속 실패와 연속 정상 기준으로 장애·복구 기록 생성
- 재시작 후에도 이벤트 기록을 읽어 같은 상태를 중복 기록하지 않음
- 오래된 점검 결과와 연결하지 않은 역할은 정상 판정에서 제외
- 대시보드와 Prometheus 형식의 metrics 제공
- Scheduler·Broker의 인증된 조회와 DB 연결, 선택한 로컬 Runtime·PostgreSQL store·K3s 상태 확인
- Scheduler 최근 20개 인스턴스와 Runtime 상태를 연결해 식별자·관측 시각·기한·종료 후 잔존 자원 확인
- 등록된 로컬 시험 인스턴스에 계산 요청을 보내 기능 정상·장애·점검 오류를 구분
- 로컬 앱 최대 8개의 정지 복구와 인증·멱등성·장애당 1회·300초 대기 조건 적용
- PostgreSQL 4개·Redis 2개의 격리 복원과 실제 서비스 전환·원래 볼륨 복귀 시험

## 로컬 실행

[실행 방법과 배포 서버 전환](local/README.md)

[AWS 배포와 GitHub 자동 배포 설정](deploy/aws/README.md)

Docker Compose로 실제 내부·외부 Backend·Frontend와 트랙별 PostgreSQL·Redis, Scheduler·Broker를 분리해 실행한다
선택 절차로 전용 K3s와 Runtime을 연결하며 기본 감시에 Django mock을 사용하지 않는다
상세 실행 절차와 필요한 형제 저장소는 로컬 실행 문서에 기록했다

## API

| 경로 | 의미 |
|---|---|
| GET /healthz | Sentinel 자체 실행 상태, 다른 서버가 정상이라는 뜻은 아님 |
| GET /api/status | 실제 점검 결과와 연결 대기 대상 |
| GET /api/instances | 최근 최대 20개 인스턴스의 Runtime 관측과 등록된 로컬 시험 서비스 기능 결과 |
| GET /api/events | 장애·복구 이벤트 |
| GET /api/recovery | 인증값을 제외한 로컬 복구 요청과 실행기 상태 |
| GET /metrics | Prometheus 형식의 상태·지연·점검 시각 |
| GET / | 상태 대시보드 |

호스트 실행기는 인증된 /internal/recovery 경로로 점검·요청 인수·결과 전달을 수행한다
요청 필드와 완료 조건은 [복구 계약](local/recovery-contract.json)에 기록했다

## 다음 연동

- Scheduler 전체 인스턴스 목록과 실제 대회 문제 생성 경로
- Broker의 실제 계정·quota·budget 상태 조회
- 실제 문제별 healthcheck와 Runtime Store의 백업·복원
- KoTH·Windows 서버의 실제 상태 조회
- 운영 자산의 관리 주소·자격 증명·복구 수신 API 연결
- 관리자 알림과 Active/Standby 전환 검증

이 항목은 실행 완료로 표시하지 않는다
제공팀의 실제 주소·인증·응답 계약을 확인한 뒤 연결한다
사용자의 인스턴스 reset을 운영 서버 복구 수단으로 자동 호출하지 않는다

## 기존 mock 코드

기존 Django mock client와 테스트는 비교용으로 보존했다
현재 API 시작 코드에는 mock client와 mock monitor를 등록하지 않는다
mock 테스트 통과를 실제 서비스 간 연동 증거로 사용하지 않는다

## 기술 스택과 보안

C#, .NET 8, ASP.NET Core, BackgroundService, HttpClientFactory, xUnit, Docker Compose

자산 점검은 GET만 사용하고 리다이렉트와 쿠키 전달을 차단한다
응답 크기·요청 시간·동시 요청 수를 제한한다
응답 원문, flag, password, 인증 token은 상태 API와 이벤트 기록에 넣지 않는다
대시보드 포트는 로컬 주소에만 공개한다
백업·kubeconfig·서비스 토큰·실행 journal과 내려받은 도구는 Git과 이미지 빌드에서 제외한다

[확인한 소스 버전](local/source-versions.json)

배포 서버의 release SHA와 image digest는 아직 확인되지 않았다
로컬은 기록한 GitHub 소스로 실행하며 배포 이미지와 같다고 주장하지 않는다

## 검증 근거

[2026년 10월 13일 인스턴스 감시 검증](local/verification/2026-10-13.json)과 [최신 저장소·노션 대조](local/sla-review-20261013.md)에 이번 변경의 근거와 남은 계약을 기록했다

후속 [실제 로컬 생성·기능·정리 검증](local/verification/2026-10-13-functional.json)은 Scheduler 생성부터 Broker 예약, Runtime 실행, 기능 장애와 복구 관측, 삭제·TTL 정리까지의 별도 실행 기록이다

[2026년 10월 11일 공개 검증 요약](local/verification/2026-10-11.json)과 [세부 검수 기록](local/architecture-audit.md)에 범위와 제한을 기록했다
세부 문서의 artifacts 경로는 Git에서 제외한 로컬 실행 결과이며 공유 가능한 요약만 버전 관리한다
Runtime에는 Nginx 연결 시험과 Scheduler가 생성한 계산 시험 인스턴스를 사용하며 실제 대회 문제나 운영 고가용성 검증을 뜻하지 않는다
