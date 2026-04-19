# Zero-Touch QA All-in-One (Apple Silicon Mac 전용 이미지)

`docker run` **한 번** 으로 Jenkins + Dify 1.13.3 + PostgreSQL + Redis + Qdrant + Playwright 를 기동하고, **호스트 Mac 의 Ollama** 를 Metal GPU 가속 LLM 으로 사용하는 All-in-One 이미지.

본 디렉토리는 `feat/allinone-mac-host-ollama` 브랜치 전용 — 빌드 스크립트 + 이미지 정의 + 프로비저닝 자동화 + 가이드 일체.

---

## 개요

### 설계 요점

Mac Docker Desktop 은 Linux 컨테이너에 **Metal GPU passthrough 를 지원하지 않는다**. 컨테이너 내부에 Ollama 를 넣어도 CPU 모드로 떨어져 `gemma4:e4b` 기준 1-2 tok/s 수준 — Planner LLM 이 Dify Chatflow 타임아웃을 초과해 Jenkins Pipeline 이 실패한다.

해결책은 **호스트 macOS 에 Ollama 를 직접 설치** 하고, 컨테이너의 Dify 가 `host.docker.internal:11434` 로 호스트 Ollama 를 호출하게 하는 것. 이 구조를 전제로 컨테이너 이미지는 내부 Ollama 바이너리·모델을 완전히 제거해 가벼워졌다.

### 이미지에 포함된 것

- Jenkins (컨트롤러 + 내부 JNLP 에이전트) — JDK 21, Python 3.11+, Playwright + Chromium
- Dify 1.13.3 — api / worker / worker_beat / web / plugin_daemon
- PostgreSQL 15 (Dify 5개 DB 사전 생성), Redis 7, Qdrant 1.8.3
- Jenkins 플러그인 hpi (workflow-aggregator + 의존성 40+개, plain-credentials, file-parameters, htmlpublisher)
- Dify 플러그인 `langgenius/ollama` (Ollama 클라이언트 — 모델은 호스트 제공)
- nginx (localhost upstream reverse proxy), supervisord (PID 1, **11개** 프로세스)
- QA 런타임: `/opt/zero_touch_qa` + 전역 venv `/opt/qa-venv` + Playwright Chromium

### 이미지에 포함되지 **않은** 것

- Ollama 바이너리 `/usr/bin/ollama` — 호스트에서 `ollama` 명령 사용
- Ollama 모델 파일 — 호스트 `~/.ollama/models/` 에 `ollama pull` 로 받음
- supervisord `[program:ollama]` 프로그램 — 컨테이너 안에서 돌지 않음

### 트레이드오프

| 항목 | 값 |
| --- | --- |
| 이미지 크기 (비압축) | ~10GB (주로 dify-api 2.3GB / Python 1.3GB / Playwright Chromium 930MB) |
| 배포 파일 tar.gz | **2-3GB** (gzip) |
| 빌드 시간 | **10-30분** (초기), 빌드 캐시 재사용 시 **3-5분** |
| 첫 기동 시간 | **3-5분** (seed + provision-apps.sh 완주) |
| 이후 기동 시간 | 30-60초 (volume 재사용 시) |
| 호스트 RAM 권장 | 16GB+ (컨테이너 ~6GB + 호스트 Ollama + 모델 ~5-10GB) |
| LLM 성능 | Metal GPU **50-80 tok/s** (Apple Silicon) |
| 외부 포트 | 18080 (Jenkins), 18081 (Dify), 50001 (JNLP) |

---

## 빠른 시작 (5단계, 10-15분)

**전제**: macOS 13+, Apple Silicon, Docker Desktop 4.30+, RAM 16GB+, 인터넷 가능.

### 1단계 — 호스트 Ollama 준비 (Metal 가속)

```bash
# Homebrew 설치
brew install ollama
brew services start ollama     # 백그라운드 데몬, 재부팅 시 자동 기동

# 모델 pull (~4GB, 1-3분)
ollama pull gemma4:e4b

# 동작 확인
ollama list                    # gemma4:e4b 보여야 함
curl -fsS http://127.0.0.1:11434/api/tags  # {"models":[...]} 응답
```

> 공식 GUI 앱을 선호하면 <https://ollama.com/download/Mac> 에서 다운로드 후 Applications 로 이동. 메뉴바 아이콘에서 데몬 상태 관리.

### 2단계 — 이미지 빌드 (3-5분, 캐시 재사용 시)

```bash
git clone <이 저장소> && cd dscore-ttc/e2e-pipeline
./offline/build-allinone.sh 2>&1 | tee /tmp/build.log
```

