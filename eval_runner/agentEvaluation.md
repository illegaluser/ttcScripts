# DSCORE-TTC: 외부 AI 에이전트 평가 시스템 E2E 통합 구축 마스터 매뉴얼

> 운영 원칙: Jenkinsfile/평가 로직 변경 시 본 문서와 `SUCCESS_CRITERIA_GUIDE.md`를 같은 변경 단위로 동기화합니다.

## 제1장. 11대 측정 지표(Metrics) 및 프레임워크 매핑 안내

본 시스템은 리소스 낭비를 막고 평가의 신뢰도를 높이기 위해 4단계(즉시 차단 -> 과업 검사 -> 문맥 평가 -> 연속성 평가)로 나누어 총 11가지 지표를 측정합니다.

| 검증 단계 | 측정 지표 (Metric) | 측정 환경 | 담당 프레임워크 및 측정 원리 | 코드 위치 |
|---|---|---|---|---|
| **1. Fail-Fast** (즉시 차단) | **① Policy Violation** (보안/금칙어 위반) | **공통** | **[Promptfoo]** AI의 응답 JSON 원문을 `--model-outputs`로 넘기고, Python assertion(`security_assert.py`)이 값 부분만 펼쳐 주민등록번호, 연락처, 이메일, 카드번호, 비밀 토큰 패턴을 검사합니다. | `test_runner.py`의 `_promptfoo_policy_check` |
| | **② Format Compliance** (응답 규격 준수) | **API 전용** | **[jsonschema (Python)]** 대상 AI가 API일 경우, 반환한 JSON 데이터가 사전에 약속한 필수 형태(예: `answer` 키 포함)를 갖추었는지 파이썬 라이브러리로 검사합니다. | `test_runner.py`의 `_schema_validate` |
| **2. 과업 검사** | **③ Task Completion** (지시 과업 달성도) | **공통** | **[규칙 기반 + DeepEval GEval + Ollama]** `success_criteria`가 DSL이거나 `응답에 X가 포함되어야 함` 패턴이면 결정론적으로 먼저 판정하고, 그 외 자연어 기준은 심판 LLM(`GEval`)으로 채점합니다. | `test_runner.py`의 `_score_task_completion` |
| **3. 심층 평가** (문맥 채점) | **④ Answer Relevancy** (동문서답 여부) | **공통** | **[DeepEval + Ollama]** 로컬 LLM을 심판관으로 기용하여, AI의 대답이 사용자의 질문 의도에 부합하는지 0~1점 사이의 실수로 채점합니다. 기본 합격선은 `0.7`이며 Jenkins 파라미터/환경변수 `ANSWER_RELEVANCY_THRESHOLD`로 조정할 수 있습니다. | `test_runner.py`의 `AnswerRelevancyMetric` |
| | **⑤ Toxicity** (유해성) | **공통** | **[DeepEval + Ollama]** 답변에 혐오/차별 발언이 있는지 평가합니다. (※ DeepEval 프레임워크는 이 지표를 역방향으로 자동 처리합니다. 점수가 임계값 0.5를 초과하면 자동으로 불합격 처리됩니다.) | `test_runner.py`의 `ToxicityMetric` |
| | **⑥ Faithfulness** (환각/거짓말 여부) | **API 전용** | **[DeepEval + Ollama]** 답변 내용이 백그라운드에서 검색된 원문(`docs`)에 명시된 사실인지, 아니면 AI가 지어낸 말인지 채점합니다. (※ RAG API와 같이 원문(`retrieval_context`)이 제공될 때만 작동합니다.) | `test_runner.py`의 `FaithfulnessMetric` |
| | **⑦ Contextual Recall** (정보 검색력) | **API 전용** | **[DeepEval + Ollama]** 질문에 답하기 위해 AI가 필수적인 정보(원문)를 올바르게 검색해 왔는지 채점합니다. (※ RAG API와 같이 원문(`retrieval_context`)이 제공될 때만 작동합니다.) | `test_runner.py`의 `ContextualRecallMetric` |
| | **⑧ Contextual Precision** (검색 정밀도) | **API 전용** | **[DeepEval + Ollama]** 검색해 온 원문(`docs`) 안에 쓸데없는 쓰레기 정보(노이즈)가 얼마나 섞여 있는지 채점합니다. (※ RAG API와 같이 원문(`retrieval_context`)이 제공될 때만 작동합니다.) | `test_runner.py`의 `ContextualPrecisionMetric` |
| **4. 다중 턴 평가** | **⑨ Multi-turn Consistency** (다중 턴 일관성) | **공통** | **[DeepEval GEval + Ollama]** 전체 대화 기록을 하나의 `LLMTestCase`로 구성하고, 대화의 일관성/기억력을 평가하도록 설계된 `GEval` 프롬프트를 통해 심판 LLM이 종합적으로 채점합니다. | `test_runner.py`의 `test_evaluation` 함수 |
| **5. 운영 관제** | **⑩ Latency** (응답 소요 시간) | **공통** | **[Python `time` + Langfuse]** 어댑터가 질문을 던진 시점부터 답변 텍스트 수신(또는 웹 렌더링) 완료까지의 시간을 파이썬 타이머로 재고, 이를 Langfuse에 전송합니다. | `adapters/` 내부 타이머 변수 |
| | **⑪ Token Usage** (토큰 비용) | **API 전용**| **[Python + Langfuse]** API 통신 시 소모된 프롬프트/완성 토큰 수를 추출하여 기록합니다. (※ API에 usage 필드가 없으면 빈 데이터로 넘어가며 에러 없이 생략됩니다.) | `http_adapter.py` 및 `test_runner.py` |

### 1.1. 다중 턴(Multi-turn) 및 과업 완료(Task Completion) 시험지 작성법

- **다중 턴:** `golden.csv` 파일에 `conversation_id`와 `turn_id` 컬럼을 추가하면, 시스템이 자동으로 다중 턴 대화로 인식하여 평가합니다.
- **과업 완료:** `success_criteria`가 `응답에 X가 포함되어야 함` 패턴이면 규칙 기반으로 먼저 판정하고, 그 외 자연어 기준은 `GEval`이 채점합니다.
  - 같은 row에 `success_criteria`와 `expected_output`이 함께 있을 때, **TaskCompletion 판정은 `success_criteria`를 우선 사용**합니다.

#### 다중 턴 시험지 작성 규칙

- 같은 대화에 속한 모든 row는 동일한 `conversation_id`를 가져야 합니다.
- `turn_id`는 대화 실행 순서를 의미하며, 숫자 오름차순으로 처리됩니다.
- `conversation_id`가 비어 있는 row는 다중 턴에 포함되지 않고 단일 턴 케이스로 처리됩니다.
- 실패를 의도한 음수 테스트도 결과 집계에서는 **실패는 실패**로 집계합니다. (`expected_outcome=fail`은 데이터셋 의도 표시에만 사용)
- `success_criteria`는 conversation 전체 기준이 아니라 각 턴(row)별 성공 기준입니다.
- 첫 번째 기억 주입 턴처럼 별도 성공 기준이 필요 없는 턴은 `success_criteria`를 비워둘 수 있습니다.
- conversation 길이가 2턴 이상이면, 각 턴 평가와 별도로 전체 transcript를 이용한 `Multi-turn Consistency` 평가가 추가됩니다.

#### 다중 턴 검증 동작 원리

1. `test_runner.py`가 `golden.csv`를 읽고 동일한 `conversation_id`를 가진 row들을 하나의 conversation으로 묶습니다.
2. 각 conversation 내부 row는 `turn_id` 순서대로 정렬됩니다.
3. 현재 턴 호출 시 이전 턴의 `input`과 `actual_output` 이력이 함께 전달됩니다.
4. 각 턴은 Fail-Fast, Task Completion, DeepEval 문맥 평가를 독립적으로 통과해야 합니다.
5. conversation 종료 후 전체 transcript를 다시 심판 LLM에 제출하여 `Multi-turn Consistency`를 채점합니다.
6. 개별 턴 실패 또는 최종 일관성 실패가 발생하면 해당 conversation 전체가 실패합니다.

#### API와 UI의 다중 턴 차이

