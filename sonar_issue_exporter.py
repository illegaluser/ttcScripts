#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# ==================================================================================
# 파일명: sonar_issue_exporter.py
# 버전: 1.2
#
# [시스템 개요]
# 이 스크립트는 품질 분석 파이프라인(Phase 3)의 **1단계(정적 분석 결과 수집)**를 담당합니다.
# SonarQube REST API를 통해 미해결(open) 이슈 목록을 페이지네이션으로 전수 조회하고,
# 각 이슈에 대해 관련 소스 코드 라인과 위반 규칙 상세 정보를 추가로 수집(enrichment)하여
# 하나의 JSON 파일로 통합합니다.
#
# [파이프라인 내 위치]
# SonarQube (정적 분석 결과)
#       ↓ REST API
# >>> sonar_issue_exporter.py (이슈 수집 + 코드/룰 보강) <<<
#       ↓ sonar_issues.json
# dify_sonar_issue_analyzer.py (AI 분석)
#
# [핵심 동작 흐름]
# 1. /api/issues/search: 미해결 이슈 목록을 100건 단위로 페이지네이션하여 전수 조회
# 2. /api/rules/show: 각 이슈의 위반 규칙 상세 설명을 조회 (캐싱하여 중복 호출 방지)
# 3. /api/sources/lines: 이슈 발생 위치 전후 100줄의 소스 코드를 조회
# 4. 모든 정보를 통합하여 sonar_issues.json 파일로 저장
#
# [실행 예시]
# python3 sonar_issue_exporter.py \
#   --sonar-host-url http://sonarqube:9000 \
#   --sonar-token squ_xxxxx \
#   --project-key myproject \
#   --output sonar_issues.json
# ==================================================================================

import argparse
import base64
import json
import sys
import html
import re
from urllib.parse import urlencode, urljoin
from urllib.request import Request, urlopen


def _clean_html_tags(text: str) -> str:
    """
    HTML 태그를 제거하고 HTML 엔티티를 디코딩합니다.

    SonarQube API 응답에는 코드와 룰 설명에 HTML 태그가 포함되어 있습니다.
    (예: <span class="k">public</span>, &lt;String&gt;)
    LLM이 코드를 정확히 분석하려면 순수 텍스트가 필요하므로,
    태그를 제거하고 엔티티를 원래 문자로 복원합니다.

    Args:
        text: HTML이 포함된 원본 텍스트

    Returns:
        HTML 태그가 제거되고 엔티티가 디코딩된 순수 텍스트
    """
    if not text: return ""
    # 1단계: HTML 태그 제거 (<span ...>, </div> 등)
    text = re.sub(r'<[^>]+>', '', text)
    # 2단계: HTML 엔티티 디코딩 (&lt; → <, &amp; → & 등)
    text = html.unescape(text)
    return text


def _http_get_json(url: str, headers: dict, timeout: int = 60) -> dict:
    """
    HTTP GET 요청을 보내고 JSON 응답을 파싱하여 반환합니다.

    SonarQube의 모든 API 호출에 공통으로 사용되는 헬퍼 함수입니다.
    """
    req = Request(url, headers=headers, method="GET")
    with urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def _build_basic_auth(token: str) -> str:
    """
    SonarQube 토큰을 HTTP Basic Authentication 헤더 값으로 변환합니다.

    SonarQube는 토큰을 사용자명으로, 비밀번호는 빈 문자열로 하는
    Basic Auth 방식을 사용합니다. (token: 형식)
    """
    return "Basic " + base64.b64encode(f"{token}:".encode("utf-8")).decode("ascii")


def _api_url(host: str, path: str, params: dict = None) -> str:
    """
    SonarQube API 엔드포인트의 전체 URL을 생성합니다.

    Args:
        host: SonarQube 호스트 URL (예: http://sonarqube:9000)
        path: API 경로 (예: /api/issues/search)
        params: 쿼리 파라미터 딕셔너리

    Returns:
        완전한 URL 문자열 (쿼리스트링 포함)
    """
    base = host.rstrip("/") + "/"
    url = urljoin(base, path.lstrip("/"))
    if params:
        url += "?" + urlencode(params, doseq=True)
    return url

