# DSCORE-TTC Zero-Touch QA 구축 및 운영 가이드 (v2.7)

* 문서 버전: v2.7
* 작성일: 2026-01-05
* 적용 범위: Jenkins(컨테이너), Docker, Ollama(호스트), Playwright, Python 에이전트
* 운영 전제: 내부망(폐쇄망) 환경, 외부 전송 금지, 컨테이너 내부에서 `host.docker.internal` 접근 가능

---

## 0. 문서 범위와 운영 원칙

본 문서는 **Zero-Touch QA**(자연어 요구사항 기반 E2E 자동 실행) 구성을 **재현 가능한 형태**로 고정한다.
구성 요소는 **이미지 빌드(Dockerfile) + 에이전트 스크립트(Python) + 파이프라인(Jenkinsfile)**로 정의한다.
모든 코드 블록(Dockerfile, Jenkinsfile, YAML, 스크립트)은 **설정/기능 단위 주석을 포함**한다.

또한 `docker-compose override` 방식은 사용하지 않는다.
운영 복잡도 증가(실행 명령 다변화, 적용 설정 추적 어려움)를 방지하기 위해 **Jenkins 이미지는 표준 compose 흐름에서 재빌드로 통일**한다.

---

## 1. Zero-Touch QA 정의

### 1.1 시스템 정의

Zero-Touch QA는 사람이 테스트 코드를 직접 작성하지 않고, **자연어 요구사항(SRS 텍스트)**만으로 E2E 테스트를 수행하는 자동화 체계다.
테스트 실행 엔진은 Playwright이며, 테스트 시나리오 설계와 복구 판단은 로컬 LLM(Ollama)을 사용한다.

### 1.2 핵심 개념

#### Intent-Driven

요소를 `#loginBtn` 같은 고정 셀렉터로만 찾지 않는다.
**“로그인 버튼”**과 같은 의미(의도)를 기준으로 요소를 탐색한다.
Playwright의 접근성 기반 Locator( role/name/label/text )를 우선 사용한다.

#### Self-Healing

UI 변경으로 특정 요소 탐색이 실패할 때, 실패를 즉시 종료하지 않는다.
정해진 순서로 복구 전략을 적용한다.
복구 결과는 실행 로그와 “치유된 시나리오(healed scenario)”로 남긴다.

#### Sequential Fast-Fail

동시 실행(병렬 탐색)은 사용하지 않는다.
대신 짧은 제한 시간으로 전략을 **순차 시도**하고, 실패 시 즉시 다음 전략으로 이동한다.
이 방식은 단순하며, 재현성과 디버깅 용이성을 높인다.

#### Regression Ready

테스트가 한 번 성공하면, AI가 찾아낸 최적의 로케이터를 포함한 독립 실행형 파이썬 스크립트(`regression_test.py`)를 자동으로 생성한다.
이후에는 LLM 호출 없이도 고속으로 리그레션 테스트를 수행할 수 있다.

---

## 2. End-to-End 동작 흐름

1. 사용자가 Jenkins Job에서 SRS 텍스트 파일을 업로드한다.
2. 에이전트가 SRS를 해석하여 `test_scenario.json`을 생성한다.
3. Playwright가 시나리오를 실행한다.
4. 실패 발생 시 Self-Healing(3단계)을 적용한다.
5. 산출물(시나리오/치유 시나리오/로그/스크린샷/HTML 리포트)을 생성한다.
6. Jenkins가 산출물을 UI 게시 및 아카이빙한다.

---

## 3. 산출물(Artifacts) 표준

모든 실행은 아래 산출물을 생성하는 것을 정상 기준으로 둔다.

* `test_scenario.json`

  * LLM이 생성한 원본 시나리오다.
  * 요구사항 해석 결과를 검토할 때 기준이 된다.

* `test_scenario.healed.json`

  * 실행 과정에서 복구된 타겟/폴백이 반영된 최종 시나리오다.
  * 동일 화면의 다음 실행에서 재사용(사실상 캐시)할 수 있다.

* `run_log.jsonl`

  * 단계별 실행/복구 시도/결과를 시계열로 기록한다.
  * 디버깅과 운영 통계를 위해 구조화된 로그(JSONL)를 사용한다.

* `index.html`

  * 최종 리포트다.
  * Step, Action, Healing 단계, 결과, 증적(스크린샷)을 제공한다.

* `regression_test.py`
  * 성공한 시나리오를 바탕으로 생성된 독립 실행 가능한 Playwright 스크립트다.
  * AI 없이도 로컬에서 즉시 실행하여 결과를 재현할 수 있다.

* `step_<N>_pass.png`, `step_<N>_fail.png`

  * 단계별 증적이다.
  * 실패 시에도 가능한 범위에서 스크린샷을 남긴다.

---

## 4. 인프라 구성

### 4.1 Jenkins 이미지 빌드(Dockerfile.jenkins)

* 파일 경로: `<PROJECT_ROOT>/Dockerfile.jenkins`
* 역할: Jenkins 컨테이너 내부에서 Python + Playwright(Chromium) + Ollama Client가 실행 가능해야 한다.