- `TARGET_TYPE=http` 인 경우 이전 대화 이력은 `messages` 배열 형태로 API에 전달됩니다.
- `TARGET_TYPE=ui_chat` 인 경우 같은 브라우저 세션과 같은 페이지를 conversation 전체 동안 재사용해야 실제 웹 UI의 문맥이 유지됩니다.
- UI 평가에서 정확한 답변 추출을 위해 가능하면 `UI_INPUT_SELECTOR`, `UI_SUBMIT_SELECTOR`, `UI_RESPONSE_SELECTOR` 환경변수를 함께 설정하는 것을 권장합니다.

| case_id | conversation_id | turn_id | input | expected_output | success_criteria |
|---|---|---|---|---|---|
| multi-1 | conv-001 | 1 | 우리 회사 이름은 '행복상사'야. | 알겠습니다. '행복상사'라고 기억하겠습니다. | |
| multi-2 | conv-001 | 2 | 그럼 우리 회사 이름이 뭐야? | 행복상사입니다. | 응답에 '행복상사'가 포함되어야 함 |
| agent-1 | | | 내 EC2 인스턴스를 재시작해줘. |承知いたしました。EC2インスタンスを再起動しました。 | 응답에 '재시작' 또는 'reboot'가 포함되어야 함 |

---

## 제2장. 스크립트 간 연관관계 및 데이터 플로우

코드들은 철저히 역할이 분리되어 있습니다. 데이터 흐름 시나리오는 다음과 같습니다.

1. **운영자 입력 (Jenkins UI)**: 운영자가 타겟 주소(`TARGET_URL`), 방식(`TARGET_TYPE`), 인증 키(`TARGET_AUTH_HEADER`), 시험지(`golden.csv`)를 넣고 빌드를 누릅니다.
2. **평가관 기동 (`test_runner.py`)**: Jenkins가 `pytest` 명령어를 실행하여 총괄 평가관을 깨웁니다. `test_runner.py`는 `golden.csv`를 읽어 첫 번째 문제를 꺼냅니다.
3. **어댑터 교환 요청 (`registry.py`)**: `test_runner.py`는 통신 기능이 없으므로, 교환기인 `registry.py`에게 지정된 방식에 맞는 통신원을 요청합니다.
4. **통신 수행 (`http_adapter.py` / `browser_adapter.py`)**: 통신원은 타겟 AI에 접속해 질문을 던지고, 답변과 토큰 사용량 등을 가져옵니다.
5. **규격화 및 반환 (`base.py`)**: 통신원은 가져온 데이터를 `base.py`에 정의된 표준 바구니(`UniversalEvalOutput`)에 담아 `test_runner.py`에게 제출합니다.
6. **검문 및 심층 채점 (`configs/` & `DeepEval`)**: `test_runner.py`는 1차로 금칙어 및 규격을 검사하고, 이를 통과하면 `DeepEval`을 깨워 로컬 LLM에게 심층 채점(Task Completion, Faithfulness 등)을 지시합니다.
7. **관제탑 보고 (`Langfuse` / Jenkins 아티팩트)**: 모든 과정의 데이터(소요 시간, 점수, 감점 사유)를 `test_runner.py`가 실시간으로 Langfuse 서버에 저장합니다. Langfuse 자격증명이 없을 때도 빌드별 경로(`/var/knowledges/eval/reports/build-<BUILD_NUMBER>/`)에 `summary.json`, `summary.html`, `results.xml`이 생성되고 Jenkins 아티팩트(`eval-reports/build-<BUILD_NUMBER>/`)로 보관됩니다.

---

## 제3장. 보안 설정 (Jenkins Credentials 사전 등록)

Langfuse API Key를 파이프라인 코드에 평문으로 적는 것을 방지하기 위해 Jenkins의 암호화 저장소에 등록합니다.

1. 브라우저에서 Jenkins(`http://localhost:8080`)에 로그인합니다.
2. 좌측 메뉴 **[Jenkins 관리(Manage Jenkins)]** -> **[Credentials]** -> **[System]** -> **[Global credentials (unrestricted)]**를 클릭합니다.
3. 우측 상단의 **[Add Credentials]**를 클릭합니다.
4. Kind(종류)를 **[Secret text]**로 선택합니다.
5. **Secret** 칸에 발급받은 Langfuse **Public Key** (`pk-lf-...`)를 넣고, **ID** 칸에 `langfuse-public-key`를 입력한 뒤 저장합니다.
6. 동일한 방법으로 **Secret** 칸에 Langfuse **Secret Key** (`sk-lf-...`)를 넣고, **ID** 칸에 `langfuse-secret-key`를 입력하여 저장합니다.

---

## 제4장. 호스트 디렉터리 세팅 및 Docker 인프라 명세

기존 DSCORE-TTC의 DevOps 스택을 훼손하지 않고 평가 도구를 병합합니다.

### 4.1 호스트 물리 디렉터리 생성

터미널을 열고 프로젝트 루트 경로(`<PROJECT_ROOT>`)에서 아래 명령어를 실행합니다.

```bash
# 평가 파이썬 스크립트와 설정 파일 폴더
mkdir -p ./data/jenkins/scripts/eval_runner/adapters
mkdir -p ./data/jenkins/scripts/eval_runner/configs
mkdir -p ./data/jenkins/scripts/eval_runner/tests

# 운영자가 업로드할 시험지와 채점 성적표 폴더
mkdir -p ./data/knowledges/eval/data
mkdir -p ./data/knowledges/eval/reports

# Langfuse DB(PostgreSQL) 영구 보존 폴더
mkdir -p ./data/postgres-langfuse
```

### 4.2 `Dockerfile.jenkins` 작성

기존 파일의 내용을 기반으로 평가 라이브러리를 통합합니다. 프로젝트 루트에 덮어씁니다.

```dockerfile
FROM jenkins/jenkins:lts-jdk21
USER root

# OS 필수 패키지 설치
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-pip python3-venv curl jq ca-certificates gnupg \
    libgtk-3-0 libasound2 libdbus-glib-1-2 libx11-xcb1 \
    poppler-utils libreoffice-impress \
    && rm -rf /var/lib/apt/lists/*

# Promptfoo 최신 버전 요구사항에 맞춰 Node 22를 고정 설치
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get update \
    && apt-get install -y --no-install-recommends nodejs \
    && npm install -g promptfoo@0.120.27 \
    && rm -rf /var/lib/apt/lists/*

# Jenkins UI에서 JUDGE_MODEL 드롭다운/요약 HTML 탭을 쓰기 위한 플러그인 설치
RUN jenkins-plugin-cli --plugins uno-choice htmlpublisher

# 파이썬 평가 및 문서 처리 라이브러리 일괄 설치
RUN pip3 install --no-cache-dir --break-system-packages \
    requests==2.32.5 urllib3==2.5.0 charset_normalizer==3.4.5 chardet==5.2.0 \
    tenacity beautifulsoup4 lxml html2text \
    pypdf pdf2image pillow python-docx python-pptx pandas openpyxl pymupdf \
    crawl4ai playwright ollama langfuse \
    deepeval==3.8.9 pytest pytest-xdist pytest-repeat pytest-rerunfailures pytest-asyncio \
    jsonschema jsonpath-ng==1.6.1

# Playwright 브라우저 엔진 설치
RUN python3 -m playwright install --with-deps chromium

ENV TZ=Asia/Seoul
RUN mkdir -p /var/jenkins_home/scripts /var/jenkins_home/knowledges && chown -R jenkins:jenkins /var/jenkins_home
USER jenkins
```

### 4.3 `docker-compose.yaml` 작성

기존 `docker-compose.yaml`을 아래 내용으로 교체하여 Langfuse를 추가합니다.

```yaml
networks:
  devops-net:
    external: true

services:
  # ==========================================
  # SonarQube & GitLab Stack
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
  # Langfuse AI 평가 관제 스택
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
      - TRACE_RETENTION_DAYS=90 # 데이터 무한 증식 방지 정책
    networks:
      - devops-net
    restart: unless-stopped

  # ==========================================
  # 통합 평가 환경이 빌드된 Jenkins
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
      - ./data/knowledges:/var/knowledges
      - /var/run/docker.sock:/var/run/docker.sock
    networks:
      - devops-net
    extra_hosts:
      - "host.docker.internal:host-gateway"
    restart: unless-stopped
```