def _get_rule_details(host: str, headers: dict, rule_key: str) -> dict:
    """
    SonarQube 위반 규칙의 상세 정보를 조회합니다.

    /api/rules/show 엔드포인트에서 규칙의 이름, 설명, 심각도, 언어 정보를 가져옵니다.
    이 정보는 LLM이 이슈를 분석할 때 "왜 이것이 문제인지"를 이해하는 데 사용됩니다.

    SonarQube 규칙 설명은 여러 섹션(descriptionSections)으로 구성될 수 있으며,
    각 섹션에는 HTML 태그가 포함되어 있으므로 태그를 제거한 후 반환합니다.

    Args:
        host: SonarQube 호스트 URL
        headers: Basic Auth 인증 헤더
        rule_key: 규칙 키 (예: "java:S1192")

    Returns:
        dict: 규칙 상세 정보 (key, name, description, severity, lang)
              API 호출 실패 시 기본값을 반환하여 전체 프로세스가 중단되지 않습니다.
    """
    if not rule_key:
        return {"key": "UNKNOWN", "name": "Unknown", "description": "No rule key."}

    url = _api_url(host, "/api/rules/show", {"key": rule_key})

    # API 호출 실패 시 사용할 기본값
    fallback = {
        "key": rule_key,
        "name": f"Rule {rule_key}",
        "description": "No detailed description available.",
        "lang": "code"
    }

    try:
        resp = _http_get_json(url, headers)
        rule = resp.get("rule", {})
        if not rule: return fallback

        # 구조화된 설명 섹션(예: ROOT_CAUSE, HOW_TO_FIX)을 순회하며 텍스트를 수집합니다.
        desc_parts = []
        sections = rule.get("descriptionSections", [])
        for sec in sections:
            k = sec.get("key", "").upper().replace("_", " ")  # 섹션 이름을 대문자로 정리
            c = sec.get("content", "")
            if c:
                # HTML 태그를 제거하여 LLM이 순수 텍스트로 읽을 수 있게 합니다.
                desc_parts.append(f"[{k}]\n{_clean_html_tags(c)}")

        full_desc = "\n\n".join(desc_parts)
        # 구조화 섹션이 없으면 레거시 필드(mdDesc, htmlDesc)를 대안으로 사용합니다.
        if not full_desc:
            raw_desc = rule.get("mdDesc") or rule.get("htmlDesc") or rule.get("description") or ""
            full_desc = _clean_html_tags(raw_desc)

        return {
            "key": rule.get("key", rule_key),
            "name": rule.get("name", fallback["name"]),
            "description": full_desc if full_desc else fallback["description"],
            "severity": rule.get("severity", "UNKNOWN"),
            "lang": rule.get("lang", "code")
        }
    except:
        return fallback

def _get_code_lines(host: str, headers: dict, component: str, target_line: int) -> str:
    """
    이슈가 발생한 소스 코드의 전후 50줄(총 101줄)을 텍스트로 추출합니다.

    SonarQube /api/sources/lines 엔드포인트에서 코드를 가져오며,
    이슈 발생 라인에 ">>" 마커를 붙여 LLM이 문제 지점을 쉽게 식별할 수 있도록 합니다.

    SonarQube가 반환하는 코드에는 구문 강조용 HTML 태그가 포함되어 있으므로
    _clean_html_tags()로 제거합니다.

    Args:
        host: SonarQube 호스트 URL
        headers: Basic Auth 인증 헤더
        component: 파일 컴포넌트 키 (예: "myproject:src/main/App.java")
        target_line: 이슈가 발생한 라인 번호

    Returns:
        str: 줄번호가 포함된 코드 텍스트 (이슈 라인에 ">>" 표시)
             조회 실패 시 빈 문자열 반환
    """
    if target_line <= 0 or not component: return ""

    # 이슈 발생 라인 전후 50줄을 요청합니다.
    start = max(1, target_line - 50)
    end = target_line + 50

    url = _api_url(host, "/api/sources/lines", {"key": component, "from": start, "to": end})
    try:
        resp = _http_get_json(url, headers)
        sources = resp.get("sources", [])
        if not sources: return ""

        out = []
        for src in sources:
            ln = src.get("line", 0)
            raw_code = src.get("code", "")

            # SonarQube가 구문 강조용으로 삽입한 HTML 태그를 제거합니다.
            # (예: <span class="k">public</span> → public)
            code = _clean_html_tags(raw_code)

            # 이슈 발생 라인에 ">>" 마커를 붙여 시각적으로 구분합니다.
            marker = ">> " if ln == target_line else "   "
            # 한 줄이 너무 길면 잘라냅니다 (LLM 토큰 절약).
            if len(code) > 400: code = code[:400] + " ...[TRUNCATED]"
            out.append(f"{marker}{ln:>5} | {code}")
        return "\n".join(out)
    except:
        return ""

