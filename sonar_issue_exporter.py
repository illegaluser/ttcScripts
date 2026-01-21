#!/usr/bin/env python3
# -*- coding: utf-8 -*-

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
    HTML 태그 제거 및 엔티티 디코딩
    """
    if not text: return ""
    # 1. 태그 제거 (<span ...>, </div> 등)
    text = re.sub(r'<[^>]+>', '', text)
    # 2. 엔티티 디코딩 (&lt; -> <)
    text = html.unescape(text)
    return text

def _http_get_json(url: str, headers: dict, timeout: int = 60) -> dict:
    req = Request(url, headers=headers, method="GET")
    with urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))

def _build_basic_auth(token: str) -> str:
    return "Basic " + base64.b64encode(f"{token}:".encode("utf-8")).decode("ascii")

def _api_url(host: str, path: str, params: dict = None) -> str:
    base = host.rstrip("/") + "/"
    url = urljoin(base, path.lstrip("/"))
    if params:
        url += "?" + urlencode(params, doseq=True)
    return url

def _get_rule_details(host: str, headers: dict, rule_key: str) -> dict:
    if not rule_key:
        return {"key": "UNKNOWN", "name": "Unknown", "description": "No rule key."}

    url = _api_url(host, "/api/rules/show", {"key": rule_key})
    
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

        desc_parts = []
        sections = rule.get("descriptionSections", [])
        for sec in sections:
            k = sec.get("key", "").upper().replace("_", " ")
            c = sec.get("content", "")
            if c:
                # 룰 설명도 태그 제거
                desc_parts.append(f"[{k}]\n{_clean_html_tags(c)}")
        
        full_desc = "\n\n".join(desc_parts)
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
    if target_line <= 0 or not component: return ""
    
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
            
            # [핵심 수정] 여기서 HTML 태그를 벗겨냄
            code = _clean_html_tags(raw_code)
            
            marker = ">> " if ln == target_line else "   "
            if len(code) > 400: code = code[:400] + " ...[TRUNCATED]"
            out.append(f"{marker}{ln:>5} | {code}")
        return "\n".join(out)
    except:
        return ""

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sonar-host-url", required=True)
    ap.add_argument("--sonar-token", required=True)
    ap.add_argument("--project-key", required=True)
    ap.add_argument("--output", default="sonar_issues.json")
    ap.add_argument("--severities", default="")
    ap.add_argument("--statuses", default="")
    ap.add_argument("--sonar-public-url", default="")
    args, _ = ap.parse_known_args()

    headers = {"Authorization": _build_basic_auth(args.sonar_token)}

    issues = []
    p = 1
    while True:
        url = _api_url(args.sonar_host_url, "/api/issues/search", {
            "componentKeys": args.project_key,
            "resolved": "false",
            "p": p, "ps": 100, "additionalFields": "_all"
        })
        try:
            res = _http_get_json(url, headers)
            items = res.get("issues", [])
            issues.extend(items)
            if not items or p * 100 >= res.get("paging", {}).get("total", 0): break
            p += 1
        except: break

    print(f"[INFO] Processing {len(issues)} issues...", file=sys.stderr)
    
    enriched = []
    rule_cache = {}

    for issue in issues:
        key = issue.get("key")
        rule_key = issue.get("rule")
        component = issue.get("component")
        
        line = issue.get("line")
        if not line and "textRange" in issue:
            line = issue["textRange"].get("startLine")
        line = int(line) if line else 0

        if rule_key not in rule_cache:
            rule_cache[rule_key] = _get_rule_details(args.sonar_host_url, headers, rule_key)
        
        snippet = _get_code_lines(args.sonar_host_url, headers, component, line)
        if not snippet: snippet = "(Code not found in SonarQube)"

        enriched.append({
            "sonar_issue_key": key,
            "sonar_rule_key": rule_key,
            "sonar_project_key": args.project_key,
            "sonar_issue_url": f"{args.sonar_host_url}/project/issues?id={args.project_key}&issues={key}&open={key}",
            "issue_search_item": issue,
            "rule_detail": rule_cache[rule_key],
            "code_snippet": snippet,
            "component": component
        })

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump({"issues": enriched}, f, ensure_ascii=False, indent=2)
    
    print(f"[OK] Exported {len(enriched)} issues.", file=sys.stdout)

if __name__ == "__main__":
    main()