# =============================================================================
# DSCORE-TTC Zero-Touch QA Agent (v1.8)
#
# 목적
# - 자연어 요구사항(SRS)을 입력받아 Intent 기반 테스트 시나리오를 생성한다.
# - Jenkins 파이프라인을 통해 업로드된 자연어 요구사항(SRS) 파일을 기반으로 테스트 시나리오를 생성하고 실행한다.
# - Playwright로 시나리오를 실행한다.
# - UI 변경으로 실패하는 경우, 3단계 Self-Healing으로 복구를 시도한다.
# - 실행 결과를 Artifact(시나리오/치유 시나리오/로그/리포트/스크린샷)로 남긴다.
#
# 핵심 원칙
# - Intent-Driven: role/name/label/text 기반 탐색을 우선한다.
# - Self-Healing: fallback -> candidate search -> LLM heal 순으로 복구한다.
# - Sequential Fast-Fail: 병렬 탐색을 사용하지 않는다.
#   각 탐색 전략은 짧은 timeout으로 순차 시도한다.
#
# 데이터 흐름
# - 입력 소스: 사용자가 Jenkins UI를 통해 직접 업로드한 자연어 요구사항 파일 (.txt)
# - 전달 방식: Jenkins 파이프라인의 'file' 파라미터(UPLOADED_SRS)를 통해 스크립트에 전달됨.
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
# Ollama LLM API의 접속 주소.
# Docker 컨테이너 내부에서 실행될 때, 호스트 머신에서 실행 중인 Ollama에
# 접근하기 위해 'host.docker.internal'을 기본값으로 사용한다.
DEFAULT_OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://host.docker.internal:11434")

# 시나리오 생성 및 Self-Healing에 사용할 LLM 모델 이름.
DEFAULT_MODEL_NAME = os.getenv("MODEL_NAME", "qwen3-coder:30b")

# 브라우저의 Headless 모드 활성화 여부.
# '1'로 설정하면 UI 없이 백그라운드에서 실행된다. Jenkins 등 CI 환경에서는 필수.
DEFAULT_HEADLESS = os.getenv("HEADLESS", "1") == "1"

# Playwright의 각 액션 사이에 추가할 지연 시간(ms).
# 너무 빠르게 테스트가 진행될 경우, 페이지 렌더링이나 비동기 작업이 완료되지 않아
# 테스트가 불안정해지는 것을 방지한다.
DEFAULT_SLOW_MO_MS = int(os.getenv("SLOW_MO_MS", "300"))

# Playwright가 요소를 찾거나 특정 상태가 되기를 기다리는 기본 대기 시간(ms).
# 이 시간을 초과하면 'TimeoutError'가 발생한다.
DEFAULT_TIMEOUT_MS = int(os.getenv("DEFAULT_TIMEOUT_MS", "10000"))

# LocatorResolver에서 각 선택자 전략을 시도할 때 사용하는 짧은 대기 시간(ms).
# 하나의 전략이 실패했을 때 오랜 시간 기다리지 않고, 빠르게 다음 전략으로
# 넘어가기 위한 '빠른 실패(Fast-Fail)' 원칙을 구현한다.
FAST_TIMEOUT_MS = int(os.getenv("FAST_TIMEOUT_MS", "1000"))

# LLM을 이용한 3단계 Self-Healing 기능의 활성화 여부 ('on' 또는 'off').
# 'off'로 설정하면 Fallback과 Candidate Search까지만 시도한다.
HEAL_MODE = os.getenv("HEAL_MODE", "on")

# Self-Healing(Fallback, Candidate Search, LLM Heal)의 최대 시도 횟수.
# 무한 루프에 빠지는 것을 방지한다.
MAX_HEAL_ATTEMPTS = int(os.getenv("MAX_HEAL_ATTEMPTS", "2"))

# Heuristic Search(Candidate Search) 결과 중, LLM에게 참고자료로 넘겨줄
# 상위 후보의 개수. 너무 많으면 프롬프트가 길어지고, 너무 적으면 정보가 부족할 수 있다.
CANDIDATE_TOP_N = int(os.getenv("CANDIDATE_TOP_N", "8"))

# -----------------------------------------------------------------------------
# [Utils] 공용 유틸리티
# -----------------------------------------------------------------------------
def now_iso() -> str:
    """로그 타임스탬프를 ISO 8601 형식의 문자열로 생성한다."""
    return datetime.now().isoformat(timespec="seconds")