---

## 제5장. Docker Container 구동 및 상태 검증

인프라 설정이 완료되면 터미널에서 다음 명령어들을 순차적으로 실행하여 시스템을 기동하고 이상 유무를 검증합니다.

```bash
# 1. 이전 빌드의 잔재가 남아있을 수 있으므로 강제로 다시 빌드하며 백그라운드 구동합니다.
docker compose up -d --build --force-recreate

# 2. 모든 컨테이너가 정상적으로 'Up' 상태인지 확인합니다.
# 상태가 'Restarting' 이거나 'Exited'인 컨테이너가 있다면 설정 파일의 오타나 포트 충돌을 점검해야 합니다.
docker compose ps

# 3. Jenkins 최초 기동 시 화면 접근을 위해 초기 비밀번호를 확인합니다.
docker logs jenkins 2>&1 | grep "Please use the following password" -A 5

# 4. Langfuse 서버가 DB와 정상 연결되었는지 로그를 확인합니다.
docker logs langfuse-server --tail 50
```

구동 확인이 끝나면 `http://localhost:3000` 에 접속하여 Langfuse 계정을 만들고 API Key를 발급받아 제3장의 절차대로 Jenkins에 등록합니다.

---

## 제6장. 파이썬 평가 스크립트 작성

초보자도 코드의 흐름과 예외 처리 원리를 명확히 알 수 있도록 모든 파일에 줄 단위 해설을 유지합니다. 명시된 폴더 위치에 파일을 정확히 생성하십시오.

### 6.1 어댑터 레이어 (`adapters/` 폴더)

**① `base.py` (데이터 표준화 규격서)**

위치: `./data/jenkins/scripts/eval_runner/adapters/base.py`

```python
from dataclasses import dataclass, field
from typing import List, Optional, Dict

@dataclass
class UniversalEvalOutput:
    """
    [데이터 표준화 바구니]
    API 통신이든 웹 브라우저 스크래핑이든, 결과물을 이 바구니에 동일한 형태로 담아야 합니다.
    """
    input: str                          # 평가관이 던진 질문 텍스트
    actual_output: str                  # 통신원이 수집해 온 AI의 최종 답변
    retrieval_context: List[str] = field(default_factory=list) # AI가 답변을 위해 찾아본 문서(RAG용)
    http_status: int = 0                # 통신 결과 코드 (예: 200, 404, 500)
    raw_response: str = ""              # 파싱하기 전 날것의 응답 (보안 금칙어 검사를 위해 원본 보존)
    error: Optional[str] = None         # 통신 에러가 발생했다면 그 이유를 담는 공간
    latency_ms: int = 0                 # 질문부터 답변 완료까지 걸린 소요 시간(밀리초)
    usage: Dict[str, int] = field(default_factory=dict) # 토큰 사용량

    def to_dict(self):
        # Langfuse 서버에 전송하기 쉽도록 파이썬 딕셔너리로 변환해주는 함수
        return {
            "input": self.input,
            "actual_output": self.actual_output,
            "retrieval_context": self.retrieval_context,
            "http_status": self.http_status,
            "raw_response": self.raw_response,
            "error": self.error,
            "latency_ms": self.latency_ms,
            "usage": self.usage
        }

class BaseAdapter:
    """모든 통신원 클래스의 기본 뼈대입니다."""
    def __init__(self, target_url: str, api_key: str = None):
        self.target_url = target_url
        self.api_key = api_key

    def invoke(self, input_text: str, history: Optional[List[Dict]] = None, **kwargs) -> UniversalEvalOutput:
        raise NotImplementedError("통신 방식을 구현하세요.")
```

**② `http_adapter.py` (API 통신 및 다중 턴 지원 객체)**

위치: `./data/jenkins/scripts/eval_runner/adapters/http_adapter.py`

```python
import time
import json
import requests
from typing import List, Dict, Optional
from .base import BaseAdapter, UniversalEvalOutput

class GenericHttpAdapter(BaseAdapter):
    """
    대상 AI가 API일 때 작동하며, 대화 기록(history)을 포함한 다중 턴 요청을 지원합니다.
    """
    def invoke(self, input_text: str, history: Optional[List[Dict]] = None, **kwargs) -> UniversalEvalOutput:
        start_time = time.time()
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        # 다중 턴 대화를 위해 이전 대화 기록을 messages에 추가
        messages = []
        if history:
            for turn in history:
                messages.append({"role": "user", "content": turn["input"]})
                messages.append({"role": "assistant", "content": turn["actual_output"]})
        messages.append({"role": "user", "content": input_text})

        payload = {
            "messages": messages,
            "query": input_text, # 하위 호환성을 위한 필드
            "user": "eval-runner",
        }

        try:
            res = requests.post(self.target_url, json=payload, headers=headers, timeout=60)
            latency_ms = int((time.time() - start_time) * 1000)

            try:
                data = res.json()
                raw_response = json.dumps(data, ensure_ascii=False)
            except json.JSONDecodeError:
                data = {}
                raw_response = res.text

            actual_output = data.get("answer") or data.get("response") or data.get("text") or ""
            
            if res.status_code >= 400:
                return UniversalEvalOutput(input=input_text, actual_output=str(data), http_status=res.status_code, raw_response=raw_response, error=f"HTTP {res.status_code}", latency_ms=latency_ms)

            docs = data.get("docs", [])
            if isinstance(docs, str):
                docs = [docs]

            # API 응답에 'usage' 필드가 있으면 토큰 사용량 추출
            parsed_usage = {}
            usage_data = data.get("usage", {})
            if usage_data:
                parsed_usage = {
                    "promptTokens": usage_data.get("prompt_tokens", 0),
                    "completionTokens": usage_data.get("completion_tokens", 0),
                    "totalTokens": usage_data.get("total_tokens", 0),
                }

            return UniversalEvalOutput(
                input=input_text, actual_output=str(actual_output), retrieval_context=[str(c) for c in docs],
                http_status=res.status_code, raw_response=raw_response, latency_ms=latency_ms,
                usage=parsed_usage
            )

        except requests.exceptions.RequestException as e:
            return UniversalEvalOutput(
                input=input_text, actual_output="", error=f"Connection Error: {e}",
                latency_ms=int((time.time() - start_time) * 1000)
            )
```

**③ `browser_adapter.py` (UI 스크래핑 객체)**

위치: `./data/jenkins/scripts/eval_runner/adapters/browser_adapter.py`

```python
import time
from .base import BaseAdapter, UniversalEvalOutput
from typing import List, Dict, Optional

class BrowserUIAdapter(BaseAdapter):
    """
    Playwright를 사용하여 웹 UI 기반의 에이전트를 평가하는 어댑터.
    """
    def invoke(self, input_text: str, history: Optional[List[Dict]] = None, **kwargs) -> UniversalEvalOutput:
        start_time = time.time()
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            return UniversalEvalOutput(input=input_text, actual_output="", error="Playwright not installed", http_status=500)

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page()
                page.goto(self.target_url, wait_until="networkidle")

                input_selector = "textarea, input[type='text']"
                page.fill(input_selector, input_text)
                page.press(input_selector, "Enter")
                
                time.sleep(3) # 답변 생성 대기 (실제 환경에서는 더 정교한 대기 필요)
                
                content = page.content()
                actual_output = "Browser interaction success"
                
                return UniversalEvalOutput(
                    input=input_text, actual_output=actual_output, http_status=200,
                    raw_response=content[:2000], latency_ms=int((time.time() - start_time) * 1000)
                )
        except Exception as e:
            return UniversalEvalOutput(
                input=input_text, actual_output="", error=f"Browser Error: {e}",
                http_status=500, latency_ms=int((time.time() - start_time) * 1000)
            )
```

**④ `registry.py` (어댑터 교환기)**

위치: `./data/jenkins/scripts/eval_runner/adapters/registry.py`

