2026년 10월 13일 12:37 KST 기준으로 MSG-CTF 저장소 10개의 원격 브랜치 328개와 열린 PR 21개를 수집했다
브랜치 이름·HEAD와 PR 상태·대상 브랜치·변경 파일·리뷰·검사 기록을 확인하고 SLA 관련 제공팀 계약을 대조했다
328개 브랜치의 모든 코드를 개별 검수하거나 열린 PR 전체를 실행한 결과는 아니다

| 저장소 | 기본 브랜치 HEAD | 브랜치 수 | 열린 PR |
| --- | --- | --- | --- |
| 2026_MSG_CTF | main / 3a961c945e54 | 69 | [79](https://github.com/MSG-CTF/2026_MSG_CTF/pull/79) |
| ctf-mock | dev / aa27492d7d6c | 6 | 없음 |
| front-team | main / ab02c87916d4 | 75 | [89](https://github.com/MSG-CTF/front-team/pull/89) |
| instance-scheduler | dev / 5c695142871a | 41 | [62](https://github.com/MSG-CTF/instance-scheduler/pull/62), [61](https://github.com/MSG-CTF/instance-scheduler/pull/61), [60](https://github.com/MSG-CTF/instance-scheduler/pull/60), [53](https://github.com/MSG-CTF/instance-scheduler/pull/53) |
| jm_devsecops | jm / 3120336d1654 | 8 | [5](https://github.com/MSG-CTF/jm_devsecops/pull/5) |
| msg-backend | main / 08be84d46f49 | 75 | [102](https://github.com/MSG-CTF/msg-backend/pull/102), [100](https://github.com/MSG-CTF/msg-backend/pull/100), [26](https://github.com/MSG-CTF/msg-backend/pull/26), [101](https://github.com/MSG-CTF/msg-backend/pull/101), [80](https://github.com/MSG-CTF/msg-backend/pull/80) |
| msgctf-devsecops | main / 40513968fa9e | 16 | [17](https://github.com/MSG-CTF/msgctf-devsecops/pull/17), [19](https://github.com/MSG-CTF/msgctf-devsecops/pull/19) |
| resource-broker | dev / bde6361dd364 | 6 | [4](https://github.com/MSG-CTF/resource-broker/pull/4) |
| secure-provisioner | main / 738419f60456 | 23 | [46](https://github.com/MSG-CTF/secure-provisioner/pull/46), [45](https://github.com/MSG-CTF/secure-provisioner/pull/45), [42](https://github.com/MSG-CTF/secure-provisioner/pull/42) |
| sentinel | dev / e17e94dd394f | 9 | [17](https://github.com/MSG-CTF/sentinel/pull/17), [16](https://github.com/MSG-CTF/sentinel/pull/16), [15](https://github.com/MSG-CTF/sentinel/pull/15) |

로컬 점검 원본은 워크스페이스 review-work/sla-refresh-20261013에 저장했다
repository-inventory에는 전체 브랜치의 SHA와 열린 PR의 SHA, 각 저장소 snapshot에는 수집한 PR 세부 기록을 보관한다
코드 실행에 사용한 제공팀 버전은 [소스 기록](source-versions.json)에 있으며 이번 원격 기본 브랜치 목록과 구분한다

Backend main에는 비로그인 게임 데이터 조회 차단 등 PR 103·98·95가 반영됐고 Frontend main에도 PR 93~109의 변경이 반영됐다
실행 사양 전달과 비밀값 연결은 Backend 26·101, Scheduler 60, Runtime 45·46, DevSecOps 19 등 열린 PR에 걸쳐 있다
각 PR이 열려 있다는 사실만으로 문제 생성 경로 전체가 연결됐다고 판정하지 않는다

Scheduler dev는 5c695142871a, PR 61은 27828ba5144c, PR 62는 f4a4de1a1ed7로 이전 검수와 동일하다
PR 61의 생성 롤백 실패 후 강제 정리에서 남은 자원과 예약 해제의 불일치 우려도 같은 HEAD에 남아 있다
이번에 그 실패 재현을 다시 실행한 것은 아니며 기존 검수와 동일한 소스라는 근거만 갱신했다
인스턴스 감시는 이 강제 정리 API를 호출하지 않는다

Sentinel PR 15는 기존 mock 기반 metrics, PR 16은 SLA 하네스이며 PR 17은 실제 자산 감시와 로컬 복구 작업이다
세 PR 모두 dev에 병합되지 않았고 PR 15와 17은 시작 코드와 metrics 경로가 겹친다
동시 병합 전에 실제 서버 감시 등록과 metrics 제공 방식을 맞춰야 한다
GitHub 검사 항목이 없다는 사실은 테스트 통과 근거가 아니며 이번 검증은 로컬 실행 기록으로 남겼다

**노션과 실제 계약 대조**

[아키텍처 설명](https://app.notion.com/p/39a0f19c72be8095b10cea704c949870)은 Django·Runtime·Broker 관측과 Scheduler를 통한 복구 신호, 관리자 알림을 요구한다
문제 인스턴스 Health Worker, Timeout 및 Resource Risk, Scheduler Signal 및 Admin Alert 작업은 작업 DB에서 진행 중이다
완료 상태인 metrics·Django 작업도 상태표만으로 실제 대회 통합 완료를 인정하지 않았다

[API 계약 임시 문서](https://app.notion.com/p/39a0f19c72be80158812e15bcbf6e070)는 7월 29일의 ctf-mock 계약이다
그 문서의 전체 목록·health·scheduler signals 경로와 camelCase 필드를 현재 Scheduler 계약으로 사용하지 않는다
[Runtime → SLA 기록](https://app.notion.com/p/3bb0f19c72be8158b1bee7282179c71c)은 8월 26일 기준 미검증으로 남아 있다
10월 5일 임원진 회의와 9월 28일 회의에서도 이를 대체할 새 SLA API 계약은 확인되지 않았다
15주차 회의 페이지는 내용이 비어 있으며 SLA 메인 페이지의 외부 저장소 객체 2개는 도구가 본문을 제공하지 않아 확인 범위에서 제외했다

[SLA 양식 설명](https://app.notion.com/p/3ac0f19c72be804da4ddd2bd9b6f1071)은 KoTH·Live Fire 문제의 핵심 기능 점검을 요구한다
컨테이너 준비나 HTTP 200만으로 문제 기능이 정상이라고 판정하지 않으며 checker 자체 오류도 문제 장애와 구분해야 한다
최초 구현은 Runtime 준비 상태까지 확인했으며 후속 검증에서 등록된 로컬 계산 시험 서비스의 기능 점검을 추가했다
실제 대회 문제 기능 결과와 트랙은 미확인으로 유지한다

**이번에 진행한 구현과 검증 범위**

[Scheduler 조회 코드](https://github.com/MSG-CTF/instance-scheduler/blob/5c695142871a5a926b96d7befcaf0832c691933f/src/main/kotlin/kr/msgctf/scheduler/instance/controller/InstanceQueryController.kt)의 최근 최대 20개 목록을 읽고, 명시적으로 등록된 Runtime target에 인스턴스 ID로 조회한다
[Runtime 상태 코드](https://github.com/MSG-CTF/secure-provisioner/blob/19d2fdffef753bb9415861c4a6b382b6408455ea/internal/k3s/status.go)의 준비·삭제 상태를 Scheduler의 사용 기한과 비교해 GET /api/instances와 대시보드에 제공한다
매핑 불일치, 오래된 관측값, 기한 초과 후 잔존 자원, 종료 상태와 실제 자원의 불일치는 관리자 확인 대상으로 표시한다
상태 비교·실패 처리의 로컬 테스트는 E2, 실제 Scheduler의 빈 최근 목록 조회는 해당 조회 경계에 한정한 E3로 기록한다
시험용 Scheduler 행과 실제 Runtime 연결 시험은 전체 Scheduler 생성 흐름의 E3 근거로 승격하지 않는다

이전 실습의 16개 정상 자산과 앱 8개의 제한된 복구 연결은 재기동 후 확인했다
이전 백업·복원·서비스 전환의 장애 시험을 이번에 재실행한 것은 아니며 이전 날짜의 검증 기록을 그대로 구분한다
검증 수, 실행 시각, 소스 해시와 남은 범위는 [10월 13일 검증 요약](verification/2026-10-13.json)에 기록한다

**후속 실제 생성·기능·정리 검증**

실제 Scheduler 생성 요청으로 인스턴스를 등록해 Broker COMMITTED와 Runtime READY까지 연결했다
로컬 계산 서비스에 새 nonce와 입력값을 보내 오답은 DOWN, 해석 불가능한 응답은 CHECKER_ERROR, 정상 복귀는 UP으로 구분했다
이때 Runtime READY가 유지되는 경우에도 기능 실패를 따로 표시했다
서비스 토큰을 기능 요청에 전달하지 않고 등록된 로컬 주소만 조회하며 오래된 기능 결과도 STALE로 처리한다

사용자 삭제와 1분 TTL 만료 모두 Scheduler CLEANED, Runtime TERMINATED, Broker RELEASED, 실제 네임스페이스 부재와 Sentinel REMOVED를 확인했다
기존 Nginx 연결 시험은 정상 상태를 유지했다
별도 시험 Pod 삭제 후 K3s Deployment가 다른 UID의 Pod를 만들고 기능 UP으로 복귀하는 것도 확인했다
복구 주체는 K3s 컨트롤러이며 Sentinel이 복구 요청을 보낸 결과가 아니다
이 짧은 Pod 교체 구간은 Sentinel DOWN으로 관측되지 않았으므로 장애 탐지 지연이나 무중단 서비스 근거로 계산하지 않는다

이 실행은 로컬 서비스 간 실제 HTTP·Kubernetes 흐름에 한정한 E3 근거다
Broker 자산 등록과 용량은 명시적인 로컬 fixture이며 실제 클라우드 계정·quota·노드 에이전트 관측은 검증하지 않았다
시험 이미지는 로컬에서 빌드해 containerd에 가져왔고 Backend·Registry 릴리스·GHCR 배포 경로는 포함하지 않았다
Scheduler dev에는 healthcheck 요청 전달 필드가 있으나 연결한 Runtime과 조회 응답에는 기능 결과 계약이 없어 Sentinel의 로컬 명시적 바인딩으로 점검한다

후속 실행 근거와 현재 시험 인스턴스의 종료 시각은 [기능 점검 검증 요약](verification/2026-10-13-functional.json)에 기록한다
기존 검증 파일은 최초 실행 시점의 증거로 보존한다

**다음 연동 순서**

- Scheduler 전체 목록이나 페이지 조회 계약과 트랙 매핑을 확정
- Backend·Registry 릴리스를 거친 실제 대회 문제 생성 경로와 기능 명세 연결
- 문제별 기능 점검 결과와 CHECKER_ERROR 전달 계약을 연결
- 인증·멱등성·시도 횟수·만료·소유 범위·접수와 결과가 있는 전용 복구 수신 계약을 연결
- 관리자 알림과 복구 실패 후 수동 대응, 운영 HA 전환 검증

남은 계약 없이 참가자 reset이나 강제 정리를 자동 복구로 사용하지 않는다
Windows·KoTH·실제 Cloud API와 Runtime Store의 백업 복원은 기존과 같이 미연결 또는 미검증 범위로 둔다
