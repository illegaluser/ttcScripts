# Zero-Touch QA All-in-One (폐쇄망 단일 이미지)

`docker load -i *.tar.gz` → `docker run` **한 번**으로 Jenkins + Dify + Ollama +
PostgreSQL + Redis + Qdrant + Playwright 모두를 기동하는 오프라인 번들.

이 디렉토리는 _온라인 빌드 머신_ 에서 번들을 만들기 위한 도구 일체다.
**폐쇄망 타겟은 이 디렉토리가 필요 없으며**, 빌드 결과물(tar.gz) 만 있으면 된다.

---

## 개요

### 동기
기존 [`setup.sh`](../setup.sh) 는 13개 컨테이너(Jenkins + Dify 생태계) 를 docker-compose
로 기동하고, 실행 중 외부 네트워크 5곳(Docker Hub / PyPI / Ollama registry /
marketplace.dify.ai / updates.jenkins.io) 에 접근한다. 폐쇄망에서는 이 접근이
모두 막혀 있으므로 설치가 불가능하다.

### 해결
**단일 All-in-One Docker 이미지** 에 다음을 모두 사전 내장:

- Jenkins (컨트롤러 + 내부 에이전트), JDK 21, Python 3.11, Playwright + Chromium
- Dify 1.13.3 의 api / worker / worker_beat / web / plugin_daemon
- PostgreSQL 15 (initdb + Dify DB 사전 생성), Redis 6, Qdrant 1.8.3
- Ollama + gemma4:e4b 모델 (~4GB)
- Jenkins 플러그인 hpi (workflow-aggregator + 의존성 40+개, plain-credentials, file-parameters, htmlpublisher)
- Dify 플러그인 `.difypkg` (langgenius/ollama)
- nginx (localhost upstream 버전)
- supervisord (PID 1, 11개 프로세스 관리)

상태는 **단일 볼륨 `/data`** 에 지속 저장. 재시작/재기동 시 모든 설정과 데이터 유지.

### 트레이드오프
| 항목 | 결과 |
|------|------|
| 이미지 크기 | 9-12GB (압축 전), 5-9GB (gzip 압축) |
| `docker run` 횟수 | 1회 |
| 첫 기동 시간 | 5-10분 (seed + 앱 프로비저닝) |
| 이후 기동 시간 | 30-60초 |
| 외부 포트 | 3개 (18080 Jenkins / 18081 Dify / 50001 JNLP) |
| 호스트 RAM 권장 | 16GB+ |

---

## 1. 온라인 빌드 머신에서 번들 제작

### 요구사항
- Docker 26+ (buildx 플러그인 활성)
- 디스크 여유 **40GB+**
- JDK 11+ (Jenkins 플러그인 매니저 실행용)
- 네트워크: Docker Hub, github.com, marketplace.dify.ai, updates.jenkins.io, PyPI
- 빌드 시간: **30-90분** (네트워크 속도 의존)

### 빌드 실행
```bash
cd e2e-pipeline
./offline/build-allinone.sh
```

### 환경변수 (선택)
```bash
# 기본값:
#   IMAGE_TAG=dscore-qa:allinone
#   TARGET_PLATFORM=linux/amd64   (폐쇄망 타겟이 arm64 라면 linux/arm64 로 덮어쓰기)
#   OLLAMA_MODEL=gemma4:e4b
#   JENKINS_VERSION=2.462.3
#   OUTPUT_TAR=dscore-qa-allinone-<timestamp>.tar.gz

TARGET_PLATFORM=linux/arm64 ./offline/build-allinone.sh
```

### 빌드 단계
1. **Jenkins 플러그인 hpi 다운로드** — `jenkins-plugin-manager.jar` 로 재귀 의존성 해결 (offline/jenkins-plugins/)
2. **Dify 플러그인 `.difypkg` 다운로드** — marketplace.dify.ai 에서 최신 langgenius/ollama (offline/dify-plugins/)
3. **Docker buildx build** — Dockerfile.allinone 으로 3-stage 멀티 빌드
   - Stage 1: ollama/ollama 에서 gemma4:e4b pull → /opt/seed/ollama 로 복사
   - Stage 2: dify-api / dify-web / dify-plugin-daemon 이미지에서 애플리케이션 파일 추출
   - Stage 3: jenkins/jenkins:lts-jdk21 기반으로 OS 패키지 + Python + nginx + PG + 모든 컴포넌트 통합
4. **docker save + gzip** — 최종 `dscore-qa-allinone-<timestamp>.tar.gz` 산출

