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
import random
from dataclasses import dataclass, asdict
from datetime import datetime
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional, Tuple

import ollama
from playwright.sync_api import sync_playwright

# =============================================================================
# 1. 환경 설정 (Configuration)
# =============================================================================
# Ollama 서버 주소 (컨테이너 내부에서 호스트의 Ollama에 접근하기 위한 주소)
DEFAULT_OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://host.docker.internal:11434")
# 시나리오 생성 및 치유에 사용할 LLM 모델명
DEFAULT_MODEL_NAME = os.getenv("MODEL_NAME", "qwen3-coder:30b")
# 브라우저 실행 모드 (서버 환경인 Jenkins에서는 기본적으로 True)
_headless_env = os.getenv("HEADLESS", "true").lower()
DEFAULT_HEADLESS = _headless_env in ("true", "1", "on", "yes")
# 액션 간 지연 시간 (눈으로 확인하거나 페이지 안정화를 위해 사용)
DEFAULT_SLOW_MO_MS = int(os.getenv("SLOW_MO_MS", "500"))
# 요소 탐색 및 액션 수행 시의 최대 대기 시간
DEFAULT_TIMEOUT_MS = int(os.getenv("DEFAULT_TIMEOUT_MS", "30000"))
# Resolver에서 여러 전략을 빠르게 시도할 때 사용하는 짧은 타임아웃
FAST_TIMEOUT_MS = int(os.getenv("FAST_TIMEOUT_MS", "2000"))
HEAL_MODE = os.getenv("HEAL_MODE", "on")
MAX_HEAL_ATTEMPTS = int(os.getenv("MAX_HEAL_ATTEMPTS", "2"))
# 자가 치유 시 LLM에게 전달할 주변 요소 후보의 개수
CANDIDATE_TOP_N = int(os.getenv("CANDIDATE_TOP_N", "8"))
# 봇 탐지 우회를 위한 실제 브라우저와 유사한 User-Agent
REAL_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"

# =============================================================================
# 2. 유틸리티 함수 (Utils)
# =============================================================================

def now_iso() -> str:
    """현재 시간을 ISO 8601 형식으로 반환 (로그 기록용)"""
    return datetime.now().isoformat(timespec="seconds")

def log(msg: str) -> None:
    """Jenkins 콘솔 출력에 즉시 나타나도록 flush 옵션을 사용하여 로그 출력"""
    print(f"[AutoQA] {msg}", flush=True)

def ensure_dir(path: str) -> None:
    """결과 리포트 및 스크린샷을 저장할 디렉터리가 없으면 생성"""
    try:
        os.makedirs(path, exist_ok=True)
    except Exception as e:
        log(f"CRITICAL: cannot create directory: {path} / error={e}")
        sys.exit(1)

def write_json(path: str, obj: Any) -> None:
    """객체를 JSON 파일로 저장 (시나리오 보관용)"""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)

def append_jsonl(path: str, obj: Dict[str, Any]) -> None:
    """구조화된 로그를 위해 JSONL(JSON Lines) 형식으로 파일 끝에 추가"""
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")

def extract_json_array(text: str) -> str:
    """LLM의 자유로운 텍스트 응답 속에서 [ ... ] 형태의 JSON 배열만 추출"""
    t = text.strip().replace("```json", "").replace("```", "").strip()
    start = t.find("[")
    end = t.rfind("]")
    if start == -1 or end == -1:
        raise ValueError("LLM response does not contain JSON array")
    return t[start:end + 1]

def extract_json_object(text: str) -> str:
    """LLM 응답 속에서 { ... } 형태의 단일 JSON 객체만 추출 (자가 치유 시 사용)"""
    t = text.strip().replace("```json", "").replace("```", "").strip()
    start = t.find("{")
    end = t.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("LLM response does not contain JSON object")
    return t[start:end + 1]

