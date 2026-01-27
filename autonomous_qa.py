#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# [프로젝트] DSCORE-TTC Zero-Touch QA Agent (v2.5 - Annotated Version)
#
# [개요]
#  - 사용자가 자연어(SRS)로 작성한 테스트 요구사항을 입력받아 자동으로 수행하는 AI 에이전트입니다.
#  - Playwright를 통해 브라우저를 제어하며, 실패 시 스스로 복구(Self-Healing)합니다.
#  - 클릭 후 새 탭/새 창이 열리는 경우를 감지하여 자동으로 제어권을 넘깁니다.
#  - 테스트 실패 시 Jenkins가 알 수 있도록 실패 코드(Exit Code 1)를 반환합니다.
#
# [주요 기능]
#  1. Plan: LLM(Ollama)을 사용하여 자연어 -> 테스트 시나리오(JSON) 변환
#  2. Execute: Playwright를 사용해 웹 브라우저 액션 수행 (클릭, 입력, 이동 등)
#  3. Self-Healing: 요소를 못 찾을 경우 3단계 전략(Fallback -> Accessibility -> LLM)으로 복구
#  4. Context Switching: 새 창이 열리면 자동으로 포커스를 이동하여 테스트 지속
#  5. Report: 실행 결과, 스크린샷, 로그를 HTML 리포트로 저장
#
# [작성자] DSCORE DevOps Team
# =============================================================================

import argparse
import json
import os
import sys
from dataclasses import dataclass, asdict
from datetime import datetime
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional, Tuple

# 외부 라이브러리 (pip install ollama playwright 필요)
import ollama
from playwright.sync_api import sync_playwright

# =============================================================================
# 1. 환경 설정 (Configuration)
# - Jenkinsfile 또는 Docker env에서 주입된 환경 변수를 로드합니다.
# =============================================================================

# Ollama 서버 주소 (기본값: Docker 호스트 내부 통신용 주소)
# - Jenkins 컨테이너에서 호스트의 Ollama를 호출하기 위해 사용됩니다.
DEFAULT_OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://host.docker.internal:11434")

# 사용할 LLM 모델 (코드 생성에 특화된 모델 권장, 예: qwen2.5-coder 등)
DEFAULT_MODEL_NAME = os.getenv("MODEL_NAME", "qwen3-coder:30b")

# Headless 모드 설정 (True: 브라우저 숨김, False: 브라우저 표시)
# - Jenkins 서버 환경(CLI)에서는 화면이 없으므로 반드시 "true"여야 합니다.
_headless_env = os.getenv("HEADLESS", "true").lower()
DEFAULT_HEADLESS = _headless_env in ("true", "1", "on", "yes")

# 동작 속도 조절 (ms 단위)
# - 너무 빠르면 웹사이트가 반응하기 전에 다음 동작을 수행해서 실패할 수 있습니다.
# - 사람처럼 천천히 동작하도록 0.5초(500ms) 지연을 줍니다.
DEFAULT_SLOW_MO_MS = int(os.getenv("SLOW_MO_MS", "500"))

# 기본 타임아웃 (30초)
# - 페이지 로딩이나 요소 찾기가 30초 내에 안 되면 실패로 간주합니다.
DEFAULT_TIMEOUT_MS = int(os.getenv("DEFAULT_TIMEOUT_MS", "30000"))

# 빠른 실패(Fail-Fast) 타임아웃 (2초)
# - 요소를 찾을 때, 일단 2초만 찾아보고 없으면 바로 "자가 치유(Healing)" 로직으로 넘어가기 위함입니다.
FAST_TIMEOUT_MS = int(os.getenv("FAST_TIMEOUT_MS", "2000"))

# 자가 치유(Self-Healing) 기능 활성화 여부
HEAL_MODE = os.getenv("HEAL_MODE", "on")

# 자가 치유 최대 시도 횟수
# - 무한 루프에 빠지지 않도록 최대 2번까지만 복구를 시도합니다.
MAX_HEAL_ATTEMPTS = int(os.getenv("MAX_HEAL_ATTEMPTS", "2"))