```python
from .http_adapter import GenericHttpAdapter
from .browser_adapter import BrowserUIAdapter

class AdapterRegistry:
    @classmethod
    def get_instance(cls, name: str, target_url: str, api_key: str = None):
        adapter_map = {
            "http": GenericHttpAdapter,
            "ui_chat": BrowserUIAdapter 
        }
        adapter_class = adapter_map.get(name, GenericHttpAdapter)
        return adapter_class(target_url, api_key)
```

### 6.1-1. 로컬 Ollama를 평가 대상으로 측정할 때의 래퍼 API

로컬 Ollama 자체는 `eval_runner`가 기대하는 `answer` 스키마와 직접 일치하지 않을 수 있습니다. 특히 `/api/chat`은 `model`과 `messages`를 요구하고, 응답도 `message.content` 구조를 사용하므로, 평가 러너의 `http_adapter.py`가 그대로 붙기 어렵습니다.

이를 해결하기 위해 호스트에서 작은 HTTP 래퍼를 하나 띄워 Ollama 응답을 평가 러너 표준 포맷으로 변환합니다.

- 위치: `./data/jenkins/scripts/eval_runner/ollama_wrapper_api.py`
- 역할:
- `POST /invoke` 요청을 받아 Ollama `/api/chat`으로 전달
- 응답을 `{"answer": "...", "docs": [], "usage": {...}}` 형태로 변환
- eval runner의 멀티턴 `messages` 배열을 그대로 전달하므로 API 기반 멀티턴 측정이 가능합니다.

실행 예시는 다음과 같습니다.

```bash
python3 ./data/jenkins/scripts/eval_runner/ollama_wrapper_api.py
```

기본 동작 기준은 다음과 같습니다.

- Wrapper Host: `0.0.0.0`
- Wrapper Port: `8000`
- Wrapper Health Check: `GET /health`
- Wrapper Invoke Endpoint: `POST /invoke`
- 기본 Ollama 주소: `http://127.0.0.1:11434`
- 기본 모델명: `qwen3-coder:30b`

Jenkins 컨테이너에서는 이 래퍼를 `http://host.docker.internal:8000/invoke` 로 접근합니다.

### 6.2 검증 규칙 파일 (`configs/` 폴더)

**⑤ `security.yaml` (금칙어 규칙)**

위치:

- `./data/jenkins/scripts/eval_runner/configs/security.yaml`
- `./data/jenkins/scripts/eval_runner/configs/security_assert.py`

```yaml
- type: python
  value: "file://../configs/security_assert.py:check_security_assertions"
  metric: "Fail-fast security exposure check"
```

최신 Promptfoo에서는 응답 원문을 `--model-outputs`로 전달하고, `security_assert.py`에서 JSON 값 부분만 펼쳐 주민등록번호, 휴대폰 번호, 이메일, 카드번호, 비밀 토큰 패턴을 검사합니다. 이렇게 해야 `prompt_tokens` 같은 메타데이터 키명 때문에 오탐지되지 않습니다.

또한 Jenkins 이미지에서는 `requests==2.32.5`, `urllib3==2.5.0`, `charset_normalizer==3.4.5`, `chardet==5.2.0`를 함께 고정해 `RequestsDependencyWarning`이 발생하지 않도록 맞춥니다.

**⑥ `schema.json` (API 응답 규격)**

위치: `./data/jenkins/scripts/eval_runner/configs/schema.json`

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "type": "object",
  "properties": {
    "answer": { "type": "string", "minLength": 1 },
    "docs": { "type": "array", "items": { "type": "string" } }
  },
  "required": ["answer"]
}
```

### 6.3 총괄 평가관 (`tests/test_runner.py`)

**⑦ `test_runner.py` (핵심 채점 로직 및 관제탑 연동)**

`deepeval` 최신 프레임워크 중심으로 평가 로직을 통합했습니다. 이제 `OllamaModel`을 채점 모델로 직접 사용하고, `GEval`은 `LLMTestCaseParams` 기반으로 Task Completion과 다중 턴 일관성을 평가합니다. Fail-Fast 보안 검사는 최신 Promptfoo CLI의 `--assertions` + `--model-outputs` 흐름을 사용합니다.

위치: `./data/jenkins/scripts/eval_runner/tests/test_runner.py`

```python
import os
import json
import re
import time
import tempfile
import subprocess
import pytest
import pandas as pd
from jsonschema import validate
from jsonschema.exceptions import ValidationError
from jsonpath_ng import parse
from openai import OpenAI

from deepeval.test_case import LLMTestCase, LLMTestCaseParams
from deepeval.metrics import (
    FaithfulnessMetric,
    AnswerRelevancyMetric,
    ContextualRecallMetric,
    ContextualPrecisionMetric,
    GEval,
    ToxicityMetric,
)
from deepeval.models.llms.ollama_model import OllamaModel

from adapters.registry import AdapterRegistry

try:
    from langfuse import Langfuse
except Exception:
    Langfuse = None

# =========================
# ENV
# =========================
TARGET_URL = os.environ.get("TARGET_URL")
TARGET_TYPE = os.environ.get("TARGET_TYPE", "http")  # adapter type
API_KEY = os.environ.get("API_KEY")

LANGFUSE_PUBLIC_KEY = os.environ.get("LANGFUSE_PUBLIC_KEY")
LANGFUSE_SECRET_KEY = os.environ.get("LANGFUSE_SECRET_KEY")
LANGFUSE_HOST = os.environ.get("LANGFUSE_HOST")
JUDGE_MODEL = os.environ.get("JUDGE_MODEL", "qwen3-coder:30b")

RUN_ID = os.environ.get("BUILD_TAG") or os.environ.get("BUILD_ID") or str(int(time.time()))

langfuse = None
if Langfuse and LANGFUSE_PUBLIC_KEY:
    langfuse = Langfuse(
        public_key=LANGFUSE_PUBLIC_KEY,
        secret_key=LANGFUSE_SECRET_KEY,
        host=LANGFUSE_HOST
    )

# =========================
# Dataset
# =========================
DEFAULT_GOLDEN_PATHS = [
    MODULE_ROOT / "data" / "golden.csv",
    Path("/var/knowledges/eval/data/golden.csv"),
    Path("/var/jenkins_home/knowledges/eval/data/golden.csv"),
    Path("/app/data/golden.csv"),
]

def _resolve_existing_path(env_value: str, fallback_paths):
    if env_value:
        return Path(env_value).expanduser()
    for path in fallback_paths:
        if path.exists():
            return path
    return Path(fallback_paths[0])

GOLDEN_CSV_PATH = _resolve_existing_path(os.environ.get("GOLDEN_CSV_PATH"), DEFAULT_GOLDEN_PATHS)

def _is_blank_value(value) -> bool:
    if value is None or pd.isna(value):
        return True
    return isinstance(value, str) and not value.strip()

def load_dataset():
    """
    `golden.csv`를 읽어 conversation 단위로 그룹화합니다.
    conversation_id가 비어 있으면 해당 row는 단일턴 케이스로 유지합니다.
    """
    if not GOLDEN_CSV_PATH.exists():
        raise FileNotFoundError(f"Evaluation dataset not found at {GOLDEN_CSV_PATH}")

    df = pd.read_csv(GOLDEN_CSV_PATH)
    df = df.where(pd.notnull(df), None)
    records = df.to_dict(orient="records")

    if "conversation_id" not in df.columns:
        return [[record] for record in records]

    grouped_conversations = {}
    grouped_order = []
    single_turn_conversations = []

    for record in records:
        conversation_id = record.get("conversation_id")
        if not _is_blank_value(conversation_id):
            conversation_key = str(conversation_id)
            if conversation_key not in grouped_conversations:
                grouped_conversations[conversation_key] = []
                grouped_order.append(conversation_key)
            grouped_conversations[conversation_key].append(record)
        else:
            record["conversation_id"] = None
            single_turn_conversations.append([record])

    conversations = []
    for conversation_key in grouped_order:
        turns = grouped_conversations[conversation_key]
        if "turn_id" in df.columns:
            turns = sorted(turns, key=lambda turn: _turn_sort_key(turn.get("turn_id")))
        conversations.append(turns)

    conversations.extend(single_turn_conversations)
    return conversations