```dockerfile
# =============================================================================
# DSCORE-TTC Jenkins Image (Zero-Touch QA Ready)
#
# 목적
# - Jenkins 컨테이너 내부에서 Zero-Touch QA 에이전트(Python)를 실행한다.
# - Playwright(Chromium)를 설치하여 Headless 브라우저 테스트를 수행한다.
# - Ollama Client를 설치하여 호스트 Ollama(LLM)와 통신한다.
#
# 주의
# - 폐쇄망 환경을 전제로 하며, 외부로 데이터 전송을 수행하지 않는다.
# - 브라우저 엔진은 Playwright의 chromium을 사용한다.
# =============================================================================

FROM jenkins/jenkins:lts-jdk21

# -------------------------------------------------------------------------
# 사용자 전환
# - OS 패키지 설치를 위해 root 권한이 필요하다.
# -------------------------------------------------------------------------
USER root

# -------------------------------------------------------------------------
# OS 패키지 설치
# - python3/pip/venv: 에이전트 실행 환경
# - libgtk 등: Playwright(Chromium) 구동 필수 라이브러리
# - curl/jq: API 호출/디버깅 유틸리티
# - poppler-utils/libreoffice-impress: DSCORE-TTC 공용 문서 파이프라인과의 호환성 유지 목적
# -------------------------------------------------------------------------
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-pip python3-venv \
    curl jq \
    libgtk-3-0 libasound2 libdbus-glib-1-2 libx11-xcb1 \
    poppler-utils \
    libreoffice-impress \
    && rm -rf /var/lib/apt/lists/*

# -------------------------------------------------------------------------
# Python 라이브러리 설치
# - playwright: 브라우저 제어 프레임워크
# - ollama: 로컬 LLM 통신 클라이언트
# - tenacity: 재시도/백오프 유틸리티(운영 안정성)
# - 기타: 텍스트/HTML 처리 유틸리티(향후 확장 및 공용 사용)
# -------------------------------------------------------------------------
RUN pip3 install --no-cache-dir --break-system-packages \
    requests tenacity \
    beautifulsoup4 lxml html2text \
    playwright ollama

# -------------------------------------------------------------------------
# Playwright 브라우저 엔진 설치
# - chromium 바이너리 및 의존성을 함께 설치한다.
# -------------------------------------------------------------------------
RUN python3 -m playwright install --with-deps chromium

# -------------------------------------------------------------------------
# 운영 기본 설정
# - TZ: 로그 타임스탬프 기준을 고정한다.
# - scripts: Jenkins Job에서 실행할 스크립트가 들어간다.
# -------------------------------------------------------------------------
ENV TZ=Asia/Seoul
RUN mkdir -p /var/jenkins_home/scripts \
    && chown -R jenkins:jenkins /var/jenkins_home

# -------------------------------------------------------------------------
# 보안 기본값
# - 설치 작업 이후에는 jenkins 사용자로 복귀한다.
# -------------------------------------------------------------------------
USER jenkins
```

### 4.2 Jenkins 재빌드 및 재기동(표준 운영)

오버라이드 파일을 사용하지 않는다.
이미지 변경은 아래 명령으로 통일한다.

```bash
# -------------------------------------------------------------------------
# 목적
# - Dockerfile.jenkins 변경 사항을 반영하여 Jenkins 이미지를 재빌드한다.
# - Jenkins 컨테이너를 강제 재생성하여 런타임 환경을 일치시킨다.
# -------------------------------------------------------------------------
docker compose up -d --build --force-recreate jenkins
```

캐시 영향이 의심될 때는 아래 방식으로 강제 빌드를 수행한다.

```bash
# -------------------------------------------------------------------------
# 목적
# - build cache를 무시하고 Jenkins 이미지를 완전 재빌드한다.
# - 재빌드 후 컨테이너를 재생성한다.
# -------------------------------------------------------------------------
docker compose build --no-cache jenkins
docker compose up -d --force-recreate jenkins
```

---

## 5. 자율 주행 에이전트 구현

### 5.1 파일 경로 및 실행 단위

* 스크립트 경로: `<PROJECT_ROOT>/data/jenkins/scripts/autonomous_qa.py`
* 실행 주체: Jenkins Pipeline(Job)
* 주요 입력: `--url`, `--srs_file`, `--out`
* 주요 출력: `test_scenario.json`, `test_scenario.healed.json`, `run_log.jsonl`, `index.html`, screenshots

---

### 5.2 Self-Healing 3단계 정의

#### 1) Deterministic Fallback

시나리오 단계에 포함된 `fallback_targets[]`를 순서대로 시도한다.
이 단계는 “LLM 호출 없이”도 복구 가능하도록 설계하는 것이 목표다.

#### 2) Candidate Search

접근성 트리(Accessibility Snapshot)에서 후보(role/name)를 수집한다.
목표 Intent(name/text/label)와 후보 name의 유사도를 계산해 상위 후보를 선택한다.
이 단계는 화면에 존재하는 요소에서 “가장 가까운 대체”를 찾는 역할을 한다.

#### 3) LLM Heal

Candidate Search 결과와 실패 정보를 LLM에 제공한다.
LLM이 새로운 `target`과 갱신된 `fallback_targets`를 제안한다.
제안 내용은 `test_scenario.healed.json`에 반영한다.

---

### 5.3 autonomous_qa.py (v1.8)

````python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# [프로젝트] DSCORE-TTC Zero-Touch QA Agent (v2.7 - Full Action Set)
#
# [변경 내역 v2.7]
# - [Feature] 기능 테스트 필수 액션 대거 추가
#   (hover, double_click, select_option, scroll, assert_text, assert_visible, go_back/forward)
# - [Update] LLM 프롬프트에 새로운 액션 목록 반영
#
# [개요]
#  - 사용자가 자연어(SRS)로 작성한 테스트 요구사항을 입력받아 자동으로 수행하는 AI 에이전트입니다.
#  - Playwright를 통해 브라우저를 제어하며, 실패 시 스스로 복구(Self-Healing)합니다.
#  - 클릭 후 새 탭/새 창이 열리는 경우를 감지하여 자동으로 제어권을 넘깁니다.
#  - 테스트 실패 시 Jenkins가 알 수 있도록 실패 코드(Exit Code 1)를 반환합니다.
# - Intent-Driven: role/name/label/text 기반 탐색을 우선한다.
# - Self-Healing: fallback -> candidate search -> LLM heal 순으로 복구한다.
# - Sequential Fast-Fail: 병렬 탐색을 사용하지 않는다.
#   각 탐색 전략은 짧은 timeout으로 순차 시도한다.
#
# 주의
# - 폐쇄망 환경을 전제로 하며 외부 전송을 수행하지 않는다.
# - Ollama는 호스트에서 실행하며 컨테이너에서 host.docker.internal로 접근한다.
# =============================================================================

