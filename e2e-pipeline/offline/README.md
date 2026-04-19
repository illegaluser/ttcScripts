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

### 1.1 사전 준비

#### 호스트 요구사항

| 항목 | 최소 | 권장 | 확인 명령 |
| ---- | ---- | ---- | --------- |
| Docker | 26.0 | 26.x 이상 | `docker --version` |
| Docker buildx | 활성 | 활성 | `docker buildx version` |
| JDK | 11 | 17/21 | `java -version` |
| 디스크 여유 | 40GB | 60GB | `df -h .` |
| 네트워크 | 외부망 가능 | — | `curl -I https://updates.jenkins.io` |
| 빌드 시간 | — | 30-90분 | (참고) |

#### 외부 네트워크 도메인 (방화벽 화이트리스트 필요)

| 도메인 | 용도 |
| ------ | ---- |
| `updates.jenkins.io` | Jenkins 플러그인 인덱스 |
| `get.jenkins.io` / `mirrors.jenkins.io` | hpi/jpi 파일 |
| `marketplace.dify.ai` | Dify 플러그인 메타+다운로드 |
| `github.com` / `objects.githubusercontent.com` | jenkins-plugin-manager.jar, qdrant tar |
| `registry-1.docker.io` / `auth.docker.io` | Docker Hub (ollama/dify/jenkins 이미지 pull) |
| `pypi.org` / `files.pythonhosted.org` | Python 패키지 |
| `playwright.azureedge.net` | Chromium 브라우저 다운로드 |
| `apt.postgresql.org` / `deb.debian.org` | OS 패키지 (이미지 빌드 중) |

#### 사전 점검 (빌드 시작 전 1회)

```bash
cd e2e-pipeline

# 1) 필수 입력 파일 존재 확인 (build-allinone.sh 가 사전 검증)
ls -1 setup.sh dify-chatflow.yaml DSCORE-ZeroTouch-QA-Docker.jenkinsPipeline
ls -d zero_touch_qa jenkins-init

# 2) Docker 정상 동작 + buildx 활성
docker run --rm hello-world
docker buildx ls

# 3) (멀티아키 빌드 시만) qemu 에뮬레이터 등록 — Apple Silicon 에서 amd64 빌드 등
docker run --privileged --rm tonistiigi/binfmt --install all
```

### 1.2 빌드 실행 (한 줄 명령)

```bash
cd e2e-pipeline
./offline/build-allinone.sh 2>&1 | tee /tmp/build-allinone.log
```

> 로그를 `tee` 로 떠두면 실패 시 어느 단계에서 멈췄는지 추적이 쉽다.

#### 환경변수로 동작 변경

| 변수 | 기본값 | 의미 |
| ---- | ------ | ---- |
| `IMAGE_TAG` | `dscore-qa:allinone` | 산출 이미지 태그 |
| `TARGET_PLATFORM` | `linux/amd64` | 폐쇄망 타겟 아키 — arm64 서버는 `linux/arm64` |
| `OLLAMA_MODEL` | `gemma4:e4b` | seed 할 LLM 모델 (~4GB) |
| `JENKINS_VERSION` | (자동 감지) | `jenkins/jenkins:lts-jdk21` 에서 동적 추출. 명시 시 그 버전 고정 |
| `OUTPUT_TAR` | `dscore-qa-allinone-<timestamp>.tar.gz` | 최종 압축 파일명 |

```bash
# 예: 폐쇄망 서버가 arm64 인 경우
TARGET_PLATFORM=linux/arm64 ./offline/build-allinone.sh

# 예: 모델 변경 (Llama 3.1 8B)
OLLAMA_MODEL=llama3.1:8b ./offline/build-allinone.sh
```

### 1.3 빌드 단계별 상세

#### [1/4] Jenkins 플러그인 hpi 다운로드 — 재귀 의존성 해결

**무엇을 하는가**: 폐쇄망 Jenkins 가 외부에서 hpi 를 받을 수 없으므로, 빌드 시 모든 의존성까지 미리 받아 이미지에 포함한다.