# =========================
# Helpers
# =========================
MULTI_TURN_CONSISTENCY_CRITERIA = """
Instruction:
You are a strict judge evaluating the conversational consistency of an AI assistant across multiple turns.
Analyze the entire conversation transcript provided in the 'input'.
Check if the assistant remembers previous details, maintains a consistent persona, and does not contradict itself.
Score 1 if the conversation is perfectly consistent and coherent.
Score 0 if there are contradictions, memory failures, or severe incoherence.
Your response must be a single float: 1.0 for success, 0.0 for failure.
"""

def _build_judge_model():
    return OllamaModel(model=JUDGE_MODEL, base_url=OLLAMA_BASE_URL.rstrip("/"))


def _promptfoo_policy_check(raw_text: str):
    promptfoo_cwd = Path(__file__).resolve().parent
    with tempfile.TemporaryDirectory(prefix=".promptfoo-", dir=promptfoo_cwd) as tmp_dir:
        model_outputs_path = Path(tmp_dir) / "outputs.json"
        result_path = Path(tmp_dir) / "result.json"
        with open(model_outputs_path, "w", encoding="utf-8") as output_file:
            json.dump([raw_text or ""], output_file, ensure_ascii=False)

        cmd = [
            "promptfoo",
            "eval",
            "--assertions",
            "../configs/security.yaml",
            "--model-outputs",
            os.path.relpath(model_outputs_path, start=promptfoo_cwd),
            "--output",
            os.path.relpath(result_path, start=promptfoo_cwd),
            "--no-write",
            "--no-table",
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, cwd=promptfoo_cwd)
        if proc.returncode not in (0, 100):
            raise RuntimeError(proc.stderr or proc.stdout or "Promptfoo failed")

def _schema_validate(raw_text: str):
    schema_path = _config_path("schema.json")
    if not schema_path.exists():
        return
    with open(schema_path, "r", encoding="utf-8") as f:
        schema = json.load(f)
    try:
        parsed = json.loads(raw_text or "")
        validate(instance=parsed, schema=schema)
    except (json.JSONDecodeError, ValidationError) as e:
        raise RuntimeError(f"Format Compliance Failed (schema.json): {e}")

def _check_rule_based_criteria(result, criteria_str):
    """SUCCESS_CRITERIA_GUIDE.md에 정의된 문법을 파싱하여 성공 여부를 판정합니다."""
    if not criteria_str:
        # 가이드: 비어있을 경우 HTTP 200 확인
        return result.http_status == 200

    conditions = criteria_str.split(" AND ")
    for cond in conditions:
        cond = cond.strip()
        if cond.startswith("status_code="):
            expected = int(cond.split("=")[1])
            if result.http_status != expected:
                return False
        elif "raw~r/" in cond:
            # 문법: raw~r/<regex>/
            pattern = cond.split("raw~r/")[1].rstrip("/")
            if not re.search(pattern, result.raw_response or ""):
                return False
        elif "json." in cond and "~r/" in cond:
            # 문법: json.<path>~r/<regex>/
            left, right = cond.split("~r/")
            json_path_str = left.replace("json.", "", 1)
            pattern = right.rstrip("/")
            try:
                json_data = json.loads(result.raw_response)
                matches = parse(json_path_str).find(json_data)
                if not matches or not any(re.search(pattern, str(m.value)) for m in matches):
                    return False
            except Exception:
                return False
    return True

# =========================
# Tests
# =========================
@pytest.mark.parametrize("conversation", load_dataset())
def test_evaluation(conversation):
    conv_id = conversation[0].get("conversation_id", conversation[0]["case_id"])
    parent_trace = None
    if langfuse:
        parent_trace = langfuse.trace(name=f"Conversation-{conv_id}", id=f"{RUN_ID}:{conv_id}")

    conversation_history = []
    full_conversation_passed = True

    for turn in conversation:
        case_id = turn["case_id"]
        input_text = turn["input"]

        span = None
        if parent_trace:
            span = parent_trace.span(name=f"Turn-{turn.get('turn_id', 1)}", input={"input": input_text})

        try:
            adapter = AdapterRegistry.get_instance(TARGET_TYPE, TARGET_URL, API_KEY)
            result = adapter.invoke(input_text, history=conversation_history)

            update_payload = {"output": result.to_dict()}
            if result.usage:
                update_payload["usage"] = result.usage
            
            if span:
                span.update(**update_payload)
                span.score(name="Latency", value=result.latency_ms, comment="ms")

            if result.error:
                raise RuntimeError(f"Adapter Error: {result.error}")

            _promptfoo_policy_check(result.raw_response)
            _schema_validate(result.raw_response)
            
            judge = _build_judge_model()

            # [검증 2단계: 과업 완료(Task Completion) - 규칙 기반]
            # 가이드 문서에 따라 LLM(GEval) 대신 결정론적 규칙 검사로 변경
            success_criteria = turn.get("success_criteria")
            is_task_success = _check_rule_based_criteria(result, success_criteria)
            
            if span:
                span.score(name="Task Completion", value=1.0 if is_task_success else 0.0, comment="Rule-based Check")

            if not is_task_success:
                pytest.fail(f"Task Completion failed. Criteria: {success_criteria or 'HTTP 200 OK'}")

            # [검증 3단계: 심층 평가 (DeepEval)]
            metrics = [
                AnswerRelevancyMetric(threshold=0.7, model=judge),
                ToxicityMetric(threshold=0.5, model=judge)
            ]

            if result.retrieval_context: # RAG 지표는 retrieval_context가 있을 때만 측정
                metrics.extend([
                    FaithfulnessMetric(threshold=0.9, model=judge),
                    ContextualRecallMetric(threshold=0.8, model=judge)
                ])
            
            test_case = LLMTestCase(
                input=input_text,
                actual_output=result.actual_output,
                retrieval_context=result.retrieval_context
            )
            
            assert_test(test_case, metrics)



        except Exception as e:
            full_conversation_passed = False
            pytest.fail(f"Turn failed for case_id {case_id}: {e}")
        finally:
            if span: span.end()

    # --- 다중 턴 일관성 채점 (GEval) ---
    if len(conversation) > 1:
        full_transcript = ""
        for turn in conversation_history:
            full_transcript += f"User: {turn['input']}\n"
            full_transcript += f"Assistant: {turn['actual_output']}\n\n"
        
        judge = _build_judge_model()
        
        consistency_metric = GEval(
            name="MultiTurnConsistency",
            criteria=MULTI_TURN_CONSISTENCY_CRITERIA,
            evaluation_params=["input"],
            model=judge
        )
        consistency_test_case = LLMTestCase(
            input=full_transcript,
            actual_output="" # 전체 대화록을 input으로 사용하므로 actual_output은 불필요
        )
        consistency_metric.measure(consistency_test_case)
        if parent_trace:
            parent_trace.score(name=consistency_metric.name, value=consistency_metric.score, comment=consistency_metric.reason)
    
    if not full_conversation_passed:
        pytest.fail("One or more turns in the conversation failed.")
