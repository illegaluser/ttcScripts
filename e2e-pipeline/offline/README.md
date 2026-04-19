# Zero-Touch QA All-in-One (Apple Silicon Mac 하이브리드)

Mac 호스트의 **Ollama 와 Jenkins agent** 를 전제로, Jenkins master + Dify + DB 서비스를 단일 컨테이너에 묶은 All-in-One 이미지. 원본 compose 설계의 핵심 UX (**macOS 화면에 Chromium 창이 뜨는 시각 검증**) 를 유지한다.

본 디렉토리는 `feat/allinone-mac-host-ollama` 브랜치 전용 — 빌드 스크립트 + 이미지 정의 + 프로비저닝 자동화 + 호스트 agent 셋업 스크립트 + 가이드 일체.

---

## 개요

### 아키텍처 — 하이브리드

```text
┌─ 컨테이너 (dscore-qa:allinone) ──────────────────────────────┐
│  Jenkins master  (:18080, :50001 JNLP)                       │
│  Dify api/worker/web/plugin-daemon  (:5001, :3000, :5002)    │
│  PostgreSQL 15 / Redis / Qdrant                               │
│  nginx reverse proxy  (:18081 → Dify)                         │
│  supervisord (10개 프로세스)                                  │
└──────────────────────────┬────────────────────────────────────┘
                           │
      host.docker.internal:11434 (Ollama)     │ JNLP :50001 (Jenkins agent)
                           │                  │
┌──────────────────────────┴──────────────────┴────────────────┐
│  macOS 호스트                                                 │
│  - Ollama 데몬   (Metal GPU 가속, 50-80 tok/s)               │
│  - Jenkins agent (JDK 21, mac-ui-tester) → Playwright 실행   │
│  - Chromium 창   (headed 모드 — 시각 검증)                   │
└───────────────────────────────────────────────────────────────┘
```

### 왜 하이브리드인가

Docker Desktop on Mac 은 Linux 컨테이너에 **GPU / 디스플레이 전달을 모두 지원하지 않는다**:

- Metal GPU passthrough 없음 → 컨테이너 내부 Ollama 는 CPU 1-2 tok/s 로 실용 불가
- X server 없음 → 컨테이너 내부 Playwright headed Chromium 불가

그래서 **성능에 민감한 두 가지 — LLM 추론과 브라우저 렌더링 — 을 호스트 Mac 으로 빼냈다**. 컨테이너는 Jenkins controller / Dify / DB 같은 "서버 지향" 서비스만 담당.

### 컨테이너에 포함

- Jenkins controller — JDK 21, 플러그인 40+개 seed
- Dify 1.13.3 — api / worker / worker_beat / web / plugin_daemon
- PostgreSQL 15 (Dify 5개 DB 사전 생성), Redis 7, Qdrant 1.8.3
- Dify 플러그인 `langgenius/ollama`
- nginx, supervisord

### 호스트 Mac 에 준비해야 하는 것

- **Ollama** 데몬 + 모델 (`brew install ollama; ollama pull gemma4:e4b`)
- **JDK 21** (`brew install --cask temurin@21`)
- **Python 3.11+** (`brew install python@3.12`)
- **Playwright Chromium** — `mac-agent-setup.sh` 가 자동 설치

### 트레이드오프

| 항목 | 값 |
| --- | --- |
| 이미지 크기 (비압축) | ~10GB |
| 배포 파일 tar.gz | **2-3GB** |
| 빌드 시간 | 10-30분 (초기), 캐시 재사용 3-5분 |
| 첫 기동 시간 | **3-5분** |
| 이후 기동 | 30-60초 |
| 호스트 RAM | 16GB+ (컨테이너 ~6GB + Ollama + 모델 5-10GB) |
| LLM 성능 | **Metal 50-80 tok/s** |
| 브라우저 | **Mac 네이티브 Chromium 창 (headed)** |
| 외부 포트 | 18080 (Jenkins), 18081 (Dify), 50001 (JNLP) |

---

## 빠른 시작 (6단계, 15-20분)

**전제**: macOS 13+, Apple Silicon, Docker Desktop 4.30+, RAM 16GB+, 인터넷 가능.

### 1단계 — 호스트 Ollama 준비

```bash
brew install ollama
brew services start ollama
ollama pull gemma4:e4b           # ~4GB, 1-3분
curl -fsS http://127.0.0.1:11434/api/tags   # 응답 확인
```