**도구**: [jenkins-plugin-manager.jar 2.13.2](https://github.com/jenkinsci/plugin-installation-manager-tool) — Jenkins 공식 CLI. `--plugin-file` 의 직접 의존성을 따라 모든 hpi 를 재귀로 받아준다.

**대상 플러그인** ([build-allinone.sh:30-35](build-allinone.sh#L30-L35)):

- `workflow-aggregator` — Pipeline 전체 (이게 ~30+ 의존성을 끌고 옴)
- `file-parameters` — Pipeline 파라미터로 파일 업로드
- `htmlpublisher` — `artifacts/index.html` 시각 리포트
- `plain-credentials` — `dify-qa-api-token` 저장

**Jenkins 버전 매칭** ([build-allinone.sh:80-89](build-allinone.sh#L80-L89)): 플러그인은 Jenkins 버전과 호환성이 깨지기 쉽다. 그래서 `jenkins/jenkins:lts-jdk21` 이미지에서 실제 jenkins.war 의 버전을 동적으로 추출해 그 버전으로 다운로드한다 (예: `2.479.3`). `JENKINS_VERSION=...` 로 덮어쓰기 가능.

**산출물**: `offline/jenkins-plugins/*.hpi` 또는 `*.jpi` (40-50개, ~150MB)

```bash
# 빌드 후 확인
ls -lh offline/jenkins-plugins/ | head
find offline/jenkins-plugins -name '*.hpi' -o -name '*.jpi' | wc -l
# 예상: 40-50개
```

> **주의**: plugin-manager 2.13.2 는 파일을 `.jpi` 로 저장한다 (`.hpi` 와 동등 — Jenkins 가 양쪽 모두 로드). [Dockerfile.allinone:105](Dockerfile.allinone#L105) 의 COPY 가 디렉토리 통째로 가져가므로 신경쓸 필요 없다.

**실패 시**:

- `Unable to resolve plugin XYZ` → Jenkins 버전이 너무 낮음. `JENKINS_VERSION=2.479.3` 같이 더 높은 LTS 명시.
- `Connection timed out updates.jenkins.io` → 방화벽/프록시 점검. 프록시는 `JAVA_OPTS="-Dhttp.proxyHost=... -Dhttp.proxyPort=..."` 로 java 호출 옵션에 전달.

---

#### [2/4] Dify 플러그인 `.difypkg` 다운로드 — marketplace 2-step API

**무엇을 하는가**: 폐쇄망 Dify 가 외부 marketplace 에 접근할 수 없으므로 `.difypkg` (Dify 플러그인 패키지) 를 사전 다운로드해 이미지에 포함한다.

**대상**: `langgenius/ollama` — Dify 가 로컬 Ollama 모델을 LLM 공급자로 사용할 수 있게 하는 플러그인.

**API 흐름** ([build-allinone.sh:106-130](build-allinone.sh#L106-L130)):

1. `POST /api/v1/plugins/batch` 로 `latest_package_identifier` 조회 → `langgenius/ollama:0.0.x@sha256:...` 형태의 UID 획득
2. `GET /api/v1/plugins/download?unique_identifier=<UID>` 로 `.difypkg` 바이너리 다운로드

**산출물**: `offline/dify-plugins/langgenius-ollama-<version>.difypkg` (~5MB)

```bash
ls -lh offline/dify-plugins/
# 예상: langgenius-ollama-0.0.6.difypkg  4.8M
```

**실패 시**:

- `marketplace 조회 실패` → `curl https://marketplace.dify.ai/api/v1/plugins/batch -X POST -H 'Content-Type: application/json' -H 'X-Dify-Version: 1.13.3' -d '{"plugin_ids":["langgenius/ollama"]}' | jq` 로 응답을 직접 확인. 응답에 `data.plugins[0].latest_package_identifier` 가 있어야 함.
- 그 외 플러그인 추가 필요 시 (예: `langgenius/openai`) → `build-allinone.sh` 에서 `DIFY_PLUGIN_ID` 변수를 배열로 바꾸고 루프하면 된다.

---

#### [3/4] Docker buildx build — 3-stage 멀티 빌드 (가장 오래 걸리는 단계)

**무엇을 하는가**: 위에서 받은 hpi/difypkg 와 7개 외부 이미지를 한 이미지로 융합한다. 30-90분 소요.

**3-stage 구조** ([Dockerfile.allinone](Dockerfile.allinone)):

| Stage | base 이미지 | 추출 대상 | 최종 이미지에 포함되는 것 |
| ----- | ----------- | --------- | ------------------------- |
| 1. `ollama-src` | `ollama/ollama:latest` | `/bin/ollama` 바이너리 + `~/.ollama` 모델 | 두 항목만 COPY → `/usr/bin/ollama`, `/opt/seed/ollama` |
| 2a. `dify-api-src` | `langgenius/dify-api:1.13.3` | `/app` (Python 앱 + .venv) | `/opt/dify-api` 통째 |
| 2b. `dify-web-src` | `langgenius/dify-web:1.13.3` | `/app` (Next.js 빌드) | `/opt/dify-web` 통째 |
| 2c. `dify-plugin-src` | `langgenius/dify-plugin-daemon:0.5.3-local` | `/app` (Go 바이너리) | `/opt/dify-plugin-daemon` 통째 |
| 3. (final) | `jenkins/jenkins:lts-jdk21` | — | OS 패키지 설치 + 위 5개 산출물 통합 |

**Stage 1 (Ollama 모델 seed)** — 가장 위험하고 큰 단계 (4GB 다운로드):

```dockerfile
# Dockerfile.allinone:18-24
RUN ollama serve & \
    ... ollama pull "${OLLAMA_MODEL}" && \
    kill "$ollama_pid"
```

빌드 컨테이너 안에서 ollama 데몬을 띄운 뒤 모델을 pull 하고, 모델 디렉토리(`/root/.ollama`)만 다음 stage 로 COPY 한다. 모델은 `/opt/seed/ollama` 에 저장되며, 컨테이너 첫 기동 시 [entrypoint-allinone.sh](entrypoint-allinone.sh) 가 `/data/ollama` 로 복사한다 (실행 중에는 모델을 다시 받지 않음).

**Stage 3 (런타임 통합)** — Jenkins LTS JDK21 위에 모든 것을 얹는다:

```
jenkins/jenkins:lts-jdk21 (Debian trixie base)
├─ APT 추가: postgresql-15 (pgdg repo), redis, nginx, supervisor, python3.11,
│           tini, jq, libreoffice-impress, fonts-noto-cjk, Playwright 의존성
├─ COPY: ollama 바이너리 + 모델 (Stage 1)
├─ DOWNLOAD: qdrant v1.8.3 tar.gz → /usr/local/bin/qdrant
├─ COPY: dify-api / dify-web / dify-plugin-daemon (Stage 2)
├─ pip install: requirements-allinone.txt + deepeval (Jenkins pipeline 용)
├─ playwright install --with-deps chromium (~400MB)
├─ COPY: offline/jenkins-plugins/*.hpi → /opt/seed/jenkins-plugins/
├─ COPY: offline/dify-plugins/*.difypkg → /opt/seed/dify-plugins/
├─ RUN: pg-init-allinone.sh (initdb + Dify DB 사전 생성 → /opt/seed/pg)
├─ COPY: supervisord.conf, nginx-allinone.conf, entrypoint, provision-apps.sh
└─ ENTRYPOINT: tini → /entrypoint.sh
```

**왜 PostgreSQL 15 인가**: jenkins/jenkins:lts-jdk21 의 base 가 Debian 13 (trixie) 이라 기본 apt 에는 PG 17 만 있다. 그러나 Dify 1.13.3 공식 지원은 PG 15 이므로 `apt.postgresql.org` 의 pgdg repo 를 추가해 15 를 강제한다 ([Dockerfile.allinone:43-52](Dockerfile.allinone#L43-L52)).

**왜 PG 를 빌드 타임에 initdb 하는가**: 폐쇄망 첫 기동 시 PG 초기화가 5-10초 더 걸리는 것을 피하고, dify_api 등 5개 DB 가 사전에 생성된 상태로 출발하기 위해서다. seed 디렉토리(`/opt/seed/pg`) 는 첫 기동 시 `/data/pg` 로 복사된다.

**진행 상황 모니터링** (다른 터미널):
```bash
# 디스크 사용량 (40GB+ 차오르면 의심)
watch -n 5 'df -h . && echo --- && docker system df'

# 빌드 캐시 상태
docker buildx du
```

**실패 시**:

- `no space left on device` → `docker system prune -af --volumes` 실행 후 재시도
- `pull access denied for langgenius/dify-api` → Docker Hub rate limit. `docker login` 또는 30분 후 재시도
- `gemma4:e4b not found` → 모델명 확인. `docker run --rm ollama/ollama:latest ollama list` 는 비어 있음 (pull 후에만 보임)
- 빌드 도중 멈춤 → Ollama pull 일 가능성 큼. Stage 1 만 별도 검증: `docker buildx build --target ollama-src -f offline/Dockerfile.allinone .`

---

#### [4/4] docker save + gzip — 배포용 단일 파일 산출

**무엇을 하는가**: 빌드된 이미지를 단일 파일로 직렬화 후 압축한다. 이 파일이 USB/사내망으로 폐쇄망에 전달된다.

```bash
# build-allinone.sh:153
docker save dscore-qa:allinone | gzip -1 > dscore-qa-allinone-<timestamp>.tar.gz
```

**왜 `gzip -1` 인가**: 최저 압축률(빠른 압축). docker layer 는 이미 압축돼 있어 추가 압축 효과가 작은데, 시간 비용은 크다. 1GB 정도 줄이는 게 목표.

**예상 크기**:

- 비압축 이미지: 9-12GB (`docker images dscore-qa:allinone` 의 SIZE)
- 압축 후: 5-9GB (모델/플러그인은 이미 압축돼 있어 절감 폭 작음)
- 압축 시간: 2-5분 (CPU 의존)

### 1.4 빌드 검증 (배포 전 필수)

```bash
# 1) 이미지 존재 + 크기 확인
docker images dscore-qa:allinone
# 예상: dscore-qa:allinone  <hash>  <date>  ~10GB

# 2) tar.gz 크기 확인
ls -lh dscore-qa-allinone-*.tar.gz

# 3) seed 파일 무결성 검사 (옵션 — 이미지 내부 점검)
docker run --rm --entrypoint bash dscore-qa:allinone -lc '
  echo "=== Ollama 모델 ===" && du -sh /opt/seed/ollama
  echo "=== Jenkins 플러그인 ===" && ls /opt/seed/jenkins-plugins | wc -l
  echo "=== Dify 플러그인 ===" && ls /opt/seed/dify-plugins
  echo "=== PG snapshot ===" && du -sh /opt/seed/pg
  echo "=== Qdrant 바이너리 ===" && qdrant --version
  echo "=== Ollama 바이너리 ===" && ollama --version
'

# 4) 스모크 테스트 — 임시로 띄워서 5분 후 헬스체크 (선택)
docker run -d --name smoke -p 18080:18080 -p 18081:18081 -v smoke-data:/data dscore-qa:allinone
sleep 300  # 첫 기동 5분 대기
curl -fsS http://localhost:18080/login | grep -q Jenkins && echo "Jenkins OK"
curl -fsS http://localhost:18081/install | grep -q Dify && echo "Dify OK"
docker rm -f smoke && docker volume rm smoke-data
```

### 1.5 산출물 위치 정리

```
e2e-pipeline/
├── dscore-qa-allinone-<timestamp>.tar.gz   ← 폐쇄망으로 이동할 파일 (유일)
└── offline/
    ├── jenkins-plugins/*.hpi (또는 *.jpi)  ← 빌드 중간물 (이미지에 포함됨, 삭제 가능)
    ├── dify-plugins/*.difypkg              ← 빌드 중간물 (이미지에 포함됨, 삭제 가능)
    └── jenkins-plugin-manager.jar          ← 재빌드 위해 캐시 (삭제하면 재다운로드)
```

빌드 후 디스크 정리:

```bash
# 다음 빌드까지 보관할 필요 없으면
rm -rf offline/jenkins-plugins offline/dify-plugins
docker image prune -f      # dangling layer 제거
docker buildx prune -f     # buildx 캐시 제거 (다음 빌드 시 재다운로드 필요)
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