import argparse
import json
import os
import sys
from dataclasses import dataclass, asdict
from datetime import datetime
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional, Tuple

import ollama
from playwright.sync_api import sync_playwright

# -----------------------------------------------------------------------------
# [Config] 환경 변수 기반 설정
# -----------------------------------------------------------------------------
# Ollama 접속 주소
# - 컨테이너에서 호스트 서비스 접근을 위해 host.docker.internal을 사용한다.
DEFAULT_OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://host.docker.internal:11434")

# 사용할 LLM 모델명
DEFAULT_MODEL_NAME = os.getenv("MODEL_NAME", "qwen3-coder:30b")
_headless_env = os.getenv("HEADLESS", "true").lower()
DEFAULT_HEADLESS = _headless_env in ("true", "1", "on", "yes")
DEFAULT_SLOW_MO_MS = int(os.getenv("SLOW_MO_MS", "500"))
DEFAULT_TIMEOUT_MS = int(os.getenv("DEFAULT_TIMEOUT_MS", "30000"))
FAST_TIMEOUT_MS = int(os.getenv("FAST_TIMEOUT_MS", "2000"))
HEAL_MODE = os.getenv("HEAL_MODE", "on")
MAX_HEAL_ATTEMPTS = int(os.getenv("MAX_HEAL_ATTEMPTS", "2"))
CANDIDATE_TOP_N = int(os.getenv("CANDIDATE_TOP_N", "8"))
REAL_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"

# 기본 timeout(ms)
# - 요소 탐색/검증에 사용하는 표준 대기 시간이다.
DEFAULT_TIMEOUT_MS = int(os.getenv("DEFAULT_TIMEOUT_MS", "10000"))

# Fast-Fail timeout(ms)
# - Resolver에서 “빠르게 실패하고 다음 전략으로 넘어가기” 위해 사용한다.
FAST_TIMEOUT_MS = int(os.getenv("FAST_TIMEOUT_MS", "1000"))

# Self-Healing 제어
# - HEAL_MODE=on/off
HEAL_MODE = os.getenv("HEAL_MODE", "on")

# 복구 시도 횟수
MAX_HEAL_ATTEMPTS = int(os.getenv("MAX_HEAL_ATTEMPTS", "2"))

# Candidate Search에서 사용할 상위 후보 수
CANDIDATE_TOP_N = int(os.getenv("CANDIDATE_TOP_N", "8"))

# -----------------------------------------------------------------------------
# [Utils] 공용 유틸리티
# -----------------------------------------------------------------------------
def now_iso() -> str:
    """로그 타임스탬프를 ISO 8601로 생성한다."""
    return datetime.now().isoformat(timespec="seconds")

def log(msg: str) -> None:
    """Jenkins Console Output에 즉시 출력한다."""
    print(f"[AutoQA] {msg}", flush=True)

def ensure_dir(path: str) -> None:
    """출력 디렉터리를 생성한다."""
    try:
        os.makedirs(path, exist_ok=True)
    except Exception as e:
        log(f"CRITICAL: cannot create directory: {path} / error={e}")
        sys.exit(1)

def write_json(path: str, obj: Any) -> None:
    """JSON 파일로 저장한다."""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)

def append_jsonl(path: str, obj: Dict[str, Any]) -> None:
    """JSONL 로그 파일에 1줄을 추가한다."""
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")

def extract_json_array(text: str) -> str:
    """
    LLM 응답에서 JSON 배열만 추출한다.
    - 코드펜스(```json) 제거를 포함한다.
    - 가장 바깥 배열 [ ... ] 구간만 사용한다.
    """
    t = text.strip().replace("```json", "").replace("```", "").strip()
    start = t.find("[")
    end = t.rfind("]")
    if start == -1 or end == -1:
        raise ValueError("LLM response does not contain JSON array")
    return t[start:end + 1]

def extract_json_object(text: str) -> str:
    """
    LLM 응답에서 JSON 객체만 추출한다.
    - 가장 바깥 객체 { ... } 구간만 사용한다.
    """
    t = text.strip().replace("```json", "").replace("```", "").strip()
    start = t.find("{")
    end = t.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("LLM response does not contain JSON object")
    return t[start:end + 1]

def similarity(a: str, b: str) -> float:
    """문자열 유사도를 계산한다. (0.0 ~ 1.0)"""
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()

# -----------------------------------------------------------------------------
# [Model] Intent Target 정의
# -----------------------------------------------------------------------------
@dataclass
class IntentTarget:
    """
    UI 요소를 ‘의도’로 표현하기 위한 데이터 구조다.
    - role/name: 접근성 역할 기반(예: role=button, name=로그인)
    - label: 입력 폼 라벨(예: label=아이디)
    - text: 화면에 보이는 텍스트
    - placeholder/testid/selector: 보조 수단
    """
    role: Optional[str] = None
    name: Optional[str] = None
    label: Optional[str] = None
    text: Optional[str] = None
    placeholder: Optional[str] = None
    testid: Optional[str] = None
    selector: Optional[str] = None

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "IntentTarget":
        return IntentTarget(
            role=d.get("role"),
            name=d.get("name"),
            label=d.get("label"),
            text=d.get("text"),
            placeholder=d.get("placeholder"),
            testid=d.get("testid"),
            selector=d.get("selector"),
        )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def brief(self) -> str:
        return f"role={self.role}, name={self.name}, label={self.label}, text={self.text}"

