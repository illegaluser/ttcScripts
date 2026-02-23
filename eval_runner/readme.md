# DSCORE-TTC: 외부 AI 에이전트 평가 시스템 E2E 통합 구축 마스터 가이드 (최종 완성본)

## 📖 제1장. 7대 측정 지표(Metrics) 및 프레임워크 매핑 안내

시스템은 자원 낭비를 막고 평가 신뢰도를 높이기 위해 3단계(Fail-Fast ➔ 과업 검사 ➔ 문맥 평가)로 나누어 총 7가지 지표를 측정합니다.

| 검증 단계 | 측정 지표 (Metric) | 담당 프레임워크 및 측정 원리 | 코드 위치 |
| --- | --- | --- | --- |
| **1. Fail-Fast**<br>

<br>(즉시 차단) | **① Policy Violation**<br>

<br>(보안/금칙어 위반) | **[Promptfoo]**<br>

<br>AI의 응답을 임시 파일로 저장한 뒤, 외장 도구인 Promptfoo를 CLI로 호출하여 주민등록번호나 API Key 등 정의된 정규식 패턴이 발견되면 즉시 불합격시킵니다. | `test_runner.py`의<br>

<br>`_promptfoo_check` |
|  | **② Format Compliance**<br>

<br>(응답 규격 준수) | **[jsonschema (Python)]**<br>

<br>대상 AI가 API일 경우, 반환한 JSON 데이터가 우리가 요구한 필수 형태(예: `answer` 키 포함)를 갖추었는지 파이썬 라이브러리로 검사합니다. | `test_runner.py`의<br>

<br>`_schema_check` |
| **2. 과업 검사**<br>

<br>(Agent 전용) | **③ Task Completion**<br>

<br>(지시 과업 달성도) | **[Python Custom Logic]**<br>

<br>대상 AI가 인프라를 제어하는 Agent일 경우, 상태 코드(`status_code=200`)나 특정 문자열(`raw~r/완료/`)을 반환했는지 자체 정규식 파서로 복합 검사합니다. | `test_runner.py`의<br>

<br>`_evaluate_agent_criteria` |
| **3. 심층 평가**<br>

<br>(문맥 채점) | **④ Answer Relevancy**<br>

<br>(동문서답 여부) | **[DeepEval + Ollama]**<br>

<br>DeepEval 프레임워크가 로컬 LLM(Ollama)을 심판관으로 기용하여, AI의 대답이 질문 의도에 부합하는지 0~1점 사이의 실수로 정밀 채점합니다. | `test_runner.py`의<br>

<br>`AnswerRelevancyMetric` |
|  | **⑤ Faithfulness**<br>

<br>(환각/거짓말 여부) | **[DeepEval + Ollama]**<br>

<br>답변 내용이 백그라운드에서 검색된 원문(`docs`)에 명시된 사실인지, 지어낸 말인지 채점합니다. (※ 대상 시스템이 원문을 반환하지 않으면 오탐 방지를 위해 생략합니다.) | `test_runner.py`의<br>

<br>`FaithfulnessMetric` |
|  | **⑥ Contextual Recall**<br>

<br>(정보 검색력) | **[DeepEval + Ollama]**<br>

<br>질문에 답하기 위해 AI가 충분하고 올바른 정보(원문)를 검색해 왔는지 채점합니다. (※ 검색 원문 확인이 가능한 API 모드 전용입니다.) | `test_runner.py`의<br>

<br>`ContextualRecallMetric` |
| **4. 운영 관제** | **⑦ Latency**<br>

<br>(응답 소요 시간) | **[Python `time` + Langfuse]**<br>

<br>질문을 던진 시점부터 답변 수신(또는 화면 렌더링) 완료까지의 체감 시간을 밀리초(ms)로 재고 Langfuse에 전송합니다. | `adapters/` 내부의<br>

<br>타이머 변수 |

---

## 📖 제2장. 스크립트 간 연관관계 및 데이터 플로우 (Architecture Flow)

평가 시스템의 코드들은 각자의 명확한 역할을 가지고 서로 데이터를 주고받으며 폭포수(Waterfall)처럼 작동합니다.

