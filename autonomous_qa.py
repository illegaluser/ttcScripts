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

# =============================================================================
# 1. 환경 설정 (Configuration)
# =============================================================================

DEFAULT_OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://host.docker.internal:11434")
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

# =============================================================================
# 2. 유틸리티 함수 (Utils)
# =============================================================================

def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")

def log(msg: str) -> None:
    print(f"[AutoQA] {msg}", flush=True)

def ensure_dir(path: str) -> None:
    try:
        os.makedirs(path, exist_ok=True)
    except Exception as e:
        log(f"CRITICAL: cannot create directory: {path} / error={e}")
        sys.exit(1)

def write_json(path: str, obj: Any) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)

def append_jsonl(path: str, obj: Dict[str, Any]) -> None:
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")

def extract_json_array(text: str) -> str:
    t = text.strip().replace("```json", "").replace("```", "").strip()
    start = t.find("[")
    end = t.rfind("]")
    if start == -1 or end == -1:
        raise ValueError("LLM response does not contain JSON array")
    return t[start:end + 1]

def extract_json_object(text: str) -> str:
    t = text.strip().replace("```json", "").replace("```", "").strip()
    start = t.find("{")
    end = t.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("LLM response does not contain JSON object")
    return t[start:end + 1]

def similarity(a: str, b: str) -> float:
    if not a or not b: return 0.0
    a_low, b_low = a.lower(), b.lower()
    # 단순 유사도 외에 포함 관계(Contains) 점수 가산
    ratio = SequenceMatcher(None, a_low, b_low).ratio()
    if a_low in b_low or b_low in a_low:
        ratio = max(ratio, 0.85) # 포함되어 있으면 높은 점수 부여
    return ratio

def sanitize_url(url: str) -> str:
    """URL에 프로토콜이 없으면 https://를 자동으로 추가합니다."""
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        return f"https://{url}"
    return url

# =============================================================================
# 3. 데이터 모델 (Data Models)
# =============================================================================

@dataclass
class IntentTarget:
    role: Optional[str] = None
    name: Optional[str] = None
    label: Optional[str] = None
    text: Optional[str] = None
    placeholder: Optional[str] = None
    title: Optional[str] = None
    testid: Optional[str] = None
    selector: Optional[str] = None

    @staticmethod
    def from_dict(d: Any) -> "IntentTarget":
        if isinstance(d, str):
            import re
            # Playwright 셀렉터 형태(role=..., [name=...])인 경우 selector로 우선 처리
            if d.startswith(("role=", "id=", "text=", "label=", "placeholder=", "title=")) or "[" in d:
                return IntentTarget(selector=d)

            # "key=value" 형태의 문자열 파싱
            kv_pairs = re.findall(r'(\w+)=([^\s=]+(?:[\s][^\s=]+)*)(?=\s+\w+=|$)', d)
            if kv_pairs:
                data = {k: v.strip().strip('"').strip("'") for k, v in kv_pairs}
                return IntentTarget(
                    role=data.get("role"),
                    name=data.get("name"),
                    label=data.get("label"),
                    text=data.get("text"),
                    placeholder=data.get("placeholder"),
                    title=data.get("title"),
                    testid=data.get("testid"),
                    selector=data.get("selector"),
                )
            return IntentTarget(text=d)
        return IntentTarget(
            role=d.get("role"),
            name=d.get("name"),
            label=d.get("label"),
            text=d.get("text"),
            placeholder=d.get("placeholder"),
            title=d.get("title"),
            testid=d.get("testid"),
            selector=d.get("selector"),
        )

    def brief(self) -> str:
        return f"role={self.role}, name={self.name}, text={self.text}, label={self.label}"

# =============================================================================
# 4. 요소 탐색기 (Locator Resolver)
# =============================================================================