### 2단계 — 호스트 JDK 21 + Python 설치

```bash
brew install --cask temurin@21   # 또는: brew install openjdk@21
brew install python@3.12         # 3.11+ 필요

java -version                    # 21 이상 확인
python3 --version                # 3.11+
```

### 3단계 — 이미지 빌드 (3-5분, 캐시 재사용 시)

```bash
git clone <이 저장소> && cd dscore-ttc/e2e-pipeline
./offline/build-allinone.sh 2>&1 | tee /tmp/build.log
```

`TARGET_PLATFORM` 은 **호스트 아키텍처 자동 감지** — Apple Silicon 에선 `linux/arm64`. 로그 앞부분에서 확인:

```text
[build-allinone] 빌드 대상: dscore-qa:allinone (platform=linux/arm64)
```

종료 시 `e2e-pipeline/dscore-qa-allinone-<timestamp>.tar.gz` (2-3GB) 생성.

### 4단계 — 컨테이너 기동

```bash
docker load -i dscore-qa-allinone-*.tar.gz

docker run -d --name dscore-qa \
  -p 18080:18080 -p 18081:18081 -p 50001:50001 \
  -v dscore-data:/data \
  --add-host host.docker.internal:host-gateway \
  -e OLLAMA_BASE_URL=http://host.docker.internal:11434 \
  -e OLLAMA_MODEL=gemma4:e4b \
  --restart unless-stopped \
  dscore-qa:allinone

# 기동 진행 관찰 (3-5분)
docker logs -f dscore-qa
```

`[entrypoint-allinone] 준비 완료. supervisord wait...` 가 찍히면 컨테이너 쪽 준비 완료. 바로 직전에 **NODE_SECRET** 값이 로그에 찍혀 있다 — 다음 단계에서 사용.

### 5단계 — 호스트 Jenkins agent 연결

컨테이너 로그에서 `NODE_SECRET: abcdef...` 줄을 찾아 그 값으로 setup 스크립트 실행:

```bash
# 로그에서 자동 추출
export NODE_SECRET=$(docker logs dscore-qa 2>&1 | grep -oE 'NODE_SECRET: [a-f0-9]{64}' | tail -1 | awk '{print $2}')
echo "NODE_SECRET: ${NODE_SECRET:0:16}..."

# 호스트 Mac agent 시작 (foreground — 이 터미널은 agent 전용으로 열어둠)
./offline/mac-agent-setup.sh
```

스크립트 동작:

1. JDK 21 확인 (없으면 에러)
2. `~/.dscore-qa-agent/venv` 생성 + Python deps (requests/playwright/pillow)
3. `playwright install chromium` → `~/Library/Caches/ms-playwright/`
4. `agent.jar` 다운로드
5. `java -jar agent.jar ...` 로 foreground 실행 → `INFO: Connected` 찍힘

터미널 하나를 agent 로 점유한다. 종료는 Ctrl+C (재연결 시 동일 스크립트 재실행).

### 6단계 — 첫 Pipeline 실행 (headed 브라우저 검증)

1. 새 터미널 또는 브라우저로 <http://localhost:18080> → `admin / password`
2. Dashboard → `DSCORE-ZeroTouch-QA-Docker` → **Build with Parameters**
3. 파라미터 (기본값 유지):

   | 파라미터 | 값 |
   | --- | --- |
   | `RUN_MODE` | `chat` |
   | `TARGET_URL` | `https://www.naver.com` |
   | `SRS_TEXT` | `검색창에 DSCORE 입력 후 엔터` |
   | `HEADLESS` | **체크 해제 (기본값) — macOS 화면에 Chromium 창 뜸** |

4. **Build** → **Mac 화면에 Chromium 창이 떠서 테스트 진행** 하는 것 확인

접속:

| URL | 기본 계정 |
| --- | --- |
| <http://localhost:18080> | Jenkins — `admin / password` |
| <http://localhost:18081> | Dify — `admin@example.com / Admin1234!` |

---

## 1. 빌드 (상세)

### 1.1 사전 준비

| 항목 | 최소 | 확인 |
| --- | --- | --- |
| macOS | 13 (Ventura) | `sw_vers` |
| 칩 | Apple Silicon (M1-M4) | `uname -m` → `arm64` |
| Docker Desktop | 4.30 | `docker --version` |
| Docker buildx | 활성 | `docker buildx version` |
| JDK | 11+ (플러그인 다운로드용, 빌드 머신) | `java -version` |
| 디스크 여유 | 20GB+ | `df -h .` |
| 인터넷 | 외부망 가능 | `curl -I https://updates.jenkins.io` |