# -----------------------------------------------------------------------------
# [Resolver] Intent 기반 Locator 해석기 (Sequential Fast-Fail)
# -----------------------------------------------------------------------------
class LocatorResolver:
    """
    IntentTarget을 Playwright Locator로 변환한다.
    - 병렬 실행을 사용하지 않는다.
    - 각 전략은 FAST_TIMEOUT_MS로 빠르게 실패한다.
    - 실패 시 즉시 다음 전략으로 이동한다.
    """

    def __init__(self, page, fast_timeout_ms: int):
        self.page = page
        self.fast_timeout_ms = fast_timeout_ms

    def _try_visible(self, locator) -> bool:
        """locator가 가시 상태인지 짧게 확인한다."""
        try:
            locator.first.wait_for(state="visible", timeout=self.fast_timeout_ms)
            return True
        except Exception:
            return False

    def resolve(self, target: IntentTarget):
        """
        전략 우선순위
        1) role + name
        2) label
        3) text(부분일치)
        4) placeholder
        5) testid
        6) selector
        """
        if target.role and target.name:
            loc = self.page.get_by_role(target.role, name=target.name)
            if self._try_visible(loc):
                return loc

        if target.label:
            loc = self.page.get_by_label(target.label)
            if self._try_visible(loc):
                return loc

        if target.text:
            loc = self.page.get_by_text(target.text, exact=False)
            if self._try_visible(loc):
                return loc

        if target.placeholder:
            loc = self.page.get_by_placeholder(target.placeholder)
            if self._try_visible(loc):
                return loc

        if target.testid:
            loc = self.page.locator(f"[data-testid='{target.testid}']")
            if self._try_visible(loc):
                return loc

        if target.selector:
            loc = self.page.locator(target.selector)
            if self._try_visible(loc):
                return loc

        raise RuntimeError(f"Target not resolved: {target.brief()}")

# -----------------------------------------------------------------------------
# [Healing] Candidate Search
# -----------------------------------------------------------------------------
def collect_accessibility_candidates(page) -> List[Dict[str, str]]:
    """
    접근성 트리에서 role/name 후보를 수집한다.
    - role과 name이 모두 존재하는 노드만 후보로 취급한다.
    - 중복은 제거한다.
    """
    snapshot = page.accessibility.snapshot()
    results: List[Dict[str, str]] = []

    def walk(node: Dict[str, Any]) -> None:
        role = node.get("role") or ""
        name = node.get("name") or ""
        if role and name:
            results.append({"role": role, "name": name})
        for child in node.get("children", []) or []:
            walk(child)

    if snapshot:
        walk(snapshot)

    dedup: Dict[str, Dict[str, str]] = {}
    for r in results:
        key = f"{r['role']}|{r['name']}"
        dedup[key] = r

    return list(dedup.values())