종료 시 `e2e-pipeline/dscore-qa-allinone-<timestamp>.tar.gz` (2-3GB) 생성. 빌드 단계 상세는 [§1.3](#13-빌드-단계-내부-이해) 참조.

### 3단계 — 컨테이너 기동

```bash
# 이미지 로드
docker load -i dscore-qa-allinone-*.tar.gz

# 기동 — Mac env 포함 필수
docker run -d --name dscore-qa \
  -p 18080:18080 -p 18081:18081 -p 50001:50001 \
  -v dscore-data:/data \
  --add-host host.docker.internal:host-gateway \
  -e OLLAMA_BASE_URL=http://host.docker.internal:11434 \
  -e OLLAMA_MODEL=gemma4:e4b \
  --restart unless-stopped \
  dscore-qa:allinone
```

### 4단계 — 기동 관찰 (약 3-5분)

```bash
docker logs -f dscore-qa
```

`[entrypoint-allinone] 준비 완료. supervisord wait...` 가 찍히면 완료. `Ctrl+C` 로 로그 스트림만 종료 (컨테이너는 계속 실행).

브라우저 접속:

| URL | 기본 계정 |
| --- | --- |
| <http://localhost:18080> | Jenkins — `admin / password` |
| <http://localhost:18081> | Dify — `admin@example.com / Admin1234!` |

### 5단계 — 첫 Pipeline 실행

1. Jenkins Dashboard → `DSCORE-ZeroTouch-QA-Docker` → **Build with Parameters**
2. 파라미터:
   - `RUN_MODE`: `chat`
   - `TARGET_URL`: `https://www.naver.com` (Google 은 봇 차단 걸리기 쉬움)
   - `SRS_TEXT`: `검색창에 DSCORE 입력 후 엔터`
   - **`HEADLESS`: 반드시 체크** ← 컨테이너에 X display 없어 headed 는 실패
3. **Build** → Console Output 관찰
4. 약 30-60초 내 `Finished: SUCCESS` + `Zero-Touch QA Report` 생성

실패 시 [§5 트러블슈팅](#5-트러블슈팅) 참조.

---

## 1. 빌드 (상세)

### 1.1 사전 준비

#### 호스트 요구사항

| 항목 | 최소 | 확인 |
| --- | --- | --- |
| macOS | 13 (Ventura) | `sw_vers` |
| 칩 | Apple Silicon (M1-M4) | `uname -m` → `arm64` |
| Docker Desktop | 4.30 | `docker --version` |
| Docker buildx | 활성 | `docker buildx version` |
| JDK | 11+ (플러그인 다운로드용) | `java -version` |
| 디스크 여유 | 20GB+ | `df -h .` |
| 인터넷 | 외부망 가능 | `curl -I https://updates.jenkins.io` |

#### 외부 네트워크 도메인 (방화벽 화이트리스트)

| 도메인 | 용도 |
| --- | --- |
| `updates.jenkins.io`, `get.jenkins.io`, `mirrors.jenkins.io` | Jenkins 플러그인 |
| `marketplace.dify.ai` | Dify 플러그인 메타·다운로드 |
| `github.com`, `objects.githubusercontent.com` | jenkins-plugin-manager.jar, qdrant, node |
| `registry-1.docker.io`, `auth.docker.io` | Docker Hub (base 이미지 pull) |
| `pypi.org`, `files.pythonhosted.org` | Python 패키지 |
| `playwright.azureedge.net` | Chromium 브라우저 |
| `apt.postgresql.org`, `deb.debian.org` | OS 패키지 |

#### 사전 점검

```bash
cd e2e-pipeline

# 필수 입력 파일 확인
ls -1 dify-chatflow.yaml DSCORE-ZeroTouch-QA-Docker.jenkinsPipeline
ls -d zero_touch_qa jenkins-init

# Docker 동작 + buildx
docker run --rm hello-world
docker buildx ls
```

### 1.2 빌드 실행

대부분의 경우 옵션 없이 실행하면 된다 (Apple Silicon 네이티브 빌드).

```bash
./offline/build-allinone.sh 2>&1 | tee /tmp/build.log
```

#### 환경변수 옵션

| 변수 | 기본값 | 언제 바꾸나 |
| --- | --- | --- |
| `TARGET_PLATFORM` | `linux/amd64` | Apple Silicon 네이티브 = `linux/arm64`, Linux 배포 대상은 기본값 유지 |
| `OLLAMA_MODEL` | `gemma4:e4b` | Dify provider 에 등록할 모델 id. **호스트에 실제로 pull 된 이름과 일치해야 함** |
| `IMAGE_TAG` | `dscore-qa:allinone` | 여러 버전을 구분해 둘 때 |
| `OUTPUT_TAR` | `dscore-qa-allinone-<timestamp>.tar.gz` | 출력 파일명 고정 시 |
| `JENKINS_VERSION` | (자동 감지) | 특정 LTS 고정 필요 시 (예: `2.479.3`) |

적용 방법 (일회성):

```bash
TARGET_PLATFORM=linux/arm64 OLLAMA_MODEL=qwen2.5:7b \
  ./offline/build-allinone.sh 2>&1 | tee /tmp/build.log
```

로그 앞부분에 `[build-allinone] 빌드 대상: ... (platform=...)` 이 찍혀 내가 준 값이 반영됐는지 즉시 확인 가능.

### 1.3 빌드 단계 내부 이해

#### [1/4] Jenkins 플러그인 hpi 다운로드

`jenkins-plugin-manager.jar` 로 `workflow-aggregator` / `file-parameters` / `htmlpublisher` / `plain-credentials` 와 재귀 의존성을 `offline/jenkins-plugins/` 에 모은다 (40-50개, ~150MB).

Jenkins 버전은 `jenkins/jenkins:lts-jdk21` 이미지에서 동적 추출 — 플러그인-호환 버전 불일치 방지.

#### [2/4] Dify 플러그인 `.difypkg` 다운로드

`marketplace.dify.ai` 에서 `langgenius/ollama` 의 최신 버전 UID 조회 → `.difypkg` 바이너리를 `offline/dify-plugins/` 에 저장 (~5MB).

#### [3/4] Docker buildx build — 2-stage 멀티빌드

| Stage | base 이미지 | 최종에 포함되는 것 |
| --- | --- | --- |
| 1a. `dify-api-src` | `langgenius/dify-api:1.13.3` | `/app` → `/opt/dify-api` |
| 1b. `dify-web-src` | `langgenius/dify-web:1.13.3` | `/app` → `/opt/dify-web` |
| 1c. `dify-plugin-src` | `langgenius/dify-plugin-daemon:0.5.3-local` | `/app` → `/opt/dify-plugin-daemon` |
| 2. final | `jenkins/jenkins:lts-jdk21` | OS 패키지 + Stage 1 산출물 + Python venv + Playwright |

Stage 2 최종 레이아웃:

```
jenkins/jenkins:lts-jdk21 (Debian trixie)
├─ APT: postgresql-15 (pgdg repo), redis, nginx, supervisor, python3, tini,
│       libreoffice-impress, fonts-noto-cjk, Playwright 의존성
├─ DOWNLOAD: qdrant v1.8.3 tar.gz → /usr/local/bin/qdrant
├─ COPY: Stage 1a/b/c 산출물 → /opt/dify-{api,web,plugin-daemon}
├─ pip install: requirements-allinone.txt + deepeval==1.3.5
├─ playwright install --with-deps chromium
├─ 전역 venv: /opt/qa-venv (--system-site-packages, 3.13)
├─ /opt/scripts-home/zero_touch_qa → /opt/zero_touch_qa (symlink)
├─ COPY: offline/jenkins-plugins/*.hpi → /opt/seed/jenkins-plugins/
├─ COPY: offline/dify-plugins/*.difypkg → /opt/seed/dify-plugins/
├─ RUN: pg-init-allinone.sh (initdb + 5개 DB 생성 → /opt/seed/pg)
├─ COPY: supervisord.conf, nginx-allinone.conf, entrypoint, provision-apps.sh
└─ ENTRYPOINT: tini → /entrypoint.sh
```

> **Ollama 스테이지 없음** — 본 브랜치 핵심 특징. 원본 `feat/allinone-offline-image` 대비 4-5GB 작고 빌드 20-60분 단축.

#### [4/4] docker save + gzip

빌드된 이미지를 `docker save | gzip -1` 로 단일 파일로 직렬화. `gzip -1` 은 빠른 압축 — Docker layer 가 이미 압축돼 있어 추가 압축 효과가 작고 시간 비용이 크다.

### 1.4 빌드 검증

```bash
# 이미지 존재 + 크기
docker images dscore-qa:allinone
# 예상: dscore-qa:allinone  <hash>  <date>  ~10GB

# tar.gz 크기
ls -lh dscore-qa-allinone-*.tar.gz      # 2-3GB

# 이미지 내부 seed 무결성
docker run --rm --entrypoint bash dscore-qa:allinone -lc '
  echo "Jenkins plugins:" && ls /opt/seed/jenkins-plugins | wc -l
  echo "Dify plugins:"    && ls /opt/seed/dify-plugins
  echo "PG snapshot:"     && du -sh /opt/seed/pg
  echo "Qdrant:"          && qdrant --version
  echo "qa-venv:"         && /opt/qa-venv/bin/python -c "import requests,playwright,PIL; print(\"OK\")"
  echo "zero_touch_qa:"   && PYTHONPATH=/opt/scripts-home /opt/qa-venv/bin/python -c "import zero_touch_qa; print(zero_touch_qa.__version__)"
'
```

---

## 2. 기동 (상세)

### 2.1 호스트 전제조건

| 항목 | 확인 방법 |
| --- | --- |
| 호스트 Ollama 실행 중 | `curl http://127.0.0.1:11434/api/tags` → 200 |
| 사용할 모델 pull 됨 | `ollama list` 의 NAME 컬럼에 `OLLAMA_MODEL` 값 포함 |
| Docker Desktop 메모리 여유 | Settings → Resources → Memory ≥ 8GB (권장 12GB+) |
| 포트 사용 가능 | `lsof -i :18080 -i :18081 -i :50001` 비어있음 |

### 2.2 `docker run` 해설

```bash
docker run -d --name dscore-qa \
  -p 18080:18080 -p 18081:18081 -p 50001:50001 \
  -v dscore-data:/data \
  --add-host host.docker.internal:host-gateway \
  -e OLLAMA_BASE_URL=http://host.docker.internal:11434 \
  -e OLLAMA_MODEL=gemma4:e4b \
  --restart unless-stopped \
  dscore-qa:allinone
```

| 옵션 | 역할 |
| --- | --- |
| `-p 18080:18080` | Jenkins 웹 UI |
| `-p 18081:18081` | Dify 웹 UI (nginx reverse proxy) |
| `-p 50001:50001` | Jenkins JNLP (내부 agent, 외부 노출 선택) |
| `-v dscore-data:/data` | 모든 상태 (PG/Redis/Qdrant/Dify storage/Jenkins home) 영속. **재기동 시 동일 볼륨 유지 필수** |
| `--add-host host.docker.internal:host-gateway` | 컨테이너 → 호스트 게이트웨이 해석. Mac Docker Desktop 은 자동 해석하나 습관적으로 포함 |
| `-e OLLAMA_BASE_URL=http://host.docker.internal:11434` | Dify 의 Ollama provider 가 호출할 URL. 생략 시 entrypoint 기본값으로 자동 설정됨 |
| `-e OLLAMA_MODEL=gemma4:e4b` | Dify 에 등록할 모델 id. 호스트 `ollama list` 의 NAME 과 일치 필수 |
| `--restart unless-stopped` | Docker 재시작 시 자동 기동 |

#### 비밀번호 사전 주입 (옵션)

기본 계정 `admin/password`, `admin@example.com/Admin1234!` 가 마음에 안 들면 **첫 기동 전에** env 로 주입:

```bash
docker run -d --name dscore-qa ... \
  -e JENKINS_ADMIN_USER=admin \
  -e JENKINS_ADMIN_PW='<strong-password>' \
  -e DIFY_EMAIL=admin@corp.example \
  -e DIFY_PASSWORD='<strong-password>' \
  ... dscore-qa:allinone
```

- [jenkins-init/basic-security.groovy](../jenkins-init/basic-security.groovy) 가 Jenkins env 로 초기 계정 생성
- [provision-apps.sh](provision-apps.sh) 가 동일 env 로 Dify 관리자 생성
- **주의**: `/data/.initialized` 플래그가 이미 있으면 env 무시됨 → 기존 계정 유지. 그 경우 §3 에서 UI 로 변경.

### 2.3 기동 타임라인

`docker logs -f dscore-qa` 에서 관측되는 이벤트 (첫 기동 기준):

| 경과 | 로그 마커 | 의미 |
| --- | --- | --- |
| 0:00 | `[entrypoint-allinone] 최초 seed: /opt/seed → /data` | 시드 디렉토리 복사 (Ollama 없어 빠름) |
| ~0:05 | `[entrypoint-allinone] seed 완료.` | 복사 끝, supervisord 기동 직전 |
| 0:05 | `[entrypoint-allinone] supervisord 기동...` | **11개** 프로세스 순차 시작 |
| 0:05-0:30 | postgresql / redis / qdrant ready | 스토리지 초기화 |
| 0:30-1:00 | dify-plugin-daemon / dify-api ready (Alembic migration 완료) | Python app 기동 |
| 1:00-1:30 | `[▶] === 1. 서비스 헬스체크 ===` | provision-apps.sh 시작 |
| 1:30-2:30 | `[▶] === 2. Dify 초기 설정 ===` (관리자 생성 → 로그인 → 플러그인 → 모델 등록 → credential swap → Redis FLUSH → 재기동) | Dify 자동 설정 |
| 2:30-3:30 | `[✓] Import 완료` → `Publish 성공` → `API Key 발급 완료` | Chatflow import |
| 3:30-4:30 | `[▶] === 3. Jenkins 초기 설정 ===` (플러그인 검증 → Credentials → Job → Node) | Jenkins 자동 설정 |
| ~4:30-5:00 | `[entrypoint-allinone] 앱 프로비저닝 완료.` → `jenkins-agent 기동 요청 완료` → `준비 완료. supervisord wait...` | 사용 가능 |

**재기동** (`docker restart`): 30-60초. `/data/.initialized` + `/data/.app_provisioned` 플래그로 seed/provision 모두 스킵.

#### 로그 마커 해석

| 마커 | 의미 |
| --- | --- |
| `[▶]` | 큰 단계 시작 |
| `[·]` | 하위 진행 |
| `[✓]` | 성공 |
| `[⚠]` | 비치명 경고 — 다음 단계로 진행 |
| `[✗]` | 치명 오류 |

경고/오류 한 줄 추출:

```bash
docker logs dscore-qa 2>&1 | grep -E '\[⚠|\[✗'
```

### 2.4 프로비저닝 체크리스트

타임라인 완료 후 아래 9개 항목이 모두 ✓ 여야 정상.

| # | 항목 | CLI 확인 |
| - | --- | --- |
| 1 | Dify 관리자 생성 | `curl -fsS http://localhost:18081/console/api/setup \| jq .setup_status` → `"finished"` |
| 2 | Dify Ollama 플러그인 설치 | `docker exec dscore-qa ls /data/dify/plugins/packages` 에 `langgenius-ollama-*.difypkg` |
| 3 | Ollama 모델 등록 (호스트 URL) | [§4.1](#41-현재-상태-확인) 의 DB 조회로 `base_url: host.docker.internal:11434` |
| 4 | Chatflow import | `docker exec dscore-qa ls /data/dify/storage/apps 2>/dev/null \| wc -l` ≥ 1 |
| 5 | Dify API Key 발급 | `docker logs dscore-qa \| grep "API Key 발급 완료"` |
| 6 | Jenkins 플러그인 4개 로드 | `curl -fsS -u admin:password 'http://localhost:18080/pluginManager/api/json?depth=1' \| jq -r '.plugins[].shortName' \| grep -cE 'workflow-aggregator\|plain-credentials\|file-parameters\|htmlpublisher'` → `4` |
| 7 | Jenkins Credentials `dify-qa-api-token` | `curl -fsS -u admin:password 'http://localhost:18080/credentials/store/system/domain/_/api/json?depth=1' \| jq -r '.credentials[].id'` 에 포함 |
| 8 | Pipeline Job 생성 | `curl -fsS -o /dev/null -w '%{http_code}' -u admin:password http://localhost:18080/job/DSCORE-ZeroTouch-QA-Docker/api/json` → `200` |
| 9 | Node `mac-ui-tester` Online | `curl -fsS -u admin:password http://localhost:18080/computer/mac-ui-tester/api/json \| jq .offline` → `false` |

비밀번호를 [사전 주입](#비밀번호-사전-주입-옵션) 방식으로 바꿨다면 위 명령의 `admin:password` 교체.

### 2.5 시나리오 검증 (Jenkins Pipeline)

1. 브라우저 `http://localhost:18080` → `admin` 로그인
2. Dashboard → `DSCORE-ZeroTouch-QA-Docker` → **Build with Parameters**
3. 파라미터 입력:

   | 파라미터 | 권장 값 | 비고 |
   | --- | --- | --- |
   | `RUN_MODE` | `chat` | 자연어 → 자동 시나리오 생성 |
   | `TARGET_URL` | `https://www.naver.com` | Google 은 봇 차단 잦음 |
   | `SRS_TEXT` | `검색창에 DSCORE 입력 후 엔터` | |
   | **`HEADLESS`** | **반드시 체크** | 컨테이너에 X display 없음 |

4. **Build** → Console Output 관찰 — 예상 시간: **30-60초**
5. 성공 조건: `Finished: SUCCESS` + 좌측 메뉴에 `Zero-Touch QA Report` 링크

실패 시 Console Output 에서 첫 `ERROR` 라인을 찾고 [§5](#5-트러블슈팅) 의 해당 항목 참조.

---

## 3. 운영

### 재시작 (상태 유지)

```bash
docker restart dscore-qa      # 30-60초
```

Dify App, Jenkins Job, Credentials, 실행 이력 모두 유지.

### 중지 / 제거

```bash
docker stop dscore-qa                  # 일시 정지 (상태 유지)
docker rm -f dscore-qa                 # 컨테이너만 제거 (볼륨 유지)
docker volume rm dscore-data           # 완전 초기화 (모든 데이터 소실)
```

### 로그 확인

서비스별 로그는 `/data/logs/` 에 분리 저장:

```bash
docker exec dscore-qa tail -f /data/logs/dify-api.log
docker exec dscore-qa tail -f /data/logs/jenkins.log
docker exec dscore-qa supervisorctl status            # 프로세스 상태
```

### 프로세스 제어

```bash
docker exec dscore-qa supervisorctl restart dify-api
docker exec dscore-qa supervisorctl restart all
```

### 백업

`/data` 볼륨 하나만 백업하면 된다:

```bash
docker run --rm -v dscore-data:/data -v $PWD:/backup busybox \
  tar czf /backup/dscore-data-$(date +%Y%m%d).tar.gz /data
```

### 복원

```bash
docker stop dscore-qa
docker run --rm -v dscore-data:/data -v $PWD:/backup busybox \
  tar xzf /backup/dscore-data-YYYYMMDD.tar.gz -C /
docker start dscore-qa
```

### 업그레이드

```bash
docker stop dscore-qa
docker rm dscore-qa                     # 볼륨은 유지

docker load -i dscore-qa-allinone-new.tar.gz
docker run -d --name dscore-qa \
  -p 18080:18080 -p 18081:18081 -p 50001:50001 \
  -v dscore-data:/data \
  --add-host host.docker.internal:host-gateway \
  -e OLLAMA_BASE_URL=http://host.docker.internal:11434 \
  -e OLLAMA_MODEL=gemma4:e4b \
  dscore-qa:allinone
```

PG 스키마 변경이 있으면 dify-api 기동 시 Alembic 이 자동 마이그레이션.

### 관리자 비밀번호 변경

| 상황 | 방법 |
| --- | --- |
| 첫 배포 직전 | [비밀번호 사전 주입](#비밀번호-사전-주입-옵션) — 가장 안전 |
| 운영 중 Jenkins | People → `admin` → Configure → Password |
| 운영 중 Dify | 우상단 계정 아이콘 → Settings → Account → Password |

---

## 4. Ollama 모델 관리 (호스트 기반)

**이 이미지는 컨테이너 내부에 Ollama 가 없다.** 모든 모델 관리는 **macOS 호스트의 `ollama` 명령** 으로 수행한다. `docker exec ... ollama` 는 `command not found` 로 실패.

### 4.1 현재 상태 확인

Mac 터미널에서:

```bash
# 호스트 Ollama 모델 목록
ollama list

# 개별 모델 세부 정보
ollama show gemma4:e4b

# 호스트 저장 경로
du -sh ~/.ollama/models
```

Dify 쪽 등록 상태 (DB 직조회):

```bash
docker exec dscore-qa bash -c "
PGPASSWORD=difyai123456 psql -h 127.0.0.1 -U postgres -d dify -c \"
SELECT pm.model_name, pmc.credential_name,
       substring(pmc.encrypted_config, 1, 100) AS cfg
  FROM provider_models pm
  JOIN provider_model_credentials pmc ON pm.credential_id = pmc.id
 WHERE pm.provider_name LIKE '%ollama%';\"
"
# 기대: base_url 에 "host.docker.internal:11434"
```

### 4.2 새 모델로 교체 (호스트 pull + 재기동)

```bash
# 1) Mac 터미널 — 새 모델 pull
ollama pull llama3.1:8b

# 2) 호스트에서 시험 호출 (Metal 가속 확인)
ollama run llama3.1:8b "간단히 소개해줘"

# 3) 컨테이너 재생성 (기존 볼륨 유지)
docker rm -f dscore-qa
docker run -d --name dscore-qa \
  -p 18080:18080 -p 18081:18081 -p 50001:50001 \
  -v dscore-data:/data \
  --add-host host.docker.internal:host-gateway \
  -e OLLAMA_BASE_URL=http://host.docker.internal:11434 \
  -e OLLAMA_MODEL=llama3.1:8b \
  --restart unless-stopped \
  dscore-qa:allinone

# 4) provision 강제 재실행 — Dify provider 갱신 + Redis FLUSH 자동 수행
docker exec dscore-qa rm -f /data/.app_provisioned
docker restart dscore-qa
```

#### 모델 선택 가이드 (호스트 기준)

| 모델 | 크기 | 호스트 RAM | 용도 |
| --- | --- | --- | --- |
| `gemma4:e4b` | ~4GB | 6-8GB | 기본 — 빠름 |
| `llama3.1:8b` | ~4.7GB | 8-10GB | 품질↑ |
| `qwen2.5:7b` | ~4.4GB | 8-10GB | 다국어 |
| `gemma2:2b` | ~1.5GB | 4GB | 저사양 |
| `llama3.1:70b` | ~40GB | 80GB+ | Mac 16GB 에서 불가 |

> **Chatflow DSL 의 모델 id 하드코딩 주의**: [dify-chatflow.yaml](../dify-chatflow.yaml) 의 LLM 노드에 `gemma4:e4b` 같은 id 가 박혀 있다. `OLLAMA_MODEL` env 를 바꾸면 provider 등록은 바뀌지만 Chatflow 는 계속 옛 모델을 쓴다. 해결: [§4.4](#44-chatflow-에서-사용-모델-변경) 참조.

### 4.3 Dify 에 추가 모델 등록 (자동 말고 수동으로)

provision-apps.sh 는 `OLLAMA_MODEL` 한 개만 등록한다. 여러 모델을 Dify UI 드롭다운에 올리고 싶을 때:

**UI 경로**:

1. Dify → 우상단 계정 → **Settings** → **Model Provider**
2. **Ollama** 카드 → **+ Add Model**
3. 입력:
   - Model Name: `llama3.1:8b` (호스트 `ollama list` NAME 그대로)
   - **Base URL: `http://host.docker.internal:11434`** ← 반드시 호스트 URL
   - Completion mode: `Chat`
   - Context size: `8192` · Max tokens: `4096`
   - Vision / Function call: 끄기
4. **Save**

> UI 로 수동 추가한 credential 은 drop-down 에 뜨지만 Chatflow 가 쓰려면 [§4.4](#44-chatflow-에서-사용-모델-변경) 로 선택.

### 4.4 Chatflow 에서 사용 모델 변경

Jenkins Pipeline 이 호출하는 Dify App 은 DSL 안에 모델 id 가 박혀 있다. provider 등록만 바꿔도 Chatflow 는 옛 모델을 쓴다.

**UI 경로 (가장 빠름)**:

1. Dify → Apps → `DSCORE-ZeroTouch-QA` 클릭
2. 캔버스에서 **LLM 노드** (Planner / Healer) 클릭
3. 오른쪽 패널 **Model** 드롭다운 → 새 모델 선택
4. 캔버스 상단 **Publish** → "Publish as API"
5. Jenkins Pipeline 재실행 시 즉시 반영. API Key 는 그대로라 Credentials 갱신 불필요.

### 4.5 모델 삭제

```bash
# Mac 터미널
ollama rm gemma4:e4b
du -sh ~/.ollama/models      # 디스크 회수 확인
```

Dify 쪽 등록 삭제는 UI: Settings → Model Provider → Ollama → 해당 모델 행의 휴지통.

> **경고**: Chatflow 가 참조 중인 모델을 삭제하면 Pipeline 이 `model "...": not found` 로 실패. 삭제 전 [§4.4](#44-chatflow-에서-사용-모델-변경) 로 먼저 교체.

### 4.6 Ollama 런타임 튜닝 (호스트)

| 환경변수 | 기본 | 의미 |
| --- | --- | --- |
| `OLLAMA_HOST` | `127.0.0.1:11434` | 바인드 주소. Docker Desktop Mac 은 `host-gateway` 로 자동 해석 |
| `OLLAMA_MAX_LOADED_MODELS` | `1` | 동시 로드 모델 수 |
| `OLLAMA_KEEP_ALIVE` | `5m` | 모델 언로드 지연. `-1` = 영구 상주 |
| `OLLAMA_NUM_PARALLEL` | `1` | 동시 요청 수 |
| `OLLAMA_FLASH_ATTENTION` | 미설정 | `1` = FA 활성 |

호스트에서 설정:

```bash
# Homebrew 서비스 (launchctl plist)
brew services stop ollama
launchctl setenv OLLAMA_KEEP_ALIVE -1
launchctl setenv OLLAMA_MAX_LOADED_MODELS 2
brew services start ollama

# 또는 foreground (디버깅)
OLLAMA_KEEP_ALIVE=-1 ollama serve
```

Dify 쪽 `context_size` / `max_tokens` 는 [provision-apps.sh:49-50](provision-apps.sh#L49-L50) 의 env 로 첫 기동 시 주입하거나 UI ([§4.3](#43-dify-에-추가-모델-등록-자동-말고-수동으로)) 에서 수정.

---

## 5. 트러블슈팅

### 컨테이너가 기동 직후 죽는다

```bash
docker logs dscore-qa | tail -50
```

- 메모리 부족 → Docker Desktop Memory 할당 증가
- PostgreSQL 초기화 실패 → `/data/logs/postgresql.err.log` 확인. 볼륨 권한 문제면 `docker volume rm dscore-data` (모든 데이터 소실)

### Pipeline Stage 3 가 `Dify /v1/chat-messages` 400 또는 timeout

Mac 브랜치에서 3대 원인:

1. **호스트 Ollama 미기동** — `ollama list` 실패 시 `brew services start ollama`
2. **모델 이름 불일치** — `OLLAMA_MODEL` env 와 `ollama list` NAME 컬럼 비교
3. **`--add-host` 누락** — `docker exec dscore-qa curl -fsS http://host.docker.internal:11434/api/tags` 실패 시 docker run 에 `--add-host host.docker.internal:host-gateway` 재추가

### Pipeline 이 `Cannot open display` / Chromium `Page crashed!`

- **`Cannot open display`** → Build with Parameters 에서 `HEADLESS` 를 체크하지 않고 실행한 경우. 컨테이너엔 X server 없음. 체크박스 활성화 후 재빌드.
- **`Page crashed!`** → `/dev/shm` 공유 메모리 부족. `docker run` 에 `--shm-size=2g` 추가:

  ```bash
  docker run ... --shm-size=2g ... dscore-qa:allinone
  ```

### Dify 가 여전히 옛 base_url 로 호출 (`127.0.0.1:11434 → Connection refused`)

증상: `OLLAMA_BASE_URL=http://host.docker.internal:11434` 로 설정했는데도 dify-api 로그에 `127.0.0.1:11434` 로 호출 실패.

**원인**: Dify 의 credential 저장 구조. POST `/models/credentials` 는 새 레코드만 추가하고 `provider_models.credential_id` 는 갱신하지 않음. Redis `provider_model_credentials:*` 도 캐시 별개.

**해결**: provision-apps.sh 재실행 — 2-3b(POST) / 2-3c(DB swap) / 2-3d(Redis FLUSH) / 2-3e(재기동) 연쇄 자동 수행:

```bash
docker exec dscore-qa bash /opt/provision-apps.sh
```

또는 완전 재프로비저닝:

```bash
docker exec dscore-qa rm -f /data/.app_provisioned
docker restart dscore-qa
```

### Pipeline 첫 스테이지 `[ERROR] venv 가 존재하지 않거나 손상되었습니다`

- **원인**: 2026-04-19 이전 구버전 이미지. Mac 브랜치 이미지는 `/opt/qa-venv` + `/opt/scripts-home` 을 이미지 빌드 타임에 생성.
- **해결**: 최신 브랜치로 재빌드. 기존 볼륨은 재사용 가능.

### Jenkins 에이전트 `mac-ui-tester` offline / Pipeline 이 `Still waiting to schedule task`

- **원인 1**: `/opt/jenkins-agent-run.sh` 미존재 (`/opt` 는 ephemeral)
- **원인 2**: NODE_SECRET 불일치 (Node 를 삭제·재생성한 경우)
- **해결**: 현행 이미지는 매 기동마다 entrypoint 가 run-script 를 `/data/jenkins-agent/run.sh` 로 재생성 + 심볼릭 링크. 그래도 offline 이면:

  ```bash
  docker restart dscore-qa
  # 여전히 문제면 Node 재등록:
  docker exec dscore-qa bash /opt/provision-apps.sh
  ```

### 프로비저닝이 "1. 서비스 헬스체크" 에서 멈춘다

증상: `docker logs` 가 `[▶] === 1. 서비스 헬스체크 ===` 까지만 찍히고 수 분 이상 진척 없음. Dify / Jenkins UI 접속 불가.

**원인**: dify-api gunicorn worker 의 gevent hub 데드락. `SERVER_WORKER_CONNECTIONS=10` (과거 기본값) 에서 provision 의 curl 재시도로 커넥션 10개 초과 즉시 블록됨.

**해결**: 현행 이미지는 `SERVER_WORKER_CONNECTIONS=1000` + `GUNICORN_TIMEOUT=360` 을 사전 주입해 증상 방지. 구버전 이미지라면 재빌드 필요.

응급 복구 (구버전):

```bash
docker exec dscore-qa bash -c '
  pids=$(pgrep -f "gunicorn.*app:app"); kill -9 $pids 2>/dev/null || true
  sleep 2
  supervisorctl restart dify-api
'
sleep 10
docker exec dscore-qa bash /opt/provision-apps.sh
```

### 시계 오류로 Dify 로그인 `Invalid encrypted data`

Mac 이 sleep 에서 깨어난 직후 Docker Desktop VM 의 시계가 뒤처질 수 있음. JWT 검증 실패.

```bash
# 호스트 시계 강제 동기화
sudo sntp -sS time.apple.com

# Docker Desktop 재기동 (메뉴바 → Restart)
# 또는:
docker restart dscore-qa
```

### 이미지 크기가 USB 이전하기 곤란

tar.gz 가 2-3GB 이므로 USB 이전은 보통 OK. 더 작게 쪼개려면:

```bash
split -b 1G dscore-qa-allinone-*.tar.gz dscore-qa-part-
# 대상 머신에서:
cat dscore-qa-part-* > dscore-qa.tar.gz
docker load -i dscore-qa.tar.gz
```

### 아키텍처 불일치 (`exec format error`)

Mac 에서 Linux x86 서버로 이미지 전송한 경우:

```bash
TARGET_PLATFORM=linux/amd64 ./offline/build-allinone.sh
# (Apple Silicon 에서 qemu 로 크로스 빌드. 느림)
```

---

## 6. 내부 구조 참고

### 파일 레이아웃

```
e2e-pipeline/offline/
├── Dockerfile.allinone          # 2-stage 멀티 빌드 정의
├── build-allinone.sh            # 온라인 빌드 실행 스크립트
├── provision-apps.sh            # 앱 프로비저닝 (Dify/Jenkins REST + DB swap + Redis FLUSH)
├── entrypoint-allinone.sh       # PID 1 진입점 (seed + supervisord + agent 기동)
├── supervisord.conf             # 11개 프로세스 정의
├── nginx-allinone.conf          # localhost upstream nginx
├── pg-init-allinone.sh          # 빌드 타임 PG initdb + DB 생성
├── requirements-allinone.txt    # Python 의존성 통합
├── README.md                    # 이 문서
├── jenkins-plugins/             # 빌드 시 채워짐 (*.hpi, *.jpi)
└── dify-plugins/                # 빌드 시 채워짐 (*.difypkg)
```

### 런타임 프로세스 토폴로지 (11개)

```
supervisord (PID 1, tini 위에)
├─ postgresql (:5432)                       # Dify 5개 DB
├─ redis (:6379)                            # Celery broker + 캐시
├─ qdrant (:6333)                           # Dify 벡터 스토어
├─ dify-plugin-daemon (:5002)               # Go, langgenius/ollama 실행
├─ dify-api (:5001)                         # Flask/gunicorn (gevent 1000 conn)
├─ dify-worker                              # Celery worker
├─ dify-worker-beat                         # Celery beat
├─ dify-web (:3000)                         # Next.js
├─ nginx (:18081 → dify-api, dify-web)      # reverse proxy
├─ jenkins (:18080, :50001)                 # controller
└─ jenkins-agent                            # loopback JNLP (autostart=false, entrypoint 가 start)

호스트 (macOS) — 컨테이너 밖
└─ ollama (:11434)                          # Metal GPU 가속, 컨테이너가 host.docker.internal 로 호출
```

### 볼륨 구조 (`/data`)

```
/data/
├── .initialized              # seed 완료 플래그
├── .app_provisioned          # provision-apps.sh 완료 플래그
├── pg/                       # PostgreSQL data (seed 에서 복사, 5개 DB 사전 생성)
├── redis/                    # Redis AOF
├── qdrant/                   # Qdrant storage
├── jenkins/                  # JENKINS_HOME
│   ├── plugins/              # hpi/jpi
│   ├── jobs/DSCORE-ZeroTouch-QA-Docker/
│   ├── nodes/mac-ui-tester/config.xml  # SCRIPTS_HOME=/opt/scripts-home
│   └── credentials.xml       # dify-qa-api-token
├── jenkins-agent/
│   ├── run.sh                # agent 기동 스크립트 (entrypoint 생성)
│   ├── agent.jar
│   └── workspace/DSCORE-ZeroTouch-QA-Docker/
│       └── .qa_home/venv → /opt/qa-venv  (symlink)
├── dify/
│   ├── storage/              # Dify app storage
│   └── plugins/packages/     # .difypkg 파일
└── logs/                     # 모든 서비스 로그 (ollama.log 는 호스트 ~/.ollama/logs/)
    ├── supervisord.log
    ├── jenkins.log
    ├── dify-api.log
    ├── dify-plugin-daemon.log
    └── ...
```

### 프로비저닝 흐름 (entrypoint-allinone.sh + provision-apps.sh)

1. `entrypoint-allinone.sh` — seed 복사 (`/opt/seed/*` → `/data/*`, `.initialized` 가 없을 때만)
2. supervisord 백그라운드 기동 → 11개 서비스 순차 기동
3. 서비스 헬스 대기 — dify-api `/console/api/setup` + dify-web `/install` + jenkins `/api/json` 전부 HTTP 200 until
4. `bash /opt/provision-apps.sh` (최초 1회, `.app_provisioned` 가 없을 때만):
   - **§1** 헬스체크 + Jenkins crumb 확보
   - **§2-1/2-2** Dify 관리자 생성 + 로그인 (password base64 인코딩)
   - **§2-3a** Ollama 플러그인 업로드 (`.difypkg`)
   - **§2-3b** 모델 공급자 등록 (`OLLAMA_BASE_URL` env 로 host URL)
   - **§2-3c** DB `provider_models.credential_id` swap → host URL 레코드로
   - **§2-3d** Redis `provider_model_credentials:*` 캐시 FLUSH
   - **§2-3e** dify-api / dify-plugin-daemon 재기동 (credential 재로드)
   - **§2-4** Chatflow DSL import → Publish (`-d "{}"`) → API Key 발급
   - **§3-1** Jenkins 플러그인 로드 검증
   - **§3-2** Credentials (`dify-qa-api-token`) 등록
   - **§3-3** Pipeline Job 생성 (Jenkinsfile from `/opt/DSCORE-ZeroTouch-QA-Docker.jenkinsPipeline`)
   - **§3-4** Node `mac-ui-tester` 등록 (SCRIPTS_HOME=`/opt/scripts-home`, `updateNode` 로 디스크 flush)
5. `/data/.app_provisioned` 플래그 생성
6. Jenkins agent 기동 — NODE_SECRET 추출 → `/data/jenkins-agent/run.sh` 생성 → `supervisorctl start jenkins-agent`
7. `wait $SUPERVISOR_PID` 로 foreground 유지