# 치유 시 LLM에게 보낼 후보 요소의 최대 개수
# - 페이지의 모든 요소를 다 보내면 LLM이 헷갈려하므로, 유사도 높은 상위 8개만 보냅니다.
CANDIDATE_TOP_N = int(os.getenv("CANDIDATE_TOP_N", "8"))


# =============================================================================
# 2. 유틸리티 함수 (Utils)
# - 로그, 파일 입출력, 문자열 처리 등 공통 기능을 담당합니다.
# =============================================================================

def now_iso() -> str:
    """현재 시간을 ISO 8601 형식 문자열(초 단위)로 반환합니다."""
    return datetime.now().isoformat(timespec="seconds")

def log(msg: str) -> None:
    """
    로그를 표준 출력(Console)에 즉시 출력합니다.
    Jenkins 콘솔 로그에서 진행 상황을 실시간으로 보기 위함입니다.
    """
    print(f"[AutoQA] {msg}", flush=True)

def ensure_dir(path: str) -> None:
    """
    지정된 경로에 디렉터리가 없으면 생성합니다.
    권한 문제 등으로 생성 실패 시, 더 이상 진행할 수 없으므로 프로그램을 종료합니다.
    """
    try:
        os.makedirs(path, exist_ok=True)
    except Exception as e:
        log(f"CRITICAL: cannot create directory: {path} / error={e}")
        sys.exit(1)

def write_json(path: str, obj: Any) -> None:
    """파이썬 객체(Dict, List)를 JSON 파일로 저장합니다."""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)

def append_jsonl(path: str, obj: Dict[str, Any]) -> None:
    """
    객체를 JSONL(Line-delimited JSON) 파일에 한 줄 추가합니다.
    실행 로그를 한 줄씩 누적 기록할 때 사용합니다.
    """
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")

def extract_json_array(text: str) -> str:
    """
    LLM 응답 텍스트에서 ```json [ ... ] ``` 부분만 추출하여 순수 JSON 배열 문자열을 반환합니다.
    LLM이 잡담(설명)을 섞어서 대답하는 경우를 처리하기 위함입니다.
    """
    t = text.strip().replace("```json", "").replace("```", "").strip()
    start = t.find("[")
    end = t.rfind("]")
    if start == -1 or end == -1:
        raise ValueError("LLM response does not contain JSON array")
    return t[start:end + 1]

def extract_json_object(text: str) -> str:
    """LLM 응답 텍스트에서 ```json { ... } ``` 부분만 추출합니다."""
    t = text.strip().replace("```json", "").replace("```", "").strip()
    start = t.find("{")
    end = t.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("LLM response does not contain JSON object")
    return t[start:end + 1]

def similarity(a: str, b: str) -> float:
    """
    두 문자열 간의 유사도를 0.0 ~ 1.0 사이 값으로 계산합니다.
    (Self-Healing 시 요소의 이름이 조금 바뀌었더라도 찾기 위함)
    """
    if not a or not b: return 0.0
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


# =============================================================================
# 3. 데이터 모델 (Data Models)
# - 테스트 대상 요소(Target)를 정의하는 구조체입니다.
# =============================================================================

@dataclass
class IntentTarget:
    """
    테스트 하려는 UI 요소의 속성들을 정의합니다.
    LLM은 이 구조에 맞춰 타겟을 제안합니다.
    """
    role: Optional[str] = None        # 예: button, link, textbox (접근성 역할)
    name: Optional[str] = None        # 예: "로그인", "Submit" (접근성 이름)
    label: Optional[str] = None       # 예: "이메일 주소" (Label 태그)
    text: Optional[str] = None        # 예: "회원가입 하기" (화면에 보이는 텍스트)
    placeholder: Optional[str] = None # 예: "비밀번호를 입력하세요"
    testid: Optional[str] = None      # 예: "data-testid" 속성 (테스트 전용 ID)
    selector: Optional[str] = None    # 예: "#login-btn", ".nav-item" (CSS 선택자)

    @staticmethod
    def from_dict(d: Any) -> "IntentTarget":
        """
        딕셔너리 또는 문자열을 받아 IntentTarget 객체로 변환합니다.
        
        [중요 - 방어 로직] 
        LLM이 가끔 JSON 객체가 아닌 단순 문자열("로그인 버튼")을 줄 때가 있습니다.
        이 경우 에러(AttributeError)가 나지 않도록, 문자열을 'text' 속성으로 자동 매핑합니다.
        """
        if isinstance(d, str):
            return IntentTarget(text=d) 
            
        return IntentTarget(
            role=d.get("role"),
            name=d.get("name"),
            label=d.get("label"),
            text=d.get("text"),
            placeholder=d.get("placeholder"),
            testid=d.get("testid"),
            selector=d.get("selector"),
        )

    def brief(self) -> str:
        """로그 출력을 위한 요약 문자열을 반환합니다."""
        return f"role={self.role}, name={self.name}, label={self.label}, text={self.text}"