def log(msg: str) -> None:
    """
    표준 출력으로 로그 메시지를 출력한다. Jenkins Console Output 등에서 즉시 확인 가능하다.
    flush=True를 통해 버퍼링 없이 즉시 출력되도록 보장한다.
    """
    print(f"[AutoQA] {msg}", flush=True)

def ensure_dir(path: str) -> None:
    """
    지정된 경로에 디렉터리가 존재하지 않으면 생성한다.
    산출물을 저장하기 전에 호출하여 경로 존재를 보장한다.
    """
    try:
        os.makedirs(path, exist_ok=True)
    except Exception as e:
        log(f"CRITICAL: cannot create directory: {path} / error={e}")
        sys.exit(1)

def write_json(path: str, obj: Any) -> None:
    """주어진 파이썬 객체를 JSON 파일로 저장한다."""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)

def append_jsonl(path: str, obj: Dict[str, Any]) -> None:
    """
    JSONL(JSON Lines) 형식의 로그 파일에 한 줄(객체 하나)을 추가한다.
    스트리밍 데이터 처리에 용이하며, 파일이 커져도 빠르게 추가할 수 있다.
    """
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")

def extract_json_array(text: str) -> str:
    """
    LLM 응답 텍스트에서 순수한 JSON 배열([]) 부분만 추출한다.
    LLM이 JSON 외에 설명 텍스트나 코드펜스(```json ... ```)를 포함하여 응답하는 경우가
    많기 때문에, 안정적인 파싱을 위해 이 함수가 필요하다.
    """
    # 코드펜스 및 불필요한 공백 제거
    t = text.strip().replace("```json", "").replace("```", "").strip()
    start = t.find("[")
    end = t.rfind("]")
    if start == -1 or end == -1:
        raise ValueError("LLM response does not contain JSON array")
    return t[start:end + 1]

def extract_json_object(text: str) -> str:
    """
    LLM 응답 텍스트에서 순수한 JSON 객체({}) 부분만 추출한다.
    배열이 아닌 단일 객체를 응답으로 받을 때 사용한다.
    """
    t = text.strip().replace("```json", "").replace("```", "").strip()
    start = t.find("{")
    end = t.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("LLM response does not contain JSON object")
    return t[start:end + 1]

def similarity(a: str, b: str) -> float:
    """
    두 문자열 간의 유사도를 0.0에서 1.0 사이의 값으로 계산한다. (SequenceMatcher 사용)
    Heuristic Search에서 원래 찾으려던 요소와 현재 화면의 후보 요소 간의 텍스트를
    비교하여 가장 '비슷한' 요소를 찾는 데 사용된다.
    """
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()

