#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# DSCORE-TTC Zero-Touch QA Agent (v1.8)
#
# 목적
# - 자연어 요구사항(SRS)을 입력받아 Intent 기반 테스트 시나리오를 생성한다.
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

# Headless 모드
# - Jenkins 환경에서는 headless=1을 기본으로 둔다.
DEFAULT_HEADLESS = os.getenv("HEADLESS", "1") == "1"

# Slow motion(ms)
# - 화면 안정성을 위해 액션 간 지연을 둔다.
DEFAULT_SLOW_MO_MS = int(os.getenv("SLOW_MO_MS", "300"))

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