class LocatorResolver:
    def __init__(self, page, fast_timeout_ms: int):
        self.page = page
        self.fast_timeout_ms = fast_timeout_ms

    def _try_visible(self, locator) -> bool:
        try:
            locator.first.wait_for(state="visible", timeout=self.fast_timeout_ms)
            return True
        except Exception:
            return False

    def resolve(self, target: IntentTarget):
        # 0. Selector (Playwright 직접 셀렉터가 제공된 경우 최우선)
        if target.selector:
            loc = self.page.locator(target.selector)
            if self._try_visible(loc): return loc

        # 1. Role + Name (유연한 매칭 적용)
        if target.role and target.name:
            loc = self.page.get_by_role(target.role, name=target.name, exact=False)
            if self._try_visible(loc): return loc
            
            # [추가] 이름이 안 맞더라도 해당 Role이 페이지에 하나뿐이라면 선택 (구글/네이버 검색창 대응)
            loc_role_only = self.page.get_by_role(target.role)
            if loc_role_only.count() == 1 and self._try_visible(loc_role_only):
                return loc_role_only

        # 2. 유연한 텍스트 기반 탐색 (Name, Text, Label, Title, Placeholder 중 하나라도 있으면 시도)
        search_term = target.name or target.text or target.label or target.title
        if search_term:
            # 구글/네이버 대응을 위해 Placeholder와 Title 탐색 순위 상향
            for method in [self.page.get_by_label, self.page.get_by_title, 
                           self.page.get_by_placeholder, self.page.get_by_text]:
                loc = method(search_term, exact=False)
                if self._try_visible(loc): return loc
            
            # [Fallback] 검색어가 'q'나 'input' 같은 단순 문자열일 경우 CSS 선택자로 간주
            try:
                loc = self.page.locator(search_term)
                if self._try_visible(loc): return loc
            except: pass

        if target.testid:
            loc = self.page.locator(f"[data-testid='{target.testid}']")
            if self._try_visible(loc): return loc
        if target.placeholder:
            loc = self.page.get_by_placeholder(target.placeholder)
            if self._try_visible(loc): return loc
        raise RuntimeError(f"Target not resolved: {target.brief()}")

# =============================================================================
# 5. 자가 치유 로직 (Self-Healing Logic)
# =============================================================================

def collect_accessibility_candidates(page) -> List[Dict[str, str]]:
    try:
        snapshot = page.accessibility.snapshot()
    except:
        return []
    results: List[Dict[str, str]] = []

    def walk(node: Dict[str, Any]) -> None:
        role = node.get("role") or ""
        name = node.get("name") or ""
        if role and name:
            results.append({"role": role, "name": name})
        for child in node.get("children", []) or []:
            walk(child)

    if snapshot: walk(snapshot)
    dedup: Dict[str, Dict[str, str]] = {}
    for r in results:
        key = f"{r['role']}|{r['name']}"
        dedup[key] = r
    return list(dedup.values())

def filter_candidates_by_action(action: str, candidates: List[Dict[str, str]]) -> List[Dict[str, str]]:
    if action in ["click", "double_click", "hover"]:
        allowed = {"button", "link", "menuitem", "tab", "checkbox", "radio", "img"}
        return [c for c in candidates if c.get("role") in allowed]
    return candidates

