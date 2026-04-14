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

```
사용자 (브라우저 / Jenkins UI)
    │
    ├── http://localhost:18080  →  Jenkins (CI 오케스트레이션)
    │       │
    │       └── mac-ui-tester 에이전트 (Playwright 직접 실행)
    │               │ DIFY_BASE_URL=http://localhost:18081/v1
    │               ▼
    ├── http://localhost:18081  →  Dify 콘솔 (Chatflow 설계)
    │       │
    │       └── api:5001  →  Dify API 서버
    │               │
    │               └── ollama:11434  →  Ollama LLM 엔진
    │
    └── [e2e-net 내부 서비스]
            dify-db (PostgreSQL), dify-redis, qdrant, dify-sandbox
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
| Dify → Ollama LLM | `http://ollama:11434` |
| Dify → Qdrant 벡터 스토어 | `http://qdrant:6333` |
| Dify → Sandbox | `http://dify-sandbox:8194` |

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
├── docker-compose.yaml              ← 독립 E2E 스택 정의
├── Dockerfile.jenkins               ← Jenkins 컨트롤러 이미지
├── zero_touch_qa/                   ← E2E 실행 엔진 패키지
│   ├── __init__.py
│   ├── __main__.py
│   └── ...
├── nginx/
│   └── dify.conf                    ← Dify nginx 라우팅 설정
├── DSCORE-ZeroTouch-QA-Docker.jenkinsPipeline
└── GUIDE.md                         ← 이 문서
```

이 폴더 외부에는 아무것도 필요하지 않다.

---

## 자동 설치 (권장): setup.sh

`setup.sh` 한 번으로 §4~§8.5의 전 과정을 자동 수행한다.  
에이전트 머신 연결(§8.6 Step 2)만 수동 작업으로 남는다.

### 실행 방법

```bash
cd ~/Developer/e2e-pipeline   # e2e-pipeline/ 폴더로 이동

# 기본값으로 실행 (모델: qwen2.5-coder:14b, 계정: admin@example.com / Admin1234!)
./setup.sh

# 환경변수로 설정 오버라이드
OLLAMA_MODEL=qwen2.5-coder:14b \
DIFY_EMAIL=me@company.com \
DIFY_PASSWORD=MySecurePass1! \
JENKINS_ADMIN_USER=admin \
JENKINS_ADMIN_PW=MySecurePass1! \
./setup.sh
```

> **사전 조건:** Docker Desktop이 실행 중이어야 하고 Python 3.9+가 설치되어 있어야 한다.

### 자동화 범위

| 단계 | 내용 | 처리 |
| --- | --- | --- |
| §4 | docker compose up --build + 헬스 대기 | ✅ 자동 |
| §5 | Ollama 모델 Pull | ✅ 자동 |
| §6 | Dify 관리자 계정 / Ollama 공급자 / 모델 등록 | ✅ 자동 |
| §7 | Chatflow import + 게시 + API Key 발급 | ✅ 자동 |
| §8.1~8.2 | Jenkins 플러그인 설치 + 재시작 | ✅ 자동 |
| §8.3 | Jenkins Credentials 등록 (dify-qa-api-token) | ✅ 자동 |
| §8.4 | CSP 완화 설정 | ✅ 자동 |
| §8.5 | Pipeline Job 생성 | ✅ 자동 |
| §8.6 Step 1 | mac-ui-tester 노드 등록 (SCRIPTS_HOME 포함) | ✅ 자동 |
| §8.6 Step 2 | agent.jar 실행 (에이전트 머신에서 수동) | ⚠️ 수동 |

### setup.sh 종료 후 남은 작업

스크립트 종료 시 다음 명령을 출력한다:

```bash
# 에이전트 머신에서 실행
pip3 install playwright && playwright install chromium

curl -O http://localhost:18080/jnlpJars/agent.jar
java -jar agent.jar \
  -url "http://localhost:18080" \
  -secret "<출력된_시크릿>" \
  -name "mac-ui-tester" \
  -workDir "${HOME}/jenkins-agent"
