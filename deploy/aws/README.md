# AWS 배포

서울 리전의 EC2 t3.small 1대에서 Sentinel을 실행한다
루트 디스크는 암호화한 gp3 30GB이며 T3 CPU 크레딧은 standard로 설정한다
무료 플랜과 남은 크레딧을 확인한 뒤 생성하며 유료 플랜 전환은 수행하지 않는다
EC2, EBS, 퍼블릭 IPv4, S3 사용량은 무료 혜택과 크레딧 범위에 따라 차감된다
추가 과금 방지를 위한 플랜 확인과 별개로 크레딧 소진 시 서비스가 중단될 수 있다

## 접근과 권한

- 보안 그룹의 인바운드 규칙은 비어 있고 Sentinel은 호스트의 127.0.0.1:8080에만 노출한다
- 운영자는 AWS Systems Manager Session Manager로 접속한다
- GitHub는 OIDC로 해당 서비스의 S3 업로드와 SSM 배포 문서 실행 권한만 받는다
- GitHub에 AWS access key나 EC2 SSH 개인키를 저장하지 않는다
- EC2 역할은 SSM 연결과 배포 버킷의 releases/ 읽기에 사용한다
- S3에는 암호화한 비공개 배포 묶음을 저장하고 3일 후 만료시킨다
- 런타임은 읽기 전용 파일 시스템, 비관리자 사용자, 메모리 512MiB, CPU 0.5 제한으로 실행한다
- 이벤트 파일은 매시간 회전 조건을 확인하고 Docker 로그와 함께 보관 크기를 제한한다

현재 AWS 프로필은 observe_only 모드다
원격 자동 복구와 인스턴스 목록 조회는 별도 인증·계약 검증을 마친 뒤 연결해야 한다
화면 응답, API 상태, 데이터베이스 상태는 각 점검이 확인한 범위까지만 표시한다

## 최초 생성

AWS CloudShell에서 검토한 커밋의 stack.yaml과 bootstrap.py를 내려받은 뒤 실행한다
bootstrap.py는 활성 무료 플랜, 남은 크레딧, 기본 VPC의 퍼블릭 서브넷을 확인한다
기존 GitHub OIDC 제공자가 있으면 재사용하고 기존 스택은 덮어쓰지 않는다

```sh
python3 bootstrap.py stack.yaml
aws cloudformation describe-stacks --stack-name msg-sentinel --region ap-northeast-2
```

스택 생성 완료와 SSM 연결을 확인한 뒤 GitHub aws-production 환경을 설정한다
배포 가능한 브랜치는 dev와 최초 연결용 feat/real-platform-monitoring으로 제한한다
PR을 dev에 병합한 뒤에는 환경 규칙과 workflow에서 최초 연결용 브랜치를 제거할 수 있다

## GitHub 설정

저장소 변수 AWS_DEPLOY_ENABLED를 true로 설정하면 검증을 통과한 push가 자동 배포된다
PR 검증에서는 배포 자격 증명을 받지 않으며 AWS를 변경하지 않는다

aws-production 환경의 Secrets:

| 이름 | 값 |
| --- | --- |
| AWS_REGION | 스택 출력 AwsRegion |
| AWS_ROLE_ARN | 스택 출력 AwsRoleArn |
| AWS_INSTANCE_ID | 스택 출력 AwsInstanceId |
| AWS_ARTIFACT_BUCKET | 스택 출력 AwsArtifactBucket |
| AWS_DEPLOY_DOCUMENT | 스택 출력 AwsDeployDocument |
| SENTINEL_PROFILE_JSON | URL·IP·점검 종류를 포함한 AWS 프로필 JSON |
| SENTINEL_PROBE_SECRETS_JSON | 사용 중인 auth_token_env와 Bearer 토큰의 JSON 객체 |
| SENTINEL_ASSET_INVENTORY_JSON | 서버 IP와 미연결 자산을 포함한 운영 자산 목록 |
| BROKER_ADMIN_USERNAME | 브로커 콘솔 관리자 계정 |
| BROKER_ADMIN_PASSWORD | 브로커 콘솔 관리자 비밀번호 |

브로커 콘솔 관리자 비밀번호는 감시 API의 Bearer 토큰으로 사용하지 않는다
해당 관리자 Secrets는 현재 배포 컨테이너에 주입하지 않는다
인증이 필요한 점검을 활성화하려면 HTTPS 주소와 감시 전용 토큰을 함께 등록한다
프로필은 platform_monitor.profile=aws, event_file=data/events.jsonl,
platform_recovery.enabled=false, instance_monitor.enabled=false를 사용한다
공개 예시에는 실제 서버 IP와 인증값을 넣지 않는다

## 배포와 복구

1. .NET 테스트, 화면 모델 테스트, 배포 설정·복구 테스트를 실행한다
2. 커밋과 출처를 라벨로 기록한 이미지를 빌드한다
3. GitHub artifact에는 이미지와 배포 코드만 보관한다
4. 배포 job에서만 환경 Secrets를 읽어 비공개 S3 묶음을 생성한다
5. SSM에서 SHA-256과 파일 목록을 검사한 뒤 이미지를 적재한다
6. 별도 데이터 디렉터리를 쓰는 후보 컨테이너의 healthz와 첫 감시 결과를 확인한다
7. 후보가 통과하면 기존 컨테이너를 보존하고 새 버전으로 교체한다
8. 교체 후 검사에 실패하면 이전 이미지와 이전 설정으로 복구하고 다시 확인한다

후보 검사는 감시 대상 전체가 정상이라는 뜻이 아니다
Sentinel 프로세스, 관찰 모드, 프로필, 첫 수집, 이벤트 저장 상태를 확인한다
일부 자산이 미연결이거나 비정상이어도 감시 서비스 배포는 가능하다
배포 후에는 현재 버전과 직전 버전의 이미지·설정을 남기고 나머지를 정리한다

프로필이나 Secret만 변경하면 자동 실행되지 않으므로 해당 브랜치 workflow를 다시 실행한다
배포 실패 시 Actions 결과와 AWS Systems Manager 명령 결과를 함께 확인한다
실패 원인을 수정한 뒤 전체 workflow를 재실행하면 새 실행 시도 번호로 배포된다

## 대시보드 열기

AWS CLI 인증과 Session Manager 플러그인이 설정된 운영자 PC에서 실행한다

```sh
aws ssm start-session --region ap-northeast-2 --target <AWS_INSTANCE_ID> \
  --document-name AWS-StartPortForwardingSession \
  --parameters '{"portNumber":["8080"],"localPortNumber":["58241"]}'
```

터널을 유지한 상태에서 http://127.0.0.1:58241/ 에 접속한다
GitHub 배포 역할에는 운영자 세션 권한이 없으므로 별도의 운영자 로그인을 사용한다
SSH나 대시보드 포트를 전체 인터넷에 개방할 필요는 없다

## 제거 시 주의

CloudFormation 스택 삭제는 EC2와 루트 디스크의 이벤트 이력을 삭제한다
필요한 기록은 먼저 보관한다
S3 버킷은 Retain으로 남고 배포 묶음은 3일 만료 규칙에 따라 정리된다
스택을 없애면 AWS_DEPLOY_ENABLED를 false로 바꾸고 더 이상 쓰지 않는 환경 Secrets를 정리한다