#### 외부 네트워크 도메인 (방화벽 화이트리스트)

| 도메인 | 용도 |
| --- | --- |
| `updates.jenkins.io`, `get.jenkins.io`, `mirrors.jenkins.io` | Jenkins 플러그인 |
| `marketplace.dify.ai` | Dify 플러그인 |
| `github.com`, `objects.githubusercontent.com` | jenkins-plugin-manager.jar, qdrant |
| `registry-1.docker.io`, `auth.docker.io` | Docker Hub |
| `pypi.org`, `files.pythonhosted.org` | Python 패키지 |
| `playwright.azureedge.net` | Chromium (빌드 머신 + 호스트 agent 양쪽) |
| `apt.postgresql.org`, `deb.debian.org` | OS 패키지 |

### 1.2 빌드 실행

```bash
./offline/build-allinone.sh 2>&1 | tee /tmp/build.log
```

#### 환경변수 옵션

| 변수 | 기본값 | 언제 바꾸나 |
| --- | --- | --- |
| `TARGET_PLATFORM` | (호스트 자동 감지: arm64 → `linux/arm64`, Intel → `linux/amd64`) | Linux x86 서버로 배포할 때만 `linux/amd64` override |
| `OLLAMA_MODEL` | `gemma4:e4b` | Dify provider 에 등록할 모델 id (호스트 `ollama list` 와 일치해야 함) |
| `IMAGE_TAG` | `dscore-qa:allinone` | 여러 버전 구분 시 |
| `OUTPUT_TAR` | `dscore-qa-allinone-<timestamp>.tar.gz` | 출력 파일명 고정 시 |

#### 빌드 후 검증

```bash
# 플랫폼 일치
docker image inspect dscore-qa:allinone --format '{{.Architecture}}'
uname -m        # 일치해야 정상

# 이미지 크기 (9-10GB 정상, 2-3GB 면 qemu silent-fail 의심)
docker images dscore-qa:allinone --format '{{.Size}}'
```

### 1.3 빌드 단계 내부

[1/4] Jenkins 플러그인 hpi 재귀 다운로드 (`offline/jenkins-plugins/`, 40-50개, ~150MB)

[2/4] Dify 플러그인 `.difypkg` (`offline/dify-plugins/langgenius-ollama-*.difypkg`, ~5MB)

[3/4] Docker buildx 2-stage 빌드:

| Stage | base | 추출 |
| --- | --- | --- |
| 1a. `dify-api-src` | `langgenius/dify-api:1.13.3` | `/opt/dify-api` |
| 1b. `dify-web-src` | `langgenius/dify-web:1.13.3` | `/opt/dify-web` |
| 1c. `dify-plugin-src` | `langgenius/dify-plugin-daemon:0.5.3-local` | `/opt/dify-plugin-daemon` |
| 2. final | `jenkins/jenkins:lts-jdk21` | OS 패키지 + 모든 산출물 |

Stage 2 에는 Ollama 바이너리·모델·agent 가 **포함되지 않는다** (원본 `feat/allinone-offline-image` 대비 크게 슬림).

[4/4] `docker save | gzip -1` → tar.gz (2-3GB)

---

## 2. 기동 + agent 연결

### 2.1 호스트 사전 조건 체크

```bash
# A. Ollama
curl -fsS http://127.0.0.1:11434/api/tags | python3 -m json.tool | head -5
ollama list | awk '/gemma4:e4b/ || NR==1'

# B. JDK 21
java -version 2>&1 | head -1

# C. Python 3.11+
python3 --version

# D. Docker Desktop 메모리 (Settings → Resources)
docker info 2>/dev/null | grep -i 'Total Memory'

# E. 포트 비어있음
lsof -i :18080 -i :18081 -i :50001 || echo "[OK] 포트 비어있음"
```

### 2.2 컨테이너 기동

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

#### 옵션 해설

