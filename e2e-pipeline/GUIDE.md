# DSCORE Zero-Touch QA — E2E 자동화 파이프라인 구축 가이드

이 문서는 `e2e-pipeline/` 독립 스택을 처음부터 끝까지 구축하는 가이드다.  
Jenkins + Dify + Ollama(LLM) + Playwright가 모두 포함된 **완전 독립 환경**을 구성한다.  
기존 `docker-compose.yaml`(메인 DevOps 스택)과 **완전히 분리**되어 공존한다.

---

## 목차

0. [소스 코드 준비](#0-소스-코드-준비)
1. [시스템 구성 개요](#1-시스템-구성-개요)
2. [사전 요구사항](#2-사전-요구사항)
3. [디렉터리 구성](#3-디렉터리-구성)
**[자동 설치 (권장)](#자동-설치-권장-setupsh)**
4. [스택 기동](#4-스택-기동)
5. [Ollama LLM 모델 설치](#5-ollama-llm-모델-설치)
6. [Dify 초기 설정](#6-dify-초기-설정)
7. [Dify Chatflow 구성 (Zero-Touch QA Brain)](#7-dify-chatflow-구성-zero-touch-qa-brain)
8. [Jenkins 초기 설정 및 에이전트 등록](#8-jenkins-초기-설정)
9. [E2E 테스트 실행](#9-e2e-테스트-실행)
10. [산출물 확인](#10-산출물-확인)
11. [트러블슈팅](#11-트러블슈팅)

---

## 0. 소스 코드 준비

`e2e-pipeline/` 폴더 하나가 전체 스택의 독립 실행 단위다.  
배포 담당자로부터 이 폴더를 통째로 받아 아무 위치에나 두고 사용한다.

```bash
# 예시: 홈 디렉터리 아래 배치
mv e2e-pipeline/ ~/Developer/e2e-pipeline
cd ~/Developer/e2e-pipeline
```

이후 모든 `docker compose` 명령은 `e2e-pipeline/` 폴더 **안에서** 실행한다.

---

## 1. 시스템 구성 개요

### 1.1 아키텍처

본 스택은 Dify 1.13.3 공식 docker-compose 구조를 기반으로 구성된다.
Jenkins 컨트롤러 + Ollama 옵션 컨테이너가 추가되고, 호스트/컨테이너 Ollama 듀얼 모드를
Docker Compose **프로파일**로 전환한다.

```
사용자 (브라우저 / Jenkins UI)
    │
    ├── http://localhost:18080  →  Jenkins (CI 오케스트레이션)
    │       │
    │       └── mac-ui-tester 에이전트 (Playwright 호스트 실행)
    │               │ DIFY_BASE_URL=http://localhost:18081/v1
    │               ▼
    └── http://localhost:18081  →  nginx (Dify 통합 진입점)
            │
            ├── /console/api, /v1, /api, /files  →  api:5001 (Dify REST API)
            │                                          │
            │                                          ├── plugin_daemon:5002  (플러그인 시스템 — Ollama 포함 모델 공급자)
            │                                          ├── db_postgres:5432   (dify, dify_plugin DB 2개)
            │                                          ├── redis:6379
            │                                          ├── qdrant:6333         (벡터 스토어)
            │                                          └── sandbox:8194        (코드 실행 격리)
            │
            └── /  →  web:3000  (Next.js 프론트엔드)

    [백그라운드 작업]
    worker (Celery) ─── redis ─── worker_beat (Celery Beat 스케줄러)

    [보안]
    sandbox ─── ssrf_proxy_network (internal) ─── ssrf_proxy (Squid) ─── 외부

    [초기화]
    init_permissions (busybox) — storage/plugin 볼륨 chown 후 exit(0)

    [LLM 엔진 — 프로파일별]
    (기본) 호스트 머신의 Ollama (http://host.docker.internal:11434)
    (container-ollama 프로파일) ollama 컨테이너 (http://ollama:11434)
```

### 1.2 서비스 주소 체계

#### 호스트 브라우저 접속 URL

| 서비스 | URL | 역할 |
| --- | --- | --- |
| Jenkins | `http://localhost:18080` | CI/CD 파이프라인 실행 |
| Dify 콘솔 | `http://localhost:18081` | Chatflow 설계, 초기 설정 |

> **포트 설계 의도:** 기존 DevOps 스택(Jenkins 8080, Dify 80)과 충돌하지 않도록 18080/18081을 사용한다.

#### 컨테이너 내부 접근 URL (e2e-net)

| 통신 경로 | URL |
| --- | --- |
| mac-ui-tester → Dify API (호스트 포트) | `http://localhost:18081/v1` |
| api → plugin_daemon | `http://plugin_daemon:5002` |
| api → db_postgres | `db_postgres:5432` (dify DB) |
| plugin_daemon → db_postgres | `db_postgres:5432` (dify_plugin DB) |
| api → redis | `redis:6379` |
| api → qdrant | `http://qdrant:6333` |
| api → sandbox | `http://sandbox:8194` |
| Dify → Ollama (호스트 모드, 기본) | `http://host.docker.internal:11434` |
| Dify → Ollama (컨테이너 모드) | `http://ollama:11434` |

### 1.3 실행 흐름

```
① Jenkins 파이프라인 트리거 (e2e-jenkins, 포트 18080)
② mac-ui-tester 에이전트: python -m zero_touch_qa 직접 실행
③ zero_touch_qa → Dify API 호출 (http://localhost:18081/v1, 시나리오 생성)
④ Dify: Planner/Healer LLM → Ollama 모델 추론
⑤ mac-ui-tester: Playwright Chromium → 실제 브라우저 자동 조작
⑥ 산출물(HTML 리포트, 스크린샷 등) → Jenkins Artifacts 아카이빙
```

---

## 2. 사전 요구사항

| 항목 | 상세 |
| --- | --- |
| OS | Windows 11 / macOS / Linux |
| Docker Desktop | 설치 및 실행 중 (최소 16 GB RAM 할당 권장) |
| Java 11 이상 | Jenkins 에이전트(agent.jar) 실행에 필요 — Mac: `brew install openjdk@17` |
| Python 3.9 이상 + pip | mac-ui-tester 에이전트에서 zero_touch_qa 실행에 필요 |
| 디스크 여유 공간 | 최소 30 GB (Ollama 모델 포함) |
| GPU (선택) | NVIDIA RTX 계열 — docker-compose.yaml의 GPU 섹션 주석 해제로 활용 가능 |

### Windows 11 환경 Docker Desktop 설치

1. [Docker Desktop for Windows](https://www.docker.com/products/docker-desktop/) 다운로드 후 설치
2. WSL2 백엔드 활성화 (설치 화면에서 **Use WSL2** 체크)  
   WSL2가 없다면 PowerShell(관리자)에서:
   ```powershell
   wsl --install
   # 재부팅 후 자동 설치됨
   ```
3. Docker Desktop 실행 → `Settings` → `Resources` → `Memory` **16 GB 이상** 설정
4. RAM이 부족한 환경(16 GB 미만 시스템)은 `.wslconfig`로 제한:
   ```ini
   # C:\Users\{사용자명}\.wslconfig
   [wsl2]
   memory=12GB
   processors=4
   ```
5. `docker info` 명령으로 정상 실행 확인

---

## 3. 디렉터리 구성

```
e2e-pipeline/                        ← 이 폴더가 전체 스택의 독립 실행 단위
├── docker-compose.yaml              ← 독립 E2E 스택 정의 (Dify 1.13.3 공식 구조)
├── Dockerfile.jenkins               ← Jenkins 컨트롤러 이미지
├── dify-chatflow.yaml               ← Dify Chatflow DSL (자동 import 용)
├── zero_touch_qa/                   ← E2E 실행 엔진 패키지
│   ├── __init__.py
│   ├── __main__.py
│   └── ...
├── nginx/
│   └── dify.conf                    ← Dify nginx 라우팅 (resolver 기반 동적 DNS)
├── postgres-init/
│   └── 01-create-plugin-db.sql      ← dify_plugin DB 생성 init 스크립트
├── ssrf_proxy/
│   ├── squid.conf.template          ← Squid 설정 (sandbox SSRF 방어)
│   └── docker-entrypoint.sh         ← Squid 기동 스크립트
├── jenkins-init/                    ← Jenkins 초기 Groovy 스크립트 (setup wizard 우회)
├── setup.sh                         ← ⚠️ 현재 Dify 1.x 구조와 호환 불가, §4~§8 수동 설치 경로 사용
├── DSCORE-ZeroTouch-QA-Docker.jenkinsPipeline
└── GUIDE.md                         ← 이 문서
```

이 폴더 외부에는 아무것도 필요하지 않다.

**docker-compose.yaml 핵심 구조**:
- `x-shared-api-worker-env` YAML 앵커 — api/worker/worker_beat/plugin_daemon 공통 환경변수
- 13개 core 서비스 (기본) + 1개 옵션 (container-ollama 프로파일)
- 2개 네트워크: `e2e-net` (기본) + `ssrf_proxy_network` (internal, sandbox 격리)
- 8개 named 볼륨 (jenkins-home, dify-db, dify-redis, dify-storage, dify-plugin, dify-sandbox-deps, qdrant, ollama)

---

## 자동 설치 (setup.sh)

[setup.sh](setup.sh) 는 Dify 1.13.3 구조 + 프로파일 기반 Ollama + 단계별 상세 로그를
지원하도록 재작성되었다. 단 **Dify 1.x 의 Ollama 플러그인/모델 공급자 등록은
마켓플레이스 기반이라 API 자동화를 지원하지 않으므로 그 부분은 수동**이다.

### 실행 방법

> **Windows 사용자:** `setup.sh` 는 bash 스크립트이므로 **WSL2 터미널**에서 실행해야 한다.
> PowerShell / cmd 에서는 동작하지 않는다. WSL2 에서 Docker 통합이 활성화되어 있어야 한다:
> Docker Desktop → Settings → Resources → WSL Integration → 사용 중인 distro 체크.

#### 권장: `.env` 파일에 계정 정보 미리 지정

setup.sh 는 같은 폴더에 **`.env` 파일이 있으면 자동으로 읽어들인다**. 이 방식의 장점:

- 비밀번호가 shell history 에 남지 않음
- 매 실행마다 길게 환경변수를 입력하지 않아도 됨
- 어떤 변수가 있는지 한 파일에서 한눈에 보임
- `.gitignore` 에 등록되어 git 에 안 올라감 (실수로 비밀번호가 커밋되지 않음)

**Step 1.** 템플릿 복사 후 편집:

```bash
cd ~/Developer/e2e-pipeline
cp .env.example .env
nano .env       # 또는 vim, code 등 본인이 쓰는 에디터
```

**Step 2.** `.env` 안의 비밀번호를 본인이 기억할 수 있는 값으로 수정:

```bash
DIFY_EMAIL=alice@mycompany.com
DIFY_PASSWORD=MySecure!Pass2026
JENKINS_ADMIN_USER=alice
JENKINS_ADMIN_PW=MySecure!Pass2026
```

**Step 3.** 그냥 실행:

```bash
./setup.sh
```

setup.sh 가 시작 배너에서 `설정 소스 : .env 파일 로드됨` 라인을 출력하면 정상.

#### `.env` 없이 실행하는 경우

`.env` 파일이 없으면 기본값으로 진행하고, 시작 배너에서 **⚠ 비밀번호가 기본값(Admin1234!)이다** 경고가 출력된다. 보안상 권장하지 않는다.

```bash
# 기본값으로 실행 (테스트 환경에서만 권장)
./setup.sh

# 컨테이너 Ollama 모드 — 스택 내부에 ollama 컨테이너를 띄움 (호스트 ollama 불필요)
OLLAMA_PROFILE=container ./setup.sh

# 일회성 오버라이드 (환경변수 → .env → 기본값 우선순위)
DIFY_PASSWORD='QuickTest1!' ./setup.sh
```

### 환경변수 전체 목록

`.env` 파일 또는 셸 환경변수로 지정 가능. 우선순위: **셸 환경변수 > .env 파일 > 기본값**.

| 변수 | 기본값 | 설명 |
| --- | --- | --- |
| `DIFY_EMAIL` | `admin@example.com` | Dify 관리자 이메일. Phase 4-1 에서 이 값으로 계정 생성 후 영구 저장 |
| `DIFY_PASSWORD` | `Admin1234!` | Dify 관리자 비밀번호. ⚠ 기본값은 보안상 권장하지 않음 |
| `JENKINS_ADMIN_USER` | `admin` | Jenkins 관리자 계정. Phase 1 의 Groovy init 으로 생성 후 영구 저장 |
| `JENKINS_ADMIN_PW` | `Admin1234!` | Jenkins 관리자 비밀번호. ⚠ 기본값은 보안상 권장하지 않음 |
| `OLLAMA_PROFILE` | `host` | `host` = 호스트 Ollama 사용 / `container` = `--profile container-ollama` 적용 |
| `OLLAMA_MODEL` | `qwen3.5:4b` | Phase 3 에서 확인/Pull 할 모델명. dify-chatflow.yaml 과 일치 필수 |
| `SCRIPTS_HOME` | 스크립트 디렉터리 | mac-ui-tester 노드 `SCRIPTS_HOME` 환경변수 값 |
| `SETUP_LOG` | `./setup.log` | 전체 로그 파일 경로 (stdout 과 동시 기록) |
| `DEBUG` | `0` | `1` 설정 시 curl 응답 등 상세 출력 |

> **계정 정보는 어디에 저장되나?**
> - **Dify**: postgres 컨테이너의 `dify` 데이터베이스 → `e2e-pipeline_e2e-dify-db` 볼륨
> - **Jenkins**: `e2e-pipeline_e2e-jenkins-home` 볼륨의 `users/` 디렉터리
>
> 한번 setup.sh 가 계정을 만들면 `docker compose down && docker compose up` 으로 스택을 껐다 켜도 동일 계정으로 로그인된다. **계정 정보를 바꾸려면**:
> 1. `docker compose down -v` (`-v` 가 핵심 — 볼륨 삭제)
> 2. `.env` 파일에서 새 비밀번호로 수정
> 3. `./setup.sh` 재실행 → 새 비밀번호로 다시 가입
>
> `-v` 없이 .env 만 바꿔도 계정은 변경되지 않는다 (이미 DB 에 저장되어 있음).

### 호스트 Ollama 자동 설치 (Phase 0)

`OLLAMA_PROFILE=host`(기본) 에서 호스트 `ollama` 명령이 없으면 Phase 0 에서 OS 를 감지해 **자동 설치**한다.

| OS | 감지 조건 (`uname -s`) | 설치 방식 | 필수 전제 |
| --- | --- | --- | --- |
| Windows | `MINGW*` / `MSYS*` / `CYGWIN*` | `winget install --id Ollama.Ollama --silent` | Windows App Installer 설치되어 있을 것. UAC 프롬프트가 뜰 수 있음 |
| macOS | `Darwin*` | `brew install ollama` + `brew services start ollama` | Homebrew 설치되어 있을 것 |
| Linux | `Linux*` | `curl -fsSL https://ollama.com/install.sh \| sh` | `sudo` 권한 (스크립트가 내부에서 요구) |

**설치 후 반드시 확인해야 하는 것:**

1. **`OLLAMA_HOST` 바인딩** — 기본값 `127.0.0.1` 로는 컨테이너에서 못 닿는다. `0.0.0.0` 으로 재바인딩 필요.
   - Windows: 시스템 환경변수 `OLLAMA_HOST=0.0.0.0` 등록 → 트레이의 Ollama 종료 → 재시작
   - macOS: `OLLAMA_HOST=0.0.0.0 ollama serve` 로 수동 기동 (또는 launchd plist 수정)
   - Linux: `sudo systemctl edit ollama` → `Environment=OLLAMA_HOST=0.0.0.0` 추가 → `systemctl restart ollama`
2. **Windows PATH 반영** — winget 설치 직후 현재 셸에는 `ollama.exe` PATH 가 즉시 반영되지 않을 수 있다.
   setup.sh 는 `$LOCALAPPDATA/Programs/Ollama` 를 자동으로 PATH 에 추가하지만, 실패하면 새 Git Bash 터미널을 연 뒤 재실행하면 된다.

**자동 설치를 건너뛰고 싶다면** `OLLAMA_PROFILE=container ./setup.sh` 로 실행. 이 경우 호스트 Ollama 는 전혀 필요하지 않고 `e2e-ollama` 컨테이너가 대신 띄워진다.

### 로그

`setup.sh` 실행 중 **모든 단계**는 다음 형식으로 화면과 `setup.log` 파일에 동시 기록된다:

```
[HH:MM:SS] [▶] === Phase N: 섹션명 ===
[HH:MM:SS] [·] 일반 진행 로그
[HH:MM:SS] [✓] 성공/완료
[HH:MM:SS] [⚠] 경고 (복구 가능한 실패)
[HH:MM:SS] [✗] 치명적 오류
[HH:MM:SS] [D] DEBUG=1 상세 출력
[HH:MM:SS] [✓] Phase 완료 (N초)
```

로그 파일 `setup.log` 로는 재실행 사이의 출력이 누적되지 않고 매 실행마다 초기화된다.
문제 추적 시 `cat setup.log` 또는 `grep -E '\[✗\]|\[⚠\]' setup.log` 로 에러/경고만 추출 가능.

### 자동화 범위 (Phase 별)

| Phase | 내용 | 상태 |
| --- | --- | --- |
| 0 | 사전 요구사항 확인 (docker, python3, 필수 파일) + 호스트 Ollama 자동 설치 (host 프로파일, 부재 시 winget/brew/curl) | ✅ 자동 |
| 1 | `jenkins-init/` + `docker-compose.override.yaml` 생성, `docker compose up -d --build` (프로파일 인식) | ✅ 자동 |
| 2 | nginx / api / plugin_daemon / jenkins 헬스 대기 | ✅ 자동 |
| 3 | Ollama 모델 확인/Pull (호스트 or 컨테이너), Dify api → Ollama 도달성 검증 | ✅ 자동 |
| 4-1 | Dify 관리자 계정 생성 (`POST /console/api/setup`) | ✅ 자동 |
| 4-2 | Dify 로그인 → access_token 획득 | ✅ 자동 |
| 4-3 | **Ollama 플러그인 설치 + 모델 공급자 등록** | ⚠️ **수동** (Dify 1.x 마켓플레이스 기반, 자동화 불가) |
| 4-4~4-6 | Chatflow DSL import → 게시 → API Key 발급 (엔드포인트가 아직 동작하면 자동) | ⚠️ 시도 자동 / 실패 시 수동 안내 |
| 5-1 | Jenkins 플러그인 설치 (`workflow-aggregator`, `file-parameters`, `htmlpublisher`) + 재시작 | ✅ 자동 |
| 5-2 | Jenkins Credentials 등록 (`dify-qa-api-token`) | ✅ 자동 (API Key 획득된 경우) |
| 5-3 | CSP 완화 (JAVA_OPTS, override.yaml 에서 영구 적용) | ✅ 자동 |
| 5-4 | `DSCORE-ZeroTouch-QA-Docker` Pipeline Job 생성 | ✅ 자동 |
| 5-5 | `mac-ui-tester` 노드 등록 (`SCRIPTS_HOME` 포함) | ✅ 자동 |
| — | agent.jar 실행 (에이전트 머신) | ⚠️ 수동 |

### ⚠️ setup.sh 가 끝난 뒤 반드시 해야 할 수동 작업

> setup.sh 가 `[✓] DSCORE Zero-Touch QA 스택 설치 완료` 를 찍었다고 모든 게 끝난 게 아니다.
> **아래 3가지 작업이 수동으로 필요**하다 — 자동화가 기술적으로 불가능한 영역이기 때문이다 (Dify 마켓플레이스 UI 강제, 에이전트 머신은 setup.sh 가 도달 불가).
>
> **이 3가지를 하지 않으면 빌드가 절대 동작하지 않는다.** 빠뜨리면 `model not found` / `agent offline` 으로 막힌다.

#### ✅ 작업 1 — Dify 에 Ollama 플러그인 설치 (5분, 필수)

**왜?** Dify 1.x 부터 모델 공급자(Ollama 포함)는 마켓플레이스 플러그인이다. plugin_daemon 이 인터넷에서 venv 를 받아와 초기화하는 절차라 REST API 자동화가 불가능.

**어떻게?**
1. 브라우저로 `http://localhost:18081/signin` 접속 → setup.sh 가 만든 계정으로 로그인
   - 기본: `admin@example.com` / `Admin1234!`
   - 오버라이드한 경우: `DIFY_EMAIL` / `DIFY_PASSWORD` 값
2. 우측 상단 프로필 아이콘 → **설정(Settings)** 클릭
3. 좌측 사이드바 **플러그인(Plugins)** → 상단 **Marketplace** 탭
4. 검색창에 `Ollama` → 결과 카드의 **Install** 클릭 → 다이얼로그에서 한 번 더 확인
5. ~30초 대기 (plugin_daemon 이 내부 venv 초기화)
6. 설치 완료 후 "Installed" 또는 "My Plugins" 목록에 `Ollama` 가 보이면 OK

**자세한 단계별 안내**: [§6.2.1](#621-ollama-플러그인-설치-단계별)

#### ✅ 작업 2 — Dify 에 Ollama 모델 등록 (2분, 필수)

**왜?** 작업 1 이 끝나도 "공급자(Ollama 자체)" 만 등록됐을 뿐이다. 어느 구체적 모델(`qwen3.5:4b` 등)을 쓸지를 별도로 지정해야 Chatflow 가 동작한다.

**어떻게?**
1. **설정 → 모델 공급자(Model Providers)**
2. **Ollama** 카드에서 **+ Add Model** 클릭
3. 다이얼로그 입력:

| 항목 | 호스트 Ollama 모드 (기본) | 컨테이너 Ollama 모드 |
| --- | --- | --- |
| Model Type | `LLM` | `LLM` |
| **Model Name** | `qwen3.5:4b` ⚠️ 정확히 | `qwen3.5:4b` ⚠️ 정확히 |
| **Base URL** | `http://host.docker.internal:11434` | `http://ollama:11434` |

4. `Save` 클릭 → 모델 목록에 표시되면 OK

> ⚠️ **Model Name 은 [dify-chatflow.yaml](dify-chatflow.yaml) 의 값과 바이트 단위로 정확히 일치**해야 한다. 파일에 `qwen3.5:4b` 로 하드코딩되어 있다. 다른 모델을 쓰려면 DSL 파일도 함께 수정 후 Chatflow 를 재import.
>
> ⚠️ **호스트 Ollama 모드 사용 시** 호스트의 Ollama 가 `OLLAMA_HOST=0.0.0.0` 로 바인딩되어 있어야 한다 (기본값 `127.0.0.1` 은 컨테이너에서 못 닿음). [§6.2 함정 체크리스트](#62-ollama-플러그인-설치-및-모델-공급자-등록) 참조.

**자세한 단계별 안내 + 함정 체크리스트**: [§6.2.2](#622-ollama-모델-공급자에-모델-등록-단계별)

#### ✅ 작업 3 — Jenkins 에이전트 머신에 agent.jar 실행 (~10분, 필수)

**왜?** setup.sh 는 Jenkins 컨트롤러에 `mac-ui-tester` 노드 메타데이터만 등록한다. 실제로 빌드를 수행할 에이전트 프로세스(`agent.jar`)는 **에이전트 머신 위에서 직접 실행**해야 한다 (Mac, Windows, Linux, WSL2 — Playwright 가 동작할 수 있는 아무 머신).

**준비물 (에이전트 머신에 설치 확인):**

| 도구 | 확인 명령 | 없을 때 설치 |
| --- | --- | --- |
| **JDK 11+** (17 권장) | `java -version` | Ubuntu/WSL2: `sudo apt install -y openjdk-17-jre-headless`<br>macOS: `brew install openjdk@17`<br>Windows: `winget install --id EclipseAdoptium.Temurin.17.JDK` |
| **Python 3.9+** | `python3 --version` | 대부분 OS 에 이미 포함. 없으면 apt/brew/winget 로 설치 |
| **Playwright + chromium** | `python3 -m playwright --version` | `pip3 install playwright && playwright install chromium` |

> 자세한 JDK/Playwright 설치 안내는 **[§8.6 사전 조건](#사전-조건-jdk-11-이상-설치)** 참조.

**실행 명령:**

setup.sh 종료 배너의 아래 블록을 그대로 복사해 **에이전트 머신 위에서** 실행한다 (시크릿은 매 setup.sh 실행마다 새로 생성되므로 setup.sh 출력에서 복사).

```bash
curl -O http://localhost:18080/jnlpJars/agent.jar
java -jar agent.jar \
  -url "http://localhost:18080/" \
  -secret "<setup.sh 배너에서 복사>" \
  -name "mac-ui-tester" \
  -webSocket \
  -workDir "$HOME/jenkins-agent"
```

> **⚠️ `-workDir` 는 반드시 쓰기 권한이 있는 경로**여야 한다.
> - **권장**: `$HOME/jenkins-agent` — bash 가 자동으로 현재 사용자의 홈 디렉터리로 확장 (예: `/home/alice/jenkins-agent`)
> - **금지**: `/home/jenkins-agent` — 이건 root 소유의 최상위 디렉터리라 일반 사용자가 만들 수 없어 `java.nio.file.AccessDeniedException` 이 난다 (실제 발생한 이슈).
> - Jenkins UI 에 `Remote root directory` 로 `/home/jenkins-agent` 가 보여도 그건 **표시용 메타데이터**일 뿐이고, 실제 디스크 경로는 `-workDir` 플래그가 결정한다.

> **컨트롤러가 다른 머신인 경우**: `http://localhost:18080/` 을 컨트롤러의 접근 가능한 주소로 교체 (예: `http://192.168.1.100:18080/`).

**완료 확인:**
1. 에이전트 터미널에 `INFO: Connected` 로그가 출력됨
2. Jenkins UI → `Jenkins 관리` → `노드 관리(Manage Nodes)` → `mac-ui-tester` 상태가 **"Connected"** (또는 "Idle") 로 표시

**⚠️ 이 명령을 종료하지 말 것** — `Ctrl+C` 로 끄면 노드가 오프라인된다. 백그라운드 실행 방법은 [§8.6 Step 2](#step-2-agentjar-다운로드--실행-반드시-수동) 참조 (`nohup` 또는 systemd 서비스).

**자세한 단계별 안내**: [§8.6 Step 2 ~ Step 3](#step-2-agentjar-다운로드--실행-반드시-수동)

---

#### 작업 3가지 완료 후 첫 빌드 실행

위 3가지가 모두 끝났다면 시스템이 동작 가능한 상태다. 빠른 동작 확인:

1. Jenkins UI → `DSCORE-ZeroTouch-QA-Docker` Job 클릭 → **Build with Parameters**
2. 파라미터 입력:
   - `RUN_MODE`: `chat`
   - `TARGET_URL`: `https://www.naver.com`
   - `SRS_TEXT`: `네이버 검색창에 DSCORE 입력 후 엔터를 누른다`
3. **Build** 클릭 → 콘솔 로그에서 `Stage: Plan → Stage: Execute → Stage: Report` 진행 확인

문제 발생 시 → [§11 트러블슈팅](#11-트러블슈팅) 참조. 특히:
- `model not found` → 작업 2 의 Model Name 일치성 재확인
- `agent offline` → 작업 3 의 agent.jar 가 살아있는지 확인
- `setup.log` 의 `[✗]` / `[⚠]` 마커 → 해당 Phase 섹션 참조

> **setup.sh 의 Phase 가 일부 실패한 경우**: `setup.log` 에서 `[✗]` / `[⚠]` 라인을 찾고, 그 Phase 가 다루는 §4~§8 섹션을 수동 진행한다. setup.sh 실행과 수동 실행은 같은 docker 스택을 다루므로 섞어 써도 안전하다.

---

## 4. 스택 기동

### 4.1 Ollama 실행 모드 선택

본 스택은 **Ollama 를 실행하는 위치**에 따라 두 가지 모드를 지원한다.
Docker Compose **프로파일** 기능으로 단일 compose 파일에서 전환한다.

| 모드 | 설명 | 기동 명령 | Dify 공급자 Base URL |
| --- | --- | --- | --- |
| **호스트 Ollama** (기본) | 호스트 머신에 Ollama 를 직접 설치 후 사용 | `docker compose up -d --build` | `http://host.docker.internal:11434` |
| **컨테이너 Ollama** | 스택 내부에 Ollama 컨테이너(`e2e-ollama`)를 올려 사용 | `docker compose --profile container-ollama up -d --build` | `http://ollama:11434` |

> **언제 어느 모드를 쓰는가?**
> - 호스트 머신에 이미 Ollama 가 설치돼 있고 다른 도구(LM Studio, VS Code 확장 등)와 공유 중이면 **호스트 Ollama 모드**. 모델을 재다운로드할 필요 없다.
> - Ollama 를 격리된 환경에서만 쓰거나 호스트 설치를 피하고 싶으면 **컨테이너 Ollama 모드**. 모델은 `e2e-ollama` 컨테이너 볼륨에 Pull 해야 한다.
>
> **호스트 Ollama 모드의 필수 사전 조건**:
> 호스트의 Ollama 가 반드시 **`OLLAMA_HOST=0.0.0.0`** 으로 바인딩되어 있어야 컨테이너에서 접근 가능하다.
> - Windows: 시스템 환경변수 `OLLAMA_HOST=0.0.0.0` 등록 후 Ollama 재시작(작업 관리자에서 ollama.exe 종료 → 다시 실행)
> - Linux/macOS: `OLLAMA_HOST=0.0.0.0 ollama serve` 로 기동
>
> 기본값(`127.0.0.1`) 상태에서는 Dify 컨테이너에서 호스트 Ollama 에 절대 도달하지 못하고, 모델 공급자 테스트가 `Connection refused` 로 실패한다.

### 4.2 최초 기동

`e2e-pipeline/` 폴더 **안에서** 실행한다.

**호스트 Ollama 모드 (기본)**:
```bash
cd ~/Developer/e2e-pipeline
docker compose up -d --build
```

**컨테이너 Ollama 모드**:
```bash
cd ~/Developer/e2e-pipeline
docker compose --profile container-ollama up -d --build
```

빌드 및 최초 기동은 **30 ~ 50분** 소요된다.
주요 시간 소요: Jenkins 이미지 빌드(Python 패키지 + Playwright, ~20~30분) + Dify 이미지 Pull + (컨테이너 모드인 경우) Ollama 이미지 Pull.
두 번째 기동부터는 Docker 레이어 캐시로 수 분 내 완료된다.

### 4.3 기동 상태 확인

> **최초 기동 시 주의:** `e2e-dify-api` 컨테이너는 기동 직후 PostgreSQL 스키마 마이그레이션을 수행한다.
> 이 작업은 **1~3분** 소요되며, 완료 전 `http://localhost:18081` 접속 시 `502 Bad Gateway`가 나타나는 것은 **정상**이다.
> 아래 명령으로 완료를 확인한다:
> ```bash
> docker logs -f e2e-dify-api 2>&1 | grep -E "Running|started|migration|Database"
> # "Database migration successful!" + "Listening at: http://0.0.0.0:5001" 출현 시 준비 완료
> ```

```bash
docker compose ps
```

아래 서비스가 모두 `Up` 상태여야 한다 (호스트 Ollama 모드 기준):

| 컨테이너 이름 | 정상 상태 | 비고 |
| --- | --- | --- |
| `e2e-jenkins` | Up | 18080 포트 응답 |
| `e2e-dify-db` | Up (healthy) | healthcheck 통과 필요 |
| `e2e-dify-redis` | Up (healthy) | healthcheck 통과 필요 |
| `e2e-dify-sandbox` | Up (healthy) | |
| `e2e-dify-ssrf-proxy` | Up | SSRF 방어 프록시 |
| `e2e-dify-api` | Up | Dify REST API |
| `e2e-dify-worker` | Up | Celery 워커 |
| `e2e-dify-worker-beat` | Up | Celery Beat 스케줄러 |
| `e2e-dify-plugin-daemon` | Up | Dify 플러그인 데몬 |
| `e2e-dify-web` | Up | Next.js 프론트엔드 |
| `e2e-dify-nginx` | Up | 18081 포트 응답 |
| `e2e-qdrant` | Up | 벡터 스토어 |
| `e2e-dify-init-permissions` | Exited (0) | 볼륨 소유권 초기화 후 종료 — 정상 |
| `e2e-ollama` | Up | **container-ollama 프로파일 사용 시에만** 기동됨 |

> `e2e-dify-init-permissions` 는 볼륨을 chown 하고 exit(0) 으로 종료하는 일회성 init 컨테이너다. `docker compose ps` 기본 출력에는 나타나지 않을 수 있으나 `docker ps -a` 에서 `Exited (0)` 로 확인된다.

### 4.4 스택 중지 및 재기동

```bash
# 중지 (데이터 보존)
docker compose down

# 재기동 (기본 = 호스트 Ollama 모드)
docker compose up -d

# 재기동 (컨테이너 Ollama 모드 — 이전에 container-ollama 프로파일로 기동했던 경우)
docker compose --profile container-ollama up -d

# 완전 초기화 (데이터 삭제 — DB/플러그인/설정 전부 날아감)
docker compose down -v
```

---

## 5. Ollama LLM 모델 설치

본 스택은 Dify Chatflow 에서 사용할 LLM 모델이 호스트(또는 컨테이너) Ollama 에
미리 Pull 되어 있다고 가정한다. [dify-chatflow.yaml](dify-chatflow.yaml) 과
[docker-compose.yaml](docker-compose.yaml) 헤더 주석은 모두 동일한 전제로 작성됐다.

### 5.1 전제 모델 (호스트 Ollama 모드 기준)

| 용도 | 모델 | 크기 | 역할 |
| --- | --- | --- | --- |
| Planner + Healer **기본값** | **`qwen3.5:4b`** | ~3.4 GB | 자연어 → 9대 DSL JSON 생성, DOM 분석 → 대체 셀렉터 JSON 제안. Qwen 시리즈는 Gemma 대비 구조화 출력 fidelity 가 안정적이라 기본값으로 선정 |
| 대안/실험용 | `gemma3:4b` | ~3.3 GB | Planner 또는 Healer 를 교체해 품질 비교 시 사용. Chatflow 내 LLM 노드의 `name` 필드만 바꾸면 됨 |

> **품질 주의:** 4B 파라미터 모델은 복잡한 JSON 스키마 생성에서 마진이 얕다.
> Planner 가 생성하는 9대 DSL JSON 에서 **필드 누락/형식 오류**가 간헐 발생할 수 있다.
> 운영 품질이 중요하면 더 큰 모델(예: `qwen3-coder:14b`, `qwen2.5-coder:14b`, `qwen3-coder:30b`)을
> Pull 한 뒤 [dify-chatflow.yaml](dify-chatflow.yaml) 의 `qwen3.5:4b` 참조 2곳을 교체하고 Dify 에서 Chatflow 를 재import 한다.

### 5.2 호스트 Ollama 모드 — 모델 확인 및 Pull

```bash
# 현재 설치된 모델 확인
ollama list
# NAME          ID              SIZE      MODIFIED
# gemma3:4b     ...             3.3 GB    ...
# qwen3.5:4b    ...             3.4 GB    ...

# 기본 모델이 없으면 Pull
ollama pull qwen3.5:4b
ollama pull gemma3:4b  # 선택
```

### 5.3 컨테이너 Ollama 모드 — 컨테이너 안에서 Pull

```bash
docker exec -it e2e-ollama ollama pull qwen3.5:4b
docker exec -it e2e-ollama ollama pull gemma3:4b  # 선택
docker exec -it e2e-ollama ollama list
```

### 5.4 GPU 가속 활성화 (컨테이너 Ollama 모드 전용)

호스트 Ollama 모드는 호스트가 직접 GPU 를 사용하므로 별도 설정 불필요.
**컨테이너 Ollama 모드**에서 GPU 를 쓰려면 [docker-compose.yaml](docker-compose.yaml) 의 `ollama` 서비스에서 GPU 주석을 해제한다:

```yaml
ollama:
  ...
  deploy:
    resources:
      reservations:
        devices:
          - driver: nvidia
            count: all
            capabilities: [gpu]
```

변경 후 재기동:

```bash
docker compose --profile container-ollama up -d ollama
```

---

## 6. Dify 초기 설정

> ## 🗺️ 이 섹션을 어떻게 읽을까
>
> **[setup.sh](setup.sh) 를 사용한 경우:**
> - §6.1 관리자 계정 생성 → **✅ 이미 완료** (건너뛰기)
> - §6.2 Ollama 플러그인 설치 및 모델 공급자 등록 → **✋ 반드시 수동** (자동화 불가)
>
> **수동으로 docker compose 를 기동한 경우:**
> - §6.1 부터 순서대로 모두 진행
>
> 어느 경로든 §6.2 의 **Ollama 플러그인 설치 + 모델 등록**은 반드시 사람이 한 번 해줘야 한다 (Dify 1.x 마켓플레이스 UI 전용).

### 6.1 관리자 계정 생성

> **✅ setup.sh 로 설치했다면 건너뛰어라.** Phase 4-1 에서 자동 생성됐다.
> 생성된 계정은 setup.sh 기본값 `admin@example.com / Admin1234!` 이거나 `DIFY_EMAIL` / `DIFY_PASSWORD` 로 오버라이드한 값이다. `http://localhost:18081/signin` 에서 그 계정으로 바로 로그인된다.

**수동 설치 시:**

1. 브라우저에서 `http://localhost:18081` 접속
2. 최초 접속 시 **관리자 계정 생성** 화면이 나타난다
3. **사용할 이메일과 비밀번호를 직접 입력**하고 계정을 생성한다

### 6.2 Ollama 플러그인 설치 및 모델 공급자 등록

> **⚠️ 반드시 수동으로 한 번 해줘야 한다.**
> Dify 1.x 는 모델 공급자(Ollama 포함)를 **마켓플레이스 플러그인**으로 제공하고, 마켓플레이스 설치는 REST API 로 자동화할 수 없다. setup.sh 로 설치했어도 이 단계는 건너뛸 수 없다.
>
> **필요한 것:** 인터넷 연결 (marketplace.dify.ai 접근), 5분 정도의 시간.

#### 6.2.1 Ollama 플러그인 설치 (단계별)

**Step 1.** `http://localhost:18081/signin` 에 로그인한다.
 - setup.sh 사용자: `admin@example.com / Admin1234!` (기본값)
 - 수동 사용자: §6.1 에서 직접 만든 계정

**Step 2.** 우측 상단 **프로필 아이콘** 클릭 → 드롭다운 메뉴에서 **설정(Settings)** 선택.

**Step 3.** 좌측 사이드바에서 **플러그인(Plugins)** 을 클릭한다.

> 만약 좌측에 "플러그인" 메뉴가 보이지 않으면, plugin_daemon 이 아직 기동 중이거나 장애 상태다. 터미널에서 확인:
> ```bash
> docker compose logs plugin_daemon | tail -30
> ```
> `Launching gnet with ... listening on: tcp://0.0.0.0:5003` 이 보이면 정상 기동. 없으면 §11 트러블슈팅 참조.

**Step 4.** 화면 상단의 **Marketplace** 탭을 클릭한다 (로컬 설치된 플러그인 목록 화면이 아니라 마켓플레이스로 넘어가야 한다).

**Step 5.** 검색창에 `Ollama` 입력 → 검색 결과에서 **Ollama** 카드의 **Install** 버튼 클릭.

**Step 6.** 설치 진행 다이얼로그가 뜨면 `Install` 확인. 약 **30초~1분** 대기 (plugin_daemon 이 내부 venv 를 초기화한다).

**Step 7.** 설치 완료 후 "My Plugins" 또는 "Installed" 목록에 `Ollama` 가 표시되고 상태가 `Installed` 면 성공.

#### 6.2.2 Ollama 모델 공급자에 모델 등록 (단계별)

플러그인을 설치했다고 모델이 자동으로 잡히는 건 아니다. Ollama 공급자에 구체적 모델(`qwen3.5:4b` 등)을 등록해야 Chatflow 에서 사용 가능하다.

**Step 1.** **설정 → 모델 공급자(Model Providers)** 로 이동.

**Step 2.** 목록에서 **Ollama** 카드를 찾는다 (§6.2.1 설치 후 바로 나타나야 함). 카드 우측 하단의 **+ Add Model** 버튼 클릭.

**Step 3.** 다이얼로그에 아래 값을 정확히 입력한다.

| 항목 | 호스트 Ollama 모드 (기본) | 컨테이너 Ollama 모드 |
| --- | --- | --- |
| **Model Type** | `LLM` | `LLM` |
| **Model Name** | `qwen3.5:4b` | `qwen3.5:4b` |
| **Base URL** | `http://host.docker.internal:11434` | `http://ollama:11434` |
| **Model context size** | `4096` (모델 기본값) | `4096` |
| **Upper bound for max tokens** | `4096` | `4096` |

나머지 옵션(Function call, Vision, Stream function 등)은 기본값 그대로 둔다.

**Step 4.** `Save` 클릭 → 에러 없이 저장되면 목록에 `qwen3.5:4b` 가 `Ollama` 아래 등록된다.

**Step 5. (선택)** 대안 모델 `gemma3:4b` 를 추가로 등록하고 싶으면 같은 방법으로 반복. Planner/Healer 교체 실험용.

---

> ## ⚠️ 실패하기 쉬운 함정 체크리스트
>
> **함정 1 — Model Name 불일치**
> 위에서 입력한 Model Name 은 반드시 [dify-chatflow.yaml](dify-chatflow.yaml) 의 Planner/Healer LLM 노드 `name` 값과 **바이트 단위로 일치**해야 한다. 파일 안에 `qwen3.5:4b` 로 하드코딩되어 있다. 다른 모델을 쓰려면 DSL 파일의 두 곳(Planner, Healer)을 바꾸고 Chatflow 를 **재import** 해야 한다. 불일치 시 Chatflow 실행에서 `model not found` 에러가 난다.
>
> **함정 2 — 호스트 Ollama 의 바인딩 주소**
> 호스트 Ollama 모드는 호스트에서 실행 중인 `ollama` 가 반드시 `OLLAMA_HOST=0.0.0.0` 으로 바인딩되어 있어야 한다. 기본값은 `127.0.0.1` 이라 컨테이너에서 접근 불가.
> - **Windows**: 시스템 환경변수 `OLLAMA_HOST=0.0.0.0` 추가 → 트레이의 Ollama 종료 → 시작 메뉴에서 재실행
> - **macOS**: `OLLAMA_HOST=0.0.0.0 ollama serve` 로 수동 기동 (brew services 로 기동한 경우 plist 수정 필요)
> - **Linux**: `sudo systemctl edit ollama` → `[Service]` 아래 `Environment=OLLAMA_HOST=0.0.0.0` 추가 → `sudo systemctl restart ollama`
>
> **함정 3 — 호스트/컨테이너 모드 Base URL 혼동**
> - 호스트 모드에서 `http://localhost:11434` 또는 `http://127.0.0.1:11434` 쓰면 **안된다**. 반드시 `http://host.docker.internal:11434`.
> - 컨테이너 모드에서 `http://host.docker.internal:11434` 쓰면 **안된다**. 반드시 서비스명 `http://ollama:11434`.
>
> ### 사전 검증 (권장)
> 모델 등록 전에 Dify api 컨테이너가 실제로 Ollama 에 닿는지 먼저 확인하는 게 안전하다:
>
> **호스트 Ollama 모드:**
> ```bash
> docker exec e2e-dify-api curl -s -o /dev/null -w "HTTP %{http_code}\n" \
>     http://host.docker.internal:11434/api/tags
> ```
>
> **컨테이너 Ollama 모드:**
> ```bash
> docker exec e2e-dify-api curl -s -o /dev/null -w "HTTP %{http_code}\n" \
>     http://ollama:11434/api/tags
> ```
>
> 둘 다 `HTTP 200` 이 나와야 한다. `HTTP 000` 이나 `Connection refused` 면 Base URL 을 등록해도 실패한다. 함정 2 를 다시 확인해라.

---

## 7. Dify Chatflow 구성 (Zero-Touch QA Brain)

Zero-Touch QA의 지능 계층이다. 자연어/기획서를 9대 DSL로 변환하고, 요소 탐색 실패 시 자가 치유 셀렉터를 제안한다.

> ## 🗺️ 두 가지 경로 중 하나 선택
>
> ### 경로 A — **setup.sh 자동 import 를 사용한 경우 (추천)**
>
> setup.sh Phase 4-4~4-6 가 [dify-chatflow.yaml](dify-chatflow.yaml) 을 자동으로 import → publish → API Key 발급까지 끝낸다. 이 경우 §7.1~§7.6 의 수동 캔버스 구성은 **전부 건너뛰고**, §7.8 (동작 테스트)만 실행하면 된다.
>
> **먼저 setup.sh 로그에서 다음 줄을 확인해라:**
> ```
> [✓] Chatflow import 완료 (App ID: xxxxxxxx)
> [✓] Chatflow publish 완료
> [✓] Dify API Key 발급 완료: app-xxxxxxxxxxxx
> ```
>
> 3 줄 모두 `[✓]` 면 §7.8 로 이동. 하나라도 `[⚠]` 면 경로 B(수동) 로 진행.
>
> ### 경로 B — **수동으로 Chatflow 를 구성하는 경우**
>
> setup.sh 를 쓰지 않았거나, 자동 import 가 실패한 경우. §7.1 부터 §7.7 까지 순서대로 진행.
>
> **빠른 대안 — DSL 파일 수동 import:**
> 전체 §7.2~§7.6 을 수동으로 재현하지 않고 파일에서 한 번에 가져올 수 있다:
> 1. `http://localhost:18081/apps` → `+ 앱 만들기` → **DSL 파일에서 가져오기(Import from DSL)** 선택
> 2. `e2e-pipeline/dify-chatflow.yaml` 파일 업로드
> 3. import 완료 후 **Publish** → §7.7 로 이동해 API Key 발급

### 7.1 Chatflow 앱 생성

1. `http://localhost:18081/apps` 접속
2. 우측 상단 `+ 앱 만들기` 클릭
3. 앱 유형: **Chatflow** 선택  
   > **주의:** `Workflow`가 아닌 반드시 `Chatflow`를 선택한다. 대화 세션(`conversation_id`)이 유지되어야 Heal 모드 컨텍스트가 보존된다.
4. 앱 이름: `ZeroTouch QA Brain`
5. `만들기` 클릭 → 캔버스 편집 화면 진입

### 7.2 Start 노드 — 입력 변수 설정

캔버스의 `Start` 노드를 클릭하여 우측 패널에서 아래 6개 변수를 추가한다.

| # | 변수명 | 타입 | 필수 | 설명 |
| --- | --- | --- | --- | --- |
| 1 | `run_mode` | **Select** | 예 | 옵션: `chat`, `doc`, `heal`. 기본값: `chat` |
| 2 | `srs_text` | **Paragraph** | 아니오 | 자연어 요구사항 (Chat/Doc 모드) |
| 3 | `target_url` | **Text Input** | 아니오 | 테스트 대상 URL |
| 4 | `error` | **Paragraph** | 아니오 | Heal 모드 — 실행 에러 메시지 |
| 5 | `dom` | **Paragraph** | 아니오 | Heal 모드 — HTML DOM 스냅샷 (최대 10,000자) |
| 6 | `failed_step` | **Paragraph** | 아니오 | Heal 모드 — 실패 스텝 JSON 문자열 |

**Select 타입 설정:** `run_mode` 변수 타입을 `Select`로 지정 → 옵션 목록에 `chat`, `doc`, `heal` 추가 → 기본값 `chat`

### 7.3 IF/ELSE 노드 — 실행 분기

`+ 노드 추가` → **IF/ELSE** 선택 → Start 노드 출력에 연결

조건 설정:

| 분기 | 조건 | 연결 대상 |
| --- | --- | --- |
| **IF** | `run_mode` `is` `heal` | Healer LLM 노드 |
| **ELSE** | (나머지: chat, doc) | Planner LLM 노드 |

### 7.4 Planner LLM 노드 (시나리오 생성)

`+ 노드 추가` → **LLM** → 이름: `Planner` → IF/ELSE의 **ELSE** 출력에 연결

**모델 선택:** `qwen3.5:4b` (§6.2 에서 Dify 공급자로 등록한 모델)
- Temperature: `0.3`
- 권장 대안: Pull 되어 있다면 `qwen3-coder:14b`, `qwen3-coder:30b` (4B 대비 JSON 품질↑)
- **주의**: [dify-chatflow.yaml](dify-chatflow.yaml) 은 이 섹션을 **DSL 파일로 자동 import** 하는 경로를 제공한다. 수동 import (DSL 파일에서 가져오기) 를 쓰면 §7.2~§7.6 의 노드 설정을 수동으로 재현할 필요 없이 한 번에 구성되고, 모델명은 파일 안에 `qwen3.5:4b` 로 하드코딩되어 있다.

**System Prompt:**

```
당신은 테스트 자동화 아키텍트입니다. 제공된 요구사항(SRS)을 분석하여 아래의 [9대 표준 액션]만 사용하는 JSON 배열을 작성하십시오.

[9대 표준 액션]: navigate, click, fill, press, select, check, hover, wait, verify

[규칙]:
- 각 스텝은 "step"(숫자), "action"(문자열), "target"(문자열 또는 객체), "value"(문자열), "description"(문자열), "fallback_targets"(문자열 배열) 키를 가져야 합니다.
- target은 가능한 한 의미론적 선택자를 사용하십시오. 예: "role=button, name=로그인", "label=이메일", "text=검색"
- CSS 셀렉터나 XPath는 의미론적 선택자가 불가능한 경우에만 사용하십시오.
- 첫 번째 스텝은 반드시 navigate 액션으로 시작하십시오.
- fallback_targets에는 target이 실패할 경우를 대비한 대체 로케이터를 2~3개 제공하십시오. 실행 엔진이 target 실패 시 이 목록을 순서대로 시도합니다.

[액션별 target/value 규칙]:
- navigate: target은 빈 문자열, value에 URL을 넣으십시오.
- fill: target은 입력할 요소, value에 입력할 텍스트를 넣으십시오.
- press: target은 키를 누를 대상 요소(직전에 fill한 요소와 동일), value에 키 이름(예: "Enter", "Tab")을 넣으십시오.
- click/hover/check/select/verify: target은 대상 요소, value는 해당 액션의 값입니다.
- wait: target은 빈 문자열, value에 대기 밀리초를 넣으십시오.

[엄수 사항]:
- 반드시 유효한 JSON 배열([...]) 형태만 출력하십시오.
- 마크다운 코드블록(```)이나 부연 설명은 절대 금지합니다.
```

**User Prompt:**

프롬프트 입력 영역에서 `{` 키를 눌러 변수를 자동완성으로 삽입한다:

```
대상 URL: {{target_url}}
요구사항: {{srs_text}}
```

### 7.5 Healer LLM 노드 (자가 치유)

`+ 노드 추가` → **LLM** → 이름: `Healer` → IF/ELSE의 **IF** 출력에 연결

**모델 선택:** `qwen3.5:4b` (Planner 와 동일 모델 공유, 단일 공급자 설정으로 충분)
- Temperature: `0.1` (치유는 결정적 출력이 바람직)
- 대안: `gemma3:4b` 로 Healer 만 교체해 DOM 추론 품질 비교 가능

**System Prompt:**

```
당신은 자가 치유(Self-Healing) 시스템입니다. 에러 메시지, 실패한 스텝 정보, 그리고 HTML DOM 스냅샷을 분석하십시오.

[작업]:
- 실패한 스텝(failed_step)의 원래 액션과 타겟을 확인하십시오.
- 에러 메시지를 바탕으로 기존 요소를 찾지 못한 이유를 파악하십시오.
- DOM 내에서 의도에 가장 부합하는 대체 셀렉터를 찾으십시오.
- DOM에 실제로 존재하는 요소만 제안하십시오.

[출력 형식]:
- 순수 JSON 객체({...}) 형식으로만 출력하십시오.
- "target" 키(새 로케이터)와 "fallback_targets" 키(대체 로케이터 배열 2~3개)를 포함하십시오.
- 부연 설명은 절대 금지합니다.
```

**User Prompt:**

```
에러: {{error}}

실패한 스텝:
{{failed_step}}

DOM 스냅샷:
{{dom}}
```

### 7.6 Answer 노드 — 응답 반환

IF/ELSE 분기 각각에 **별도 Answer 노드 2개**를 연결한다.

**Healer 분기 Answer:**
1. `+ 노드 추가` → **Answer** → 이름: `Answer (Heal)`
2. Healer 노드 출력 → 이 Answer 노드 입력 연결
3. 응답 내용: `{` 키 입력 → `Healer` → `text` 선택하여 변수 삽입

**Planner 분기 Answer:**
1. `+ 노드 추가` → **Answer** → 이름: `Answer (Plan)`
2. Planner 노드 출력 → 이 Answer 노드 입력 연결
3. 응답 내용: `{` 키 입력 → `Planner` → `text` 선택하여 변수 삽입

**최종 캔버스 구조:**

```
Start → IF/ELSE ─── IF (heal) ──────→ Healer  → Answer (Heal)
                 └── ELSE (chat/doc) ──→ Planner → Answer (Plan)
```

> **주의:** Answer 노드를 하나로 합치는 방식(두 LLM 출력을 동일 Answer에 연결)은 Dify 버전에 따라  
> 동작이 다를 수 있으므로 분리 방식을 권장한다.

### 7.7 Chatflow 게시 및 API Key 발급

> **✅ setup.sh 로 설치했다면 건너뛰어라.** Phase 4-5/4-6 에서 자동으로 완료된다.
> setup.sh 로그 마지막에 출력된 `API Key: app-xxxxxxxxxxxx` 값을 기록해둬라 — Jenkins Credentials 에 이미 `dify-qa-api-token` 으로 자동 등록되어 있다.
> 자동 등록 실패 시(`API Key 발급 실패` 경고가 있었던 경우)에만 아래 수동 절차를 진행.

**수동 절차:**

1. 캔버스 우측 상단 `게시(Publish)` 버튼 클릭 (이미 publish 된 상태면 "게시됨(Published)" 으로 표시된다)
2. 좌측 메뉴 `API 접근(API Access)` 클릭
3. 화면 우측 상단 `+ 새 API Key 만들기(+ New API Key)` 클릭
4. 이름: `jenkins-zerotouch-qa` 입력 → `Create`
5. 생성 직후 한 번만 전체 토큰이 표시된다 — `app-xxxxxxxxxxxxxxxxxxxx` 값을 **즉시 복사해서 안전한 곳에 저장**

> **⚠️ API Base URL — Jenkins 파이프라인 관점**
> 이 화면 상단에 Dify 가 보여주는 Base URL 은 `http://localhost/v1` 같은 내부 값일 수 있다. **그 값을 그대로 쓰지 마라.**
> mac-ui-tester 에이전트는 호스트 포트로 Dify 에 접근하므로 Jenkinsfile 의 `DIFY_BASE_URL` 은 고정값:
> ```groovy
> DIFY_BASE_URL = "http://localhost:18081/v1"
> ```

### 7.8 Chatflow 동작 테스트

게시 후 캔버스 우측 상단 `미리보기(Preview)` 클릭 → 좌측 변수 패널에 입력:

| 변수 | 테스트값 |
| --- | --- |
| `run_mode` | `chat` |
| `srs_text` | `네이버 메인 페이지에서 검색창에 DSCORE를 입력하고 엔터를 누른다` |
| `target_url` | `https://www.naver.com` |

채팅 입력창에 `실행을 요청합니다.` 입력 후 전송

**기대 출력 (순수 JSON 배열):**

```json
[
  {"step": 1, "action": "navigate", "value": "https://www.naver.com", "target": "", "description": "네이버 메인 페이지로 이동"},
  {"step": 2, "action": "fill", "target": "role=search, name=검색어 입력", "value": "DSCORE", "description": "검색창에 DSCORE 입력"},
  {"step": 3, "action": "press", "target": "role=search, name=검색어 입력", "value": "Enter", "description": "엔터 키 입력"}
]
```

> LLM 응답이 JSON 배열이 아닌 마크다운 코드블록으로 감싸져 나오면, System Prompt의 `[엄수 사항]`을 더 강조하거나 모델을 변경한다.

---

## 8. Jenkins 초기 설정

> ## 🗺️ 이 섹션을 어떻게 읽을까
>
> **setup.sh 로 설치한 경우 (대부분):**
> - §8.1 초기 잠금 해제 → **✅ 자동 우회됨** (Setup Wizard 비활성화 + Groovy init 으로 계정 자동 생성)
> - §8.2 추가 플러그인 설치 → **✅ 자동 완료** (`workflow-aggregator`, `file-parameters`, `htmlpublisher`, `plain-credentials`)
> - §8.3 Credentials 등록 → **✅ 자동 완료** (Dify API Key 가 발급됐을 경우. 실패했으면 수동 절차 필요)
> - §8.4 CSP 설정 → **✅ 자동 완료** (override.yaml JAVA_OPTS)
> - §8.5 Pipeline Job → **✅ 자동 생성**
> - §8.6 mac-ui-tester 노드 → **✅ 자동 등록** (단, agent.jar 실행은 반드시 수동)
>
> **수동으로 docker compose 를 기동한 경우:**
> - §8.1 부터 순서대로 모두 진행
>
> setup.sh 사용자도 **§8.6 Step 2 (agent.jar 실행)** 는 반드시 직접 해야 한다 — Jenkins 밖(에이전트 머신)에서 실행되기 때문.

### 8.1 초기 잠금 해제

> **✅ setup.sh 로 설치했다면 건너뛰어라.** docker-compose.override.yaml 의 `-Djenkins.install.runSetupWizard=false` 로 Setup Wizard 가 비활성화되고, `jenkins-init/01-security.groovy` 가 관리자 계정 (`admin / Admin1234!` 또는 `JENKINS_ADMIN_USER/PW` 오버라이드 값)을 자동 생성한다. `http://localhost:18080/login` 에서 그 계정으로 바로 로그인된다.

**수동 설치 시:**

1. `http://localhost:18080` 접속
2. 초기 비밀번호 확인:

```bash
docker exec -it e2e-jenkins cat /var/jenkins_home/secrets/initialAdminPassword
```

3. 출력된 값을 Unlock 화면에 입력
4. `Install suggested plugins` 선택 → 기본 플러그인 설치 완료 대기
5. 관리자 계정 생성

### 8.2 추가 플러그인 설치

> **✅ setup.sh 로 설치했다면 건너뛰어라.** Phase 5-1 에서 아래 4개가 Jenkins REST API (`/pluginManager/installNecessaryPlugins`) 로 일괄 설치된다.

필요한 플러그인:

| 플러그인 | 용도 | 필수 여부 |
| --- | --- | --- |
| `workflow-aggregator` | Pipeline (Jenkinsfile) 지원 | 필수 |
| `file-parameters` | `base64File` 파라미터 지원 (Doc/Convert 모드 파일 업로드) | 필수 |
| `htmlpublisher` | HTML 리포트 Jenkins UI 게시 | 필수 |
| `plain-credentials` | `StringCredentialsImpl` (Dify API Key) 구현체 제공 | 필수 |

**수동 설치:**
`Jenkins 관리` → `Plugins` → `Available plugins` 탭에서 위 4개를 각각 검색해서 체크 → `Install` 클릭 → 설치 완료 후 Jenkins 재시작:

```bash
docker restart e2e-jenkins
```

### 8.3 Jenkins Credentials 등록

> **✅ setup.sh 가 API Key 까지 뽑아냈다면 이미 완료.** Phase 5-2 에서 `dify-qa-api-token` 이라는 id 로 자동 등록된다. 확인 방법:
> `Jenkins 관리` → `Credentials` → `System` → `Global credentials` → 목록에 `dify-qa-api-token` 이 보이면 OK.
>
> **⚠️ setup.sh 가 `Credentials 등록 실패` 를 경고했다면** (또는 API Key 가 없으면) 아래 수동 절차 필요.

#### UI 로 등록하기 (가장 쉬운 방법)

1. `Jenkins 관리(Manage Jenkins)` → `Credentials(자격 증명)` 클릭
2. 화면 중앙의 `Stores scoped to Jenkins` 아래 **System** 행 클릭
3. **Global credentials (unrestricted)** 행 클릭
4. 좌측 사이드바의 `+ Add Credentials` 클릭
5. 아래 값을 정확히 입력:

| 항목 | 값 |
| --- | --- |
| **Kind** | `Secret text` |
| **Scope** | `Global (Jenkins, nodes, items, all child items, etc)` |
| **Secret** | §7.7 에서 복사한 Dify API Key (`app-xxxxxxxxxxxxxxxxxxxx`) |
| **ID** | `dify-qa-api-token` ⚠️ 바이트 단위로 정확히 일치해야 한다 |
| **Description** | (자유) 예: `Dify QA Chatflow API Key` |

6. `Create` 클릭 → 목록에 등록되면 성공.

> **⚠️ ID 는 바이트 단위로 정확히 일치해야 한다.**
> Jenkinsfile 이 `credentials('dify-qa-api-token')` 로 참조한다. 대소문자, 하이픈 위치 하나라도 다르면 빌드 시점에 `Could not find credentials entry` 에러가 난다.

#### XML 로 등록하기 (스크립팅 환경용)

UI 접근이 어려운 환경에서는 아래 curl 명령으로 등록한다. **클래스 경로는 `org.jenkinsci.plugins.plaincredentials.impl.StringCredentialsImpl` 이다** — Jenkins 구 문서나 블로그에서 흔히 보이는 `com.cloudbees.plugins.credentials.impl.StringCredentialsImpl` 은 존재하지 않는 클래스라 반드시 500 에러가 난다.

```bash
DIFY_API_KEY="app-xxxxxxxxxxxxxxxxxxxx"       # §7.7 에서 발급받은 값으로 교체

CRED_XML="<org.jenkinsci.plugins.plaincredentials.impl.StringCredentialsImpl plugin=\"plain-credentials\">
  <scope>GLOBAL</scope>
  <id>dify-qa-api-token</id>
  <description>Dify QA Chatflow API Key</description>
  <secret>${DIFY_API_KEY}</secret>
</org.jenkinsci.plugins.plaincredentials.impl.StringCredentialsImpl>"

curl -sS -X POST \
  "http://localhost:18080/credentials/store/system/domain/_/createCredentials" \
  -u "admin:Admin1234!" \
  -H "Content-Type: application/xml" \
  --data "$CRED_XML"
```

**성공 판정:** HTTP 200 또는 302 응답. 에러가 나면:
- `ClassNotFoundException` → `plain-credentials` 플러그인이 설치되지 않음 (§8.2 부터 다시)
- `401 Unauthorized` → `-u admin:비밀번호` 값 확인
- `403 Forbidden` → CSRF crumb 필요 (setup.sh 가 `setCrumbIssuer(null)` 로 끄지만 수동 설치 환경에선 crumb 를 먼저 받아야 할 수 있음)

### 8.4 HTML 리포트 CSP 설정

Jenkins 기본 보안 정책(CSP)이 JavaScript를 차단하여 `publishHTML`로 게시한 리포트가 빈 화면으로 표시된다.

**자동 설치(setup.sh) 사용 시:** `docker-compose.override.yaml`의 `JAVA_OPTS`에 이미 영구 적용되어 있으므로 이 단계를 건너뜐다.

**수동 설치 시:** `docker-compose.yaml`과 같은 폴더에 아래 내용으로 `docker-compose.override.yaml`을 생성한다:

```yaml
services:
  jenkins:
    environment:
      JAVA_OPTS: "-Dhudson.model.DirectoryBrowserSupport.CSP="
```

생성 후 Jenkins 재기동:

```bash
docker compose up -d jenkins
```

> **주의:** Script Console에서 `System.setProperty(...)` 방식은 **재시작 시 초기화**된다.  
> `JAVA_OPTS` 방식이 영구 적용되는 올바른 방법이다.

### 8.5 Pipeline Job 생성

1. Jenkins 메인 → `새로운 Item`
2. 이름: `DSCORE-ZeroTouch-QA-Docker`
3. 유형: **Pipeline** 선택 → `OK`
4. Pipeline 섹션에서 Definition: `Pipeline script` 선택
5. `DSCORE-ZeroTouch-QA-Docker.jenkinsPipeline` 파일 내용을 그대로 붙여넣기
6. `저장` 클릭

> **파일 내용 확인 방법 (Windows):**  
> 탐색기에서 `e2e-pipeline` 폴더 안의 `DSCORE-ZeroTouch-QA-Docker.jenkinsPipeline` 파일을 메모장으로 열어 전체 복사한다.

> **경로 수정은 불필요하다.** `AGENT_HOME`은 Jenkins workspace 기반으로 자동 결정되고,  
> `SCRIPTS_HOME`은 §8.6 Step 2에서 노드 환경변수로 한 번만 설정한다.

### 8.6 mac-ui-tester 에이전트 등록

Jenkinsfile의 `agent { label 'mac-ui-tester' }`를 실행할 Jenkins 에이전트를 등록한다.
에이전트는 Playwright와 Python이 설치된 머신(Mac, Windows, Linux, WSL2 중 어디든)이다.
Docker 스택과 같은 호스트여도 되고 다른 머신이어도 된다.

> **✅ setup.sh 로 설치했다면 Step 1 (노드 등록) 은 자동 완료** — Phase 5-5 가 이미 끝냈다.
> 하지만 **Step 2 (agent.jar 다운로드 + 실행)** 은 setup.sh 가 도달할 수 없는 에이전트 머신 쪽 작업이라 반드시 수동.

---

#### 사전 조건: JDK 11 이상 설치

agent.jar 는 Java 11 이상에서 동작한다. 에이전트 머신에 Java 가 없거나 버전이 낮으면 먼저 설치한다.

**설치 여부 확인:**
```bash
java -version
# 출력 예: openjdk version "17.0.9" 2023-10-17   ← 11 이상이면 OK
# "command not found" 또는 11 미만이면 아래 설치
```

**OS 별 설치 명령:**

| OS | 설치 명령 | 비고 |
| --- | --- | --- |
| **Ubuntu / Debian / WSL2** | `sudo apt update && sudo apt install -y openjdk-17-jre-headless` | apt 패키지 매니저 기본 |
| **Fedora / RHEL / Rocky** | `sudo dnf install -y java-17-openjdk-headless` | |
| **macOS** (Homebrew) | `brew install openjdk@17` → `sudo ln -sfn $(brew --prefix)/opt/openjdk@17/libexec/openjdk.jdk /Library/Java/JavaVirtualMachines/openjdk-17.jdk` | 심볼릭 링크는 시스템이 java 를 인식하게 하기 위함 |
| **Windows** (winget) | `winget install --id EclipseAdoptium.Temurin.17.JDK` | Temurin = Eclipse Adoptium 의 빌드, 가장 호환성 좋음 |
| **Windows** (수동) | [https://adoptium.net](https://adoptium.net) → Temurin 17 LTS 다운로드 → 설치 관리자 실행 | PATH 자동 등록 옵션 체크 |

**설치 후 다시 확인:**
```bash
java -version
```
`11.x` 이상 버전이 출력되면 OK. Windows 에서는 새 PowerShell/cmd 창을 열어야 PATH 가 갱신된다.

> **Java 17 을 권장**하는 이유: Jenkins LTS 2.426+ 부터 권장 런타임이 Java 17 이다. agent.jar 는 11~21 모두 호환되지만 17 이 가장 안정적.

---

#### Step 1: Jenkins 에서 에이전트 노드 등록

> **✅ setup.sh 가 이 Step 을 자동 완료한다.** 수동 설치 시에만 아래를 따른다.

1. `Jenkins 관리` → `노드 관리(Manage Nodes)` → `New Node`
2. 이름: `mac-ui-tester`, 유형: `Permanent Agent` → `OK`
3. 아래 설정 입력:

| 항목 | 값 |
| --- | --- |
| Remote root directory | 에이전트 머신의 작업 디렉터리 (예: `/home/{사용자명}/jenkins-agent`). **Jenkins UI 표시용 메타데이터일 뿐** — 실제 동작은 Step 2 의 `-workDir` 플래그가 결정한다. |
| Labels | `mac-ui-tester` |
| Launch method | `Launch agent by connecting it to the controller` |

4. **Node Properties** → `환경변수(Environment variables)` 체크 → `추가`:

| 이름 | 값 |
| --- | --- |
| `SCRIPTS_HOME` | `e2e-pipeline/` 폴더의 절대 경로 (예: `/home/{사용자명}/e2e-pipeline`) |

> `SCRIPTS_HOME`은 이 한 번 설정으로 끝난다. Jenkinsfile을 수정할 필요가 없다.

5. `저장` 클릭

---

#### Step 2: agent.jar 다운로드 + 실행 (반드시 수동)

에이전트 머신 위에서 (**setup.sh 를 돌린 머신과 달라도 된다**):

```bash
# 1) agent.jar 다운로드 (Jenkins 컨트롤러에서 직접)
curl -O http://localhost:18080/jnlpJars/agent.jar

# 2) 에이전트 실행
java -jar agent.jar \
  -url "http://localhost:18080/" \
  -secret "<여기에_노드_시크릿>" \
  -name "mac-ui-tester" \
  -webSocket \
  -workDir "$HOME/jenkins-agent"
```

**시크릿 값 찾는 방법:**
- setup.sh 를 쓴 경우: 종료 배너에 `-secret "..."` 한 줄이 출력된다. 그 값 복사.
- 수동 설치: Jenkins UI → `노드 관리` → `mac-ui-tester` 클릭 → 상세 페이지에 `java -jar agent.jar -url ... -secret XXX -name mac-ui-tester` 가 표시됨. `XXX` 부분.

> **⚠️ `-workDir` 은 현재 사용자가 쓰기 권한을 가진 경로여야 한다.**
>
> **`$HOME/jenkins-agent`** 를 권장 — bash 가 현재 사용자의 홈(`/home/alice/` 또는 `/Users/alice/`) 으로 자동 확장하므로 권한 문제가 없다. 디렉터리가 없으면 agent.jar 가 알아서 만든다.
>
> **피해야 할 경로:**
> - `/home/jenkins-agent` — root 소유의 최상위 경로, 일반 사용자가 못 만듦 → `AccessDeniedException`
> - `/var/lib/jenkins` — 권한 제한
> - Windows 에서 `C:\jenkins-agent` — Administrator 권한 필요할 수 있음
>
> Windows 에서는 Git Bash / PowerShell 에서 `"$HOME/jenkins-agent"` 대신 `"%USERPROFILE%\jenkins-agent"` (cmd) / `"$env:USERPROFILE\jenkins-agent"` (PowerShell) 사용.

> **`-webSocket` 플래그:** Jenkins 2.293+ 부터 권장되는 에이전트 연결 방식. 기존 TCP 포트(50000) 가 막힌 네트워크에서도 80/443 HTTP(S) 만으로 연결 가능. **이 플래그는 유지 권장**.

**컨트롤러가 다른 머신인 경우**: `http://localhost:18080/` 대신 컨트롤러의 접근 가능한 주소로 교체. 예: `http://192.168.1.100:18080/` 또는 `http://ci.mycompany.internal:18080/`.

**완료 확인:** 명령 실행 후 터미널에 아래 로그가 나오면 연결 성공이다:
```
INFO: Connected
```

Jenkins UI → `노드 관리` → `mac-ui-tester` 상태가 **`Connected`** 로 바뀌면 완료.

**이 명령을 종료하지 말 것** — `Ctrl+C` 로 끄면 에이전트가 오프라인 된다. 백그라운드로 유지하려면:

```bash
# Linux/macOS — nohup 로 백그라운드 실행
nohup java -jar agent.jar \
  -url "http://localhost:18080/" \
  -secret "<시크릿>" \
  -name "mac-ui-tester" \
  -webSocket \
  -workDir "$HOME/jenkins-agent" \
  > "$HOME/jenkins-agent/agent.log" 2>&1 &

# 또는 systemd 서비스로 등록 (Linux, 재부팅 후 자동 시작)
# /etc/systemd/system/jenkins-agent.service 작성 후 systemctl enable --now
```

---

#### Step 3: 에이전트 Python / Playwright 환경 확인

에이전트가 빌드 시 Playwright 를 실행하므로 Python 3.9+ 와 chromium 이 설치되어 있어야 한다.

```bash
python3 --version                    # 3.9 이상
python3 -m playwright --version       # 설치 여부 확인

# 미설치 시
pip3 install playwright
playwright install chromium

# Linux 에이전트라면 chromium 시스템 라이브러리도 필요할 수 있다
playwright install-deps chromium      # sudo 권한 필요
```

---

## 9. E2E 테스트 실행

### 9.1 파라미터 안내

| 파라미터 | 설명 |
| --- | --- |
| `RUN_MODE` | `chat`: 자연어 입력 / `doc`: 기획서 업로드 / `convert`: 녹화 변환 / `execute`: 기존 시나리오 재실행 |
| `TARGET_URL` | 테스트 대상 외부 URL (예: `https://www.naver.com`) |
| `SRS_TEXT` | [Chat] 자연어 요구사항 |
| `DOC_FILE` | [Doc] 기획서 PDF / [Convert] Playwright `.py` 파일 / [Execute] `scenario.json` |
| `HEADLESS` | 체크 해제(기본): 실제 브라우저 창 표시(headed) / 체크: 백그라운드 headless 실행 |

### 9.2 Flow 1: Chat 모드 (자연어 입력)

1. Jenkins → `DSCORE-ZeroTouch-QA-Docker` → `Build with Parameters`
2. 파라미터 입력:

| 파라미터 | 예시값 |
| --- | --- |
| `RUN_MODE` | `chat` |
| `TARGET_URL` | `https://www.naver.com` |
| `SRS_TEXT` | `네이버 검색창에 DSCORE 입력 후 엔터를 누른다` |

3. `빌드` 클릭

**SRS_TEXT 작성 팁:**

```
# 좋은 예 (구체적, 단계별)
네이버 메인 페이지에서 검색창에 'DSCORE'를 입력하고 엔터를 누른다.
검색 결과 페이지에서 첫 번째 링크를 클릭한다.

# 나쁜 예 (모호함)
네이버에서 검색해줘
```

### 9.3 Flow 2: Doc 모드 (기획서 업로드)

1. `RUN_MODE`: `doc`
2. `TARGET_URL`: 테스트 대상 URL
3. `DOC_FILE`: 기획서 파일 업로드 (PDF 또는 DOCX)

시스템 흐름: 파일 업로드 → Dify Parser TC 추출 → Planner LLM DSL 변환 → Playwright 자동 실행

### 9.4 Flow 3: Convert 모드 (Playwright 녹화 변환)

#### Step 1: 로컬에서 Playwright codegen으로 녹화

로컬 환경에 Playwright가 없다면 먼저 설치한다:

```bash
pip install playwright
playwright install chromium
```

녹화 실행:

```bash
playwright codegen https://target-app.com --output recorded.py
```

브라우저가 열리면 평소처럼 조작 → 브라우저 창 닫으면 `recorded.py` 저장

#### Step 2: Jenkins에서 변환 + 실행

1. `RUN_MODE`: `convert`
2. `DOC_FILE`: `recorded.py` 파일 업로드
3. `빌드` 클릭

변환과 실행이 **단일 호출**로 체이닝된다. 별도 execute 명령 불필요.

### 9.5 Execute 모드 (기존 시나리오 재실행)

이전 빌드의 `scenario.json` 또는 `scenario.healed.json`을 재사용한다.

1. `RUN_MODE`: `execute`
2. `DOC_FILE`: `scenario.json` 파일 업로드
3. `빌드` 클릭

---

## 10. 산출물 확인

빌드 완료 후 Jenkins에서 아래 경로로 확인한다:

### 10.1 HTML 리포트

좌측 메뉴 `Zero-Touch QA Report` 링크 클릭 → 스텝별 상태 배지 + 스크린샷 시각적 확인

### 10.2 산출물 파일 목록

`Artifacts` 섹션에서 전체 파일 다운로드 가능:

| 파일 | 설명 |
| --- | --- |
| `scenario.json` | Dify가 생성한 원본 DSL 시나리오 (재현 및 감사용) |
| `scenario.healed.json` | Self-Healing이 반영된 최종 시나리오 (다음 실행 캐시 재사용 가능) |
| `run_log.jsonl` | 스텝별 실행 결과(status, heal_stage, timestamp) 시계열 로그 |
| `index.html` | 시각적 HTML 리포트 |
| `regression_test.py` | 전체 성공 시 자동 생성 — LLM 없이 독립 실행 가능한 Playwright 회귀 테스트 |
| `step_N_pass.png` | 스텝 성공 증적 스크린샷 |
| `step_N_healed.png` | 자가 치유 후 성공 스크린샷 |
| `error_final.png` | 최종 실패 시 에러 스크린샷 |

### 10.3 run_log.jsonl 레코드 형식

```json
{"step": 1, "action": "navigate", "target": "https://example.com", "status": "PASS", "heal_stage": "none", "ts": 1711700000.0}
{"step": 2, "action": "click", "target": "role=button, name=로그인", "status": "HEALED", "heal_stage": "local", "ts": 1711700003.5}
{"step": 3, "action": "fill", "target": "label=이메일", "status": "FAIL", "heal_stage": "none", "ts": 1711700010.2}
```

`heal_stage` 값: `none` (치유 불필요) / `fallback` / `local` / `dify`

---

## 11. 트러블슈팅

### 502 Bad Gateway (Dify 콘솔 접속 시 간헐적 발생)

**증상:** 브라우저에서 `http://localhost:18081/` 또는 `/console/api/*` 경로 접근 시 502.
nginx 로그에 `connect() failed (111: Connection refused) while connecting to upstream, upstream: "http://<IP>:5001/..."` 형태 에러.

**가능한 원인 2가지** — 구분이 중요하다.

**원인 A: api 가 부팅 중 (gunicorn 아직 미리슨)**
- `docker compose up` 직후 또는 `docker compose restart api` 직후 1~3분간 나타나는 전이 상태
- 해결: 대기. `docker logs e2e-dify-api | grep -E "Database migration|Listening"` 로 준비 여부 확인
- `Database migration successful!` + `Listening at: http://0.0.0.0:5001` 두 줄이 나오면 준비 완료

**원인 B: nginx 의 upstream DNS 캐시 stale (구 compose 의 잠재 버그, 현재 compose 에선 해결됨)**
- 과거 `upstream` 블록 방식은 **config load 시 1회만** DNS 해석 → api 컨테이너가 재생성되어 IP 바뀌면 nginx 는 stale IP 고집 → 영구 502
- 현재 `nginx/dify.conf` 는 `resolver 127.0.0.11 valid=10s;` + `proxy_pass $var` 패턴으로 변경되어 **매 요청마다 DNS 재해석** → 재발 차단
- 만약 구 버전 nginx 설정을 사용 중이라면 `docker compose restart nginx` 로 임시 복구 후 [nginx/dify.conf](nginx/dify.conf) 를 최신 resolver 패턴으로 업데이트

**구분 방법**:
```bash
# nginx 가 보는 api IP vs 실제 api 컨테이너 IP 비교
docker exec e2e-dify-nginx getent hosts api
docker inspect e2e-dify-api --format '{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}'
# 둘이 다르면 원인 B, 같으면 원인 A (api 부팅 지연)
```

---

### mac-ui-tester 가 Dify 에 접근 불가

**증상:** 빌드 실행 시 `ConnectionRefusedError` 또는 `http://localhost:18081/v1` 타임아웃

**확인:**

```bash
# e2e 스택이 실행 중인지 확인
docker compose ps

# Dify API 응답 확인 (setup 엔드포인트가 정식 헬스 체크 대용)
curl -sS -o - -w "\nHTTP %{http_code}\n" http://localhost:18081/console/api/setup
# HTTP 200 이면 정상
```

> **주의:** `/v1/health` 엔드포인트는 Dify 에 **존재하지 않는다**. 과거 문서/스크립트에 해당 호출이 있으면 `/console/api/setup` 또는 `/console/api/features` 로 교체한다.

**가능한 원인:**
- `docker compose up` 직후 DB 마이그레이션이 아직 진행 중 (§4.3 참조, 1~3분 대기)
- `e2e-dify-api` 컨테이너가 비정상 종료 → `docker logs e2e-dify-api` 확인
- nginx stale upstream (위 502 섹션 참조)

### e2e 스택 네트워크 이름 확인

```bash
docker network ls | grep e2e
# 정상: e2e-net    bridge
# 비정상: e2e-pipeline_e2e-net    bridge
```

`e2e-pipeline_e2e-net` 이 표시되면 [docker-compose.yaml](docker-compose.yaml) 의 `networks` 블록에 `name: e2e-net` 이 누락된 상태다. 재기동:

```bash
docker compose down
docker compose up -d
```

### mac-ui-tester 에이전트가 오프라인 상태

**증상:** Jenkins 빌드 대기 중 `Waiting for next available executor` 또는 `Offline` 표시

**확인:** `Jenkins 관리` → `노드 관리` → `mac-ui-tester` 상태 확인

**해결:** 에이전트 Mac/Windows 에서 agent.jar 재실행 (§8.6 참조)

---

### Dify API 연결 실패

**증상:** Jenkins 빌드 로그에 `Dify 연결 실패` 또는 `Connection refused`

**확인:**

```bash
# mac-ui-tester 머신에서 Dify API 접근 테스트 (호스트 포트)
curl -s -o /dev/null -w "%{http_code}" http://localhost:18081/console/api/setup
# 200 이면 정상. 502/000 이면 §11 의 502 섹션 참조
```

**해결:**

1. `docker compose ps` 로 `e2e-dify-api` 상태 확인
2. `e2e-dify-db` / `e2e-dify-redis` 가 `healthy` 인지 확인 (api depends_on 대상)
3. 기동 직후라면 DB 마이그레이션 완료까지 1~3분 대기 (§4.3 참조)
4. 재기동 필요 시: `docker compose restart api worker worker_beat plugin_daemon`
   > **주의:** api 만 단독 재기동 후 브라우저 접속 시 1~2분 간 502 가 보일 수 있다 (api 부팅 + plugin_daemon 재연결 시간). 부팅 후엔 자동 복구된다.

### Ollama 모델 미설치

**증상:** Dify Chatflow 테스트 시 `model not found` 오류

**확인/해결 — 프로파일별로 다름**:

**호스트 Ollama 모드 (기본)** — 호스트 셸에서 직접:
```bash
ollama list
ollama pull qwen3.5:4b
```

**컨테이너 Ollama 모드** (`--profile container-ollama` 로 기동한 경우):
```bash
docker exec -it e2e-ollama ollama list
docker exec -it e2e-ollama ollama pull qwen3.5:4b
```

> dify-chatflow.yaml 은 `qwen3.5:4b` 로 하드코딩되어 있으므로 Dify 모델 공급자 등록 이름과 Ollama 태그 이름이 모두 정확히 `qwen3.5:4b` 여야 한다.

### Dify 에서 호스트 Ollama 에 도달 불가 (호스트 모드)

**증상:** Dify 콘솔에서 Ollama 모델 공급자 Base URL `http://host.docker.internal:11434` 로 설정 후 테스트 시 `Connection refused` / `model list unavailable`

**원인:** 호스트 Ollama 가 기본값(`127.0.0.1`)에만 바인딩되어 컨테이너에서 못 닿는다.

**확인 (컨테이너 안에서 호스트 Ollama 도달성 체크):**
```bash
docker exec e2e-dify-api curl -sS -o - -w "\nHTTP %{http_code}\n" http://host.docker.internal:11434/api/tags
# HTTP 200 + 모델 리스트 JSON 이면 정상
# HTTP 000 또는 Connection refused 면 호스트 Ollama 가 0.0.0.0 미바인딩
```

**해결:**
- **Windows**: 시스템 환경변수 `OLLAMA_HOST=0.0.0.0` 등록 → 작업 관리자에서 `ollama.exe` 종료 → 다시 실행
- **Linux/macOS**: `OLLAMA_HOST=0.0.0.0 ollama serve` 로 재기동
- 방화벽: Windows 방화벽이 11434 포트 vEthernet 인바운드를 허용해야 함

### Playwright 브라우저 실행 실패

**증상:** mac-ui-tester 빌드 로그에 `Browser not found` 또는 `playwright install` 관련 오류

**해결:**

```bash
# mac-ui-tester 머신에서 직접 실행
playwright install chromium

# chromium 실행 의존성 설치 (Linux 에이전트인 경우)
playwright install-deps chromium
```

### Jenkins 이미지 빌드 실패

**증상:** `docker compose up --build` 시 `Dockerfile.jenkins` 관련 오류

**해결:**

```bash
# 빌드 캐시 무시하고 재빌드
docker compose build --no-cache jenkins
```

### Dify 콘솔 접속 불가 (18081 포트)

**확인 순서 — 레이어별**:

```bash
# 1. nginx 컨테이너 로그
docker logs e2e-dify-nginx 2>&1 | tail -20

# 2. web (Next.js) 컨테이너 로그
docker logs e2e-dify-web 2>&1 | tail -20

# 3. api 컨테이너 로그 (502 원인이 api 쪽일 수 있음)
docker logs e2e-dify-api 2>&1 | tail -20

# 4. 전체 경로 테스트 (호스트 → nginx → upstream)
curl -sS -o - -w "\nHTTP %{http_code}\n" http://localhost:18081/install       # web 경로
curl -sS -o - -w "\nHTTP %{http_code}\n" http://localhost:18081/console/api/setup  # api 경로
```

**해결**:
- `502 Bad Gateway` 발생 → 위 "502 Bad Gateway" 섹션 참조
- `Connection refused` → 스택이 기동 중이거나 `docker compose up` 필요
- 웹 경로는 200 인데 api 경로는 502 → api 서비스만 부팅 중이거나 재시작 필요
- `e2e-dify-web` / `e2e-dify-api` 둘 다 완전 기동까지 1~3분 대기 후 재시도

### Dify 로그인 `Invalid encrypted data` 401

**증상:** `/console/api/login` 호출 시 HTTP 401 + body:
```json
{"code":"authentication_failed","message":"Invalid encrypted data","status":401}
```

**원인:** 이름은 `"encrypted"` 지만 실제는 **단순 base64 인코딩**이다. Dify 1.13.3 의 `/console/api/login` 은 `@decrypt_password_field` 데코레이터가 걸려 있고, 그 내부(`api/libs/encryption.py`)는 `base64.b64decode(password)` 만 수행한다. docstring 원문:
> "This uses Base64 encoding for obfuscation, not cryptographic encryption. Real security relies on HTTPS."

plaintext `Admin1234!` 을 그대로 보내면 `!` 가 base64 알파벳 밖 문자라 디코드 예외 → None → "Invalid encrypted data" 401.

**핵심 차이:**
- `/console/api/setup` — `@decrypt_password_field` **없음** → plaintext 그대로 OK
- `/console/api/login` — 데코레이터 **있음** → password 를 base64 로 감싸서 보내야 함

**해결:**

최신 setup.sh 는 Phase 4-2 에서 password 를 base64 로 인코딩한 뒤 전송한다. 수동으로 재현 시:

```bash
EMAIL='admin@example.com'
PASSWORD='Admin1234!'
ENC_PW=$(printf '%s' "$PASSWORD" | base64)   # 결과: QWRtaW4xMjM0IQ==

curl -sS -c /tmp/dify-cookies.txt \
    -X POST "http://localhost:18081/console/api/login" \
    -H "Content-Type: application/json" \
    -d "{\"email\":\"$EMAIL\",\"password\":\"$ENC_PW\",\"remember_me\":true}"
# 기대: {"result":"success"}

grep access_token /tmp/dify-cookies.txt
# 기대: 한 줄 이상 (HttpOnly access_token 쿠키 저장됨)
```

base64 로 감싼 뒤에도 401 이 뜨면 email/password 불일치다. 계정이 이미 다른 비밀번호로 생성됐을 수 있다 (volume 이 persist 중이면 `docker compose down -v` 로 초기화 후 재시도).

---

### Dify console API `CSRF token is missing or invalid` 401

**증상:** 로그인은 성공했고 cookies 도 저장됐는데 후속 호출 (`/console/api/apps/imports`, `workflows/publish`, `api-keys` 등) 에서 401 + body:
```json
{"code":"unauthorized","message":"CSRF token is missing or invalid.","status":401}
```

**원인:** Dify 1.13.3 은 자체 구현 CSRF 보호를 켜고 있다 (Flask-WTF 같은 라이브러리 아님, `api/libs/token.py` 의 자체 로직). `@login_required` 가 붙은 모든 console 엔드포인트는 자동으로 CSRF 검사 대상이고 환경변수로 끄는 옵션이 없다.

작동 방식:
1. `POST /console/api/login` 응답이 `Set-Cookie: csrf_token=<JWT>` 쿠키를 내려준다 (HttpOnly=False).
2. 후속 호출은 **반드시** `X-CSRF-Token: <쿠키와 동일한 JWT>` 헤더를 포함해야 한다.
3. 서버는 (a) 헤더값 (b) 쿠키값 (c) JWT 서명/만료 (d) JWT subject == 현재 user.id 4가지를 모두 검증한다.

쿠키 이름은 `csrf_token` (HTTPS + 빈 `COOKIE_DOMAIN` 환경에서는 `__Host-csrf_token` prefix).

**해결:**

최신 setup.sh 는 Phase 4-2 에서 cookies.txt 의 6번째 컬럼이 `csrf_token` 또는 `__Host-csrf_token` 인 라인의 7번째 컬럼(= JWT 값)을 추출해 `DIFY_CSRF_TOKEN` 변수에 저장하고, Phase 4-4/4-5/4-6 의 모든 curl 에 `-H "X-CSRF-Token: ${DIFY_CSRF_TOKEN}"` 를 추가한다.

**수동 재현:**

```bash
# 1) 로그인 → 쿠키 jar 저장
curl -sS -c /tmp/dify-cookies.txt \
    -X POST "http://localhost:18081/console/api/login" \
    -H "Content-Type: application/json" \
    -d '{"email":"admin@example.com","password":"QWRtaW4xMjM0IQ==","remember_me":true}'

# 2) cookies.txt 에서 csrf_token 값 추출 (6번째 컬럼 == name, 7번째 == value)
DIFY_CSRF=$(awk '$6 == "csrf_token" || $6 == "__Host-csrf_token" { print $7; exit }' /tmp/dify-cookies.txt)
echo "CSRF: $DIFY_CSRF"

# 3) 후속 호출에 X-CSRF-Token 헤더 포함
curl -sS -b /tmp/dify-cookies.txt \
    -X POST "http://localhost:18081/console/api/apps/imports" \
    -H "Content-Type: application/json" \
    -H "X-CSRF-Token: $DIFY_CSRF" \
    -d '{"mode":"yaml-content","yaml_content":"..."}'
```

> **중요**: cookies.txt 가 Netscape format 일 때 행 형식은 `domain<TAB>flag<TAB>path<TAB>secure<TAB>expiration<TAB>name<TAB>value`. awk `$6` `$7` 로 정확히 추출 가능. curl 7.50+ 권장.

---

### setup.sh Phase 4-4 Chatflow import 422 / 응답에 app_id 없음

**증상:** setup.sh 로그에서
```
[⚠] Chatflow import 실패
   status : (없음)
   응답   : {"message":"...","code":"unprocessable_entity"}
```

**원인:** 과거 버전 스크립트가 `/apps/import` (단수) 경로에 `{"data":"..."}` body 를 보냄. Dify 1.13.3 의 실제 스펙은:
- 경로: **`/console/api/apps/imports`** (복수)
- body: **`{"mode":"yaml-content","yaml_content":"<raw yaml>"}`**
- 응답의 `id` 는 import-record id 이고 실제 app_id 는 **`app_id`** 필드
- `status:"pending"` 이면 `/apps/imports/{id}/confirm` 후속 호출 필요

**해결:**
- 최신 setup.sh 는 새 스펙으로 수정됨. 재실행.
- 수동으로 import 하려면 Dify 콘솔 → `+ 앱 만들기` → **DSL 파일에서 가져오기** UI 경로가 가장 안전 (API 버전 변화에 영향받지 않음).

---

### Jenkins `/createItem` 500 "A problem occurred while processing the request"

**증상:** Pipeline Job 생성 호출이 HTTP 500 + "Oops!" HTML 에러 페이지. 응답에 `workflow-cps` / `workflow-job` / `CpsFlowDefinition` 같은 클래스 이름이 포함되거나, 헤더에 "Sign in" 버튼이 보인다 (세션 context 없이 에러 페이지가 렌더링됐을 뿐 실제 auth 실패는 아님).

**원인:** `workflow-cps` (또는 `workflow-job`) 플러그인이 **아직 로드되지 않았다**. Jenkins `/pluginManager/installNecessaryPlugins` 엔드포인트는 **비동기**라서 요청 즉시 200 을 리턴하지만 실제 다운로드는 백그라운드에서 진행된다. `workflow-aggregator` 는 의존성 40+ 개를 끌어오는 메타플러그인이라 60초 sleep 으로는 부족할 수 있다. 이 상태에서 Jenkins 를 재시작하면 미완료 플러그인은 로드되지 않고, 해당 클래스를 참조하는 Job XML 을 POST 하면 XStream 역직렬화에서 500 이 난다.

**해결:**

최신 setup.sh 는 Phase 5-1 에서 `/pluginManager/installNecessaryPlugins` 대신 **Groovy `/scriptText` + `UpdateSite.Plugin.deploy(true).get()`** 동기 경로를 사용한다. `.get()` 이 다운로드가 끝날 때까지 블록하므로 async 경합이 사라진다.

또한 Phase 5-1 종료 후 `/pluginManager/api/json` 을 폴링해서 4개 필수 플러그인(`workflow-cps`, `plain-credentials`, `file-parameters`, `htmlpublisher`)이 `enabled=true && active=true` 인지 검증한다. 미로드 시 경고 출력.

**수동 복구:**

1. 현재 플러그인 상태 확인:
```bash
curl -sS -u admin:Admin1234! \
  "http://localhost:18080/pluginManager/api/json?depth=1&tree=plugins[shortName,enabled,active]" \
  | python3 -c "
import json,sys
d=json.load(sys.stdin)
for p in d['plugins']:
    if p['shortName'] in ('workflow-cps','workflow-job','plain-credentials','file-parameters','htmlpublisher'):
        print(f\"{p['shortName']:25s} enabled={p['enabled']} active={p['active']}\")
"
```
2. 누락된 플러그인이 있으면 `Jenkins 관리 → Plugins → Available plugins` 에서 설치 후 재시작
3. 재시작 후 `/createItem` 재호출

---

### setup.sh Phase 5-2 Credentials 등록 실패 — ClassNotFoundException

**증상:** `Credentials 등록 실패` 경고 + 응답에 `ClassNotFoundException` 또는 `com.cloudbees.plugins.credentials.impl.StringCredentialsImpl` 언급.

**원인:** `StringCredentialsImpl` 의 **실제 클래스 경로는 `org.jenkinsci.plugins.plaincredentials.impl.StringCredentialsImpl`** 이다 (`plain-credentials` 플러그인 소속). `com.cloudbees.plugins.credentials.impl` 패키지에는 해당 클래스가 없다 — 과거 버전 스크립트는 이 잘못된 경로로 매번 500 에러를 받아왔고, `-sf >/dev/null` 로 에러가 은폐됐었다.

**해결:**
- 최신 setup.sh 는 올바른 경로로 수정됨 + Phase 5-1 플러그인 목록에 `plain-credentials@latest` 명시 추가.
- 수동 등록은 §8.3 참조.
- 설치된 Jenkins 의 클래스 경로를 직접 확인하고 싶다면:
```bash
docker exec e2e-jenkins ls /var/jenkins_home/plugins/plain-credentials/
# plain-credentials 폴더가 있어야 한다
```

---

### LLM 응답이 JSON이 아닌 마크다운 형식

**증상:** `시나리오 파싱 실패. Dify 원본 응답: ```json ...` 로그

**해결:**

1. Dify Chatflow 캔버스에서 Planner LLM 노드 System Prompt의 `[엄수 사항]` 부분을 더 강조
2. 더 강력한 모델로 교체 (예: `qwen3-coder:30b`)
3. Dify 미리보기에서 직접 테스트하여 응답 형식 확인

---

## 부록: 9대 표준 DSL 레퍼런스

zero_touch_qa가 지원하는 액션 전체 목록이다.

| 액션 | target | value | 예시 |
| --- | --- | --- | --- |
| `navigate` | `""` (빈 문자열) | URL 문자열 | `{"action":"navigate","target":"","value":"https://example.com"}` |
| `click` | 요소 선택자 | `""` | `{"action":"click","target":"role=button, name=로그인"}` |
| `fill` | 요소 선택자 | 입력할 텍스트 | `{"action":"fill","target":"label=이메일","value":"test@test.com"}` |
| `press` | 대상 요소 | 키 이름 | `{"action":"press","target":"label=이메일","value":"Enter"}` |
| `select` | 드롭다운 요소 | 옵션 값 | `{"action":"select","target":"#country","value":"한국"}` |
| `check` | 체크박스 요소 | `"on"` / `"off"` | `{"action":"check","target":"label=약관 동의","value":"on"}` |
| `hover` | 호버할 요소 | `""` | `{"action":"hover","target":"text=메뉴"}` |
| `wait` | `""` (빈 문자열) | 대기 밀리초 | `{"action":"wait","target":"","value":"2000"}` |
| `verify` | 검증할 요소 | 기대 텍스트 | `{"action":"verify","target":"#result","value":"성공"}` |