### 산출물
- `dscore-qa-allinone-<timestamp>.tar.gz` — 폐쇄망으로 이동할 파일
- `offline/jenkins-plugins/*.hpi` — 빌드 중간 산출물 (삭제 가능)
- `offline/dify-plugins/*.difypkg` — 빌드 중간 산출물 (삭제 가능)

### 빌드 검증
```bash
docker images dscore-qa:allinone
# 예상: dscore-qa:allinone  <hash>  <date>  9.5GB

ls -lh dscore-qa-allinone-*.tar.gz
# 예상: -rw-r--r--  1 user  staff  7.2G dscore-qa-allinone-20260419-143000.tar.gz
```

---

## 2. 폐쇄망 타겟에서 기동

### 요구사항
- Docker 26+
- 디스크 여유 **30GB+**
- 메모리 **16GB+** 권장 (Ollama 추론 + Dify + Jenkins 동시 가동)
- CPU 4코어+

### 파일 전송
USB 또는 파일 전송 방식으로 타겟에 복사:
```
/home/user/dscore-qa-allinone-20260419-143000.tar.gz
```

### 이미지 로드
```bash
cd /home/user
docker load -i dscore-qa-allinone-20260419-143000.tar.gz
# Loaded image: dscore-qa:allinone

docker images dscore-qa:allinone
```

### 기동
```bash
docker run -d --name dscore-qa \
  -p 18080:18080 \
  -p 18081:18081 \
  -p 50001:50001 \
  -v dscore-data:/data \
  --add-host host.docker.internal:host-gateway \
  --restart unless-stopped \
  dscore-qa:allinone
```

**포트 설명**:
- `18080` — Jenkins 웹 UI
- `18081` — Dify 웹 UI (nginx reverse proxy)
- `50001` — Jenkins JNLP (내부 에이전트 loopback 용, 외부 노출 선택)

**볼륨 설명**:
- `dscore-data` → `/data` — 모든 상태 (PG, Redis, Qdrant, Ollama 모델, Jenkins home, Dify storage)

### 기동 로그 확인
```bash
docker logs -f dscore-qa
```

초기 5-10분간 다음 단계가 순차 실행된다:
1. `seed`: `/opt/seed/*` → `/data/*` 복사 (Ollama 모델, Jenkins hpi, PG 스냅샷, Dify 플러그인)
2. `supervisord` 기동 (postgresql → redis → qdrant → ollama → plugin_daemon → dify-api → worker → web → nginx → jenkins)
3. 각 서비스 헬스체크
4. `provision-apps.sh` 실행 (오프라인 전용 독립 스크립트 — 기존 setup.sh 미사용):
   - Dify 관리자 계정 생성 (`admin@example.com` / `Admin1234!`)
   - Dify Ollama 플러그인 설치 (로컬 `.difypkg` 업로드)
   - Dify 모델 공급자 등록 (`gemma4:e4b` @ `http://127.0.0.1:11434`)
   - Dify Chatflow import (`dify-chatflow.yaml`) + Publish + API Key 발급
   - Jenkins Credentials 등록 (`dify-qa-api-token`)
   - Jenkins Pipeline Job 생성 (`DSCORE-ZeroTouch-QA-Docker`)
   - Jenkins Node 등록 (`mac-ui-tester`, 내부 loopback JNLP)

완료 표시:
```
[entrypoint-allinone] 앱 프로비저닝 완료.
[entrypoint-allinone] jenkins-agent 기동 요청 완료.
[entrypoint-allinone] 준비 완료. supervisord wait...
```

---

## 3. 접속 및 확인

### Jenkins
```
URL:      http://<host>:18080
계정:     admin / password
```

- Manage Jenkins → Plugins → Installed: `workflow-aggregator`, `plain-credentials`, `file-parameters`, `htmlpublisher` 활성
- Manage Jenkins → Credentials → System → Global: `dify-qa-api-token` 등록 확인
- Dashboard → `DSCORE-ZeroTouch-QA-Docker` Pipeline Job 존재
- Manage Jenkins → Nodes → `mac-ui-tester` Online

### Dify
```
URL:      http://<host>:18081
계정:     admin@example.com / Admin1234!
```

- Apps → DSCORE-ZeroTouch-QA Chatflow 존재 (Published)
- Settings → Model Provider → Ollama → `gemma4:e4b` 등록됨 (Base URL: `http://127.0.0.1:11434`)