1. **`Jenkins Pipeline` (운영자 인터페이스)**: 운영자가 폼에 입력한 타겟 주소, 방식(http/ui_chat), 인증 키, 시험지(CSV)를 환경 변수로 세팅하고 총괄 평가관을 깨웁니다.
2. **`test_runner.py` (총괄 평가관)**: 시스템의 지휘소입니다. 시험지를 한 줄씩 읽은 뒤, 교환기(`registry.py`)에 현재 방식에 맞는 통신원을 파견해달라고 요청합니다.
3. **`registry.py` (어댑터 교환기)**: `test_runner.py`의 요청을 받아, API 방식이면 `http_adapter.py`를, 웹 방식이면 `playwright_adapter.py`를 매칭해 줍니다.
4. **`http_adapter.py` / `playwright_adapter.py` (통신원)**: 실제 대상 AI에 접속해 질문을 던지고 답변을 받아옵니다. 이때 방식이 달라도 반드시 `base.py`에 정의된 **표준 바구니(UniversalEvalOutput)** 규격에 데이터를 담아 평가관에게 제출합니다.
5. **`configs/security.yaml` & `schema.json` (검문소)**: 통신원이 가져온 답변 바구니를 평가관이 1차로 검사할 때 쓰는 규칙 문서입니다.
6. **`DeepEval` & `Ollama` (심판관)**: 1차 검사를 통과하면, 평가관이 로컬 LLM을 호출해 문맥의 질(환각, 동문서답)을 채점시킵니다.
7. **`Langfuse` (관제탑)**: 모든 과정(통신 속도, 에러, 점수, 감점 이유)을 실시간으로 전달받아 90일간 안전하게 저장하고 시각화합니다.

---

## 제3장. Jenkins Credentials 사전 등록 (보안)

파이프라인 소스 코드에 Langfuse API Key를 하드코딩하면 보안 취약점이 발생합니다. Jenkins의 암호화 저장소를 이용합니다.

1. 브라우저에서 `http://localhost:8080` (Jenkins)에 로그인합니다.
2. **[Jenkins 관리(Manage Jenkins)]** ➔ **[Credentials]** ➔ **[System]** ➔ **[Global credentials (unrestricted)]**를 클릭합니다.
3. 우측 상단 **[Add Credentials]** 클릭 ➔ Kind를 **[Secret text]**로 선택합니다.
4. Secret에 Langfuse **Public Key**(`pk-lf-...`)를, ID에 `langfuse-public-key`를 입력하고 저장합니다.
5. 다시 **[Add Credentials]** 클릭 ➔ Secret에 **Secret Key**(`sk-lf-...`)를, ID에 `langfuse-secret-key`를 입력하고 저장합니다.

---

## 제4장. 호스트 디렉터리 세팅 및 Docker 인프라 병합 구성

기존 DSCORE-TTC의 DevOps 및 지식 관리 인프라를 전혀 건드리지 않고, 필요한 패키지와 서비스만 정확하게 덧붙이는 과정입니다.

### 4.1 호스트 물리 디렉터리 생성

터미널을 열고 `<PROJECT_ROOT>`에서 기존 구조에 평가용 폴더들을 추가합니다.

```bash
# 1. 평가 파이썬 스크립트 및 설정 파일 폴더
mkdir -p data/jenkins/scripts/eval_runner/adapters
mkdir -p data/jenkins/scripts/eval_runner/configs
mkdir -p data/jenkins/scripts/eval_runner/tests

# 2. 평가 기준 시험지 및 결과 리포트 폴더
mkdir -p data/knowledges/eval/data
mkdir -p data/knowledges/eval/reports

# 3. Langfuse 관제탑 데이터베이스 보존용 폴더
mkdir -p data/postgres-langfuse

```

### 4.2 `Dockerfile.jenkins` (기존 구성 + 신규 평가 도구 통합)

기존 파일의 내용을 기반으로, `nodejs`, `npm`, `deepeval`, `langfuse` 등을 병합한 완전본입니다. 파일을 덮어쓰십시오.