# -----------------------------------------------------------------------------
# [Model] Intent Target 정의
# -----------------------------------------------------------------------------
@dataclass
class IntentTarget:
    """
    UI 요소를 '어떻게' 찾을지가 아닌 '무엇을' 찾을지에 대한 '의도'를 표현하는 데이터 구조.
    깨지기 쉬운 CSS 선택자나 XPath 대신, 사용자가 인지하는 속성(역할, 이름, 레이블 등)을
    사용하여 테스트의 안정성과 가독성을 높인다.

    Attributes:
        role: ARIA role (e.g., 'button', 'link'). 가장 우선적으로 사용되는 속성.
        name: 접근성 이름 (e.g., '로그인'). role과 조합하여 요소를 특정한다.
        label: `<label>` 태그와 연결된 폼 요소(input, textarea 등)를 찾을 때 사용.
        text: 요소가 포함하고 있는 텍스트.
        placeholder: 입력 필드의 플레이스홀더 텍스트.
        testid: `data-testid` 속성. 개발팀과 협의하여 사용하는 테스트 전용 식별자.
        selector: 최후의 수단으로 사용되는 CSS 또는 XPath 선택자.
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
        """딕셔너리에서 IntentTarget 객체를 생성한다."""
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
        """IntentTarget 객체를 딕셔너리로 변환한다."""
        return asdict(self)

    def brief(self) -> str:
        """로그 출력 등을 위한 간략한 요약 문자열을 반환한다."""
        return f"role={self.role}, name={self.name}, label={self.label}, text={self.text}"

# -----------------------------------------------------------------------------
# [Resolver] Intent 기반 Locator 해석기 (Sequential Fast-Fail)
# -----------------------------------------------------------------------------
class LocatorResolver:
    """
    IntentTarget을 Playwright의 Locator 객체로 변환하는 역할을 담당한다.
    안정성이 높은 전략부터 순차적으로, 그리고 빠르게(Fast-Fail) 시도하여
    가장 적합한 요소를 찾아낸다.
    """

    def __init__(self, page, fast_timeout_ms: int):
        self.page = page
        self.fast_timeout_ms = fast_timeout_ms

    def _try_visible(self, locator) -> bool:
        """
        주어진 locator에 해당하는 요소가 화면에 보이는지(visible) 짧게 확인한다.
        FAST_TIMEOUT_MS를 사용하여 응답이 없으면 빠르게 실패하고 False를 반환한다.
        """
        try:
            # first: 여러 요소가 찾아지더라도 첫 번째 요소만 확인하여 성능 확보
            locator.first.wait_for(state="visible", timeout=self.fast_timeout_ms)
            return True
        except Exception:
            # Timeout 등 예외 발생 시, 요소를 찾지 못한 것으로 간주
            return False

    def resolve(self, target: IntentTarget):
        """
        주어진 IntentTarget에 대해 여러 선택자 전략을 순서대로 시도한다.
        하나의 전략이라도 성공하면, 해당 Locator를 즉시 반환한다.

        탐색 우선순위:
        1. Role + Name: 가장 명확하고 접근성이 높은 방법. (예: "로그인" 버튼)
        2. Label: 폼 입력 요소를 찾을 때 가장 적합. (예: "아이디" 레이블이 붙은 입력창)
        3. Text: 페이지에 보이는 텍스트로 탐색. (부분 일치)
        4. Placeholder: 입력창의 placeholder 텍스트로 탐색.
        5. Test ID: 테스트를 위해 부여된 `data-testid` 속성으로 탐색.
        6. Selector: 최후의 수단으로 CSS 또는 XPath 직접 사용.
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
        
        # 모든 전략이 실패하면 에러 발생
        raise RuntimeError(f"Target not resolved: {target.brief()}")

# -----------------------------------------------------------------------------
# [Healing] Candidate Search (Heuristic-based Healing)
# -----------------------------------------------------------------------------
def collect_accessibility_candidates(page) -> List[Dict[str, str]]:
    """
    페이지의 접근성 트리(Accessibility Tree)를 스캔하여 상호작용 가능한 모든 요소의
    'role'과 'name'을 수집한다. 이는 Self-Healing의 2단계인 Heuristic Search에서
    복구 대상을 찾기 위한 후보 목록(candidates)을 만드는 데 사용된다.
    """
    # 페이지의 현재 접근성 스냅샷을 가져온다.
    snapshot = page.accessibility.snapshot()
    results: List[Dict[str, str]] = []

    def walk(node: Dict[str, Any]) -> None:
        """접근성 트리를 재귀적으로 순회하며 role과 name이 있는 노드를 수집한다."""
        role = node.get("role") or ""
        name = node.get("name") or ""
        # 의미 있는 요소(role과 name이 모두 존재)만 후보로 간주
        if role and name:
            results.append({"role": role, "name": name})
        for child in node.get("children", []) or []:
            walk(child)
    
    # 스냅샷이 존재하면 트리 순회 시작
    if snapshot:
        walk(snapshot)

    # 중복된 후보(동일한 role과 name)를 제거하여 목록을 정리한다.
    dedup: Dict[str, Dict[str, str]] = {}
    for r in results:
        key = f"{r['role']}|{r['name']}"
        dedup[key] = r

    return list(dedup.values())