# =============================================================================
# 4. 요소 탐색기 (Locator Resolver)
# - IntentTarget 정보를 바탕으로 실제 웹 페이지의 요소를 찾아냅니다.
# =============================================================================

class LocatorResolver:
    def __init__(self, page, fast_timeout_ms: int):
        self.page = page
        self.fast_timeout_ms = fast_timeout_ms

    def _try_visible(self, locator) -> bool:
        """
        찾은 요소가 실제로 화면에 보이고 조작 가능한지(Visible) 확인합니다.
        'fast_timeout_ms'(2초) 내에 안 나타나면 즉시 False를 반환하여
        불필요한 대기 시간을 줄입니다.
        """
        try:
            locator.first.wait_for(state="visible", timeout=self.fast_timeout_ms)
            return True
        except Exception:
            return False

    def resolve(self, target: IntentTarget):
        """
        여러 가지 전략을 순차적으로 시도하여 요소를 찾습니다.
        가장 정확도가 높고 유지보수가 쉬운 방법(Role+Name)부터 시도합니다.
        """
        # 전략 1: 접근성 역할(Role)과 이름(Name)으로 찾기 (권장)
        # 예: role="button", name="로그인"
        if target.role and target.name:
            loc = self.page.get_by_role(target.role, name=target.name)
            if self._try_visible(loc): return loc
        
        # 전략 2: 라벨(Label)로 찾기 
        # 예: <label>이메일</label> <input>
        if target.label:
            loc = self.page.get_by_label(target.label)
            if self._try_visible(loc): return loc
        
        # 전략 3: 화면에 보이는 텍스트(Text)로 찾기 
        # 예: <span>회원가입</span> (exact=False로 부분 일치 허용)
        if target.text:
            loc = self.page.get_by_text(target.text, exact=False)
            if self._try_visible(loc): return loc
            
        # 전략 4: 플레이스홀더(Placeholder)로 찾기 
        # 예: <input placeholder="검색어 입력">
        if target.placeholder:
            loc = self.page.get_by_placeholder(target.placeholder)
            if self._try_visible(loc): return loc
            
        # 전략 5: CSS/XPath 선택자(Selector)로 찾기 (최후의 수단)
        # 예: #main-content > div > button
        if target.selector:
            loc = self.page.locator(target.selector)
            if self._try_visible(loc): return loc
            
        # 모든 전략 실패 시 에러 발생 -> Self-Healing으로 넘어감
        raise RuntimeError(f"Target not resolved: {target.brief()}")


# =============================================================================
# 5. 자가 치유 로직 (Self-Healing Logic)
# - 요소 찾기에 실패했을 때 대안을 찾아내는 핵심 알고리즘입니다.
# =============================================================================

