#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import json
import sys
import time
import uuid
import re
from urllib.request import Request, urlopen
from urllib.error import HTTPError

# [수정] HTML 정제 함수 삭제 (데이터 손실 원인)
# 대신 룰 설명이 너무 길어 코드를 밀어내는 것만 방지
def truncate_text(text, max_chars=1000):
    if not text: return ""
    if len(text) <= max_chars: return text
    return text[:max_chars] + "... (Rule Truncated)"

def send_dify_request(url, api_key, payload):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = Request(url, method="POST", headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}, data=data)
    try:
        with urlopen(req, timeout=300) as resp:
            return resp.status, resp.read().decode("utf-8")
    except HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")
    except Exception as e:
        return 0, str(e)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dify-api-base", required=True)
    parser.add_argument("--dify-api-key", required=True)
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", default="llm_analysis.jsonl")
    parser.add_argument("--max-issues", type=int, default=0)
    parser.add_argument("--user", default="")
    parser.add_argument("--response-mode", default="")
    parser.add_argument("--timeout", type=int, default=0)
    parser.add_argument("--print-first-errors", type=int, default=0)
    args, _ = parser.parse_known_args()

    try:
        with open(args.input, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print(f"[ERROR] Cannot read input file: {e}", file=sys.stderr)
        sys.exit(1)

    issues = data.get("issues", [])
    if args.max_issues > 0: issues = issues[:args.max_issues]

    out_fp = open(args.output, "w", encoding="utf-8")
    
    base_url = args.dify_api_base.rstrip("/")
    if not base_url.endswith("/v1"):
        base_url += "/v1"
    target_api_url = f"{base_url}/workflows/run"

    print(f"[INFO] Analyzing {len(issues)} issues...", file=sys.stderr)

    for item in issues:
        key = item.get("sonar_issue_key")
        rule = item.get("sonar_rule_key", "")
        project = item.get("sonar_project_key", "")
        
        issue_item = item.get("issue_search_item", {})
        msg = issue_item.get("message", "")
        severity = issue_item.get("severity", "")
        component = item.get("component", "")
        line = issue_item.get("line") or issue_item.get("textRange", {}).get("startLine", 0)
        
        # [핵심 수정] 코드 추출 로직 강화 (여러 키 시도)
        raw_code = item.get("code_snippet", "")
        if not raw_code:
            raw_code = item.get("source", "") or item.get("code", "")
        
        # 가공 없이 원본 사용 (정제 로직 삭제)
        final_code = raw_code if raw_code else "(NO CODE CONTENT)"
        
        # 룰 설명 (길이 제한만 적용, HTML 제거 안 함)
        rule_detail = item.get("rule_detail", {})
        raw_desc = rule_detail.get("description", "")
        safe_desc = truncate_text(raw_desc, max_chars=800)
        
        # 룰 JSON 생성 (중괄호만 치환하여 Dify 에러 방지)
        safe_rule_json = json.dumps({
            "key": rule_detail.get("key"),
            "name": rule_detail.get("name"),
            "description": safe_desc.replace("{", "(").replace("}", ")")
        }, ensure_ascii=False)

        # 메타데이터 JSON 생성
        safe_issue_json = json.dumps({
            "key": key, "rule": rule, "message": msg, "severity": severity,
            "project": project, "component": component, "line": line
        }, ensure_ascii=False)

        session_user = f"jenkins-{uuid.uuid4()}"

        print(f"\n[DEBUG] >>> Sending Issue {key}")
        
        # [데이터 전송] 가공 없는 원본 코드 전송
        inputs = {
            "sonar_issue_key": key,
            "sonar_project_key": project,
            "code_snippet": final_code, 
            "sonar_issue_url": item.get("sonar_issue_url", ""),
            "kb_query": f"{rule} {msg}",
            "sonar_issue_json": safe_issue_json,
            "sonar_rule_json": safe_rule_json
        }

        # [검증 로그] 실제 잡힌 코드 확인
        print(f"   [DATA CHECK] Code Length: {len(final_code)}")
        print(f"   [DATA CHECK] Preview: {final_code[:100].replace(chr(10), ' ')}...")
        
        payload = {
            "inputs": inputs,
            "response_mode": "blocking",
            "user": session_user
        }

        success = False
        for i in range(3):
            status, body = send_dify_request(target_api_url, args.dify_api_key, payload)
            
            if status == 200:
                try:
                    res = json.loads(body)
                    if res.get("data", {}).get("status") == "succeeded":
                        out_row = {
                            "sonar_issue_key": key,
                            "severity": severity,
                            "sonar_message": msg,
                            "outputs": res["data"]["outputs"],
                            "generated_at": int(time.time())
                        }
                        out_fp.write(json.dumps(out_row, ensure_ascii=False) + "\n")
                        success = True
                        print(f"   -> Success.")
                        break
                    else:
                        print(f"   -> Dify Internal Fail: {res}", file=sys.stderr)
                except: pass
            
            print(f"   -> Retry {i+1}/3 due to Status {status} | Error: {body}")
            time.sleep(2)
        
        if not success:
            print(f"[FAIL] Failed to analyze {key}", file=sys.stderr)

    out_fp.close()

if __name__ == "__main__":
    main()