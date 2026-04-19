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

## 전체 흐름 한눈에 보기

두 개 머신에서 각각 아래 명령만 따라하면 된다. 자세한 설명/옵션/트러블슈팅은 뒤 섹션 참조.

### A. 온라인 빌드 머신 (인터넷 가능)

```bash
# 이 저장소를 클론한 곳
cd e2e-pipeline

# 1) 빌드 (30-90분)
./offline/build-allinone.sh 2>&1 | tee /tmp/build.log

# 2) 배포 파일 확인
ls -lh dscore.ttc.playwright-*.tar.gz
sha256sum dscore.ttc.playwright-*.tar.gz   # 이 값을 메모해 폐쇄망에서 검증

# 3) USB / 사내망 / scp 로 dscore.ttc.playwright-*.tar.gz 를 폐쇄망 서버로 이동
```

**산출물은 tar.gz 하나만**. `offline/` 폴더 전체를 옮길 필요 없다.

### B. 폐쇄망 서버 (인터넷 없음)

```bash
# 1) 무결성 검증
sha256sum dscore.ttc.playwright-*.tar.gz   # 위 A-2 의 값과 일치?

# 2) 이미지 로드
docker load -i dscore.ttc.playwright-*.tar.gz

# 3) 기동 (첫 실행 시 5-10분 소요)
docker run -d --name dscore.ttc.playwright \
  -p 18080:18080 -p 18081:18081 -p 50001:50001 \
  -v dscore-data:/data \
  --add-host host.docker.internal:host-gateway \
  --restart unless-stopped \
  dscore.ttc.playwright:latest

# 4) 기동 진행 관찰 (종료: Ctrl+C — 컨테이너는 계속 돌아감)
docker logs -f dscore.ttc.playwright

# 5) 로그에 `[▶] === 프로비저닝 완료 ===` 찍히면 브라우저 접속
# - http://<서버IP>:18080   Jenkins  (admin / password)
# - http://<서버IP>:18081   Dify     (admin@example.com / Admin1234!)
```

