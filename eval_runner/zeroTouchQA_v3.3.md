# DSCORE Zero-Touch QA v3.3 통합 마스터 가이드

> 운영 원칙: Jenkinsfile 또는 실행 엔진 변경 시 본 문서를 동일 변경 단위로 동기화한다.

---

## 1. 설계 의도 및 아키텍처 방향

본 시스템은 기존 UI 자동화의 치명적 단점인 스크립트 깨짐(Flakiness)을 극복하기 위해 설계된 3세대 자율 주행 QA 플랫폼이다.
핵심 원칙은 **지능(Brain)과 실행(Muscle)의 완벽한 분리**이다.

### 1.1. 핵심 설계 원칙

**지능과 실행의 분리**

- **지능 계층 (Dify Brain):** 사람의 자연어나 화면을 분석해 방향을 지시(Plan)하고, 심각한 에러 발생 시 원인을 분석해 타겟을 재설정(Heal)하는 관제탑 역할을 담당한다. 프롬프트 수정만으로 시스템의 지능을 업그레이드할 수 있어 유지보수 비용이 제로(0)에 수렴한다.
- **실행 계층 (Python Executor):** Dify의 지시를 받아 물리적으로 브라우저를 제어한다. 단순한 심부름꾼이 아니라 자체적인 7단계 DOM 탐색 로직과 비용 제로(Zero-Cost)의 로컬 자가 치유 로직을 갖추고 있어, 경미한 UI 변경은 외부(LLM) 도움 없이 스스로 극복하는 강건한 생존력을 가진다.

**하이브리드 자가 치유 (Hybrid Self-Healing)**

로컬(비용 0, 속도 최상) → LLM(고성능, 고비용) 순으로 동작하여 운영 효율과 성공률을 동시에 달성한다.

**9대 표준 DSL 준수**

AI의 환각(Hallucination)을 원천 차단하기 위해 두 계층은 다음 9개의 표준 액션으로만 소통한다.

| 액션 | 설명 | 예시 |
| --- | --- | --- |
| `navigate` (또는 `Maps`) | 지정된 URL로 이동 | `{"action": "navigate", "value": "https://example.com"}` |
| `click` | 요소 클릭 | `{"action": "click", "target": "role=button, name=로그인"}` |
| `fill` | 텍스트 입력 | `{"action": "fill", "target": "label=이메일", "value": "test@test.com"}` |
| `press` | 키보드 키 입력 | `{"action": "press", "value": "Enter"}` |
| `select` | 드롭다운 선택 | `{"action": "select", "target": "#country", "value": "한국"}` |
| `check` | 체크박스 토글 | `{"action": "check", "target": "label=약관 동의", "value": "on"}` |
| `hover` | 마우스 호버 | `{"action": "hover", "target": "text=메뉴"}` |
| `wait` | 지정 시간 대기 (ms) | `{"action": "wait", "value": "2000"}` |
| `verify` | 요소 존재/텍스트 확인 | `{"action": "verify", "target": "#result", "value": "성공"}` |

### 1.2. 변경 이력

| 버전 | 변경 사항 |
| --- | --- |
| v3.0 | 3-Flow 통합 아키텍처 초안. Dify Brain + Python Executor 분리 설계 |
| v3.1 | 7단계 LocatorResolver, 3단계 하이브리드 Self-Healing, 9대 액션 완전 매핑, 산출물 6종 체계 |
| v3.2 | dict target 방어 코드, 미지원 액션 예외 처리, scenario.healed.json 저장, Candidate Search 액션별 분기 |
| v3.3 | Flow 1 파일 업로드 API 연동, Record 캡처 엔진 고도화(input/change/select), Base64 이미지 압축(Pillow), Dify heal 변수 구조 명확화, CLI `--file` 인자 추가 |

---

## 2. 시스템 아키텍처 구성도

### 2.1. 전체 계층 구조

```
┌─────────────────────────────────────────────────────────────────────┐
│                        사용자 진입 계층                               │
│                                                                     │
│   ┌──────────────┐  ┌──────────────┐  ┌──────────────────────────┐  │
│   │ Flow 1: Doc  │  │ Flow 2: Chat │  │ Flow 3: Record (로컬)    │  │
│   │ 기획서 업로드  │  │ 자연어 입력   │  │ 브라우저 조작 캡처        │  │
│   └──────┬───────┘  └──────┬───────┘  └────────────┬─────────────┘  │
│          │                 │                       │                │
└──────────┼─────────────────┼───────────────────────┼────────────────┘
           │                 │                       │
           ▼                 ▼                       ▼
┌──────────────────────────────────────────────────────────────────────┐
│                   지능 계층 (Dify Brain)                              │
│                                                                      │
│   ┌────────────────────┐  ┌──────────────────┐  ┌────────────────┐  │
│   │  Planner LLM       │  │  Vision Refactor │  │  Healer LLM    │  │
│   │  (Doc/Chat → DSL)  │  │  (Record → DSL)  │  │  (에러 → 수정) │  │
│   │                    │  │                  │  │                │  │
│   │  모델: qwen3-coder │  │  모델: GPT-4o    │  │  모델: 가용LLM  │  │
│   └────────┬───────────┘  └────────┬─────────┘  └───────▲────────┘  │
│            │                       │                    │           │
│            └───────────┬───────────┘                    │           │
│                        │ 9대 표준 DSL (JSON)            │           │
└────────────────────────┼────────────────────────────────┼───────────┘
                         │                                │
                         ▼                                │ 에러+DOM
┌─────────────────────────────────────────────────────────┼───────────┐
│                실행 계층 (Python Executor)                │           │
│                                                         │           │
│   ┌─────────────────────────────────────────────────────┼────────┐  │
│   │                  QAExecutor (오케스트레이터)           │        │  │
│   │                                                     │        │  │
│   │   ┌───────────────────────────┐                     │        │  │
│   │   │   LocatorResolver         │                     │        │  │
│   │   │   (7단계 시맨틱 탐색)      │                     │        │  │
│   │   │                           │                     │        │  │
│   │   │   1. role + name          │                     │        │  │
│   │   │   2. text                 │                     │        │  │
│   │   │   3. label                │                     │        │  │
│   │   │   4. placeholder          │                     │        │  │
│   │   │   5. testid               │                     │        │  │
│   │   │   6. CSS / XPath          │                     │        │  │
│   │   │   7. 존재 검증 → 실패 시  ─┼──┐                 │        │  │
│   │   └───────────────────────────┘  │                  │        │  │
│   │                                  ▼                  │        │  │
│   │   ┌───────────────────────────────────────────────┐ │        │  │
│   │   │        3단계 하이브리드 자가 치유               │ │        │  │
│   │   │                                               │ │        │  │
│   │   │   [1단계] Candidate Search (로컬, 비용 0)     │ │        │  │
│   │   │          액션별 셀렉터 분기 + 유사도 매칭      │ │        │  │
│   │   │                    │                          │ │        │  │
│   │   │                 실패 시                        │ │        │  │
│   │   │                    ▼                          │ │        │  │
│   │   │   [2단계] Dify Healer LLM 호출 ───────────────┼─┼────────┘  │
│   │   │          DOM 스냅샷 + 에러 전송 → 수정 DSL 수신│ │           │
│   │   │                    │                          │ │           │
│   │   │                 실패 시                        │ │           │
│   │   │                    ▼                          │ │           │
│   │   │   [3단계] 최종 실패 → 에러 스크린샷 저장       │ │           │
│   │   └───────────────────────────────────────────────┘ │           │
│   └─────────────────────────────────────────────────────┘           │
│                                                                     │
│   ┌─────────────────────────────────────────────────────────────┐   │
│   │                    산출물 생성                                │   │
│   │                                                             │   │
│   │   scenario.json          원본 DSL 시나리오                   │   │
│   │   scenario.healed.json   치유된 최종 시나리오                 │   │
│   │   run_log.jsonl          스텝별 실행 로그                    │   │
│   │   step_N_pass.png        성공 증적 스크린샷                  │   │
│   │   step_N_healed.png      치유 후 성공 스크린샷               │   │
│   │   error_final.png        최종 에러 스크린샷                  │   │
│   └─────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────────┐
│                   인프라 계층 (Jenkins + Mac Agent)                   │
│                                                                     │
│   ┌──────────────────┐     ┌─────────────────────────────────────┐  │
│   │  Jenkins Master   │────▶│  Mac Local Agent (mac-ui-tester)   │  │
│   │  (Docker)         │     │  - Java 17 + Playwright Chromium   │  │
│   │  :8080            │     │  - Headed 브라우저 (GUI 세션)       │  │
│   └──────────────────┘     │  - 화면 기록/접근성 권한 필수        │  │
│                            └─────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘
```