### 동작 검증
Jenkins Pipeline 빌드 실행:
1. `DSCORE-ZeroTouch-QA-Docker` → Build with Parameters
2. `SCENARIO_TEXT`: "Google.com 에 접속해 'Claude AI' 를 검색한다"
3. Build 시작
4. Console Output 에서 순차 실행:
   - Dify API 호출 (Planner LLM → DSL JSON)
   - Playwright headless 실행 (Chromium)
   - 스크린샷 + HTML 리포트 생성
5. Build 페이지 → `artifacts/index.html` 링크 클릭 → 시각적 리포트 확인

---

## 4. 운영

### 재시작 (상태 유지)
```bash
docker restart dscore-qa
```
- 약 30-60초 후 서비스 재가동
- Dify App, Jenkins Job, Credentials, Pipeline 실행 이력, 모델 등록 모두 유지

### 중지
```bash
docker stop dscore-qa
```

### 로그 확인
각 서비스별 로그는 `/data/logs/` 에 분리 저장:
```bash
# 특정 서비스 로그
docker exec dscore-qa tail -f /data/logs/dify-api.log
docker exec dscore-qa tail -f /data/logs/jenkins.log
docker exec dscore-qa tail -f /data/logs/ollama.log

# supervisord 상태
docker exec dscore-qa supervisorctl status
```

### 프로세스 제어
```bash
# 특정 서비스 재시작
docker exec dscore-qa supervisorctl restart dify-api

# 모두 재시작
docker exec dscore-qa supervisorctl restart all
```

### 백업
`/data` 볼륨 하나만 백업하면 된다:
```bash
docker run --rm -v dscore-data:/data -v $PWD:/backup busybox \
  tar czf /backup/dscore-data-backup-$(date +%Y%m%d).tar.gz /data
```

### 복원
```bash
docker stop dscore-qa
docker run --rm -v dscore-data:/data -v $PWD:/backup busybox \
  tar xzf /backup/dscore-data-backup-YYYYMMDD.tar.gz -C /
docker start dscore-qa
```

### 업그레이드 (새 번들 수령 시)
```bash
# 1. 기존 컨테이너 중지 (볼륨 유지)
docker stop dscore-qa
docker rm dscore-qa

# 2. 기존 이미지 삭제 (선택)
docker rmi dscore-qa:allinone

# 3. 새 번들 로드
docker load -i dscore-qa-allinone-new.tar.gz

# 4. 재기동 (동일 볼륨 사용 → 기존 상태 유지)
docker run -d --name dscore-qa \
  -p 18080:18080 -p 18081:18081 -p 50001:50001 \
  -v dscore-data:/data \
  dscore-qa:allinone
```

PG 마이그레이션이 있으면 Dify api 기동 시 자동 수행 (Alembic).

---

## 5. 트러블슈팅

### 컨테이너가 기동 직후 죽는다
```bash
docker logs dscore-qa | tail -50
```
- 메모리 부족: 호스트 RAM 16GB+ 필요. `docker stats` 로 모니터링.
- PostgreSQL 초기화 실패: `/data/logs/postgresql.err.log` 확인. 볼륨 권한 문제라면 `docker volume rm dscore-data` 후 재기동 (주의: 모든 데이터 소실).

### Dify 웹 UI 502 Bad Gateway
- api 프로세스 미기동: `docker exec dscore-qa supervisorctl status dify-api`
- 마이그레이션 진행 중: `docker exec dscore-qa tail -f /data/logs/dify-api.log` 에서 `Running migration` 기다림 (1-3분)

### Jenkins Pipeline 빌드 실패 (에이전트 offline)
- `docker exec dscore-qa supervisorctl start jenkins-agent` 로 수동 기동
- NODE_SECRET 재추출: `docker exec dscore-qa bash /entrypoint.sh` 재실행 없이, jenkins 쪽에서 Configure Node → Re-connect

### Ollama 응답 없음 / 모델 없음
- `docker exec dscore-qa ollama list` 에서 `gemma4:e4b` 확인
- 누락이면 seed 실패 — `ls /data/ollama/models` 확인
- 볼륨 리셋 필요 시: `docker rm dscore-qa && docker volume rm dscore-data && docker run ...` (모든 데이터 소실 주의)

### 이미지 크기가 너무 커서 USB 이전 곤란
- 여러 조각으로 분할:
  ```bash
  split -b 2G dscore-qa-allinone-*.tar.gz dscore-qa-part-
  # 폐쇄망에서:
  cat dscore-qa-part-* > dscore-qa.tar.gz
  docker load -i dscore-qa.tar.gz
  ```