| 옵션 | 역할 |
| --- | --- |
| `-p 18080/18081/50001` | Jenkins UI, Dify UI, JNLP (호스트 agent 접속용) |
| `-v dscore-data:/data` | 모든 상태 영속. 재기동 시 동일 볼륨 필수 |
| `--add-host host.docker.internal:host-gateway` | 컨테이너 → 호스트 Ollama 경로 해석 |
| `-e OLLAMA_BASE_URL=...` | Dify provider 의 Ollama URL |
| `-e OLLAMA_MODEL=...` | Dify 에 등록할 모델 id (호스트 모델명과 일치) |
| `--restart unless-stopped` | Docker 재시작 자동 기동 |

#### 비밀번호 사전 주입 (옵션)

```bash
docker run -d --name dscore-qa ... \
  -e JENKINS_ADMIN_USER=admin \
  -e JENKINS_ADMIN_PW='<strong-pw>' \
  -e DIFY_EMAIL=admin@corp.example \
  -e DIFY_PASSWORD='<strong-pw>' \
  ... dscore-qa:allinone
```

`/data/.initialized` 플래그가 이미 있으면 env 무시 (첫 기동에서만 유효).

### 2.3 기동 타임라인

| 경과 | 마커 |
| --- | --- |
| 0:00 | `[entrypoint-allinone] 최초 seed: /opt/seed → /data` |
| ~0:05 | `[entrypoint-allinone] supervisord 기동...` (**10개** 프로세스) |
| 0:30-1:30 | PG / Redis / Qdrant / dify-plugin-daemon / dify-api ready |
| 1:30-2:30 | `[▶] === 1. 서비스 헬스체크 ===` → provision-apps.sh 시작 |
| 2:30-4:00 | Dify 관리자/플러그인/모델/Chatflow/API Key + Jenkins 플러그인/Credentials/Job/Node |
| 4:00-4:30 | `[▶] === 프로비저닝 완료 ===` |
| 4:30 | **`NODE_SECRET: <64자>`** 로그에 출력 → 호스트 agent 연결에 사용 |
| 4:30 | `[entrypoint-allinone] 준비 완료. supervisord wait...` |

### 2.4 호스트 agent 연결

```bash
export NODE_SECRET=$(docker logs dscore-qa 2>&1 | grep -oE 'NODE_SECRET: [a-f0-9]{64}' | tail -1 | awk '{print $2}')
./offline/mac-agent-setup.sh
```

스크립트 수행 (idempotent, 5-10분 최초):

1. **JDK 21 확인** — 없으면 중단 + 설치 안내
2. **venv** `~/.dscore-qa-agent/venv` 생성 + `pip install requests playwright pillow`
3. **Playwright Chromium** `~/Library/Caches/ms-playwright/` 에 설치 (macOS arm64 네이티브)
4. **agent.jar** 다운로드 → `~/.dscore-qa-agent/agent.jar`
5. **run-agent.sh** 생성 — `SCRIPTS_HOME=<e2e-pipeline 위치>`, venv PATH 주입, agent 기동
6. **foreground agent 실행** — `INFO: Connected` 찍히면 연결됨. 이 터미널은 agent 전용

> **재연결**: 같은 스크립트를 같은 NODE_SECRET 으로 재실행. JDK/venv/Chromium/agent.jar 는 재사용. NODE_SECRET 이 바뀌었으면 (컨테이너 재생성) 컨테이너 로그에서 새 값 추출.

### 2.5 프로비저닝 체크리스트

| # | 항목 | 확인 |
| - | --- | --- |
| 1 | Dify 관리자 생성 | `curl -fsS http://localhost:18081/console/api/setup \| jq .setup_status` → `"finished"` |
| 2 | Dify Ollama 플러그인 | `docker exec dscore-qa ls /data/dify/plugins/packages` 에 `langgenius-ollama-*.difypkg` |
| 3 | Ollama 모델 등록 (호스트 URL) | DB 조회로 `base_url: host.docker.internal:11434` |
| 4 | Chatflow import | Dify UI 에 `DSCORE-ZeroTouch-QA` 앱 |
| 5 | Dify API Key | `docker logs dscore-qa \| grep "API Key 발급 완료"` |
| 6 | Jenkins 플러그인 4개 | `curl -fsS -u admin:password 'http://localhost:18080/pluginManager/api/json?depth=1' \| jq -r '.plugins[].shortName' \| grep -cE 'workflow-aggregator\|plain-credentials\|file-parameters\|htmlpublisher'` → `4` |
| 7 | Credentials | `curl -fsS -u admin:password 'http://localhost:18080/credentials/store/system/domain/_/api/json?depth=1' \| jq -r '.credentials[].id'` 에 `dify-qa-api-token` |
| 8 | Pipeline Job | Dashboard 에 `DSCORE-ZeroTouch-QA-Docker` |
| 9 | **호스트 agent online** | `curl -fsS -u admin:password http://localhost:18080/computer/mac-ui-tester/api/json \| jq .offline` → `false` (step 2.4 완료 후) |