def similarity(a: str, b: str) -> float:
    """두 문자열 사이의 유사도를 0~1 사이로 계산. 포함 관계일 경우 가산점 부여"""
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
    """
    UI 요소를 '의도(Intent)' 기반으로 찾기 위한 데이터 모델입니다.
    고정된 ID 대신 역할(Role), 이름(Name), 텍스트 등을 복합적으로 활용하여 UI 변경에 강한 탐색을 지원합니다.
    """
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
        """딕셔너리 또는 'role=button name=로그인' 형태의 문자열로부터 객체 생성"""
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
        """로그 출력용 요약 문자열"""
        return f"role={self.role}, name={self.name}, text={self.text}, label={self.label}"

# =============================================================================
# 4. 요소 탐색기 (Locator Resolver)
# =============================================================================

class LocatorResolver:
    """
    IntentTarget 정보를 바탕으로 실제 브라우저 내의 Playwright Locator를 찾아주는 핵심 클래스입니다.
    여러 탐색 전략을 순차적으로 시도하며, 실패 시 빠르게 다음 전략으로 넘어가는 방식을 사용합니다.
    """
    def __init__(self, page, fast_timeout_ms: int):
        self.page = page
        self.fast_timeout_ms = fast_timeout_ms

    def _try_visible(self, locator) -> bool:
        """요소가 실제로 화면에 보이는지 짧은 시간 동안 확인"""
        try:
            locator.first.wait_for(state="visible", timeout=self.fast_timeout_ms)
            return True
        except Exception:
            return False

    def resolve(self, target: IntentTarget):
        """
        우선순위에 따라 요소를 탐색합니다: 0.직접 셀렉터 -> 1.역할+이름 -> 2.텍스트/라벨 -> 3.플레이스홀더
        """
        # 0. Selector (Playwright 직접 셀렉터가 제공된 경우 최우선)
        if target.selector:
            loc = self.page.locator(target.selector).filter(visible=True)
            if self._try_visible(loc): return loc

        # 1. Role + Name (접근성 기반 탐색)
        # exact=False를 사용하여 공백이나 부분 일치에도 대응할 수 있도록 함 (예: '로그인'으로 '로그인 버튼' 찾기)
        if target.role and target.name:
            loc = self.page.get_by_role(target.role, name=target.name, exact=False).filter(visible=True)
            # exact=False를 사용하여 공백이나 부분 일치에 대응합니다.
            loc = self.page.get_by_role(target.role, name=target.name, exact=False)
            if self._try_visible(loc): return loc
            
            # [Fallback] 이름이 안 맞더라도 해당 Role(예: combobox)이 페이지에 하나뿐이라면 그것을 선택
            loc_role_only = self.page.get_by_role(target.role).filter(visible=True)
            if loc_role_only.count() == 1 and self._try_visible(loc_role_only):
                return loc_role_only

        # 1.5 검색창 특화 휴리스틱 (구글/네이버/빙 대응)
        if target.role in ["combobox", "textbox", "searchbox"]:
            for selector in ["input[name='q']", "textarea[name='q']", "input[name='query']", "#query"]:
                loc = self.page.locator(selector).filter(visible=True)
                if self._try_visible(loc): return loc

        # 2. 유연한 텍스트 기반 탐색 (Name, Text, Label, Title, Placeholder 중 하나라도 있으면 시도)
        search_term = target.name or target.text or target.label or target.title
        if search_term:
            # 구글/네이버 대응을 위해 Placeholder와 Title 탐색 순위 상향
            for method in [self.page.get_by_label, self.page.get_by_title, 
                           self.page.get_by_placeholder, self.page.get_by_text]:
                loc = method(search_term, exact=False).filter(visible=True)
                loc = method(search_term, exact=False)
                if self._try_visible(loc): return loc
            
            # [Fallback] 검색어가 'q'나 'input' 같은 단순 문자열일 경우 CSS 선택자로 간주
            try:
                loc = self.page.locator(search_term).filter(visible=True)
                loc = self.page.locator(search_term)
                if self._try_visible(loc): return loc
            except: pass

        if target.testid:
            loc = self.page.locator(f"[data-testid='{target.testid}']").filter(visible=True)
            loc = self.page.locator(f"[data-testid='{target.testid}']")
            if self._try_visible(loc): return loc
        if target.placeholder:
            loc = self.page.get_by_placeholder(target.placeholder).filter(visible=True)
            loc = self.page.get_by_placeholder(target.placeholder)
            if self._try_visible(loc): return loc
        raise RuntimeError(f"Target not resolved: {target.brief()}")