### 2.2. 데이터 흐름 (Flow별)

**Flow 1: Doc-to-Test**

```
기획서(PDF/Word) → [Dify Parser] → TC 추출 → [Planner LLM] → 9대 DSL JSON
    → [Python Executor] → LocatorResolver → 브라우저 실행 → 산출물
```

**Flow 2: Chat-to-Test**

```
자연어("로그인해줘") → [Planner LLM] → 9대 DSL JSON
    → [Python Executor] → LocatorResolver → 브라우저 실행 → 산출물
    (모호 시 Dify HITL → 사용자에게 되물음 → 재시도)
```

**Flow 3: Record-to-Test**

```
브라우저 조작 → [JS 이벤트 캡처 + Red Box 스크린샷] → action_log
    → [Vision Refactor LLM] → 시맨틱 DSL JSON → scenario.json 저장
```

**Self-Healing 흐름**

```
요소 탐색 실패
    → [1단계] Candidate Search (로컬 유사도 매칭, 비용 0, ~10ms)
        → 성공 시: 대체 요소로 실행, HEALED (LOCAL) 기록
        → 실패 시:
    → [2단계] Dify Healer LLM (DOM + 에러 전송, 비용 발생, ~3초)
        → 성공 시: 수정된 DSL로 재시도
        → 실패 시:
    → [3단계] 최종 실패 → error_final.png 저장 → 빌드 실패
```

---

## 3. 통합 유저 워크플로우 (3대 진입 경로)

사용자는 코드를 작성할 필요 없이, 목적에 맞는 진입 경로를 선택하면 모든 결과가 표준 9대 DSL로 수렴하여 실행된다.

### 3.1. Flow 1: 문서 업로드 (Doc-to-Test)

- **목적:** 기획서 기반 대량 시나리오 구축
- **사용자 행동:** Dify 화면에 요구사항 정의서(PDF/Word)를 업로드한다.
- **시스템 흐름:** Dify 파서가 문서를 읽고 테스트 케이스(TC)를 분리한다. Planner LLM이 각 TC를 9대 DSL로 번역한다. 파이썬 엔진이 실행한다.

### 3.2. Flow 2: 대화형 직접 입력 (Chat-to-Test)

- **목적:** 신규 기능의 즉각적인 단건 검증 및 디버깅
- **사용자 행동:** Dify 채팅창에 자연어로 입력한다. (예: "네이버 검색창에 DSCORE 치고 엔터 눌러줘")
- **시스템 흐름:** Planner LLM이 즉시 의도를 파악해 9대 DSL로 번역한다. 대상이 모호할 경우 Dify가 채팅으로 사용자에게 되묻는다(HITL). 파이썬 엔진이 실행한다.

### 3.3. Flow 3: 스마트 레코딩 (Record-to-Test)

- **목적:** 복잡한 UI 인터랙션을 시각적으로 캡처하여 영구 자산화
- **사용자 행동:** 맥북 터미널에서 `--mode record` 옵션으로 스크립트를 실행한 후 브라우저를 평소처럼 조작한다.
- **시스템 흐름:** 파이썬이 주입한 JS가 클릭/입력 시마다 붉은 테두리(Red Box)를 그리고 화면을 캡처한다. 브라우저 종료 시 Dify Vision LLM이 스크린샷과 원시 이벤트 로그를 교차 검증하여 의미론적 DSL로 정제한다. 최종 스크립트를 `scenario.json`으로 저장한다.

---

## 4. Jenkins 인프라 및 에이전트 구축 가이드 (Mac Local)

Docker 컨테이너의 GUI 부재 한계를 극복하기 위해 맥북을 Jenkins 노드로 직접 연결하여 화면이 보이는(Headed) 테스트 환경을 구축한다.

### 4.1. Java 17 및 디렉토리 세팅 (맥북 터미널)

Jenkins 에이전트는 Java 17 버전에서 가장 안정적으로 동작한다.