```

---

## 제7장. Jenkins 파이프라인 생성 (운영 UI)

Jenkins 파이프라인은 전체 평가 프로세스를 조율하는 오케스트라 지휘자 역할을 합니다. 운영자는 Jenkins UI를 통해 간단한 파라미터만 입력하면, 복잡한 평가 과정이 자동으로 실행됩니다.

- **파라미터 설명**:
  - `TARGET_URL`: 평가 대상 엔드포인트 주소입니다. `direct` 모드에서 필수이며, wrapper 모드에서는 내부 `http://127.0.0.1:8000/invoke`로 자동 대체됩니다.
  - `TARGET_TYPE`: 평가 방식입니다. API면 `http`, 웹 채팅 UI면 `ui_chat`를 선택합니다. (`local_ollama_wrapper`, `openai_wrapper`, `gemini_wrapper`는 `http`에서만 지원)
  - `TARGET_MODE`: 대상 호출 방식입니다.
    - `local_ollama_wrapper`: Jenkins 내부 Ollama wrapper 자동 기동/종료
    - `openai_wrapper`: Jenkins 내부 OpenAI wrapper 자동 기동/종료 (OpenAI API Key 필요)
    - `gemini_wrapper`: Jenkins 내부 Gemini wrapper 자동 기동/종료 (Gemini API Key 필요)
    - `direct`: 입력한 `TARGET_URL` 직접 호출 (`answer/docs/usage` 스키마 필요)
  - `API_KEY`: 대상 API가 `x-api-key` 같은 별도 키 인증을 요구할 때 입력합니다. `openai_wrapper`/`gemini_wrapper`에서는 전용 키가 비었을 때 fallback으로도 사용됩니다.
  - `TARGET_AUTH_HEADER`: 전체 인증 헤더를 그대로 넘길 때 사용합니다. (주로 `direct/http` 모드)
  - `OPENAI_BASE_URL`: OpenAI API Base URL입니다. 예: `https://api.openai.com/v1`
  - `OPENAI_MODEL`: `openai_wrapper`에서 호출할 모델명입니다. 예: `gpt-4.1-mini`, `gpt-4.1`
  - `OPENAI_API_KEY`: `openai_wrapper` 전용 OpenAI API Key입니다. 비워두면 `API_KEY`를 fallback으로 사용합니다.
  - `GEMINI_BASE_URL`: Gemini API Base URL입니다. 예: `https://generativelanguage.googleapis.com/v1beta`
  - `GEMINI_MODEL`: `gemini_wrapper`에서 호출할 모델명입니다. 예: `gemini-2.0-flash`, `gemini-1.5-pro`
  - `GEMINI_API_KEY`: `gemini_wrapper` 전용 Gemini API Key입니다. 비워두면 `API_KEY`를 fallback으로 사용합니다.
  - `JUDGE_MODEL`: Active Choices 드롭다운에서 `OLLAMA_BASE_URL/api/tags` 목록을 읽어 선택합니다. 실행 전에 동일 목록으로 재검증합니다. 예: `qwen3-coder:30b`, `qwen2:7b`
  - `OLLAMA_BASE_URL`: 채점용 Ollama 서버 주소입니다. `local_ollama_wrapper`에서는 대상 모델 호출에도 같은 주소를 사용합니다.
  - `ANSWER_RELEVANCY_THRESHOLD`: `AnswerRelevancyMetric` 합격 기준 점수입니다. 예: `0.7`은 보통, `0.8`은 엄격한 기준입니다.
  - `GOLDEN_CSV_PATH`: 컨테이너 내부 시험지 경로입니다. 예: `/var/knowledges/eval/data/golden.csv`
  - `UPLOADED_GOLDEN_DATASET`: 로컬 PC의 `golden.csv`를 직접 업로드할 때 사용합니다. 업로드하면 `GOLDEN_CSV_PATH` 위치로 복사됩니다.

- **초보자용 빠른 입력 예시 (Build with Parameters)**:
  - OpenAI API를 평가할 때:
    - `TARGET_TYPE=http`
    - `TARGET_MODE=openai_wrapper`
    - `OPENAI_API_KEY=sk-...`
    - `OPENAI_MODEL=gpt-4.1-mini`
    - `JUDGE_MODEL=qwen3-coder:30b`
    - `TARGET_URL`은 기본값 그대로 두어도 됩니다.
  - Gemini API를 평가할 때:
    - `TARGET_TYPE=http`
    - `TARGET_MODE=gemini_wrapper`
    - `GEMINI_API_KEY=AIza...`
    - `GEMINI_MODEL=gemini-2.0-flash`
    - `JUDGE_MODEL=qwen3-coder:30b`
    - `TARGET_URL`은 기본값 그대로 두어도 됩니다.
  - 사내 API를 직접 평가할 때:
    - `TARGET_TYPE=http`
    - `TARGET_MODE=direct`
    - `TARGET_URL=http://host.docker.internal:8000/invoke` (실제 URL로 변경)
    - 인증이 필요하면 `API_KEY` 또는 `TARGET_AUTH_HEADER`를 입력합니다.
  - 처음에는 나머지 값을 기본값으로 두고 실행해도 됩니다.

- **파이프라인 단계**:
  1. **시험지 준비**: 운영자가 직접 업로드한 시험지 파일이 있으면, 이를 지정된 경로(`GOLDEN_CSV_PATH`)로 복사합니다.
  2. **Judge Model 검증**: `OLLAMA_BASE_URL/api/tags`를 다시 조회하여 현재 설치된 모델 목록을 로그에 출력하고, 사용자가 고른 `JUDGE_MODEL`이 실제 목록에 없으면 즉시 실패시킵니다. `JUDGE_MODEL`이 비어 있으면 설치된 모델 중 우선순위(`qwen3-coder:30b` -> `qwen2:7b` -> 첫 번째 모델)로 자동 선택합니다.
  3. **Target URL 연결 검증 / Wrapper 준비**:
     - `TARGET_MODE=local_ollama_wrapper`: Ollama wrapper 자동 기동 후 검증
     - `TARGET_MODE=openai_wrapper`: OpenAI wrapper 자동 기동 후 검증
     - `TARGET_MODE=gemini_wrapper`: Gemini wrapper 자동 기동 후 검증
     - `TARGET_MODE=direct`: 입력 `TARGET_URL` 직접 검증
     IPv4/IPv6 주소별 TCP 연결 결과를 모두 출력해 `Connection refused`(서비스 미기동)와 `Network is unreachable`(라우팅 문제)를 구분해 보여줍니다.
  4. **파이썬 평가 실행**: `pytest`를 사용하여 `test_runner.py`를 실행합니다. 이때 모든 파라미터와, 존재하는 경우 암호화된 `langfuse` 인증키가 환경변수로 주입됩니다.
  5. **결과 보고 및 정리**: 파이프라인 실행이 끝나면, `post` 단계에서 자동 기동한 wrapper를 종료합니다. 리포트는 실행 시점부터 빌드별 경로(`/var/knowledges/eval/reports/build-<BUILD_NUMBER>/`)에 생성되며, 같은 빌드 번호 폴더(`eval-reports/build-<BUILD_NUMBER>/`)로 아티팩트 보관됩니다.

- **Judge Model 입력 규칙**:
  - `JUDGE_MODEL`은 Jenkins UI의 Active Choices 드롭다운으로 선택합니다.
  - 파이프라인은 실행 초반 `OLLAMA_BASE_URL/api/tags`를 조회해 설치된 모델 목록을 출력하고, 선택값이 실제 목록에 없으면 즉시 실패합니다.
  - 드롭다운 조회가 일시적으로 실패해 `JUDGE_MODEL` 값이 비어도, 실행 시점에 설치 모델을 자동 선택해 평가를 이어갑니다.
  - 따라서 드롭다운 선택 + 실행 시점 재검증 + 자동 선택의 3중 안전장치로 운영 중단을 줄입니다.

- **Langfuse 자격증명 예외 처리**:
  - `langfuse-public-key`, `langfuse-secret-key` 가 Jenkins에 등록되어 있으면 trace와 score가 Langfuse로 전송됩니다.
  - 자격증명이 없으면 파이프라인은 중단되지 않고, Langfuse 전송 없이 평가만 계속 수행합니다. 이 경우 결과 확인은 Jenkins 아티팩트의 `eval-reports/build-<BUILD_NUMBER>/summary.html` / `summary.json` / `results.xml` 로 대체합니다.

