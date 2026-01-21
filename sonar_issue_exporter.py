#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# sonar_issue_exporter.py (Debug Version)
# 목적: 이슈 수집 및 스니펫 추출 실패 원인 상세 로깅

import argparse
import base64
import json
import sys
import time
from urllib.parse import urlencode, urljoin
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

def _http_get_json(url: str, headers: dict, timeout: int = 60) -> dict:
    req = Request(url, headers=headers, method="GET")
    with urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8", errors="replace")
        return json.loads(body)

def _safe_get_json(url: str, headers: dict, context_msg: str = "") -> dict:
    try:
        return _http_get_json(url, headers=headers)
    except Exception as e:
        print(f"[WARN] JSON Fetch Failed ({context_msg}): {e} URL={url}", file=sys.stderr)
        return {}

def _safe_get_text(url: str, headers: dict, context_msg: str = "") -> str:
    try:
        req = Request(url, headers=headers, method="GET")
        with urlopen(req, timeout=60) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        print(f"[WARN] Text Fetch Failed ({context_msg}): {e} URL={url}", file=sys.stderr)
        return ""

def _build_basic_auth(token: str) -> str:
    raw = f"{token}:".encode("utf-8")
    b64 = base64.b64encode(raw).decode("ascii")
    return f"Basic {b64}"

def _api_url(sonar_host_url: str, api_path: str, params: dict) -> str:
    base = sonar_host_url.rstrip("/") + "/"
    qs = urlencode(params, doseq=True)
    return urljoin(base, api_path.lstrip("/")) + ("?" + qs if qs else "")

def _extract_snippet_from_json(snippet_json: dict) -> str:
    if not snippet_json: return ""
    sources = snippet_json.get("sources") or []
    if not sources: return ""
    first = sources[0] or {}
    blocks = first.get("code") or first.get("lines") or []
    lines = []
    for b in blocks:
        if isinstance(b, dict) and "line" in b:
            lines.append(f"{b['line']:>4} | {b.get('text','')}")
    return "\n".join(lines).strip()

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sonar-host-url", required=True)
    ap.add_argument("--sonar-public-url", default="")
    ap.add_argument("--sonar-token", required=True)
    ap.add_argument("--project-key", required=True)
    ap.add_argument("--severities", default="")
    ap.add_argument("--statuses", default="OPEN,REOPENED")
    ap.add_argument("--types", default="")
    ap.add_argument("--ps", type=int, default=200)
    ap.add_argument("--output", default="sonar_issues.json")
    args = ap.parse_args()

    headers = {"Authorization": _build_basic_auth(args.sonar_token)}
    
    # 1. 이슈 검색 (additionalFields 시도)
    print(f"[DEBUG] Fetching issues for {args.project_key}...")
    issues = []
    p = 1
    use_all_fields = True
    
    while True:
        params = {
            "componentKeys": args.project_key,
            "resolved": "false",
            "p": str(p),
            "ps": str(args.ps)
        }
        if use_all_fields: params["additionalFields"] = "_all"
        
        url = _api_url(args.sonar_host_url, "/api/issues/search", params)
        try:
            page = _http_get_json(url, headers=headers)
        except HTTPError:
            if use_all_fields:
                print("[INFO] additionalFields=_all not supported, retrying without it.")
                use_all_fields = False
                continue
            raise

        page_issues = page.get("issues") or []
        issues.extend(page_issues)
        
        paging = page.get("paging") or {}
        if paging.get("pageIndex", p) * paging.get("pageSize", args.ps) >= paging.get("total", 0):
            break
        p += 1

    print(f"[DEBUG] Found {len(issues)} issues. Starting detail fetch...")
    
    enriched = []
    for it in issues:
        key = it.get("key")
        component = it.get("component")
        msg = it.get("message")
        
        # 2. 이슈 상세 조회 (API fallback)
        detail = {}
        if not msg: # 검색 결과에 메시지가 없으면 상세 조회 시도
             detail_url = _api_url(args.sonar_host_url, "/api/issues/show", {"issueKey": key})
             detail = _safe_get_json(detail_url, headers=headers, context_msg="Issue Detail")
             # 데이터 병합 (중요)
             if not msg: it["message"] = detail.get("message")
             if not it.get("severity"): it["severity"] = detail.get("severity")
             if not it.get("rule"): it["rule"] = detail.get("rule")

        # 3. 룰 조회
        rule_key = it.get("rule")
        rule_url = _api_url(args.sonar_host_url, "/api/rules/show", {"key": rule_key})
        rule_json = _safe_get_json(rule_url, headers=headers, context_msg="Rule Detail")

        # 4. 스니펫 추출 (핵심 디버깅 구간)
        snippet = ""
        
        # A. issue_snippets API 시도
        snip_url = _api_url(args.sonar_host_url, "/api/sources/issue_snippets", {"issueKey": key})
        snip_json = _safe_get_json(snip_url, headers=headers, context_msg="Snippet API")
        snippet = _extract_snippet_from_json(snip_json)

        # B. Raw Source Fallback
        if not snippet:
            print(f"[DEBUG] Issue {key}: Snippet API returned empty. Trying Raw Source...")
            
            # 라인 번호 찾기 (textRange -> line 순)
            start_line = 0
            end_line = 0
            
            if "textRange" in it:
                start_line = it["textRange"].get("startLine", 0)
                end_line = it["textRange"].get("endLine", 0)
            elif "line" in it:
                start_line = int(it["line"])
                end_line = int(it["line"])
            
            # Line 정보가 없으면 상세 정보(detail)에서 다시 확인
            if start_line == 0 and detail:
                if "textRange" in detail:
                    start_line = detail["textRange"].get("startLine", 0)
                    end_line = detail["textRange"].get("endLine", 0)
                elif "line" in detail:
                    start_line = int(detail["line"])
                    end_line = int(detail["line"])

            if start_line > 0:
                # 앞뒤 3줄 추가
                s = max(1, start_line - 3)
                e = end_line + 3
                raw_url = _api_url(args.sonar_host_url, "/api/sources/raw", {"key": component, "from": s, "to": e})
                raw_text = _safe_get_text(raw_url, headers=headers, context_msg=f"Raw Source {component}:{s}-{e}")
                
                if raw_text:
                    lines = []
                    curr = s
                    for l in raw_text.splitlines():
                        lines.append(f"{curr:>4} | {l}")
                        curr += 1
                    snippet = "\n".join(lines)
                    print(f"[DEBUG] Issue {key}: Raw Source extraction SUCCESS.")
                else:
                    print(f"[DEBUG] Issue {key}: Raw Source extraction FAILED (Empty response). Check permissions?")
            else:
                print(f"[DEBUG] Issue {key}: No line number found. Cannot fetch code.")

        record = {
            "sonar_issue_key": key,
            "sonar_project_key": args.project_key,
            "sonar_issue_url": args.sonar_public_url + f"/project/issues?id={args.project_key}&issues={key}&open={key}",
            "issue_search_item": it, # 여기에 message, severity가 채워져 있어야 함
            "issue_detail": detail,
            "rule_detail": rule_json,
            "sonar_rule_key": rule_key,
            "component": component,
            "code_snippet": snippet 
        }
        enriched.append(record)

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump({"issues": enriched, "count": len(enriched)}, f, indent=2, ensure_ascii=False)
    
    print(f"[OK] Exported {len(enriched)} issues to {args.output}")
    return 0

if __name__ == "__main__":
    sys.exit(main())