```bash
# Java 17 설치 및 환경 변수 등록
brew install openjdk@17
sudo ln -sfn $(brew --prefix)/opt/openjdk@17/libexec/openjdk.jdk \
  /Library/Java/JavaVirtualMachines/openjdk-17.jdk
echo 'export PATH="/opt/homebrew/opt/openjdk@17/bin:$PATH"' >> ~/.zshrc
source ~/.zshrc
java -version  # 반드시 17.x.x 출력 확인 (Java 8 불가)

# 에이전트 및 테스트 실행 워크스페이스 생성
mkdir -p /Users/luuuuunatic/Developer/jenkins_agent
mkdir -p /Users/luuuuunatic/Developer/automation/local_qa
```

### 4.2. Jenkins Master 노드 생성 (Jenkins UI)

Jenkins 웹 UI(`http://localhost:8080`)에서 다음 절차를 수행한다.

1. `Jenkins 관리` > `Nodes` > `New Node` 선택
2. 노드 이름: `mac-local-agent` (Permanent Agent)
3. 설정값:
   - **Number of executors:** `1` (UI 테스트 충돌 방지를 위해 단일 실행자 사용)
   - **Remote root directory:** `/Users/luuuuunatic/Developer/jenkins_agent`
   - **Labels:** `mac-ui-tester`
   - **Usage:** `Only build jobs with label expressions matching this node`
   - **Launch method:** `Launch agent by connecting it to the controller` (Inbound Agent)

### 4.3. 에이전트 연결 및 macOS 보안 권한 해제

이 설정을 누락하면 스크린샷이 검게 나오거나 마우스 제어가 차단된다.

**에이전트 연결:**

노드 생성 후 Jenkins가 제공하는 `java -jar agent.jar ...` 명령어를 복사하여 맥북 터미널(`jenkins_agent` 폴더)에서 실행한다. `Connected` 상태를 확인한다.

**macOS 보안 권한 부여:**

맥북 `시스템 설정` > `개인정보 보호 및 보안`으로 이동하여 다음 권한을 부여한다.

| 항목 | 대상 앱 | 미허용 시 증상 |
| --- | --- | --- |
| **화면 기록 (Screen Recording)** | `Terminal`, `Java` | 캡처 화면이 검게 나옴 |
| **접근성 (Accessibility)** | `Terminal`, `Java` | Playwright 브라우저 제어 차단 |

---

## 5. Dify Brain (Chatflow) 상세 설정 가이드

앱 생성 시 반드시 **Chatflow** 타입을 선택한다. 각 노드가 9대 DSL 스키마를 이탈하지 않도록 강력한 페르소나를 부여한다.

### 5.1. 전역 변수 (Start Node)

| 변수명 | 타입 | 설명 |
| --- | --- | --- |
| `run_mode` | Select | `chat`, `doc`, `record`, `heal` 중 선택. 실행 흐름 분기용 |
| `is_automated` | Boolean | CI/수동 개입 판별 |
| `srs_text` | String | 자연어 요구사항 (Chat/Doc 모드용) |
| `error` | Paragraph | Heal 모드 전용. 실행 엔진이 전달하는 에러 메시지 |
| `dom` | Paragraph | Heal 모드 전용. 실행 엔진이 전달하는 HTML DOM 스냅샷 (최대 10,000자) |

### 5.2. 조건 분기 (IF/ELSE 노드)

`run_mode` 값에 따라 각 LLM 노드로 화살표를 분기한다.

- `run_mode == "doc"` 또는 `run_mode == "chat"` → Planner LLM 노드
- `run_mode == "record"` → Vision Refactor LLM 노드
- `run_mode == "heal"` → Healer LLM 노드 (이 경우 `error`, `dom` 변수가 함께 전달됨)

### 5.3. Planner LLM 노드 (Flow 1, 2 처리용)

- **권장 모델:** `qwen3-coder:30b` (또는 최신 코딩 특화 모델)
- **System Prompt:**

```text
당신은 테스트 자동화 아키텍트입니다. 제공된 요구사항(SRS)을 분석하여 아래의 [9대 표준 액션]만 사용하는 JSON 배열을 작성하십시오.

[9대 표준 액션]: navigate, click, fill, press, select, check, hover, wait, verify

[규칙]:
- 각 스텝은 "step"(숫자), "action"(문자열), "target"(문자열 또는 객체), "value"(문자열), "description"(문자열) 키를 가져야 합니다.
- target은 가능한 한 의미론적 선택자를 사용하십시오. 예: "role=button, name=로그인", "label=이메일", "text=검색"
- CSS 셀렉터나 XPath는 의미론적 선택자가 불가능한 경우에만 사용하십시오.

[엄수 사항]:
- 반드시 유효한 JSON 배열([...]) 형태만 출력하십시오.
- 마크다운 코드블록(```)이나 부연 설명은 절대 금지합니다.
```

### 5.4. Vision Refactor LLM 노드 (Flow 3 처리용)

- **권장 모델:** `GPT-4o` 또는 `Claude 3.5 Sonnet` (Vision Detail: High)
- **System Prompt:**

```text
당신은 UI 스크립트 정제 전문가입니다. 첨부된 Playwright 원시 이벤트 로그와 스크린샷들을 교차 분석하십시오.

[작업]:
1. 노이즈 제거: 불필요한 마우스 이동, 중복 이벤트를 제거하십시오.
2. 시각적 정제: 이미지 내 붉은 테두리(Red Box)가 강조된 요소를 확인하고, 원시 태그명을 의미론적 선택자(예: role=button, name=로그인 또는 text=로그인)로 교체하십시오.

[엄수 사항]:
- 반드시 9대 표준 액션(navigate, click, fill, press, select, check, hover, wait, verify)을 준수하는 순수 JSON 배열([...]) 형식으로만 출력하십시오.
- 마크다운 코드블록이나 부연 설명은 절대 금지합니다.
```

### 5.5. Healer LLM 노드 (에러 복구용)

- **권장 모델:** 가용한 최고 성능 모델
- **System Prompt:**

```text
당신은 자가 치유(Self-Healing) 시스템입니다. 에러 메시지와 제공된 HTML DOM 스냅샷을 분석하십시오.

[작업]:
- 기존 요소를 찾지 못한 이유를 파악하십시오.
- DOM 내에서 가장 안정적인 대체 셀렉터를 찾으십시오.
- 해당 단일 스텝만 수정하여 출력하십시오.