```

Jenkins UI에서 `mac-ui-tester` 상태가 `Connected`로 바뀌면 전체 구성 완료다.

> **수동 설치가 필요한 경우:** 아래 §4~§8를 단계별로 따른다.  
> setup.sh 실패 시 로그의 ⚠ 경고 메시지를 확인하고 해당 섹션만 수동으로 진행한다.

---

## 4. 스택 기동

### 4.1 최초 기동

`e2e-pipeline/` 폴더 **안에서** 실행한다.

```bash
cd ~/Developer/e2e-pipeline   # e2e-pipeline/ 폴더로 이동
docker compose up -d --build
```

빌드 및 최초 기동은 **30 ~ 50분** 소요된다.  
주요 시간 소요: Jenkins 이미지 빌드(Python 패키지 + Playwright, ~20~30분) + Dify 이미지 Pull + Ollama 이미지 Pull.  
두 번째 기동부터는 Docker 레이어 캐시로 수 분 내 완료된다.

### 4.2 기동 상태 확인

> **최초 기동 시 주의:** `e2e-dify-api` 컨테이너는 기동 직후 PostgreSQL 스키마 마이그레이션을 수행한다.  
> 이 작업은 **3~5분** 소요되며, 완료 전 `http://localhost:18081` 접속 시 `502 Bad Gateway`가 나타나는 것은 **정상**이다.  
> 아래 명령으로 완료를 확인한다:
> ```bash
> docker logs -f e2e-dify-api 2>&1 | grep -E "Running|started|migration"
> # "Running on http://0.0.0.0:5001" 메시지가 나타나면 준비 완료
> ```

```bash
docker compose ps
```

아래 서비스가 모두 `Up` 상태여야 한다:

| 컨테이너 이름 | 정상 상태 | 비고 |
| --- | --- | --- |
| `e2e-jenkins` | Up | 18080 포트 응답 |
| `e2e-ollama` | Up | |
| `e2e-qdrant` | Up | |
| `e2e-dify-db` | Up (healthy) | healthcheck 통과 필요 |
| `e2e-dify-redis` | Up (healthy) | healthcheck 통과 필요 |
| `e2e-dify-sandbox` | Up | |
| `e2e-dify-api` | Up | |
| `e2e-dify-worker` | Up | |
| `e2e-dify-web` | Up | |
| `e2e-dify-nginx` | Up | 18081 포트 응답 |

### 4.3 스택 중지 및 재기동

```bash
# 중지 (데이터 보존)
docker compose down

# 재기동
docker compose up -d

# 완전 초기화 (데이터 삭제)
docker compose down -v
```

---

## 5. Ollama LLM 모델 설치

Dify Chatflow에서 사용할 LLM 모델을 Ollama 컨테이너에 설치한다.

### 5.1 권장 모델 Pull

```bash
# Planner 노드용 (시나리오 생성) — 코드 생성 특화 모델
docker exec -it e2e-ollama ollama pull qwen3-coder:30b

# 경량 대안 (메모리 부족 시)
docker exec -it e2e-ollama ollama pull qwen2.5-coder:14b

# Healer 노드용 (DOM 분석, 동일 모델 사용 가능)
# 별도 Pull 불필요 — Planner와 동일 모델 공유
```

> **RTX 4070 Mobile (8 GB VRAM) 환경:** `qwen2.5-coder:14b` 또는 `qwen3-coder:14b`를 권장한다.  
> `qwen3-coder:30b`는 30 GB+ VRAM 또는 CPU+RAM 혼합 추론이 필요하다.

### 5.2 설치 확인

```bash
docker exec -it e2e-ollama ollama list
```

Pull한 모델이 목록에 표시되면 정상이다.

### 5.3 GPU 가속 활성화 (선택)

`docker-compose.yaml`의 `ollama` 서비스에서 GPU 관련 주석을 해제한다:

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
docker compose up -d ollama
```

---

## 6. Dify 초기 설정

### 6.1 관리자 계정 생성

1. 브라우저에서 `http://localhost:18081` 접속
2. 최초 접속 시 **관리자 계정 생성** 화면이 나타난다
3. **사용할 이메일과 비밀번호를 직접 입력**하고 계정을 생성한다

> **`INIT_PASSWORD`에 대해:** `docker-compose.yaml`의 `INIT_PASSWORD: "admin1234"` 값은  
> Dify 서버 내부 초기화 프로세스 용도이며, 웹 UI 로그인 비밀번호와 무관하다.  
> 웹 UI 비밀번호는 이 화면에서 직접 설정한 값을 사용한다.

### 6.2 Ollama LLM 공급자 등록

Dify가 `e2e-net` 내부의 Ollama에 접근하도록 설정한다.

1. 우측 상단 프로필 아이콘 → `설정(Settings)` 클릭
2. 좌측 메뉴 `모델 공급자(Model Providers)` 클릭
3. `Ollama` 검색 후 선택
4. `+ Add Model` 클릭
5. 아래 설정 입력:

| 항목 | 값 |
| --- | --- |
| Model Type | `LLM` |
| Model Name | Pull한 모델명 그대로 입력 (예: `qwen2.5-coder:14b`) |
| Base URL | `http://ollama:11434` |