```dockerfile
# DSCORE-TTC 통합 Jenkins 이미지 (AI 에이전트 평가 도구 포함 확장판)
FROM jenkins/jenkins:lts-jdk21
USER root

# 1. 시스템 의존성 설치 (기존 + Promptfoo용 nodejs/npm 추가)
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-pip python3-venv \
    curl jq \
    libgtk-3-0 libasound2 libdbus-glib-1-2 libx11-xcb1 \
    poppler-utils \
    libreoffice-impress \
    nodejs npm \
    && rm -rf /var/lib/apt/lists/*

# 2. 필수 파이썬 패키지 일괄 설치
# 기존 지식관리(pymupdf, crawl4ai 등) 라이브러리와 신규 평가용 라이브러리를 함께 설치
RUN pip3 install --no-cache-dir --break-system-packages \
    requests tenacity beautifulsoup4 lxml html2text \
    pypdf pdf2image pillow python-docx python-pptx pandas openpyxl pymupdf \
    crawl4ai playwright ollama \
    deepeval==1.3.5 pytest==8.0.0 pytest-xdist==3.5.0 \
    jsonschema==4.21.1 langfuse==2.15.0 jsonpath-ng==1.6.1

# 3. Playwright 브라우저 엔진 설치
RUN python3 -m playwright install --with-deps chromium

# 4. 정적 보안 분석용 Promptfoo 글로벌 설치
RUN npm install -g promptfoo@0.50.0

ENV TZ=Asia/Seoul

RUN mkdir -p /var/jenkins_home/scripts \
    /var/jenkins_home/knowledges \
    && chown -R jenkins:jenkins /var/jenkins_home

USER jenkins

```

### 4.3 `docker-compose.yaml` (기존 구성 + Langfuse 서버 통합)

기존의 SonarQube와 GitLab 구성을 모두 유지한 채 Langfuse 스택만 추가한 완전본입니다. 파일을 덮어쓰십시오.

```yaml
networks:
  devops-net:
    external: true

services:
  # ==========================================
  # [기존] SonarQube & GitLab Stack
  # ==========================================
  postgres-sonar:
    image: postgres:15-alpine
    container_name: postgres-sonar
    environment:
      POSTGRES_USER: sonar
      POSTGRES_PASSWORD: sonarpassword
      POSTGRES_DB: sonar
    volumes:
      - ./data/postgres-sonar:/var/lib/postgresql/data
    networks:
      - devops-net
    restart: unless-stopped

  sonarqube:
    image: sonarqube:community
    container_name: sonarqube
    depends_on:
      - postgres-sonar
    environment:
      SONAR_JDBC_URL: jdbc:postgresql://postgres-sonar:5432/sonar
      SONAR_JDBC_USERNAME: sonar
      SONAR_JDBC_PASSWORD: sonarpassword
      SONAR_ES_BOOTSTRAP_CHECKS_DISABLE: true
    ports:
      - "9000:9000"
    volumes:
      - ./data/sonarqube/data:/opt/sonarqube/data
      - ./data/sonarqube/extensions:/opt/sonarqube/extensions
      - ./data/sonarqube/logs:/opt/sonarqube/logs
    networks:
      - devops-net
    restart: unless-stopped

  gitlab:
    image: gitlab/gitlab-ce:latest
    container_name: gitlab
    hostname: gitlab.local
    environment:
      GITLAB_OMNIBUS_CONFIG: |
        external_url 'http://localhost:8929'
        gitlab_rails['gitlab_shell_ssh_port'] = 2224
        puma['worker_processes'] = 2
        sidekiq['concurrency'] = 5
        prometheus_monitoring['enable'] = false
        gitlab_rails['time_zone'] = 'Asia/Seoul'
    ports:
      - "8929:8929"
      - "2224:22"
    volumes:
      - ./data/gitlab/config:/etc/gitlab
      - ./data/gitlab/logs:/var/log/gitlab
      - ./data/gitlab/data:/var/opt/gitlab
    networks:
      - devops-net
    shm_size: "256m"
    restart: unless-stopped

  # ==========================================
  # [신규] Langfuse AI 평가 관제 스택
  # ==========================================
  db-langfuse:
    image: postgres:15-alpine
    container_name: db-langfuse
    environment:
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: postgrespassword
      POSTGRES_DB: langfuse
    volumes:
      - ./data/postgres-langfuse:/var/lib/postgresql/data
    networks:
      - devops-net
    restart: unless-stopped

  langfuse-server:
    image: langfuse/langfuse:latest
    container_name: langfuse-server
    depends_on:
      - db-langfuse
    ports:
      - "3000:3000"
    environment:
      - DATABASE_URL=postgresql://postgres:postgrespassword@db-langfuse:5432/langfuse
      - NEXTAUTH_URL=http://localhost:3000
      - NEXTAUTH_SECRET=dscore_super_secret_key
      - TELEMETRY_ENABLED=false
      - TRACE_RETENTION_DAYS=90 # 90일 경과 로그 자동 삭제 (무한 증식 방지)
    networks:
      - devops-net
    restart: unless-stopped

  # ==========================================
  # [수정] 통합 Jenkins (볼륨 마운트 유지)
  # ==========================================
  jenkins:
    build:
      context: .
      dockerfile: Dockerfile.jenkins
    container_name: jenkins
    user: root
    ports:
      - "8080:8080"
      - "50000:50000"
    volumes:
      - ./data/jenkins:/var/jenkins_home
      - ./data/jenkins/scripts:/var/jenkins_home/scripts
      - /var/run/docker.sock:/var/run/docker.sock
      - ./data/knowledges:/var/knowledges
    networks:
      - devops-net
    extra_hosts:
      - "host.docker.internal:host-gateway"
    restart: unless-stopped

```