def collect_accessibility_candidates(page) -> List[Dict[str, str]]:
    """
    현재 페이지의 모든 접근성 트리(Accessibility Tree)를 스캔하여
    '클릭하거나 입력할 수 있는' 모든 요소들의 목록을 수집합니다.
    """
    try:
        snapshot = page.accessibility.snapshot()
    except:
        return [] # 스냅샷 실패 시 빈 목록 반환
        
    results: List[Dict[str, str]] = []

    def walk(node: Dict[str, Any]) -> None:
        role = node.get("role") or ""
        name = node.get("name") or ""
        # 의미 있는 역할과 이름이 있는 요소만 후보로 등록
        if role and name:
            results.append({"role": role, "name": name})
        for child in node.get("children", []) or []:
            walk(child)

    if snapshot: walk(snapshot)
    
    # 중복 제거 (같은 버튼이 여러 번 잡히는 경우 방지)
    dedup: Dict[str, Dict[str, str]] = {}
    for r in results:
        key = f"{r['role']}|{r['name']}"
        dedup[key] = r
    return list(dedup.values())

def filter_candidates_by_action(action: str, candidates: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """
    수행하려는 동작(클릭/입력)에 맞는 요소만 남깁니다.
    예: 'click' 동작인데 'textbox'를 추천하면 안 되니까요.
    """
    if action == "click":
        allowed = {"button", "link", "menuitem", "tab", "checkbox", "radio"}
        return [c for c in candidates if c.get("role") in allowed]
    return candidates

def rank_candidates(query: str, target_role: Optional[str], candidates: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    """
    원래 찾으려던 요소의 이름(Query)과 가장 이름이 비슷한 후보들을 찾습니다.
    (예: '로그인하기' 버튼을 못 찾았는데, 화면에 '로그인' 버튼이 있다면 추천)
    """
    scored: List[Dict[str, Any]] = []
    for c in candidates:
        # 문자열 유사도 계산
        s = similarity(query, c.get("name", ""))
        # 역할(Role)까지 같다면 가산점(+0.1) 부여
        if target_role and c.get("role") == target_role: s += 0.10
        scored.append({"role": c.get("role", ""), "name": c.get("name", ""), "score": round(s, 3)})
    
    # 점수가 높은 순서대로 정렬
    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored

def build_llm_heal_prompt(action: str, failed_target: Dict[str, Any], error_text: str, page_url: str, ranked_candidates: List[Dict[str, Any]]) -> str:
    """
    LLM에게 '이 상황에서 어떤 요소를 대신 눌러야 할까?'라고 묻기 위한 프롬프트를 만듭니다.
    후보 요소 목록(ranked_candidates)을 함께 제공하여 정확도를 높입니다.
    """
    candidates_json = json.dumps(ranked_candidates[:CANDIDATE_TOP_N], ensure_ascii=False, indent=2)
    return f"""
[Self-Healing Request]
아래 실패 상황을 기준으로, 실행 가능한 대체 target과 fallback_targets를 JSON으로 제안하라.
[Action] {action}
[Failed Target] {json.dumps(failed_target, ensure_ascii=False)}
[Error] {error_text}
[URL] {page_url}
[Candidate Elements] {candidates_json}
[Output Schema]
{{
  "target": {{"role": "...", "name": "..."}},
  "fallback_targets": [ {{"role": "...", "name": "..."}} ]
}}
""".strip()

def build_html_report(rows: List[Dict[str, Any]]) -> str:
    """테스트 실행 결과(성공/실패, 스크린샷 등)를 HTML 테이블 형태로 만들어 반환합니다."""
    html = """<html><head><meta charset="utf-8"/><style>
  body { font-family: Arial, sans-serif; margin: 18px; }
  table { border-collapse: collapse; width: 100%; }
  th, td { border: 1px solid #ddd; padding: 8px; vertical-align: top; }
  th { background: #f2f2f2; }
  .pass { color: #137333; font-weight: bold; }
  .fail { color: #b3261e; font-weight: bold; }
  .mono { font-family: Menlo, Consolas, monospace; font-size: 12px; }
</style></head><body>
<h2>Zero-Touch QA Report</h2>
<div>Artifacts: <a href="test_scenario.json">scenario</a> | <a href="run_log.jsonl">logs</a></div><br/>
<table><tr><th>Step</th><th>Action</th><th>Description</th><th>Healing</th><th>Result</th><th>Evidence</th></tr>"""
    for r in rows:
        status = r.get("status", "")
        cls = "pass" if status == "PASS" else "fail"
        img = r.get("evidence", "")
        img_link = f"<a href='{img}'>open</a>" if img else "-"
        html += f"""<tr>
  <td class="mono">{r.get("step")}</td><td class="mono">{r.get("action")}</td>
  <td>{r.get("description")}</td><td class="mono">{r.get("heal_stage")}</td>
  <td class="{cls}">{status}</td><td>{img_link}</td></tr>"""
    html += "</table></body></html>"
    return html


# =============================================================================
# 6. 메인 에이전트 (ZeroTouchAgent)
# - 전체 테스트 흐름을 제어하는 핵심 클래스입니다.
# =============================================================================

class ZeroTouchAgent:
    def __init__(self, url: str, srs_text: str, out_dir: str, ollama_host: str, model: str):
        self.url = url
        self.srs_text = srs_text
        self.out_dir = out_dir
        self.ollama_host = ollama_host
        self.model = model
        
        # 결과물을 저장할 폴더 생성
        ensure_dir(out_dir)
        self.path_scenario = os.path.join(out_dir, "test_scenario.json")
        self.path_healed = os.path.join(out_dir, "test_scenario.healed.json")
        self.path_log = os.path.join(out_dir, "run_log.jsonl")
        self.path_report = os.path.join(out_dir, "index.html")
        
        # Ollama 클라이언트 연결
        self.client = ollama.Client(host=ollama_host)

    def plan_scenario(self) -> List[Dict[str, Any]]:
        """
        [1단계: 계획]
        LLM을 호출하여 자연어 요구사항(SRS)을 실행 가능한 JSON 시나리오로 변환합니다.
        """
        log(f"Plan: model={self.model}")
        prompt = f"""
QA 엔지니어다. SRS를 Playwright 시나리오(JSON 배열)로 변환하라.
[SRS] {self.srs_text}
[URL] {self.url}
[규칙]
1. action: navigate|click|fill|check|wait
2. target: role+name, label, text 위주
3. fallback_targets 2개 이상 포함
4. 출력은 JSON 배열만
""".strip()
        # 온도(Temperature)를 0.1로 낮춰서 창의성보다는 정확하고 일관된 답변을 유도합니다.
        res = self.client.chat(model=self.model, messages=[{"role": "user", "content": prompt}], options={"temperature": 0.1})
        scenario = json.loads(extract_json_array(res["message"]["content"]))
        
        # 생성된 시나리오 저장
        write_json(self.path_scenario, scenario)
        append_jsonl(self.path_log, {"ts": now_iso(), "phase": "plan", "steps": len(scenario)})
        return scenario

    def _log_step_event(self, payload: Dict[str, Any]) -> None:
        """단계별 실행 로그를 JSONL 파일에 기록합니다."""
        base = {"ts": now_iso()}
        base.update(payload)
        append_jsonl(self.path_log, base)

    def _execute_action(self, page, resolver: LocatorResolver, step: Dict[str, Any]) -> None:
        """
        [액션 수행]
        Playwright를 이용해 실제로 브라우저를 조작합니다.
        """
        action = step.get("action")
        
        # 1. 페이지 이동
        if action == "navigate":
            target_url = step.get("value") or self.url
            # [중요] 네이버/구글 등 포털은 'networkidle'을 쓰면 무한 대기에 빠질 수 있습니다.
            # 대신 'domcontentloaded'(DOM 구성 완료) 상태까지만 기다리도록 완화합니다.
            page.goto(target_url, timeout=60000, wait_until="domcontentloaded")
            # 추가적으로 2초간 강제 대기하여 화면이 안정화되길 기다립니다.
            page.wait_for_timeout(2000)
            return

        # 2. 대기 (Explicit Wait)
        if action == "wait":
            page.wait_for_timeout(int(step.get("value", 1500)))
            return

        # 3. 상호작용 (클릭, 입력, 체크)
        if action in ["click", "fill", "check"]:
            target = IntentTarget.from_dict(step.get("target") or {})
            loc = resolver.resolve(target) # 요소를 찾음
            
            if action == "click": 
                loc.first.click(timeout=DEFAULT_TIMEOUT_MS)
            elif action == "fill": 
                loc.first.fill(str(step.get("value", "")), timeout=DEFAULT_TIMEOUT_MS)
            elif action == "check": 
                loc.first.wait_for(state="visible", timeout=DEFAULT_TIMEOUT_MS)
            
            # [Tip] 클릭 후 화면 전환이나 팝업이 뜰 수 있으므로 0.5초 정도 안정화 대기
            page.wait_for_timeout(500)
            return
            
        raise RuntimeError(f"Unsupported action: {action}")

    def _heal_step(self, page, resolver, step, action, error_text) -> Tuple[bool, str]:
        """
        [자가 치유]
        액션 수행 실패 시(요소를 못 찾음 등), 대안을 찾아 복구를 시도합니다.
        """
        original_target = step.get("target") or {}
        # 문자열 타겟 방어 로직
        if isinstance(original_target, str):
            original_target = {"text": original_target}

        query = (original_target.get("name") or "") or (original_target.get("text") or "")
        
        for attempt in range(1, MAX_HEAL_ATTEMPTS + 1):
            # 전략 1: Fallback Target 시도 (시나리오 생성 시 미리 만들어둔 예비 타겟)
            fallbacks = step.get("fallback_targets") or []
            if attempt <= len(fallbacks):
                step["target"] = fallbacks[attempt - 1]
                try:
                    self._execute_action(page, resolver, step)
                    return True, f"fallback_{attempt}"
                except Exception: pass
            
            # 전략 2: 후보군 검색 (화면상의 요소들과 이름 유사도 비교)
            try:
                candidates = collect_accessibility_candidates(page)
                candidates = filter_candidates_by_action(action, candidates)
                ranked = rank_candidates(query, original_target.get("role"), candidates)
                if ranked:
                    # 가장 유사한 요소로 타겟 교체 후 재시도
                    step["target"] = {"role": ranked[0]["role"], "name": ranked[0]["name"]}
                    self._execute_action(page, resolver, step)
                    return True, "candidate_search"
            except Exception: pass

            # 전략 3: LLM에게 물어보기 (최후의 수단)
            if HEAL_MODE == "on":
                try:
                    candidates = collect_accessibility_candidates(page)
                    candidates = filter_candidates_by_action(action, candidates)
                    ranked = rank_candidates(query, original_target.get("role"), candidates)
                    
                    # 현재 화면 상황과 에러 내용, 후보 요소들을 LLM에게 보내서 판단 요청
                    prompt = build_llm_heal_prompt(action, original_target, error_text, page.url, ranked)
                    res = self.client.chat(model=self.model, messages=[{"role": "user", "content": prompt}], options={"temperature": 0.1})
                    
                    heal_obj = json.loads(extract_json_object(res["message"]["content"]))
                    step["target"] = heal_obj.get("target") or original_target
                    step["fallback_targets"] = heal_obj.get("fallback_targets") or []
                    
                    self._execute_action(page, resolver, step)
                    return True, "llm_heal"
                except Exception: pass

        # 모든 복구 시도 실패 -> 원래 타겟으로 되돌리고 실패 처리
        step["target"] = original_target
        return False, "heal_failed"

    def save_report(self, rows: List[Dict[str, Any]]) -> None:
        """테스트 종료 후 HTML 리포트를 파일로 저장합니다."""
        html = build_html_report(rows)
        with open(self.path_report, "w", encoding="utf-8") as f:
            f.write(html)

    def execute(self, scenario: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        [2단계: 실행]
        생성된 시나리오를 순차적으로 실행합니다.
        브라우저 탭 관리(Context Switching) 로직이 포함되어 있습니다.
        """
        rows, healed_scenario = [], json.loads(json.dumps(scenario))
        
        with sync_playwright() as p:
            # 브라우저 실행 (Headless)
            browser = p.chromium.launch(headless=DEFAULT_HEADLESS, slow_mo=DEFAULT_SLOW_MO_MS)
            
            # [중요] BrowserContext 생성: 탭/창을 독립적으로 관리하기 위함
            context = browser.new_context(viewport={"width": 1280, "height": 800})
            page = context.new_page()
            resolver = LocatorResolver(page, FAST_TIMEOUT_MS)

            # enumerate를 사용하여 Step ID 자동 할당 (1, 2, 3...)
            for idx, step in enumerate(healed_scenario, start=1):
                sid = step.get("step") or idx
                step["step"] = sid
                
                action = step.get("action")
                desc = step.get("description", "")
                heal_stage = "none"
                status = "PASS"
                evidence = ""

                self._log_step_event({"phase": "exec", "step": sid, "action": action, "event": "start"})
                
                # [새 페이지 감지 1] 액션 수행 전 현재 열린 페이지 수 확인
                pages_before = len(context.pages)

                try:
                    self._execute_action(page, resolver, step)
                except Exception as e:
                    # 실행 실패 시 로그 기록 및 자가 치유(Heal) 시도
                    self._log_step_event({"phase": "exec", "step": sid, "action": action, "event": "fail", "error": str(e)})
                    if action in ["click", "fill", "check"]:
                        ok, heal_stage = self._heal_step(page, resolver, step, action, str(e))
                        if not ok: status = "FAIL"
                    else: status = "FAIL"

                # [새 페이지 감지 2] 액션 수행 후 페이지 수가 늘어났다면? (예: 클릭 후 새 탭 열림)
                if len(context.pages) > pages_before:
                    # 가장 최근에 열린 페이지(리스트의 마지막)를 가져옴
                    new_page = context.pages[-1]
                    try: 
                        new_page.wait_for_load_state("domcontentloaded")
                    except: pass
                    
                    # [Context Switching] 제어권을 새 페이지로 넘김
                    page = new_page
                    resolver = LocatorResolver(page, FAST_TIMEOUT_MS) # Resolver도 갱신
                    self._log_step_event({"phase": "exec", "step": sid, "event": "new_page_detected", "url": page.url})

                # 증거 스크린샷 촬영 (성공/실패 여부 포함)
                evidence = f"step_{sid}_{status.lower()}.png"
                try: page.screenshot(path=os.path.join(self.out_dir, evidence))
                except Exception: pass
                
                self._log_step_event({"phase": "exec", "step": sid, "status": status, "heal": heal_stage})
                rows.append({"step": sid, "action": action, "description": desc, "heal_stage": heal_stage, "status": status, "evidence": evidence})
                
                # 하나라도 실패하면 즉시 테스트 중단
                if status == "FAIL": break

            browser.close()
        return rows, healed_scenario

    def run(self) -> None:
        """전체 에이전트 실행 흐름 (계획 -> 실행 -> 리포트 -> 종료코드 처리)"""
        append_jsonl(self.path_log, {"ts": now_iso(), "phase": "run", "status": "start", "headless": DEFAULT_HEADLESS})
        
        # 1. 시나리오 생성
        scenario = self.plan_scenario()
        
        # 2. 실행
        rows, healed = self.execute(scenario)
        
        # 3. 결과 저장
        write_json(self.path_healed, healed)
        self.save_report(rows)
        append_jsonl(self.path_log, {"ts": now_iso(), "phase": "run", "status": "end"})

        # [중요] 실행 결과 중 하나라도 'FAIL'이 있으면
        # Jenkins가 빌드를 '실패(FAILURE)'로 처리할 수 있도록 Exit Code 1을 반환하며 종료합니다.
        if any(r.get("status") == "FAIL" for r in rows):
            log("Test FAILED: Exiting with status code 1.")
            sys.exit(1)

# =============================================================================
# 7. 엔트리 포인트 (Main)
# =============================================================================

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--srs_file", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--ollama_host", default=DEFAULT_OLLAMA_HOST)
    parser.add_argument("--model", default=DEFAULT_MODEL_NAME)
    args = parser.parse_args()

    try:
        with open(args.srs_file, "r", encoding="utf-8") as f: srs_text = f.read()
    except Exception as e:
        log(f"CRITICAL: cannot read srs_file: {args.srs_file} / error={e}")
        sys.exit(1)

    agent = ZeroTouchAgent(args.url, srs_text, args.out, args.ollama_host, args.model)
    agent.run()

if __name__ == "__main__":
    main()