# Zero-Touch QA All-in-One (호스트 하이브리드 — Mac / Windows 11)

Jenkins master + Dify + DB 를 **단일 Docker 이미지**로 묶고, 추론 (Ollama) 과 브라우저 실행 (Playwright agent) 은 **호스트에서** 수행하는 하이브리드 배포본. 컨테이너 내부에는 Ollama 도 Jenkins agent 도 없다.

---

## 이 문서 읽는 법

| 목적 | 섹션 |
|------|------|
| **처음 설치하고 Pipeline 을 돌리기까지** — Mac 또는 Windows 11 | [§1 설치 및 구동 절차](#1-설치-및-구동-절차) |
| "이 스크립트/파일이 정확히 뭐 하는지" 레퍼런스 | [§2 구성 파일 레퍼런스](#2-구성-파일-레퍼런스) |
| 재시작 / 백업 / 업그레이드 / Ollama 모델 변경 등 일상 운영 | [§3 운영 가이드](#3-운영-가이드) |
| 문제가 생겼을 때 | [§4 트러블슈팅](#4-트러블슈팅) |
| 프로세스 토폴로지 / 볼륨 구조 / 포트 | [부록 A](#부록-a-토폴로지--볼륨-구조) |

---

## 개요

### 아키텍처

```text
┌─ 컨테이너 (dscore.ttc.playwright:latest) ─────────────────┐
│  Jenkins master   (:18080, :50001 JNLP)                   │
│  Dify api/worker/web/plugin-daemon  (:5001, :3000, :5002) │
│  PostgreSQL 15 / Redis / Qdrant                           │
│  nginx (:18081 → Dify)                                    │
│  supervisord (10 개 프로세스)                             │
└────────┬─────────────────────────────────────┬────────────┘
         │                                     │
 host.docker.internal:11434 (Ollama)     JNLP :50001 (agent)
         │                                     │
┌────────┴───────────────────────────┐  ┌──────┴─────────────┐
│ 호스트 Ollama                      │  │ 호스트 agent       │
│  - Mac:  macOS 네이티브 (Metal)    │  │  - Mac:  bash      │
│  - Win:  Windows 네이티브 (CUDA)   │  │  - Win:  WSL2 bash │
└────────────────────────────────────┘  │  JDK 21 + Python + │
                                        │  Playwright        │
                                        │  → headed Chromium │
                                        │    (Mac: 창 직접 / │
                                        │     Win: WSLg 경유)│
                                        └────────────────────┘
```

### 왜 하이브리드인가

Docker Desktop 이 Linux 컨테이너에 **GPU / 디스플레이 전달을 지원하지 않는다**:

- Metal GPU passthrough 부재 (Mac) → 컨테이너 Ollama 1-2 tok/s
- X/Wayland 소켓 없음 → 컨테이너 Playwright headed Chromium 불가
- Windows 는 LLM 은 CUDA 로 컨테이너 가능하지만 headed 창을 Windows 데스크탑에 띄울 수 없음

그래서 **성능·UX 에 민감한 두 컴포넌트 (LLM 추론, 브라우저) 를 호스트로** 빼냈다.

### 트레이드오프

| 항목 | 값 |
|------|-----|
| 이미지 크기 (비압축) | ~10GB |
| 배포 tar.gz | 2-3GB |
| 빌드 | 10-30분 (초기) / 3-5분 (캐시) |
| 첫 기동 | 3-5분 (provision 포함) |
| 이후 기동 | 30-60초 |
| 호스트 RAM | 16GB+ |
| LLM | Mac Metal / Windows CUDA 30-80 tok/s |
| 브라우저 | 호스트 네이티브 headed Chromium |
| 외부 포트 | 18080 (Jenkins) / 18081 (Dify) / 50001 (JNLP) |

---

## 1. 설치 및 구동 절차

**이 순서대로만 따라하면 됩니다.** Mac / Windows 11 공통 4 단계.

### 1.1 플랫폼별 사전 준비 (최초 1회)

공통: Docker Desktop 4.30+, RAM 16GB+, 인터넷 가능 (빌드 시).

#### Mac (Apple Silicon)

```bash
# A. Ollama + 모델
brew install ollama
brew services start ollama
ollama pull gemma4:e4b

# B. JDK 21 + Python 3.11+
brew install openjdk@21
brew install python@3.12

# 확인
ollama list | grep gemma4:e4b
java -version    # "21.x"
python3 --version    # "3.11" 이상
```

> 위 A/B 를 건너뛰고 **Step 3 에서 `AUTO_INSTALL_DEPS=true`** 로 자동 설치도 가능. brew 는 직접 먼저 설치돼 있어야 함.

#### Windows 11

Windows 측 (PowerShell 관리자):

```powershell
# A. WSL2 + Ubuntu 22.04+ (한 번만)
wsl --install -d Ubuntu-22.04     # 재부팅 1회 필요. 재부팅 후 Ubuntu 앱 기동 → user 설정

# B. NVIDIA 드라이버 최신 (GPU 사용 시) — WSL2 에 CUDA 자동 노출

# C. Ollama — Windows 네이티브 설치
winget install Ollama.Ollama       # 트레이 앱 자동 기동
ollama pull gemma4:e4b
```

> **왜 Windows 네이티브 Ollama?** 컨테이너는 Docker Desktop 포워딩으로 `host.docker.internal:11434` → Windows 127.0.0.1 에 도달한다. WSL 안에 Ollama 를 중복 설치하면 모델이 두 번 디스크 차지.

이후 **모든 명령은 WSL2 Ubuntu 셸** 안에서 실행:

```bash
# D. WSL Ubuntu 안 — JDK 21 + Python 3.11+
sudo apt update
sudo apt install -y openjdk-21-jdk-headless python3.12 python3.12-venv

# 확인
nvidia-smi        # Windows 드라이버가 WSL2 에 GPU 노출
java -version
python3 --version
```

폐쇄망 Windows 오프라인 설치는 [§4.5](#45-windows-wsl2-오프라인-설치-폐쇄망) 참조.

### 1.2 Step 1 — 이미지 빌드

```bash
git clone <저장소> && cd <저장소>/e2e-pipeline
./offline/build-allinone.sh 2>&1 | tee /tmp/build.log
```

- 10-30분 걸림 (초기), 3-5분 (캐시 재사용)
- 출력: `e2e-pipeline/dscore.ttc.playwright-<timestamp>.tar.gz` (2-3GB)
- 플랫폼 자동 감지 (Mac arm64 → linux/arm64, Win/Linux x86 → linux/amd64)

> 상세 옵션은 [§2.1 build-allinone.sh](#21-build-allinonesh) 참조.

### 1.3 Step 2 — 컨테이너 기동

빌드 머신과 실행 머신이 다르면 tar.gz 를 USB/사내망으로 옮긴 뒤 실행 머신에서:

```bash
docker load -i dscore.ttc.playwright-*.tar.gz

docker run -d --name dscore.ttc.playwright \
  -p 18080:18080 -p 18081:18081 -p 50001:50001 \
  -v dscore-data:/data \
  --add-host host.docker.internal:host-gateway \
  -e OLLAMA_BASE_URL=http://host.docker.internal:11434 \
  -e OLLAMA_MODEL=gemma4:e4b \
  --restart unless-stopped \
  dscore.ttc.playwright:latest

docker logs -f dscore.ttc.playwright
```

첫 기동 3-5분 동안 내부에서:

| 경과 | 로그 마커 |
|------|-----------|
| 0:00 | `최초 seed: /opt/seed → /data` |
| 0:05-0:30 | `supervisord 기동` + 10 개 프로세스 RUNNING |
| 0:30-1:30 | dify-api HTTP 200 대기 (amd64 는 60-90s 걸릴 수 있음) |
| 1:30-2:30 | `[▶] 1. 서비스 헬스체크` → `2. Dify 초기 설정` |
| 2:30-4:00 | Ollama 플러그인 / credential swap / Chatflow / API Key / Jenkins Job / Node |
| ~3-5분 | **`NODE_SECRET: <64자 hex>`** 로그 출력 → Step 3 에서 사용 |
| 직후 | `[▶] === 프로비저닝 완료 ===` + `/data/.app_provisioned` 생성 |

컨테이너 내부 동작의 파일 단위 상세: [§2.3 entrypoint](#23-entrypoint-allinonesh) + [§2.4 provision](#24-provision-appssh).

### 1.4 Step 3 — 호스트 Jenkins agent 연결

**터미널 하나를 agent 전용으로 열어두고**:

#### Mac

```bash
./offline/mac-agent-setup.sh
```

#### Windows 11 / WSL2 Ubuntu

```bash
./offline/wsl-agent-setup.sh
```

자동으로:

1. 기존 `agent.jar` 프로세스 + 중복 setup 인스턴스 정리 (session id 기반)
2. `docker logs` 에서 **NODE_SECRET 자동 추출**
3. JDK 21 / Python 3.11+ 확인
4. venv 생성 + Playwright Chromium 설치
5. Jenkins Node `mac-ui-tester` 의 remoteFS 를 호스트 절대경로로 갱신
6. agent.jar 다운로드
7. foreground 로 agent 기동 → `INFO: Connected`

의존성 자동 설치가 필요하면 (1.1 B/D 단계 스킵) `AUTO_INSTALL_DEPS=true` 를 앞에 붙인다:

```bash
AUTO_INSTALL_DEPS=true ./offline/mac-agent-setup.sh       # brew 로 자동 설치
AUTO_INSTALL_DEPS=true ./offline/wsl-agent-setup.sh       # sudo apt 로 자동 설치
```

스크립트 내부 동작 상세: [§2.5 agent-setup](#25-mac-agent-setupsh--wsl-agent-setupsh).

### 1.5 Step 4 — 첫 Pipeline 실행 (검증)

브라우저에서 <http://localhost:18080> → `admin / password` 로 로그인:

1. Dashboard → `DSCORE-ZeroTouch-QA-Docker` → **Build with Parameters**
2. 기본값 유지 (`HEADLESS` 체크 해제 상태)
3. **Build** → 호스트 화면에 Chromium 창이 떠서 네이버 검색 시나리오 진행 → 30-90 초 내 `Finished: SUCCESS`

접속 정보:

| 서비스 | URL | 기본 계정 |
|--------|-----|-----------|
| Jenkins | <http://localhost:18080> | admin / password |
| Dify | <http://localhost:18081> | admin@example.com / Admin1234! |

프로비저닝이 정말 끝났는지 확인하는 체크리스트: [§3.5 프로비저닝 체크리스트](#35-프로비저닝-체크리스트).

### 1.6 개발 루프 단축 — `--redeploy`

같은 호스트에서 **빌드 → 기존 컨테이너 정리 → 재기동 → agent 재연결** 을 한 명령으로:

```bash
./offline/build-allinone.sh --redeploy              # 볼륨 유지 (provision 재사용)
./offline/build-allinone.sh --redeploy --fresh      # 볼륨까지 초기화
./offline/build-allinone.sh --redeploy --no-agent   # 컨테이너만 재기동
```

폐쇄망 타겟 (빌드 머신 ≠ 실행 머신) 에서는 `--redeploy` 를 쓰지 않고 **Step 1 만** 수행한 뒤 tar.gz 를 옮겨 실행 머신에서 **Step 2 → Step 3** 을 수행한다.

옵션 상세: [§2.1 build-allinone.sh](#21-build-allinonesh).

---

## 2. 구성 파일 레퍼런스

`offline/` 디렉토리의 모든 파일을 사용자가 실행/접근하는 순서에 맞춰 나열. 각 섹션 포맷은 일관:

- **목적** — 한 줄 요약
- **실행 시점** — 빌드 타임 / 컨테이너 내부 / 호스트 / 참조 전용
- **주요 env / 옵션**
- **동작 요약**
- **수정이 필요할 때**

### 2.1 `build-allinone.sh`

**목적**: 온라인 빌드 머신에서 이미지 `*.tar.gz` 를 산출. 선택적으로 같은 호스트에 바로 재배포까지.

**실행 시점**: 호스트 (Mac / Linux / WSL2 bash) — 수동 실행.

**주요 env / 옵션**:

| 이름 | 기본값 | 비고 |
|------|--------|------|
| `IMAGE_TAG` | `dscore.ttc.playwright:latest` | 이미지 repo:tag |
| `TARGET_PLATFORM` | `uname -m` 자동 감지 | Apple Silicon → `linux/arm64`, 그 외 → `linux/amd64`. override 시 qemu silent-fail 주의 |
| `OLLAMA_MODEL` | `gemma4:e4b` | Dify provider 등록 시 사용될 모델 id |
| `OUTPUT_TAR` | `dscore.ttc.playwright-<ts>.tar.gz` | 출력 tar.gz 파일명 |
| `--redeploy` | — | 빌드 후 기존 컨테이너 rm → run → NODE_SECRET 대기 (최대 15분) → agent-setup 자동 기동 |
| `--fresh` | — | `--redeploy` 와 함께 — `dscore-data` 볼륨까지 삭제 (provision 재수행) |
| `--no-agent` | — | `--redeploy` 와 함께 — 컨테이너만 재기동, agent 는 수동으로 |

**동작 요약**:

1. Jenkins 플러그인 `.hpi` 재귀 다운로드 → `offline/jenkins-plugins/` (40-50 개)
2. Dify 플러그인 `.difypkg` 다운로드 → `offline/dify-plugins/`
3. `docker buildx build` → Dockerfile.allinone 로 이미지 생성
4. `docker save | gzip -1` → `dscore.ttc.playwright-<ts>.tar.gz`
5. (`--redeploy` 시) 컨테이너 재기동 + agent-setup 자동 호출

**수정이 필요할 때**: 플러그인 목록 변경 / 빌드 플로우 추가 / 재배포 옵션 확장.

### 2.2 `Dockerfile.allinone`

**목적**: 이미지 레이아웃 정의 (2-stage 멀티 빌드).

**실행 시점**: `build-allinone.sh` 의 [3/4] 단계가 호출하는 `docker buildx`.

**동작 요약**:

- **Stage 1** — 공식 이미지에서 `/opt/dify-*` 애플리케이션 디렉토리 복사만
  - `langgenius/dify-api:1.13.3`
  - `langgenius/dify-web:1.13.3`
  - `langgenius/dify-plugin-daemon:0.5.3-local`
- **Stage 2** (final) — `jenkins/jenkins:lts-jdk21` 베이스에 합치기
  - OS 패키지: postgresql-15, redis-server, nginx, python3+venv, supervisor, tini, Playwright 의존 라이브러리
  - Stage 1 산출물 COPY
  - Qdrant 바이너리 + Node.js (TARGETARCH 분기 — amd64 는 glibc / arm64 는 musl)
  - `offline/jenkins-plugins/` + `offline/dify-plugins/` seed
  - 프로비저닝용 스크립트/설정 파일 + 루트의 `DSCORE-ZeroTouch-QA-Docker.jenkinsPipeline` / `dify-chatflow.yaml` 을 이미지에 포함
  - 빌드 타임에 `pg-init-allinone.sh` 실행 → `/opt/seed/pg/` 에 Dify DB initdb 산출물 생성

**수정이 필요할 때**: Dify / Jenkins 버전업, OS 패키지 추가, seed 자원 경로 변경.

### 2.3 `entrypoint-allinone.sh`

**목적**: 컨테이너 PID 1 (tini 위) — seed 복사 + supervisord 기동 + provision 호출 + NODE_SECRET 출력.

**실행 시점**: `docker run` 이 자동 호출. 직접 호출하지 않는다.

**동작 요약** (타임라인):

1. **seed** — `/data/.initialized` 플래그 없으면 `/opt/seed/*` → `/data/` 복사 (pg/jenkins/dify 초기 상태)
2. **supervisord 기동** — [§2.6 supervisord.conf](#26-supervisordconf) 로드, 10 개 프로세스
3. **헬스 대기** — dify-api `/console/api/setup` + dify-web + Jenkins 모두 HTTP 200 까지 (최대 10분)
4. **provision 호출** — `/data/.app_provisioned` 없으면 `bash /opt/provision-apps.sh`
5. **NODE_SECRET 출력** — `/computer/mac-ui-tester/slave-agent.jnlp` 에서 추출해 로그로 출력 (재시작 때마다 재출력)
6. **supervisord foreground wait**

**수정이 필요할 때**: 기동 순서 변경, 추가 seed 경로, 헬스 대기 로직 변경.

### 2.4 `provision-apps.sh`

**목적**: Dify + Jenkins 프로비저닝 (관리자 / 플러그인 / 모델 / Chatflow / API Key / Pipeline Job / Node 생성).

**실행 시점**: 컨테이너 내부. entrypoint 가 `.app_provisioned` 없을 때 자동 호출. 수동 재실행도 가능:

```bash
docker exec dscore.ttc.playwright bash /opt/provision-apps.sh
```

**동작 요약**:

| 단계 | 내용 |
|------|------|
| 1. 서비스 헬스체크 | dify-web / dify-api / Jenkins HTTP 200 대기 |
| 2-1. Dify 관리자 생성 | POST `/console/api/setup` |
| 2-2. Dify 로그인 | POST `/console/api/login` → 쿠키 + CSRF 토큰 |
| 2-3a. Ollama 플러그인 설치 | 로컬 `.difypkg` 업로드 |
| 2-3b. 모델 공급자 등록 | `base_url = host.docker.internal:11434`, 모델 id = `$OLLAMA_MODEL` |
| 2-3c. credential_id swap | Dify 내부 `provider_models.credential_id` 를 신규 레코드로 교체 (plugin-daemon 캐시 우회) |
| 2-3d. Redis FLUSH | `provider_model_credentials:*` + 모델 다이제스트 키 삭제 |
| 2-3e. dify-api + plugin-daemon 재기동 | `supervisorctl restart` + **HTTP readiness 대기 (최대 300s)** |
| 2-4. Chatflow DSL import | `dify-chatflow.yaml` 읽어 Dify App 생성 |
| 2-5. Publish + API Key 발급 | Dify App 을 publish → Jenkins Credentials 에 등록 |
| 3. Jenkins 설정 | 플러그인 4 개 검증 / Credentials / Pipeline Job / Node `mac-ui-tester` 생성 |

**주요 env** (entrypoint 가 주입):

- `DIFY_URL`, `JENKINS_URL`, `DIFY_EMAIL`, `DIFY_PASSWORD`, `JENKINS_ADMIN_USER/PW`
- `OFFLINE_DIFY_PLUGIN_DIR`, `OFFLINE_DIFY_CHATFLOW_YAML`, `OFFLINE_JENKINS_PIPELINE`
- `OLLAMA_MODEL`, `OLLAMA_BASE_URL`

**완전 재프로비저닝**:

```bash
docker exec dscore.ttc.playwright rm -f /data/.app_provisioned
docker restart dscore.ttc.playwright
```

**수정이 필요할 때**: 초기 설정 플로우 변경, 새 Chatflow / Credential / Job 추가, Dify API 변경 대응.

### 2.5 `mac-agent-setup.sh` / `wsl-agent-setup.sh`

**목적**: 호스트에서 Jenkins agent 환경 구성 + agent.jar 실행. 두 파일은 동일한 **7 단계** 를 bash 로 수행, 차이는 패키지 매니저 (brew vs apt) 와 JDK 경로 탐색뿐.

**실행 시점**: 호스트 셸 (Mac: macOS Terminal / Windows: WSL2 Ubuntu bash). 사용자가 **반복 실행 가능** — 매번 이전 agent 를 자동 정리하고 새로 연결.

**주요 env**:

| 이름 | 기본값 | 비고 |
|------|--------|------|
| `NODE_SECRET` | (자동 추출) | 없으면 `docker logs $CONTAINER_NAME` 에서 추출 |
| `CONTAINER_NAME` | `dscore.ttc.playwright` | 자동 추출 대상 컨테이너 |
| `AUTO_INSTALL_DEPS` | `false` | `true` 시 brew/apt 로 의존성 자동 설치 |
| `OLLAMA_MODEL` | `gemma4:e4b` | 존재 확인 대상 모델 (정보성) |
| `JENKINS_URL` | `http://localhost:18080` | |
| `AGENT_NAME` | `mac-ui-tester` | Pipeline `agent { label ... }` 과 일치해야 함 |
| `MAC_AGENT_WORKDIR` / `WSL_AGENT_WORKDIR` | `$HOME/.dscore.ttc.playwright-agent` | 작업 디렉토리 |
| `FORCE_AGENT_DOWNLOAD` | `false` | `true` 시 agent.jar 강제 재다운로드 |

**동작 요약**:

| 단계 | 내용 |
|------|------|
| **0-A. 정리** | 기존 `agent.jar` + 다른 세션의 setup 인스턴스를 session id 기반으로 종료. Jenkins disconnect 인지 대기 (최대 15s) |
| **0-B. NODE_SECRET 추출** | env 없으면 `docker logs` 에서 자동 추출 |
| **1. Ollama 도달성** | 호스트 Ollama 가 reachable 한지 정보성 확인 (실패해도 진행 — 컨테이너 쪽 경로 별도) |
| **2. JDK 21** | 엄격 탐지. Mac: `/opt/homebrew/opt/openjdk@21` 또는 `/usr/libexec/java_home -v21`. WSL2: `/usr/lib/jvm/{temurin,java}-21-*` |
| **3. Python 3.11+** | `sys.version_info >= (3,11)` 만족하는 `python3` |
| **4. venv + Chromium** | `~/.dscore.ttc.playwright-agent/venv` 생성 + `pip install requests playwright pillow` + `playwright install chromium` |
| **5. Node remoteFS** | Groovy 로 Jenkins Node `mac-ui-tester` 의 remoteFS 를 호스트 절대경로로 갱신 + workspace venv symlink |
| **6. agent.jar** | `$JENKINS_URL/jnlpJars/agent.jar` 다운로드 |
| **7. 기동** | `run-agent.sh` 생성 + `exec java -jar agent.jar ...` foreground 실행 → `INFO: Connected` |

**결과물**:

```
~/.dscore.ttc.playwright-agent/
├── venv/                         # Python 3.11+ + Playwright
├── agent.jar                     # Jenkins remoting jar
├── run-agent.sh                  # 재연결 시 이 스크립트 직접 실행해도 됨
└── workspace/DSCORE-ZeroTouch-QA-Docker/
    └── .qa_home/                 # Pipeline Stage 1 이 생성
        ├── venv → /home/user/.dscore.ttc.playwright-agent/venv (symlink)
        └── artifacts/
```

**수정이 필요할 때**: 새 OS/패키지 매니저 지원, JDK/Python 탐색 경로 추가, Chromium 외 다른 브라우저 추가.

### 2.6 `supervisord.conf`

**목적**: 컨테이너 내부 10 개 프로세스의 기동 순서 / env / 로그 경로 정의.

**실행 시점**: `entrypoint-allinone.sh` 가 호출하는 `supervisord` 가 로드 (`/etc/supervisor/supervisord.conf`).

**기동 순서** (priority 낮을수록 먼저):

| priority | 프로그램 | 포트 | 역할 |
|----------|----------|------|------|
| 100 | postgresql | 5432 | Dify DB 5 개 |
| 100 | redis | 6379 | Dify 큐 + Celery broker |
| 100 | qdrant | 6333 | Dify 벡터 스토어 |
| 200 | dify-plugin-daemon | 5002 | Ollama 플러그인 gRPC |
| 300 | dify-api | 5001 | gunicorn + gevent 1 worker (첫 응답 60-90s 걸릴 수 있음) |
| 300 | dify-worker / dify-worker-beat | — | Celery 워커 + 스케줄러 |
| 300 | dify-web | 3000 | Next.js 프론트 |
| 400 | nginx | 18081 | api/web 프록시 ([§2.7](#27-nginx-allinoneconf)) |
| 500 | jenkins | 18080, 50001 | Jenkins controller |

**컨테이너 내부에 없는 프로세스**: `ollama`, `jenkins-agent` — 호스트에서 실행.

**주요 env (dify-api)**: `DB_HOST=127.0.0.1`, `REDIS_HOST=127.0.0.1`, `PLUGIN_DAEMON_URL=http://127.0.0.1:5002`, `SERVER_WORKER_CONNECTIONS=1000`, `GUNICORN_TIMEOUT=360`.

**수정이 필요할 때**: 서비스 추가/제거, gunicorn 튜닝, 환경변수 변경.

### 2.7 `nginx-allinone.conf`

**목적**: 컨테이너 외부 포트 **:18081** 로 들어온 요청을 Dify web / api 로 프록시.

**실행 시점**: `[program:nginx]` 가 시작 시 로드.

**라우팅 규칙**:

- `/console/api/*`, `/api/*`, `/v1/*`, `/files/*` → `http://127.0.0.1:5001` (dify-api)
- 그 외 (`/install`, `/apps`, ...) → `http://127.0.0.1:3000` (dify-web)

**수정이 필요할 때**: Dify 가 새로운 API prefix 를 추가하거나 CORS 설정을 바꿀 때.

### 2.8 `pg-init-allinone.sh`

**목적**: 빌드 타임에 PostgreSQL 초기화 (initdb + Dify DB 5 개 사전 생성) 를 미리 수행해 `/opt/seed/pg/` 에 박아두기.

**실행 시점**: Dockerfile.allinone 의 빌드 단계. 런타임에는 실행 안 됨.

**왜 빌드 타임에?** 런타임에 initdb + 5 개 DB 생성 + extension 로드까지 하면 첫 기동에 5-10분 추가됨. 빌드 타임에 한 번 만들어 둔 걸 첫 기동에 `/opt/seed/pg/` → `/data/pg/` 로 복사만 하면 끝.

**수정이 필요할 때**: Dify 가 요구하는 DB 목록/이름/extension 이 바뀔 때 (major 업그레이드).

### 2.9 `requirements-allinone.txt`

**목적**: 컨테이너의 Dify api / worker 전용 Python 의존성 (pinned).

**실행 시점**: Dockerfile.allinone 의 `pip install -r requirements-allinone.txt` 단계 (빌드 타임). Dify 애플리케이션 코드 (`/opt/dify-api/api/`) 의 `.venv` 에 설치된다.

**왜 별도 파일?** Dify 공식 이미지에서 애플리케이션 코드 (`/opt/dify-*`) 는 복사하지만 `.venv` 는 아키텍처/glibc 차이로 재사용 불가 → 이 파일로 해당 환경의 네이티브 wheel 을 재설치.

**수정이 필요할 때**: Dify 버전업 시 Dify 공식 repo 의 `api/requirements.txt` (또는 `pyproject.toml`) 로부터 재생성.

### 2.10 seed 자원 (빌드 시점에 이미지로 들어가는 파일들)

참고용 — 이 파일들은 `offline/` 에 있지 않고 저장소 루트 또는 빌드 산출물:

| 경로 | 역할 | 빌드 시점에 이미지 어디로 |
|------|------|----------------------------|
| `DSCORE-ZeroTouch-QA-Docker.jenkinsPipeline` (루트) | Jenkins Pipeline DSL (`agent { label 'mac-ui-tester' }` + `zero_touch_qa` 실행) | `/opt/DSCORE-ZeroTouch-QA-Docker.jenkinsPipeline` |
| `dify-chatflow.yaml` (루트) | Dify App `ZeroTouch QA Brain` 의 Chatflow DSL | `/opt/dify-chatflow.yaml` |
| `offline/jenkins-plugins/*.hpi` | `build-allinone.sh [1/4]` 가 수집한 Jenkins 플러그인 | `/usr/share/jenkins/ref/plugins/` |
| `offline/dify-plugins/*.difypkg` | `build-allinone.sh [2/4]` 가 받은 Dify Ollama 플러그인 | `/opt/seed/dify-plugins/` |

---

## 3. 운영 가이드

### 3.1 재시작 / 중지 / 제거

```bash
# 컨테이너만 재시작 (상태 유지)
docker restart dscore.ttc.playwright             # 30-60초

# 일시 중지
docker stop dscore.ttc.playwright

# 컨테이너 제거 (볼륨 유지)
docker rm -f dscore.ttc.playwright

# 완전 초기화 (주의 — Dify DB / Jenkins config 다 사라짐)
docker volume rm dscore-data
```

**호스트 agent 정리** (Mac / WSL 공통):

```bash
# agent 터미널에서 Ctrl+C 후
rm -rf ~/.dscore.ttc.playwright-agent
```

### 3.2 호스트 agent 재연결

컨테이너를 재시작하면 JNLP 세션이 끊기므로 agent 도 다시 연결해야 한다. 같은 스크립트를 그냥 다시 실행하면 된다 (step 0-A 가 이전 agent 정리, 0-B 가 새 NODE_SECRET 자동 추출):

```bash
./offline/mac-agent-setup.sh       # Mac
./offline/wsl-agent-setup.sh       # WSL2
```

### 3.3 로그

```bash
# 컨테이너 내부 로그 (서비스별 분리)
docker exec dscore.ttc.playwright tail -f /data/logs/dify-api.log
docker exec dscore.ttc.playwright tail -f /data/logs/jenkins.log
docker exec dscore.ttc.playwright supervisorctl status

# 호스트 agent 로그 (--redeploy 로 기동한 경우)
tail -f /tmp/dscore-agent.log
```

### 3.4 백업 / 복원 / 업그레이드

**백업**:

```bash
docker run --rm -v dscore-data:/data -v $PWD:/backup busybox \
  tar czf /backup/dscore-data-$(date +%Y%m%d).tar.gz /data
```

**복원**:

```bash
docker stop dscore.ttc.playwright
docker run --rm -v dscore-data:/data -v $PWD:/backup busybox \
  tar xzf /backup/dscore-data-YYYYMMDD.tar.gz -C /
docker start dscore.ttc.playwright
```

**업그레이드** (새 tar.gz 를 받은 경우):

```bash
docker stop dscore.ttc.playwright
docker rm dscore.ttc.playwright
docker load -i dscore.ttc.playwright-new.tar.gz
# Step 2 의 docker run 과 동일 옵션으로 재기동
# Step 3 의 agent-setup 재실행
```

호스트 `~/.dscore.ttc.playwright-agent` 는 백업 불필요 — agent-setup 재실행으로 복구됨.

### 3.5 프로비저닝 체크리스트

프로비저닝이 제대로 됐는지 항목별로 확인:

| # | 항목 | 확인 명령 |
|---|------|-----------|
| 1 | Dify 관리자 | `curl -fsS http://localhost:18081/console/api/setup \| jq .setup_status` → `"finished"` |
| 2 | Dify Ollama 플러그인 | `docker exec dscore.ttc.playwright ls /data/dify/plugins/packages` 에 `langgenius-ollama-*` |
| 3 | Ollama 모델 등록 (host URL) | DB 조회로 `base_url: host.docker.internal:11434` |
| 4 | Chatflow | Dify UI 에 `DSCORE-ZeroTouch-QA` 앱 |
| 5 | Dify API Key | `docker logs dscore.ttc.playwright \| grep "API Key 발급 완료"` |
| 6 | Jenkins 플러그인 4 개 | UI `/pluginManager/` 또는 `curl … /pluginManager/api/json?depth=1` |
| 7 | Jenkins Credentials | `curl -u admin:pw … /credentials/store/system/domain/_/api/json` 에 `dify-qa-api-token` |
| 8 | Pipeline Job | Dashboard 에 `DSCORE-ZeroTouch-QA-Docker` |
| 9 | **Node online** | `curl -u admin:pw … /computer/mac-ui-tester/api/json \| jq .offline` → `false` (Step 3 이후) |

### 3.6 관리자 비밀번호 변경

| 상황 | 방법 |
|------|------|
| 첫 배포 직전 | docker run 에 env 주입: `-e JENKINS_ADMIN_PW='<pw>' -e DIFY_PASSWORD='<pw>'` (`.initialized` 플래그가 없을 때만 적용) |
| 운영 중 Jenkins | UI → People → `admin` → Configure → Password |
| 운영 중 Dify | 우상단 계정 → Settings → Account → Password |

### 3.7 Ollama 모델 관리 (호스트)

컨테이너에는 Ollama 가 없으니 **모든 모델 작업은 호스트의 `ollama` 명령**.

```bash
# 현재 상태 (Mac / Windows 동일)
ollama list
ollama show gemma4:e4b
```

**새 모델로 교체**:

```bash
# 1) 호스트에서 pull
ollama pull llama3.1:8b

# 2) 컨테이너 재생성 — 새 OLLAMA_MODEL 로
docker rm -f dscore.ttc.playwright
docker run -d --name dscore.ttc.playwright ... -e OLLAMA_MODEL=llama3.1:8b ... dscore.ttc.playwright:latest

# 3) 강제 재프로비저닝
docker exec dscore.ttc.playwright rm -f /data/.app_provisioned
docker restart dscore.ttc.playwright

# 4) agent 재연결
./offline/mac-agent-setup.sh       # 또는 wsl-agent-setup.sh
```

**Chatflow 에서 사용 모델 변경**: Dify `DSCORE-ZeroTouch-QA` 앱의 LLM 노드 (Planner / Healer) 에서 Model 드롭다운 → Publish. provider 등록만으로는 Chatflow DSL 이 갱신되지 않는다.

**모델 선택 기준**:

| 모델 | 크기 | 호스트 RAM | 용도 |
|------|------|------------|------|
| `gemma4:e4b` | ~4GB | 6-8GB | 기본 — 빠름 |
| `llama3.1:8b` | ~4.7GB | 8-10GB | 품질↑ |
| `qwen2.5:7b` | ~4.4GB | 8-10GB | 다국어 |
| `gemma2:2b` | ~1.5GB | 4GB | 저사양 |

**Ollama 런타임 튜닝** (영구 상주 / 동시 로드):

Mac:

```bash
brew services stop ollama
launchctl setenv OLLAMA_KEEP_ALIVE -1
launchctl setenv OLLAMA_MAX_LOADED_MODELS 2
brew services start ollama
```

Windows (PowerShell):

```powershell
[Environment]::SetEnvironmentVariable('OLLAMA_KEEP_ALIVE', '-1', 'User')
[Environment]::SetEnvironmentVariable('OLLAMA_MAX_LOADED_MODELS', '2', 'User')
Get-Process -Name 'ollama*' -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Process "$env:LOCALAPPDATA\Programs\Ollama\ollama app.exe"
```

---

## 4. 트러블슈팅

### 4.1 컨테이너가 기동 직후 죽는다

```bash
docker logs dscore.ttc.playwright | tail -50
```

- 메모리 부족 → Docker Desktop 메모리 할당 ↑
- PG init 실패 → `/data/logs/postgresql.err.log`. 권한 문제면 `docker volume rm dscore-data` 후 재기동

### 4.2 Pipeline 이 `'mac-ui-tester' is offline` 에서 대기

```bash
# Node 상태 확인
curl -sS -u admin:password http://localhost:18080/computer/mac-ui-tester/api/json | grep -oE '"offline":(true|false)'
```

**원인 A**: 호스트 agent 미연결 → `./offline/<mac|wsl>-agent-setup.sh` 재실행.

**원인 B**: 이전 agent 프로세스가 좀비로 남음. 스크립트 step 0-A 가 자동 정리하므로 그냥 다시 실행하면 해결.

**원인 C**: JNLP 포트 50001 방화벽.

- Mac: `lsof -i :50001`, `nc 127.0.0.1 50001`
- WSL2: `ss -tlnp | grep 50001`. Windows Defender 방화벽이 차단 시 관리자 PowerShell 에서 규칙 추가

### 4.3 agent 기동 시 `Cannot open display` / Chromium `Page crashed!`

**Mac**:
- `Cannot open display` → `unset DISPLAY` 후 재시도 (XQuartz 잔재 env 때문)
- `Page crashed!` → macOS "확인 없이 열기" 권한 (시스템 설정 → 보안)

**Windows 11 / WSL2**:
- `Cannot open display` → WSLg 비활성. 관리자 PowerShell `wsl --update` → `wsl --shutdown` → 재기동. WSL 안에서 `echo $DISPLAY $WAYLAND_DISPLAY` 로 확인
- `Page crashed!` → `/dev/shm` 공유메모리 부족. `sudo mount -o remount,size=2G /dev/shm` (영구: `/etc/fstab` 에 `tmpfs /dev/shm tmpfs defaults,size=2g 0 0`)
- Playwright 의존 deb 누락:
  ```bash
  ~/.dscore.ttc.playwright-agent/venv/bin/python -m playwright install-deps chromium
  ```

### 4.4 Pipeline Stage 3 `Dify /v1/chat-messages` 400 또는 timeout

3 대 원인:

1. **호스트 Ollama 미기동** — `ollama list` / `curl http://127.0.0.1:11434/api/tags` 확인 후:
    - Mac: `brew services start ollama`
    - Windows: 트레이 앱 재기동 또는 `Start-Process ollama -ArgumentList 'serve'`
2. **모델 이름 불일치** — `OLLAMA_MODEL` env 값 vs `ollama list` NAME 비교
3. **`--add-host` 누락** — `docker exec dscore.ttc.playwright curl -fsS http://host.docker.internal:11434/api/tags` 실패 시 docker run 에 `--add-host host.docker.internal:host-gateway` 재추가

### 4.5 Dify 가 옛 base_url 로 호출 (`Connection refused`)

provision-apps.sh 재실행으로 credential swap + Redis FLUSH + 재기동 연쇄 자동 수행:

```bash
docker exec dscore.ttc.playwright bash /opt/provision-apps.sh
```

완전 재프로비저닝:

```bash
docker exec dscore.ttc.playwright rm -f /data/.app_provisioned
docker restart dscore.ttc.playwright
```

### 4.6 agent-setup 이 `JDK 21 미설치` 로 중단

Mac:

```bash
brew install --cask temurin@21
# 또는
brew install openjdk@21
sudo ln -sfn /opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk \
  /Library/Java/JavaVirtualMachines/openjdk-21.jdk
```

WSL2 Ubuntu:

```bash
sudo apt update && sudo apt install -y openjdk-21-jdk-headless
# 여러 JDK 가 있어 update-alternatives 가 다른 버전을 가리키면:
sudo update-alternatives --config java    # 21 선택
```

### 4.7 Playwright Chromium 설치 실패

Mac:

```bash
source ~/.dscore.ttc.playwright-agent/venv/bin/activate
python -m playwright install chromium --force
```

WSL2 Ubuntu:

```bash
~/.dscore.ttc.playwright-agent/venv/bin/python -m playwright install chromium --force
~/.dscore.ttc.playwright-agent/venv/bin/python -m playwright install-deps chromium
```

### 4.8 이미지가 너무 작다 (2-3GB)

아키텍처 불일치 시 qemu 크로스 빌드 silent-fail. `build-allinone.sh` 는 `uname -m` 자동 감지하지만 실수로 `TARGET_PLATFORM` override 했다면:

```bash
docker rmi dscore.ttc.playwright:latest
./offline/build-allinone.sh         # 자동 감지
docker image inspect dscore.ttc.playwright:latest --format '{{.Architecture}}'
```

### 4.9 Windows 11: Ollama 가 CPU 로 추론 (1-3 tok/s)

```powershell
# GPU 로드 로그
Get-Content "$env:LOCALAPPDATA\Ollama\server.log" -Tail 100 | Select-String 'GPU|CUDA|offload'
# "offloaded N/N layers to GPU" 가 비(非)-0 이어야 GPU 사용

# VRAM 점유 확인
nvidia-smi
```

체크:

1. NVIDIA 드라이버 최신
2. `winget upgrade Ollama.Ollama`
3. 다른 프로세스 VRAM 점유 중이면 닫기
4. VRAM 용량 — gemma4:e4b 는 5-6GB 소비, RTX 30/40 8GB+ 권장

### 4.10 Windows 11: WSL2 메모리 부족 빌드 OOM

`%USERPROFILE%\.wslconfig`:

```ini
[wsl2]
memory=16GB
processors=8
swap=8GB
```

관리자 PowerShell 에서 `wsl --shutdown` → WSL 재기동.

### 4.11 시계 오류로 Dify 로그인 `Invalid encrypted data`

Mac:

```bash
sudo sntp -sS time.apple.com
docker restart dscore.ttc.playwright
```

Windows 11 / WSL2:

```powershell
# 관리자 PowerShell
w32tm /resync
```

```bash
# WSL 안에서
sudo hwclock -s
docker restart dscore.ttc.playwright
```

### 4.12 Windows: WSL2 오프라인 설치 (폐쇄망)

온라인 머신에서 미리 수집:

1. **WSL 커널 MSI** — `https://learn.microsoft.com/windows/wsl/install-manual` 의 "WSL2 Linux 커널 업데이트 패키지"
2. **Ubuntu rootfs** — `https://cloud-images.ubuntu.com/wsl/jammy/current/` 의 `ubuntu-jammy-wsl-amd64-ubuntu.rootfs.tar.gz`
3. 본 번들 `dscore.ttc.playwright-*.tar.gz` + `e2e-pipeline/offline/` 전체 + `apt-get download` 로 수집한 의존 deb (openjdk-21, python3.12 등)

폐쇄망 Windows 11 관리자 PowerShell:

```powershell
# (1) WSL / VirtualMachinePlatform 기능 활성 — 재부팅
dism /online /enable-feature /featurename:Microsoft-Windows-Subsystem-Linux /all /norestart
dism /online /enable-feature /featurename:VirtualMachinePlatform /all /norestart
Restart-Computer

# (2) WSL 커널 MSI 설치
msiexec /i D:\Usb\wsl_update_x64.msi /quiet

# (3) 기본 WSL 버전 2
wsl --set-default-version 2

# (4) Ubuntu tarball 을 import
mkdir C:\WSL\Ubuntu
wsl --import Ubuntu C:\WSL\Ubuntu D:\Usb\ubuntu-jammy-wsl-amd64-ubuntu.rootfs.tar.gz --version 2

# (5) 기본 사용자 설정
wsl -d Ubuntu -- bash -c "useradd -m -G sudo -s /bin/bash <사용자명> && passwd <사용자명>"
wsl --manage Ubuntu --set-default-user <사용자명>
```

이후 WSL Ubuntu 셸 진입 → 의존성 deb 로컬 설치 → 본 README §1.2 Step 1 부터 진행.

---

## 부록 A. 토폴로지 / 볼륨 구조

### 프로세스 토폴로지

```
supervisord (PID 1, tini 위, 10 개 프로그램)
├─ postgresql (:5432)
├─ redis (:6379)
├─ qdrant (:6333)
├─ dify-plugin-daemon (:5002)
├─ dify-api (:5001)
├─ dify-worker / dify-worker-beat (Celery)
├─ dify-web (:3000)
├─ nginx (:18081 → dify-api / dify-web)
└─ jenkins (:18080, :50001 JNLP)

호스트 (Mac 또는 WSL2 Ubuntu)
├─ ollama (:11434)
│   ├─ Mac:          macOS 네이티브 — Metal 가속
│   └─ Windows 11:   Windows 네이티브 — CUDA 가속 (Docker Desktop 포워딩)
└─ jenkins agent (JDK 21, Node 레이블 mac-ui-tester)
   └─ Playwright Chromium (headed)
      ├─ Mac:        macOS 네이티브 창
      └─ Windows 11: WSLg 경유 Windows 데스크탑 창
```

### 볼륨 구조

컨테이너 볼륨 `dscore-data` → `/data/`:

```
/data/
├── .initialized               # seed 완료 플래그
├── .app_provisioned           # provision 완료 플래그
├── pg/                        # PostgreSQL 데이터
├── redis/                     # Redis AOF
├── qdrant/                    # Qdrant 벡터 스토어
├── jenkins/                   # JENKINS_HOME (plugins / jobs / credentials / nodes)
├── dify/                      # storage + plugins/packages
└── logs/                      # 서비스별 로그
```

호스트 agent 디렉토리 (Mac / WSL 공통 — `$HOME` 하위):

```
~/.dscore.ttc.playwright-agent/
├── venv/                      # Python 3.11+ + playwright
├── agent.jar                  # Jenkins remoting
├── run-agent.sh               # agent 기동 스크립트
└── workspace/DSCORE-ZeroTouch-QA-Docker/
    └── .qa_home/
        ├── venv → (symlink)
        └── artifacts/
```

### 외부 포트 (호스트 관점)

| 포트 | 컨테이너 내부 | 역할 |
|------|---------------|------|
| 18080 | jenkins :18080 | Jenkins UI + REST |
| 18081 | nginx :18081 | Dify (web + api 프록시) |
| 50001 | jenkins :50001 | JNLP — 호스트 agent 접속용 |

### 외부 네트워크 도메인 (빌드 타임 화이트리스트)

빌드 시점에만 필요 — 런타임은 외부 접근 없음:

| 도메인 | 용도 |
|--------|------|
| `updates.jenkins.io`, `get.jenkins.io`, `mirrors.jenkins.io` | Jenkins 플러그인 |
| `marketplace.dify.ai` | Dify 플러그인 |
| `github.com`, `objects.githubusercontent.com` | jenkins-plugin-manager.jar, qdrant |
| `registry-1.docker.io`, `auth.docker.io` | Docker Hub |
| `pypi.org`, `files.pythonhosted.org` | Python 패키지 |
| `playwright.azureedge.net` | Chromium (빌드 + agent-setup 양쪽) |
| `apt.postgresql.org`, `deb.debian.org` | OS 패키지 |