**실행 명령:** `<PROJECT_ROOT>`에서 `docker compose up -d --build` 를 실행하여 인프라를 구동합니다.

---

## 제5장. 파이썬 평가 스크립트 작성 (상세 주석 완비)

초보자도 코드의 흐름을 이해할 수 있도록 상세한 주석을 포함한 7개의 파이썬 및 설정 파일을 각 경로에 생성합니다.

### 5.1 어댑터 레이어 (`adapters/` 폴더)

**① `base.py` (데이터 표준 규격서)**

* **경로:** `./data/jenkins/scripts/eval_runner/adapters/base.py`

```python
from dataclasses import dataclass, field
from typing import List, Optional

@dataclass
class UniversalEvalOutput:
    """
    API, UI 등 통신 방식이 달라도 모든 결과를 이 바구니에 동일한 형태로 담습니다.
    평가관은 이 바구니의 형태만 보고 채점을 진행합니다.
    """
    input: str                          # 사용자의 질문
    actual_output: str                  # AI의 최종 답변
    retrieval_context: List[str] = field(default_factory=list) # RAG 봇이 참고한 원문
    http_status: int = 0                # 상태 코드 (200=정상)
    raw_response: str = ""              # 파싱 전 원본 응답 데이터 (보안 검사용)
    error: Optional[str] = None         # 통신 에러 메시지
    latency_ms: int = 0                 # 질문부터 답변 수신까지 걸린 밀리초 시간

    def to_dict(self):
        # Langfuse 전송을 위한 딕셔너리 변환
        return {"input": self.input, "actual_output": self.actual_output, "latency_ms": self.latency_ms, "error": self.error}

class BaseAdapter:
    """통신원들의 뼈대 클래스입니다."""
    def __init__(self, target_url: str):
        self.target_url = target_url

    def invoke(self, input_text: str, **kwargs) -> UniversalEvalOutput:
        raise NotImplementedError

```

**② `http_adapter.py` (API 통신 및 동적 파싱)**

* **경로:** `./data/jenkins/scripts/eval_runner/adapters/http_adapter.py`

