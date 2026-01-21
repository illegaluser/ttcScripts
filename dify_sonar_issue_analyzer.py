#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
dify_sonar_issue_analyzer.py
[목적] Dify Workflow 분석 실행.
"""

import argparse
import json
import sys
import time
from urllib.parse import urljoin
from urllib.request import Request, urlopen
from urllib.error import HTTPError

def _http_post_json(url: str, headers: dict, payload: dict, timeout: int = 60):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    h = dict(headers or {})
    h["Content-Type"] = "application/json"
    h["Accept"] = "application/json"
    req = Request(url, method="POST", headers=h, data=data)
    try:
        with urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return resp.status, body
    except HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")
    except Exception as e:
        return 0, str(e)

def _safe_json_dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))

def build_kb_query(issue_record: dict) -> str:
    msg = (issue_record.get("issue_search_item") or {}).get("message") or ""
    rule_key = issue_record.get("sonar_rule_key") or ""
    sev = (issue_record.get("issue_search_item") or {}).get("severity") or ""
    return f"{rule_key} {sev} {msg}".strip()[:200]

def dify_run_workflow(*, dify_api_base: str, dify_api_key: str, inputs: dict, user: str, response_mode: str, timeout: int):
    endpoint = urljoin(dify_api_base.rstrip("/") + "/", "workflows/run")
    headers = {"Authorization": f"Bearer {dify_api_key}"}
    payload = {"inputs": inputs, "response_mode": response_mode, "user": user}

    # [디버그] Dify로 보내는 데이터를 파일로 저장하여 증거 확보
    try:
        with open("dify_input_debug.json", "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
    except: pass

    status, body = _http_post_json(endpoint, headers, payload, timeout=timeout)
    
    if status >= 400:
        return False, status, body, None
        
    try:
        data = json.loads(body)
        run_status = data.get("data", {}).get("status") or data.get("status")
        outputs = data.get("data", {}).get("outputs") or data.get("outputs")
        
        if run_status and str(run_status).lower() not in ("succeeded", "success", "completed"):
            return False, status, body, None
            
        return True, status, body, outputs
    except Exception:
        return False, status, body, None

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dify-api-base", required=True)
    ap.add_argument("--dify-api-key", required=True)
    ap.add_argument("--input", required=True)
    ap.add_argument("--output", default="llm_analysis.jsonl")
    ap.add_argument("--user", default="jenkins")
    ap.add_argument("--response-mode", default="blocking")
    ap.add_argument("--timeout", type=int, default=180)
    ap.add_argument("--max-issues", type=int, default=0)
    ap.add_argument("--print-first-errors", type=int, default=5)
    args = ap.parse_args()

    try:
        with open(args.input, "r", encoding="utf-8") as f:
            src = json.load(f)
    except Exception as e:
        print(f"[ERROR] Read failed: {e}", file=sys.stderr)
        return 2

    issues = src.get("issues") or []
    limit = args.max_issues if args.max_issues > 0 else len(issues)
    
    analyzed = 0
    failed = 0
    printed = 0

    out_fp = open(args.output, "w", encoding="utf-8")

    for rec in issues[:limit]:
        sonar_key = rec.get("sonar_issue_key")
        issue_item = rec.get("issue_search_item") or {}
        rule_detail = rec.get("rule_detail") or {}
        raw_snippet = rec.get("code_snippet") or ""
        
        target_line = issue_item.get("line")
        rule_key = rec.get("sonar_rule_key", "")
        rule_name = rule_detail.get("name", "")
        
        # 언어 감지
        lang_hint = "CODE"
        if ":" in rule_key:
            lang_hint = rule_key.split(":")[0].upper()
        
        injected_snippet = (
            f"!!! SYSTEM INSTRUCTION: THIS IS {lang_hint} CODE. ANALYZE BASED ON {lang_hint} RULES !!!\n"
            f"=== VIOLATED RULE: {rule_key} ===\n"
            f"Rule Name: {rule_name}\n"
            f"Description: {rule_detail.get('mdDesc') or rule_detail.get('description') or ''}\n"
            f"--------------------------------------------------\n"
            f"=== TARGET CODE (Context +/- 20 lines) ===\n"
            f"{raw_snippet}"
        )
        
        if len(injected_snippet) > 40000:
            injected_snippet = injected_snippet[:40000] + "\n...(truncated)..."

        inputs = {
            "sonar_issue_json": _safe_json_dumps({
                "key": sonar_key,
                "project": rec.get("sonar_project_key"),
                "severity": issue_item.get("severity"),
                "message": issue_item.get("message"),
                "line": target_line,
                "component": rec.get("component")
            }),
            "sonar_rule_json": _safe_json_dumps(rule_detail),
            "code_snippet": injected_snippet,
            "sonar_issue_url": rec.get("sonar_issue_url"),
            "sonar_issue_key": sonar_key,
            "sonar_project_key": rec.get("sonar_project_key"),
            "kb_query": build_kb_query(rec),
        }

        try:
            ok, http, body, outputs = dify_run_workflow(
                dify_api_base=args.dify_api_base,
                dify_api_key=args.dify_api_key,
                inputs=inputs,
                user=args.user,
                response_mode=args.response_mode,
                timeout=args.timeout
            )
        except Exception as e:
            failed += 1
            print(f"[FAIL] Exception {sonar_key}: {e}", file=sys.stderr)
            continue

        if not ok:
            failed += 1
            if printed < args.print_first_errors:
                print(f"[FAIL] Dify error {sonar_key}: http={http} body={body}", file=sys.stderr)
                printed += 1
            continue

        if not isinstance(outputs, dict): outputs = {}

        row = {
            "sonar_issue_key": sonar_key,
            "severity": issue_item.get("severity"),
            "sonar_message": issue_item.get("message"),
            "outputs": outputs,
            "generated_at": int(time.time()),
        }
        out_fp.write(json.dumps(row, ensure_ascii=False) + "\n")
        analyzed += 1

    out_fp.close()
    print(f"[OK] Analyzed: {analyzed}, Failed: {failed}", file=sys.stdout)
    return 2 if failed else 0

if __name__ == "__main__":
    sys.exit(main())