[출력 형식]:
- 순수 JSON 객체({...}) 형식으로만 출력하십시오.
- "step", "action", "target", "value", "description" 키를 포함하십시오.
- 부연 설명은 절대 금지합니다.
```

---

## 6. 통합 실행 엔진 (`mac_local_executor.py`) 전체 소스코드

7단계 시맨틱 탐색, 하이브리드 자가 치유, 9대 액션 완전 매핑, 산출물 생성, Flow 1 파일 업로드, Record 캡처 고도화가 모두 포함된 코어 스크립트이다.

**파일 경로:** `/Users/luuuuunatic/Developer/automation/local_qa/mac_local_executor.py`

**의존성:** `pip install requests playwright pillow`

```python
import os
import json
import time
import re
import base64
import difflib
import io

import requests
from PIL import Image
from playwright.sync_api import sync_playwright


# =============================================================================
# 환경 변수
# =============================================================================
DIFY_BASE_URL = os.getenv("DIFY_BASE_URL", "http://localhost/v1")
DIFY_API_KEY = os.getenv("DIFY_API_KEY")


# =============================================================================
# 유틸리티
# =============================================================================
def extract_json_safely(text):
    """
    LLM 응답에서 마크다운 코드펜스, C-style 주석을 제거한 후
    순수 JSON 배열 또는 객체만 추출하여 파싱한다.
    """
    text = re.sub(r'//.*?\n|/\*.*?\*/', '', text, flags=re.S)
    match = re.search(r'\[\s*\{.*\}\s*\]|\{\s*".*\}\s*', text, re.DOTALL)
    return json.loads(match.group(0)) if match else None


def compress_image_to_b64(file_path):
    """
    API 전송 용량 최적화를 위한 이미지 압축 및 Base64 인코딩.
    1024px로 리사이즈 후 JPEG Quality 60%으로 압축한다.
    원본 대비 약 1/5 수준으로 페이로드를 줄여
    Dify API 요청 크기 제한(15MB)에 걸리지 않도록 방어한다.
    """
    with Image.open(file_path) as img:
        img = img.convert("RGB")
        img.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
        buffer = io.BytesIO()
        img.save(buffer, format="JPEG", quality=60)
        return base64.b64encode(buffer.getvalue()).decode("utf-8")


# =============================================================================
# Dify Brain (통신 계층)
# =============================================================================
class DifyBrain:
    """
    Dify Chatflow API와의 통신을 전담한다.
    - /v1/files/upload: Flow 1 (Doc) 문서 파일 업로드
    - /v1/chat-messages: 시나리오 생성/치유 요청 (blocking 모드)
    """

    def __init__(self):
        self.headers = {"Authorization": f"Bearer {DIFY_API_KEY}"}

    def upload_file(self, file_path):
        """
        Flow 1 (Doc-to-Test)을 위한 문서 파일 업로드.
        Dify Files API(/v1/files/upload)에 파일을 전송하고
        upload_file_id를 반환한다.

        Args:
            file_path: 업로드할 문서 파일 경로 (PDF, DOCX 등)

        Returns:
            Dify가 발급한 file_id 문자열
        """
        print(f"[Doc] 문서 업로드 중... ({file_path})")
        with open(file_path, "rb") as f:
            files = {"file": (os.path.basename(file_path), f, "application/pdf")}
            data = {"user": "mac-agent"}
            res = requests.post(
                f"{DIFY_BASE_URL}/files/upload",
                headers=self.headers,
                files=files,
                data=data,
            )
            res.raise_for_status()
            file_id = res.json().get("id")
            print(f"[Doc] 문서 업로드 완료 (ID: {file_id})")
            return file_id

    def call_api(self, payload, file_id=None):
        """
        Dify Chat Messages API에 payload를 전송하고,
        응답에서 JSON을 추출하여 반환한다.

        Args:
            payload: Dify inputs 딕셔너리
            file_id: upload_file()에서 받은 파일 ID (Flow 1 전용, 선택)

        Returns:
            파싱된 JSON (list 또는 dict), 실패 시 None
        """
        req_body = {
            "inputs": payload,
            "query": "실행을 요청합니다.",
            "response_mode": "blocking",
            "user": "mac-agent",
        }
        # Flow 1: 업로드된 파일을 첨부
        if file_id:
            req_body["files"] = [{
                "type": "document",
                "transfer_method": "local_file",
                "upload_file_id": file_id,
            }]

        try:
            res = requests.post(
                f"{DIFY_BASE_URL}/chat-messages",
                json=req_body,
                headers={
                    "Authorization": f"Bearer {DIFY_API_KEY}",
                    "Content-Type": "application/json",
                },
                timeout=120,  # 문서 파싱을 고려하여 타임아웃 연장
            )
            res.raise_for_status()
            return extract_json_safely(res.json().get("answer", ""))
        except Exception as e:
            print(f"[ERROR] Dify API 통신 실패: {e}")
            return None


# =============================================================================
# LocatorResolver (7단계 시맨틱 탐색 엔진)
# =============================================================================
class LocatorResolver:
    """
    Dify가 생성한 target을 Playwright Locator로 변환한다.
    target이 dict(의미론적 객체)이든 str(접두사 문법 또는 CSS/XPath)이든
    모두 수용할 수 있도록 방어 로직을 갖추고 있다.

    탐색 순서:
      1. role + name   (접근성 역할 기반, 가장 안정적)
      2. label         (입력 폼 라벨)
      3. text          (화면 표시 텍스트)
      4. placeholder   (입력 필드 힌트)
      5. testid        (data-testid 속성)
      6. CSS / XPath   (구조적 폴백)
      7. 존재 검증     (count > 0 확인 후 반환, 실패 시 None)
    """

    def __init__(self, page):
        self.page = page

    def resolve(self, target):
        if not target:
            return None

        # ── Dict 타겟 처리 (Dify가 JSON 객체로 보낸 경우) ──
        if isinstance(target, dict):
            if target.get("role"):
                return self.page.get_by_role(
                    target["role"], name=target.get("name", "")
                ).first
            if target.get("label"):
                return self.page.get_by_label(target["label"]).first
            if target.get("text"):
                return self.page.get_by_text(target["text"]).first
            if target.get("placeholder"):
                return self.page.get_by_placeholder(target["placeholder"]).first
            if target.get("testid"):
                return self.page.get_by_test_id(target["testid"]).first
            # dict 내부에 selector가 있으면 문자열로 전환하여 아래 로직 진행
            target = target.get("selector", str(target))

        target_str = str(target).strip()

        # ── 1단계: role + name ──
        if target_str.startswith("role="):
            m = re.match(r"role=(.+?),\s*name=(.+)", target_str)
            if m:
                return self.page.get_by_role(
                    m.group(1).strip(), name=m.group(2).strip()
                ).first

        # ── 2~5단계: 시맨틱 접두사 탐색 ──
        prefix_map = {
            "text=": self.page.get_by_text,
            "label=": self.page.get_by_label,
            "placeholder=": self.page.get_by_placeholder,
            "testid=": self.page.get_by_test_id,
        }
        for prefix, method in prefix_map.items():
            if target_str.startswith(prefix):
                value = target_str.replace(prefix, "", 1).strip()
                return method(value).first

        # ── 6~7단계: CSS/XPath 폴백 및 존재 검증 ──
        loc = self.page.locator(target_str)
        return loc.first if loc.count() > 0 else None


