#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
sonar_issue_exporter.py
[긴급 수정]
- Code Drift Correction: SonarQube DB상의 라인과 실제 파일 라인이 다를 때,
  메시지(예: 'Unexpected var')를 단서로 실제 에러 라인을 찾아내는 로직 추가.
"""

import argparse
import base64
import json
import sys
import time
from urllib.parse import urlencode, urljoin
from urllib.request import Request, urlopen

def _http_get_text(url: str, headers: dict, timeout: int = 60) -> str:
    req = Request(url, headers=headers, method="GET")
    with urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")

def _http_get_json(url: str, headers: dict, timeout: int = 60) -> dict:
    req = Request(url, headers=headers, method="GET")
    with urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))

def _safe_get_json(url: str, headers: dict, label: str = "") -> dict:
    try:
        return _http_get_json(url, headers)
    except:
        return {}

def _build_basic_auth(token: str) -> str:
    return "Basic " + base64.b64encode(f"{token}:".encode("utf-8")).decode("ascii")

def _api_url(host: str, path: str, params: dict = None) -> str:
    base = host.rstrip("/") + "/"
    url = urljoin(base, path.lstrip("/"))
    if params:
        url += "?" + urlencode(params, doseq=True)
    return url

def _find_real_line(lines: list, target_line: int, search_keyword: str) -> int:
    """
    [핵심] DB상 라인(target_line) 주변에서 실제 키워드(var 등)가 있는 라인을 찾는다.
    """
    # 0-based index 변환
    idx = target_line - 1
    
    # 1. 정확한 위치 확인
    if 0 <= idx < len(lines):
        if search_keyword in lines[idx]:
            return target_line

    # 2. 앞뒤 50줄 검색 (Shift 감지)
    start = max(0, idx - 50)
    end = min(len(lines), idx + 50)
    
    for i in range(start, end):
        if search_keyword in lines[i]:
            # 가장 가까운 라인 반환
            return i + 1
            
    return target_line # 못 찾으면 원래 라인 반환

def _get_corrected_snippet(host: str, headers: dict, issue: dict) -> str:
    key = issue.get("key")
    component = issue.get("component")
    message = issue.get("message", "").lower()
    
    # "Unexpected var" 메시지라면 "var "를 검색 키워드로 사용
    keyword = ""
    if "var" in message: keyword = "var "
    elif "const" in message: keyword = "const "
    elif "let" in message: keyword = "let "
    
    target_line = 0
    if "textRange" in issue:
        target_line = issue["textRange"].get("startLine", 0)
    elif "line" in issue:
        target_line = int(issue["line"])
        
    if target_line > 0 and component:
        # 파일 전체를 가져와서 라인을 찾음 (가장 확실함)
        raw_url = _api_url(host, "/api/sources/raw", {"key": component})
        try:
            full_text = _http_get_text(raw_url, headers)
            all_lines = full_text.splitlines()
            
            # [보정 로직 수행]
            real_line = target_line
            if keyword:
                real_line = _find_real_line(all_lines, target_line, keyword)
                
            if real_line != target_line:
                print(f"[WARN] Line Mismatch Detected for {key}! DB:{target_line} -> Actual:{real_line}", file=sys.stderr)
            
            # 보정된 라인 기준으로 ±20줄 추출
            start = max(1, real_line - 20)
            end = min(len(all_lines), real_line + 20)
            
            out = []
            for i in range(start - 1, end):
                curr = i + 1
                marker = ">> " if curr == real_line else "   "
                content = all_lines[i]
                if len(content) > 300: content = content[:300] + "..."
                out.append(f"{marker}{curr:>5} | {content}")
                
            return "\n".join(out)
            
        except Exception as ex:
            print(f"[ERROR] Raw fetch failed: {ex}", file=sys.stderr)
            
    return ""

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sonar-host-url", required=True)
    ap.add_argument("--sonar-token", required=True)
    ap.add_argument("--project-key", required=True)
    ap.add_argument("--sonar-public-url", default="")
    ap.add_argument("--severities", default="BLOCKER,CRITICAL,MAJOR,MINOR")
    ap.add_argument("--statuses", default="OPEN,REOPENED,CONFIRMED")
    ap.add_argument("--output", default="sonar_issues.json")
    args = ap.parse_args()

    headers = {
        "Authorization": _build_basic_auth(args.sonar_token),
        "Accept": "application/json"
    }

    print(f"[INFO] Collecting issues...", file=sys.stderr)
    # (이슈 수집 로직은 동일하므로 생략 없이 전체 코드 유지)
    issues = []
    p = 1
    while True:
        params = {
            "componentKeys": args.project_key,
            "resolved": "false",
            "p": p,
            "ps": 100,
            "additionalFields": "_all",
            "severities": args.severities,
            "statuses": args.statuses
        }
        url = _api_url(args.sonar_host_url, "/api/issues/search", params)
        try:
            res = _http_get_json(url, headers)
            issues.extend(res.get("issues", []))
            if p * 100 >= res.get("paging", {}).get("total", 0): break
            p += 1
        except Exception:
            break

    print(f"[INFO] Found {len(issues)} issues. Enriching with CORRECTION...", file=sys.stderr)

    enriched = []
    rule_cache = {}

    for issue in issues:
        key = issue.get("key")
        rule_key = issue.get("rule")
        
        if rule_key and rule_key not in rule_cache:
            rule_url = _api_url(args.sonar_host_url, "/api/rules/show", {"key": rule_key})
            rule_resp = _safe_get_json(rule_url, headers, f"Rule {rule_key}")
            rule_cache[rule_key] = rule_resp.get("rule", {})

        # [수정] 보정된 스니펫 가져오기
        snippet = _get_corrected_snippet(args.sonar_host_url, headers, issue)
        
        public_url = args.sonar_public_url or args.sonar_host_url
        issue_link = f"{public_url.rstrip('/')}/project/issues?id={args.project_key}&issues={key}&open={key}"

        enriched.append({
            "sonar_issue_key": key,
            "sonar_project_key": args.project_key,
            "sonar_issue_url": issue_link,
            "sonar_rule_key": rule_key,
            "issue_search_item": issue,
            "rule_detail": rule_cache.get(rule_key, {}),
            "code_snippet": snippet,
            "component": issue.get("component")
        })

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump({"issues": enriched}, f, ensure_ascii=False, indent=2)

    print(f"[OK] Exported {len(enriched)} issues.", file=sys.stdout)
    return 0

if __name__ == "__main__":
    sys.exit(main())