### 2.6 시나리오 검증

Dashboard → `DSCORE-ZeroTouch-QA-Docker` → **Build with Parameters**:

| 파라미터 | 값 | 비고 |
| --- | --- | --- |
| `RUN_MODE` | `chat` | |
| `TARGET_URL` | `https://www.naver.com` | Google 은 봇 차단 빈번 |
| `SRS_TEXT` | `검색창에 DSCORE 입력 후 엔터` | |
| `HEADLESS` | **체크 해제 (기본)** | **Mac 화면에 Chromium 창 뜸** |

**Build** → macOS 에서 Chromium 창이 실제로 뜨고 네이버 탐색 → 검색 수행 → 결과 스크린샷. 30-90초 내 `Finished: SUCCESS`.

---

## 3. 운영

### 재시작

**컨테이너만**:

```bash
docker restart dscore-qa       # 30-60초
```

호스트 agent 가 미리 붙어있다면 Jenkins master 재기동 후 자동 재연결 (NODE_SECRET 은 Jenkins 가 유지).

**호스트 agent 끊겼을 때**:

```bash
# 같은 NODE_SECRET 으로 재실행
export NODE_SECRET=$(docker logs dscore-qa 2>&1 | grep -oE 'NODE_SECRET: [a-f0-9]{64}' | tail -1 | awk '{print $2}')
./offline/mac-agent-setup.sh
```

### 중지 / 제거

```bash
docker stop dscore-qa             # 일시 정지
docker rm -f dscore-qa            # 컨테이너 제거 (볼륨 유지)
docker volume rm dscore-data      # 완전 초기화
```

호스트 쪽: agent 터미널 `Ctrl+C` / `rm -rf ~/.dscore-qa-agent` 로 완전 정리.

### 로그

```bash
docker exec dscore-qa tail -f /data/logs/dify-api.log
docker exec dscore-qa tail -f /data/logs/jenkins.log
docker exec dscore-qa supervisorctl status

# 호스트 agent 로그는 agent 실행 터미널에 실시간 출력
```

### 백업 / 복원

컨테이너 데이터 (`/data` 볼륨) 만 백업하면 충분:

```bash
docker run --rm -v dscore-data:/data -v $PWD:/backup busybox \
  tar czf /backup/dscore-data-$(date +%Y%m%d).tar.gz /data
```

복원:

```bash
docker stop dscore-qa
docker run --rm -v dscore-data:/data -v $PWD:/backup busybox \
  tar xzf /backup/dscore-data-YYYYMMDD.tar.gz -C /
docker start dscore-qa
```

호스트 쪽 `~/.dscore-qa-agent` 는 backup 불필요 (setup 스크립트 재실행으로 복구).

### 업그레이드

```bash
docker stop dscore-qa
docker rm dscore-qa
docker load -i dscore-qa-allinone-new.tar.gz
docker run -d --name dscore-qa ... dscore-qa:allinone    # 같은 옵션

# 새 NODE_SECRET 으로 agent 재연결
export NODE_SECRET=$(docker logs dscore-qa 2>&1 | grep -oE 'NODE_SECRET: [a-f0-9]{64}' | tail -1 | awk '{print $2}')
./offline/mac-agent-setup.sh
```

### 관리자 비밀번호 변경

| 상황 | 방법 |
| --- | --- |
| 첫 배포 직전 | docker run 에 env 주입 (`JENKINS_ADMIN_PW`, `DIFY_PASSWORD`) |
| 운영 중 Jenkins | People → `admin` → Configure → Password |
| 운영 중 Dify | 우상단 계정 → Settings → Account → Password |

---

## 4. Ollama 모델 관리 (호스트 기반)

컨테이너엔 Ollama 가 없으니 **모든 모델 작업은 macOS 호스트의 `ollama` 명령** 으로.

### 4.1 현재 상태