# =============================================================================
# LocalHealer (비용 제로 로컬 자가 치유 엔진)
# =============================================================================
class LocalHealer:
    """
    LLM 호출 없이(비용 0) 현재 페이지의 DOM을 스캔하여
    실패한 타겟과 가장 유사한 요소를 찾아 반환한다.

    액션 타입에 따라 검색 대상 요소를 분기한다:
      - click/check/hover: button, a, [role='button'], [role='link']
      - fill/press:        input, textarea, [role='textbox']
      - select:            select, [role='listbox'], [role='combobox']
    """

    def __init__(self, page):
        self.page = page

    def try_local_healing(self, step):
        """
        step의 target과 유사한 요소를 DOM에서 검색한다.
        유사도 80% 이상 매칭 시 해당 요소를 반환한다.

        Args:
            step: DSL 스텝 딕셔너리

        Returns:
            매칭된 Playwright Locator 또는 None
        """
        act = step["action"].lower()
        tgt = step.get("target", "")

        # 액션별 검색 대상 셀렉터 분기
        if act in ("fill", "press"):
            selector = "input, textarea, [role='textbox'], [role='searchbox'], [contenteditable='true']"
        elif act == "select":
            selector = "select, [role='listbox'], [role='combobox']"
        else:
            selector = "button, a, [role='button'], [role='link'], [role='menuitem'], [role='tab']"

        # target 문자열에서 접두사 제거하여 순수 텍스트 추출
        clean_target = re.sub(
            r"^(text|role|label|placeholder|testid)=", "", str(tgt)
        )
        clean_target = re.sub(r"role=.+?,\s*name=", "", clean_target).strip()

        if len(clean_target) <= 1:
            return None

        best_match = None
        highest_ratio = 0.0

        for el in self.page.locator(selector).all():
            text = (
                el.inner_text()
                or el.get_attribute("placeholder")
                or el.get_attribute("value")
                or el.get_attribute("aria-label")
                or ""
            ).strip()
            if not text:
                continue
            ratio = difflib.SequenceMatcher(None, clean_target, text).ratio()
            if ratio > 0.8 and ratio > highest_ratio:
                highest_ratio = ratio
                best_match = el

        if best_match:
            print(
                f"  [로컬복구 성공] 유사도 {highest_ratio * 100:.0f}% 매칭"
            )
        return best_match


# =============================================================================
# QAExecutor (오케스트레이터)
# =============================================================================
class QAExecutor:
    """
    DSL 시나리오를 받아 실행하고, 3단계 하이브리드 자가 치유를 수행하며,
    모든 산출물(scenario.json, scenario.healed.json, run_log.jsonl,
    스텝별 스크린샷)을 생성한다.
    """

    ARTIFACTS_DIR = "artifacts"

    def __init__(self):
        self.run_log = []
        os.makedirs(self.ARTIFACTS_DIR, exist_ok=True)

    # ── 9대 액션 매핑 ──
    def _perform_action(self, page, locator, step):
        """
        9대 표준 DSL 액션을 실행한다.
        미지원 액션이 들어오면 즉시 ValueError를 발생시킨다.
        """
        act = step["action"].lower()
        val = step.get("value", "")

        if act == "click":
            locator.click(timeout=5000)
        elif act == "fill":
            locator.fill(str(val))
        elif act == "press":
            locator.press(str(val))
        elif act == "select":
            locator.select_option(label=str(val))
        elif act == "check":
            if str(val).lower() == "off":
                locator.uncheck()
            else:
                locator.check()
        elif act == "hover":
            locator.hover()
        elif act == "verify":
            if not val:
                assert locator.is_visible(), (
                    f"요소가 보이지 않습니다: {step.get('target')}"
                )
            else:
                actual = locator.inner_text() or locator.input_value()
                assert str(val) in actual, (
                    f"텍스트 불일치: 기대='{val}', 실제='{actual}'"
                )
        elif act in ("navigate", "maps", "wait"):
            # 메인 루프에서 처리하므로 여기서는 무시
            pass
        else:
            raise ValueError(
                f"미지원 DSL 액션: '{act}'. "
                f"허용: navigate, click, fill, press, select, check, hover, wait, verify"
            )

    # ── 로그 기록 ──
    def _log_step(self, step, status, heal_stage="none"):
        self.run_log.append({
            "step": step.get("step", "-"),
            "action": step.get("action", ""),
            "target": str(step.get("target", "")),
            "status": status,
            "heal_stage": heal_stage,
            "ts": time.time(),
        })

    # ── 산출물 저장 ──
    def _save_artifacts(self, scenario):
        """scenario.healed.json 및 run_log.jsonl을 저장한다."""
        healed_path = os.path.join(self.ARTIFACTS_DIR, "scenario.healed.json")
        with open(healed_path, "w", encoding="utf-8") as f:
            json.dump(scenario, f, indent=2, ensure_ascii=False)

        log_path = os.path.join(self.ARTIFACTS_DIR, "run_log.jsonl")
        with open(log_path, "w", encoding="utf-8") as f:
            for entry in self.run_log:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    # ── 메인 실행 루프 ──
    def execute(self, scenario, is_ci):
        """
        DSL 시나리오를 순차 실행한다.
        각 스텝에서 실패 시 3단계 하이브리드 자가 치유를 시도한다.

        Args:
            scenario: DSL 스텝 리스트
            is_ci: True이면 Jenkins CI 환경 (headed 모드)
        """
        # 원본 시나리오 보존
        original_path = os.path.join(self.ARTIFACTS_DIR, "scenario.json")
        with open(original_path, "w", encoding="utf-8") as f:
            json.dump(scenario, f, indent=2, ensure_ascii=False)

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=not is_ci, slow_mo=500)
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            resolver = LocatorResolver(page)
            healer = LocalHealer(page)
            brain = DifyBrain()

            try:
                for step in scenario:
                    act = step["action"].lower()
                    step_id = step.get("step", "-")

                    # ── 메타 액션 (타겟 불필요) ──
                    if act in ("navigate", "maps"):
                        url = step.get("value") or step.get("target", "")
                        page.goto(url)
                        page.screenshot(
                            path=os.path.join(
                                self.ARTIFACTS_DIR, f"step_{step_id}_pass.png"
                            )
                        )
                        self._log_step(step, "PASS")
                        print(f"  [Step {step_id}] navigate -> PASS")
                        continue

                    if act == "wait":
                        ms = int(step.get("value", 1000))
                        page.wait_for_timeout(ms)
                        self._log_step(step, "PASS")
                        print(f"  [Step {step_id}] wait {ms}ms -> PASS")
                        continue

                    # ── 타겟 필요 액션: 실행 + 자가 치유 루프 ──
                    max_retries = 2
                    for attempt in range(max_retries):
                        try:
                            desc = step.get("description", "")
                            print(f"  [Step {step_id}] {act}: {desc}")

                            locator = resolver.resolve(step.get("target"))
                            if not locator:
                                raise Exception(
                                    f"요소 탐색 실패: {step.get('target')}"
                                )

                            self._perform_action(page, locator, step)
                            page.screenshot(
                                path=os.path.join(
                                    self.ARTIFACTS_DIR,
                                    f"step_{step_id}_pass.png",
                                )
                            )
                            self._log_step(step, "PASS")
                            break

                        except Exception as e:
                            print(f"  [Step {step_id}] 에러: {e}")

                            # ── [Heal 1단계] 로컬 유사도 매칭 ──
                            healed_loc = healer.try_local_healing(step)
                            if healed_loc:
                                self._perform_action(page, healed_loc, step)
                                page.screenshot(
                                    path=os.path.join(
                                        self.ARTIFACTS_DIR,
                                        f"step_{step_id}_healed.png",
                                    )
                                )
                                self._log_step(
                                    step, "HEALED", heal_stage="local"
                                )
                                break

                            # ── [Heal 2단계] Dify Healer LLM 호출 ──
                            if attempt == max_retries - 1:
                                self._log_step(step, "FAIL")
                                raise e

                            print(
                                "  [Heal 2단계] Dify Healer LLM 호출 중..."
                            )
                            dom_snapshot = page.content()[:10000]
                            new_step = brain.call_api({
                                "run_mode": "heal",
                                "error": str(e),
                                "dom": dom_snapshot,
                            })
                            if new_step:
                                step.update(new_step)
                                print(
                                    f"  [Heal 2단계] LLM 복구 완료. "
                                    f"새 타겟: {step.get('target')}"
                                )
                            else:
                                self._log_step(step, "FAIL")
                                raise Exception("Healer LLM 응답 없음")

            except Exception as final_e:
                page.screenshot(
                    path=os.path.join(self.ARTIFACTS_DIR, "error_final.png")
                )
                raise final_e

            finally:
                self._save_artifacts(scenario)
                browser.close()