### 아키텍처 불일치 (exec format error)
- 빌드 머신 (macOS Apple Silicon = arm64) 과 폐쇄망 (Linux server = amd64) 다른 경우
- 빌드 시 명시:
  ```bash
  TARGET_PLATFORM=linux/amd64 ./offline/build-allinone.sh
  ```

### 시계 오류로 Dify 로그인 실패
- 폐쇄망에 NTP 없으면 컨테이너 시계가 초기화될 수 있음
- 호스트 시계를 먼저 맞추고 `docker restart dscore-qa`
- `docker exec dscore-qa date` 로 확인

---

## 6. 내부 구조 참고

### 파일 레이아웃
```
e2e-pipeline/offline/
├── Dockerfile.allinone          # 3-stage 멀티 빌드 정의
├── build-allinone.sh            # 온라인 빌드 실행 스크립트
├── provision-apps.sh            # 오프라인 전용 앱 프로비저닝 (Dify/Jenkins REST)
├── entrypoint-allinone.sh       # 컨테이너 PID 1 진입점 (seed + supervisord)
├── supervisord.conf             # 11개 프로세스 정의
├── nginx-allinone.conf          # localhost upstream nginx
├── pg-init-allinone.sh          # 빌드 타임 PG initdb + DB 생성
├── requirements-allinone.txt    # Python 의존성 통합
├── README.md                    # 이 문서
├── jenkins-plugins/             # 빌드 시 채워짐 (*.hpi)
└── dify-plugins/                # 빌드 시 채워짐 (*.difypkg)
```

### 런타임 프로세스 토폴로지
```
supervisord (PID 1, tini 위에)
├─ postgresql (:5432)
├─ redis (:6379)
├─ qdrant (:6333)
├─ ollama (:11434)
├─ dify-plugin-daemon (:5002)
├─ dify-api (:5001)
├─ dify-worker (celery)
├─ dify-worker-beat (celery beat)
├─ dify-web (:3000)
├─ nginx (:18081 → 5001/3000)
├─ jenkins (:18080 / :50001)
└─ jenkins-agent (loopback JNLP → 50001)
```

모든 서비스가 동일 네트워크 네임스페이스를 공유하므로 `127.0.0.1` 로 상호 통신.

### 볼륨 구조
```
/data/
├── .initialized              # seed 완료 플래그
├── .app_provisioned          # 앱 프로비저닝 완료 플래그
├── pg/                       # PostgreSQL data
├── redis/                    # Redis AOF
├── qdrant/                   # Qdrant storage
├── ollama/models/            # Ollama 모델 파일
├── jenkins/                  # JENKINS_HOME
│   ├── plugins/              # hpi
│   ├── jobs/
│   └── credentials.xml
├── jenkins-agent/            # agent.jar 작업 디렉토리
├── dify/
│   ├── storage/              # Dify app storage
│   └── plugins/              # plugin_daemon 저장소 (.difypkg 포함)
└── logs/                     # 모든 서비스 로그
    ├── supervisord.log
    ├── jenkins.log
    ├── dify-api.log
    ├── ollama.log
    └── ...
```

### 오프라인 프로비저닝 흐름
1. `entrypoint-allinone.sh` 가 supervisord 를 백그라운드로 기동
2. 서비스 헬스체크 통과 (Dify `/install` 200 + Jenkins `/api/json` 200)
3. `bash /opt/provision-apps.sh` 호출 (오프라인 전용 독립 스크립트 — 기존 setup.sh 와 분리)
   - 환경변수: `DIFY_URL=http://127.0.0.1:18081`, `JENKINS_URL=http://127.0.0.1:18080`,
     `OFFLINE_DIFY_PLUGIN_DIR=/opt/seed/dify-plugins`, `OLLAMA_BASE_URL=http://127.0.0.1:11434`
   - 수행: localhost 헬스체크 → Dify 관리자/로그인 → 로컬 .difypkg 업로드 →
     모델 등록 → Chatflow import/Publish/API Key 발급 → Jenkins 플러그인 로드 검증 →
     Credentials 등록 → Pipeline Job 생성 → Node 등록
   - setup.sh 는 건드리지 않으며, 이미지에 setup.sh 자체가 포함되지 않는다.
4. `/data/.app_provisioned` 플래그 생성 → 다음 기동부터는 프로비저닝 스킵
5. Jenkins agent.jnlp 에서 NODE_SECRET 추출 → supervisord 에 `jenkins-agent` 기동 요청
6. `wait supervisord_pid` 로 foreground 유지