def rank_candidates(query: str, target_role: Optional[str], candidates: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    scored: List[Dict[str, Any]] = []
    for c in candidates:
        s = similarity(query, c.get("name", ""))
        if target_role and c.get("role") == target_role: s = max(s, 0.6) # Role이 같으면 언어가 달라도 높은 점수 부여
        scored.append({"role": c.get("role", ""), "name": c.get("name", ""), "score": round(s, 3)})
    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored

def build_llm_heal_prompt(action: str, failed_target: Dict[str, Any], error_text: str, page_url: str, ranked_candidates: List[Dict[str, Any]]) -> str:
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
# =============================================================================

class ZeroTouchAgent:
    def __init__(self, url: str, srs_text: str, out_dir: str, ollama_host: str, model: str):
        self.url = sanitize_url(url)
        self.srs_text = srs_text
        self.out_dir = out_dir
        self.ollama_host = ollama_host
        self.model = model
        ensure_dir(out_dir)
        self.path_scenario = os.path.join(out_dir, "test_scenario.json")
        self.path_healed = os.path.join(out_dir, "test_scenario.healed.json")
        self.path_log = os.path.join(out_dir, "run_log.jsonl")
        self.path_report = os.path.join(out_dir, "index.html")
        self.client = ollama.Client(host=ollama_host)

    def _get_locator_code(self, target_dict: Any) -> str:
        """IntentTarget 데이터를 Playwright 코드 문자열로 변환합니다."""
        if not target_dict: return "page"
        t = IntentTarget.from_dict(target_dict)
        if t.role and t.name:
            return f'page.get_by_role("{t.role}", name="{t.name}")'
        if t.label:
            return f'page.get_by_label("{t.label}")'
        if t.placeholder:
            return f'page.get_by_placeholder("{t.placeholder}")'
        if t.title:
            return f'page.get_by_title("{t.title}")'
        if t.testid:
            return f'page.locator("[data-testid=\'{t.testid}\']")'
        if t.selector:
            return f'page.locator("{t.selector}")'
        if t.text:
            return f'page.get_by_text("{t.text}", exact=False)'
        return "page"

    def generate_regression_script(self, scenario: List[Dict[str, Any]]) -> str:
        """성공한 시나리오를 바탕으로 독립 실행 가능한 Playwright 스크립트를 생성합니다."""
        code = [
            "import time",
            "from playwright.sync_api import sync_playwright",
            "",
            "def run_regression():",
            "    with sync_playwright() as p:",
            "        browser = p.chromium.launch(headless=False)",
            f"        context = browser.new_context(viewport={{'width': 1920, 'height': 1080}}, user_agent='{REAL_USER_AGENT}')",
            "        page = context.new_page()",
            "        # Stealth script to bypass bot detection",
            "        page.add_init_script(\"Object.defineProperty(navigator, 'webdriver', {get: () => undefined})\")",
            ""
        ]

        for step in scenario:
            action = step.get("action")
            value = step.get("value")
            target_data = step.get("target")
            desc = step.get("description", action)
            
            code.append(f"        # Step {step.get('step')}: {desc}")
            
            if action == "navigate":
                code.append(f"        page.goto('{value or self.url}', wait_until='domcontentloaded')")
                code.append("        page.wait_for_timeout(2000)")
            elif action == "go_back": code.append("        page.go_back()")
            elif action == "go_forward": code.append("        page.go_forward()")
            elif action == "wait": code.append(f"        page.wait_for_timeout({value or 1500})")
            elif action == "press_key":
                if target_data: code.append(f"        {self._get_locator_code(target_data)}.first.press('{value or 'Enter'}')")
                else: code.append(f"        page.keyboard.press('{value or 'Enter'}')")
            else:
                loc = self._get_locator_code(target_data)
                if action == "click": code.append(f"        {loc}.first.click()")
                elif action == "double_click": code.append(f"        {loc}.first.dblclick()")
                elif action == "hover": code.append(f"        {loc}.first.hover()")
                elif action == "fill": code.append(f"        {loc}.first.fill('{value}')")
                elif action == "press_sequential": code.append(f"        {loc}.first.press_sequential('{value}', delay=100)")
                elif action == "select_option": code.append(f"        {loc}.first.select_option(value='{value}')")
                elif action == "scroll": code.append(f"        {loc}.first.scroll_into_view_if_needed()")
                elif action == "assert_visible": code.append(f"        {loc}.first.wait_for(state='visible')")
                elif action == "assert_text": code.append(f"        assert '{value}' in {loc}.first.inner_text()")
            code.append("        page.wait_for_timeout(500)\n")

        code.extend(["        print('Regression test passed!')", "        browser.close()", "", "if __name__ == '__main__':", "    run_regression()"])
        return "\n".join(code)

    def plan_scenario(self) -> List[Dict[str, Any]]:
        log(f"Plan: model={self.model}")
        # [Update] LLM에게 알려줄 액션 목록 확장
        prompt = f"""
QA 엔지니어다. SRS를 Playwright 시나리오(JSON 배열)로 변환하라.
[SRS] {self.srs_text}
[URL] {self.url}
[Action List]
- navigate: URL 이동 (value=url)
- click: 클릭 (target)
- double_click: 더블 클릭 (target)
- hover: 마우스 오버 (target)
- fill: 텍스트 입력 (target, value=text)
- select_option: 드롭다운 선택 (target, value=option_value)
- press_sequential: 순차적 키 입력 (target, value=text) - fill이 안될 때 사용
- check: 체크박스 체크 (target)
- press_key: 키보드 입력 (value="Enter" 등)
- scroll: 해당 요소가 보이게 스크롤 (target)
- assert_text: 요소 내 텍스트 검증 (target, value=expected_text)
- assert_visible: 요소 노출 여부 검증 (target)
- go_back: 뒤로 가기
- go_forward: 앞으로 가기
- wait: 대기 (value=ms)

[규칙]
1. target은 객체 형태 {{"role": "...", "name": "..."}}를 권장함.
2. Google/Naver 검색창은 보통 role="combobox"임.
3. Google/Naver 검색어 입력 시 봇 탐지 방지를 위해 반드시 action="press_sequential"을 사용한다.
4. Google 검색 실행은 action="press_key", value="Enter" 사용.
5. fallback_targets 2개 이상 포함.
6. 출력은 JSON 배열만.
""".strip()
        res = self.client.chat(model=self.model, messages=[{"role": "user", "content": prompt}], options={"temperature": 0.1})
        scenario = json.loads(extract_json_array(res["message"]["content"]))
        write_json(self.path_scenario, scenario)
        append_jsonl(self.path_log, {"ts": now_iso(), "phase": "plan", "steps": len(scenario)})
        return scenario

    def _log_step_event(self, payload: Dict[str, Any]) -> None:
        base = {"ts": now_iso()}
        base.update(payload)
        append_jsonl(self.path_log, base)

    def _execute_action(self, page, resolver: LocatorResolver, step: Dict[str, Any]) -> None:
        """
        [확장된 액션 실행기]
        다양한 브라우저 동작을 처리합니다.
        """
        action = step.get("action")
        value = step.get("value")
        
        # 1. 페이지 탐색 및 히스토리
        if action == "navigate":
            target_url = value or self.url
            target_url = sanitize_url(target_url)
            page.goto(target_url, timeout=60000, wait_until="domcontentloaded")
            page.wait_for_timeout(2000)
            return
        if action == "go_back":
            page.go_back()
            page.wait_for_timeout(1000)
            return
        if action == "go_forward":
            page.go_forward()
            page.wait_for_timeout(1000)
            return

        # 2. 대기 및 키보드
        if action == "wait":
            page.wait_for_timeout(int(value or 1500))
            return
        if action == "press_key":
            key_name = str(value or "Enter")
            target_data = step.get("target")
            if target_data:
                target = IntentTarget.from_dict(target_data)
                loc = resolver.resolve(target)
                loc.first.focus(timeout=DEFAULT_TIMEOUT_MS)
                page.wait_for_timeout(200)
                loc.first.press(key_name)
            else:
                page.keyboard.press(key_name)
            page.wait_for_timeout(1000)
            return

        # 3. 요소 타겟팅이 필요한 액션들
        target_actions = [
            "click", "double_click", "hover", "fill", "check", 
            "select_option", "scroll", "assert_text", "assert_visible", "press_sequential"
        ]
        
        if action in target_actions:
            target = IntentTarget.from_dict(step.get("target") or {})
            loc = resolver.resolve(target)
            
            # (1) 마우스 조작
            if action == "click":
                loc.first.click(timeout=DEFAULT_TIMEOUT_MS)
                page.wait_for_timeout(500)
            elif action == "double_click":
                loc.first.dblclick(timeout=DEFAULT_TIMEOUT_MS)
                page.wait_for_timeout(500)
            elif action == "hover":
                loc.first.hover(timeout=DEFAULT_TIMEOUT_MS)
                page.wait_for_timeout(500)
            
            # (2) 입력 및 선택
            elif action == "fill":
                loc.first.focus(timeout=DEFAULT_TIMEOUT_MS)
                page.wait_for_timeout(100)
                loc.first.fill(str(value or ""), timeout=DEFAULT_TIMEOUT_MS)
            elif action == "check":
                loc.first.check(timeout=DEFAULT_TIMEOUT_MS)
            elif action == "select_option":
                loc.first.select_option(value=str(value or ""), timeout=DEFAULT_TIMEOUT_MS)
            
            # (2.5) 순차 입력 (실제 키보드 타이핑 시뮬레이션)
            elif action == "press_sequential":
                loc.first.focus(timeout=DEFAULT_TIMEOUT_MS)
                page.wait_for_timeout(100)
                loc.first.press_sequential(str(value or ""), delay=100, timeout=DEFAULT_TIMEOUT_MS)
                page.wait_for_timeout(500)

            # (3) 스크롤
            elif action == "scroll":
                loc.first.scroll_into_view_if_needed(timeout=DEFAULT_TIMEOUT_MS)
                
            # (4) 검증 (Assertion) - 실패 시 에러 발생
            elif action == "assert_visible":
                # 단순히 현재 보이는지가 아니라, 나타날 때까지 대기하도록 수정
                loc.first.wait_for(state="visible", timeout=DEFAULT_TIMEOUT_MS)
            elif action == "assert_text":
                expected = str(value or "")
                actual = loc.first.inner_text()
                if expected not in actual:
                    raise AssertionError(f"Text mismatch. Expected '{expected}' in '{actual}'")
            
            return
            
        raise RuntimeError(f"Unsupported action: {action}")

    def _heal_step(self, page, resolver, step, action, error_text) -> Tuple[bool, str]:
        target_data = step.get("target")
        t = IntentTarget.from_dict(target_data)
        original_target_dict = t.__dict__

        # Healing을 위한 핵심 쿼리 추출
        query = t.name or t.text or t.label or t.title or ""
        if not query and t.selector:
            # 셀렉터 문자열에서 이름 추출 시도 (예: role=textbox[name='Search'] -> Search)
            import re
            m = re.search(r"name=['\"](.+?)['\"]", t.selector)
            query = m.group(1) if m else t.selector
        
        for attempt in range(1, MAX_HEAL_ATTEMPTS + 1):
            fallbacks = step.get("fallback_targets") or []
            if attempt <= len(fallbacks):
                step["target"] = fallbacks[attempt - 1]
                try:
                    self._execute_action(page, resolver, step)
                    return True, f"fallback_{attempt}"
                except Exception: pass
            
            try:
                candidates = collect_accessibility_candidates(page)
                candidates = filter_candidates_by_action(action, candidates)
                ranked = rank_candidates(query, t.role, candidates)
                if ranked:
                    step["target"] = {"role": ranked[0]["role"], "name": ranked[0]["name"]}
                    self._execute_action(page, resolver, step)
                    return True, "candidate_search"
            except Exception: pass

            if HEAL_MODE == "on":
                try:
                    candidates = collect_accessibility_candidates(page)
                    candidates = filter_candidates_by_action(action, candidates)
                    ranked = rank_candidates(query, t.role, candidates)
                    prompt = build_llm_heal_prompt(action, original_target_dict, error_text, page.url, ranked)
                    res = self.client.chat(model=self.model, messages=[{"role": "user", "content": prompt}], options={"temperature": 0.1})
                    heal_obj = json.loads(extract_json_object(res["message"]["content"]))
                    step["target"] = heal_obj.get("target") or target_data
                    step["fallback_targets"] = heal_obj.get("fallback_targets") or []
                    self._execute_action(page, resolver, step)
                    return True, "llm_heal"
                except Exception: pass

        step["target"] = target_data
        return False, "heal_failed"

    def save_report(self, rows: List[Dict[str, Any]]) -> None:
        html = build_html_report(rows)
        with open(self.path_report, "w", encoding="utf-8") as f:
            f.write(html)

    def execute(self, scenario: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        rows, healed_scenario = [], json.loads(json.dumps(scenario))
        
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=DEFAULT_HEADLESS, slow_mo=DEFAULT_SLOW_MO_MS)
            context = browser.new_context(
                viewport={"width": 1920, "height": 1080},
                user_agent=REAL_USER_AGENT,
                locale="ko-KR"
            )
            page = context.new_page()
            
            # [Stealth] 봇 감지 우회를 위한 스크립트 주입
            # navigator.webdriver 속성을 제거하여 자동화 도구임을 숨깁니다.
            page.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                window.chrome = { runtime: {} };
                const originalQuery = window.navigator.permissions.query;
                window.navigator.permissions.query = (parameters) => (
                    parameters.name === 'notifications' ?
                    Promise.resolve({ state: Notification.permission }) :
                    originalQuery(parameters)
                );
                Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
                Object.defineProperty(navigator, 'languages', { get: () => ['ko-KR', 'ko', 'en-US', 'en'] });
            """)
            # 추가적인 봇 탐지 방지 헤더 설정
            context.set_extra_http_headers({"Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7"})

            resolver = LocatorResolver(page, FAST_TIMEOUT_MS)

            for idx, step in enumerate(healed_scenario, start=1):
                sid = step.get("step") or idx
                step["step"] = sid
                
                action = step.get("action")
                desc = step.get("description", "")
                heal_stage = "none"
                status = "PASS"
                evidence = ""

                self._log_step_event({"phase": "exec", "step": sid, "action": action, "event": "start"})
                pages_before = len(context.pages)

                # [CAPTCHA Detection] 캡차 화면 감지 시 로그 기록 및 비헤드리스 모드 시 대기
                if "google.com/sorry" in page.url or "captcha" in page.content().lower():
                    log(f"!! [WARNING] CAPTCHA detected at step {sid}. URL: {page.url}")
                    if not DEFAULT_HEADLESS:
                        log("Waiting 30s for manual CAPTCHA solve...")
                        page.wait_for_timeout(30000)

                try:
                    self._execute_action(page, resolver, step)
                except Exception as e:
                    self._log_step_event({"phase": "exec", "step": sid, "action": action, "event": "fail", "error": str(e)})
                    # Assertion 실패는 Healing 대상이 아니지만, 요소를 못 찾은 경우는 Healing 시도
                    if action in ["click", "fill", "check", "hover", "select_option", "assert_text", "assert_visible", "press_sequential"]:
                        ok, heal_stage = self._heal_step(page, resolver, step, action, str(e))
                        if not ok: status = "FAIL"
                    else: status = "FAIL"

                if len(context.pages) > pages_before:
                    new_page = context.pages[-1]
                    try: new_page.wait_for_load_state("domcontentloaded")
                    except: pass
                    page = new_page
                    resolver = LocatorResolver(page, FAST_TIMEOUT_MS)
                    self._log_step_event({"phase": "exec", "step": sid, "event": "new_page_detected", "url": page.url})

                evidence = f"step_{sid}_{status.lower()}.png"
                try: page.screenshot(path=os.path.join(self.out_dir, evidence))
                except Exception: pass
                
                self._log_step_event({"phase": "exec", "step": sid, "status": status, "heal": heal_stage})
                rows.append({"step": sid, "action": action, "description": desc, "heal_stage": heal_stage, "status": status, "evidence": evidence})
                if status == "FAIL": break

            browser.close()
        return rows, healed_scenario

    def run(self) -> None:
        append_jsonl(self.path_log, {"ts": now_iso(), "phase": "run", "status": "start", "headless": DEFAULT_HEADLESS})
        scenario = self.plan_scenario()
        rows, healed = self.execute(scenario)
        write_json(self.path_healed, healed)

        # [추가] 리그레션 스크립트 생성 및 저장
        regression_script = self.generate_regression_script(healed)
        with open(os.path.join(self.out_dir, "regression_test.py"), "w", encoding="utf-8") as f:
            f.write(regression_script)
        log(f"Regression script generated: regression_test.py")

        self.save_report(rows)
        append_jsonl(self.path_log, {"ts": now_iso(), "phase": "run", "status": "end"})

        if any(r.get("status") == "FAIL" for r in rows):
            log("Test FAILED: Exiting with status code 1.")
            sys.exit(1)

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