# =============================================================================
# 스마트 레코더 (Flow 3: Record-to-Test, 로컬 전용)
# =============================================================================
def run_recorder(url):
    """
    브라우저에서의 사용자 조작을 캡처하여 Dify Vision LLM으로 전송,
    정제된 DSL 시나리오를 scenario.json으로 저장한다.

    v3.3 캡처 엔진:
      - mousedown: 클릭 이벤트 (버튼, 링크 등. 입력/선택 요소는 제외)
      - change: 체크박스, 라디오, 셀렉트 드롭다운 변경 감지
      - input: 텍스트/텍스트에어리어 입력 (0.8초 디바운싱으로 타이핑 완료 감지)
      - Red Box: 캡처 시 대상 요소에 4px 붉은 테두리 강조
      - 이미지 압축: Pillow로 1024px 리사이즈 + JPEG 60% 압축 (페이로드 1/5 절감)
    """
    os.makedirs("artifacts", exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        page = browser.new_page()
        action_log = []

        def capture_snapshot(evt_type, target_info, value=""):
            """JS에서 호출되는 Python 브릿지 함수. value는 입력값/선택값."""
            file_name = f"artifacts/snap_{int(time.time())}.png"
            page.screenshot(path=file_name)
            img_b64 = compress_image_to_b64(file_name)

            action_data = {
                "action": evt_type,
                "target": target_info,
                "image": img_b64,
            }
            if value:
                action_data["value"] = value
            action_log.append(action_data)
            print(f"  [Record] 캡처: {evt_type} -> {target_info} (value: {value})")

        page.expose_function("captureSnapshot", capture_snapshot)

        # JS 주입: mousedown(Click), change(Select/Check), input(Fill) 감지
        page.add_init_script("""
            let typingTimer;

            // 1. Click 이벤트 (버튼, 링크 등 — 입력/선택 요소는 제외)
            document.addEventListener('mousedown', async (e) => {
                if (['INPUT', 'SELECT', 'TEXTAREA'].includes(e.target.tagName)) return;
                const oldOutline = e.target.style.outline;
                e.target.style.outline = '4px solid red';
                const tagInfo = e.target.tagName +
                    (e.target.id ? '#' + e.target.id : '') +
                    (e.target.className ? '.' + e.target.className.split(' ')[0] : '');
                await window.captureSnapshot('click', tagInfo, '');
                setTimeout(() => { e.target.style.outline = oldOutline; }, 200);
            }, { capture: true });

            // 2. Change 이벤트 (Checkbox, Radio, Select)
            document.addEventListener('change', async (e) => {
                const oldOutline = e.target.style.outline;
                e.target.style.outline = '4px solid red';
                let actionType = 'click';
                let val = e.target.value;
                if (e.target.tagName === 'SELECT') {
                    actionType = 'select';
                } else if (e.target.type === 'checkbox') {
                    actionType = 'check';
                    val = e.target.checked ? 'on' : 'off';
                } else if (e.target.type === 'radio') {
                    actionType = 'click';
                }
                const tagInfo = e.target.tagName +
                    (e.target.id ? '#' + e.target.id : '') +
                    (e.target.name ? '[name=' + e.target.name + ']' : '');
                await window.captureSnapshot(actionType, tagInfo, val);
                e.target.style.outline = oldOutline;
            }, { capture: true });

            // 3. Input 이벤트 (Text, Textarea — 0.8초 디바운싱)
            document.addEventListener('input', (e) => {
                if (e.target.type === 'checkbox' || e.target.type === 'radio') return;
                clearTimeout(typingTimer);
                typingTimer = setTimeout(async () => {
                    const oldOutline = e.target.style.outline;
                    e.target.style.outline = '4px solid red';
                    const tagInfo = e.target.tagName +
                        (e.target.id ? '#' + e.target.id : '') +
                        (e.target.name ? '[name=' + e.target.name + ']' : '');
                    await window.captureSnapshot('fill', tagInfo, e.target.value);
                    e.target.style.outline = oldOutline;
                }, 800);
            }, { capture: true });
        """)

        page.goto(url)
        print("[Record] 레코딩 중... 브라우저 창을 닫으면 종료됩니다.")
        page.wait_for_event("close")

        print("[Record] 브라우저 종료. Dify Vision API로 데이터 전송 중...")
        brain = DifyBrain()
        refined_dsl = brain.call_api({
            "run_mode": "record",
            "payload": json.dumps(action_log),
        })

        if refined_dsl:
            output_path = "artifacts/scenario.json"
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(refined_dsl, f, indent=2, ensure_ascii=False)
            print(f"[Record] 정제 완료. 저장: {output_path}")
            print(json.dumps(refined_dsl, indent=2, ensure_ascii=False))
        else:
            print("[Record] Vision LLM 정제 실패. action_log를 확인하십시오.")


# =============================================================================
# 메인 엔트리포인트
# =============================================================================
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="DSCORE Zero-Touch QA v3.3 통합 실행 엔진"
    )
    parser.add_argument(
        "--mode",
        choices=["execute", "record"],
        default="execute",
        help="execute: Dify에서 DSL을 받아 실행, record: 브라우저 조작 캡처 (로컬 전용)",
    )
    parser.add_argument(
        "--url",
        default="https://www.google.com",
        help="record 모드 시 시작 URL",
    )
    parser.add_argument(
        "--file",
        default=None,
        help="Flow 1 (Doc) 모드용 기획서 파일 경로 (PDF, DOCX 등)",
    )
    args = parser.parse_args()

    if args.mode == "record":
        run_recorder(args.url)
    else:
        brain = DifyBrain()
        run_mode = os.getenv("RUN_MODE", "chat")

        # Flow 1 (Doc): 파일 업로드 후 file_id 획득
        file_id = None
        if run_mode == "doc" and args.file:
            if not os.path.exists(args.file):
                raise FileNotFoundError(f"파일을 찾을 수 없습니다: {args.file}")
            file_id = brain.upload_file(args.file)
        elif run_mode == "doc" and not args.file:
            print("[WARN] Doc 모드이지만 --file 인자가 없습니다. SRS_TEXT로 대체합니다.")

        payload = {
            "run_mode": run_mode,
            "srs_text": os.getenv("SRS_TEXT", ""),
        }
        scenario = brain.call_api(payload, file_id=file_id)

        if scenario:
            is_ci = bool(os.getenv("JENKINS_HOME"))
            QAExecutor().execute(scenario, is_ci)
        else:
            print("[ERROR] 시나리오를 받아오지 못했습니다. Dify 설정을 확인하십시오.")
