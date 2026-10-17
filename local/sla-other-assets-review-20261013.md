# 추가 자산 점검 결과

2026년 10월 13일 KST 기준 관련 저장소 5개와 원격 브랜치 189개, 열린 PR 12개, 노션 문서 5개를 대조했다
브랜치는 목록과 커밋을 확인했고 상세 소스 검토는 KoTH·발행·Registry 계약에 집중했다
로컬 앱 8개의 실제 중단·자동 복구와 Runtime Store의 격리 백업·복원은 통과했다
점검 종료 시 직접 관측 12개와 Backend 구성 요소 4개가 정상이었고 9개 역할은 미연결이었다

## KoTH 연결 전 수정할 시간 계약

현재 Backend 명령을 옵션 없이 15분 경계에 실행하면 card-battle 서비스의 점수를 수집하지 못할 수 있다
Backend는 현재 진행 중인 구간을 기본 period로 고르고, card-battle은 그 구간이 끝나기 전까지 PeriodNotReady를 반환한다
기본 재시도는 시작 후 0·60·180·420초에 이뤄지는데 구간은 900초가 지나야 끝난다

예를 들어 12시 00분에 기본 명령을 실행하면 12시 00분 구간을 요청하고 마지막 재시도도 12시 07분에 끝난다
해당 구간은 12시 15분부터 조회할 수 있으며 다음 기본 실행은 새로 시작한 12시 15분 구간을 고른다
따라서 외부 스케줄러가 직전 완료 구간을 명시하거나 기본 period 선택을 완료 구간으로 바꿔야 한다
이미 완료 구간을 명시하는 호출자에는 이 지적이 적용되지 않는다

확인한 두 커밋에서 시간 관련 함수만 추출하고 저장소·설정 의존성을 대체해 네 개의 15분 경계를 검증했다
총 16회 모두 결과가 아직 준비되지 않은 것으로 반환됐다
이는 E2 구성 요소 계약 근거이며 실제 KoTH 서비스·DB·주기 실행을 연결한 결과는 아니다

