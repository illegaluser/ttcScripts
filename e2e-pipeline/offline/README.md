# Zero-Touch QA All-in-One (호스트 하이브리드 — Mac / Windows 공용)

호스트의 **Ollama 와 Jenkins agent** 를 전제로, Jenkins master + Dify + DB 서비스를 단일 컨테이너에 묶은 All-in-One 이미지. 원본 compose 설계의 핵심 UX (**호스트 화면에 Chromium 창이 뜨는 시각 검증 — headed 모드**) 를 유지한다.

본 디렉토리는 `feat/allinone-wsl-windows` 브랜치 — Mac / Windows 공통 빌드 스크립트 + 이미지 정의 + 프로비저닝 자동화 + 양 플랫폼용 호스트 agent 셋업 스크립트 + 가이드 일체.

---

## 개요

### 아키텍처 — 하이브리드

```text
┌─ 컨테이너 (dscore.ttc.playwright:latest) ──────────────────────────────┐
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
│  호스트 (Mac / Windows)                                       │
│  - Ollama 데몬   (Mac: Metal / Windows: CUDA — 30-80 tok/s)  │
│  - Jenkins agent (JDK 21, mac-ui-tester Node) → Playwright   │
│  - Chromium 창   (headed 모드 — OS 네이티브 창)              │
└───────────────────────────────────────────────────────────────┘
```

> Node 레이블이 `mac-ui-tester` 인 건 루트 `DSCORE-ZeroTouch-QA-Docker.jenkinsPipeline` 과의 호환성 때문이다. Windows 호스트에서도 동일 이름의 Node 가 사용된다.

### 왜 하이브리드인가

Docker Desktop 이 **Linux 컨테이너에 GPU / 디스플레이 전달을 모두 지원하지 않는다**:

| 플랫폼 | GPU | 디스플레이 (headed 브라우저) |
|--------|-----|------------------------------|
| Mac (Apple Silicon) | Metal passthrough **부재** → CPU 1-2 tok/s | X server 부재 |
| Windows 11 (WSL2 + Docker Desktop) | CUDA passthrough **가능** (NVIDIA Container Toolkit) | WSLg 는 WSL 리눅스 GUI 전용, Windows 데스크탑 창 불가 |

Windows 는 LLM 은 컨테이너 안에서도 GPU 가속 가능하지만, **headed Chromium 창을 Windows 데스크탑에 띄우는 것은 불가능**하다. 시각 검증 UX 를 포기하지 않으려면 Playwright 를 호스트에서 돌려야 한다. 그래서 Mac / Windows 공통으로 **성능에 민감한 두 가지 — LLM 추론과 브라우저 렌더링 — 을 호스트로 빼냈다**. 컨테이너는 Jenkins controller / Dify / DB 같은 "서버 지향" 서비스만 담당.

### 컨테이너에 포함

- Jenkins controller — JDK 21, 플러그인 40+개 seed
- Dify 1.13.3 — api / worker / worker_beat / web / plugin_daemon
- PostgreSQL 15 (Dify 5개 DB 사전 생성), Redis 7, Qdrant 1.8.3
- Dify 플러그인 `langgenius/ollama`
- nginx, supervisord

### 호스트에 준비해야 하는 것

**Mac (Apple Silicon)**:

- **Ollama** 데몬 + 모델 (`brew install ollama; ollama pull gemma4:e4b`)
- **JDK 21** (`brew install --cask temurin@21`)
- **Python 3.11+** (`brew install python@3.12`)
- **Playwright Chromium** — `mac-agent-setup.sh` 가 자동 설치

**Windows 11** — Mac 의 "호스트" 역할이 Windows 와 WSL2 Ubuntu 로 **분리된 하이브리드**:

| 역할 | 어디서 실행 | 준비 방법 |
| --- | --- | --- |
| **Ollama 데몬 + 모델** (GPU CUDA 직접) | **Windows 네이티브** | `winget install Ollama.Ollama` → 트레이 앱에서 자동 기동 → `ollama pull gemma4:e4b` (PowerShell) |
| **NVIDIA 드라이버** | Windows | 최신 드라이버 (WSL2 CUDA 자동 노출) |
| **Jenkins agent + Playwright Chromium** (headed) | **WSL2 Ubuntu** | 본 번들의 `wsl-agent-setup.sh` 가 JDK/Python/venv/Chromium 설치 + 기동 |
| **JDK 21** | WSL2 Ubuntu | `sudo apt install -y openjdk-21-jdk-headless` |
| **Python 3.11+** | WSL2 Ubuntu | `sudo apt install -y python3.12 python3.12-venv` |
| **Docker** (컨테이너 실행) | Docker Desktop (WSL2 백엔드) 또는 WSL native | 컨테이너는 `host.docker.internal:11434` 로 **Windows 호스트 Ollama** 에 도달 (Docker Desktop 자동 포워딩) |
| **Chromium 창** | WSLg → Windows 데스크탑 | agent 가 실행하면 자동으로 Windows 화면에 표시 |

즉 Ollama 는 Windows 네이티브 (CUDA 직접 접근), agent 는 WSL2 안 (bash + WSLg) — 이 두 호스트 자원이 컨테이너의 Jenkins/Dify 와 `host.docker.internal` / JNLP(:50001) 로 연결된다.

### 트레이드오프

| 항목 | 값 |
| --- | --- |
| 이미지 크기 (비압축) | ~10GB |
| 배포 파일 tar.gz | **2-3GB** |
| 빌드 시간 | 10-30분 (초기), 캐시 재사용 3-5분 |
| 첫 기동 시간 | **3-5분** |
| 이후 기동 | 30-60초 |
| 호스트 RAM | 16GB+ (컨테이너 ~6GB + Ollama + 모델 5-10GB) |
| LLM 성능 | **Mac Metal / Windows CUDA 30-80 tok/s** |
| 브라우저 | **호스트 네이티브 Chromium 창 (headed)** |
| 외부 포트 | 18080 (Jenkins), 18081 (Dify), 50001 (JNLP) |

---

## 빠른 시작 (6단계, 15-20분)

Mac 과 Windows 11 모두 **동일한 bash 기반 6단계** 흐름을 따른다. 다만 Windows 11 은 Ollama 만 Windows 네이티브에 설치 (GPU CUDA 직접), 그 외 (빌드 / docker / agent) 는 WSL2 Ubuntu 안에서 bash 로 실행. Chromium 창은 WSLg 를 통해 Windows 데스크탑에 뜬다.

**공통 전제**: Docker 26+ (buildx), RAM 16GB+, 인터넷 가능 (빌드 시점).

- Mac: macOS 13+, Apple Silicon (M1-M4), Docker Desktop 4.30+
- Windows 11: WSL2 활성 + Ubuntu 22.04+ 설치, NVIDIA 드라이버 (Windows host, 자동으로 WSL2 에 CUDA 노출), Docker Desktop (WSL2 백엔드) 또는 WSL native Docker

### 0단계 — Windows 11 만: WSL2 + Ubuntu 설치 (한 번만)

Mac 사용자는 이 단계 생략.

관리자 PowerShell 에서:

```powershell
# WSL2 활성화 + Ubuntu 22.04 배포판 설치 (재부팅 1회 필요)
wsl --install -d Ubuntu-22.04
# 이후 'Ubuntu' 앱을 시작 메뉴에서 실행 → 초기 user/password 설정 → bash 프롬프트
```