> **주의:** `http://localhost:11434`가 아닌 **컨테이너 서비스명** `ollama`를 사용해야 한다.  
> `e2e-net` 내부에서 Dify와 Ollama가 서비스명으로 통신한다.

6. `저장` 클릭 후 "Model Name" 이 목록에 표시되면 정상

> 모델을 추가로 사용하려면 `+ Add Model` 을 반복한다.  
> (예: Planner용 `qwen2.5-coder:14b` 등록 후 Healer용 동일 모델 재사용 가능)

---

## 7. Dify Chatflow 구성 (Zero-Touch QA Brain)

Zero-Touch QA의 지능 계층이다. 자연어/기획서를 9대 DSL로 변환하고, 요소 탐색 실패 시 자가 치유 셀렉터를 제안한다.

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
| 2 | `srs_text` | **String** | 아니오 | 자연어 요구사항 (Chat/Doc 모드) |
| 3 | `target_url` | **String** | 아니오 | 테스트 대상 URL |
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

**모델 선택:** (Dify에서 등록한 Ollama 모델 중 선택)
- 권장: `qwen2.5-coder:14b` 또는 `qwen3-coder:30b`

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

**모델 선택:** Planner와 동일한 모델 사용 가능 (성능이 높을수록 치유 정확도 향상)

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

1. 캔버스 우측 상단 `게시(Publish)` 클릭
2. 좌측 메뉴 `API 접근(API Access)` 클릭
3. `+ 새 API Key 만들기` → 이름: `jenkins-zerotouch-qa`
4. 생성된 API Key(`app-xxxxxxxxxx`) 복사 후 저장

> **API Base URL 확인:**  
> 같은 화면 상단에 Base URL이 표시된다. mac-ui-tester 에이전트는 호스트 포트로 Dify에 접근하므로  
> Jenkinsfile의 `DIFY_BASE_URL = "http://localhost:18081/v1"`을 사용한다.  
> (Dify 콘솔이 보여주는 Base URL이 아닌, 위 값을 그대로 사용한다.)

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

### 8.1 초기 잠금 해제

1. `http://localhost:18080` 접속
2. 초기 비밀번호 확인:

```bash
docker exec -it e2e-jenkins cat /var/jenkins_home/secrets/initialAdminPassword
```

3. 출력된 값을 Unlock 화면에 입력
4. `Install suggested plugins` 선택 → 기본 플러그인 설치 완료 대기
5. 관리자 계정 생성

### 8.2 추가 플러그인 설치

`Jenkins 관리` → `Plugins` → `Available plugins` 탭에서 검색 후 설치:

| 플러그인 | 용도 |
| --- | --- |
| `file-parameters` | `base64File` 파라미터 지원 (Doc/Convert 모드 파일 업로드) |
| `htmlpublisher` | HTML 리포트 Jenkins UI 게시 |

설치 후 Jenkins 재시작:

```bash
docker restart e2e-jenkins
```

### 8.3 Jenkins Credentials 등록

`Jenkins 관리` → `Credentials` → `System` → `Global credentials` → `Add Credentials`

- Kind: `Secret text`
- Secret: 7.7에서 복사한 Dify API Key
- ID: **`dify-qa-api-token`** (정확히 일치해야 파이프라인에서 참조 가능)

### 8.4 HTML 리포트 CSP 설정

Jenkins 기본 보안 정책(CSP)이 JavaScript를 차단하여 `publishHTML`로 게시한 리포트가 빈 화면으로 표시된다.  
최초 1회 아래 설정을 적용한다.

`Jenkins 관리` → `스크립트 콘솔(Script Console)` → 아래 코드 입력 후 실행:

```groovy
System.setProperty("hudson.model.DirectoryBrowserSupport.CSP", "")
```

> **주의:** 이 설정은 Jenkins 재시작 시 초기화된다. 영구 적용이 필요하면  
> Jenkins 시작 옵션에 `-Dhudson.model.DirectoryBrowserSupport.CSP=""` 추가가 필요하다.

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
에이전트는 Playwright와 Python이 설치된 Mac(또는 Docker 스택과 동일한 호스트)이다.

> **사전 확인:** 에이전트를 실행할 머신에 Java 11 이상이 설치돼 있어야 한다.
> ```bash
> java -version  # 11 이상이면 OK
> # 미설치 시 (macOS): brew install openjdk@17
> ```

#### Step 1: Jenkins에서 에이전트 노드 등록

1. `Jenkins 관리` → `노드 관리(Manage Nodes)` → `New Node`
2. 이름: `mac-ui-tester`, 유형: `Permanent Agent` → `OK`
3. 아래 설정 입력:

| 항목 | 값 |
| --- | --- |
| Remote root directory | 에이전트 머신의 작업 디렉터리 (예: `/home/{사용자명}/jenkins-agent`) |
| Labels | `mac-ui-tester` |
| Launch method | `Launch agent by connecting it to the controller` |

4. 같은 화면에서 **Node Properties** 섹션 → `환경변수(Environment variables)` 체크 → `추가` 클릭:

| 이름 | 값 |
| --- | --- |
| `SCRIPTS_HOME` | `e2e-pipeline/` 폴더의 절대 경로 (예: `/home/{사용자명}/e2e-pipeline`) |

> `SCRIPTS_HOME`은 이 한 번 설정으로 끝난다. Jenkinsfile을 수정할 필요가 없다.

5. `저장` 클릭

#### Step 2: agent.jar 다운로드 및 에이전트 실행

등록된 노드 목록에서 `mac-ui-tester` 클릭 → 상세 페이지에 연결 명령이 표시된다.  
또는 아래 URL에서 직접 다운로드할 수 있다:

```bash
# agent.jar 다운로드
curl -O http://localhost:18080/jnlpJars/agent.jar

# 에이전트 실행 (노드 상세 페이지의 시크릿 값 사용)
java -jar agent.jar \
  -url "http://localhost:18080" \
  -secret <노드_상세_페이지의_시크릿> \
  -name "mac-ui-tester" \
  -workDir "/Users/{사용자명}/Developer/automation/jenkins-agent"
```

Jenkins UI의 `노드 관리` → `mac-ui-tester`에서 상태가 `Connected`로 바뀌면 성공이다.

#### Step 3: 에이전트 Python 환경 확인

```bash
python3 --version          # 3.9 이상
python3 -m playwright --version  # playwright 설치 확인

# 미설치 시
pip3 install playwright
playwright install chromium
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

### mac-ui-tester가 Dify에 접근 불가

**증상:** 빌드 실행 시 `ConnectionRefusedError` 또는 `http://localhost:18081/v1` 타임아웃

**확인:**

```bash
# e2e 스택이 실행 중인지 확인
docker compose ps

# Dify API 응답 확인
curl -s http://localhost:18081/v1/health || echo "Dify 미응답"
```

**가능한 원인:**
- `docker compose up` 직후 DB 마이그레이션이 아직 진행 중 (§4.2 참조, 3~5분 대기)
- e2e-dify-api 컨테이너가 비정상 종료 → `docker logs e2e-dify-api` 확인

### e2e 스택 네트워크 이름 확인

```bash
docker network ls | grep e2e
# 정상: e2e-net    bridge
# 비정상: e2e-pipeline_e2e-net    bridge (name: 누락 시)
```

`e2e-pipeline_e2e-net`이 표시되면 `docker-compose.yaml`의 `networks` 블록에 `name: e2e-net`을 추가 후 재기동:

```bash
docker compose down
docker compose up -d
```

### mac-ui-tester 에이전트가 오프라인 상태

**증상:** Jenkins 빌드 대기 중 `Waiting for next available executor` 또는 `Offline` 표시

**확인:** `Jenkins 관리` → `노드 관리` → `mac-ui-tester` 상태 확인

**해결:** 에이전트 Mac에서 agent.jar 재실행 (§8.6 참조)

---

### Dify API 연결 실패

**증상:** Jenkins 빌드 로그에 `Dify 연결 실패` 또는 `Connection refused`

**확인:**

```bash
# mac-ui-tester 머신에서 Dify API 접근 테스트 (호스트 포트)
curl -s -o /dev/null -w "%{http_code}" http://localhost:18081/v1/health
# 200 이면 정상. 000 이면 Dify 미기동 또는 포트 문제
```

**해결:**

1. `docker compose ps`로 `e2e-dify-api` 상태 확인
2. `e2e-dify-db`와 `e2e-dify-redis`가 `healthy`인지 확인 (api가 이 둘에 의존)
3. 기동 직후라면 DB 마이그레이션 완료까지 3~5분 대기 (§4.2 참조)
4. 필요 시 재기동: `docker compose restart api worker`

### Ollama 모델 미설치

**증상:** Dify Chatflow 테스트 시 `model not found` 오류

**해결:**

```bash
docker exec -it e2e-ollama ollama list  # 설치된 모델 확인
docker exec -it e2e-ollama ollama pull qwen2.5-coder:14b
```

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

**확인:**

```bash
docker logs e2e-dify-nginx
docker logs e2e-dify-web
```

**해결:** `e2e-dify-web` 컨테이너가 완전히 기동될 때까지 1~2분 대기 후 재시도

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