```bash
ollama list                  # 호스트 모델 목록
ollama show gemma4:e4b       # 세부 정보
du -sh ~/.ollama/models      # 디스크

# Dify 에 등록된 provider (컨테이너 DB 조회)
docker exec dscore-qa bash -c "
PGPASSWORD=difyai123456 psql -h 127.0.0.1 -U postgres -d dify -c \"
SELECT pm.model_name, pmc.credential_name, substring(pmc.encrypted_config,1,100) AS cfg
  FROM provider_models pm JOIN provider_model_credentials pmc ON pm.credential_id = pmc.id
 WHERE pm.provider_name LIKE '%ollama%';\""
```

### 4.2 새 모델로 교체

```bash
# 1) 호스트에서 pull
ollama pull llama3.1:8b

# 2) 호스트에서 시험 (Metal 가속 확인)
ollama run llama3.1:8b "간단히 소개해줘"

# 3) 컨테이너 재생성
docker rm -f dscore-qa
docker run -d --name dscore-qa ... -e OLLAMA_MODEL=llama3.1:8b ... dscore-qa:allinone

# 4) 강제 재프로비저닝
docker exec dscore-qa rm -f /data/.app_provisioned
docker restart dscore-qa

# 5) 호스트 agent 재연결 (새 NODE_SECRET)
export NODE_SECRET=$(docker logs dscore-qa 2>&1 | grep -oE 'NODE_SECRET: [a-f0-9]{64}' | tail -1 | awk '{print $2}')
./offline/mac-agent-setup.sh
```

#### 모델 선택 (호스트 기준)

| 모델 | 크기 | 호스트 RAM | 용도 |
| --- | --- | --- | --- |
| `gemma4:e4b` | ~4GB | 6-8GB | 기본 — 빠름 |
| `llama3.1:8b` | ~4.7GB | 8-10GB | 품질↑ |
| `qwen2.5:7b` | ~4.4GB | 8-10GB | 다국어 |
| `gemma2:2b` | ~1.5GB | 4GB | 저사양 |