def filter_candidates_by_action(action: str, candidates: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """
    수집된 후보 목록을 실패한 '액션'의 종류에 따라 필터링한다.
    예를 들어 'fill'(입력) 액션이 실패했다면, 복구 후보도 입력 가능한 요소
    (textbox 등) 중에서 찾는 것이 합리적이다.
    """
    if action == "click":
        # 클릭 가능한 역할들
        allowed = {"button", "link", "menuitem", "tab", "checkbox", "radio"}
        return [c for c in candidates if c.get("role") in allowed]
    if action == "fill":
        # 입력 가능한 역할들
        allowed = {"textbox", "searchbox", "combobox"}
        return [c for c in candidates if c.get("role") in allowed]
    
    # 'check' 등 다른 액션은 특별히 제한하지 않음
    return candidates

def rank_candidates(query: str, target_role: Optional[str], candidates: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    """
    필터링된 후보 목록을 원래 찾으려던 요소와의 유사도를 기준으로 순위를 매긴다.

    Args:
        query: 원래 찾으려던 요소의 텍스트 (name, text, label 등).
        target_role: 원래 찾으려던 요소의 역할.
        candidates: 후보 요소 목록.

    Returns:
        점수(score)가 추가되고 내림차순으로 정렬된 후보 목록.
    """
    scored: List[Dict[str, Any]] = []
    for c in candidates:
        # 1. 기본 점수: 원래 텍스트(query)와 후보의 이름(name) 간의 문자열 유사도.
        s = similarity(query, c.get("name", ""))
        # 2. 가산점: 만약 원래 역할과 후보의 역할이 같다면, 더 관련성이 높다고 판단하여 보너스 점수 부여.
        if target_role and c.get("role") == target_role:
            s += 0.10
        scored.append({"role": c.get("role", ""), "name": c.get("name", ""), "score": round(s, 3)})
    
    # 최종 점수가 높은 순으로 정렬
    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored

# -----------------------------------------------------------------------------
# [Healing] LLM Heal Prompt (AI-based Healing)
# -----------------------------------------------------------------------------
def build_llm_heal_prompt(
    action: str,
    failed_target: Dict[str, Any],
    error_text: str,
    page_url: str,
    ranked_candidates: List[Dict[str, Any]],
) -> str:
    """
    Self-Healing 3단계인 LLM기반 복구를 위해, LLM에게 전달할 프롬프트를 생성한다.
    실패 상황에 대한 상세한 맥락(실패한 액션, 타겟, 에러, URL, 유력한 후보 목록 등)을
    제공하여, LLM이 정확한 해결책을 JSON 형식으로 제안하도록 유도한다.
    """
    # 후보 목록을 JSON 문자열로 변환하여 프롬프트에 포함
    candidates_json = json.dumps(ranked_candidates[:CANDIDATE_TOP_N], ensure_ascii=False, indent=2)

    # 프롬프트 템플릿
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
    테스트 실행 결과를 담은 `rows` 데이터를 기반으로, Jenkins 파이프라인 등에서
    쉽게 확인할 수 있는 단일 HTML 리포트 파일을 생성한다.
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
    QA 자동화의 전체 흐름(Plan -> Execute -> Heal -> Report)을 관장하는 메인 클래스.
    """

    def __init__(self, url: str, srs_text: str, out_dir: str, ollama_host: str, model: str):
        self.url = url
        self.srs_text = srs_text
        self.out_dir = out_dir
        self.ollama_host = ollama_host
        self.model = model

        # 산출물 디렉터리 생성 보장
        ensure_dir(out_dir)

        # 주요 산출물의 전체 경로 정의
        self.path_scenario = os.path.join(out_dir, "test_scenario.json")
        self.path_healed = os.path.join(out_dir, "test_scenario.healed.json")
        self.path_log = os.path.join(out_dir, "run_log.jsonl")
        self.path_report = os.path.join(out_dir, "index.html")

        # Ollama 클라이언트 초기화
        self.client = ollama.Client(host=ollama_host)

    def plan_scenario(self) -> List[Dict[str, Any]]:
        """
        자연어 요구사항(SRS)을 LLM에 보내, 실행 가능한 테스트 시나리오(JSON)를 생성한다.
        이 단계는 자동화의 'Plan' 단계에 해당한다.
        """
        log(f"Plan: Generating scenario using model={self.model}")
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
4. 각 주요 동작 후에는 check step을 포함하여 상태를 검증한다.
5. 출력은 JSON 배열만 허용한다. 다른 설명은 절대 포함하지 마라.

[Step 예시]
[
  {{"step": 1, "action": "navigate", "value": "{self.url}", "description": "메인 페이지 접속"}},
  {{"step": 2, "action": "click", "target": {{"role": "button", "name": "로그인"}}, "fallback_targets":[{{"text":"로그인"}}, {{"role":"link","name":"로그인"}}], "description":"로그인 버튼 클릭"}},
  {{"step": 3, "action": "check", "target": {{"text": "로그인"}}, "description":"로그인 화면 요소 확인"}}
]
""".strip()

        # LLM 호출
        res = self.client.chat(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            options={"temperature": 0.1}, # 낮은 온도로 일관성 있는 결과 유도
        )
        # LLM 응답에서 순수 JSON 배열만 추출
        scenario_text = extract_json_array(res["message"]["content"])
        scenario = json.loads(scenario_text)

        # 생성된 원본 시나리오를 파일로 저장
        write_json(self.path_scenario, scenario)

        # 실행 로그에 Plan 단계 완료 기록
        append_jsonl(self.path_log, {
            "ts": now_iso(),
            "phase": "plan",
            "status": "ok",
            "model": self.model,
            "scenario_steps": len(scenario),
        })

        return scenario

    def _log_step_event(self, payload: Dict[str, Any]) -> None:
        """테스트 실행 중 발생하는 주요 이벤트를 구조화된 로그(JSONL)로 기록한다."""
        base = {"ts": now_iso()}
        base.update(payload)
        append_jsonl(self.path_log, base)

    def _execute_action(self, page, resolver: LocatorResolver, step: Dict[str, Any]) -> None:
        """
        시나리오의 단일 스텝(step)에 정의된 액션을 수행한다.
        'click', 'fill', 'check' 액션은 LocatorResolver를 통해 동적으로 요소를 찾는다.
        """
        action = step.get("action")
        
        if action == "navigate":
            page.goto(step.get("value") or self.url, timeout=60000)
            page.wait_for_load_state("networkidle") # 네트워크가 안정될 때까지 대기
            return

        if action == "wait":
            ms = int(step.get("value", 1500))
            page.wait_for_timeout(ms)
            return

        # Intent 기반의 액션 처리
        if action in ["click", "fill", "check"]:
            target_dict = step.get("target") or {}
            target = IntentTarget.from_dict(target_dict)
            # Resolver를 통해 IntentTarget을 실제 Locator로 변환
            loc = resolver.resolve(target)

            if action == "click":
                loc.first.click(timeout=DEFAULT_TIMEOUT_MS)
                return

            if action == "fill":
                loc.first.fill(str(step.get("value", "")), timeout=DEFAULT_TIMEOUT_MS)
                return

            if action == "check":
                # 'check'는 해당 요소가 화면에 보이는지 검증하는 동작
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
        테스트 스텝 실행 실패 시, 3단계에 걸친 Self-Healing을 수행한다.
        1. Deterministic Fallback -> 2. Heuristic Search -> 3. LLM Heal

        Returns:
            (성공 여부, 적용된 복구 단계 이름)
        """
        original_target = step.get("target") or {}
        # Heuristic/LLM Search에 사용할 검색어 추출 (name > text > label 순)
        query = (
            (original_target.get("name") or "")
            or (original_target.get("text") or "")
            or (original_target.get("label") or "")
        )

        for attempt in range(1, MAX_HEAL_ATTEMPTS + 1):
            log(f"Healing attempt {attempt}/{MAX_HEAL_ATTEMPTS} for step {step.get('step')}")

            # --- 1단계: Deterministic Fallback ---
            fallbacks = step.get("fallback_targets") or []
            if attempt <= len(fallbacks):
                log("Healing strategy: 1. Fallback")
                step["target"] = fallbacks[attempt - 1]
                try:
                    self._execute_action(page, resolver, step)
                    return True, f"fallback_{attempt}"
                except Exception as e:
                    error_text = str(e) # 다음 단계를 위해 에러 메시지 갱신

            # --- 2단계: Heuristic Search (Candidate Search) ---
            log("Healing strategy: 2. Candidate Search")
            try:
                candidates = collect_accessibility_candidates(page)
                candidates = filter_candidates_by_action(action, candidates)
                ranked = rank_candidates(query, original_target.get("role"), candidates)
                
                if ranked and ranked[0]["score"] > 0.3: # 최소 유사도 임계치
                    top = ranked[0]
                    step["target"] = {"role": top["role"], "name": top["name"]}
                    self._execute_action(page, resolver, step)
                    return True, "candidate_search"
            except Exception as e:
                error_text = str(e)

            # --- 3단계: LLM Heal ---
            if HEAL_MODE == "on":
                log("Healing strategy: 3. LLM Heal")
                try:
                    # LLM에게 전달할 최신 후보 목록 다시 수집
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

                    # LLM 제안으로 step 정보 업데이트
                    step["target"] = heal_obj.get("target") or original_target
                    step["fallback_targets"] = heal_obj.get("fallback_targets") or []

                    self._execute_action(page, resolver, step)
                    return True, "llm_heal"
                except Exception as e:
                    error_text = str(e)
        
        # 모든 복구 시도 실패
        log(f"All healing attempts failed for step {step.get('step')}")
        step["target"] = original_target # step을 원상 복구
        return False, "heal_failed"

    def execute(self, scenario: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        주어진 시나리오를 Playwright를 사용하여 실행한다. 실패 시 Self-Healing을 시도한다.
        
        Returns:
            (리포트 생성을 위한 결과 요약 rows, 복구 내용이 반영된 최종 시나리오)
        """
        log("Execute: Starting scenario execution")
        rows: List[Dict[str, Any]] = []
        # 원본 시나리오의 깊은 복사본을 만들어, 복구 시 변경 내용을 여기에 반영
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
                    "phase": "execute", "step": sid, "action": action, 
                    "description": desc, "event": "start",
                })

                try:
                    # 1차 실행 시도
                    self._execute_action(page, resolver, step)

                except Exception as e:
                    # 1차 실행 실패: 에러 기록 및 Self-Healing 시작
                    error_text = str(e)
                    self._log_step_event({
                        "phase": "execute", "step": sid, "action": action, 
                        "event": "fail", "error": error_text, "target": step.get("target"),
                    })

                    # 치유는 의미 있는 상호작용(click, fill, check)에 대해서만 수행
                    if action in ["click", "fill", "check"]:
                        ok, heal_stage = self._heal_step(
                            page=page, resolver=resolver, step=step,
                            action=action, error_text=error_text,
                        )
                        if not ok:
                            # 최종 복구 실패 시, 전체 테스트 중단
                            status = "FAIL"
                            raise RuntimeError(f"Self-healing failed for step {sid}: {error_text}")
                    else:
                        # navigate, wait 등은 치유 대상이 아님
                        status = "FAIL"
                        raise

                finally:
                    # 스텝이 성공했거나, 치유에 성공했을 경우
                    if status == "PASS":
                        evidence = f"step_{sid}_pass.png"
                        page.screenshot(path=os.path.join(self.out_dir, evidence))

                        self._log_step_event({
                            "phase": "execute", "step": sid, "action": action, 
                            "event": "pass", "heal_stage": heal_stage, "url": page.url,
                            "evidence": evidence,
                        })

                    # 최종 결과 row 추가
                    rows.append({
                        "step": sid, "action": action, "description": desc,
                        "heal_stage": heal_stage, "status": status, "evidence": evidence,
                    })

            browser.close()

        log("Execute: Scenario execution finished")
        return rows, healed_scenario

    def save_report(self, rows: List[Dict[str, Any]]) -> None:
        """HTML 리포트를 생성하여 파일로 저장한다."""
        log("Report: Saving HTML report")
        html = build_html_report(rows)
        with open(self.path_report, "w", encoding="utf-8") as f:
            f.write(html)

    def run(self) -> None:
        """에이전트의 전체 작업을 순서대로 실행하는 메인 엔트리포인트."""
        self._log_step_event({"phase": "run", "status": "start"})

        try:
            # 1. Plan: SRS로부터 시나리오 생성
            scenario = self.plan_scenario()

            # 2. Execute & Heal: 시나리오 실행 및 자동 복구
            rows, healed = self.execute(scenario)

            # 3. Report: 결과 저장
            write_json(self.path_healed, healed) # 복구된 시나리오 저장
            self.save_report(rows) # HTML 리포트 저장

        except Exception as e:
            log(f"CRITICAL: A fatal error occurred during the run: {e}")
            self._log_step_event({"phase": "run", "status": "fatal", "error": str(e)})
            # 에러 발생 시에도 현재까지의 로그는 남아있음
            sys.exit(1)

        self._log_step_event({"phase": "run", "status": "end"})
        log("Run finished successfully.")


# -----------------------------------------------------------------------------
# [Entry Point] CLI 인자 처리
# -----------------------------------------------------------------------------
def main() -> None:
    """스크립트 실행 시 커맨드라인 인자를 파싱하고 ZeroTouchAgent를 실행한다."""
    parser = argparse.ArgumentParser(description="DSCORE-TTC Zero-Touch QA Agent")

    parser.add_argument("--url", required=True, help="Target web application URL")
    parser.add_argument("--srs_file", required=True, help="Path to SRS (.txt) file. Provided via Jenkins pipeline parameters.")
    parser.add_argument("--out", required=True, help="Output directory for all artifacts (reports, logs, screenshots)")
    
    # 환경변수 대신 인자로도 설정 오버라이드 가능
    parser.add_argument("--ollama_host", default=DEFAULT_OLLAMA_HOST, help="Ollama host URL")
    parser.add_argument("--model", default=DEFAULT_MODEL_NAME, help="LLM model name to use")

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
