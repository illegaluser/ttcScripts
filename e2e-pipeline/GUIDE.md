# DSCORE Zero-Touch QA — E2E 자동화 파이프라인 구축 가이드

이 문서는 `e2e-pipeline/` 독립 스택을 처음부터 끝까지 구축하는 가이드다.  
Jenkins + Dify + Ollama(LLM) + Playwright가 모두 포함된 **완전 독립 환경**을 구성한다.  
기존 `docker-compose.yaml`(메인 DevOps 스택)과 **완전히 분리**되어 공존한다.

---

## 목차

### 빠른 시작 (setup.sh 로 자동 설치)

1. [**§0 소스 코드 준비**](#0-소스-코드-준비) — 어디 풀어넣어도 OK, bash 있으면 됨
2. [**§자동 설치 (setup.sh)**](#자동-설치-setupsh) — `.env` 설정 후 `./setup.sh`
3. [**§6.2 Dify Ollama 플러그인 수동 등록**](#62-ollama-플러그인-설치-및-모델-공급자-등록) — 5분 (자동화 불가)
4. [**§8.6 Jenkins 에이전트 연결**](#86-mac-ui-tester-에이전트-등록) — agent.jar 실행
5. [**§9 E2E 테스트 실행**](#9-e2e-테스트-실행) — 첫 빌드

### 전체 목차

0. [소스 코드 준비](#0-소스-코드-준비)
1. [시스템 구성 개요](#1-시스템-구성-개요)
2. [사전 요구사항](#2-사전-요구사항)
3. [디렉터리 구성](#3-디렉터리-구성)
- [**자동 설치 (setup.sh)**](#자동-설치-setupsh)
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

`e2e-pipeline/` 폴더 하나가 전체 스택의 **독립 실행 단위**다. 이 폴더 안의 모든 경로가 상대 참조로 설계돼 있어 **어느 위치에 풀어넣어도 동일하게 동작한다**. git clone 을 하든, zip 을 받아 풀든, 사본을 복사해 오든 무방.

### 0.1 어디에 풀어넣을 것인가

> ### ✅ 결론: **당신이 평소 쓰는 작업 폴더 어디든 상관없다.**
>
> `setup.sh` 가 자기 위치(`SCRIPT_DIR`) 를 자동으로 기준점으로 잡고, `docker-compose.yaml` 의 모든 bind mount 도 상대 경로(`./postgres-init`, `./nginx`, ...) 로 되어 있다. 절대 경로 가정이 전혀 없다.

실제 환경별로는 아래처럼 배치하면 편하다 — **이건 편의성 참고일 뿐 강제가 아니다**:

| 환경 | 편리한 위치 | 왜 |
|---|---|---|
| **Windows + WSL2 + Docker Desktop** | `C:\Users\<사용자>\Documents\ttcScripts\` (WSL 에서는 `/mnt/c/Users/<사용자>/Documents/ttcScripts/` 로 보임) | Windows IDE 편집 ↔ WSL 터미널이 **자동으로 같은 파일**을 본다. 별도 동기화 불필요. |
| **macOS / Linux 네이티브** | `~/projects/ttcScripts` 또는 `~/Developer/ttcScripts` | 홈 하위 표준 패턴, 성능 이슈 없음 |
| **Windows 순수 Git Bash** | `C:\Users\<사용자>\projects\ttcScripts` | WSL2 안 쓰고 Git Bash 만으로 돌리는 경우. 동작은 하지만 Windows 경로 변환 이슈가 간헐적으로 발생할 수 있음 |

기술적으로는 `/tmp/foo/e2e-pipeline` 이든 `/opt/ttcScripts` 든 `D:\work\e2e` 든 모두 동작한다.

### 0.2 두 가지 기술적 요구사항

"어디든 OK" 지만 두 가지 **절대 전제**가 있다.

#### ① bash 인터프리터가 필요하다

`setup.sh` 는 bash 스크립트다. 실행 가능한 환경:

| 환경 | bash 상태 | 비고 |
|---|---|---|
| Linux | ✅ 기본 포함 | |
| macOS | ✅ 기본 포함 (zsh 가 기본이지만 `/bin/bash` 존재) | |
| WSL2 Ubuntu / Debian / 기타 | ✅ 기본 포함 | |
| Windows + Git for Windows | ✅ Git Bash 형태로 포함 | https://git-scm.com/download/win 에서 설치 |
| Windows PowerShell / cmd 단독 | ❌ 불가 | bash 가 없음 — Git for Windows 를 설치하거나 WSL2 를 켜야 한다 |

#### ② Docker Desktop 또는 Docker Engine 이 필요하다

| 환경 | 권장 설치 |
|---|---|
| Windows | Docker Desktop for Windows (WSL2 backend 자동 구성) |
| macOS | Docker Desktop for Mac |
| Linux | Docker Engine + Compose v2 (`sudo apt install docker-ce docker-compose-plugin`) |

자세한 설치 요구사항은 [§2 사전 요구사항](#2-사전-요구사항) 참조.

### 0.3 ⛔ 단 한 가지 금지 경로: `/mnt/wsl/docker-desktop-bind-mounts/`

WSL2 터미널에서 Docker 관련 경로를 탐색하다 다음과 같은 경로에 실수로 들어가는 경우가 있다:

```
/mnt/wsl/docker-desktop-bind-mounts/Ubuntu/5929fcbdc8974a2c89946f4fecdf489dbe3613520a8779faf3323db559666bf9/
```

이 경로는 **Docker Desktop 이 내부적으로 WSL2 bind mount 를 노출하는 관리 공간**이다 (64자 hex 문자열은 Docker 의 content-addressable ID). **사용자 편집용이 아니다** — Docker 가 언제든 정리·재구성할 수 있고, 볼륨 경합으로 파일이 유실될 수 있다.

여기서 `./setup.sh` 를 실행하면 Phase 0 가 이 경로 패턴을 감지해 **즉시 중단**시킨다. 방어장치이므로 에러 메시지가 나오면 당황하지 말고 안내대로 정상 경로로 옮긴다.

### 0.4 폴더 준비 예시

#### git clone 으로 받는 경우

```bash
# 원하는 위치로 이동 (예시 — 본인 환경에 맞게)
cd ~/projects               # macOS/Linux
# 또는
cd /mnt/c/Users/alice/Documents    # Windows+WSL2
# 또는
cd /c/Users/alice/projects         # Windows Git Bash

git clone https://github.com/illegaluser/ttcScripts.git
cd ttcScripts
git checkout feat/standalone-e2e-pipeline   # 또는 main
cd e2e-pipeline
```

#### zip / 압축 파일로 받는 경우

```bash
# 1. 파일 탐색기로 원하는 위치에 압축 풀기 — 위치 자유
# 2. 터미널에서 해당 폴더의 e2e-pipeline/ 로 이동
cd <압축_푼_경로>/e2e-pipeline
```

### 0.5 첫 실행 확인

어떤 방식으로 준비했든 최종 확인은 동일:

```bash
pwd
# <어느 경로든>/e2e-pipeline   가 나오면 OK

ls
# setup.sh  docker-compose.yaml  .env.example  GUIDE.md  ...  가 보이면 OK

bash --version     # 3.2 이상 아무 버전
docker --version   # 20.10 이상 권장
```

준비 끝. 이제 [§자동 설치 (setup.sh)](#자동-설치-setupsh) 로 넘어간다.

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

> **어떤 셸에서 돌려야 하나?**
>
> `setup.sh` 는 bash 스크립트라 **bash 가 있는 환경**이면 어디든 OK. 구체적으로:
>
> - **Linux / macOS**: 아무 터미널
> - **Windows + WSL2** (권장): `Ubuntu` 셸 — Docker Desktop 의 WSL2 backend 와 가장 깔끔
> - **Windows + Git Bash**: Git for Windows 의 Git Bash 터미널 — 동작은 하지만 경로 변환 이슈가 간헐적
> - **Windows PowerShell / cmd**: ❌ 불가 (bash 없음)
>
> Windows + WSL2 사용자는 `Docker Desktop → Settings → Resources → WSL Integration → 사용 중인 distro 체크` 가 켜져 있는지 한번 확인.

#### 권장: `.env` 파일에 계정 정보 미리 지정

setup.sh 는 같은 폴더에 **`.env` 파일이 있으면 자동으로 읽어들인다**. 이 방식의 장점:

- 비밀번호가 shell history 에 남지 않음
- 매 실행마다 길게 환경변수를 입력하지 않아도 됨
- 어떤 변수가 있는지 한 파일에서 한눈에 보임
- `.gitignore` 에 등록되어 git 에 안 올라감 (실수로 비밀번호가 커밋되지 않음)

**Step 1.** 템플릿 복사 후 편집:

```bash
cd <저장소_경로>/e2e-pipeline   # 예: /mnt/c/Users/alice/Documents/ttcScripts/e2e-pipeline
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
| 5-4 | `DSCORE-ZeroTouch-QA-Docker` Pipeline Job 생성 (이미 존재하면 스크립트 업데이트) | ✅ 자동 |
| 5-5 | `mac-ui-tester` 노드 등록 (`SCRIPTS_HOME` 포함, 이미 존재 시 값 업데이트) | ✅ 자동 |
| 6-1 | Java 17 설치 확인/자동 설치 (apt/brew/winget) | ✅ 자동 |
| 6-2 | `python3-venv` 패키지 설치 (Ubuntu/Debian) | ✅ 자동 |
| 6-3 | `agent.jar` 다운로드 (`$HOME/jenkins-agent/`) | ✅ 자동 |
| 6-4 | Pipeline venv 사전 생성 + 패키지/Chromium 설치 (폐쇄망 대응) | ✅ 자동 |
| — | `agent.jar` 실행 (에이전트 연결) | ⚠️ 수동 (포그라운드 프로세스) |

### ⚠️ setup.sh 가 끝난 뒤 반드시 해야 할 수동 작업

> setup.sh 가 `[✓] DSCORE Zero-Touch QA 스택 설치 완료` 를 찍었다고 모든 게 끝난 게 아니다.
> Phase 6 에서 Java, Playwright, agent.jar 등 에이전트 사전 요구사항은 **자동 설치**되지만,
> **아래 작업은 여전히 수동으로 필요**하다 — 자동화가 기술적으로 불가능한 영역이기 때문이다 (Dify 마켓플레이스 UI 강제, agent.jar 는 포그라운드 프로세스).
>
> **이 작업들을 하지 않으면 빌드가 절대 동작하지 않는다.** 빠뜨리면 `model not found` / `agent offline` 으로 막힌다.

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

> **💡 max_tokens 확인 (추론 모델 사용 시 필수):**
> Qwen, DeepSeek 같은 추론(thinking) 모델은 `<think>` 블록에 토큰을 대량 소비하므로, Chatflow의 LLM 노드 `max_tokens` 가 충분해야 한다.
> `dify-chatflow.yaml` 에 `max_tokens: 4096` 이 이미 설정되어 있다. DSL 재import 없이 UI 에서 수정했다면 값이 다를 수 있으므로 확인:
> - Chatflow 편집 → **Planner** / **Healer** LLM 노드 → 모델 파라미터 → Max Tokens ≥ **4096**
> - 값이 작으면(512, 1024 등) JSON 응답이 잘려 `시나리오 파싱 실패` 에러가 발생한다.

#### ✅ 작업 3 — Jenkins 에이전트 연결 (agent.jar 실행, 필수)

**왜?** setup.sh Phase 6 에서 Java, Playwright, agent.jar 를 자동 설치했지만, `agent.jar` 자체는 **포그라운드 프로세스**로 실행해야 하므로 수동으로 시작해야 한다.

> Phase 6 에서 다음 항목이 자동 설치된다:
> - JDK 17, python3-venv, Playwright + Chromium, agent.jar 다운로드
>
> 설치에 실패한 항목이 있으면 setup.sh 로그에 `[⚠]` 경고가 표시된다. 해당 항목만 수동으로 설치하면 된다.

**실행 명령:**

setup.sh 종료 배너의 아래 블록을 그대로 복사해 실행한다 (시크릿은 매 setup.sh 실행마다 새로 생성되므로 setup.sh 출력에서 복사).

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
> - Jenkins UI 에 `Remote root directory` 로 표시된 경로는 **Jenkins 가 에이전트 위에 workspace 를 만드는 실제 기준 경로**다. 반드시 에이전트 사용자가 쓸 수 있는 경로여야 한다 (예: `$HOME/jenkins-agent`).

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
cd <저장소_경로>/e2e-pipeline   # 예: /mnt/c/Users/alice/Documents/ttcScripts/e2e-pipeline
docker compose up -d --build
```

**컨테이너 Ollama 모드**:
```bash
cd <저장소_경로>/e2e-pipeline   # 예: /mnt/c/Users/alice/Documents/ttcScripts/e2e-pipeline
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

`setup.sh` 가 Pipeline Job 을 자동 생성한다. **이미 존재하는 경우에도 최신 Jenkinsfile 로 스크립트를 덮어쓴다** (config.xml 업데이트). 따라서 Jenkinsfile 을 수정한 뒤 `./setup.sh` 를 재실행하면 Jenkins Job 에 즉시 반영된다.

수동으로 생성하려면:

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

Jenkins 파이프라인은 `agent { label 'mac-ui-tester' }` 로 표시된 머신 위에서 실제 Playwright 브라우저 테스트를 실행한다. 이 머신을 **에이전트(agent) 머신** 이라고 부른다.

> ## 🗺️ 에이전트 머신은 어느 컴퓨터인가?
>
> - **가장 흔한 케이스**: `setup.sh` 를 돌린 같은 컴퓨터 (Docker 스택과 에이전트가 한 머신에 공존)
> - **고급 케이스**: 테스트 대상 앱이 Mac 전용이라 Mac 에서만 돌려야 할 때 — 별도의 Mac 을 에이전트로 연결
> - **CI 환경**: 리눅스 빌드 서버를 에이전트로 등록
>
> **같은 머신이든 다른 머신이든** 이 섹션의 절차는 동일하다. "에이전트 머신" 이라는 표현이 나오면 본인이 agent.jar 를 돌릴 그 컴퓨터를 말한다.

#### 전체 흐름 요약 (한눈에)

```
┌──────────────────────────────────────────────────────────────────┐
│  사전 조건: JDK 11+ 설치  (§8.6.1)                              │
│       ↓                                                           │
│  Step 1: Jenkins 에 노드 등록  (§8.6.2)                          │
│       ↓     ✅ setup.sh 가 이 단계를 자동 완료함                │
│       ↓     수동 설치 시에만 UI 에서 직접 등록                  │
│       ↓                                                           │
│  Step 2: 노드 시크릿 확보  (§8.6.3)                              │
│       ↓                                                           │
│  Step 3: agent.jar 다운로드  (§8.6.4)                            │
│       ↓                                                           │
│  Step 4: agent.jar 실행 — OS별 명령  (§8.6.5)                   │
│       ↓                                                           │
│  Step 5: 연결 확인  (§8.6.6)                                     │
│       ↓                                                           │
│  Step 6: 백그라운드 유지 설정  (§8.6.7)  ← 선택, 권장           │
│       ↓                                                           │
│  Step 7: Python + Playwright 설치  (§8.6.8)                      │
│       ↓                                                           │
│  완료 → §9 E2E 테스트 실행으로                                  │
└──────────────────────────────────────────────────────────────────┘
```

---

#### 8.6.1 사전 조건: JDK 11 이상 설치

`agent.jar` 는 Java 11 이상에서 동작한다. 에이전트 머신에 JDK 가 이미 있는지 먼저 확인.

**설치 여부 확인:**

```bash
java -version
```

출력 예시:

- ✅ `openjdk version "17.0.9" 2023-10-17` — 11 이상이면 OK, 바로 §8.6.2 로
- ❌ `command not found` — 설치 필요 (아래)
- ❌ `java version "1.8.0_...` — 너무 낮음, 업그레이드 필요

**OS 별 설치:**

<details>
<summary><b>🐧 Ubuntu / Debian / WSL2 Ubuntu</b></summary>

```bash
sudo apt update
sudo apt install -y openjdk-17-jre-headless
java -version    # 재확인
```
</details>

<details>
<summary><b>🎩 Fedora / RHEL / Rocky Linux</b></summary>

```bash
sudo dnf install -y java-17-openjdk-headless
java -version
```
</details>

<details>
<summary><b>🍎 macOS (Homebrew)</b></summary>

```bash
brew install openjdk@17

# 시스템이 java 를 인식하도록 심볼릭 링크
sudo ln -sfn \
  "$(brew --prefix)/opt/openjdk@17/libexec/openjdk.jdk" \
  /Library/Java/JavaVirtualMachines/openjdk-17.jdk

java -version
```

Homebrew 가 없으면 먼저 https://brew.sh 에서 설치.
</details>

<details>
<summary><b>🪟 Windows (winget, 권장)</b></summary>

**PowerShell 을 관리자 권한으로** 열고:

```powershell
winget install --id EclipseAdoptium.Temurin.17.JDK
```

설치 후 **새 PowerShell 창**을 열어야 PATH 가 반영된다. 확인:

```powershell
java -version
```
</details>

<details>
<summary><b>🪟 Windows (수동 설치)</b></summary>

1. https://adoptium.net 접속
2. **Temurin 17 (LTS)** 선택 → 본인 OS 에 맞는 installer (`.msi`) 다운로드
3. 설치 관리자 실행 → **"Set JAVA_HOME variable"** 과 **"Add to PATH"** 옵션 체크
4. 설치 완료 후 새 터미널(PowerShell/cmd)에서 `java -version` 확인
</details>

> **왜 Java 17?** Jenkins LTS 2.426+ 부터 권장 런타임이 Java 17 이다. `agent.jar` 는 Java 11~21 모두 호환되지만 17 이 가장 안정적이고 검증됐다.

---

#### 8.6.2 Step 1 — Jenkins 에 노드 등록

> ### ✅ setup.sh 를 사용했다면 이 단계는 **이미 완료** 됐다.
> Phase 5-5 가 Groovy 스크립트로 `mac-ui-tester` 노드를 자동 생성한다.
> **`setup.sh` 사용자는 §8.6.3 (시크릿 확보) 로 바로 이동.**

**수동 설치 시에만 아래를 따른다:**

1. Jenkins 에 로그인 (`http://localhost:18080/login`)
2. 좌측/상단 메뉴 → **`Jenkins 관리(Manage Jenkins)`**
3. **`노드 관리(Manage Nodes and Clouds)`** 클릭
4. 좌측 사이드바 → **`+ Create a node`** (또는 `New Node`) 클릭
5. **Node name**: `mac-ui-tester` 입력 (반드시 이 이름, 바이트 단위로 정확히)
6. **Type**: `Permanent Agent` 선택 → `Create` 또는 `OK` 클릭
7. 상세 설정 화면에서 아래 값 입력:

| 항목 | 값 | 설명 |
|---|---|---|
| **Remote root directory** | `$HOME/jenkins-agent` (예: `/home/alice/jenkins-agent`) | **Jenkins 가 이 경로 아래에 workspace 디렉터리를 실제로 생성한다.** 에이전트 사용자가 쓸 수 있는 경로여야 한다. `/home/jenkins-agent` 같은 루트 소유 경로는 `AccessDeniedException`. setup.sh 자동 등록 시 `$HOME/jenkins-agent` 로 설정됨. |
| **Labels** | `mac-ui-tester` | Jenkinsfile 이 `agent { label 'mac-ui-tester' }` 로 참조 — 일치 필수 |
| **Launch method** | `Launch agent by connecting it to the controller` | TCP/WebSocket 연결 방식 |

8. **Node Properties** 섹션 → `Environment variables` 체크 → `Add` 클릭:

| Name | Value |
|---|---|
| `SCRIPTS_HOME` | 에이전트 머신 위의 `e2e-pipeline/` 절대 경로 <br/>예: `/home/alice/projects/ttcScripts/e2e-pipeline` |

> **`SCRIPTS_HOME` 이 뭔가?** Jenkinsfile 이 파이썬 모듈(`zero_touch_qa`)을 불러올 때 이 경로를 `PYTHONPATH` 로 사용한다. 이 한 번 설정으로 끝난다 — Jenkinsfile 을 수정할 일은 없다.

9. `Save` 클릭 → 노드 목록에 `mac-ui-tester` 가 `offline` 상태로 추가됨 (agent.jar 가 아직 안 붙었으니 당연)

---

#### 8.6.3 Step 2 — 노드 시크릿 확보

에이전트가 Jenkins 컨트롤러에 연결할 때 쓰는 **64자 hex 문자열**이다. 세 가지 방법 중 아무거나.

<details>
<summary><b>방법 1 — setup.sh 종료 배너에서 (가장 빠름)</b></summary>

`setup.sh` 를 돌렸다면 마지막에 아래 블록이 출력됐다:

```
6. agent.jar 다운로드 및 연결:

     curl -O http://localhost:18080/jnlpJars/agent.jar
     java -jar agent.jar \
       -url "http://localhost:18080/" \
       -secret "d583f7de9fae736db11fcbc948989d0d98f046f1386c34622c72b7e19ee57f75" \
       -name "mac-ui-tester" \
       -webSocket \
       -workDir "$HOME/jenkins-agent"
```

`-secret "..."` 안의 64자 hex 문자열이 시크릿. 터미널에서 못 찾겠으면 `setup.log` 에서:

```bash
grep -oE 'secret "[0-9a-f]{40,64}"' e2e-pipeline/setup.log
```
</details>

<details>
<summary><b>방법 2 — Jenkins UI 에서</b></summary>

1. `Jenkins 관리` → `노드 관리(Manage Nodes and Clouds)`
2. 목록에서 **`mac-ui-tester`** 클릭
3. 상세 페이지에 아래와 같은 안내 박스가 보인다:

   > Run from agent command line:
   > ```
   > java -jar agent.jar -url http://localhost:18080/ -secret <64자hex> -name "mac-ui-tester" -webSocket -workDir "..."
   > ```

4. `-secret` 뒤에 나오는 64자 hex 문자열을 복사
</details>

<details>
<summary><b>방법 3 — curl 한 줄 (가장 빠름, 자동화용)</b></summary>

```bash
curl -sS -u "admin:Admin1234!" \
  "http://localhost:18080/computer/mac-ui-tester/slave-agent.jnlp" \
  | grep -oE '[0-9a-f]{64}' | head -n1
```

출력된 64자 hex 가 시크릿. `admin:Admin1234!` 는 본인이 `.env` 에 지정한 Jenkins 관리자 계정으로 교체.
</details>

> **⚠️ 시크릿은 재사용 가능하다.** 에이전트가 한번 연결되면 해당 노드에 바인딩된다. 노드를 삭제하거나 `setup.sh` 를 `down -v` 로 초기화하지 않는 한 같은 시크릿이 계속 유효하다.

---

#### 8.6.4 Step 3 — agent.jar 다운로드

에이전트 머신의 원하는 작업 폴더로 이동 후:

```bash
curl -O http://localhost:18080/jnlpJars/agent.jar
ls -la agent.jar         # 1.5MB 정도의 jar 파일이 생성됨
```

> **Windows PowerShell 에서 curl 대신:**
> ```powershell
> Invoke-WebRequest -Uri "http://localhost:18080/jnlpJars/agent.jar" -OutFile "agent.jar"
> ```
> 또는 `curl.exe` (Windows 10+ 기본 포함):
> ```powershell
> curl.exe -O http://localhost:18080/jnlpJars/agent.jar
> ```

**컨트롤러가 다른 머신인 경우**: `http://localhost:18080/` 을 컨트롤러의 접근 가능 주소로 교체 (예: `http://192.168.1.100:18080/`).

---

#### 8.6.5 Step 4 — agent.jar 실행 (OS 별 명령)

본인 환경에 해당하는 블록을 그대로 복사해서 실행. **`<시크릿>` 자리만 §8.6.3 에서 확보한 값으로 교체**.

##### 🐧 Linux / WSL2 Ubuntu

```bash
java -jar agent.jar \
  -url "http://localhost:18080/" \
  -secret "<시크릿>" \
  -name "mac-ui-tester" \
  -webSocket \
  -workDir "$HOME/jenkins-agent"
```

`$HOME` 이 자동으로 `/home/사용자명` 으로 확장된다. 최종 작업 디렉터리: `/home/alice/jenkins-agent/`.

> **⚠️ WSL2 에서 실행 위치 주의:** `/mnt/c/...` 경로에 `cd` 한 상태로 `java -jar agent.jar` 를 실행하면
> `Could not determine current working directory` VM 초기화 오류가 발생할 수 있다.
> Java 의 `platformProperties()` 가 9P 파일시스템 브릿지(WSL↔Windows) 위의 CWD 를 해석하지 못하기 때문이다.
> **반드시 `-workDir` 에 지정한 경로로 `cd` 한 뒤 실행한다:**
>
> ```bash
> cd "$HOME/jenkins-agent" && java -jar agent.jar \
>   -url "http://localhost:18080/" \
>   -secret "<시크릿>" \
>   -name "mac-ui-tester" \
>   -webSocket \
>   -workDir "$HOME/jenkins-agent"
> ```

##### 🍎 macOS

```bash
java -jar agent.jar \
  -url "http://localhost:18080/" \
  -secret "<시크릿>" \
  -name "mac-ui-tester" \
  -webSocket \
  -workDir "$HOME/jenkins-agent"
```

최종 작업 디렉터리: `/Users/alice/jenkins-agent/`.

##### 🪟 Windows PowerShell

```powershell
java -jar agent.jar `
  -url "http://localhost:18080/" `
  -secret "<시크릿>" `
  -name "mac-ui-tester" `
  -webSocket `
  -workDir "$env:USERPROFILE\jenkins-agent"
```

> PowerShell 의 줄 이음 문자는 백틱(\`) 이다. bash 의 백슬래시(`\`) 와 다름에 주의.

최종 작업 디렉터리: `C:\Users\alice\jenkins-agent\`.

##### 🪟 Windows cmd

한 줄로 입력:

```cmd
java -jar agent.jar -url "http://localhost:18080/" -secret "<시크릿>" -name "mac-ui-tester" -webSocket -workDir "%USERPROFILE%\jenkins-agent"
```

##### 🪟 Windows Git Bash

```bash
java -jar agent.jar \
  -url "http://localhost:18080/" \
  -secret "<시크릿>" \
  -name "mac-ui-tester" \
  -webSocket \
  -workDir "$HOME/jenkins-agent"
```

Git Bash 에서 `$HOME` 은 `/c/Users/alice` 로 확장된다.

---

> ## ⚠️ `-workDir` 함정 체크리스트
>
> - **✅ 권장**: `$HOME/jenkins-agent` / `$env:USERPROFILE\jenkins-agent` / `%USERPROFILE%\jenkins-agent` — 사용자 홈 하위, 권한 문제 없음, agent.jar 가 자동 생성
> - **❌ 금지**: `/home/jenkins-agent` — root 소유의 최상위, 일반 사용자가 디렉터리 생성 불가 → `java.nio.file.AccessDeniedException`
> - **❌ 금지**: `/var/lib/jenkins` — 권한 제한
> - **❌ 금지**: `C:\jenkins-agent` — Administrator 권한 필요할 수 있음
> - **❌ 금지**: 경로에 공백이나 유니코드 문자 포함 — 가능은 하지만 예외 케이스에서 꼬일 수 있음
>
> 가장 안전한 건 **사용자 홈 바로 아래 `jenkins-agent` 폴더**다. agent.jar 가 없는 폴더면 알아서 만든다.

> ## 🔌 `-webSocket` 플래그는 왜 붙이나?
>
> Jenkins 2.293+ 부터 권장되는 에이전트 연결 방식. 기본 TCP 포트 50000 대신 HTTP(S) (80/443) 위에서 WebSocket 으로 연결한다. 방화벽/프록시 환경에서 문제가 훨씬 적고, Jenkins 컨트롤러도 추가 포트를 열 필요가 없다. **항상 이 플래그를 붙이는 것을 권장.**

---

#### 8.6.6 Step 5 — 연결 확인

agent.jar 를 실행하면 터미널에 아래와 비슷한 로그가 흐른다:

```
INFO: Using /home/alice/jenkins-agent/remoting as the remoting work directory
INFO: Located /home/alice/jenkins-agent/remoting/jarCache as remoting jar cache
INFO: Agent discovery successful
  Agent address: localhost
  Agent port:    50000
INFO: Handshaking
INFO: Connected
INFO: Connected
```

마지막의 **`INFO: Connected`** 가 성공 신호.

**Jenkins UI 에서 재확인:**

1. 브라우저에서 `http://localhost:18080/computer/` 접속
2. 노드 목록에서 `mac-ui-tester` 를 찾는다
3. 상태 아이콘이 **녹색** (Online / Idle) 이면 OK

> **터미널을 끄면 어떻게 되나?** `Ctrl+C` 로 `java -jar agent.jar` 프로세스를 종료하면 즉시 노드가 `offline` 으로 바뀐다. Jenkins 빌드를 돌리려면 이 프로세스가 **살아있어야** 한다. 장기적으로는 §8.6.7 의 백그라운드 유지 방법을 쓰는 걸 권장.

---

#### 8.6.7 Step 6 — 백그라운드 유지 (선택, 권장)

에이전트 터미널을 계속 띄워놓는 건 불편하다. 아래 방법 중 하나로 백그라운드로 돌린다.

<details>
<summary><b>🐧 Linux / macOS — nohup (가장 단순)</b></summary>

```bash
nohup java -jar agent.jar \
  -url "http://localhost:18080/" \
  -secret "<시크릿>" \
  -name "mac-ui-tester" \
  -webSocket \
  -workDir "$HOME/jenkins-agent" \
  > "$HOME/jenkins-agent/agent.log" 2>&1 &

# 실행 중 확인
jobs                                 # [1]+ Running  nohup java ...
ps aux | grep agent.jar               # 프로세스 ID 확인

# 로그 실시간 모니터링
tail -f ~/jenkins-agent/agent.log

# 중지
kill %1                               # 또는 kill <PID>
```

**재부팅하면 꺼진다.** 자동 시작이 필요하면 아래 systemd 방법으로.
</details>

<details>
<summary><b>🐧 Linux — systemd 서비스 (재부팅 후 자동 시작)</b></summary>

`sudo nano /etc/systemd/system/jenkins-agent.service` 로 아래 내용 작성:

```ini
[Unit]
Description=Jenkins mac-ui-tester Agent
After=network.target

[Service]
Type=simple
User=<본인_사용자명>           # 예: alice
WorkingDirectory=/home/<본인_사용자명>/jenkins-agent
ExecStart=/usr/bin/java -jar /home/<본인_사용자명>/jenkins-agent/agent.jar \
  -url http://localhost:18080/ \
  -secret <시크릿> \
  -name mac-ui-tester \
  -webSocket \
  -workDir /home/<본인_사용자명>/jenkins-agent
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
# agent.jar 를 서비스 작업 디렉터리로 복사
cp agent.jar ~/jenkins-agent/

sudo systemctl daemon-reload
sudo systemctl enable --now jenkins-agent
sudo systemctl status jenkins-agent    # 상태 확인
journalctl -u jenkins-agent -f         # 로그 실시간
```

재부팅 시 자동 시작. 중지: `sudo systemctl stop jenkins-agent`.
</details>

<details>
<summary><b>🍎 macOS — launchd LaunchAgent (재부팅 후 자동 시작)</b></summary>

`~/Library/LaunchAgents/com.dscore.jenkins-agent.plist` 작성:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.dscore.jenkins-agent</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/java</string>
    <string>-jar</string>
    <string>/Users/<본인>/jenkins-agent/agent.jar</string>
    <string>-url</string>
    <string>http://localhost:18080/</string>
    <string>-secret</string>
    <string><시크릿></string>
    <string>-name</string>
    <string>mac-ui-tester</string>
    <string>-webSocket</string>
    <string>-workDir</string>
    <string>/Users/<본인>/jenkins-agent</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>/Users/<본인>/jenkins-agent/agent.log</string>
  <key>StandardErrorPath</key>
  <string>/Users/<본인>/jenkins-agent/agent.err.log</string>
</dict>
</plist>
```

```bash
launchctl load ~/Library/LaunchAgents/com.dscore.jenkins-agent.plist
launchctl list | grep jenkins-agent          # 상태 확인
# 중지
launchctl unload ~/Library/LaunchAgents/com.dscore.jenkins-agent.plist
```
</details>

<details>
<summary><b>🪟 Windows — 작업 스케줄러 (Task Scheduler)</b></summary>

1. **시작 메뉴** → `작업 스케줄러(Task Scheduler)` 실행
2. 우측 **작업 만들기(Create Task)** 클릭
3. **일반(General)** 탭:
   - Name: `Jenkins mac-ui-tester Agent`
   - `사용자가 로그온했을 때만 실행` 또는 `사용자의 로그온 여부에 관계없이 실행` 선택 (후자 권장)
4. **트리거(Triggers)** 탭 → `새로 만들기(New)`:
   - Begin the task: `시작 시(At startup)`
5. **동작(Actions)** 탭 → `새로 만들기`:
   - Action: `프로그램 시작(Start a program)`
   - Program/script: `java`
   - Arguments:
     ```
     -jar "C:\Users\alice\jenkins-agent\agent.jar" -url "http://localhost:18080/" -secret "<시크릿>" -name "mac-ui-tester" -webSocket -workDir "C:\Users\alice\jenkins-agent"
     ```
   - Start in: `C:\Users\alice\jenkins-agent`
6. `확인` → 비밀번호 입력 → 저장

확인: 작업 스케줄러 목록에서 `Jenkins mac-ui-tester Agent` 우클릭 → `실행(Run)`.

**더 간단한 대안 (관리자 권한 필요):** NSSM (Non-Sucking Service Manager) 로 Windows 서비스 등록.
```powershell
# https://nssm.cc 에서 nssm.exe 다운로드 후 PATH 에 두기
nssm install JenkinsAgent "C:\Program Files\Eclipse Adoptium\jdk-17.*\bin\java.exe"
nssm set JenkinsAgent AppParameters "-jar C:\Users\alice\jenkins-agent\agent.jar -url http://localhost:18080/ -secret <시크릿> -name mac-ui-tester -webSocket -workDir C:\Users\alice\jenkins-agent"
nssm set JenkinsAgent AppDirectory "C:\Users\alice\jenkins-agent"
nssm start JenkinsAgent
```
</details>

---

#### 8.6.8 Step 7 — Python + Playwright 설치

에이전트가 빌드 시 Playwright (크로미움 브라우저 자동화 라이브러리) 를 실행하므로 **Python 3.9 이상 + Playwright 가 에이전트 머신에 설치**되어 있어야 한다.

**현재 상태 확인:**

```bash
python3 --version                     # 3.9 이상이면 OK
python3 -m playwright --version        # Version 1.x 가 나오면 OK
```

**설치 (공통):**

```bash
pip3 install playwright
playwright install chromium
```

**Ubuntu / Debian / WSL2 — `python3-venv` 패키지 필수:**

Jenkinsfile 의 Stage 1 이 `python3 -m venv` 로 가상환경을 생성한다. Ubuntu/Debian 은 `venv` 모듈이 기본 설치되지 않으므로 아래를 먼저 실행해야 한다:

```bash
sudo apt update
sudo apt install -y python3-venv
```

> 특정 Python 버전을 사용하는 경우 버전에 맞는 패키지를 설치한다:
> ```bash
> python3 --version                    # 예: Python 3.12.x
> sudo apt install -y python3.12-venv  # 버전 번호 일치시킬 것
> ```
>
> 이 패키지가 없으면 빌드 시 `ensurepip is not available` 오류가 발생한다.

**Linux 에이전트 추가 단계:**

Linux 에서는 chromium 이 추가 시스템 라이브러리를 요구한다. 아래를 한번 돌린다 (sudo 필요):

```bash
playwright install-deps chromium
```

설치 확인:

```bash
playwright --version                    # 예: Version 1.45.0
python3 -c "from playwright.sync_api import sync_playwright; print('OK')"
# 출력: OK
```

---

#### 8.6.9 체크포인트 — 여기까지 왔다면

✅ JDK 11+ 설치 완료
✅ `mac-ui-tester` 노드가 Jenkins 에 등록됨
✅ 시크릿 확보 완료
✅ `agent.jar` 다운로드 완료
✅ agent.jar 실행 중 (터미널 또는 백그라운드)
✅ Jenkins UI 에서 `mac-ui-tester` 상태 = **`Online`**
✅ Python 3.9+ 와 Playwright + chromium 설치 완료

다음은 **[§9 E2E 테스트 실행](#9-e2e-테스트-실행)** 으로 첫 빌드를 돌려본다.

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

**증상:**
- Jenkins UI → `노드 관리` → `mac-ui-tester` 에 빨간 X 아이콘 / "Offline" 표시
- 빌드 시작 시 `Waiting for next available executor on mac-ui-tester` 메시지 후 영원히 대기

**확인 순서:**

1. **agent.jar 프로세스가 살아있는가?**
   ```bash
   ps aux | grep agent.jar                 # Linux/macOS/WSL
   tasklist /FI "IMAGENAME eq java.exe"     # Windows cmd
   Get-Process java                         # Windows PowerShell
   ```
   프로세스가 안 보이면 종료된 것 → agent.jar 를 다시 실행해야 한다 (§8.6.5).

2. **agent.jar 를 실행한 터미널의 마지막 로그:**
   - `Connection refused` → Jenkins 컨트롤러 주소가 잘못됐거나 컨트롤러가 멈춤
   - `Authentication failed` → 시크릿 값이 틀림 (재발급 필요, §8.6.3)
   - `Protocol error` → Jenkins 버전과 agent.jar 버전 불일치 → `agent.jar` 를 다시 다운로드 (§8.6.4)
   - `java.nio.file.AccessDeniedException` → `-workDir` 경로에 쓰기 권한 없음 (§8.6.5 함정 체크리스트 참조)

3. **Jenkins 컨트롤러는 살아있는가?**
   ```bash
   curl -sS -o /dev/null -w "%{http_code}" http://localhost:18080/login
   # 200 이어야 정상
   ```

**해결:**
- agent.jar 프로세스가 죽어있으면 §8.6.5 명령을 다시 실행.
- 반복해서 죽으면 백그라운드 유지 방법(§8.6.7) 중 하나로 전환.
- Jenkins 버전 업그레이드 후엔 agent.jar 도 재다운로드(§8.6.4) 필요.

---

### `java.nio.file.AccessDeniedException: /home/jenkins-agent`

**증상:** agent.jar 실행 시 즉시 아래 스택트레이스 출력:

```
Exception in thread "main" java.nio.file.AccessDeniedException: /home/jenkins-agent
  at java.base/sun.nio.fs.UnixException.translateToIOException(UnixException.java:90)
  ...
  at org.jenkinsci.remoting.engine.WorkDirManager.initializeWorkDir(WorkDirManager.java:213)
  at hudson.remoting.Launcher.initialize(Launcher.java:519)
```

**원인:** `-workDir` 에 `/home/jenkins-agent` 또는 비슷한 **root 소유 최상위 경로**를 지정. 일반 사용자 계정으로는 `/home/` 아래에 새 디렉터리를 만들 수 없다.

Jenkins UI 의 노드 상세 페이지에 "Remote root directory" 로 `/home/jenkins-agent` 가 표시되어 있다면, **Jenkins 는 이 경로 아래에 workspace 를 실제로 생성하려 시도한다**. 일반 사용자는 `/home/` 아래에 디렉터리를 만들 수 없으므로 `AccessDeniedException` 이 난다. 별도로, `-workDir` 은 agent.jar 의 remoting 내부 상태 저장용 경로이고 workspace 와는 다른 것이다.

**해결:** `-workDir` 을 **사용자 홈 하위** 경로로 교체. §8.6.5 의 OS별 명령 참조.

```bash
# Linux/macOS/WSL — 이렇게
-workDir "$HOME/jenkins-agent"       # → /home/alice/jenkins-agent

# Windows PowerShell — 이렇게
-workDir "$env:USERPROFILE\jenkins-agent"    # → C:\Users\alice\jenkins-agent
```

> **⚠️ Remote root directory 변경 후 에이전트 재연결 필수:**
> Jenkins UI 에서 노드의 **Remote root directory** 를 변경한 뒤 에이전트를 재연결하지 않으면, 파이프라인은 **이전 경로**로 workspace 를 생성하려 시도한다.
> 빌드 로그의 `Running on mac-ui-tester in <경로>` 줄에서 workspace 경로가 의도한 경로와 다르면 이 상황이다.
>
> **수순:**
> 1. Jenkins UI → `노드 관리` → `mac-ui-tester` → 설정 → **Remote root directory** 를 `-workDir` 과 동일한 경로로 변경 → 저장
> 2. 기존 `agent.jar` 프로세스를 `Ctrl+C` 로 종료
> 3. 에이전트를 **같은 경로의 `-workDir` 로** 다시 실행 (§8.6.5)
> 4. 파이프라인 재실행 → 빌드 로그에서 새 workspace 경로 확인

---

### WSL2: `Could not determine current working directory` (agent.jar 실행 시)

**증상:** WSL2 에서 `/mnt/c/...` 경로에 `cd` 한 상태로 `java -jar agent.jar` 를 실행하면 즉시 VM 초기화 실패:

```
Error occurred during initialization of VM
java.lang.Error: Properties init: Could not determine current working directory.
    at jdk.internal.util.SystemProps$Raw.platformProperties(Native Method)
    ...
```

**원인:** WSL2 의 `/mnt/c/...` 경로는 9P 파일시스템 브릿지를 통해 Windows 파일시스템에 접근한다. Java 의 네이티브 `platformProperties()` 호출이 이 브릿지 위의 CWD 를 해석하지 못하는 경우가 있다.

**해결:** `-workDir` 에 지정한 경로(또는 네이티브 Linux 경로)로 `cd` 한 뒤 실행한다:

```bash
cd "$HOME/jenkins-agent" && java -jar agent.jar \
  -url "http://localhost:18080/" \
  -secret "<시크릿>" \
  -name "mac-ui-tester" \
  -webSocket \
  -workDir "$HOME/jenkins-agent"
```

---

### `ensurepip is not available` (python3 -m venv 실패)

**증상:** Jenkins 빌드 Stage 1 에서 venv 생성 실패:

```
The virtual environment was not created successfully because ensurepip is not
available.  On Debian/Ubuntu systems, you need to install the python3-venv
package using the following command.

    apt install python3.12-venv
```

**원인:** Ubuntu / Debian / WSL2 Ubuntu 는 `python3-venv` 패키지가 기본 설치되지 않는다. `python3` 바이너리는 있어도 `venv` 모듈이 없다.

**해결:**

```bash
sudo apt update
sudo apt install -y python3-venv
```

특정 Python 버전을 사용하는 경우:

```bash
python3 --version                    # 예: Python 3.12.x
sudo apt install -y python3.12-venv  # 버전 번호 일치
```

설치 후 파이프라인을 다시 실행하면 venv 가 정상 생성된다.

---

### `cannot open .../venv/bin/activate: No such file` (손상된 venv)

**증상:** Jenkins 빌드 Stage 1 에서 "Using existing virtual environment." 출력 후, Stage 3 에서:

```
.: cannot open /.../.qa_home/venv/bin/activate: No such file
```

**원인:** `.qa_home/venv` 디렉터리는 존재하지만, 내부 `bin/activate` 파일이 없다. 이전 빌드에서 `python3 -m venv` 가 중간에 실패했거나, 파일시스템 문제로 venv 가 불완전하게 생성된 경우 발생한다. Stage 1 의 `[ ! -d ... ]` 체크는 디렉터리 존재 여부만 보기 때문에 손상된 venv 를 "정상"으로 오판한다.

**해결:** 현재 Jenkinsfile(v2.1+)은 `bin/activate` 존재 여부를 추가로 검증하여 손상 시 자동 재생성한다. 구버전 Jenkinsfile 사용 시 에이전트 머신에서 수동으로 삭제:

```bash
rm -rf "$HOME/jenkins-agent/workspace/DSCORE-ZeroTouch-QA-Docker/.qa_home/venv"
```

삭제 후 파이프라인을 다시 실행하면 venv 가 처음부터 새로 생성된다.

---

### `source: not found` (Jenkins sh 스텝에서)

**증상:** Jenkins 빌드 Stage 3 (또는 venv activate 하는 단계) 에서:

```
/tmp/.../script.sh.copy: 2: source: not found
```

**원인:** Jenkins 의 `sh` 스텝은 `/bin/sh` 를 사용한다. WSL2 Ubuntu 에서 `/bin/sh` 는 **dash** 이며, dash 에는 `source` 명령이 **없다**. `source` 는 bash 전용(bashism)이고, POSIX sh 에서는 `.` (dot) 명령이 동일한 역할을 한다.

**해결:** Jenkinsfile 의 `source` 를 `.` 으로 교체한다:

```groovy
// 변경 전 (bash 전용)
source '${AGENT_HOME}/venv/bin/activate'

// 변경 후 (POSIX 호환)
. '${AGENT_HOME}/venv/bin/activate'
```

> 현재 Jenkinsfile 은 이미 수정되어 있다. 커스텀 Jenkinsfile 을 사용하는 경우 동일하게 교체할 것.

---

### agent.jar 실행 시 `UnsupportedClassVersionError`

**증상:**
```
Exception in thread "main" java.lang.UnsupportedClassVersionError:
  hudson/remoting/Launcher has been compiled by a more recent version of the Java Runtime
  (class file version 61.0), this version of the Java Runtime only recognizes class file versions up to 52.0
```

**원인:** 에이전트 머신의 Java 버전이 너무 낮음. class file version 61.0 = Java 17. 52.0 = Java 8. 즉 **Java 8 로 Java 17 agent.jar 를 실행하려는 상황**.

**해결:** §8.6.1 의 "사전 조건: JDK 11 이상 설치" 를 따라 Java 17 설치. 설치 후 확인:

```bash
java -version
# openjdk version "17.0.9" 또는 이상
```

여러 Java 버전이 설치된 경우 PATH 우선순위 확인:
```bash
which java         # Linux/macOS — /usr/bin/java 또는 brew 경로
where java         # Windows cmd — 첫 번째 결과가 실제 사용되는 java
```

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

### `/mnt/wsl/docker-desktop-bind-mounts/` 경로에서 실행 시도

**증상:** setup.sh Phase 0 에서 아래와 같이 중단된다:

```
[✗] 프로젝트가 Docker Desktop 내부 관리 경로에 위치해 있다:
[✗]   /mnt/wsl/docker-desktop-bind-mounts/Ubuntu/5929fcbd.../e2e-pipeline
```

**원인:** 이 경로는 **Docker Desktop 이 내부적으로 WSL2 bind mount 를 노출하는 관리용 공간**이다. 64자 hex 문자열은 Docker 의 content-addressable ID. Docker 가 언제든 정리·재구성할 수 있고 사용자 편집용이 아니라 setup.sh 가 의도적으로 차단한다.

**왜 여기 들어갔나?** 보통 사용자가 Docker 볼륨의 실제 내용을 탐색하려다 실수로 cd 한 경우다. 혹은 `docker inspect` 에서 본 경로를 그대로 따라간 경우.

**해결:** 현재 경로에서 나와서 정상 위치(§0.1 의 권장 위치) 에 다시 git clone. 이 경로에 있는 파일은 복사하지 말고 버린다 (git push 된 상태라면 remote 에 모든 게 있으므로 손실 없음).

```bash
cd ~
# Windows + WSL2 사용자: Windows 측 작업 폴더 (예: /mnt/c/Users/<사용자>/Documents)
# macOS/Linux 사용자: ~/projects
cd /mnt/c/Users/<사용자>/Documents
git clone https://github.com/illegaluser/ttcScripts.git
cd ttcScripts/e2e-pipeline
cp .env.example .env
./setup.sh
```

---

### WSL2 에서 `/mnt/c/` 사용 시 `docker build` / `git` 이 느림 (정상)

**증상:**
- `docker build` 가 10분 이상 걸림 (일반적으로는 2~3분)
- `git status` / `find` 가 수초 단위로 걸림

**원인:** WSL2 에서 Windows 드라이브 (`/mnt/c/...`) 는 **9P 프로토콜 경유 파일시스템**이라 네이티브 ext4 대비 I/O 가 2~5배 느리다. 이건 WSL2 의 구조적 특성이라 **기능상 문제는 없다** — 속도만 느릴 뿐.

**언제 이 경로를 쓰는가?**
- Windows 쪽 IDE (VS Code / Cursor / Claude Code 등) 에서 편집한 파일이 WSL 터미널에서 즉시 보여야 할 때 — 같은 파일을 양쪽에서 공유하는 가장 자연스러운 방법이 `/mnt/c/` 경로다 (동기화 불필요).
- Windows 측 백업 (OneDrive, 회사 백업 시스템 등) 이 프로젝트 폴더를 포함해야 할 때.

즉 이 설치/운영 흐름에서는 **의도된 trade-off** 다.

**그래도 느려서 개선하고 싶다면 (선택):**
- 반복 `docker build --no-cache` 를 자주 하는 디버깅 단계에서만 일시적으로 WSL2 네이티브 경로 (`~/projects/ttcScripts`) 에 별도 clone 을 두고 거기서 실험
- 주 작업은 `/mnt/c/` 에서 유지해 동기화 편의성 확보
- Jenkins 이미지 레이어 캐시가 한번 만들어지면 이후 rebuild 는 10초대로 내려가므로 첫 빌드만 견디면 실용상 문제 없음

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