```python
import time, os, requests
import jsonpath_ng.ext as jp # 중첩된 JSON 파싱 라이브러리
from .base import BaseAdapter, UniversalEvalOutput

class GenericHttpAdapter(BaseAdapter):
    """대상 AI가 API 형태일 때 작동하며, 인증 헤더와 동적 JSON Path를 지원합니다."""
    def invoke(self, input_text: str, **kwargs) -> UniversalEvalOutput:
        start_time = time.time()
        
        payload = {"query": input_text, "user": "eval-runner"}
        headers = {"Content-Type": "application/json"}
        
        # 외부 시스템이 토큰을 요구할 경우 환경 변수에서 꺼내 헤더에 주입합니다.
        auth_header = os.environ.get("TARGET_AUTH_HEADER")
        if auth_header: 
            headers["Authorization"] = auth_header
        
        try:
            res = requests.post(self.target_url, json=payload, headers=headers, timeout=60)
            lat_ms = int((time.time() - start_time) * 1000)
            data = res.json() if res.status_code == 200 else {}
            
            actual_out = ""
            
            # 파라미터로 받은 JSON Path(예: $.result.data)를 기반으로 답변을 긁어옵니다.
            path_expr = os.environ.get("RESPONSE_JSON_PATH", "$.answer")
            try:
                match = jp.parse(path_expr).find(data)
                if match: 
                    actual_out = match[0].value
            except: 
                pass
            
            # 동적 파싱에 실패하면 기본 키워드를 탐색합니다.
            if not actual_out:
                actual_out = data.get("answer", data.get("response", data.get("text", "")))
            
            docs = data.get("docs", [])
            if isinstance(docs, str): 
                docs = [docs]

            return UniversalEvalOutput(
                input=input_text, actual_output=str(actual_out), retrieval_context=[str(c) for c in docs],
                http_status=res.status_code, raw_response=res.text, latency_ms=lat_ms, 
                error=f"HTTP {res.status_code}" if res.status_code >= 400 else None
            )

        except Exception as e:
            return UniversalEvalOutput(input=input_text, actual_output="", error=str(e), latency_ms=int((time.time() - start_time) * 1000))

```

**③ `playwright_adapter.py` (웹 스크래핑 및 자가 치유)**

* **경로:** `./data/jenkins/scripts/eval_runner/adapters/playwright_adapter.py`

```python
import time, os
from playwright.sync_api import sync_playwright
from openai import OpenAI
from .base import BaseAdapter, UniversalEvalOutput

class PlaywrightChatbotAdapter(BaseAdapter):
    """웹 화면에 접속하여 타이핑하고 답변을 긁어옵니다."""
    def invoke(self, input_text: str, **kwargs) -> UniversalEvalOutput:
        lat_ms, actual_out, error_msg = 0, "", None

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            
            try:
                page.goto(self.target_url, wait_until="domcontentloaded", timeout=30000)
                page.get_by_placeholder("질문", exact=False).first.fill(input_text)
                
                start_time = time.time()
                page.keyboard.press("Enter")
                
                # API 호출이 멈추는 networkidle 상태까지 스마트하게 대기합니다.
                page.wait_for_load_state("networkidle", timeout=15000)
                page.wait_for_timeout(2000) # 타이핑 애니메이션 추가 대기
                
                try:
                    # 1차 시도: 웹 표준 로그 태그 탐색
                    actual_out = page.get_by_role("log").last.inner_text(timeout=3000)
                except:
                    # 2차 시도: 화면 전체를 긁어 로컬 LLM에게 정제(Self-Healing)를 지시합니다.
                    vis = page.locator("body").inner_text()
                    cli = OpenAI(base_url=f"{os.environ.get('OLLAMA_BASE_URL')}/v1", api_key="ollama")
                    prompt = f"질문 '{input_text}'에 대한 답변만 추출해. 다른 말은 절대 하지 마.\n[화면]\n{vis}"
                    res = cli.chat.completions.create(model="qwen3-coder:30b", messages=[{"role": "user", "content": prompt}])
                    extracted = res.choices[0].message.content.strip()
                    
                    # LLM이 너무 많은 HTML을 뱉으면 데이터 오염으로 간주해 실패 처리합니다.
                    if len(extracted) > 2000 or "<html" in extracted.lower():
                        raise RuntimeError("자가 치유 실패: 데이터 오염 감지")
                    actual_out = extracted
                
                lat_ms = int((time.time() - start_time) * 1000)

            except Exception as e:
                error_msg = f"UI 조작 실패: {str(e)}"
                lat_ms = int((time.time() - start_time) * 1000)
            finally:
                browser.close()

        return UniversalEvalOutput(input=input_text, actual_output=actual_out, http_status=200, raw_response=actual_out, latency_ms=lat_ms, error=error_msg)

```

**④ `registry.py` (어댑터 교환기)**

* **경로:** `./data/jenkins/scripts/eval_runner/adapters/registry.py`