```

---

## 7. Jenkins Pipeline (Job #8) 전체 스크립트

Venv 캐싱, 환경 변수 자동 주입, 산출물 영구 보관이 결합된 파이프라인이다.

### 7.1. Job 정의

| 항목 | 값 |
| --- | --- |
| Job 이름 | `DSCORE-ZeroTouch-QA-v3` |
| Job 유형 | Pipeline |
| 실행 노드 | `mac-ui-tester` (Mac Local Agent) |
| 입력 | `RUN_MODE`, `TARGET_URL`, `SRS_TEXT` |
| 출력 | `artifacts/` 폴더 전체 아카이빙 |

### 7.2. Jenkinsfile

```groovy
pipeline {
    agent { label 'mac-ui-tester' }

    parameters {
        choice(
            name: 'RUN_MODE',
            choices: ['chat', 'doc'],
            description: '테스트 진입 방식 (레코딩은 로컬 터미널에서 --mode record로 실행)'
        )
        string(
            name: 'TARGET_URL',
            defaultValue: 'https://www.google.com',
            description: '테스트 시작 URL'
        )
        text(
            name: 'SRS_TEXT',
            defaultValue: '검색창에 DSCORE 입력 후 엔터',
            description: '[Chat] 자연어 요구사항'
        )
        base64File(
            name: 'DOC_FILE',
            description: '[Doc] 기획서 문서 업로드 (PDF/Docx). File Parameter 플러그인 필요.'
        )
    }

    environment {
        AGENT_HOME = "/Users/luuuuunatic/Developer/automation/local_qa"
        DIFY_API_KEY = credentials('dify-qa-api-token')
    }

    stages {
        stage('1. 환경 동기화 및 캐싱') {
            steps {
                sh """
                    cd ${AGENT_HOME}

                    # 이전 실행 아티팩트 초기화
                    rm -rf artifacts/* || true
                    mkdir -p artifacts

                    # 파이썬 Venv 캐시 활용 (최초 실행 시에만 생성)
                    if [ ! -d "venv" ]; then
                        echo "[Setup] Creating new virtual environment..."
                        python3 -m venv venv
                        source venv/bin/activate
                        pip install requests playwright pillow
                        playwright install chromium
                    else
                        echo "[Setup] Using existing virtual environment."
                    fi
                """
            }
        }

        stage('2. 문서 파일 준비 (Doc 모드)') {
            when {
                expression { params.RUN_MODE == 'doc' && params.DOC_FILE != '' }
            }
            steps {
                withFileParameter('DOC_FILE') {
                    sh "cp \$DOC_FILE ${AGENT_HOME}/upload.pdf"
                }
            }
        }

        stage('3. 자율 주행 QA 엔진 가동 (Execute & Heal)') {
            steps {
                script {
                    sh """
                        cd ${AGENT_HOME}
                        source venv/bin/activate

                        export RUN_MODE="${params.RUN_MODE}"
                        export TARGET_URL="${params.TARGET_URL}"
                        export SRS_TEXT="${params.SRS_TEXT}"

                        FILE_ARG=""
                        if [ "${params.RUN_MODE}" = "doc" ] && [ -f "upload.pdf" ]; then
                            FILE_ARG="--file upload.pdf"
                        fi

                        echo "[Engine] QA 엔진 구동 시작 (RUN_MODE=${params.RUN_MODE})"
                        python3 mac_local_executor.py --mode execute \$FILE_ARG
                    """
                }
            }
        }
    }

    post {
        always {
            // 실행 결과와 무관하게 모든 산출물 영구 보관
            // scenario.json, scenario.healed.json, run_log.jsonl,
            // step_N_pass.png, step_N_healed.png, error_final.png
            archiveArtifacts artifacts: 'artifacts/*', allowEmptyArchive: true
            echo "테스트 종료. Jenkins 빌드 결과에서 Artifacts를 확인하십시오."
        }
    }
}
```

**Jenkins 플러그인 요구사항:** `base64File` 파라미터를 사용하려면 Jenkins에 **File Parameter** 플러그인이 설치되어 있어야 한다. `Jenkins 관리` > `Plugins` > `Available`에서 `file-parameters`를 검색하여 설치한다.

### 7.3. Jenkins Credentials 사전 등록

파이프라인에서 `credentials('dify-qa-api-token')`을 사용하므로, 사전에 Jenkins에 등록해야 한다.

1. `Jenkins 관리` > `Credentials` > `System` > `Global credentials` > `Add Credentials`
2. Kind: `Secret text`
3. Secret: Dify API Key 값
4. ID: `dify-qa-api-token`
5. 저장

---

## 8. 산출물(Artifacts) 표준

모든 실행은 `artifacts/` 디렉토리에 아래 산출물을 생성한다.

| 파일 | 설명 | 생성 시점 |
| --- | --- | --- |
| `scenario.json` | Dify가 생성한 원본 DSL 시나리오. 재현 및 감사용. | 실행 시작 시 |
| `scenario.healed.json` | Self-Healing이 반영된 최종 시나리오. 다음 실행 시 캐시로 재사용 가능. | 실행 종료 시 (finally) |
| `run_log.jsonl` | 스텝별 실행 결과(status, heal_stage, timestamp)를 시계열로 기록. 디버깅용. | 실행 종료 시 (finally) |
| `step_N_pass.png` | 각 스텝 성공 시 캡처한 증적 스크린샷. | 스텝 성공 시 |
| `step_N_healed.png` | 로컬 자가 치유 후 성공 시 캡처한 증적 스크린샷. | 로컬 치유 성공 시 |
| `error_final.png` | 모든 치유 시도가 실패한 후 캡처한 최종 에러 스크린샷. | 최종 실패 시 |

### 8.1. run_log.jsonl 레코드 형식

```json
{"step": 1, "action": "navigate", "target": "https://example.com", "status": "PASS", "heal_stage": "none", "ts": 1711700000.0}
{"step": 2, "action": "click", "target": "role=button, name=로그인", "status": "HEALED", "heal_stage": "local", "ts": 1711700003.5}
{"step": 3, "action": "fill", "target": "label=이메일", "status": "FAIL", "heal_stage": "none", "ts": 1711700010.2}
```

---

## 9. 운영 가이드

### 9.1. Jenkins에서 실행 (CI 모드)

1. Jenkins에서 `DSCORE-ZeroTouch-QA-v3` Job을 연다.
2. `Build with Parameters`를 선택한다.
3. `RUN_MODE`를 선택하고, `SRS_TEXT`에 자연어 요구사항을 입력한다.
4. `빌드` 버튼을 누른다.
5. 빌드 완료 후 `Artifacts`에서 산출물을 확인한다.

### 9.2. Jenkins에서 Doc 모드 실행 (Flow 1)

1. Jenkins에서 `DSCORE-ZeroTouch-QA-v3` Job을 연다.
2. `Build with Parameters`를 선택한다.
3. `RUN_MODE`를 `doc`으로 선택한다.
4. `DOC_FILE`에 기획서 파일(PDF/DOCX)을 업로드한다.
5. `빌드` 버튼을 누른다.
6. 시스템이 파일을 Dify에 업로드한 후 Planner LLM이 TC를 추출하여 DSL로 변환, 자동 실행한다.

### 9.3. 로컬에서 Doc 모드 실행 (CLI)

```bash
cd /Users/luuuuunatic/Developer/automation/local_qa
source venv/bin/activate
export DIFY_API_KEY="app-xxxxxxxxx"
export RUN_MODE="doc"