- [Backend 기본 period와 재시도 명령](https://github.com/MSG-CTF/msg-backend/blob/08be84d46f49c6f51947a871cb0003c49ed531ed/apps/koth/management/commands/poll_koth_scores.py#L31)
- [현재 구간을 고르는 함수](https://github.com/MSG-CTF/msg-backend/blob/08be84d46f49c6f51947a871cb0003c49ed531ed/apps/koth/services.py#L256)
- [card-battle의 구간 완료 조건](https://github.com/MSG-CTF/2026_MSG_CTF/blob/3a961c945e544c9a50fd7938868d67f07fe78828/koth-card-battle-arena/prob/for_organizer/service/scoring.py#L105)

## 자산별 판정

| 자산 | 이번에 확인한 근거 | 로컬 연결 전에 남은 일 |
|---|---|---|
| KoTH 서비스 | 2026_MSG_CTF에 실제 서비스가 있으며 card-battle의 healthz와 점수 API 구현을 확인 | 전용 실행 대상·관리 주소·재시작 후 경기 상태 보존 검증 |
| KoTH 채점 | Backend 수집 명령·동일 period 재시도와 서버의 완료 구간 계산 코드를 확인 | 완료 period 기준을 맞추고 URL·방향별 토큰·문제 ID·주기 실행·마지막 성공 구간을 연결 |
| Windows 문제 VM | 현재 연결한 Runtime의 생성 요청은 KUBERNETES만 허용 | 실제 Windows VM 이미지·관리 연결·전용 서비스 복구 계약 |
| Cloud API | Broker에 실습용 수동 자원 한 행을 등록한 상태 | 실제 provider 자산 조회·관측 시각·권한·VM 매핑 |
| 문제 사양·CI | 최신 main의 validate는 성공했지만 pwn-random6 발행의 Trivy 검사 단계 실패 | 실패 원인을 확인하고 같은 revision의 발행 결과 재검증 |
| GHCR·Bundle | 별도 승인 WEB 실행 점검은 성공했지만 위 발행 run에는 publish-bundle이 없음 | 실행할 동일 digest의 실제 pull과 정식 bundle 발행·수집 확인 |
| Challenge Registry | 최신 Backend main의 tree와 URL 구성에도 새 release 수신 경로가 없으며 PR 26은 열림 | 수신 구현 반영 후 poller 수집·중복 처리·활성 revision 전환 검증 |

문제 저장소 main 3a961c945e54의 [발행 run 38238229618](https://github.com/MSG-CTF/2026_MSG_CTF/actions/runs/38238229618)은 Trivy 검사 단계에서 실패했다
뒤의 k3s-smoke-deploy와 aggregate는 건너뛰었고 조회한 산출물 4개에는 publish-bundle이 없다
같은 커밋의 [승인 WEB 실행 run 38346073355](https://github.com/MSG-CTF/2026_MSG_CTF/actions/runs/38346073355)은 성공했지만 다른 문제·다른 workflow의 결과이므로 전체 발행 성공으로 합치지 않았다
Trivy 단계의 구체적인 실패 원인은 이번 점검에서 확정하지 않았다

[DevSecOps 통합 문서](https://github.com/MSG-CTF/msgctf-devsecops/blob/40513968fa9e4a8a624dc7c11a6e0a399aada323/docs/challenge-registry-integration.md)의 이전 증거는 재생성한 후보 bundle을 PR 26의 로컬 API로 등록한 결과다
공식 poller 수집이나 실제 K3s의 GHCR cold pull 성공으로 확장할 수 없다

8월의 [KoTH 검토 문서](https://app.notion.com/p/3bb0f19c72be813a9d06c6a8dc535c6b)는 최신 서비스 구현을 반영하지 못한다
[10월 5일 KoTH QA](https://app.notion.com/p/3ec0f19c72be81a0a73cc24dc5b46747)와 최신 소스를 우선 대조했다
기존 Backend 템플릿의 빈 checker만 보고 KoTH 전체 구현이 없다고 쓰던 로컬 설명을 수정했다

## 실제 복구 검증

| 점검 | 결과 | 확인 범위 |
|---|---|---|
| 앱 자동 복구 | 8개 모두 성공 | 내부·외부 Backend, Scheduler, Broker, 내부·외부 Frontend, Edge, Runtime을 차례로 중단 후 자동 기동 |
| 복구 경계 | 통과 | 같은 컨테이너 ID, 장애당 새 요청 하나, 다른 앱 불변, 두 트랙의 프록시 응답 일치 |
| 원본 데이터 컨테이너 | 7개 불변 | 앱 복구 시험 중 PostgreSQL·Redis 컨테이너 ID와 시작 시각 유지 |
| Runtime Store 복원 | 9개 확인 통과 | 테이블 2개·10행, 완료 작업 6건의 실제 Runtime API 응답 일치 |
| 복원 후 재기동 | 통과 | 복원한 DB·Runtime 재기동 뒤 데이터와 작업 응답 유지 |
| 복원 시험 정리 | 통과 | 원본 컨테이너·볼륨·데이터 유지, 시험 컨테이너·볼륨·네트워크 제거 |
| 현재 자산 점검 | 20개 확인 통과 | 직접 12개·간접 4개 정상, 9개 미연결, 복구 실행기 연결 |
| 화면 | 모델 테스트 59개·브라우저 확인 14개 통과 | 25개 역할, KoTH 연결 대기 설명, 오프라인 처리, 두 트랙 React 화면, 모바일 폭 |

Runtime Store 복원 시험은 45.8초 걸렸으며 이는 격리 실습의 실행 시간이다
운영 RTO, 진행 중 작업 인계, 실제 K3s 재연결, 원본 서비스 전환을 입증하는 수치가 아니다
완료되지 않은 작업이 있으면 이 시험을 시작하지 않으며 실제 kubeconfig·클러스터 인증값도 시험 Runtime에 전달하지 않는다

현재 계산 시험 인스턴스 1개는 READY·기능 UP이고 삭제한 2개는 REMOVED다
실제 대회 문제와 Windows·KoTH·Cloud 가용성은 별도 검증이 필요하다

## 반영한 변경

기존 점검 도구의 인스턴스 0개·자동 복구 앱 7개 가정을 제거하고 현재 연결한 Runtime과 명시적 로컬 Broker 자원을 반영했다
브라우저 점검도 실행 자산 연결 여부에 따라 정상·미연결 수를 계산한다
화면에는 미연결 자산의 점검 계획을 표시하고, KoTH 구현 근거·발행 실패·Registry 대기·이미 검증한 로컬 생성과 복구 범위를 갱신했다

새 Runtime Store 검증 명령은 local/verify_runtime_store.py다
백업·manifest·인증정보는 비공개 위치에 남기며 공개 요약에는 실행 범위·개수·결과만 담았다
이번 검토는 다른 팀의 소스 변경, GitHub 게시, 노션 편집을 포함하지 않는다

재확인 결과와 파일 해시는 [추가 자산 검증 기록](verification/2026-10-13-other-assets.json)에 저장했다