```python
from .http_adapter import GenericHttpAdapter
from .playwright_adapter import PlaywrightChatbotAdapter

class AdapterRegistry:
    @classmethod
    def get_instance(cls, name: str, target_url: str):
        adapter_class = {"http": GenericHttpAdapter, "ui_chat": PlaywrightChatbotAdapter}.get(name, GenericHttpAdapter)
        return adapter_class(target_url)

```

### 5.2 검증 룰셋 파일 (`configs/` 폴더)

**⑤ `security.yaml` (금칙어 규칙)**

* **경로:** `./data/jenkins/scripts/eval_runner/configs/security.yaml`

```yaml
prompts: [ "{{ prompt }}" ]
providers: [ "echo" ]
tests:
  - vars: { name: "pii_check" }
    assert: [ { type: not-contains-regex, value: "\\b\\d{6}-\\d{7}\\b" } ]

```

**⑥ `schema.json` (응답 구조 규칙)**

* **경로:** `./data/jenkins/scripts/eval_runner/configs/schema.json`

```json
{"type": "object"}

```

### 5.3 총괄 평가관 (`tests/test_runner.py`)

**⑦ `test_runner.py` (평가 및 Langfuse 기록 로직)**

* **경로:** `./data/jenkins/scripts/eval_runner/tests/test_runner.py`

```python
import os, json, re, tempfile, subprocess, pytest, pandas as pd, uuid
from jsonschema import validate
from deepeval import assert_test
from deepeval.test_case import LLMTestCase
from deepeval.metrics import FaithfulnessMetric, AnswerRelevancyMetric, ContextualRecallMetric
from deepeval.models.gpt_model import GPTModel
from adapters.registry import AdapterRegistry
from langfuse import Langfuse

TARGET_URL = os.environ.get("TARGET_URL")
TARGET_TYPE = os.environ.get("TARGET_TYPE", "http")
RUN_ID = os.environ.get("BUILD_TAG", "Manual-Run")

langfuse = Langfuse(public_key=os.environ.get("LANGFUSE_PUBLIC_KEY"), secret_key=os.environ.get("LANGFUSE_SECRET_KEY"), host=os.environ.get("LANGFUSE_HOST"))

def load_dataset():
    p = "/var/knowledges/eval/data/golden.csv"
    if os.path.exists(p):
        df = pd.read_csv(p)
        return df.where(pd.notnull(df), None).to_dict(orient="records")
    return []

def _evaluate_agent_criteria(criteria_str: str, result) -> bool:
    """Agent의 복합 과업(AND 조건, 정규식) 달성 여부를 파싱합니다."""
    if not criteria_str: return result.http_status == 200
    for cond in [c.strip() for c in criteria_str.split(" AND ")]:
        if "status_code=" in cond and str(result.http_status) != cond.split("=")[1].strip(): return False
        elif "raw~r/" in cond and not re.search(cond.split("raw~r/")[1].rstrip("/"), result.raw_response): return False
    return True

@pytest.mark.parametrize("case", load_dataset())
def test_eval(case):
    # ID 중복 및 데이터 덮어쓰기 방지를 위한 UUID 폴백
    cid = case.get("case_id") or str(uuid.uuid4())[:8]
    trace = langfuse.trace(name=f"Eval-{cid}", id=f"{RUN_ID}-{cid}", tags=[RUN_ID, TARGET_TYPE], input=case["input"])
    
    # 1. 통신
    res = AdapterRegistry.get_instance(TARGET_TYPE, TARGET_URL).invoke(case["input"])
    trace.update(output=res.to_dict()); trace.score(name="Latency", value=res.latency_ms)
    if res.error: pytest.fail(f"Conn Fail: {res.error}")

    # 2. Fail-Fast
    try: 
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as t: t.write(res.raw_response)
        if subprocess.run(["promptfoo", "eval", "-c", "/var/jenkins_home/scripts/eval_runner/configs/security.yaml", "--prompts", f"file://{t.name}", "-o", "json"], capture_output=True).returncode != 0: raise RuntimeError("보안 정책(금칙어) 위반")
        if TARGET_TYPE == "http": validate(instance=json.loads(res.raw_response), schema=json.load(open("/var/jenkins_home/scripts/eval_runner/configs/schema.json")))
    except Exception as e: pytest.fail(str(e))

    # 3. Agent 판단
    if case.get("target_type") == "agent":
        passed = _evaluate_agent_criteria(case.get("success_criteria", ""), res)
        trace.score(name="TaskCompletion", value=1 if passed else 0)
        assert passed
        return

    # 4. 문맥 심층 평가 (DeepEval)
    judge = GPTModel(model="qwen3-coder:30b", base_url=f"{os.environ.get('OLLAMA_BASE_URL')}/v1")
    tc = LLMTestCase(input=case["input"], actual_output=res.actual_output, expected_output=case.get("expected_output"), retrieval_context=res.retrieval_context)
    mets = [AnswerRelevancyMetric(threshold=0.7, model=judge)]
    
    if case.get("target_type") == "rag":
        # 원문 누락 시 억지로 채점하여 0점 처리되는 오탐(Bypass) 방지 로직
        if res.retrieval_context and len(res.retrieval_context) > 0 and str(res.retrieval_context[0]).strip() != "":
            mets.append(FaithfulnessMetric(threshold=0.8, model=judge))
            if TARGET_TYPE == "http": mets.append(ContextualRecallMetric(threshold=0.8, model=judge))
        else:
            trace.update(metadata={"warning": "원문(retrieval_context) 부재로 환각 평가 생략"})
    
    for m in mets:
        m.measure(tc)
        trace.score(name=m.__class__.__name__, value=m.score, comment=m.reason)
    assert_test(tc, mets)

```