> **Chatflow DSL 의 모델 id 하드코딩**: [dify-chatflow.yaml](../dify-chatflow.yaml) 의 LLM 노드에 `gemma4:e4b` 박혀있음. `OLLAMA_MODEL` env 를 바꿔도 Chatflow 는 옛 모델을 쓰므로 [§4.4](#44-chatflow-에서-사용-모델-변경) 로 UI 에서 교체.

### 4.3 Dify 에 추가 모델 등록 (수동)

provision-apps.sh 는 `OLLAMA_MODEL` 하나만 등록. 여러 개 쓰려면 UI:

1. Dify → 우상단 계정 → **Settings** → **Model Provider**
2. **Ollama** 카드 → **+ Add Model**
3. 입력:
   - Model Name: `llama3.1:8b` (호스트 `ollama list` NAME 과 일치)
   - **Base URL: `http://host.docker.internal:11434`** (반드시 호스트 URL)
   - Completion mode: `Chat`
   - Context size: `8192`, Max tokens: `4096`

### 4.4 Chatflow 에서 사용 모델 변경

Jenkins Pipeline 이 호출하는 Dify App 의 DSL 안에 모델 id 가 박혀있음. provider 등록만으로는 Chatflow 가 안 바뀐다.

1. Dify → Apps → `DSCORE-ZeroTouch-QA`
2. 캔버스 LLM 노드 (Planner/Healer) 클릭
3. 오른쪽 패널 **Model** 드롭다운 → 새 모델 선택
4. 상단 **Publish** → "Publish as API"
5. Pipeline 재실행 시 즉시 반영

### 4.5 모델 삭제

```bash
ollama rm gemma4:e4b
du -sh ~/.ollama/models
```

Chatflow 참조 중인 모델 삭제 시 Pipeline 이 `model not found` 로 실패 → 삭제 전 [§4.4](#44-chatflow-에서-사용-모델-변경) 로 교체.

### 4.6 Ollama 런타임 튜닝 (호스트)

```bash
# Homebrew 서비스
brew services stop ollama
launchctl setenv OLLAMA_KEEP_ALIVE -1          # 모델 영구 상주
launchctl setenv OLLAMA_MAX_LOADED_MODELS 2    # 동시 2개
brew services start ollama
```

| env | 기본 | 의미 |
| --- | --- | --- |
| `OLLAMA_HOST` | `127.0.0.1:11434` | 바인드 |
| `OLLAMA_KEEP_ALIVE` | `5m` | `-1` = 영구 |
| `OLLAMA_MAX_LOADED_MODELS` | `1` | 동시 로드 수 |
| `OLLAMA_NUM_PARALLEL` | `1` | 동시 요청 수 |
| `OLLAMA_FLASH_ATTENTION` | 미설정 | `1` = 활성 |

---

## 5. 트러블슈팅

### 컨테이너가 기동 직후 죽는다

```bash
docker logs dscore-qa | tail -50
```

- 메모리 부족 → Docker Desktop Memory 할당 증가
- PG init 실패 → `/data/logs/postgresql.err.log`. 권한 문제면 `docker volume rm dscore-data`

### Pipeline 이 `'mac-ui-tester' is offline` 에서 대기

**원인 1**: 호스트 agent 미연결.

```bash
# 컨테이너 측에서 Node 상태 확인
curl -sS -u admin:password http://localhost:18080/computer/mac-ui-tester/api/json \
  | python3 -c "import json,sys; d=json.load(sys.stdin); print('offline:', d.get('offline'))"
# True 이면 호스트 agent 가 붙지 않은 것

# 호스트 agent 재연결
export NODE_SECRET=$(docker logs dscore-qa 2>&1 | grep -oE 'NODE_SECRET: [a-f0-9]{64}' | tail -1 | awk '{print $2}')
./offline/mac-agent-setup.sh
```

**원인 2**: NODE_SECRET 불일치 — Node 삭제·재생성된 경우. 위와 같이 최신 secret 추출 후 재연결.

**원인 3**: JNLP 포트(50001) 방화벽 차단. Mac `lsof -i :50001` / `nc 127.0.0.1 50001` 로 연결성 확인.

### agent 기동 시 `Cannot open display` / Chromium `Page crashed!`

이 증상이 **호스트에서** 나면:

- `Cannot open display` → `$DISPLAY` 없음. 하지만 macOS 호스트에서는 AppKit 으로 띄우므로 이 에러가 나올 일이 거의 없음. XQuartz 관련 잔재 env 때문일 수 있음 — `unset DISPLAY` 후 재시도
- `Page crashed!` → macOS 권한 팝업 ("Chromium이 인터넷에서 다운로드된 앱") 이 블록 중일 수 있음. 시스템 설정 → 보안 및 개인 정보 보호 → "확인 없이 열기"

이 증상이 **컨테이너에서** 나면 브랜치 잘못 사용 — 이 브랜치는 Playwright 가 호스트에서 실행되어야 함. 위 agent 연결 단계 (§2.4) 수행.

### Pipeline Stage 3 가 `Dify /v1/chat-messages` 400 또는 timeout

3대 원인:

1. **호스트 Ollama 미기동** — `ollama list` / `curl http://127.0.0.1:11434/api/tags` 확인 후 `brew services start ollama`
2. **모델 이름 불일치** — `OLLAMA_MODEL` env 값과 `ollama list` NAME 비교
3. **`--add-host` 누락** — `docker exec dscore-qa curl -fsS http://host.docker.internal:11434/api/tags` 실패 시 docker run 에 `--add-host host.docker.internal:host-gateway` 재추가

### Dify 가 옛 base_url 로 호출 (`127.0.0.1:11434 → Connection refused`)

provision-apps.sh 재실행으로 credential swap + Redis FLUSH + plugin-daemon 재기동 연쇄 자동 수행:

```bash
docker exec dscore-qa bash /opt/provision-apps.sh
```

완전 재프로비저닝:

```bash
docker exec dscore-qa rm -f /data/.app_provisioned
docker restart dscore-qa
```

### mac-agent-setup.sh 가 `JDK 21 미설치` 로 중단

```bash
brew install --cask temurin@21
# 또는
brew install openjdk@21
sudo ln -sfn /opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk \
  /Library/Java/JavaVirtualMachines/openjdk-21.jdk
java -version   # 21 확인
```

### mac-agent-setup.sh 가 `Playwright install 실패`

Apple Silicon 에서 Playwright Chromium 은 `~/Library/Caches/ms-playwright/chromium-*` 에 설치됨. 네트워크 문제라면:

```bash
source ~/.dscore-qa-agent/venv/bin/activate
python -m playwright install chromium --force
```

### 이미지가 너무 작다 (2-3GB)

Apple Silicon 에서 `TARGET_PLATFORM=linux/amd64` 로 qemu 크로스 빌드 시 발생. 2026-04-19 이후 build-allinone.sh 는 `uname -m` 자동 감지. 그래도 실수로 amd64 로 빌드했다면:

```bash
docker rm -f dscore-qa
docker rmi dscore-qa:allinone
./offline/build-allinone.sh    # 자동 감지 → arm64
docker image inspect dscore-qa:allinone --format '{{.Architecture}}'  # arm64 확인
```

### 시계 오류로 Dify 로그인 `Invalid encrypted data`

```bash
sudo sntp -sS time.apple.com
docker restart dscore-qa
```

---

## 6. 내부 구조 참고

### 파일 레이아웃

```
e2e-pipeline/offline/
├── Dockerfile.allinone          # 2-stage 멀티 빌드
├── build-allinone.sh            # 온라인 빌드
├── provision-apps.sh            # Dify/Jenkins 프로비저닝 (DB swap + Redis FLUSH + updateNode)
├── entrypoint-allinone.sh       # PID 1 — seed + supervisord + NODE_SECRET 출력
├── mac-agent-setup.sh           # 호스트 Mac agent 셋업 (JDK/venv/Chromium/agent.jar/연결)
├── supervisord.conf             # 10개 프로세스 정의 (agent 제거)
├── nginx-allinone.conf          # localhost upstream
├── pg-init-allinone.sh          # 빌드 타임 PG initdb
├── requirements-allinone.txt    # Python 의존성
├── README.md                    # 이 문서
├── jenkins-plugins/             # 빌드 시 채워짐 (*.hpi)
└── dify-plugins/                # 빌드 시 채워짐 (*.difypkg)
```

### 런타임 프로세스 토폴로지

```
supervisord (PID 1, tini 위, 10개 프로그램)
├─ postgresql (:5432)
├─ redis (:6379)
├─ qdrant (:6333)
├─ dify-plugin-daemon (:5002)
├─ dify-api (:5001)
├─ dify-worker / dify-worker-beat (Celery)
├─ dify-web (:3000)
├─ nginx (:18081 → dify-api, dify-web)
└─ jenkins (:18080, :50001 JNLP)

호스트 macOS
├─ ollama (:11434, Metal)
└─ jenkins agent (JDK 21, mac-ui-tester)
   └─ Playwright Chromium (headed, macOS 창)
```

### 볼륨 구조

```
/data/                         (컨테이너 볼륨)
├── .initialized               # seed 완료 플래그
├── .app_provisioned           # provision 완료 플래그
├── pg/                        # PostgreSQL data
├── redis/                     # Redis AOF
├── qdrant/                    # Qdrant storage
├── jenkins/                   # JENKINS_HOME (plugins, jobs, credentials, nodes)
├── dify/                      # storage + plugins/packages
└── logs/                      # 서비스별 로그

~/.dscore-qa-agent/            (호스트 Mac)
├── venv/                      # Python 3.11+ + playwright/requests/pillow
├── agent.jar                  # Jenkins controller 에서 받은 jar
├── run-agent.sh               # SCRIPTS_HOME env 주입 + java -jar agent.jar ...
└── workspace/DSCORE-ZeroTouch-QA-Docker/
    └── .qa_home/              # Pipeline 이 생성 (Jenkinsfile AGENT_HOME)
        └── artifacts/

~/Library/Caches/ms-playwright/chromium-*/   # Chromium arm64 바이너리
~/.ollama/models/                             # 호스트 Ollama 모델
```

### 프로비저닝 흐름

1. `entrypoint-allinone.sh` — seed 복사 (`.initialized` 가 없을 때만)
2. supervisord 백그라운드 기동 → 10개 서비스
3. 헬스 대기 — dify-api / dify-web / jenkins 모두 HTTP 200
4. `bash /opt/provision-apps.sh` (`.app_provisioned` 가 없을 때만):
   - Dify 관리자 / 로그인 / Ollama 플러그인 / 모델 등록 (base_url=host.docker.internal) / credential_id swap / Redis FLUSH / dify-api 재기동
   - Chatflow import / Publish / API Key 발급
   - Jenkins 플러그인 검증 / Credentials / Pipeline Job / Node 생성 (remoteFS=`~/.dscore-qa-agent`, `updateNode` 로 디스크 flush)
5. entrypoint 가 NODE_SECRET 을 로그에 출력 (호스트 agent 연결용)
6. foreground wait
7. **호스트 Mac 에서 `mac-agent-setup.sh` 실행** → Node online → Pipeline 실행 가능