def filter_candidates_by_action(action: str, candidates: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """
    액션 성격에 따라 후보 역할을 제한한다.
    - click은 button/link/menuitem 위주로 좁힌다.
    - fill은 textbox/searchbox/combobox 위주로 좁힌다.
    - check는 제한을 완화한다.
    """
    if action == "click":
        allowed = {"button", "link", "menuitem", "tab", "checkbox", "radio"}
        return [c for c in candidates if c.get("role") in allowed]
    if action == "fill":
        allowed = {"textbox", "searchbox", "combobox"}
        return [c for c in candidates if c.get("role") in allowed]
    return candidates

def rank_candidates(query: str, target_role: Optional[str], candidates: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    """
    후보를 유사도 기준으로 정렬한다.
    - 기본 점수는 query vs candidate.name 유사도다.
    - role이 일치하면 가산점을 준다.
    """
    scored: List[Dict[str, Any]] = []
    for c in candidates:
        s = similarity(query, c.get("name", ""))
        if target_role and c.get("role") == target_role:
            s += 0.10
        scored.append({"role": c.get("role", ""), "name": c.get("name", ""), "score": round(s, 3)})
    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored

# -----------------------------------------------------------------------------
# [Healing] LLM Heal Prompt
# -----------------------------------------------------------------------------
def build_llm_heal_prompt(
    action: str,
    failed_target: Dict[str, Any],
    error_text: str,
    page_url: str,
    ranked_candidates: List[Dict[str, Any]],
) -> str:
    """
    LLM에 제공할 복구 프롬프트를 구성한다.
    - 실패 타겟과 에러 메시지, URL, 후보 목록을 전달한다.
    - 출력은 JSON 객체 1개로 제한한다.
    """
    candidates_json = json.dumps(ranked_candidates[:CANDIDATE_TOP_N], ensure_ascii=False, indent=2)

    return f"""
[Self-Healing Request]
아래 실패 상황을 기준으로, 실행 가능한 대체 target과 fallback_targets를 JSON으로 제안하라.

[Action]
{action}

[Failed Target]
{json.dumps(failed_target, ensure_ascii=False)}

[Error]
{error_text}

[URL]
{page_url}

[Candidate Elements]
{candidates_json}

[Output Rules]
1. 출력은 JSON 객체 1개만 허용한다.
2. target은 role+name 또는 label 또는 text 중심으로 작성한다.
3. fallback_targets는 2개 이상 포함한다.
4. 불확실한 selector 생성은 지양한다.

[Output Schema]
{{
  "target": {{"role": "...", "name": "..."}},
  "fallback_targets": [
    {{"role": "...", "name": "..."}},
    {{"text": "..."}}
  ]
}}
""".strip()

# -----------------------------------------------------------------------------
# [Report] HTML 리포트 생성
# -----------------------------------------------------------------------------
def build_html_report(rows: List[Dict[str, Any]]) -> str:
    """
    Jenkins에서 바로 확인 가능한 단일 HTML 리포트를 생성한다.
    - 결과 요약을 표로 제공한다.
    - 산출물 링크(Scenario/Healed/Logs)를 포함한다.
    """
    html = """
<html>
<head>
<meta charset="utf-8"/>
<style>
  body { font-family: Arial, sans-serif; margin: 18px; }
  table { border-collapse: collapse; width: 100%; }
  th, td { border: 1px solid #ddd; padding: 8px; vertical-align: top; }
  th { background: #f2f2f2; }
  .pass { color: #137333; font-weight: bold; }
  .fail { color: #b3261e; font-weight: bold; }
  .mono { font-family: Menlo, Consolas, monospace; font-size: 12px; }
</style>
</head>
<body>
<h2>Zero-Touch QA Report</h2>
<div>
  <span>Artifacts:</span>
  <a href="test_scenario.json">test_scenario.json</a>
  |
  <a href="test_scenario.healed.json">test_scenario.healed.json</a>
  |
  <a href="run_log.jsonl">run_log.jsonl</a>
</div>
<br/>
<table>
<tr>
  <th style="width:60px;">Step</th>
  <th style="width:90px;">Action</th>
  <th>Description</th>
  <th style="width:120px;">Healing</th>
  <th style="width:120px;">Result</th>
  <th style="width:120px;">Evidence</th>
</tr>
"""
    for r in rows:
        status = r.get("status", "")
        cls = "pass" if status == "PASS" else "fail"
        img = r.get("evidence", "")
        img_link = f"<a href='{img}'>open</a>" if img else "-"
        desc = (r.get("description") or "").replace("<", "&lt;").replace(">", "&gt;")
        heal = (r.get("heal_stage") or "none").replace("<", "&lt;").replace(">", "&gt;")
        html += f"""
<tr>
  <td class="mono">{r.get("step")}</td>
  <td class="mono">{r.get("action")}</td>
  <td>{desc}</td>
  <td class="mono">{heal}</td>
  <td class="{cls}">{status}</td>
  <td>{img_link}</td>
</tr>
"""
    html += """
</table>
</body>
</html>
""".strip()
    return html

# -----------------------------------------------------------------------------
# [Agent] 메인 에이전트
# -----------------------------------------------------------------------------
class ZeroTouchAgent:
    """
    Plan -> Execute -> Heal -> Report 흐름을 통합한다.
    - Plan: SRS를 JSON 시나리오로 변환한다.
    - Execute: 시나리오를 수행한다.
    - Heal: 실패 시 3단계 복구를 적용한다.
    """

    def __init__(self, url: str, srs_text: str, out_dir: str, ollama_host: str, model: str):
        self.url = url
        self.srs_text = srs_text
        self.out_dir = out_dir
        self.ollama_host = ollama_host
        self.model = model

        ensure_dir(out_dir)

        # 표준 산출물 경로
        self.path_scenario = os.path.join(out_dir, "test_scenario.json")
        self.path_healed = os.path.join(out_dir, "test_scenario.healed.json")
        self.path_log = os.path.join(out_dir, "run_log.jsonl")
        self.path_report = os.path.join(out_dir, "index.html")

        # Ollama 클라이언트 초기화
        self.client = ollama.Client(host=ollama_host)

    def plan_scenario(self) -> List[Dict[str, Any]]:
        """
        SRS를 Intent 기반 시나리오(JSON 배열)로 생성한다.
        - 각 step은 가능한 범위에서 fallback_targets를 포함해야 한다.
        - 출력은 JSON 배열만 허용한다.
        """
        log(f"Plan: model={self.model}")
        prompt = f"""
당신은 QA 엔지니어다.
아래 SRS를 Playwright 실행 가능한 테스트 시나리오(JSON 배열)로 변환하라.

[SRS]
{self.srs_text}

[Target URL]
{self.url}

[작성 규칙]
1. step.action은 navigate|click|fill|check|wait 중 하나다.
2. click/fill/check는 target을 Intent 기반(role+name, label, text)으로 작성한다.
3. click/fill step에는 fallback_targets를 2개 이상 포함한다.
4. 각 주요 동작 후에는 check step을 포함한다.
5. 출력은 JSON 배열만 허용한다.

[Step 예시]
[
  {{"step": 1, "action": "navigate", "value": "{self.url}", "description": "메인 페이지 접속"}},
  {{"step": 2, "action": "click", "target": {{"role": "button", "name": "로그인"}}, "fallback_targets":[{{"text":"로그인"}}, {{"role":"link","name":"로그인"}}], "description":"로그인 버튼 클릭"}},
  {{"step": 3, "action": "check", "target": {{"text": "로그인"}}, "description":"로그인 화면 요소 확인"}}
]
""".strip()

        res = self.client.chat(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            options={"temperature": 0.1},
        )
        scenario_text = extract_json_array(res["message"]["content"])
        scenario = json.loads(scenario_text)

        # 생성 결과를 즉시 저장한다.
        write_json(self.path_scenario, scenario)

        append_jsonl(self.path_log, {
            "ts": now_iso(),
            "phase": "plan",
            "status": "ok",
            "model": self.model,
            "scenario_steps": len(scenario),
        })

        return scenario

    def _log_step_event(self, payload: Dict[str, Any]) -> None:
        """Step 단위 구조화 로그를 기록한다."""
        base = {"ts": now_iso()}
        base.update(payload)
        append_jsonl(self.path_log, base)

    def _execute_action(self, page, resolver: LocatorResolver, step: Dict[str, Any]) -> None:
        """
        단일 step의 action을 실행한다.
        - navigate/wait는 일반 실행이다.
        - click/fill/check는 target을 해석하여 수행한다.
        """
        action = step.get("action")
        if action == "navigate":
            page.goto(step.get("value") or self.url, timeout=60000)
            page.wait_for_load_state("networkidle")
            return

        if action == "wait":
            ms = int(step.get("value", 1500))
            page.wait_for_timeout(ms)
            return

        if action in ["click", "fill", "check"]:
            target_dict = step.get("target") or {}
            target = IntentTarget.from_dict(target_dict)
            loc = resolver.resolve(target)

            if action == "click":
                loc.first.click(timeout=DEFAULT_TIMEOUT_MS)
                return

            if action == "fill":
                loc.first.fill(str(step.get("value", "")), timeout=DEFAULT_TIMEOUT_MS)
                return

            if action == "check":
                loc.first.wait_for(state="visible", timeout=DEFAULT_TIMEOUT_MS)
                return

        raise RuntimeError(f"Unsupported action: {action}")

    def _heal_step(
        self,
        page,
        resolver: LocatorResolver,
        step: Dict[str, Any],
        action: str,
        error_text: str,
    ) -> Tuple[bool, str]:
        """
        실패한 step에 대해 Self-Healing을 수행한다.
        반환
        - (성공 여부, 적용된 heal_stage)
        """
        # ---------------------------------------------------------------------
        # Heal Attempt 1..MAX_HEAL_ATTEMPTS
        # - 각 attempt는 아래 순서로 처리한다.
        #   1) Deterministic Fallback
        #   2) Candidate Search
        #   3) LLM Heal (HEAL_MODE=on일 때만)
        # ---------------------------------------------------------------------
        original_target = step.get("target") or {}
        query = (
            (original_target.get("name") or "")
            or (original_target.get("text") or "")
            or (original_target.get("label") or "")
        )

        for attempt in range(1, MAX_HEAL_ATTEMPTS + 1):
            # 1) Deterministic Fallback
            fallbacks = step.get("fallback_targets") or []
            if attempt <= len(fallbacks):
                step["target"] = fallbacks[attempt - 1]
                try:
                    self._execute_action(page, resolver, step)
                    return True, f"fallback_{attempt}"
                except Exception as e:
                    error_text = str(e)

            # 2) Candidate Search
            try:
                candidates = collect_accessibility_candidates(page)
                candidates = filter_candidates_by_action(action, candidates)
                ranked = rank_candidates(query, original_target.get("role"), candidates)
                if ranked:
                    top = ranked[0]
                    step["target"] = {"role": top["role"], "name": top["name"]}
                    try:
                        self._execute_action(page, resolver, step)
                        return True, "candidate_search"
                    except Exception as e:
                        error_text = str(e)
            except Exception as e:
                error_text = str(e)

            # 3) LLM Heal
            if HEAL_MODE == "on":
                try:
                    candidates = collect_accessibility_candidates(page)
                    candidates = filter_candidates_by_action(action, candidates)
                    ranked = rank_candidates(query, original_target.get("role"), candidates)

                    heal_prompt = build_llm_heal_prompt(
                        action=action,
                        failed_target=original_target,
                        error_text=error_text,
                        page_url=page.url,
                        ranked_candidates=ranked,
                    )
                    res = self.client.chat(
                        model=self.model,
                        messages=[{"role": "user", "content": heal_prompt}],
                        options={"temperature": 0.1},
                    )
                    heal_obj = json.loads(extract_json_object(res["message"]["content"]))

                    # LLM이 제안한 target/fallback을 step에 반영한다.
                    step["target"] = heal_obj.get("target") or step.get("target") or original_target
                    step["fallback_targets"] = heal_obj.get("fallback_targets") or step.get("fallback_targets") or []

                    # 반영 후 실행을 재시도한다.
                    self._execute_action(page, resolver, step)
                    return True, "llm_heal"
                except Exception as e:
                    error_text = str(e)

        # 모든 복구 실패
        step["target"] = original_target
        return False, "heal_failed"

    def execute(self, scenario: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        시나리오를 실행하고 결과 rows를 반환한다.
        - healed_scenario는 실행 중 갱신된 target/fallback을 포함한다.
        """
        rows: List[Dict[str, Any]] = []
        healed_scenario = json.loads(json.dumps(scenario))

        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=DEFAULT_HEADLESS,
                slow_mo=DEFAULT_SLOW_MO_MS,
            )
            page = browser.new_page()
            page.set_viewport_size({"width": 1280, "height": 800})

            resolver = LocatorResolver(page, FAST_TIMEOUT_MS)

            for step in healed_scenario:
                sid = step.get("step")
                action = step.get("action")
                desc = step.get("description", "")
                heal_stage = "none"
                status = "PASS"
                evidence = ""

                self._log_step_event({
                    "phase": "execute",
                    "step": sid,
                    "action": action,
                    "description": desc,
                    "event": "start",
                })

                try:
                    # 1차 실행
                    self._execute_action(page, resolver, step)

                except Exception as e:
                    # 실패 시 Self-Healing 수행
                    error_text = str(e)
                    self._log_step_event({
                        "phase": "execute",
                        "step": sid,
                        "action": action,
                        "event": "fail",
                        "error": error_text,
                        "target": step.get("target"),
                    })

                    # 치유는 click/fill/check에서만 수행한다.
                    if action in ["click", "fill", "check"]:
                        ok, heal_stage = self._heal_step(
                            page=page,
                            resolver=resolver,
                            step=step,
                            action=action,
                            error_text=error_text,
                        )
                        if not ok:
                            raise RuntimeError(f"Self-healing failed: {error_text}")
                    else:
                        raise

                # 증적 생성
                evidence = f"step_{sid}_pass.png"
                page.screenshot(path=os.path.join(self.out_dir, evidence))

                self._log_step_event({
                    "phase": "execute",
                    "step": sid,
                    "action": action,
                    "event": "pass",
                    "heal_stage": heal_stage,
                    "url": page.url,
                    "evidence": evidence,
                })

                rows.append({
                    "step": sid,
                    "action": action,
                    "description": desc,
                    "heal_stage": heal_stage,
                    "status": status,
                    "evidence": evidence,
                })

            browser.close()

        return rows, healed_scenario

    def save_report(self, rows: List[Dict[str, Any]]) -> None:
        """HTML 리포트를 파일로 저장한다."""
        html = build_html_report(rows)
        with open(self.path_report, "w", encoding="utf-8") as f:
            f.write(html)

    def run(self) -> None:
        """전체 실행 엔트리다."""
        append_jsonl(self.path_log, {"ts": now_iso(), "phase": "run", "status": "start"})

        scenario = self.plan_scenario()

        rows, healed = self.execute(scenario)

        # healed scenario 저장
        write_json(self.path_healed, healed)

        # report 저장
        self.save_report(rows)

        append_jsonl(self.path_log, {"ts": now_iso(), "phase": "run", "status": "end"})


# -----------------------------------------------------------------------------
# [Entry Point] CLI 인자 처리
# -----------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser()

    # -------------------------------------------------------------------------
    # url
    # - 테스트 대상 웹 애플리케이션 진입 URL이다.
    # -------------------------------------------------------------------------
    parser.add_argument("--url", required=True, help="Target webapp URL")

    # -------------------------------------------------------------------------
    # srs_file
    # - 자연어 요구사항 텍스트 파일 경로다.
    # -------------------------------------------------------------------------
    parser.add_argument("--srs_file", required=True, help="Path to SRS(.txt) file")

    # -------------------------------------------------------------------------
    # out
    # - 산출물 저장 경로다.
    # - Jenkins에서는 BUILD_NUMBER 기반 폴더를 권장한다.
    # -------------------------------------------------------------------------
    parser.add_argument("--out", required=True, help="Output directory for artifacts")

    # -------------------------------------------------------------------------
    # ollama_host/model
    # - LLM 호출 환경을 명시한다.
    # - 폐쇄망 운영에서는 내부 고정 값으로 통제하는 것을 권장한다.
    # -------------------------------------------------------------------------
    parser.add_argument("--ollama_host", default=DEFAULT_OLLAMA_HOST, help="Ollama host URL")
    parser.add_argument("--model", default=DEFAULT_MODEL_NAME, help="LLM model name")

    args = parser.parse_args()

    try:
        with open(args.srs_file, "r", encoding="utf-8") as f:
            srs_text = f.read()
    except Exception as e:
        log(f"CRITICAL: cannot read srs_file: {args.srs_file} / error={e}")
        sys.exit(1)

    agent = ZeroTouchAgent(
        url=args.url,
        srs_text=srs_text,
        out_dir=args.out,
        ollama_host=args.ollama_host,
        model=args.model,
    )
    agent.run()


if __name__ == "__main__":
    main()
````

---

## 6. Jenkins 파이프라인 구성

### 6.1 Job 정의

* Job 이름: `DSCORE-ZeroTouch-QA`
* Job 유형: Pipeline
* 입력: `TARGET_URL`, `SRS_FILE`, `MODEL_NAME`, `HEAL_MODE`, `MAX_HEAL_ATTEMPTS`
* 출력: HTML Report 게시 + 핵심 산출물 아카이빙

### 6.2 Jenkinsfile

```groovy
// =============================================================================
// DSCORE-ZeroTouch-QA Jenkinsfile (v1.8)
//
// 목적
// - 사용자가 업로드한 SRS 파일을 기반으로 Zero-Touch QA 에이전트를 실행한다.
// - 실행 산출물(Scenario/Healed/Logs/Report)을 Jenkins UI에 게시하고 아카이빙한다.
//
// 주의
// - 환경 변수로 Healing 동작을 제어한다.
// - 산출물 경로는 DSCORE-TTC 공용 볼륨(/var/knowledges) 규칙을 따른다.
// =============================================================================

pipeline {
    agent any

    // ------------------------------------------------------------------------
    // parameters
    // - 비개발자 입력 가능 항목을 최소화한다.
    // - 모델명, 복구 모드, 복구 시도 횟수는 운영 정책에 따라 제한할 수 있다.
    // ------------------------------------------------------------------------
    parameters {
        string(
            name: 'TARGET_URL',
            defaultValue: 'http://host.docker.internal:3000',
            description: '테스트 대상 웹앱 URL'
        )
        file(
            name: 'SRS_FILE',
            description: '자연어 요구사항 파일(.txt)'
        )
        string(
            name: 'MODEL_NAME',
            defaultValue: 'qwen3-coder:30b',
            description: 'Ollama LLM 모델명'
        )
        choice(
            name: 'HEAL_MODE',
            choices: ['on', 'off'],
            description: 'Self-Healing 활성화 여부'
        )
        string(
            name: 'MAX_HEAL_ATTEMPTS',
            defaultValue: '2',
            description: '최대 복구 시도 횟수'
        )
    }

    // ------------------------------------------------------------------------
    // environment
    // - scripts: Jenkins 컨테이너 내부 스크립트 위치
    // - report: 실행 산출물 저장 위치
    // - ollama: 호스트 Ollama 접근 주소
    // ------------------------------------------------------------------------
    environment {
        SCRIPTS_DIR = '/var/jenkins_home/scripts'
        REPORT_DIR  = "/var/knowledges/qa_reports/${BUILD_NUMBER}"
        OLLAMA_HOST = 'http://host.docker.internal:11434'
    }

    stages {
        stage('1. Prepare Output Directory') {
            steps {
                // -----------------------------------------------------------------
                // 목적
                // - 빌드 번호 단위로 산출물 폴더를 생성한다.
                // -----------------------------------------------------------------
                sh "mkdir -p ${REPORT_DIR}"
            }
        }

        stage('2. Run Zero-Touch QA Agent') {
            steps {
                // -----------------------------------------------------------------
                // withFileParameter
                // - Jenkins file parameter를 워크스페이스 임시 파일로 제공한다.
                // -----------------------------------------------------------------
                withFileParameter(name: 'SRS_FILE', variable: 'UPLOADED_SRS') {
                    script {
                        // ---------------------------------------------------------
                        // 환경 변수 전달
                        // - 에이전트 내부에서 HEAL_MODE, MAX_HEAL_ATTEMPTS를 읽는다.
                        // ---------------------------------------------------------
                        sh """
                        export HEAL_MODE="${params.HEAL_MODE}"
                        export MAX_HEAL_ATTEMPTS="${params.MAX_HEAL_ATTEMPTS}"
                        export OLLAMA_HOST="${env.OLLAMA_HOST}"
                        export MODEL_NAME="${params.MODEL_NAME}"

                        python3 ${SCRIPTS_DIR}/autonomous_qa.py \
                            --url "${params.TARGET_URL}" \
                            --srs_file "${UPLOADED_SRS}" \
                            --out "${REPORT_DIR}" \
                            --ollama_host "${env.OLLAMA_HOST}" \
                            --model "${params.MODEL_NAME}"
                        """
                    }
                }
            }
        }
    }

    post {
        always {
            // -----------------------------------------------------------------
            // HTML Publisher
            // - Jenkins UI에서 index.html을 직접 열람 가능하게 한다.
            // -----------------------------------------------------------------
            publishHTML([
                allowMissing: false,
                alwaysLinkToLastBuild: true,
                keepAll: true,
                reportDir: "${REPORT_DIR}",
                reportFiles: 'index.html',
                reportName: 'Zero-Touch QA Report'
            ])

            // -----------------------------------------------------------------
            // Archive Artifacts
            // - 핵심 산출물을 아카이빙한다.
            // - fingerprint를 통해 빌드 간 추적성을 확보한다.
            // -----------------------------------------------------------------
            archiveArtifacts artifacts: "${REPORT_DIR}/*.json,${REPORT_DIR}/*.jsonl,${REPORT_DIR}/*.html,${REPORT_DIR}/*.png", fingerprint: true
        }
    }
}
```

---

## 7. 사용 절차

### 7.1 SRS 텍스트 작성 규칙(최소 규칙)

SRS는 “테스트하고 싶은 동작”을 문장으로 나열한다.
문장 자체는 자유 형식이지만, 아래처럼 단계가 명확할수록 시나리오 품질이 안정된다.

예시(`login_negative.txt`)

* 로그인 페이지로 이동한다.
* 아이디 입력창에 `admin`을 입력한다.
* 비밀번호 입력창에 `wrong_pw`를 입력한다.
* 로그인 버튼을 클릭한다.
* 로그인 실패 메시지가 표시되는지 확인한다.

### 7.2 Jenkins 실행

1. Jenkins에서 `DSCORE-ZeroTouch-QA` Job을 연다.
2. `Build with Parameters`를 실행한다.
3. `SRS_FILE`에 텍스트 파일을 업로드한다.
4. 필요 시 `HEAL_MODE`, `MAX_HEAL_ATTEMPTS`를 조정한다.
5. Build를 실행한다.

### 7.3 결과 확인

* Jenkins 화면에서 `Zero-Touch QA Report`를 연다.
* 표의 `Healing` 컬럼을 확인한다.

  * `none`: 최초 타겟으로 성공했다.
  * `fallback_1`, `fallback_2`: 시나리오에 포함된 예비 타겟으로 복구했다.
  * `candidate_search`: 접근성 트리 후보로 복구했다.
  * `llm_heal`: LLM 제안 타겟으로 복구했다.
* `run_log.jsonl`에서 step 단위 이벤트를 확인한다.
* `test_scenario.healed.json`에서 “치유된 타겟”이 어떻게 반영되었는지 확인한다.

---

## 8. 운영 튜닝 가이드

### 8.1 Fast-Fail 튜닝

* `FAST_TIMEOUT_MS`를 늘리면 탐색 안정성은 증가한다.
* `FAST_TIMEOUT_MS`를 줄이면 전략 전환이 빨라진다.
* 기본값(1000ms)은 “실패를 빠르게 판정하고 다음 전략으로 이동”하는 목적에 맞춘다.

### 8.2 Healing 강도 조절

* `HEAL_MODE=off`

  * fallback/candidate까지만 사용한다.
  * 운영 환경에서 LLM 호출을 엄격히 제한해야 할 때 사용한다.

* `MAX_HEAL_ATTEMPTS`

  * 값이 커질수록 복구 시도는 늘지만, 실행 시간도 늘어난다.
  * 2를 기본으로 두고, UI 변동이 큰 구간에서만 상향한다.

### 8.3 로그 강화 방향

`run_log.jsonl`은 운영/디버깅의 중심이다.
추가로 강화할 항목은 아래 순서가 안정적이다.

* (1) step 시작/종료 시간(ms) 기록
* (2) heal 시도별 후보 Top N과 점수 기록
* (3) 실패 유형 분류(Timeout, NotFound, Navigation 등) 기록
* (4) URL 변화(history) 기록

---

## 9. 정상 동작 판정 기준

아래 파일이 모두 생성되면 “구성 관점 정상”으로 판정한다.

* `test_scenario.json`
* `test_scenario.healed.json`
* `run_log.jsonl`
* `index.html`
* `regression_test.py`
* `step_*.png`

추가로 `Healing`이 `none`이 아니더라도 실패로 보지 않는다.
Self-Healing으로 성공했으면 “의도한 기능이 작동”한 것으로 본다.

---

원하시면, 다음 단계로 **로그 스키마(필드 표준)**를 별도 섹션으로 고정하고, `run_log.jsonl`을 기반으로 **실패 유형별 집계 리포트(JSON → HTML Summary)**까지 포함한 **v1.9 강화판** 형태로 바로 확장해드리겠습니다.
