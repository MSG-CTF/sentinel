# MSG CTF Sentinel

MSG CTF의 서비스·실행 노드·문제 인스턴스를 관측하는 SLA·운영 대시보드입니다
HTTP 응답, DB·Redis 연결, 자원 상태와 수집 시각을 함께 보여주고 장애·복구 이력을 남깁니다

[플랫폼 전체 소개](https://github.com/MSG-CTF/msg-ctf.github.io)

## 맡은 일

- 내부·외부 트랙의 백엔드와 공통 제어 서비스 상태 수집
- 스케줄러 전체 목록을 페이지 끝까지 읽어 인스턴스 상태 관측
- 브로커 등록 목록과 실제 K3s 노드·Windows 연결 상태 동기화
- 실행 중·정리 대기·오래된 관측값을 구분하는 대시보드
- Prometheus·Grafana와 장애·복구 이벤트, Discord 알림 연결
- AWS 배포, 제한된 관리 작업과 KOTH 관측 연결

## 함께 만든 사람들

| 기여자 | 기여 내용 | 관련 PR |
| --- | --- | --- |
| [minsoo0506](https://github.com/minsoo0506) | SLA 계약·.NET 기본 구조, health check worker와 Prometheus metrics | [#10](https://github.com/MSG-CTF/sentinel/pull/10) · [#13](https://github.com/MSG-CTF/sentinel/pull/13) · [#15](https://github.com/MSG-CTF/sentinel/pull/15) |
| [jongcoding](https://github.com/jongcoding) | 실제 서버·전체 인스턴스 감시, 운영 대시보드·AWS 배포·노드 수집 연결 | [#18](https://github.com/MSG-CTF/sentinel/pull/18) · [#35](https://github.com/MSG-CTF/sentinel/pull/35) · [#44](https://github.com/MSG-CTF/sentinel/pull/44) |

2026-11-09까지 병합된 PR과 커밋을 기준으로 정리했습니다

## 기술 구성

C# · .NET 8 · ASP.NET Core · Prometheus · Grafana · Docker · AWS

## 개발·운영 문서

- [로컬 실행과 검증](local/README.md)
- [AWS 배포](deploy/aws/README.md)
- [브로커 노드 감시](deploy/telemetry/BROKER-FLEET.md)
- [실행 준비 상태 관측](docs/runtime-readiness.md)
- [기존 개발·설정 참고 문서](README.reference.md)