# =============================================================================
# 5. 자가 치유 로직 (Self-Healing Logic)
# =============================================================================

def collect_accessibility_candidates(page) -> List[Dict[str, str]]:
    """
    현재 페이지의 접근성 트리(Accessibility Tree)를 스캔하여 모든 제어 가능한 요소 수집.
    자가 치유 시 '주변에 어떤 요소들이 있는지' LLM에게 알려주기 위한 용도.
    """
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
    """수행하려는 액션에 적합한 역할(Role)을 가진 요소들만 필터링 (예: 클릭 시 버튼/링크만)"""
    if action in ["click", "double_click", "hover"]:
        allowed = {"button", "link", "menuitem", "tab", "checkbox", "radio", "img"}
        return [c for c in candidates if c.get("role") in allowed]
    if action in ["fill"]:
        allowed = {"textbox", "searchbox", "combobox", "spinbutton", "textarea"}
        return [c for c in candidates if c.get("role") in allowed]
    return candidates

def rank_candidates(query: str, target_role: Optional[str], candidates: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    """수집된 후보 요소들을 검색어와의 유사도 및 역할 일치 여부에 따라 점수화하여 정렬"""
    scored: List[Dict[str, Any]] = []
    for c in candidates:
        s = similarity(query, c.get("name", ""))
        if target_role and c.get("role") == target_role: s = max(s, 0.6) # Role이 같으면 언어가 달라도 높은 점수 부여
        scored.append({"role": c.get("role", ""), "name": c.get("name", ""), "score": round(s, 3)})
    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored

def build_llm_heal_prompt(action: str, failed_target: Dict[str, Any], error_text: str, page_url: str, ranked_candidates: List[Dict[str, Any]]) -> str:
    """LLM에게 현재 실패 상황과 주변 요소 목록을 전달하여 새로운 셀렉터를 제안받기 위한 프롬프트 생성"""
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
    """테스트 결과를 한눈에 볼 수 있는 단일 HTML 리포트 생성"""
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
    """
    자연어 요구사항(SRS)으로부터 시나리오를 계획(Plan)하고,
    실행(Execute)하며, 실패 시 치유(Heal)하는 전체 과정을 관리하는 메인 에이전트.
    """
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
        # 리그레션 스크립트에서는 특정된 셀렉터를 최우선으로 사용 (의도 기반 탐색 배제)
        if t.selector:
            return f'page.locator("{t.selector}")'
        if t.role and t.name:
            return f'page.get_by_role("{t.role}", name="{t.name}", exact=False)'
        if t.label:
            return f'page.get_by_label("{t.label}")'
        if t.placeholder:
            return f'page.get_by_placeholder("{t.placeholder}")'
        if t.title:
            return f'page.get_by_title("{t.title}")'
        if t.testid:
            return f'page.locator("[data-testid=\'{t.testid}\']")'
        if t.text:
            return f'page.get_by_text("{t.text}", exact=False)'
        return "page"

    def generate_regression_script(self, scenario: List[Dict[str, Any]]) -> str:
        """성공한 시나리오(박제된 셀렉터 포함)를 바탕으로 AI 없이 실행 가능한 독립 파이썬 스크립트 생성"""
        """성공한 시나리오를 바탕으로 독립 실행 가능한 Playwright 스크립트를 생성합니다."""
        code = [
            "import time",
            "import random",
            "from playwright.sync_api import sync_playwright",
            "",
            "def run_regression():",
            "    with sync_playwright() as p:",
            "        # Extreme Stealth: Disable internal automation flags that reCAPTCHA detects",
            "        browser = p.chromium.launch(",
            "            headless=False, ",
            "            args=[",
            "                '--disable-blink-features=AutomationControlled',",
            "                '--no-sandbox',",
            "                '--disable-infobars',",
            "                '--window-position=0,0',",
            "                '--ignore-certificate-errors',",
            "                '--ignore-certificate-errors-spki-list',",
            "                '--disable-extensions'",
            "            ]",
            "        )",
            f"        context = browser.new_context(viewport={{'width': 1920, 'height': 1080}}, user_agent='{REAL_USER_AGENT}', locale='ko-KR')",
            "        page = context.new_page()",
            "        # Ultimate Stealth suite to bypass reCAPTCHA and bot detection",
            "        page.add_init_script(\"\"\"",
            "            // 1. Hide automation signs",
            "            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});",
            "            if (Object.getPrototypeOf(navigator).hasOwnProperty('webdriver')) {",
            "                delete Object.getPrototypeOf(navigator).webdriver;",
            "            }",
            "            ",
            "            // 2. Mock Chrome runtime with full properties",
            "            window.chrome = {",
            "                runtime: {},",
            "                app: { isInstalled: false, InstallState: { DISABLED: 'disabled', INSTALLED: 'installed', NOT_INSTALLED: 'not_installed' }, getDetails: () => {}, getIsInstalled: () => false },",
            "                csi: () => {},",
            "                loadTimes: () => {}",
            "            };",
            "            ",
            "            // 3. Fix Permissions query",
            "            window.navigator.permissions.query = (parameters) => (",
            "                parameters.name === 'notifications' ?",
            "                Promise.resolve({ state: Notification.permission }) :",
            "                originalQuery(parameters)",
            "            );",
            "            ",
            "            // 4. Mock Plugins and MimeTypes",
            "            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5], configurable: true });",
            "            Object.defineProperty(navigator, 'mimeTypes', { get: () => [1, 2, 3, 4, 5], configurable: true });",
            "            Object.defineProperty(navigator, 'languages', { get: () => ['ko-KR', 'ko', 'en-US', 'en'] });",
            "            Object.defineProperty(navigator, 'vendor', { get: () => 'Google Inc.' });",
            "            Object.defineProperty(navigator, 'maxTouchPoints', { get: () => 0 });",
            "            ",
            "            // 5. Mask WebGL Vendor/Renderer",
            "            const getParameter = WebGLRenderingContext.prototype.getParameter;",
            "            WebGLRenderingContext.prototype.getParameter = function(parameter) {",
            "                if (parameter === 37445) return 'Intel Inc.';",
            "                if (parameter === 37446) return 'Intel(R) Iris(TM) Plus Graphics 640';",
            "                return getParameter.apply(this, arguments);",
            "            };",
            "            ",
            "            // 6. Fix Headless window properties",
            "            Object.defineProperty(window, 'outerWidth', { get: () => window.innerWidth });",
            "            Object.defineProperty(window, 'outerHeight', { get: () => window.innerHeight });",
            "            Object.defineProperty(window, 'devicePixelRatio', { get: () => 1 });",
            "            ",
            "            // 7. Add Canvas Fingerprinting protection",
            "            const originalGetImageData = CanvasRenderingContext2D.prototype.getImageData;",
            "            CanvasRenderingContext2D.prototype.getImageData = function (x, y, w, h) {",
            "                const imageData = originalGetImageData.apply(this, arguments);",
            "                imageData.data[0] = imageData.data[0] + (Math.random() > 0.5 ? 1 : -1);",
            "                return imageData;",
            "            };",
            "            ",
            "            // 8. Add fake hardware info",
            "            Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8 });",
            "            Object.defineProperty(navigator, 'deviceMemory', { get: () => 8 });",
            "        \"\"\")",
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
                code.append("        page.wait_for_timeout(random.randint(3000, 6000))")
                code.append("        # CAPTCHA Detection")
                code.append("        if 'google.com/sorry' in page.url or 'captcha' in page.content().lower():")
                code.append("            print('!! CAPTCHA detected. Please solve it manually.')")
                code.append("            page.wait_for_timeout(60000)")
            elif action == "go_back": code.append("        page.go_back()")
            elif action == "go_forward": code.append("        page.go_forward()")
            elif action == "wait": code.append(f"        page.wait_for_timeout({value or 1500})")
            elif action == "press_key":
                code.append("        # Random delay before key press")
                code.append("        page.wait_for_timeout(random.randint(300, 700))")
                if target_data:
                    code.append("        page.wait_for_timeout(300)")
                    loc_code = self._get_locator_code(target_data)
                    code.append(f"        {loc_code}.first.press('{value or 'Enter'}')")
                else: code.append(f"        page.keyboard.press('{value or 'Enter'}')")
            else:
                loc = self._get_locator_code(target_data)
                if action == "click": code.append(f"        {loc}.first.click()")
                elif action == "double_click": code.append(f"        {loc}.first.dblclick()")
                elif action == "hover": code.append(f"        {loc}.first.hover()")
                elif action == "fill":
                    code.append(f"        {loc}.first.click()")
                    code.append(f"        {loc}.first.fill('')")
                    code.append(f"        for char in '{value}':")
                    code.append(f"            {loc}.first.press(char)")
                    code.append(f"            page.wait_for_timeout(random.randint(200, 800))")
                elif action == "select_option": code.append(f"        {loc}.first.select_option(value='{value}')")
                elif action == "scroll": code.append(f"        {loc}.first.scroll_into_view_if_needed()")
                elif action == "assert_visible": code.append(f"        {loc}.first.wait_for(state='visible')")
                elif action == "assert_text": code.append(f"        assert '{value}' in {loc}.first.inner_text()")
            code.append("        page.wait_for_timeout(500)\n")

        code.extend([
            "        print('Regression test passed!')",
            "        page.wait_for_timeout(10000)",
            "        browser.close()",
            "",
            "if __name__ == '__main__':",
            "    run_regression()"
        ])
        return "\n".join(code)

    def plan_scenario(self) -> List[Dict[str, Any]]:
        """LLM을 호출하여 자연어 SRS를 구조화된 JSON 테스트 시나리오로 변환"""
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
3. Google/Naver 검색어 입력 시 action="fill"을 사용한다.
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
        """각 단계의 시작, 성공, 실패 이벤트를 타임스탬프와 함께 기록"""
        base = {"ts": now_iso()}
        base.update(payload)
        append_jsonl(self.path_log, base)

    def _capture_selector(self, loc, step: Dict[str, Any]) -> None:
        """
        성공적으로 찾은 요소의 고유한 CSS Selector를 추출하여 시나리오에 저장합니다.
        이 정보는 나중에 생성되는 리그레션 스크립트에서 '의도 기반 탐색' 없이 즉시 요소를 타겟팅하는 데 사용됩니다.
        특히 네이버/구글의 동적 ID(fdr- 등)를 감지하여 제외하는 지능형 경로 생성 로직이 포함되어 있습니다.
        """
        try:
            if loc.count() == 0: return
            selector = loc.first.evaluate("""el => {
                const isDynamic = (id) => {
                    if (!id) return true;
                    // 동적 ID 판별: 너무 길거나, 숫자가 너무 많거나, 특정 패턴(fdr-, u_)인 경우
                    if (id.length > 25) return true;
                    if ((id.match(/\\d/g) || []).length > 6) return true;
                    if (id.startsWith('fdr-') || id.startsWith('u_')) return true;
                    if (/^[0-9a-f-]+$/.test(id) && id.includes('-')) return true;
                    return false;
                };
                const testid = el.getAttribute('data-testid') || el.getAttribute('data-test-id');
                if (testid) return `[data-testid="${testid}"]`;
                if (el.id && !isDynamic(el.id)) return '#' + el.id;

                const parts = [];
                while (el && el.nodeType === Node.ELEMENT_NODE) {
                    let sel = el.nodeName.toLowerCase();
                    if (el.id && !isDynamic(el.id)) { // 고정 ID가 있으면 즉시 반환
                                          
                        sel += '#' + el.id;
                        parts.unshift(sel);
                        break;
                    } else {
                        let sibling = el, nth = 1;
                        while (sibling = sibling.previousElementSibling) {
                            if (sibling.nodeName.toLowerCase() == sel) nth++;
                        }
                        if (nth != 1) sel += `:nth-of-type(${nth})`;
                    } // ID가 없으면 부모로 올라가며 경로 생성
                    parts.unshift(sel);
                    el = el.parentNode;
                }
                return parts.join(' > ');
            }""")
            if isinstance(step.get("target"), dict):
                step["target"]["selector"] = selector
            else:
                step["target"] = {"selector": selector, "text": str(step.get("target", ""))}
        except:
            pass

    def _execute_action(self, page, resolver: LocatorResolver, step: Dict[str, Any]) -> None:
        """
        Playwright를 사용하여 실제 브라우저 액션을 수행하는 실행기입니다.
        단순 클릭/입력 외에도 리캡챠 우회를 위한 '인간다운 입력(Human-like typing)' 로직이 포함되어 있습니다.
        """
        action = step.get("action")
        value = step.get("value")
        target_data = step.get("target")
        
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
                
                # [개선] combobox인 경우 내부의 실제 입력 필드를 찾아 조작
                target_el = loc.first
                if target.role == "combobox":
                    inner = loc.first.locator("input, textarea, [contenteditable='true']").first
                    if inner.count() > 0: target_el = inner

                # [중요] 안정성을 위해 입력 전 클릭하여 포커스를 확보하고 셀렉터를 박제합니다.
                self._capture_selector(target_el, step)
                target_el.click(timeout=DEFAULT_TIMEOUT_MS)
                page.wait_for_timeout(random.randint(400, 800))
                target_el.press(key_name)
            else:
                page.keyboard.press(key_name)
            page.wait_for_timeout(1000)
            return

        # 3. 요소 타겟팅이 필요한 액션들
        target_actions = [
            "click", "double_click", "hover", "fill", "check",
            "select_option", "scroll", "assert_text", "assert_visible"
        ]
        
        if action in target_actions:
            target = IntentTarget.from_dict(step.get("target") or {})
            loc = resolver.resolve(target)
            
            # (1) 마우스 조작
            if action == "click":
                self._capture_selector(loc.first, step)
                loc.first.click(timeout=DEFAULT_TIMEOUT_MS)
                page.wait_for_timeout(500)
            elif action == "double_click":
                self._capture_selector(loc.first, step)
                loc.first.dblclick(timeout=DEFAULT_TIMEOUT_MS)
                page.wait_for_timeout(500)
            elif action == "hover":
                self._capture_selector(loc.first, step)
                loc.first.hover(timeout=DEFAULT_TIMEOUT_MS)
                page.wait_for_timeout(500)
            
            # (2) 입력 및 선택
            elif action == "fill":
                target_el = loc.first
                if target.role == "combobox":
                    inner = loc.first.locator("input, textarea, [contenteditable='true']").first
                    if inner.count() > 0: target_el = inner
                
                # [중요] 리캡챠 우회를 위한 인간다운 입력 시뮬레이션
                self._capture_selector(target_el, step)
                target_el.click(timeout=DEFAULT_TIMEOUT_MS)
                page.wait_for_timeout(random.randint(500, 1000))
                # 기존 내용을 비우고, 글자마다 랜덤한 지연 시간을 두어 타이핑합니다.
                target_el.fill("")
                for char in str(value or ""):
                    target_el.press(char)
                    page.wait_for_timeout(random.randint(200, 800))
            elif action == "check":
                self._capture_selector(loc.first, step)
                loc.first.check(timeout=DEFAULT_TIMEOUT_MS)
            elif action == "select_option":
                self._capture_selector(loc.first, step)
                loc.first.select_option(value=str(value or ""), timeout=DEFAULT_TIMEOUT_MS)

            # (3) 스크롤
            elif action == "scroll":
                self._capture_selector(loc.first, step)
                loc.first.scroll_into_view_if_needed(timeout=DEFAULT_TIMEOUT_MS)
                
            # (4) 검증 (Assertion) - 실패 시 에러 발생
            elif action == "assert_visible":
                self._capture_selector(loc.first, step)
                # 단순히 현재 보이는지가 아니라, 나타날 때까지 대기하도록 수정
                loc.first.wait_for(state="visible", timeout=DEFAULT_TIMEOUT_MS)
            elif action == "assert_text":
                self._capture_selector(loc.first, step)
                expected = str(value or "")
                loc.first.wait_for(state="visible", timeout=DEFAULT_TIMEOUT_MS)
                actual = loc.first.inner_text()
                if expected not in actual:
                    raise AssertionError(f"Text mismatch. Expected '{expected}' in '{actual}'")
            
            return
            
        raise RuntimeError(f"Unsupported action: {action}")

    def _heal_step(self, page, resolver, step, action, error_text) -> Tuple[bool, str]:
        """
        테스트 실패 시 3단계 복구 시도:
        1. Fallback: 시나리오에 정의된 예비 타겟 시도
        2. Candidate: 접근성 트리 기반 유사 요소 탐색
        3. LLM: AI에게 현재 상황을 물어보고 해결책 제안받기
        """
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
        """수행 결과 데이터를 HTML 파일로 저장"""
        html = build_html_report(rows)
        with open(self.path_report, "w", encoding="utf-8") as f:
            f.write(html)

    def execute(self, scenario: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """시나리오의 각 단계를 순차적으로 실행하며 페이지 전환 및 캡차 발생 여부 감시"""
        rows, healed_scenario = [], json.loads(json.dumps(scenario))
        
        with sync_playwright() as p:
            # 에이전트 실행 시에도 자동화 플래그 제거 및 스텔스 인자 강화
            browser = p.chromium.launch(
                headless=DEFAULT_HEADLESS, 
                slow_mo=DEFAULT_SLOW_MO_MS,
                args=[
                    '--disable-blink-features=AutomationControlled',
                    '--no-sandbox',
                    '--disable-infobars',
                    '--window-position=0,0',
                    '--disable-extensions'
                ]
            )
            context = browser.new_context(
                viewport={"width": 1920, "height": 1080},
                user_agent=REAL_USER_AGENT,
                locale="ko-KR"
            )
            page = context.new_page()
            
            # [Enhanced Stealth] 봇 감지 우회를 위한 스크립트 주입
            page.add_init_script("""
                // 1. Hide automation signs
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                if (Object.getPrototypeOf(navigator).hasOwnProperty('webdriver')) {
                    delete Object.getPrototypeOf(navigator).webdriver;
                }
                
                // 2. Mock Chrome runtime with full properties
                window.chrome = {
                    runtime: {},
                    app: { isInstalled: false, InstallState: { DISABLED: 'disabled', INSTALLED: 'installed', NOT_INSTALLED: 'not_installed' }, getDetails: () => {}, getIsInstalled: () => false },
                    csi: () => {},
                    loadTimes: () => {}
                };
                
                // 3. Fix Permissions query
                window.navigator.permissions.query = (parameters) => (
                    parameters.name === 'notifications' ?
                    Promise.resolve({ state: Notification.permission }) :
                    originalQuery(parameters)
                );
                
                // 4. Mock Plugins and MimeTypes
                Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5], configurable: true });
                Object.defineProperty(navigator, 'mimeTypes', { get: () => [1, 2, 3, 4, 5], configurable: true });
                Object.defineProperty(navigator, 'languages', { get: () => ['ko-KR', 'ko', 'en-US', 'en'] });
                Object.defineProperty(navigator, 'vendor', { get: () => 'Google Inc.' });
                Object.defineProperty(navigator, 'maxTouchPoints', { get: () => 0 });
                
                // 5. Mask WebGL Vendor/Renderer
                const getParameter = WebGLRenderingContext.prototype.getParameter;
                WebGLRenderingContext.prototype.getParameter = function(parameter) {
                    if (parameter === 37445) return 'Intel Inc.';
                    if (parameter === 37446) return 'Intel(R) Iris(TM) Plus Graphics 640';
                    return getParameter.apply(this, arguments);
                };
                
                // 6. Fix Headless window properties
                Object.defineProperty(window, 'outerWidth', { get: () => window.innerWidth });
                Object.defineProperty(window, 'outerHeight', { get: () => window.innerHeight });
                Object.defineProperty(window, 'devicePixelRatio', { get: () => 1 });
                Object.defineProperty(navigator, 'platform', { get: () => 'Win32' });
                
                // 7. Add Canvas Fingerprinting protection
                const originalGetImageData = CanvasRenderingContext2D.prototype.getImageData;
                CanvasRenderingContext2D.prototype.getImageData = function (x, y, w, h) {
                    const imageData = originalGetImageData.apply(this, arguments);
                    imageData.data[0] = imageData.data[0] + (Math.random() > 0.5 ? 1 : -1);
                    return imageData;
                };
                
                // 8. Add fake hardware info
                Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8 });
                Object.defineProperty(navigator, 'deviceMemory', { get: () => 8 });
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
                    if action in ["click", "fill", "check", "hover", "select_option", "assert_text", "assert_visible", "press_key"]:
                        ok, heal_stage = self._heal_step(page, resolver, step, action, str(e))
                        if not ok: status = "FAIL"
                    else: status = "FAIL"

                # 액션 수행 후 새 탭이나 새 창이 열렸는지 확인하여 제어권 전환
                if len(context.pages) > pages_before:
                    new_page = context.pages[-1]
                    try: new_page.wait_for_load_state("domcontentloaded")
                    except: pass
                    page = new_page
                    resolver = LocatorResolver(page, FAST_TIMEOUT_MS)
                    self._log_step_event({"phase": "exec", "step": sid, "event": "new_page_detected", "url": page.url})

                # 각 단계 완료 후 증적 스크린샷 저장
                evidence = f"step_{sid}_{status.lower()}.png"
                try: page.screenshot(path=os.path.join(self.out_dir, evidence))
                except Exception: pass
                
                self._log_step_event({"phase": "exec", "step": sid, "status": status, "heal": heal_stage})
                rows.append({"step": sid, "action": action, "description": desc, "heal_stage": heal_stage, "status": status, "evidence": evidence})
                if status == "FAIL": break

            browser.close()
        return rows, healed_scenario

    def run(self) -> None:
        """에이전트 실행 메인 엔트리: 계획 -> 실행 -> 리포트 저장"""
        append_jsonl(self.path_log, {"ts": now_iso(), "phase": "run", "status": "start", "headless": DEFAULT_HEADLESS})
        scenario = self.plan_scenario()
        rows, healed = self.execute(scenario)
        write_json(self.path_healed, healed)

        # [추가] 리그레션 스크립트 생성 및 저장
        passed_steps = [step for step, row in zip(healed, rows) if row.get("status") == "PASS"]
        regression_script = self.generate_regression_script(passed_steps)
        with open(os.path.join(self.out_dir, "regression_test.py"), "w", encoding="utf-8") as f:
            f.write(regression_script)
        log(f"Regression script generated: regression_test.py")

        self.save_report(rows)
        append_jsonl(self.path_log, {"ts": now_iso(), "phase": "run", "status": "end"})

        # 하나라도 실패하면 Jenkins 빌드를 실패로 처리하기 위해 종료 코드 1 반환
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