이후 **모든 명령은 WSL Ubuntu 셸 안에서** 수행한다. Docker Desktop 을 쓰는 경우 Settings → Resources → WSL Integration 에서 Ubuntu 배포판을 체크 (이미지가 WSL 안에서 보이게).

**오프라인 환경**: `wsl --install` 이 인터넷을 요구하므로 폐쇄망은:

1. 온라인 머신에서 Microsoft 공식 WSL 설치 패키지 (`wsl.2.x.x.x_x64.msi`) 와 Ubuntu 배포 tarball/appx 를 미리 다운로드
2. USB 로 폐쇄망 Windows 에 전송
3. 관리자 PowerShell 에서 `dism` 으로 기능 활성화 → WSL 커널 MSI 설치 → `wsl --import Ubuntu ...` 로 tarball 로드
4. 세부 절차는 [§5 트러블슈팅 — Windows: WSL2 오프라인 설치](#windows-wsl2-오프라인-설치) 참조

### 1단계 — 호스트 Ollama 준비 (선택: 5단계에서 자동 설치 가능)

**Mac**:

```bash
brew install ollama
brew services start ollama
ollama pull gemma4:e4b           # ~4GB, 1-3분
curl -fsS http://127.0.0.1:11434/api/tags   # 응답 확인
```

**Windows 11 — Ollama 는 Windows 네이티브에 설치** (WSL 에 설치하지 않음):

PowerShell 에서:

```powershell
winget install Ollama.Ollama
# 설치 후 Ollama 트레이 앱이 자동 기동 (백그라운드). 수동 기동:
#   & "$env:LOCALAPPDATA\Programs\Ollama\ollama app.exe"

ollama pull gemma4:e4b           # ~4GB, 1-3분
ollama list                      # 모델 확인
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:11434/api/tags | Select-Object -ExpandProperty Content
```

NVIDIA GPU 가 있으면 Ollama Windows 가 자동으로 CUDA 를 쓴다. 첫 프롬프트 로그는 `%LOCALAPPDATA%\Ollama\server.log` 에서 확인:

```powershell
ollama run gemma4:e4b "hi"       # 30-80 tok/s 면 CUDA OK. 1-3 tok/s 는 CPU fallback
Get-Content "$env:LOCALAPPDATA\Ollama\server.log" -Tail 30 | Select-String 'GPU|CUDA'
```

> **왜 Windows 네이티브인가**: (1) Windows Ollama 는 Windows GPU 드라이버의 CUDA 를 직접 쓴다, (2) 컨테이너는 `host.docker.internal` 로 Windows Ollama 에 자동 도달 (Docker Desktop 포워딩), (3) WSL 안에 중복 설치하면 같은 모델을 두 번 디스크에 올리게 된다.
>
> **WSL agent 는 Ollama 를 직접 호출하지 않으므로** Ollama 가 WSL 에서 보이지 않아도 무방하다. WSL 에서도 체크하고 싶다면 Windows 사용자 환경변수에 `OLLAMA_HOST=0.0.0.0` 을 설정 후 Ollama 트레이 앱 재기동.

### 2단계 — 호스트 JDK 21 + Python 설치 (선택: 5단계에서 자동 설치 가능)

**Mac**:

```bash
brew install openjdk@21          # 권장 (sudo 불요) — 또는: brew install --cask temurin@21
brew install python@3.12         # 3.11+ 필요

java -version                    # 21 이상 확인
python3 --version                # 3.11+
```

**Windows 11 / WSL2 Ubuntu**:

```bash
sudo apt update
sudo apt install -y openjdk-21-jdk-headless python3.12 python3.12-venv
# Ubuntu 22.04 기본 apt 에 python3.12 가 없으면 python3.11 로 대체:
#   sudo apt install -y python3.11 python3.11-venv
# 또는 deadsnakes PPA: sudo add-apt-repository ppa:deadsnakes/ppa && sudo apt update

java -version                    # 21 이상 확인
python3 --version                # 3.11+
```

> **2단계 생략**: Mac 은 brew 만 있으면 **5단계 스크립트에 `AUTO_INSTALL_DEPS=true`** 를 넘겨 ollama + 모델 pull + JDK 21 + Python 3.12 를 한 번에 자동 설치. WSL2 Ubuntu 는 `sudo apt install openjdk-21 python3.12` 부분만 자동 수행 (Windows 쪽 Ollama 설치는 1단계 PowerShell 로 별도).

### 3단계 — 이미지 빌드 (3-5분, 캐시 재사용 시)

**Mac / WSL2 Ubuntu 공통 (bash)**:

```bash
git clone <이 저장소> && cd dscore-ttc/e2e-pipeline
./offline/build-allinone.sh 2>&1 | tee /tmp/build.log
```

`TARGET_PLATFORM` 은 **호스트 아키텍처 자동 감지** — Apple Silicon 은 `linux/arm64`, WSL2 Ubuntu (x86_64) 는 `linux/amd64`. 로그 앞부분에서 확인:

```text
[build-allinone] 빌드 대상: dscore.ttc.playwright:latest (platform=linux/amd64)
```

종료 시 `e2e-pipeline/dscore.ttc.playwright-<timestamp>.tar.gz` (2-3GB) 생성.

#### 3단계 단축 — `--redeploy`: 빌드 + 재기동 + agent 한 방에

개발 루프에서 "소스 수정 → 빌드 → 기존 컨테이너 정리 → 새 컨테이너 run → agent 재연결" 을 한 명령으로:

```bash
# 기존 dscore-data 볼륨 유지 (provision 재사용 — 60초 내 준비 완료)
./offline/build-allinone.sh --redeploy

# 제로베이스로 완전 초기화 (볼륨 삭제 → provision 재수행, 2-3분 소요)
./offline/build-allinone.sh --redeploy --fresh

# 컨테이너만 재기동, agent 는 수동으로 띄우고 싶을 때
./offline/build-allinone.sh --redeploy --no-agent
```

- `--redeploy` 는 기존 `dscore.ttc.playwright` 컨테이너를 `docker rm -f` 후 새 이미지로 run
- 기존 `agent.jar` 프로세스 정리는 **agent-setup 스크립트가 스크립트 맨 앞 (step 0-A) 에서 자동 수행** — `NODE_SECRET` 도 `docker logs` 에서 자동 추출
- `--fresh` 는 `docker volume rm dscore-data` 까지 수행해 Dify DB + Jenkins config 를 완전 리셋
- 폐쇄망 타겟은 빌드 머신과 런타임 머신이 분리되므로 `--redeploy` 를 쓰지 않는다 — 빌드 머신에서 tar.gz 만 만들고 폐쇄망 타겟에서 `docker load + docker run + agent-setup` 을 각각 실행

전체 옵션: `./offline/build-allinone.sh --help`

### 4단계 — 컨테이너 기동 (Mac / WSL2 공통)

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

`[entrypoint-allinone] 준비 완료. supervisord wait...` 가 찍히면 컨테이너 쪽 준비 완료. 바로 직전에 **NODE_SECRET** 값이 로그에 찍혀 있다 — 다음 단계에서 사용.

> WSL2 에서 `host.docker.internal` 은 Docker Desktop WSL2 백엔드가 자동 해석하지만, native Docker Engine (`apt install docker-ce`) 을 쓸 때는 `--add-host host.docker.internal:host-gateway` 가 반드시 필요하다 (위 예시에 포함되어 있음 — 안전).

### 5단계 — 호스트 Jenkins agent 연결

`NODE_SECRET` 은 **스크립트가 자동으로 `docker logs` 에서 추출**하므로 명시 지정 없이 그냥 실행해도 된다. 기존에 돌고 있던 `agent.jar` / 중복 setup 프로세스도 스크립트 시작 시점 (step 0-A) 에서 자동 정리된다.

**Mac**:

```bash
# 자동 실행 (권장) — NODE_SECRET 자동 추출 + 기존 agent 정리 + 재연결
./offline/mac-agent-setup.sh

# 의존성까지 한 번에 자동 설치 (brew 필요)
AUTO_INSTALL_DEPS=true ./offline/mac-agent-setup.sh

# 명시 지정이 필요한 경우 (다른 컨테이너 이름 / 외부 Jenkins 등)
NODE_SECRET=<64자> CONTAINER_NAME=other-name JENKINS_URL=http://... ./offline/mac-agent-setup.sh
```

**Windows 11 / WSL2 Ubuntu**:

```bash
# 자동 실행
./offline/wsl-agent-setup.sh

# 의존성까지 한 번에 자동 설치 (sudo 필요)
AUTO_INSTALL_DEPS=true ./offline/wsl-agent-setup.sh
```

스크립트 동작 (Mac / WSL2 모두 7단계, idempotent — 설치된 것은 스킵):

1. Ollama 도달성 확인
    - Mac: brew 로 설치된 호스트 Ollama 체크 (`AUTO_INSTALL_DEPS=true` 시 `brew install ollama` + `services start` + `ollama pull $OLLAMA_MODEL`)
    - WSL2: **정보성 확인만** (Windows 네이티브 Ollama 는 `OLLAMA_HOST=0.0.0.0` 이 아니면 WSL 에서 안 보이지만 컨테이너는 host.docker.internal 로 접근. 실패해도 치명적 에러 아님)
2. JDK 21 확인 (`AUTO_INSTALL_DEPS=true` → Mac: `brew install openjdk@21` / WSL2: `sudo apt install openjdk-21-jdk-headless`)
3. Python 3.11+ 확인 (`AUTO_INSTALL_DEPS=true` → Mac: `brew install python@3.12` / WSL2: `sudo apt install python3.12 python3.12-venv`)
4. `~/.dscore.ttc.playwright-agent/venv` 생성 + `pip install requests playwright pillow` + `playwright install chromium`
5. Jenkins Node `mac-ui-tester` 의 remoteFS 를 호스트 절대경로로 갱신 + workspace venv 사전 링크
6. `agent.jar` 다운로드 (`$JENKINS_URL/jnlpJars/agent.jar`)
7. `java -jar agent.jar ...` 로 foreground 실행 → `INFO: Connected` 찍힘

터미널 하나를 agent 로 점유한다. 종료는 Ctrl+C (재연결 시 동일 스크립트 재실행).

> WSL2 는 WSLg 가 활성화돼 있어야 headed Chromium 창이 Windows 데스크탑에 뜬다. Windows 11 기본 탑재. 환경변수 `$DISPLAY` / `$WAYLAND_DISPLAY` 가 비어있다면 `wsl --update` (PowerShell 관리자) 로 커널 + WSLg 업데이트.

### 6단계 — 첫 Pipeline 실행 (headed 브라우저 검증)

1. 새 터미널 또는 브라우저로 <http://localhost:18080> → `admin / password`
2. Dashboard → `DSCORE-ZeroTouch-QA-Docker` → **Build with Parameters**
3. 파라미터 (기본값 유지):

   | 파라미터 | 값 |
   | --- | --- |
   | `RUN_MODE` | `chat` |
   | `TARGET_URL` | `https://www.naver.com` |
   | `SRS_TEXT` | `검색창에 DSCORE 입력 후 엔터` |
   | `HEADLESS` | **체크 해제 (기본값) — 호스트 화면에 Chromium 창 뜸** |

4. **Build** → **호스트 화면에 Chromium 창이 떠서 테스트 진행** — Mac 은 macOS 창, Windows 11 은 WSLg 경유로 Windows 데스크탑에 창

접속:

| URL | 기본 계정 |
| --- | --- |
| <http://localhost:18080> | Jenkins — `admin / password` |
| <http://localhost:18081> | Dify — `admin@example.com / Admin1234!` |

---

## 1. 빌드 (상세)

### 1.1 사전 준비

**Mac**:

| 항목 | 최소 | 확인 |
| --- | --- | --- |
| macOS | 13 (Ventura) | `sw_vers` |
| 칩 | Apple Silicon (M1-M4) | `uname -m` → `arm64` |
| Docker Desktop | 4.30 | `docker --version` |
| Docker buildx | 활성 | `docker buildx version` |
| JDK | 11+ (플러그인 다운로드용, 빌드 머신) | `java -version` |
| 디스크 여유 | 20GB+ | `df -h .` |
| 인터넷 | 외부망 가능 | `curl -I https://updates.jenkins.io` |

**Windows 11 (WSL2 Ubuntu — 모든 명령 WSL 안에서)**:

| 항목 | 최소 | 확인 (PowerShell / WSL Ubuntu) |
| --- | --- | --- |
| Windows | 11 | PowerShell: `winver` |
| WSL2 + Ubuntu | Ubuntu 22.04+ | PowerShell: `wsl -l -v` → VERSION 2 |
| WSLg | 활성 (Windows 11 기본) | WSL: `echo $DISPLAY $WAYLAND_DISPLAY` → 값 있음 |
| NVIDIA 드라이버 (Windows) | 최신 | WSL: `nvidia-smi` 에 GPU 정보 |
| Docker | Docker Desktop (WSL2 백엔드) 또는 WSL native | WSL: `docker --version && docker buildx version` |
| JDK | 11+ (빌드 시 플러그인 매니저용) | WSL: `java -version` |
| 디스크 여유 | 20GB+ (WSL2 ext4 기준) | WSL: `df -h .` |
| 인터넷 | 외부망 가능 | WSL: `curl -I https://updates.jenkins.io` |

> **빌드도 런타임도 모두 WSL2 Ubuntu 안에서 bash 로** 수행한다. PowerShell 은 WSL 설치 단계 (§빠른 시작 0단계) 에서만 사용. Docker Desktop 의 WSL Integration 이 켜져 있으면 WSL 안에서 `docker` 를 실행해도 Docker Desktop 데몬을 공유하며, `docker images` 가 PowerShell/WSL 어디서 보나 같은 결과.

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
| `IMAGE_TAG` | `dscore.ttc.playwright:latest` | 여러 버전 구분 시 |
| `OUTPUT_TAR` | `dscore.ttc.playwright-<timestamp>.tar.gz` | 출력 파일명 고정 시 |

#### 빌드 후 검증

```bash
# 플랫폼 일치
docker image inspect dscore.ttc.playwright:latest --format '{{.Architecture}}'
uname -m        # 일치해야 정상

# 이미지 크기 (9-10GB 정상, 2-3GB 면 qemu silent-fail 의심)
docker images dscore.ttc.playwright:latest --format '{{.Size}}'
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

**Mac**:

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

**Windows 11 / WSL2 Ubuntu (bash)**:

```bash
# A. Windows Ollama 도달성 — 기본은 WSL 에서 안 보임 (127.0.0.1 바인드). 컨테이너는 별개.
#    Windows PowerShell 에서 확인 권장: Invoke-WebRequest http://127.0.0.1:11434/api/tags
curl -fsS --max-time 2 http://host.docker.internal:11434/api/tags 2>&1 | head -c 100 || echo "(WSL 에서는 기본적으로 접근 불가 — 정상. 컨테이너는 별개 경로)"

# B. JDK 21
java -version 2>&1 | head -1

# C. Python 3.11+
python3 --version

# D. Docker + GPU 접근
docker info 2>/dev/null | grep -i 'Total Memory\|Runtimes'
nvidia-smi --query-gpu=name --format=csv,noheader   # GPU 가시성 (WSL2 ↔ Windows 드라이버)

# E. 포트 비어있음
ss -tlnp 2>/dev/null | grep -E ':(18080|18081|50001)' || echo "[OK] 포트 비어있음"

# F. WSLg 활성 (headed 브라우저 창 표시 가능 여부)
echo "DISPLAY=$DISPLAY WAYLAND_DISPLAY=$WAYLAND_DISPLAY"
```

### 2.2 컨테이너 기동

```bash
docker run -d --name dscore.ttc.playwright \
  -p 18080:18080 -p 18081:18081 -p 50001:50001 \
  -v dscore-data:/data \
  --add-host host.docker.internal:host-gateway \
  -e OLLAMA_BASE_URL=http://host.docker.internal:11434 \
  -e OLLAMA_MODEL=gemma4:e4b \
  --restart unless-stopped \
  dscore.ttc.playwright:latest
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
docker run -d --name dscore.ttc.playwright ... \
  -e JENKINS_ADMIN_USER=admin \
  -e JENKINS_ADMIN_PW='<strong-pw>' \
  -e DIFY_EMAIL=admin@corp.example \
  -e DIFY_PASSWORD='<strong-pw>' \
  ... dscore.ttc.playwright:latest
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

**Mac**:

```bash
export NODE_SECRET=$(docker logs dscore.ttc.playwright 2>&1 | grep -oE 'NODE_SECRET: [a-f0-9]{64}' | tail -1 | awk '{print $2}')
./offline/mac-agent-setup.sh
```

**Windows 11 / WSL2 Ubuntu**:

```bash
export NODE_SECRET=$(docker logs dscore.ttc.playwright 2>&1 | grep -oE 'NODE_SECRET: [a-f0-9]{64}' | tail -1 | awk '{print $2}')
./offline/wsl-agent-setup.sh
```

스크립트 수행 (Mac / WSL2 모두 7단계, idempotent, 5-10분 최초):

1. **호스트 Ollama 도달성 확인**
    - Mac: brew 로 설치된 Ollama 127.0.0.1:11434 체크 (`AUTO_INSTALL_DEPS=true` 시 `brew install ollama` + `services start` + `ollama pull $OLLAMA_MODEL`)
    - WSL2: **정보성 확인만 수행** (Windows 네이티브 Ollama 는 사용자가 PowerShell 로 설치 — Windows 바인드가 127.0.0.1 이면 WSL 에선 보이지 않지만 컨테이너는 host.docker.internal 로 자동 도달하므로 Pipeline 은 동작)
2. **JDK 21 확인** — 없으면 중단 + 설치 안내 (또는 `AUTO_INSTALL_DEPS=true` 로 Mac: `brew install openjdk@21` / WSL2: `sudo apt install openjdk-21-jdk-headless`)
3. **Python 3.11+ 확인** — 없으면 중단 (또는 `AUTO_INSTALL_DEPS=true` 로 Mac: `brew install python@3.12` / WSL2: `sudo apt install python3.12 python3.12-venv`)
4. **venv + Playwright Chromium** — `~/.dscore.ttc.playwright-agent/venv` 생성 + `pip install requests playwright pillow` + Chromium 설치
    - Mac:  `~/Library/Caches/ms-playwright/` (arm64 네이티브)
    - WSL2: `~/.cache/ms-playwright/` (Linux x64 — WSLg 로 Windows 데스크탑에 창 표시)
5. **Jenkins Node remoteFS 절대경로 갱신** + workspace venv symlink (Mac / WSL2 모두 `ln -sfn`)
6. **agent.jar** 다운로드 → `~/.dscore.ttc.playwright-agent/agent.jar`
7. **run-agent.sh 생성 + foreground 기동** — `INFO: Connected` 찍히면 연결됨. 이 터미널은 agent 전용

#### AUTO_INSTALL_DEPS 환경변수

| 값 | 동작 |
| --- | --- |
| `false` (기본) | 누락 시 설치 명령을 안내하고 exit. 사용자가 수동으로 설치 |
| `true` | 누락 의존성을 패키지 매니저로 자동 설치<br>Mac:  `brew install` (Homebrew 필수) — ollama/JDK/Python 모두<br>WSL2: `sudo apt install` (JDK/Python 만 — Ollama 는 Windows 쪽 설치가 별도) |

> **재연결**: 같은 스크립트를 같은 NODE_SECRET 으로 재실행. JDK/venv/Chromium/agent.jar 는 재사용. NODE_SECRET 이 바뀌었으면 (컨테이너 재생성) 컨테이너 로그에서 새 값 추출.

### 2.5 프로비저닝 체크리스트

| # | 항목 | 확인 |
| - | --- | --- |
| 1 | Dify 관리자 생성 | `curl -fsS http://localhost:18081/console/api/setup \| jq .setup_status` → `"finished"` |
| 2 | Dify Ollama 플러그인 | `docker exec dscore.ttc.playwright ls /data/dify/plugins/packages` 에 `langgenius-ollama-*.difypkg` |
| 3 | Ollama 모델 등록 (호스트 URL) | DB 조회로 `base_url: host.docker.internal:11434` |
| 4 | Chatflow import | Dify UI 에 `DSCORE-ZeroTouch-QA` 앱 |
| 5 | Dify API Key | `docker logs dscore.ttc.playwright \| grep "API Key 발급 완료"` |
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
| `HEADLESS` | **체크 해제 (기본)** | **호스트 (Mac / Windows) 화면에 Chromium 창 뜸** |

**Build** → 호스트 OS 에서 Chromium 창이 실제로 뜨고 네이버 탐색 → 검색 수행 → 결과 스크린샷. 30-90초 내 `Finished: SUCCESS`.

---

## 3. 운영

### 재시작

**컨테이너만**:

```bash
docker restart dscore.ttc.playwright       # 30-60초
```

호스트 agent 가 미리 붙어있다면 Jenkins master 재기동 후 자동 재연결 (NODE_SECRET 은 Jenkins 가 유지).

**호스트 agent 끊겼을 때**:

Mac:

```bash
export NODE_SECRET=$(docker logs dscore.ttc.playwright 2>&1 | grep -oE 'NODE_SECRET: [a-f0-9]{64}' | tail -1 | awk '{print $2}')
./offline/mac-agent-setup.sh
```

Windows 11 / WSL2 Ubuntu:

```bash
export NODE_SECRET=$(docker logs dscore.ttc.playwright 2>&1 | grep -oE 'NODE_SECRET: [a-f0-9]{64}' | tail -1 | awk '{print $2}')
./offline/wsl-agent-setup.sh
```

### 중지 / 제거

```bash
docker stop dscore.ttc.playwright             # 일시 정지
docker rm -f dscore.ttc.playwright            # 컨테이너 제거 (볼륨 유지)
docker volume rm dscore-data      # 완전 초기화
```

호스트 쪽 정리 (Mac / WSL2 공통):

```bash
# agent 터미널 Ctrl+C
rm -rf ~/.dscore.ttc.playwright-agent
```

### 로그

```bash
docker exec dscore.ttc.playwright tail -f /data/logs/dify-api.log
docker exec dscore.ttc.playwright tail -f /data/logs/jenkins.log
docker exec dscore.ttc.playwright supervisorctl status

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
docker stop dscore.ttc.playwright
docker run --rm -v dscore-data:/data -v $PWD:/backup busybox \
  tar xzf /backup/dscore-data-YYYYMMDD.tar.gz -C /
docker start dscore.ttc.playwright
```

호스트 쪽 `~/.dscore.ttc.playwright-agent` 는 backup 불필요 (setup 스크립트 재실행으로 복구).

### 업그레이드

```bash
docker stop dscore.ttc.playwright
docker rm dscore.ttc.playwright
docker load -i dscore.ttc.playwright-new.tar.gz
docker run -d --name dscore.ttc.playwright ... dscore.ttc.playwright:latest    # 같은 옵션

# 새 NODE_SECRET 으로 agent 재연결 (Mac / WSL2 공통 bash)
export NODE_SECRET=$(docker logs dscore.ttc.playwright 2>&1 | grep -oE 'NODE_SECRET: [a-f0-9]{64}' | tail -1 | awk '{print $2}')
# Mac:
./offline/mac-agent-setup.sh
# WSL2:
# ./offline/wsl-agent-setup.sh
```

### 관리자 비밀번호 변경

| 상황 | 방법 |
| --- | --- |
| 첫 배포 직전 | docker run 에 env 주입 (`JENKINS_ADMIN_PW`, `DIFY_PASSWORD`) |
| 운영 중 Jenkins | People → `admin` → Configure → Password |
| 운영 중 Dify | 우상단 계정 → Settings → Account → Password |

---

## 4. Ollama 모델 관리 (호스트 기반)

컨테이너엔 Ollama 가 없으니 **모든 모델 작업은 호스트의 `ollama` 명령** 으로 (Mac / Windows 동일).

### 4.1 현재 상태

```bash
ollama list                  # 호스트 모델 목록 (Mac/Windows 동일)
ollama show gemma4:e4b       # 세부 정보
```

모델 디스크 사용량 (Mac / WSL2 공통):

```bash
du -sh ~/.ollama/models
```

Dify 에 등록된 provider (컨테이너 DB 조회):

```bash
docker exec dscore.ttc.playwright bash -c "
PGPASSWORD=difyai123456 psql -h 127.0.0.1 -U postgres -d dify -c \"
SELECT pm.model_name, pmc.credential_name, substring(pmc.encrypted_config,1,100) AS cfg
  FROM provider_models pm JOIN provider_model_credentials pmc ON pm.credential_id = pmc.id
 WHERE pm.provider_name LIKE '%ollama%';\""
```

### 4.2 새 모델로 교체

```bash
# 1) 호스트에서 pull
ollama pull llama3.1:8b

# 2) 호스트에서 시험 (GPU 가속 확인 — Mac: Metal / WSL2: CUDA)
ollama run llama3.1:8b "간단히 소개해줘"

# 3) 컨테이너 재생성
docker rm -f dscore.ttc.playwright
docker run -d --name dscore.ttc.playwright ... -e OLLAMA_MODEL=llama3.1:8b ... dscore.ttc.playwright:latest

# 4) 강제 재프로비저닝
docker exec dscore.ttc.playwright rm -f /data/.app_provisioned
docker restart dscore.ttc.playwright

# 5) 호스트 agent 재연결 (새 NODE_SECRET)
export NODE_SECRET=$(docker logs dscore.ttc.playwright 2>&1 | grep -oE 'NODE_SECRET: [a-f0-9]{64}' | tail -1 | awk '{print $2}')
# Mac:
./offline/mac-agent-setup.sh
# WSL2:
# ./offline/wsl-agent-setup.sh
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

**Mac (Homebrew 서비스)**:

```bash
brew services stop ollama
launchctl setenv OLLAMA_KEEP_ALIVE -1          # 모델 영구 상주
launchctl setenv OLLAMA_MAX_LOADED_MODELS 2    # 동시 2개
brew services start ollama
```

**WSL2 Ubuntu (systemd 활성 시 systemctl override)**:

```bash
# systemd 있는 경우 (권장)
sudo mkdir -p /etc/systemd/system/ollama.service.d
sudo tee /etc/systemd/system/ollama.service.d/override.conf >/dev/null <<'EOF'
[Service]
Environment="OLLAMA_KEEP_ALIVE=-1"
Environment="OLLAMA_MAX_LOADED_MODELS=2"
Environment="OLLAMA_FLASH_ATTENTION=1"
EOF
sudo systemctl daemon-reload
sudo systemctl restart ollama

# systemd 없는 WSL (기본) — ~/.bashrc 에 export 추가 후 재기동
echo 'export OLLAMA_KEEP_ALIVE=-1'          >> ~/.bashrc
echo 'export OLLAMA_MAX_LOADED_MODELS=2'    >> ~/.bashrc
source ~/.bashrc
pkill -f 'ollama serve' || true
nohup ollama serve >/tmp/ollama.log 2>&1 &
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
docker logs dscore.ttc.playwright | tail -50
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
```

재연결:

Mac / WSL2 공통 (bash):

```bash
export NODE_SECRET=$(docker logs dscore.ttc.playwright 2>&1 | grep -oE 'NODE_SECRET: [a-f0-9]{64}' | tail -1 | awk '{print $2}')
./offline/mac-agent-setup.sh      # Mac
./offline/wsl-agent-setup.sh      # WSL2
```

**원인 2**: NODE_SECRET 불일치 — Node 삭제·재생성된 경우. 최신 secret 추출 후 재연결.

**원인 3**: JNLP 포트(50001) 방화벽 차단.

- Mac: `lsof -i :50001` / `nc 127.0.0.1 50001` 로 연결성 확인
- WSL2: `ss -tlnp | grep 50001` / `nc -v 127.0.0.1 50001`. Windows Defender 방화벽이 WSL 의 `java` outbound 를 차단 중일 때: 관리자 PowerShell 에서 `New-NetFirewallRule -DisplayName "WSL java" -Direction Outbound -Program 'C:\Windows\System32\wsl.exe' -Action Allow`

### agent 기동 시 `Cannot open display` / Chromium `Page crashed!`

이 증상이 **호스트에서** 나면:

**Mac**:
- `Cannot open display` → `$DISPLAY` 없음. 하지만 macOS 호스트에서는 AppKit 으로 띄우므로 이 에러가 나올 일이 거의 없음. XQuartz 관련 잔재 env 때문일 수 있음 — `unset DISPLAY` 후 재시도
- `Page crashed!` → macOS 권한 팝업 ("Chromium이 인터넷에서 다운로드된 앱") 이 블록 중일 수 있음. 시스템 설정 → 보안 및 개인 정보 보호 → "확인 없이 열기"

**Windows 11 / WSL2 Ubuntu**:
- `Cannot open display` → WSLg 비활성 또는 `$DISPLAY` / `$WAYLAND_DISPLAY` 누락. 관리자 PowerShell 에서 `wsl --update` 로 커널 + WSLg 업데이트, 이후 `wsl --shutdown` → WSL 재기동. 빈 변수 확인: WSL `echo $DISPLAY $WAYLAND_DISPLAY`
- `Page crashed!` → Chromium 이 `/dev/shm` 공유메모리가 부족할 때 발생. WSL2 의 `/dev/shm` 은 기본 64MB. 해결:
  ```bash
  sudo mount -o remount,size=2G /dev/shm
  ```
  영구 적용은 `/etc/fstab` 에 `tmpfs /dev/shm tmpfs defaults,size=2g 0 0` 추가
- Playwright Chromium 의존 라이브러리 누락:
  ```bash
  ~/.dscore.ttc.playwright-agent/venv/bin/python -m playwright install-deps chromium
  # 또는: sudo apt install -y libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 libxkbcommon0 libxcomposite1 libxdamage1 libxrandr2 libgbm1 libasound2
  ```

이 증상이 **컨테이너에서** 나면 브랜치 잘못 사용 — 이 설계는 Playwright 가 호스트에서 실행되어야 함. 위 agent 연결 단계 (§2.4) 수행.

### Pipeline Stage 3 가 `Dify /v1/chat-messages` 400 또는 timeout

3대 원인:

1. **호스트 Ollama 미기동** — `ollama list` / `curl http://127.0.0.1:11434/api/tags` 확인 후:
    - Mac: `brew services start ollama`
    - WSL2: `sudo systemctl start ollama` (systemd 활성) 또는 `nohup ollama serve >/tmp/ollama.log 2>&1 &`
2. **모델 이름 불일치** — `OLLAMA_MODEL` env 값과 `ollama list` NAME 비교
3. **`--add-host` 누락** — `docker exec dscore.ttc.playwright curl -fsS http://host.docker.internal:11434/api/tags` 실패 시 docker run 에 `--add-host host.docker.internal:host-gateway` 재추가

### Dify 가 옛 base_url 로 호출 (`127.0.0.1:11434 → Connection refused`)

provision-apps.sh 재실행으로 credential swap + Redis FLUSH + plugin-daemon 재기동 연쇄 자동 수행:

```bash
docker exec dscore.ttc.playwright bash /opt/provision-apps.sh
```

완전 재프로비저닝:

```bash
docker exec dscore.ttc.playwright rm -f /data/.app_provisioned
docker restart dscore.ttc.playwright
```

### agent-setup 스크립트가 `JDK 21 미설치` 로 중단

**Mac**:

```bash
brew install --cask temurin@21
# 또는
brew install openjdk@21
sudo ln -sfn /opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk \
  /Library/Java/JavaVirtualMachines/openjdk-21.jdk
java -version   # 21 확인
```

**WSL2 Ubuntu**:

```bash
sudo apt update && sudo apt install -y openjdk-21-jdk-headless
java -version   # 21 확인

# 여러 JDK 가 깔려있어 update-alternatives 가 다른 버전을 가리키면:
sudo update-alternatives --config java    # 21 선택

# Temurin apt repo 를 쓰고 싶다면:
sudo apt install -y wget apt-transport-https
wget -O - https://packages.adoptium.net/artifactory/api/gpg/key/public | sudo tee /etc/apt/trusted.gpg.d/adoptium.asc
echo "deb https://packages.adoptium.net/artifactory/deb $(lsb_release -cs) main" | sudo tee /etc/apt/sources.list.d/adoptium.list
sudo apt update && sudo apt install -y temurin-21-jdk
```

### agent-setup 스크립트가 `Playwright install 실패`

네트워크 문제라면 수동으로 재시도:

**Mac**:

```bash
source ~/.dscore.ttc.playwright-agent/venv/bin/activate
python -m playwright install chromium --force
```

**WSL2 Ubuntu**:

```bash
source ~/.dscore.ttc.playwright-agent/venv/bin/activate
python -m playwright install chromium --force
# 의존 deb 누락 경고가 나오면:
python -m playwright install-deps chromium    # 내부에서 apt install 호출 → sudo 암호 필요
```

### 이미지가 너무 작다 (2-3GB)

아키텍처가 호스트와 다른 경우 qemu 크로스 빌드 silent-fail. build-allinone.sh 는 `uname -m` 자동 감지 (Apple Silicon → arm64, Windows WSL2/Intel → amd64) 하지만 실수로 override 했다면:

```bash
docker rm -f dscore.ttc.playwright
docker rmi dscore.ttc.playwright:latest
./offline/build-allinone.sh    # 자동 감지 — TARGET_PLATFORM env 없이
docker image inspect dscore.ttc.playwright:latest --format '{{.Architecture}}'  # 호스트와 일치해야 정상
```

### Windows: Ollama 가 CPU 로 추론 (속도 1-3 tok/s)

Ollama 는 Windows 네이티브에서 실행되므로 Windows GPU 드라이버의 CUDA 를 직접 사용한다. CPU fallback 증상이면 PowerShell 에서:

```powershell
# A. Ollama GPU 로드 로그 확인
Get-Content "$env:LOCALAPPDATA\Ollama\server.log" -Tail 100 | Select-String 'GPU|CUDA|offload'
# "offloaded 43/43 layers to GPU" 처럼 비(非)-0 offload 면 GPU 사용 중
# "CUDA error" / "0/43 layers" 면 CPU fallback

# B. VRAM 점유 확인
nvidia-smi
# 다른 프로세스가 VRAM 을 거의 점유하면 Ollama 가 CPU 로 떨어짐
```

체크리스트:

1. **Windows NVIDIA 드라이버 최신** — GeForce Experience 또는 CUDA 호환 드라이버
2. **Ollama 재설치/업데이트** — `winget upgrade Ollama.Ollama`
3. 다른 Windows 프로세스 (게임, 브라우저 GPU 가속 등) 가 VRAM 점유 중이면 닫고 재시도
4. **VRAM 용량** — gemma4:e4b 는 5-6GB 소비. RTX 30/40 8GB+ 권장

### <a id="windows-wsl2-오프라인-설치"></a>Windows: WSL2 오프라인 설치 (폐쇄망)

온라인 머신에서 아래 3가지를 미리 수집:

1. **WSL 커널 업데이트 MSI** — `https://learn.microsoft.com/windows/wsl/install-manual` 의 "WSL2 Linux 커널 업데이트 패키지"
2. **Ubuntu 배포판 tarball** — Microsoft Store 의 Ubuntu 22.04 앱이 오프라인에선 불가하므로, `https://cloud-images.ubuntu.com/wsl/jammy/current/` 에서 `ubuntu-jammy-wsl-amd64-ubuntu.rootfs.tar.gz` 다운로드
3. **본 번들** — `dscore.ttc.playwright-*.tar.gz` + `e2e-pipeline/offline/` 전체 (wsl-agent-setup.sh 포함) + 호스트 의존성 deb (openjdk-21, python3.12 등. `apt-get download` 로 수집)

폐쇄망 Windows 11 에서 관리자 PowerShell:

```powershell
# (1) WSL / VirtualMachinePlatform 기능 활성 — 재부팅 필요
dism /online /enable-feature /featurename:Microsoft-Windows-Subsystem-Linux /all /norestart
dism /online /enable-feature /featurename:VirtualMachinePlatform /all /norestart
Restart-Computer

# (2) WSL 커널 MSI 설치
msiexec /i D:\Usb\wsl_update_x64.msi /quiet

# (3) 기본 WSL 버전을 2 로 설정
wsl --set-default-version 2

# (4) Ubuntu tarball 을 Custom 배포로 import
mkdir C:\WSL\Ubuntu
wsl --import Ubuntu C:\WSL\Ubuntu D:\Usb\ubuntu-jammy-wsl-amd64-ubuntu.rootfs.tar.gz --version 2

# (5) 기본 사용자 설정
wsl -d Ubuntu -- bash -c "useradd -m -G sudo -s /bin/bash <사용자명> && passwd <사용자명>"
wsl --manage Ubuntu --set-default-user <사용자명>
```

이후 `wsl` 로 Ubuntu 셸 진입 → 의존성 deb 로컬 설치 → 본 번들 `docker load` → `./offline/wsl-agent-setup.sh` 흐름.

### Windows: WSL2 메모리 부족으로 빌드가 OOM

WSL2 는 기본적으로 호스트 메모리의 50% 또는 8GB 중 작은 값으로 제한. `%USERPROFILE%\.wslconfig` 에:

```ini
[wsl2]
memory=16GB
processors=8
swap=8GB
```

설정 후 PowerShell 관리자에서 `wsl --shutdown` → WSL 재기동.

### 시계 오류로 Dify 로그인 `Invalid encrypted data`

Mac:

```bash
sudo sntp -sS time.apple.com
docker restart dscore.ttc.playwright
```

Windows 11 / WSL2 Ubuntu (Windows 호스트 시계는 관리자 PowerShell 에서 `w32tm /resync`, 이후 WSL 안에서):

```bash
# WSL2 는 Windows 호스트 시계를 따라감. WSL 시계가 어긋났다면:
sudo hwclock -s
docker restart dscore.ttc.playwright
```

---

## 6. 스크립트 가이드

### 파일 레이아웃

```
e2e-pipeline/offline/
├── Dockerfile.allinone          # 2-stage 멀티 빌드
├── build-allinone.sh            # [호스트] 온라인 빌드 — tar.gz 산출
├── entrypoint-allinone.sh       # [컨테이너 PID 1] seed + supervisord + NODE_SECRET
├── provision-apps.sh            # [컨테이너] Dify/Jenkins 프로비저닝 (최초 1회 + 수동 복구)
├── mac-agent-setup.sh           # [호스트 Mac]              agent 셋업 (7단계, idempotent)
├── wsl-agent-setup.sh           # [호스트 WSL2 Ubuntu]      agent 셋업 (7단계, idempotent)
├── pg-init-allinone.sh          # [빌드 타임] PG initdb — Dify 5개 DB 사전 생성
├── supervisord.conf             # [컨테이너] 10개 프로세스 정의
├── nginx-allinone.conf          # [컨테이너] localhost upstream (Dify :18081)
├── requirements-allinone.txt    # [빌드 타임] 컨테이너 Python 의존성
├── README.md                    # 이 문서
├── jenkins-plugins/             # [빌드 타임 산출] *.hpi
└── dify-plugins/                # [빌드 타임 산출] *.difypkg
```

이하 5 개가 **사용자가 직접 호출하거나 동작을 이해할 필요가 있는** 핵심 스크립트.

### 6.1 `build-allinone.sh` — 이미지 빌드 (호스트 실행)

**언제 실행**: 최초 배포, 플러그인/의존성 변경, 버전 업그레이드.

**사전 조건**: 인터넷, Docker buildx, JDK 11+ (플러그인 매니저 실행용), 디스크 여유 20GB+.

**동작 (4 단계)**:

1. Jenkins 플러그인 hpi 재귀 다운로드 → `offline/jenkins-plugins/` (40-50 개)
2. Dify 플러그인 `.difypkg` 다운로드 → `offline/dify-plugins/`
3. Docker buildx 2-stage 빌드 → 이미지 로드
4. `docker save | gzip -1` → `dscore.ttc.playwright-<ts>.tar.gz`

**주요 env** (override 가능):

| 변수 | 기본값 | 비고 |
| --- | --- | --- |
| `IMAGE_TAG` | `dscore.ttc.playwright:latest` | 이미지 repo:tag |
| `TARGET_PLATFORM` | `uname -m` 자동감지 | Apple Silicon → `linux/arm64`, Windows (WSL2/Intel) / Linux x86 → `linux/amd64`. override 시 qemu silent-fail 주의 |
| `OLLAMA_MODEL` | `gemma4:e4b` | Dify provider 등록 시 사용될 모델 id |
| `OUTPUT_TAR` | `dscore.ttc.playwright-<ts>.tar.gz` | 출력 tar.gz 파일명 |
| `JENKINS_VERSION` | (빌드 시 자동 추출) | 2.479+ 요구 |

**소요**: 최초 10-30분, 캐시 재사용 3-5분.

### 6.2 `entrypoint-allinone.sh` — 컨테이너 PID 1

**실행 방식**: `docker run` 이 자동 호출. 직접 호출할 일 없음.

**주요 동작**:

1. `/data/.initialized` 없으면 `/opt/seed/*` → `/data/` 복사 (pg/jenkins/dify 초기 상태)
2. `supervisord` 백그라운드 기동 → 10개 서비스
3. 헬스 대기 → Dify `/console/api/setup` + Jenkins `/api/json` 모두 200
4. `/data/.app_provisioned` 없으면 `bash /opt/provision-apps.sh` 호출 (§6.3)
5. **`NODE_SECRET: <64자 hex>`** 로그 출력 — 호스트 agent 연결에 사용 (§6.4)
6. supervisord foreground wait

**재시작 시**: seed 와 provision 은 플래그로 스킵 → 30-60초 내 ready. NODE_SECRET 은 **매번 재출력**.

### 6.3 `provision-apps.sh` — Dify/Jenkins 프로비저닝 (컨테이너 내부)

**트리거**: entrypoint 가 `.app_provisioned` 없을 때 자동 호출.

**수동 재실행** (Dify base_url 꼬임 등 복구):

```bash
docker exec dscore.ttc.playwright bash /opt/provision-apps.sh
```

**수행 내용**:

- **Dify**: 관리자 생성 → 로그인 → Ollama 플러그인 install → 모델 등록 (`base_url=host.docker.internal:11434`) → **credential_id swap** (Docker 내부 plugin-daemon 캐시 해결) → **Redis FLUSHALL** → dify-api 재기동 → Chatflow import → Publish → API Key 발급
- **Jenkins**: 플러그인 4개 검증 → Credentials (Dify API Key) → Pipeline Job 생성 → **Node `mac-ui-tester`** 생성 (remoteFS=`~/.dscore.ttc.playwright-agent`)

**완전 재프로비저닝**:

```bash
docker exec dscore.ttc.playwright rm -f /data/.app_provisioned
docker restart dscore.ttc.playwright
```

**주의**: Node remoteFS 는 `~` 를 쓰지만 Jenkins remoting 이 expand 하지 않으므로 `mac-agent-setup.sh` 가 실행 시 절대경로로 재설정한다 (§6.4 [5/7]).

### 6.4 `mac-agent-setup.sh` / `wsl-agent-setup.sh` — 호스트 agent 셋업 ⭐

**이 프로젝트의 주된 UX 지점.** 호스트 (Mac 또는 WSL2 Ubuntu) 의 JDK/Python/venv/Playwright/agent.jar 를 idempotent 하게 준비하고 foreground 로 agent 기동한다. 두 스크립트 모두 **bash + 같은 7단계** 로 동작하며 차이는 패키지 매니저 (brew vs apt) 와 JDK 경로 탐색뿐이다.

**실행**:

Mac:

```bash
# 기본 — 의존성이 이미 설치된 환경
NODE_SECRET=<64자> ./offline/mac-agent-setup.sh

# 의존성 자동 설치 (brew 필요; ollama + 모델 pull + openjdk@21 + python@3.12)
NODE_SECRET=<64자> AUTO_INSTALL_DEPS=true ./offline/mac-agent-setup.sh
```

WSL2 Ubuntu (Windows 11 호스트):

```bash
# 기본
NODE_SECRET=<64자> ./offline/wsl-agent-setup.sh

# 의존성 자동 설치 (sudo + apt 필요; openjdk-21 + python3.12)
# Ollama 는 Windows 네이티브 설치이므로 WSL agent 의 책임이 아님 (정보성 체크만)
NODE_SECRET=<64자> AUTO_INSTALL_DEPS=true ./offline/wsl-agent-setup.sh
```

**7 단계 (idempotent — 이미 설치된 것은 스킵)**:

| 단계 | 동작 | Mac 구현 | WSL2 구현 |
| --- | --- | --- | --- |
| 1 | 호스트 Ollama 도달성 체크 | `brew services start ollama` + `ollama list` (Mac 호스트 위에서 동작) | 정보성만 — Windows 네이티브 Ollama 는 사용자가 PowerShell 로 별도 설치. WSL 은 host.docker.internal / default gateway 로 HTTP ping 시도 (실패해도 warn) |
| 2 | JDK 21 탐지 (엄격) | `/opt/homebrew/opt/openjdk@21/bin/java` 또는 `/usr/libexec/java_home -v21` | `/usr/lib/jvm/{temurin-21-jdk,java-21-openjdk}-{amd,arm}64/bin/java` |
| 3 | Python 3.11+ 탐지 | `sys.version_info >= (3,11)` 만족 `python3` | 동일 (Ubuntu 는 `python3.12` / `python3.11`) |
| 4 | venv + `pip install requests playwright pillow` + `playwright install chromium` | `~/.dscore.ttc.playwright-agent/venv/bin/python3` → `~/Library/Caches/ms-playwright/` | `~/.dscore.ttc.playwright-agent/venv/bin/python3` → `~/.cache/ms-playwright/` |
| 5 | Jenkins Node `mac-ui-tester` 의 remoteFS 를 **절대경로**로 Groovy 갱신 + workspace venv symlink | `ln -sfn` | `ln -sfn` |
| 6 | `agent.jar` 다운로드 | `curl -fL` | `curl -fL` |
| 7 | 기존 agent.jar 프로세스 감지 → 종료 → Jenkins disconnect 인지 대기 → 새 agent foreground 기동 | `pgrep` / `kill` + `run-agent.sh` | 동일 |

**주요 env (두 스크립트 공통)**:

| 변수 | 기본값 | 비고 |
| --- | --- | --- |
| `NODE_SECRET` | (필수) | 컨테이너 로그에서 추출한 64자 hex |
| `AUTO_INSTALL_DEPS` | `false` | `true` 면 누락 의존성을 자동 설치<br>Mac: brew (ollama/JDK/Python)<br>WSL2: apt (JDK/Python 만 — Ollama 는 Windows 네이티브 설치이므로 이 스크립트 책임 밖) |
| `OLLAMA_PING_URL` | (자동 탐색) | Ollama 사전 확인 URL 강제 지정. WSL2 에서 Windows Ollama 가 OLLAMA_HOST=0.0.0.0 으로 열려있다면 `http://<windows-ip>:11434` 로 지정 가능 |
| `OLLAMA_MODEL` | `gemma4:e4b` | 존재 확인 대상 모델 |
| `JENKINS_URL` | `http://localhost:18080` | |
| `AGENT_NAME` | `mac-ui-tester` | Jenkins Node 이름 (Pipeline `label` 과 일치해야 함) |
| `MAC_AGENT_WORKDIR` / `WSL_AGENT_WORKDIR` | `$HOME/.dscore.ttc.playwright-agent` | 호스트 agent 작업 디렉토리 |
| `FORCE_AGENT_DOWNLOAD` | `false` | `true` 면 agent.jar 재다운로드 |

**WSL2 고유 주의사항**:

- `$DISPLAY` / `$WAYLAND_DISPLAY` 가 비어있으면 WSLg 미활성 → 관리자 PowerShell `wsl --update`
- NVIDIA GPU 는 Windows 드라이버가 WSL2 에 CUDA 를 자동 노출. `nvidia-smi` 가 Ubuntu 안에서 보여야 Ollama CUDA 사용 가능
- systemd 가 WSL 기본으로 꺼져 있으면 `/etc/wsl.conf` 에 `[boot]\nsystemd=true` 후 `wsl --shutdown`. `wsl-agent-setup.sh` 는 systemd 유무를 감지해서 `systemctl` 또는 `nohup ollama serve` 를 선택한다

**재연결 시나리오**: 같은 NODE_SECRET 으로 재실행하면 7단계의 pre-kill 로직이 기존 java 프로세스를 정리하고 깨끗하게 재연결. 컨테이너 재생성으로 **NODE_SECRET 이 바뀐 경우**엔 새 값 추출 후 재실행.

### 6.5 보조 파일

| 파일 | 역할 | 수정 빈도 |
| --- | --- | --- |
| `Dockerfile.allinone` | 2-stage 멀티 빌드 정의 | 드묾 |
| `pg-init-allinone.sh` | 빌드 타임 `initdb` + Dify 5개 DB 생성 | Dify 버전업 시 |
| `supervisord.conf` | 10개 컨테이너 프로세스 정의 | 서비스 추가/제거 시 |
| `nginx-allinone.conf` | Dify upstream 프록시 (localhost) | 드묾 |
| `requirements-allinone.txt` | 컨테이너 Python 의존성 (Dify api/worker 용) | Dify 버전업 시 |

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

호스트 (Mac 또는 WSL2 Ubuntu)
├─ ollama (:11434, Mac: Metal / WSL2: CUDA)
└─ jenkins agent (JDK 21, Node 레이블 mac-ui-tester)
   └─ Playwright Chromium (headed)
      ├─ Mac:  macOS 네이티브 창
      └─ WSL2: WSLg 경유 Windows 데스크탑 창
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

~/.dscore.ttc.playwright-agent/                  (호스트 Mac / WSL2 Ubuntu — $HOME)
├── venv/                      # Python 3.11+ + playwright/requests/pillow (Linux/Mac 표준 bin/)
├── agent.jar                  # Jenkins controller 에서 받은 jar
├── run-agent.sh               # SCRIPTS_HOME env 주입 + java -jar agent.jar ...
└── workspace/DSCORE-ZeroTouch-QA-Docker/
    └── .qa_home/              # Pipeline 이 생성 (Jenkinsfile AGENT_HOME)
        └── artifacts/

# Chromium 캐시 / Ollama 모델 위치
#   Mac:  ~/Library/Caches/ms-playwright/chromium-*/   ~/.ollama/models/
#   WSL2: ~/.cache/ms-playwright/chromium-*/          ~/.ollama/models/
```

### 프로비저닝 흐름

1. `entrypoint-allinone.sh` — seed 복사 (`.initialized` 가 없을 때만)
2. supervisord 백그라운드 기동 → 10개 서비스
3. 헬스 대기 — dify-api / dify-web / jenkins 모두 HTTP 200
4. `bash /opt/provision-apps.sh` (`.app_provisioned` 가 없을 때만):
   - Dify 관리자 / 로그인 / Ollama 플러그인 / 모델 등록 (base_url=host.docker.internal) / credential_id swap / Redis FLUSH / dify-api 재기동
   - Chatflow import / Publish / API Key 발급
   - Jenkins 플러그인 검증 / Credentials / Pipeline Job / Node 생성 (remoteFS=`~/.dscore.ttc.playwright-agent`, `updateNode` 로 디스크 flush)
5. entrypoint 가 NODE_SECRET 을 로그에 출력 (호스트 agent 연결용)
6. foreground wait
7. **호스트에서 agent setup 스크립트 실행** (Mac: `mac-agent-setup.sh` / WSL2: `wsl-agent-setup.sh`) → Node online → Pipeline 실행 가능