python3 mac_local_executor.py --mode execute --file ./requirements_spec.pdf
```

### 9.4. 로컬에서 레코딩 (Record 모드)

```bash
cd /Users/luuuuunatic/Developer/automation/local_qa
source venv/bin/activate
export DIFY_API_KEY="app-xxxxxxxxx"

python3 mac_local_executor.py --mode record --url https://target-app.com
```

브라우저가 열리면 평소처럼 조작한다. 브라우저 창을 닫으면 레코딩이 종료되고, Vision LLM이 정제한 DSL이 `artifacts/scenario.json`에 저장된다.

### 9.5. 로컬에서 실행 (디버깅)

```bash
cd /Users/luuuuunatic/Developer/automation/local_qa
source venv/bin/activate
export DIFY_API_KEY="app-xxxxxxxxx"
export RUN_MODE="chat"
export SRS_TEXT="네이버에서 DSCORE 검색 후 엔터"

python3 mac_local_executor.py --mode execute
```

---

## 10. 후속 작업 (v3.4 로드맵)

### 10.1. v3.3에서 완료된 항목

| 항목 | 상태 |
| --- | --- |
| Flow 1 파일 업로드 (Dify Files API 연동) | v3.3 완료 |
| Record 캡처 고도화 (input/change/select 이벤트) | v3.3 완료 |
| Base64 페이로드 이미지 압축 (Pillow) | v3.3 완료 |
| Dify heal 모드 변수 구조 명확화 | v3.3 완료 |

### 10.2. 미완료 항목

| 항목 | 설명 | 난이도 |
| --- | --- | --- |
| `regression_test.py` 자동 생성 | 성공한 시나리오를 LLM 없이 재실행 가능한 독립 Playwright 스크립트로 변환 | 중간 |
| `index.html` 리포트 생성 | Jenkins에 게시할 시각적 HTML 리포트 (기존 `build_html_report()` 패턴 이식) | 중간 |
| Candidate Search 셀렉터 확장 | select/hover 액션에 대한 검색 대상 요소 추가 | 낮음 |
| Langfuse 연동 | 기존 eval_runner의 Langfuse 관측성 체계를 Zero-Touch QA에도 적용 | 중간 |