**이게 전부다.** 이후 Jenkins Dashboard → `DSCORE-ZeroTouch-QA-Docker` 빌드로 시나리오 검증 ([2.9](#29-시나리오-검증) 참조).

> **재시작은 항상 같은 볼륨으로** — `-v dscore-data:/data` 를 빼먹으면 4GB seed 복사 + 10분 프로비저닝을 다시 한다.

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

### 1.2 빌드 실행

#### (a) 기본 빌드 — 옵션 없이

대부분의 경우 이 한 줄이면 된다. 폐쇄망 타겟이 **일반 Linux 서버(x86_64)** 이고 **기본 LLM(`gemma4:e4b`)** 을 쓸 계획이라면 환경변수를 건드리지 않아도 된다.

```bash
cd e2e-pipeline
./offline/build-allinone.sh 2>&1 | tee /tmp/build-allinone.log
```

- `2>&1 | tee /tmp/build-allinone.log` — 콘솔에 출력을 유지하면서 로그 파일로도 떠둔다. 실패 시 `grep -E 'ERROR|FAIL' /tmp/build-allinone.log` 로 어느 단계에서 멈췄는지 바로 찾을 수 있다.

#### (b) 옵션 — 환경변수 5개

| 변수 | 기본값 | 언제 바꿔야 하나 |
| ---- | ------ | --------------- |
| `TARGET_PLATFORM` | `linux/amd64` | **폐쇄망 서버가 ARM64** (예: AWS Graviton, 라즈베리파이 서버, Apple Silicon 기반 Mac mini 서버) 일 때만 `linux/arm64` 로 바꾼다. 일반 Intel/AMD 서버는 건드리지 말 것. |
| `OLLAMA_MODEL` | `gemma4:e4b` | 다른 LLM 을 쓰고 싶을 때 (예: `llama3.1:8b`, `qwen2.5:7b`). **주의**: 이미지가 ~4-6GB 더 커진다. |
| `IMAGE_TAG` | `dscore.ttc.playwright:latest` | 동시에 여러 버전을 빌드·구분하고 싶을 때 (예: `dscore.ttc.playwright:v2`). 기본값이면 기존 이미지가 덮어쓰기됨. |
| `OUTPUT_TAR` | `dscore.ttc.playwright-<timestamp>.tar.gz` | 전달할 파일명을 직접 지정하고 싶을 때 (예: `dscore.ttc.playwright-2026-04-19.tar.gz`). |
| `JENKINS_VERSION` | (자동 감지) | 거의 건드릴 필요 없음. Jenkins 플러그인이 특정 버전과만 호환되는 특수 상황에서만 `2.479.3` 같이 고정. |

#### 환경변수 넣는 법 (Bash/Zsh 문법)

환경변수는 **명령 실행 1회만 적용** 하는 것이 원칙. 두 가지 방법이 있다.

**(방법 1) 명령 앞에 `VAR=VALUE` 로 붙이기 — 권장**

```bash
TARGET_PLATFORM=linux/arm64 ./offline/build-allinone.sh
```

- 이 빌드 1회만 `TARGET_PLATFORM=linux/arm64`. 끝나면 자동 원복.
- 여러 변수 동시 지정 — 공백으로 나열:

  ```bash
  TARGET_PLATFORM=linux/arm64 OLLAMA_MODEL=llama3.1:8b ./offline/build-allinone.sh
  ```

**(방법 2) 터미널에 `export` — 연속 빌드 시**

```bash
export TARGET_PLATFORM=linux/arm64
export OLLAMA_MODEL=llama3.1:8b

./offline/build-allinone.sh          # 첫 빌드
# (옵션) requirements 변경 후 다시
./offline/build-allinone.sh          # 두 번째 빌드 — 동일 env 로
```

- 터미널 세션을 닫거나 `unset TARGET_PLATFORM` 하기 전까지 유지된다.
- 끌 때: `unset TARGET_PLATFORM OLLAMA_MODEL`

> 공백 없이 `VAR=VALUE` (등호 앞뒤 공백 X). `VAR = VALUE` 는 오류.

#### 실전 시나리오별 커맨드

##### (1) 폐쇄망 서버가 ARM64 (AWS Graviton, Apple Silicon Mac mini 등)

```bash
TARGET_PLATFORM=linux/arm64 ./offline/build-allinone.sh 2>&1 | tee /tmp/build-allinone.log
```

##### (2) 모델을 Llama 3.1 8B 로 바꾸고 싶을 때

```bash
OLLAMA_MODEL=llama3.1:8b ./offline/build-allinone.sh 2>&1 | tee /tmp/build-allinone.log
```

##### (3) 모델도 바꾸고 ARM64 도 타겟일 때 (동시 지정)

```bash
TARGET_PLATFORM=linux/arm64 OLLAMA_MODEL=qwen2.5:7b ./offline/build-allinone.sh 2>&1 | tee /tmp/build-allinone.log
```

##### (4) 날짜 붙은 tar 파일명으로 산출하고 싶을 때

```bash
OUTPUT_TAR=dscore.ttc.playwright-2026-04-19-prod.tar.gz ./offline/build-allinone.sh 2>&1 | tee /tmp/build-allinone.log
```

##### (5) 이미지 태그를 버전으로 구분해 여러 번 빌드 (이전 이미지 유지)

```bash
# v1 빌드
IMAGE_TAG=dscore.ttc.playwright:v1 OUTPUT_TAR=dscore.ttc.playwright-v1.tar.gz ./offline/build-allinone.sh

# 이후 v2 빌드 (v1 이미지는 그대로 남음)
IMAGE_TAG=dscore.ttc.playwright:v2 OUTPUT_TAR=dscore.ttc.playwright-v2.tar.gz ./offline/build-allinone.sh

# 확인
docker images | grep dscore.ttc.playwright
# dscore.ttc.playwright  v1       ...  10GB
# dscore.ttc.playwright  v2       ...  10GB
```

##### (6) 한 셸 세션에서 연속 빌드 (export 방식)

```bash
export TARGET_PLATFORM=linux/arm64
export OLLAMA_MODEL=llama3.1:8b

cd e2e-pipeline
./offline/build-allinone.sh 2>&1 | tee /tmp/build-1.log

# 문제 수정 후 재빌드
./offline/build-allinone.sh 2>&1 | tee /tmp/build-2.log

# 작업 완료 후 정리
unset TARGET_PLATFORM OLLAMA_MODEL
```

#### 빌드 직후 적용된 값 확인

빌드 시작 시 다음 라인이 로그 맨 앞부분에 찍힌다 — 내가 준 옵션이 제대로 들어갔는지 눈으로 확인하라.

```
[build-allinone] 빌드 대상: dscore.ttc.playwright:latest (platform=linux/arm64)
[build-allinone] 출력 파일: dscore.ttc.playwright-20260419-143000.tar.gz
[build-allinone]   대상 Jenkins 버전: 2.479.3
```

변수를 지정했는데 로그에 반영되지 않았다면 shell 문법 오류 가능성 (`VAR = VALUE` 형태 등). Ctrl+C 로 중단 후 다시 입력한다.

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
docker save dscore.ttc.playwright:latest | gzip -1 > dscore.ttc.playwright-<timestamp>.tar.gz
```

**왜 `gzip -1` 인가**: 최저 압축률(빠른 압축). docker layer 는 이미 압축돼 있어 추가 압축 효과가 작은데, 시간 비용은 크다. 1GB 정도 줄이는 게 목표.

**예상 크기**:

- 비압축 이미지: 9-12GB (`docker images dscore.ttc.playwright:latest` 의 SIZE)
- 압축 후: 5-9GB (모델/플러그인은 이미 압축돼 있어 절감 폭 작음)
- 압축 시간: 2-5분 (CPU 의존)

### 1.4 빌드 검증 (배포 전 필수)

```bash
# 1) 이미지 존재 + 크기 확인
docker images dscore.ttc.playwright:latest
# 예상: dscore.ttc.playwright:latest  <hash>  <date>  ~10GB

# 2) tar.gz 크기 확인
ls -lh dscore.ttc.playwright-*.tar.gz

# 3) seed 파일 무결성 검사 (옵션 — 이미지 내부 점검)
docker run --rm --entrypoint bash dscore.ttc.playwright:latest -lc '
  echo "=== Ollama 모델 ===" && du -sh /opt/seed/ollama
  echo "=== Jenkins 플러그인 ===" && ls /opt/seed/jenkins-plugins | wc -l
  echo "=== Dify 플러그인 ===" && ls /opt/seed/dify-plugins
  echo "=== PG snapshot ===" && du -sh /opt/seed/pg
  echo "=== Qdrant 바이너리 ===" && qdrant --version
  echo "=== Ollama 바이너리 ===" && ollama --version
'

# 4) 스모크 테스트 — 임시로 띄워서 5분 후 헬스체크 (선택)
docker run -d --name smoke -p 18080:18080 -p 18081:18081 -v smoke-data:/data dscore.ttc.playwright:latest
sleep 300  # 첫 기동 5분 대기
curl -fsS http://localhost:18080/login | grep -q Jenkins && echo "Jenkins OK"
curl -fsS http://localhost:18081/install | grep -q Dify && echo "Dify OK"
docker rm -f smoke && docker volume rm smoke-data
```

### 1.5 산출물 위치 정리

```
e2e-pipeline/
├── dscore.ttc.playwright-<timestamp>.tar.gz   ← 폐쇄망으로 이동할 파일 (유일)
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

## 2. 폐쇄망 타겟에서 기동 → 검증

cold-start 운영자가 **파일 수령 → docker run → 타임라인 관측 → 체크리스트 → 부분 실패 진단 → 시나리오 실행** 까지 끊김 없이 따라갈 수 있도록 시간순으로 구성했다. 각 서브섹션은 앞 단계 완료를 전제한다.

### 2.1 요구사항

| 항목 | 최소 | 권장 |
| ---- | ---- | ---- |
| Docker | 26.0 | 26.x 이상 |
| 디스크 여유 | 30GB | 50GB |
| 메모리 | 16GB | 32GB (Ollama + Dify + Jenkins 동시 가동) |
| CPU | 4 코어 | 8 코어 |

### 2.2 이미지 수령 및 로드

```bash
# (1) 빌드 머신에서 산출된 tar.gz 를 USB/사내망으로 폐쇄망에 전달
cp /home/user/dscore.ttc.playwright-20260419-143000.tar.gz .

# (2) 무결성 검증 (빌드 머신의 sha256 과 비교)
sha256sum dscore.ttc.playwright-*.tar.gz
# 예상: e3f2... dscore.ttc.playwright-20260419-143000.tar.gz

# (3) 이미지 로드
docker load -i dscore.ttc.playwright-20260419-143000.tar.gz
# Loaded image: dscore.ttc.playwright:latest

docker images dscore.ttc.playwright:latest
# REPOSITORY    TAG         IMAGE ID       CREATED        SIZE
# dscore.ttc.playwright     allinone    <hash>         <date>         ~10GB
```

### 2.3 기동

두 가지 경로 중 하나를 선택한다.

#### (a) 기본 PoC — 빠른 확인용

```bash
docker run -d --name dscore.ttc.playwright \
  -p 18080:18080 -p 18081:18081 -p 50001:50001 \
  -v dscore-data:/data \
  --add-host host.docker.internal:host-gateway \
  --restart unless-stopped \
  dscore.ttc.playwright:latest
```

기본 자격증명:

- Jenkins: `admin` / `password`
- Dify: `admin@example.com` / `Admin1234!`

#### (b) 운영 권장 — 첫 기동 전 비밀번호 주입

```bash
docker run -d --name dscore.ttc.playwright \
  -p 18080:18080 -p 18081:18081 -p 50001:50001 \
  -v dscore-data:/data \
  -e JENKINS_ADMIN_USER=admin \
  -e JENKINS_ADMIN_PW='<strong-password-1>' \
  -e DIFY_EMAIL=admin@corp.example \
  -e DIFY_PASSWORD='<strong-password-2>' \
  --add-host host.docker.internal:host-gateway \
  --restart unless-stopped \
  dscore.ttc.playwright:latest
```

- [basic-security.groovy](../jenkins-init/basic-security.groovy) 가 `JENKINS_ADMIN_USER` / `JENKINS_ADMIN_PW` 를 읽어 Jenkins 초기 계정 생성
- [provision-apps.sh:37-40](provision-apps.sh#L37-L40) 가 동일 env 로 Dify 관리자 생성 및 로그인 수행
- **주의**: env 는 `/data` 볼륨이 비어 있는 **첫 기동에서만** 적용된다. 이미 `/data/.initialized` 플래그가 있으면 무시되고 기존 계정이 유지됨 → 그 경우 [2.8 관리자 비밀번호 변경](#28-관리자-비밀번호-변경) 의 UI 경로를 사용한다.

#### (c) Windows 11 WSL2 + NVIDIA GPU — GPU 가속 모드

Linux 컨테이너 안 Ollama 가 **Windows 호스트의 NVIDIA GPU** 를 직접 사용하도록
하는 경로. WSL2 2.0+ 가 CUDA 드라이버를 컨테이너에 노출하기 때문에 가능하며,
Mac Docker Desktop 과 달리 호스트에 별도 Ollama 를 설치할 필요가 없다.

**(c-1) 호스트 전제조건 (한 번만 설정)**:

- Windows 11 + NVIDIA GPU (RTX 20/30/40 시리즈, **VRAM 8GB 이상** 권장 — gemma4:e4b 는 5-6GB 소비)
- [NVIDIA Windows 드라이버](https://www.nvidia.com/Download/index.aspx) 최신 (WSL2 CUDA 지원 포함, 2024-01 이후 버전)
- Docker Desktop 4.30+ (WSL2 백엔드 활성, NVIDIA Container Toolkit 자동 내장)
- `.wslconfig` 메모리 할당 — Windows 사용자 홈 (`C:\Users\<user>\.wslconfig`, 없으면 신규 생성):

  ```ini
  [wsl2]
  memory=24GB
  processors=8
  swap=8GB
  ```

  설정 적용: PowerShell 관리자에서 `wsl --shutdown` → Docker Desktop 재기동.

- **GPU 가용성 사전 검증** (실패하면 아래 `docker run` 도 실패):

  ```bash
  docker run --rm --gpus all nvidia/cuda:12.0.0-base-ubuntu22.04 nvidia-smi
  # 예상: GPU 모델, 드라이버 버전 표, 메모리 용량이 출력되어야 함
  ```

  이 명령이 실패하면 드라이버/Docker Desktop/NVIDIA Container Toolkit 설정 문제 —
  [트러블슈팅 "WSL2 컨테이너에서 nvidia-smi 가 안 보임"](#wsl2-컨테이너에서-nvidia-smi-가-안-보임) 참조.

**(c-2) docker run — GPU 옵션 2 개 필수**:

```bash
docker run -d --name dscore.ttc.playwright \
  -p 18080:18080 -p 18081:18081 -p 50001:50001 \
  -v dscore-data:/data \
  --add-host host.docker.internal:host-gateway \
  --gpus all \
  --shm-size=2g \
  --restart unless-stopped \
  dscore.ttc.playwright:latest
```

| 옵션 | 역할 |
| ---- | ---- |
| `--gpus all` | **GPU 가속 핵심**. NVIDIA Container Toolkit 이 `/dev/nvidia*` 디바이스와 `libnvidia-*.so` 라이브러리를 컨테이너에 주입한다. 누락 시 Ollama 가 CPU 모드로 떨어져 Mac 동일한 성능 저하 (타임아웃 빈발). |
| `--shm-size=2g` | Playwright Chromium 이 `/dev/shm` 을 공유 메모리로 사용. Docker 기본 64MB 는 Chromium 렌더러 크래시(`Page crashed!`) 를 유발하므로 2GB 로 상향. |

**(c-3) 검증**:

```bash
# (1) 컨테이너가 GPU 에 도달하는지
docker exec dscore.ttc.playwright nvidia-smi
# 예상: Windows 호스트의 GPU 정보가 그대로 보임

# (2) Ollama 가 GPU 로 레이어를 오프로드하는지 (모델 로드 후 30초 대기 후)
docker exec dscore.ttc.playwright bash -c 'sleep 30 && grep -E "offloaded [^0/]" /data/logs/ollama.log | head -3'
# 예상: "offloaded 43/43 layers to GPU" 같은 비(非)-0 offload 라인.
# "offloaded 0/43 layers to GPU" 가 보이면 CPU 모드로 떨어진 것 → --gpus all 누락 재확인.

# (3) 속도 체감 — gemma4:e4b 직접 호출
docker exec dscore.ttc.playwright curl -fsS http://127.0.0.1:11434/api/generate \
  -H "Content-Type: application/json" \
  -d '{"model":"gemma4:e4b","prompt":"hi","stream":false,"options":{"num_predict":50}}' \
  | python3 -c "import json,sys; d=json.load(sys.stdin); print(f\"토큰/초: {d['eval_count']/d['eval_duration']*1e9:.1f}\")"
# RTX 3060 이상: 30-80 토큰/초. CPU 모드: 1-3 토큰/초.
```

**(c-4) Jenkins Pipeline 실행 시 — HEADLESS 체크 필수**:

컨테이너 안에는 X display server 가 없으므로 Playwright Chromium 이
`--headed` 모드로 돌면 `Cannot open display` 로 즉시 실패한다. Dashboard →
`DSCORE-ZeroTouch-QA-Docker` → **Build with Parameters** 화면에서
`HEADLESS` 체크박스를 **반드시 체크**한 뒤 Build.

WSL2 에서 GUI 디버깅이 꼭 필요하면 WSLg 를 활용해 호스트 `DISPLAY` 를
컨테이너에 마운트하는 별도 설정이 필요 (본 가이드 범위 밖).

#### (d) GPU 없는 WSL2 (Intel/AMD 내장 GPU) 또는 Linux — CPU 대체 경로

WSL2 는 NVIDIA CUDA 만 노출하고 OpenCL/ROCm 은 지원하지 않는다. AMD/Intel
내장 GPU 환경 또는 GPU 전무 서버에서는 CPU 모드를 감수하되 **모델을 줄여**
실용 속도를 확보한다.

```bash
# 빌드 머신에서 경량 모델로 재빌드
TARGET_PLATFORM=linux/amd64 OLLAMA_MODEL=qwen2.5:1.5b ./offline/build-allinone.sh
```

| 모델 | CPU 모드 속도 (대략) | 품질 |
| ---- | --------------------- | ---- |
| `qwen2.5:1.5b` | 10-30 토큰/초 | Planner LLM 용으로 **최소 하한**. 복잡한 시나리오는 취약 |
| `gemma2:2b` | 5-15 토큰/초 | 다국어 괜찮음 |
| `gemma4:e4b` (기본) | 1-3 토큰/초 | Dify timeout 빈발 — **CPU 환경에선 비권장** |

CPU 모드는 Pipeline Stage 3 의 Planner 호출이 30-60초 걸린다. 기본
[Jenkinsfile](../DSCORE-ZeroTouch-QA-Docker.jenkinsPipeline) 의
`HEAL_TIMEOUT_SEC=180` 으로는 충분하지만 heal 단계가 연쇄되면 빌드 1회에
수 분 소요 가능성.

#### 포트 / 볼륨

| 항목 | 용도 |
| ---- | ---- |
| `18080` | Jenkins 웹 UI |
| `18081` | Dify 웹 UI (nginx reverse proxy) |
| `50001` | Jenkins JNLP (내부 에이전트 loopback 전용, 외부 노출 선택) |
| `-v dscore-data:/data` | 모든 상태 (PG, Redis, Qdrant, Ollama 모델, Jenkins home, Dify storage) 영속. **재기동 시 반드시 동일 볼륨 지정** — 생략하면 매번 4GB seed + 10분 프로비저닝 반복. |

### 2.4 기동 타임라인

```bash
docker logs -f dscore.ttc.playwright
```

**첫 기동 — 경과 시간별 관측 이벤트** ([entrypoint-allinone.sh](entrypoint-allinone.sh), [provision-apps.sh](provision-apps.sh)):

| 경과 | 로그 (발췌) | 의미 |
| ---- | ---------- | ---- |
| 0:00 | `[entrypoint-allinone] seed 시작` | `/opt/seed/*` → `/data/*` 복사 시작 |
| 0:00-0:30 | (출력 없음) | Ollama 모델 ~4GB 복사 중 |
| ~0:30 | `[entrypoint-allinone] seed 완료.` | 복사 끝, supervisord 기동 직전 |
| 0:30 | `[entrypoint-allinone] supervisord 기동...` | 11개 프로세스 순차 시작 |
| 0:30-1:30 | `postgresql ... ready` / `redis ... ready` / `ollama runner started` 등 | 각 서비스 초기화 |
| 1:30-3:00 | `[HH:MM:SS] [▶] === 1. 서비스 헬스체크 ===` | `provision-apps.sh` 시작. Dify/Jenkins HTTP 200 대기 |
| 3:00-5:00 | `[▶] === 2. Dify 관리자 설정 ===` → `... 3. Dify 플러그인 설치 ===` → `... 4. Ollama 모델 등록 ===` | Dify REST 호출 (관리자 → 로그인 → 플러그인 업로드 → 모델 공급자 등록) |
| 5:00-8:00 | `[▶] === 5. Chatflow Import ===` → `... 6. API Key 발급 ===` | App import → publish → 토큰 발급 |
| 8:00-10:00 | `[▶] === 7. Jenkins 플러그인 검증 ===` → `... 8. Credentials ===` → `... 9. Pipeline Job ===` → `... 10. Node ===` | Jenkins REST/Groovy |
| ~10:00 | `[▶] === 프로비저닝 완료 ===`  ·  `[entrypoint-allinone] 앱 프로비저닝 완료.`  ·  `[entrypoint-allinone] 준비 완료. supervisord wait...` | 사용 가능 상태 |

**재기동** (`docker restart dscore.ttc.playwright`): 30-60초. `/data/.initialized` + `/data/.app_provisioned` 플래그 덕분에 seed 와 provision 모두 스킵.

**로그 마커 해석** ([provision-apps.sh:58-65](provision-apps.sh#L58-L65)):

| 마커 | 의미 | 조치 |
| ---- | ---- | ---- |
| `[▶]` | 큰 단계 시작 | 정보성 |
| `[·]` | 하위 진행 | 정보성 |
| `[✓]` | 성공 | — |
| `[⚠]` | **비치명 실패** — 다음 단계로 진행함 | [2.6](#26-부분-실패-진단) 참조 |
| `[✗]` | 치명 오류 | [2.6](#26-부분-실패-진단) 참조 |

### 2.5 프로비저닝 체크리스트

타임라인이 끝난 직후, 9개 자동화 항목이 모두 완료됐는지 점검한다.

| # | 항목 | 확인 (CLI) | 확인 (UI) | 실패 시 |
| - | ---- | ---------- | --------- | ------ |
| 1 | Dify 관리자 생성 | `curl -fsS http://localhost:18081/console/api/setup \| jq .setup_status` → `"finished"` | Dify 로그인 가능 | [2.7](#27-프로비저닝-재실행) |
| 2 | Dify Ollama 플러그인 로드 | `docker exec dscore.ttc.playwright ls /data/dify/plugins/packages` 에 `langgenius-ollama-*.difypkg` | Plugins → Installed 에 Ollama | [2.6-A](#a-플러그인-업로드-타임아웃-120초) |
| 3 | Ollama 모델 등록 | `docker exec dscore.ttc.playwright ollama list` 에 `gemma4:e4b` | Dify Settings → Model Provider → Ollama 에 `gemma4:e4b` | [2.6-B](#b-ollama-모델-등록-실패) |
| 4 | Chatflow import/publish | `ls /data/dify/storage/apps 2>/dev/null \| wc -l` ≥ 1 | Dify Apps 에 `DSCORE-ZeroTouch-QA` | [2.6-C](#c-chatflow-import-실패--api-key-공란) |
| 5 | Dify API Key 발급 | `docker logs dscore.ttc.playwright \| grep "API Key:"` 에 토큰 접두 12자 | Jenkins Credentials #7 에 반영됨 | [2.6-C](#c-chatflow-import-실패--api-key-공란) |
| 6 | Jenkins 플러그인 4개 로드 | `curl -fsS -u admin:password 'http://localhost:18080/pluginManager/api/json?depth=1' \| jq -r '.plugins[].shortName' \| grep -E 'workflow-aggregator\|plain-credentials\|file-parameters\|htmlpublisher' \| wc -l` → `4` | Manage Jenkins → Plugins → Installed | [2.6-D](#d-jenkins-플러그인-미로드-치명) (재빌드 필요) |
| 7 | Jenkins Credentials `dify-qa-api-token` | `curl -fsS -u admin:password 'http://localhost:18080/credentials/store/system/domain/_/api/json?depth=1' \| jq -r '.credentials[].id'` 에 포함 | Manage Jenkins → Credentials | [2.7](#27-프로비저닝-재실행) |
| 8 | Pipeline Job 생성 | `curl -fsS -o /dev/null -w '%{http_code}' -u admin:password http://localhost:18080/job/DSCORE-ZeroTouch-QA-Docker/api/json` → `200` | Dashboard 에 Job | [2.7](#27-프로비저닝-재실행) |
| 9 | Node `mac-ui-tester` Online | `curl -fsS -u admin:password http://localhost:18080/computer/mac-ui-tester/api/json \| jq .offline` → `false` | Manage Jenkins → Nodes | [2.6-E](#e-node-offline) |

비밀번호를 [2.3(b)](#b-운영-권장--첫-기동-전-비밀번호-주입) 방식으로 바꿨다면 위 명령의 `admin:password` 를 해당 값으로 교체.

### 2.6 부분 실패 진단

먼저 한 줄로 경고/오류를 뽑는다.

```bash
docker logs dscore.ttc.playwright 2>&1 | grep -E '\[⚠|\[✗'
```

출력이 비어 있으면 모든 단계가 `[✓]` 로 끝난 것이다. 아래는 주요 실패 유형과 복구 절차.

#### (A) 플러그인 업로드 타임아웃 (120초)

- **증상**: `[⚠] 플러그인 설치 120s 내 완료 확인 못함`
- **원인**: dify-plugin-daemon 부하 또는 첫 기동 시 Python/Go 콜드스타트로 120s 초과
- **복구**:

  ```bash
  docker exec dscore.ttc.playwright supervisorctl restart dify-plugin-daemon
  docker exec dscore.ttc.playwright bash /opt/provision-apps.sh
  ```

#### (B) Ollama 모델 등록 실패

- **증상**: `[⚠] Ollama 모델 등록 응답 이상`
- **원인**: Ollama 데몬 미기동 또는 모델 seed 누락
- **복구**:

  ```bash
  docker exec dscore.ttc.playwright ollama list              # gemma4:e4b 존재 확인
  docker exec dscore.ttc.playwright supervisorctl restart ollama
  docker exec dscore.ttc.playwright bash /opt/provision-apps.sh
  ```

#### (C) Chatflow import 실패 / API Key 공란

- **증상**: `[⚠] Chatflow import 실패` 또는 프로비저닝 요약에 `API Key:` 라인이 없다
- **결과**: Jenkins Pipeline 실행 시 Dify API 호출이 401 로 실패
- **복구** — Dify UI 에서 수동:
  1. Dify → Apps → **Import DSL** → 빌드 머신에서 `e2e-pipeline/dify-chatflow.yaml` 재전송 후 업로드
  2. App → **Publish**
  3. App → API Access → **API Key 생성** → 토큰 복사
  4. Jenkins → Manage Credentials → `dify-qa-api-token` 편집 → Secret 에 토큰 붙여넣기

#### (D) Jenkins 플러그인 미로드 (치명)

- **증상**: `[✗] 필수 플러그인 누락: ...`
- **원인**: 빌드 시 `offline/jenkins-plugins/` 에 hpi 가 비어 있었음
- **폐쇄망에서 복구 불가** → 온라인 머신에서 [1.3 빌드 단계별 상세](#13-빌드-단계별-상세) 재실행 후 새 tar.gz 재배포 필요

#### (E) Node offline — Jenkins Pipeline 이 `'mac-ui-tester' is offline` 에서 멈춤

- **증상**: 체크리스트 #9 에서 `offline: true`, 또는 Pipeline Console 에 `Still waiting to schedule task / 'mac-ui-tester' is offline` 반복
- **원인별 분기**:

  1. **`/opt/jenkins-agent-run.sh` 부재** (가장 흔함) — `/opt` 는 컨테이너 ephemeral 이므로 `docker restart` / 재생성 시 사라진다. 과거 버전 이미지는 이 스크립트를 첫 프로비저닝 때만 만들어 `/data/.app_provisioned` 가 있으면 재생성하지 않음.
  2. **NODE_SECRET 불일치** — Jenkins Node 를 삭제·재생성하면 secret 이 바뀌는데 run-script 는 옛 값 사용.
  3. **JNLP 포트(50001) 충돌** — 드묾.
- **진단**:

  ```bash
  docker exec dscore.ttc.playwright bash -c '
    ls -la /opt/jenkins-agent-run.sh /data/jenkins-agent/run.sh 2>&1
    supervisorctl status jenkins-agent
    tail -20 /data/logs/jenkins-agent.err.log
    curl -sS -u admin:password http://127.0.0.1:18080/computer/mac-ui-tester/api/json \
      | python3 -c "import json,sys; d=json.load(sys.stdin); print(f\"offline={d.get(chr(39)+chr(111)+chr(102)+chr(102)+chr(108)+chr(105)+chr(110)+chr(101)+chr(39))}\")" 2>/dev/null || true
  '
  ```

- **복구 A — 자동 재생성 (2026-04-19 이후 이미지)**:

  ```bash
  docker restart dscore.ttc.playwright   # entrypoint 가 매 기동마다 run.sh 재생성 + agent 기동
  ```

- **복구 B — 구버전 이미지 응급 패치 (재빌드 전 임시 복구)**:

  ```bash
  docker exec dscore.ttc.playwright bash -c '
    SECRET=$(curl -sS -u admin:password http://127.0.0.1:18080/computer/mac-ui-tester/slave-agent.jnlp \
      | sed -n "s/.*<argument>\([a-f0-9]\{64\}\)<\/argument>.*/\1/p" | head -n1)
    mkdir -p /data/jenkins-agent
    printf "#!/usr/bin/env bash\nset -e\ncd /data/jenkins-agent\nif [ ! -f agent.jar ]; then cp /opt/jenkins-agent.jar agent.jar 2>/dev/null || curl -sf -o agent.jar http://127.0.0.1:18080/jnlpJars/agent.jar; fi\nexec java -jar agent.jar -url http://127.0.0.1:18080 -secret %s -name mac-ui-tester -workDir /data/jenkins-agent\n" "$SECRET" > /data/jenkins-agent/run.sh
    chmod +x /data/jenkins-agent/run.sh
    ln -sfn /data/jenkins-agent/run.sh /opt/jenkins-agent-run.sh
    supervisorctl restart jenkins-agent
  '
  sleep 5
  docker exec dscore.ttc.playwright supervisorctl status jenkins-agent   # RUNNING 확인
  ```

- **복구 C — Node 자체가 삭제됐거나 secret 무효**: `docker exec dscore.ttc.playwright bash /opt/provision-apps.sh` (Node 재등록 + secret 재추출) → `docker restart dscore.ttc.playwright`

### 2.7 프로비저닝 재실행

```bash
docker exec dscore.ttc.playwright bash /opt/provision-apps.sh
```

- **멱등성**: 각 단계가 "이미 존재하면 스킵 또는 덮어쓰기" 로 구현돼 여러 번 실행해도 안전.
- **전체 처음부터 재실행**: `docker exec dscore.ttc.playwright rm /data/.app_provisioned && docker restart dscore.ttc.playwright` — entrypoint 가 재기동 시 자동으로 provision 을 돌린다.

### 2.8 관리자 비밀번호 변경

| 경로 | 상황 | 방법 |
| ---- | ---- | ---- |
| 기동 시 사전 주입 (가장 안전) | 첫 배포 직전 | [2.3(b)](#b-운영-권장--첫-기동-전-비밀번호-주입) 방식. `/data` 볼륨이 비어 있을 때만 적용됨 |
| 기동 후 UI 변경 (Jenkins) | 이미 운영 중 | People → `admin` → **Configure** → Password 변경 |
| 기동 후 UI 변경 (Dify) | 이미 운영 중 | 우상단 계정 아이콘 → **Settings** → **Account** → Password 변경 |

**주의**:

- Jenkins 비밀번호를 UI 로 바꾼 뒤 provision 재실행이 필요하다면, 새 비밀번호를 `JENKINS_ADMIN_PW` env 로 주입해 docker run 을 재생성한다 — `/data/.app_provisioned` 플래그가 있어 seed 는 도는 않지만 Jenkins REST 인증은 새 비번으로 되므로 정상 재실행 가능.
- Jenkins Credentials `dify-qa-api-token` 은 Dify API Key 와 별개 값이므로 비밀번호 변경 시 갱신 불필요. **Dify API Key 를 재발급**했다면 Credentials 를 수동 업데이트한다.

### 2.9 시나리오 검증

1. 브라우저에서 `http://<host>:18080` 접속 → `admin` 로그인
2. Dashboard → `DSCORE-ZeroTouch-QA-Docker` → **Build with Parameters**
3. `SCENARIO_TEXT` 입력 — 예: `Google.com 에 접속해 'Claude AI' 를 검색한다`
4. **Build** → Console Output 을 실시간으로 관찰
5. 예상 스테이지:
   - `[Pipeline] stage (Plan)` — Dify API 호출 (Planner LLM → DSL JSON)
   - `[Pipeline] stage (Run)` — Playwright Chromium headless 실행
   - `[Pipeline] stage (Report)` — 스크린샷 + HTML 리포트 생성
6. **성공 조건**: Build 결과 `SUCCESS` + `Build Artifacts` 에 `artifacts/index.html` 링크 존재
7. 클릭 → 시각적 리포트 확인

**첫 빌드 실패 시 확인 순서**:

1. Console Output 에서 첫 ERROR 라인 찾기
2. `docker exec dscore.ttc.playwright supervisorctl status ollama` — Ollama 가동 확인
3. [2.5 체크리스트](#25-프로비저닝-체크리스트) #3 (모델), #5 (API Key), #9 (Node) 재확인
4. 의심되는 서비스 로그 확인 — `docker exec dscore.ttc.playwright tail -50 /data/logs/<service>.log`

---

## 3. 운영

### 재시작 (상태 유지)
```bash
docker restart dscore.ttc.playwright
```
- 약 30-60초 후 서비스 재가동
- Dify App, Jenkins Job, Credentials, Pipeline 실행 이력, 모델 등록 모두 유지

### 중지
```bash
docker stop dscore.ttc.playwright
```

### 로그 확인
각 서비스별 로그는 `/data/logs/` 에 분리 저장:
```bash
# 특정 서비스 로그
docker exec dscore.ttc.playwright tail -f /data/logs/dify-api.log
docker exec dscore.ttc.playwright tail -f /data/logs/jenkins.log
docker exec dscore.ttc.playwright tail -f /data/logs/ollama.log

# supervisord 상태
docker exec dscore.ttc.playwright supervisorctl status
```

### 프로세스 제어
```bash
# 특정 서비스 재시작
docker exec dscore.ttc.playwright supervisorctl restart dify-api

# 모두 재시작
docker exec dscore.ttc.playwright supervisorctl restart all
```

### 백업
`/data` 볼륨 하나만 백업하면 된다:
```bash
docker run --rm -v dscore-data:/data -v $PWD:/backup busybox \
  tar czf /backup/dscore-data-backup-$(date +%Y%m%d).tar.gz /data
```

### 복원
```bash
docker stop dscore.ttc.playwright
docker run --rm -v dscore-data:/data -v $PWD:/backup busybox \
  tar xzf /backup/dscore-data-backup-YYYYMMDD.tar.gz -C /
docker start dscore.ttc.playwright
```

### 업그레이드 (새 번들 수령 시)
```bash
# 1. 기존 컨테이너 중지 (볼륨 유지)
docker stop dscore.ttc.playwright
docker rm dscore.ttc.playwright

# 2. 기존 이미지 삭제 (선택)
docker rmi dscore.ttc.playwright:latest

# 3. 새 번들 로드
docker load -i dscore.ttc.playwright-new.tar.gz

# 4. 재기동 (동일 볼륨 사용 → 기존 상태 유지)
docker run -d --name dscore.ttc.playwright \
  -p 18080:18080 -p 18081:18081 -p 50001:50001 \
  -v dscore-data:/data \
  dscore.ttc.playwright:latest
```

PG 마이그레이션이 있으면 Dify api 기동 시 자동 수행 (Alembic).

---

## 4. Ollama 모델 관리

이 이미지는 GUI Ollama (Ollama Desktop) 를 포함하지 않는다. 모델 추가·교체·삭제는 모두 **컨테이너 안의 Ollama 서버(`127.0.0.1:11434`) 와 Dify REST/UI** 로 수행한다. 폐쇄망이라 `ollama pull` 이 외부 레지스트리에 닿지 못하므로, 신규 모델 투입은 **재빌드** 또는 **온라인 머신 → 오프라인 전송** 두 경로뿐이다.

### 4.1 현재 상태 확인

```bash
# (1) Ollama 데몬에 탑재된 모델 목록 + 크기
docker exec dscore.ttc.playwright ollama list
# NAME             ID              SIZE      MODIFIED
# gemma4:e4b       abcd1234...     4.1 GB    2 hours ago

# (2) 개별 모델 세부 정보 (context length, params)
docker exec dscore.ttc.playwright ollama show gemma4:e4b

# (3) 실제 디스크 사용량 + 저장 위치 (OLLAMA_MODELS=/data/ollama/models)
docker exec dscore.ttc.playwright du -sh /data/ollama/models

# (4) Ollama 데몬 상태 / 로그
docker exec dscore.ttc.playwright supervisorctl status ollama
docker exec dscore.ttc.playwright tail -30 /data/logs/ollama.log

# (5) Dify 의 Ollama 프로바이더에 등록된 모델 목록 (빠른 자가진단)
docker exec dscore.ttc.playwright bash -c '
  curl -sS -b /tmp/dify-cookies.txt \
    http://127.0.0.1:18081/console/api/workspaces/current/model-providers/langgenius/ollama/ollama/models \
  | python3 -m json.tool | grep -E "\"model\"|\"model_type\""
'
```

> **쿠키가 만료됐다면** `/tmp/dify-cookies.txt` 가 비어 있을 수 있다. 이 경우 Dify UI 로그인 확인이 더 빠르다 — `http://<host>:18081` → Settings → Model Provider → Ollama.

### 4.2 시작(기본) 모델 바꾸기 — 재빌드 (깨끗한 방법)

이미지를 처음부터 다른 모델로 만드는 가장 표준적인 방법. 빌드 타임에 `ollama pull` 이 돌아 모델이 이미지에 seed 로 박힌다.

```bash
# 온라인 빌드 머신에서
cd e2e-pipeline
OLLAMA_MODEL=llama3.1:8b ./offline/build-allinone.sh 2>&1 | tee /tmp/build.log
```

연쇄적으로 바뀌는 것:

- [Dockerfile.allinone:14-24](Dockerfile.allinone#L14-L24) 의 `ARG OLLAMA_MODEL` → `ollama pull` 대상
- [provision-apps.sh:44](provision-apps.sh#L44) 의 `OLLAMA_MODEL` → Dify 에 등록되는 모델 id
- **주의**: [dify-chatflow.yaml](../dify-chatflow.yaml) 의 LLM 노드에는 **모델 id 가 하드코딩**돼 있다. 모델을 바꾸면 chatflow 도 함께 수정해야 하거나, 대안으로 4.5 의 UI 경로로 chatflow 의 모델만 재선택한다.

**사전 점검 — 모델이 Ollama 레지스트리에 실제 존재하는지**:

```bash
# 빌드 시작 전에 온라인 머신에서 확인 (빌드 중 pull 실패로 40GB 폐기되는 것 방지)
docker run --rm ollama/ollama:latest bash -lc '
  ollama serve &>/dev/null & sleep 2
  ollama pull llama3.1:8b && echo OK || echo FAIL
'
```

**디스크·메모리 가이드**:

| 모델 | 크기 (blob) | 실행 시 RAM | 용도 |
| ---- | --------- | --------- | ---- |
| `gemma4:e4b` | ~4GB | 6-8GB | 기본 (빠름, 저사양) |
| `llama3.1:8b` | ~4.7GB | 8-10GB | 품질↑ |
| `qwen2.5:7b` | ~4.4GB | 8-10GB | 다국어 강세 |
| `llama3.1:70b` | ~40GB | 80GB+ | 고사양 전용 — **폐쇄망 16GB RAM 기준 불가** |

### 4.3 런타임에 모델 추가 투입 — 오프라인 전송

이미 운영 중인 컨테이너에 신규 모델을 **재빌드 없이** 더하는 방법. 재빌드가 부담스러운 경우에만 쓴다.

**(1) 온라인 머신에서 모델을 pull 하고 blob/manifest 를 아카이브**

```bash
# 임시 디렉토리에 ollama 데이터만 모으기 — 전체 이미지 빌드 불필요
WORK=$(mktemp -d)
docker run --rm -v "$WORK":/root/.ollama ollama/ollama:latest bash -lc '
  ollama serve &>/dev/null & sleep 2
  ollama pull llama3.1:8b
'
ls "$WORK/models"   # blobs/, manifests/ 가 있으면 정상

# models 디렉토리만 tar — 약 4-5GB
tar czf llama3.1-8b.ollama.tar.gz -C "$WORK" models
rm -rf "$WORK"

# sha256 메모
sha256sum llama3.1-8b.ollama.tar.gz
```

**(2) tar 를 폐쇄망 서버로 이동 (USB/scp/사내망)**

**(3) 폐쇄망에서 컨테이너 내부에 푼다**

```bash
# 무결성 검증
sha256sum llama3.1-8b.ollama.tar.gz

# 컨테이너의 /data/ollama 밑에 풀기 (같은 볼륨이므로 host 에서 직접 풀어도 됨)
docker cp llama3.1-8b.ollama.tar.gz dscore.ttc.playwright:/tmp/
docker exec dscore.ttc.playwright bash -c '
  cd /data/ollama
  tar xzf /tmp/llama3.1-8b.ollama.tar.gz   # models/ 에 blobs/manifests 병합
  chown -R root:root /data/ollama/models
  rm /tmp/llama3.1-8b.ollama.tar.gz
'

# Ollama 데몬이 새 manifest 를 다시 스캔하도록 재기동 (10초 소요)
docker exec dscore.ttc.playwright supervisorctl restart ollama
sleep 5

# 확인
docker exec dscore.ttc.playwright ollama list   # llama3.1:8b 가 보여야 함
docker exec dscore.ttc.playwright curl -sS http://127.0.0.1:11434/api/tags | python3 -m json.tool
```

> **주의**: `docker cp` 는 컨테이너를 멈추지 않지만, tar 풀기 중 Ollama 가 blob 을 lock 할 수 있다. 이 경우 먼저 `supervisorctl stop ollama` → `docker cp` → tar 풀기 → `supervisorctl start ollama` 순서가 안전하다.

### 4.4 Dify 에 신규 모델 등록

Ollama 에 모델이 올라가도 Dify 의 드롭다운에는 자동으로 뜨지 않는다. 공급자 레벨에서 한 번 등록해야 한다.

#### (a) UI 경로 — 권장

1. 브라우저에서 `http://<host>:18081` 로그인
2. 우상단 계정 아이콘 → **Settings** → **Model Provider**
3. **Ollama** 카드 → **+ Add Model** 클릭
4. 입력:
   - **Model Name**: `llama3.1:8b` (정확히 `ollama list` 에 나온 id)
   - **Base URL**: `http://127.0.0.1:11434`
   - **Completion mode**: `Chat`
   - **Context size**: `8192` (모델별 공식 max 참고)
   - **Upper bound for max tokens**: `4096`
   - **Vision / Function call**: 끄기 (gemma/llama/qwen 일반 모델)
5. **Save** → 드롭다운에서 선택 가능

#### (b) CLI 경로 — 자동화

provision-apps.sh 의 2-3b 단계와 동일한 REST 호출. 컨테이너 내부에서:

```bash
docker exec dscore.ttc.playwright bash <<'SH'
MODEL="llama3.1:8b"
COOKIES=/tmp/dify-cookies.txt

# 쿠키 재로그인 (만료된 경우)
curl -sS -c $COOKIES -b $COOKIES \
  -X POST http://127.0.0.1:18081/console/api/login \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"${DIFY_EMAIL:-admin@example.com}\",\"password\":\"${DIFY_PASSWORD:-Admin1234!}\",\"remember_me\":true}" \
  > /dev/null

CSRF=$(awk '$6=="csrf_token"{print $7}' $COOKIES | tail -n1)

curl -sS -b $COOKIES -X POST \
  "http://127.0.0.1:18081/console/api/workspaces/current/model-providers/langgenius/ollama/ollama/models/credentials" \
  -H "Content-Type: application/json" \
  -H "X-CSRF-Token: $CSRF" \
  -d "{
    \"model\": \"$MODEL\",
    \"model_type\": \"llm\",
    \"credentials\": {
      \"base_url\": \"http://127.0.0.1:11434\",
      \"mode\": \"chat\",
      \"context_size\": \"8192\",
      \"max_tokens\": \"4096\",
      \"vision_support\": \"false\",
      \"function_call_support\": \"false\"
    }
  }"
SH
```

성공 시 HTTP 200/201. 실패는 체크리스트:

- 403 → CSRF 토큰 추출 실패. 쿠키 파일 (`cat /tmp/dify-cookies.txt`) 에 `csrf_token` 라인이 있는지 확인
- 400 `model not found` → Ollama 에 실제로 그 이름이 없음. `ollama list` 로 이름 재확인
- 500 `plugin not found` → langgenius/ollama 플러그인 미설치. [체크리스트 #2](#25-프로비저닝-체크리스트) 확인

### 4.5 Chatflow 에서 사용 모델 변경

`DSCORE-ZeroTouch-QA-Docker` Jenkins Pipeline 이 호출하는 Dify App 은 **chatflow DSL 안에 모델 id 가 박혀 있다**. Ollama/Dify 양쪽에 모델을 등록해도 chatflow 는 여전히 예전 모델을 쓰므로 별도 수정 필요.

**UI 경로 — 가장 빠름**:

1. Dify → Apps → `DSCORE-ZeroTouch-QA` 클릭
2. 좌측 캔버스에서 **LLM 노드** (노드 타이틀: Planner 등) 클릭
3. 오른쪽 패널의 **Model** 드롭다운에서 새 모델 선택
4. 캔버스 상단 **Publish** → "Publish as API" 확인
5. Pipeline 재실행 시 즉시 반영 (API Key 는 변경되지 않으므로 Jenkins Credentials 손댈 필요 없음)

**DSL 직수정 경로 — 재배포용**:

빌드 머신에서 [dify-chatflow.yaml](../dify-chatflow.yaml) 의 LLM 노드를 편집한 뒤 전체 번들을 재빌드하거나, 폐쇄망에서 Dify → Apps → **Import DSL** 로 새 버전 업로드.

### 4.6 모델 삭제 / 디스크 회수

```bash
# Ollama 에서 제거 (blob GC 포함)
docker exec dscore.ttc.playwright ollama rm gemma4:e4b

# 디스크 절감 확인
docker exec dscore.ttc.playwright du -sh /data/ollama/models
```

Dify 공급자 등록 삭제는 UI: Settings → Model Provider → Ollama → 해당 모델 행의 휴지통 아이콘.

> **경고**: chatflow 가 참조 중인 모델을 삭제하면 Pipeline 실행 시 `ollama model "...": not found` 으로 실패한다. 삭제 전 [4.5](#45-chatflow-에서-사용-모델-변경) 로 먼저 교체.

### 4.7 Ollama 런타임 설정 튜닝

대부분의 설정은 Ollama 데몬의 **환경변수** 로 제어한다 ([supervisord.conf:73-75](supervisord.conf#L73-L75)).

| 환경변수 | 기본 | 의미 |
| ------- | ---- | ---- |
| `OLLAMA_HOST` | `127.0.0.1:11434` | 바인드 주소. 컨테이너 밖에서 직접 호출하려면 `0.0.0.0:11434` + `-p 11434:11434` 추가 |
| `OLLAMA_MODELS` | `/data/ollama/models` | 모델 저장소. `/data` 볼륨이므로 변경 비권장 |
| `OLLAMA_NUM_PARALLEL` | `1` (버전별 다름) | 동시 요청 수 |
| `OLLAMA_MAX_LOADED_MODELS` | `1` | 메모리에 올릴 모델 수. RAM 여유 있으면 2-3 |
| `OLLAMA_KEEP_ALIVE` | `5m` | 모델 언로드 지연. `-1` 이면 영구 상주 |
| `OLLAMA_FLASH_ATTENTION` | 미설정 | `1` 로 설정 시 FA 활성 (지원 모델 한정) |

변경 방법 — [supervisord.conf](supervisord.conf) 의 `[program:ollama]` 블록 편집 후 재빌드, 또는 런타임 응급 패치:

```bash
# 응급 — 재빌드 없이 적용 (다음 재기동까진 유지)
docker exec dscore.ttc.playwright bash -c '
  sed -i "s|OLLAMA_MODELS=\"/data/ollama/models\"|&,OLLAMA_KEEP_ALIVE=\"-1\",OLLAMA_MAX_LOADED_MODELS=\"2\"|" \
    /etc/supervisor/supervisord.conf
  supervisorctl reread && supervisorctl update && supervisorctl restart ollama
'
```

Dify 쪽 `context_size` / `max_tokens` 는 [provision-apps.sh:49-50](provision-apps.sh#L49-L50) 의 `OLLAMA_CONTEXT_SIZE`, `OLLAMA_MAX_TOKENS` 환경변수로 — 첫 기동 `docker run` 에 `-e OLLAMA_CONTEXT_SIZE=16384` 형태로 주입하거나, UI (4.4(a)) 에서 해당 모델 행을 열어 수정.

---

## 5. 트러블슈팅

### 컨테이너가 기동 직후 죽는다

```bash
docker logs dscore.ttc.playwright | tail -50
```

- 메모리 부족: 호스트 RAM 16GB+ 필요. `docker stats` 로 모니터링.
- PostgreSQL 초기화 실패: `/data/logs/postgresql.err.log` 확인. 볼륨 권한 문제라면 `docker volume rm dscore-data` 후 재기동 (주의: 모든 데이터 소실).

### crash loop — `rm: cannot remove '/var/jenkins_home': Device or resource busy`

- **증상**: 로그에 `[entrypoint-allinone] 기존 볼륨 감지 — seed 건너뜀.` 뒤로 위 메시지가 무한 반복되고 Jenkins/Dify 모두 접속 불가
- **원인**: `jenkins/jenkins` base 이미지가 선언한 `VOLUME /var/jenkins_home` 때문에 Docker 가 해당 경로에 익명 볼륨을 자동 마운트 → 마운트 포인트는 `rm` 할 수 없어 구버전 entrypoint 의 symlink 시도가 실패 → `set -e` 로 엔트리포인트 즉사 → `--restart unless-stopped` 로 반복 기동
- **해결**: `c61cc52` 이후 이미지는 symlink 를 쓰지 않고 `JENKINS_HOME=/data/jenkins` env 로 직접 리다이렉트하므로 이 증상이 발생하지 않는다. 구버전 tar.gz 를 사용 중이면 **이미지 재빌드 필수**:

  ```bash
  # 빌드 머신에서
  cd e2e-pipeline
  ./offline/build-allinone.sh    # buildx 캐시 덕분에 2-3분 (ollama pull, pip 등은 재사용)

  # 폐쇄망에서
  docker rm -f dscore.ttc.playwright                      # 볼륨은 살려둠 (seed 재활용)
  docker load -i dscore.ttc.playwright-new.tar.gz
  docker run -d --name dscore.ttc.playwright \
    -p 18080:18080 -p 18081:18081 -p 50001:50001 \
    -v dscore-data:/data dscore.ttc.playwright:latest
  ```

### 프로비저닝이 "1. 서비스 헬스체크" 에서 멈춘다 — Dify 로그인 불가 + Jenkins Job 미생성

- **증상 (둘이 세트로 나타남)**:
  - Dify (`http://<host>:18081/signin`) 로그인 화면 진입 불가 (무한 로딩 또는 5xx)
  - Jenkins 는 뜨지만 `DSCORE-ZeroTouch-QA-Docker` Pipeline Job 이 Dashboard 에 없음
  - `docker logs dscore.ttc.playwright` 에 `[▶] === 1. 서비스 헬스체크 ===` 까지만 찍히고 이후 수 분 이상 진척이 없음
- **원인**: dify-api gunicorn 의 **gevent 워커가 커넥션 백로그로 데드락**. 구버전 이미지 (2026-04-19 이전 빌드) 는 `SERVER_WORKER_CONNECTIONS=10` (dify entrypoint 기본값) 을 그대로 상속받아 provision-apps.sh 의 240s curl 재시도 루프에서 커넥션 10개 초과 즉시 gevent hub 이 dead-lock 됨. Dify 공식 compose 의 권장값은 `1000`.
- **진단 (3초 안에 확증)**:

  ```bash
  # dify-api 가 직접 응답하는지 (5초 내 200 아니면 데드락 확정)
  docker exec dscore.ttc.playwright curl -sS -o /dev/null \
    -w "%{http_code} t=%{time_total}s\n" \
    --max-time 5 http://127.0.0.1:5001/console/api/setup
  # 예상 (정상):  200 t=0.04s
  # 예상 (증상):  000 t=5.00s  ← 커넥트는 되지만 응답 지연

  # 백로그 누적 확인 (CLOSE-WAIT 가 수십 개면 확정)
  docker exec dscore.ttc.playwright ss -tn 2>/dev/null | grep :5001 | wc -l
  ```

- **응급 복구 (구버전 이미지로 당장 띄워야 할 때)**:

  ```bash
  # gunicorn 마스터/워커 zombie kill 후 supervisord 로 재기동
  docker exec dscore.ttc.playwright bash -c '
    pids=$(pgrep -f "gunicorn.*app:app"); kill -9 $pids 2>/dev/null || true;
    sleep 2; supervisorctl restart dify-api'

  # 10초 대기 후 200 응답 확인
  sleep 10
  docker exec dscore.ttc.playwright curl -sSf --max-time 5 http://127.0.0.1:5001/console/api/setup >/dev/null && echo OK

  # 프로비저닝 재실행 (멱등)
  docker exec dscore.ttc.playwright bash /opt/provision-apps.sh
  ```

- **영구 해결**: 2026-04-19 이후 커밋의 [supervisord.conf](supervisord.conf) 는 `SERVER_WORKER_CONNECTIONS=1000` + `GUNICORN_TIMEOUT=360` 을 명시하고, [entrypoint-allinone.sh](entrypoint-allinone.sh) 의 헬스 대기는 dify-api `/console/api/setup` 이 실제 200 응답할 때까지 기다린 뒤 provisioning 에 진입한다. **이미지 재빌드**:

  ```bash
  # 빌드 머신에서
  cd e2e-pipeline
  ./offline/build-allinone.sh    # buildx 캐시 덕분에 2-5분

  # 폐쇄망에서 (볼륨 유지 → 기존 Jenkins Job/Credentials/Dify App 상태 보존)
  docker rm -f dscore.ttc.playwright
  docker load -i dscore.ttc.playwright-new.tar.gz
  docker run -d --name dscore.ttc.playwright \
    -p 18080:18080 -p 18081:18081 -p 50001:50001 \
    -v dscore-data:/data --add-host host.docker.internal:host-gateway \
    dscore.ttc.playwright:latest
  ```

### Pipeline 첫 스테이지에서 `[ERROR] venv 가 존재하지 않거나 손상되었습니다`

- **증상**: Jenkins Pipeline Stage 1 에서 `${WORKSPACE}/.qa_home/venv/bin/activate` 없음 에러 → 전체 빌드 FAILURE. setup.sh 재실행 안내가 나오지만 All-in-One 이미지엔 setup.sh 가 없음.
- **원인**: 구버전 이미지 (2026-04-19 이전) 는 전역 venv 를 사전 생성하지 않고 Jenkins Node 의 `SCRIPTS_HOME` 이 비어있는 워크스페이스를 가리키고 있어 venv/`zero_touch_qa` 패키지 모두 빠져 있음.
- **원리 (최신 빌드)**:
  - 이미지 빌드 시 [Dockerfile.allinone](Dockerfile.allinone) 가 `/opt/qa-venv` (Python 3.13 + system-site-packages) 와 `/opt/scripts-home/zero_touch_qa` (심볼릭 링크) 를 생성
  - [provision-apps.sh:552-554](provision-apps.sh#L552-L554) 이 Node 환경변수 `SCRIPTS_HOME=/opt/scripts-home` 설정
  - [entrypoint-allinone.sh](entrypoint-allinone.sh) 가 매 기동마다 `${WORKSPACE}/.qa_home/venv → /opt/qa-venv` 심볼릭 링크를 만듦
- **영구 해결**: 최신 커밋으로 이미지 재빌드 후 동일 볼륨으로 재기동.
- **응급 복구 (구버전 이미지를 당장 써야 할 때)**:

  ```bash
  docker exec dscore.ttc.playwright bash -c '
    # 1) 전역 venv (3.13 deps 상속)
    /usr/bin/python3 -m venv --system-site-packages /opt/qa-venv
    /opt/qa-venv/bin/python -c "import requests, playwright, PIL" && echo "deps OK"

    # 2) SCRIPTS_HOME 디렉토리
    mkdir -p /opt/scripts-home
    ln -sfn /opt/zero_touch_qa /opt/scripts-home/zero_touch_qa

    # 3) 워크스페이스 스켈레톤
    WS=/data/jenkins-agent/workspace/DSCORE-ZeroTouch-QA-Docker
    mkdir -p "$WS/.qa_home/artifacts"
    ln -sfn /opt/qa-venv "$WS/.qa_home/venv"

    # 4) Jenkins Node 의 SCRIPTS_HOME 환경변수 덮어쓰기 (Groovy)
    GROOVY="import jenkins.model.*; import hudson.slaves.*;
      def n = Jenkins.getInstance().getNode(\"mac-ui-tester\");
      n.getNodeProperties().removeAll { it instanceof EnvironmentVariablesNodeProperty };
      n.getNodeProperties().add(new EnvironmentVariablesNodeProperty([
        new EnvironmentVariablesNodeProperty.Entry(\"SCRIPTS_HOME\",\"/opt/scripts-home\")]));
      Jenkins.getInstance().save(); println \"OK\""

    CRUMB=$(curl -sS -u admin:password "http://127.0.0.1:18080/crumbIssuer/api/json" \
      | python3 -c "import json,sys; d=json.load(sys.stdin); print(d[\"crumbRequestField\"]+\":\"+d[\"crumb\"])")

    curl -sS -u admin:password -H "$CRUMB" \
      --data-urlencode "script=$GROOVY" \
      http://127.0.0.1:18080/scriptText
  '

  # 5) Jenkins Pipeline 재빌드 (Dashboard → Build with Parameters)
  ```

### Dify 웹 UI 502 Bad Gateway

- dify-api 프로세스 미기동: `docker exec dscore.ttc.playwright supervisorctl status dify-api`
- Alembic 마이그레이션 진행 중: `docker exec dscore.ttc.playwright tail -f /data/logs/dify-api.log` 에서 `Running migration` 대기 (1-3분)
- 위 두 가지가 아니라면 → 위 **"프로비저닝이 '1. 서비스 헬스체크' 에서 멈춘다"** 항목의 gunicorn 워커 데드락일 가능성 높음

### Jenkins 에이전트 offline / Ollama 모델 이슈

프로비저닝 단계의 부분 실패인 경우가 대부분 — [2.6 부분 실패 진단](#26-부분-실패-진단) 의 `(B) Ollama 모델 등록 실패`, `(E) Node offline` 참조.

### WSL2 컨테이너에서 nvidia-smi 가 안 보임

Jenkins Pipeline Stage 3 가 5분 timeout 으로 실패하고 `docker exec dscore.ttc.playwright nvidia-smi` 명령도 `command not found` 나 에러를 반환하는 경우.

**진단 (상위부터 차례대로)**:

1. **Windows 호스트의 드라이버 확인** — PowerShell 에서 `nvidia-smi`. 명령 없으면 NVIDIA 드라이버 미설치. 2024-01 이후 버전 설치: <https://www.nvidia.com/Download/index.aspx>
2. **Docker Desktop → Settings → Resources → WSL Integration** 이 활성 배포판에 체크돼 있는지.
3. **NVIDIA Container Toolkit 동작 확인**:

   ```bash
   docker run --rm --gpus all nvidia/cuda:12.0.0-base-ubuntu22.04 nvidia-smi
   ```

   이 명령이 실패하면 **우리 이미지도 당연히 실패**. 먼저 이걸 성공시켜야 함.
4. **`docker run` 에 `--gpus all` 누락** — 2.3(c-2) 의 `docker run` 예시 전체를 그대로 복사해 사용.
5. 공식 가이드: <https://docs.docker.com/desktop/features/gpu/>

### WSL2 에서 컨테이너가 기동 중 OOM 종료

증상: `docker logs dscore.ttc.playwright` 에 중단 없이 메모리 OOM 로그 또는 일부 서비스가 FATAL 로 표시. `docker stats` 에 메모리 사용량 급증.

- **.wslconfig 메모리 부족** 이 주 원인. 본 이미지는 10-12GB (Ollama + 모델) + 2-3GB (Dify) + 1.5-2.5GB (Jenkins) + 500MB-1GB (PG/Redis/Qdrant) ≈ **피크 16-18GB 소비**.
- Windows 사용자 홈 `C:\Users\<user>\.wslconfig` 를 다음과 같이 재설정 후 PowerShell 관리자 `wsl --shutdown` + Docker Desktop 재기동:

  ```ini
  [wsl2]
  memory=24GB
  processors=8
  swap=8GB
  ```

- 적용 확인: WSL2 배포판 안에서 `free -g` 로 Total 이 설정값에 가까운지.

### Jenkins Pipeline Stage 3 가 `Cannot open display` / Chromium `Page crashed!`

- **`Cannot open display`**: Build with Parameters 에서 `HEADLESS` 를 체크하지 않고 실행했을 때. 컨테이너에는 X server 없음. 체크박스 활성화 후 재빌드.
- **`Page crashed!` / Chromium 프로세스 랜덤 종료**: `/dev/shm` 64MB 기본값으로 인한 공유 메모리 부족. `docker run` 에 `--shm-size=2g` 추가 (2.3(c-2) 예시 참조).

### 이미지 크기가 너무 커서 USB 이전 곤란
- 여러 조각으로 분할:
  ```bash
  split -b 2G dscore.ttc.playwright-*.tar.gz dscore.ttc.playwright-part-
  # 폐쇄망에서:
  cat dscore.ttc.playwright-part-* > dscore.ttc.playwright.tar.gz
  docker load -i dscore.ttc.playwright.tar.gz
  ```

### 아키텍처 불일치 (exec format error)
- 빌드 머신 (macOS Apple Silicon = arm64) 과 폐쇄망 (Linux server = amd64) 다른 경우
- 빌드 시 명시:
  ```bash
  TARGET_PLATFORM=linux/amd64 ./offline/build-allinone.sh
  ```

### 시계 오류로 Dify 로그인 실패
- 폐쇄망에 NTP 없으면 컨테이너 시계가 초기화될 수 있음
- 호스트 시계를 먼저 맞추고 `docker restart dscore.ttc.playwright`
- `docker exec dscore.ttc.playwright date` 로 확인

---

## 6. 스크립트 가이드

### 파일 레이아웃

```
e2e-pipeline/offline/
├── Dockerfile.allinone          # 3-stage 멀티 빌드 정의
├── build-allinone.sh            # [빌드 머신] 온라인 빌드 — tar.gz 산출
├── entrypoint-allinone.sh       # [컨테이너 PID 1] seed + supervisord + 에이전트 기동
├── provision-apps.sh            # [컨테이너] Dify/Jenkins 프로비저닝 (최초 1회 + 수동 복구)
├── pg-init-allinone.sh          # [빌드 타임] PG initdb — Dify 5개 DB 사전 생성
├── supervisord.conf             # [컨테이너] 11개 프로세스 정의 (jenkins-agent 포함)
├── nginx-allinone.conf          # [컨테이너] localhost upstream (Dify :18081)
├── requirements-allinone.txt    # [빌드 타임] 컨테이너 Python 의존성
├── README.md                    # 이 문서
├── jenkins-plugins/             # [빌드 타임 산출] *.hpi
└── dify-plugins/                # [빌드 타임 산출] *.difypkg
```

이하 3 개가 **사용자가 직접 호출하거나 동작을 이해할 필요가 있는** 핵심 스크립트.

### 6.1 `build-allinone.sh` — 이미지 빌드 (빌드 머신 실행)

**언제 실행**: 최초 배포, 플러그인/의존성 변경, Ollama 모델 교체, 버전 업그레이드.

**사전 조건**: 인터넷, Docker buildx, JDK 11+ (플러그인 매니저 실행용), 디스크 여유 40GB+ (Ollama 모델 + 레이어 캐시).

**동작 (4 단계)**:

1. Jenkins 플러그인 hpi 재귀 다운로드 → `offline/jenkins-plugins/` (40-50 개)
2. Dify 플러그인 `.difypkg` 다운로드 → `offline/dify-plugins/`
3. Docker buildx 3-stage 빌드 → Ollama 모델 pull + Dify 서비스 파일 + 최종 런타임 구성
4. `docker save | gzip -1` → `dscore.ttc.playwright-<ts>.tar.gz`

**주요 env** (override 가능):

| 변수 | 기본값 | 비고 |
| --- | --- | --- |
| `IMAGE_TAG` | `dscore.ttc.playwright:latest` | 이미지 repo:tag. 다중 버전 운영 시 `:v1`, `:v2` 등으로 override |
| `TARGET_PLATFORM` | `linux/amd64` | Windows WSL2/Linux 서버 대응. arm64 타겟이면 `linux/arm64` |
| `OLLAMA_MODEL` | `gemma4:e4b` | Stage 1 에서 `ollama pull` 할 모델 (~4GB) |
| `OUTPUT_TAR` | `dscore.ttc.playwright-<ts>.tar.gz` | 출력 tar.gz 파일명 |
| `JENKINS_VERSION` | (빌드 시 자동 추출) | 2.479+ 요구. override 시 명시 가능 |

**소요**: 최초 30-90분 (모델 pull 포함), 캐시 재사용 5-10분.

### 6.2 `entrypoint-allinone.sh` — 컨테이너 PID 1

**실행 방식**: `docker run` 이 자동 호출. 직접 호출할 일 없음.

**주요 동작**:

1. `/data/.initialized` 없으면 `/opt/seed/*` → `/data/` 복사 (pg/jenkins/dify/ollama 모델)
2. `supervisord` 백그라운드 기동 → 11개 서비스
3. 헬스 대기 → Dify `/install` + Jenkins `/login` 모두 200
4. `/data/.app_provisioned` 없으면 `bash /opt/provision-apps.sh` 호출 (§6.3)
5. Jenkins Node `in-container-agent` 의 NODE_SECRET 추출 → supervisord 에 `jenkins-agent` 프로그램 start 요청
6. supervisord foreground wait (`wait $SUPERVISORD_PID`)

**재시작 시**: seed 와 provision 은 플래그로 스킵 → 30-60초 내 ready. jenkins-agent 는 매번 NODE_SECRET 을 다시 추출해 기동 (멱등).

### 6.3 `provision-apps.sh` — Dify/Jenkins 프로비저닝 (컨테이너 내부)

**트리거**: entrypoint 가 `.app_provisioned` 없을 때 자동 호출.

**수동 재실행** (Dify 등록 꼬임 / Jenkins Node 재생성 등 복구):

```bash
docker exec dscore.ttc.playwright bash /opt/provision-apps.sh
```

**수행 내용**:

- **Dify**: 관리자 생성 → 로그인 → 로컬 `.difypkg` 업로드 (marketplace 호출 없음) → Ollama 모델 등록 (`base_url=http://127.0.0.1:11434` — 컨테이너 loopback) → Chatflow import → Publish → API Key 발급
- **Jenkins**: 플러그인 로드 검증 (이미지에 seed 된 hpi) → Credentials 등록 → Pipeline Job 생성 → Node `in-container-agent` 등록 (loopback JNLP)

**완전 재프로비저닝**:

```bash
docker exec dscore.ttc.playwright rm -f /data/.app_provisioned
docker restart dscore.ttc.playwright
```

**주의**: 이 스크립트는 오프라인 단일 이미지 전용. 원본 `setup.sh` 는 이미지에 포함되지 않으며 본 스크립트가 완전히 대체한다.

### 6.4 보조 파일

| 파일 | 역할 | 수정 빈도 |
| --- | --- | --- |
| `Dockerfile.allinone` | 3-stage 멀티 빌드 (Ollama / Dify 서비스 / 최종 런타임) | 드묾 |
| `pg-init-allinone.sh` | 빌드 타임 `initdb` + Dify 5개 DB 생성 | Dify 버전업 시 |
| `supervisord.conf` | 11개 컨테이너 프로세스 정의 (ollama / jenkins-agent 포함) | 서비스 추가/제거 시 |
| `nginx-allinone.conf` | Dify upstream 프록시 (localhost) | 드묾 |
| `requirements-allinone.txt` | 컨테이너 Python 의존성 (Dify api/worker + Playwright) | Dify 버전업 시 |

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