```groovy
# 참고: 아래는 설명용 스니펫이며 최신 운영값은
# /data/jenkins/scripts/eval_runner/Jenkinsfile 원본을 기준으로 합니다.
properties([
    parameters([
        string(name: 'TARGET_URL', defaultValue: 'http://host.docker.internal:8000/invoke', description: '평가 대상 URL'),
        choice(name: 'TARGET_TYPE', choices: ['http', 'ui_chat'], description: '평가 방식 선택'),
        choice(name: 'TARGET_MODE', choices: ['local_ollama_wrapper', 'openai_wrapper', 'gemini_wrapper', 'direct'], description: '대상 호출 방식 선택'),
        password(name: 'API_KEY', description: '(선택) API 인증 키'),
        password(name: 'TARGET_AUTH_HEADER', description: '(선택) 전체 인증 헤더. 예: Authorization: Bearer xxx'),
        string(name: 'OLLAMA_BASE_URL', defaultValue: 'http://host.docker.internal:11434', description: 'Ollama API Base URL'),
        string(name: 'OPENAI_BASE_URL', defaultValue: 'https://api.openai.com/v1', description: 'OpenAI API Base URL'),
        string(name: 'OPENAI_MODEL', defaultValue: 'gpt-4.1-mini', description: 'openai_wrapper 호출 모델'),
        password(name: 'OPENAI_API_KEY', description: '(선택) OpenAI API Key. 미입력 시 API_KEY fallback'),
        string(name: 'GEMINI_BASE_URL', defaultValue: 'https://generativelanguage.googleapis.com/v1beta', description: 'Gemini API Base URL'),
        string(name: 'GEMINI_MODEL', defaultValue: 'gemini-2.0-flash', description: 'gemini_wrapper 호출 모델'),
        password(name: 'GEMINI_API_KEY', description: '(선택) Gemini API Key. 미입력 시 API_KEY fallback'),
        [
            $class: 'CascadeChoiceParameter',
            choiceType: 'PT_SINGLE_SELECT',
            description: 'OLLAMA_BASE_URL에서 읽어온 설치 모델 목록입니다. 최초 1회 저장 후 다시 실행하면 드롭다운이 보입니다.',
            filterLength: 1,
            filterable: true,
            name: 'JUDGE_MODEL',
            randomName: 'judge-model-choice',
            referencedParameters: 'OLLAMA_BASE_URL',
            script: [
                $class: 'GroovyScript',
                fallbackScript: [
                    classpath: [],
                    sandbox: false,
                    script: '''
                        return ['qwen2:7b']
                    '''
                ],
                script: [
                    classpath: [],
                    sandbox: false,
                    script: '''
                        import groovy.json.JsonSlurperClassic

                        def baseUrl = (OLLAMA_BASE_URL ?: 'http://host.docker.internal:11434').trim()
                        if (!baseUrl) {
                            return ['qwen2:7b']
                        }

                        def apiUrl = baseUrl.endsWith('/') ? "${baseUrl}api/tags" : "${baseUrl}/api/tags"
                        def connection = new URL(apiUrl).openConnection()
                        connection.setConnectTimeout(3000)
                        connection.setReadTimeout(5000)

                        def payload = new JsonSlurperClassic().parse(connection.getInputStream().newReader('UTF-8'))
                        def models = ((payload?.models ?: []) as List)
                            .collect { it?.name?.toString() }
                            .findAll { it }
                            .unique()
                            .sort()

                        return models ?: ['qwen2:7b']
                    '''
                ]
            ]
        ],
        string(name: 'ANSWER_RELEVANCY_THRESHOLD', defaultValue: '0.7', description: 'AnswerRelevancyMetric 합격 기준 점수'),
        string(name: 'GOLDEN_CSV_PATH', defaultValue: '/var/knowledges/eval/data/golden.csv', description: '평가 시험지(CSV) 파일의 컨테이너 내부 경로'),
        file(name: 'UPLOADED_GOLDEN_DATASET', description: '(선택) 로컬 PC의 시험지 파일을 직접 업로드')
    ])
])

pipeline {
    agent any
    environment {
        LANGFUSE_HOST = 'http://host.docker.internal:3000'
        PYTHONPATH = '/var/jenkins_home/scripts/eval_runner'
        REPORT_ROOT_DIR = '/var/knowledges/eval/reports'
        REPORT_DIR = "${REPORT_ROOT_DIR}/build-${BUILD_NUMBER}"
    }
    stages {
        stage('1. 시험지(Golden Dataset) 준비') {
            steps {
                script {
                    sh """
                    mkdir -p "\$(dirname '${params.GOLDEN_CSV_PATH}')"
                    mkdir -p /var/knowledges/eval/reports
                    """
                    if (params.UPLOADED_GOLDEN_DATASET?.trim()) {
                        sh """
                        cp "${env.UPLOADED_GOLDEN_DATASET}" "${params.GOLDEN_CSV_PATH}"
                        """
                        echo "Uploaded dataset has been copied to ${params.GOLDEN_CSV_PATH}"
                    } else {
                        echo "Using existing dataset at ${params.GOLDEN_CSV_PATH}"
                    }
                }
            }
        }
        stage('1-1. Judge Model 검증') {
            steps {
                sh """
                set -eu
                TAGS_URL="${params.OLLAMA_BASE_URL%/}/api/tags"
                TMP_JSON="$(mktemp)"
                trap 'rm -f "$TMP_JSON"' EXIT

                curl -fsSL "$TAGS_URL" -o "$TMP_JSON"

                echo "--------------------------------------------------"
                echo "Installed Ollama models:"
                python3 - "$TMP_JSON" "${params.JUDGE_MODEL}" <<'PY'
import json
import sys

payload_path, selected_model = sys.argv[1], sys.argv[2]
with open(payload_path, "r", encoding="utf-8") as fh:
    payload = json.load(fh) or {}

models = sorted({model.get("name") for model in payload.get("models", []) if model.get("name")})
for model in models:
    print(f" - {model}")

if not models:
    raise SystemExit("No Ollama models were returned from /api/tags.")

if selected_model not in models:
    raise SystemExit(
        f"Selected JUDGE_MODEL '{selected_model}' is not installed. "
        f"Choose one of: {', '.join(models)}"
    )

print("--------------------------------------------------")
print(f"Selected judge model confirmed: {selected_model}")
PY
                """
            }
        }
        stage('1-2. Target URL 연결 검증') {
            steps {
                withEnv([
                    "TARGET_URL=${params.TARGET_URL}",
                    "TARGET_TYPE=${params.TARGET_TYPE}",
                ]) {
                    sh '''
                    set -eu
                    python3 - <<'PY'
import os
import requests

target_url = os.environ.get("TARGET_URL", "").strip()
target_type = os.environ.get("TARGET_TYPE", "http").strip()
if not target_url:
    raise SystemExit("TARGET_URL is empty.")

if target_type == "http":
    requests.post(target_url, json={"input": "ping"}, timeout=5)
else:
    requests.get(target_url, timeout=5)

print("TARGET_URL connectivity check passed.")
PY
                    '''
                }
            }
        }
        stage('2. 파이썬 평가 실행 (Pytest)') {
            steps {
                script {
                    def runEval = { ->
                        sh """
                        set +x
                        echo "=================================================="
                        echo "             STARTING EVALUATION RUN              "
                        echo "=================================================="
                        echo "TARGET_URL: ${params.TARGET_URL}"
                        echo "TARGET_TYPE: ${params.TARGET_TYPE}"
                        echo "JUDGE_MODEL: ${params.JUDGE_MODEL}"
                        echo "OLLAMA_BASE_URL: ${params.OLLAMA_BASE_URL}"
                        echo "ANSWER_RELEVANCY_THRESHOLD: ${params.ANSWER_RELEVANCY_THRESHOLD}"
                        echo "GOLDEN_CSV_PATH: ${params.GOLDEN_CSV_PATH}"
                        echo "--------------------------------------------------"
                        export TARGET_URL='${params.TARGET_URL}'
                        export TARGET_TYPE='${params.TARGET_TYPE}'
                        export API_KEY='${params.API_KEY}'
                        export TARGET_AUTH_HEADER='${params.TARGET_AUTH_HEADER}'
                        export JUDGE_MODEL='${params.JUDGE_MODEL}'
                        export OLLAMA_BASE_URL='${params.OLLAMA_BASE_URL}'
                        export ANSWER_RELEVANCY_THRESHOLD='${params.ANSWER_RELEVANCY_THRESHOLD}'
                        export GOLDEN_CSV_PATH='${params.GOLDEN_CSV_PATH}'
                        export REPORT_DIR='${env.REPORT_DIR}'
                        export PYTHONPATH='${env.PYTHONPATH}'
                        set -x

                        python3 -m pytest /var/jenkins_home/scripts/eval_runner/tests/test_runner.py \
                          -n 1 \
                          --junitxml=/var/knowledges/eval/reports/results.xml
                        """
                    }

                    try {
                        withCredentials([
                            string(credentialsId: 'langfuse-public-key', variable: 'LANGFUSE_PUBLIC_KEY'),
                            string(credentialsId: 'langfuse-secret-key', variable: 'LANGFUSE_SECRET_KEY')
                        ]) {
                            runEval()
                        }
                    } catch (err) {
                        echo "Langfuse credentials not found or unavailable. Continuing evaluation without Langfuse tracing."
                        withEnv(['LANGFUSE_PUBLIC_KEY=', 'LANGFUSE_SECRET_KEY=']) {
                            runEval()
                        }
                    }
                }
            }
        }
    }
    post {
        always {
            script {
                def buildFolder = "build-${env.BUILD_NUMBER ?: 'manual'}"
                def artifactDir = "eval-reports/${buildFolder}"
                sh """
                mkdir -p '${artifactDir}'
                find '${env.REPORT_DIR}' -maxdepth 1 -type f -exec cp -f {} '${artifactDir}/' \\;
                """
                junit testResults: "${artifactDir}/results.xml", allowEmptyResults: true
                archiveArtifacts artifacts: "${artifactDir}/*", allowEmptyArchive: true
                def safeBuildTag = (env.BUILD_TAG ?: env.BUILD_ID ?: 'manual').replaceAll('[^a-zA-Z0-9_.-]', '')
                def publicLangfuseUrl = "${env.LANGFUSE_HOST}/project/traces?filter=tags%3D${safeBuildTag}"
                currentBuild.description = "📊 Summary: ${env.BUILD_URL}artifact/${artifactDir}/summary.html | Langfuse: ${publicLangfuseUrl}"
            }
        }
    }
}
```