def main():
    """
    메인 실행 함수: SonarQube에서 미해결 이슈를 전수 조회하고 코드/룰 정보로 보강합니다.

    [전체 처리 흐름]
    1. CLI 인자 파싱 (SonarQube 접속 정보, 프로젝트 키 등)
    2. /api/issues/search로 미해결 이슈 목록을 페이지네이션하여 전수 조회
    3. 각 이슈에 대해:
       a. 위반 규칙 상세 정보 조회 (동일 규칙은 캐싱하여 중복 호출 방지)
       b. 이슈 발생 위치의 소스 코드 전후 50줄 조회
       c. 모든 정보를 하나의 enriched 객체로 통합
    4. 전체 결과를 sonar_issues.json 파일로 저장
    """
    # ---------------------------------------------------------------
    # [1단계] CLI 인자 파싱
    # ---------------------------------------------------------------
    ap = argparse.ArgumentParser()
    ap.add_argument("--sonar-host-url", required=True)   # SonarQube 호스트 URL
    ap.add_argument("--sonar-token", required=True)       # SonarQube 인증 토큰
    ap.add_argument("--project-key", required=True)       # 분석 대상 프로젝트 키
    ap.add_argument("--output", default="sonar_issues.json")  # 출력 파일 경로
    ap.add_argument("--severities", default="")           # 심각도 필터 (미사용, 하위 호환)
    ap.add_argument("--statuses", default="")             # 상태 필터 (미사용, 하위 호환)
    ap.add_argument("--sonar-public-url", default="")     # 외부 접근용 URL (미사용, 하위 호환)
    args, _ = ap.parse_known_args()

    # SonarQube API 인증 헤더 (Basic Auth)
    headers = {"Authorization": _build_basic_auth(args.sonar_token)}

    # ---------------------------------------------------------------
    # [2단계] 미해결 이슈 전수 조회 (페이지네이션)
    # SonarQube는 한 번에 최대 100건까지 반환하므로,
    # 전체 이슈를 가져오려면 page를 증가시키며 반복 호출해야 합니다.
    # ---------------------------------------------------------------
    issues = []
    p = 1
    while True:
        url = _api_url(args.sonar_host_url, "/api/issues/search", {
            "componentKeys": args.project_key,  # 조회 대상 프로젝트
            "resolved": "false",                # 미해결 이슈만 조회
            "p": p, "ps": 100,                  # 페이지 번호 / 페이지 크기
            "additionalFields": "_all"          # 모든 부가 정보 포함
        })
        try:
            res = _http_get_json(url, headers)
            items = res.get("issues", [])
            issues.extend(items)
            # 더 이상 가져올 이슈가 없거나 전체 수에 도달하면 루프 종료
            if not items or p * 100 >= res.get("paging", {}).get("total", 0): break
            p += 1
        except: break

    print(f"[INFO] Processing {len(issues)} issues...", file=sys.stderr)

    # ---------------------------------------------------------------
    # [3단계] 각 이슈에 대해 룰 정보 + 소스 코드 보강(Enrichment)
    # ---------------------------------------------------------------
    enriched = []
    # 동일한 규칙 키에 대한 중복 API 호출을 방지하는 캐시입니다.
    # 프로젝트에서 같은 규칙 위반이 수십~수백 건 발생할 수 있기 때문입니다.
    rule_cache = {}

    for issue in issues:
        key = issue.get("key")              # SonarQube 이슈 고유 키
        rule_key = issue.get("rule")        # 위반 규칙 ID
        component = issue.get("component")  # 파일 컴포넌트 키

        # 이슈 발생 라인 번호 추출 (두 가지 위치 표현 방식을 모두 지원)
        line = issue.get("line")
        if not line and "textRange" in issue:
            line = issue["textRange"].get("startLine")
        line = int(line) if line else 0

        # --- 3-a. 위반 규칙 상세 정보 조회 (캐싱) ---
        if rule_key not in rule_cache:
            rule_cache[rule_key] = _get_rule_details(args.sonar_host_url, headers, rule_key)

        # --- 3-b. 이슈 발생 위치의 소스 코드 조회 ---
        snippet = _get_code_lines(args.sonar_host_url, headers, component, line)
        if not snippet: snippet = "(Code not found in SonarQube)"

        # --- 3-c. 통합 객체 생성 ---
        # 이 객체가 dify_sonar_issue_analyzer.py의 입력으로 사용됩니다.
        enriched.append({
            "sonar_issue_key": key,           # 이슈 고유 키
            "sonar_rule_key": rule_key,       # 위반 규칙 ID
            "sonar_project_key": args.project_key,  # 프로젝트 키
            "sonar_issue_url": f"{args.sonar_host_url}/project/issues?id={args.project_key}&issues={key}&open={key}",  # SonarQube 이슈 직링크
            "issue_search_item": issue,       # /api/issues/search 원본 응답 항목
            "rule_detail": rule_cache[rule_key],  # 규칙 상세 (이름, 설명, 심각도)
            "code_snippet": snippet,          # 이슈 전후 소스 코드 (">>" 마커 포함)
            "component": component            # 파일 컴포넌트 키
        })

    # ---------------------------------------------------------------
    # [4단계] 결과 저장
    # 전체 enriched 이슈를 하나의 JSON 파일로 저장합니다.
    # 다음 단계(dify_sonar_issue_analyzer.py)가 이 파일을 입력으로 사용합니다.
    # ---------------------------------------------------------------
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump({"issues": enriched}, f, ensure_ascii=False, indent=2)

    print(f"[OK] Exported {len(enriched)} issues.", file=sys.stdout)

if __name__ == "__main__":
    main()