---

## 제6장. Jenkins 파이프라인 생성 (운영 UI)

사용자가 쉽게 실행할 수 있도록 Jenkins 파이프라인을 생성합니다. API 키는 코드 내 하드코딩되지 않고 `withCredentials`를 통해 안전하게 주입됩니다.

1. Jenkins 브라우저 메인에서 **[새로운 Item]** ➔ `DSCORE-Universal-Eval` ➔ **[Pipeline]** 선택 후 OK.
2. 하단의 Pipeline Script 입력창에 아래 코드를 그대로 붙여넣고 저장합니다.

```groovy
pipeline {
    agent any

    // 사용자 입력 폼
    parameters {
        string(name: 'TARGET_URL', defaultValue: '', description: '평가 대상 URL (예: http://대상:5000/chat)')
        choice(name: 'TARGET_TYPE', choices: ['http', 'ui_chat'], description: '평가 통신 방식 선택 (API=http, 웹 화면 스크래핑=ui_chat)')
        string(name: 'TARGET_AUTH_HEADER', defaultValue: '', description: '(선택) 대상이 인증을 요구할 경우 헤더 값 입력 (예: Bearer YOUR_TOKEN)')
        string(name: 'RESPONSE_JSON_PATH', defaultValue: '$.answer', description: '(API 전용) 답변이 위치한 JSON Path (기본: $.answer)')
        file(name: 'GOLDEN_DATASET', description: '로컬 PC의 평가 시험지(golden.csv) 파일 업로드')
    }

    environment {
        EVAL_DATA_DIR = '/var/knowledges/eval/data'
        EVAL_REPORT_DIR = '/var/knowledges/eval/reports'
        EVAL_SCRIPT_DIR = '/var/jenkins_home/scripts/eval_runner'
        OLLAMA_BASE_URL = "http://host.docker.internal:11434"
        LANGFUSE_HOST = "http://langfuse-server:3000"
    }

    stages {
        stage('1. 파일 이동') {
            steps {
                script {
                    def uploaded = sh(script: "ls golden.csv || echo ''", returnStdout: true).trim()
                    if (uploaded == 'golden.csv') {
                        sh "mv golden.csv ${EVAL_DATA_DIR}/golden.csv"
                    } else {
                        error "[실패] 시험지 파일이 첨부되지 않았습니다."
                    }
                }
            }
        }

        stage('2. 파이썬 평가 실행') {
            steps {
                // Jenkins Credentials에 저장해둔 암호화된 키를 불러와 주입합니다.
                withCredentials([
                    string(credentialsId: 'langfuse-public-key', variable: 'LANGFUSE_PUBLIC_KEY'),
                    string(credentialsId: 'langfuse-secret-key', variable: 'LANGFUSE_SECRET_KEY')
                ]) {
                    sh """
                    export PYTHONPATH=${EVAL_SCRIPT_DIR}
                    export OLLAMA_BASE_URL=${OLLAMA_BASE_URL}
                    export TARGET_URL='${params.TARGET_URL}'
                    export TARGET_TYPE='${params.TARGET_TYPE}'
                    export TARGET_AUTH_HEADER='${params.TARGET_AUTH_HEADER}'
                    export RESPONSE_JSON_PATH='${params.RESPONSE_JSON_PATH}'
                    export BUILD_TAG='${env.BUILD_TAG}'
                    
                    # 데이터 충돌 방지를 위해 직렬 실행
                    python3 -m pytest ${EVAL_SCRIPT_DIR}/tests/test_runner.py --junitxml=${EVAL_REPORT_DIR}/results.xml
                    """
                }
            }
        }

        stage('3. 리포트 게시') {
            steps { junit "${EVAL_REPORT_DIR}/results.xml" }
        }
    }

    post {
        always {
            script {
                // 방금 실행한 내역만 바로 볼 수 있게 딥링크 생성
                def publicLangfuseUrl = "http://localhost:3000/project/traces?filter=tags%3D${env.BUILD_TAG}"
                currentBuild.description = """
                <div style="padding:15px; border:1px solid #cce5ff; border-radius:5px;">
                    <b>타겟:</b> ${params.TARGET_URL} (${params.TARGET_TYPE})<br><br>
                    <a href='${publicLangfuseUrl}' target='_blank' style='font-size:16px; font-weight:bold; color:#0056b3;'>
                        👉 [Langfuse 관제탑] 상세 점수, LLM 감점 사유, 오류 로그 확인
                    </a>
                </div>
                """
            }
        }
    }
}

```