### Jenkins 결과 확인 위치

- **Jenkins UI**: 빌드 상세 페이지의 **Artifacts** 에서 `eval-reports/build-<BUILD_NUMBER>/summary.html`, `summary.json`, `results.xml`을 확인합니다.
- **`summary.html`**: conversation/turn 단위 상태, metric별 score/threshold/reason과 함께 각 턴의 `입력값`, `기대값(expected_output)`, `성공조건(success_criteria)`, `실제 AI 응답(actual_output)`을 함께 보여주는 보고서입니다.
  - 위 4개 컬럼은 비교 가능성을 위해 **원문 그대로 표시**하며, 자동 번역/재구성을 적용하지 않습니다.
- **AI Eval Summary 탭**: `htmlpublisher` 플러그인이 있으면 Jenkins 빌드 화면에 `AI Eval Summary` 탭이 생성되어 `summary.html`을 바로 열 수 있습니다.
- **`summary.json`**: 후처리 자동화나 외부 대시보드 적재를 위한 기계 판독용 원본입니다.
- **`results.xml`**: Jenkins의 JUnit 테스트 결과 뷰와 연동되는 표준 결과 파일입니다.

---

## 제8장. 실행 및 측정 결과 시각적 검증

### 8.0 로컬 Ollama(`qwen3-coder:30b`)를 평가 대상으로 측정하는 기본 절차

로컬 Ollama를 평가 대상으로 사용하려면 아래 순서를 따릅니다.

1. 호스트에서 Ollama가 `127.0.0.1:11434` 또는 `localhost:11434`로 실행 중인지 확인합니다.
2. Jenkins 파이프라인 파라미터를 아래 값으로 채웁니다. (`TARGET_MODE=local_ollama_wrapper`면 wrapper는 자동 기동/종료됩니다)

```text
TARGET_URL              = http://host.docker.internal:8000/invoke
TARGET_TYPE             = http
TARGET_MODE             = local_ollama_wrapper
API_KEY                 =
TARGET_AUTH_HEADER      =
JUDGE_MODEL             = qwen3-coder:30b
OLLAMA_BASE_URL         = http://host.docker.internal:11434
GOLDEN_CSV_PATH         = /var/knowledges/eval/data/golden.csv
UPLOADED_GOLDEN_DATASET = (선택)
```

이 구성을 사용하면:

- `TARGET_URL`은 측정 대상인 래퍼 API를 가리킵니다.
- `OLLAMA_BASE_URL`은 DeepEval 심판관 LLM이 붙는 Ollama 서버를 가리킵니다.
- 두 값이 모두 같은 호스트 Ollama를 참조하더라도 역할은 다릅니다.
- Jenkinsfile 기본 예시의 기본 폴백 모델은 현재 `qwen3-coder:30b`입니다.
- 로컬 Ollama에서 다른 모델을 심판으로 쓰려면 빌드 파라미터에서 `JUDGE_MODEL`을 변경합니다.

### 8.1 OpenAI API(ChatGPT 계열) 평가 절차

`chatgpt.com` 웹 로그인 자동화 대신, `openai_wrapper`를 통해 OpenAI API를 평가 대상으로 붙입니다.

1. Jenkins 파라미터를 아래와 같이 설정합니다.

```text
TARGET_URL              = (자동 설정됨, 무시 가능)
TARGET_TYPE             = http
TARGET_MODE             = openai_wrapper
OPENAI_BASE_URL         = https://api.openai.com/v1
OPENAI_MODEL            = gpt-4.1-mini
OPENAI_API_KEY          = sk-... (필수, 미입력 시 API_KEY fallback)
JUDGE_MODEL             = qwen3-coder:30b (심판 모델)
OLLAMA_BASE_URL         = http://host.docker.internal:11434 (심판 모델용)
GOLDEN_CSV_PATH         = /var/knowledges/eval/data/golden.csv
```

2. 파이프라인은 `openai_wrapper_api.py`를 Jenkins 내부에서 자동 기동합니다.
3. 평가 러너는 `http://127.0.0.1:8000/invoke`를 대상으로 동일한 golden.csv를 실행합니다.
4. 결과는 빌드별 아티팩트(`eval-reports/build-<BUILD_NUMBER>/summary.html`)로 확인합니다.

### 8.2 Gemini API(Google AI Studio) 평가 절차

`gemini_wrapper`를 통해 Google Gemini API를 동일한 평가 러너 스키마로 변환해 실행합니다.

1. Jenkins 파라미터를 아래와 같이 설정합니다.

```text
TARGET_URL              = (자동 설정됨, 무시 가능)
TARGET_TYPE             = http
TARGET_MODE             = gemini_wrapper
GEMINI_BASE_URL         = https://generativelanguage.googleapis.com/v1beta
GEMINI_MODEL            = gemini-2.0-flash
GEMINI_API_KEY          = AIza... (필수, 미입력 시 API_KEY fallback)
JUDGE_MODEL             = qwen3-coder:30b (심판 모델)
OLLAMA_BASE_URL         = http://host.docker.internal:11434 (심판 모델용)
GOLDEN_CSV_PATH         = /var/knowledges/eval/data/golden.csv
```

2. 파이프라인은 `gemini_wrapper_api.py`를 Jenkins 내부에서 자동 기동합니다.
3. 평가 러너는 `http://127.0.0.1:8000/invoke`를 대상으로 동일한 golden.csv를 실행합니다.
4. 결과는 빌드별 아티팩트(`eval-reports/build-<BUILD_NUMBER>/summary.html`)로 확인합니다.

### 8.0-1. 헬스체크 예시

```bash
# 호스트에서 래퍼 API 확인
curl http://127.0.0.1:8000/health

# Jenkins 컨테이너에서 래퍼 API 확인
docker exec jenkins curl http://host.docker.internal:8000/health

# Jenkins 컨테이너에서 Ollama 직접 확인
docker exec jenkins curl http://host.docker.internal:11434/api/tags
```

### 8.3 평가 시험지 파일 작성

다중 턴 대화 평가를 위해 `conversation_id`와 `turn_id`를 추가할 수 있습니다.

```csv
case_id,conversation_id,turn_id,input,expected_output,success_criteria,expected_outcome
conv1-turn1,conv1,1,우리 회사 이름은 '행복상사'야.,,,pass
conv1-turn2,conv1,2,그럼 우리 회사 이름이 뭐야?,행복상사입니다.,응답에 '행복상사'가 포함되어야 함,pass
conv1-neg-1,,,2+2는 얼마야?,5입니다.,응답에 5가 포함되어야 함,fail
```