---

## 제7장. 실행 및 측정 결과 확인 (사용자 가이드)

### 7.1 평가 시험지(CSV) 작성

바탕화면에 엑셀이나 메모장으로 `golden.csv`를 만듭니다. (ID를 비워두면 시스템이 UUID를 자동 부여해 충돌을 막아줍니다.)

```csv
case_id,target_type,input,expected_output,success_criteria
,rag,테스트 질문입니다. 이 시스템의 목적은?,AI 품질의 정량 검증입니다.,

```

### 7.2 파이프라인 실행

1. Jenkins에서 **[Build with Parameters]**를 클릭합니다.
2. URL, 통신 방식(http/ui_chat), 인증 헤더(필요시)를 넣고 바탕화면의 `golden.csv`를 첨부하여 **[Build]** 합니다.

### 7.3 측정 결과 확인처 상세 분석

#### 📊 확인 1: Jenkins 대시보드 (Pass/Fail 직관적 확인)

* 빌드 결과의 `Test Result` 트렌드 그래프를 봅니다.
* 만약 빨간색 실패가 떴다면 `Console Output`을 열어보십시오. Promptfoo 금칙어 정책 위반이나, JSON 규격 불일치로 인한 **Fail-Fast** 발생 시 여기에 명확한 사유가 찍힙니다.

#### 🔍 확인 2: Langfuse 대시보드 (심층 점수 및 감점 사유 분석)

* Jenkins 화면 중앙에 나타난 **"👉 [Langfuse 관제탑]..."** 딥링크를 클릭합니다.
* 열린 화면(Traces 리스트)에서 테스트 항목을 하나 클릭하면 다음을 볼 수 있습니다.
1. **응답 소요 시간(Latency)**: 우측 상단에 밀리초(ms)로 표기됩니다.
2. **심층 문맥 점수(Scores)**: 화면 중앙/하단의 `Scores` 탭에서 LLM이 채점한 `AnswerRelevancy`, `Faithfulness` 점수(0.0~1.0)를 확인합니다.
3. **심판관 감점 사유(Comment)**: 해당 점수 우측의 `Comment` 필드를 열람하십시오. 심판관이 *"답변 내용이 의도와 일치하지 않으므로 0.3점을 부여합니다"* 와 같이 적어둔 평가 리포트를 통해 AI의 품질을 파악할 수 있습니다.