#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
dify_sonar_issue_analyzer.py

목적
- sonar_issue_exporter.py가 만든 sonar_issues.json을 읽는다.
- 이슈 1건씩 Dify Workflow(/v1/workflows/run)에 입력으로 전달한다.
- Dify End(Output)에서 내려온 outputs를 llm_analysis.jsonl로 저장한다.
- Jenkins 파이프라인(Job #5)에서 SonarQube 이슈를 LLM이 해석해
  제목/설명/라벨을 만드는 단계에 사용된다.
- 결과물인 llm_analysis.jsonl은 다음 단계(gitlab_issue_creator.py)에서
  실제 GitLab 이슈 생성 입력으로 활용된다.
- [수정] GitLab 이슈 제목의 정확성을 위해 SonarQube 원본 Message를 추출하여 전달한다.

중요
- Dify Workflow Start inputs 변수명은 아래 7개를 "그대로" 사용한다.
  sonar_issue_json, sonar_rule_json, code_snippet, sonar_issue_url,
  sonar_issue_key, sonar_project_key, kb_query

- Dify Workflow End outputs는 아래 3개 키를 "반드시" 반환해야 한다.
  title, description_markdown, labels
"""

import argparse
import json
import sys
import time
from urllib.parse import urljoin
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError


def _http_post_json(url: str, headers: dict, payload: dict, timeout: int = 60):
    """
    간단한 HTTP POST 헬퍼.
    """
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    h = dict(headers or {})
    h["Content-Type"] = "application/json"
    h["Accept"] = "application/json"
    req = Request(url, method="POST", headers=h, data=data)
    with urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8", errors="replace")
        return resp.status, body


def _safe_json_dumps(obj) -> str:
    """
    JSON 직렬화를 실패 없이 수행하기 위한 헬퍼.
    """
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def build_kb_query(issue_record: dict) -> str:
    """
    LLM이 참고할 추가 검색어(kb_query)를 만든다.
    """
    msg = ""
    try:
        msg = (issue_record.get("issue_search_item") or {}).get("message") or ""
    except Exception:
        msg = ""
    rule_key = issue_record.get("sonar_rule_key") or ""
    sev = ""
    try:
        sev = (issue_record.get("issue_search_item") or {}).get("severity") or ""
    except Exception:
        sev = ""
    q = f"{rule_key} {sev} {msg}".strip()
    return (q[:200]).strip()


def dify_run_workflow(*, dify_api_base: str, dify_api_key: str, inputs: dict, user: str,
                      response_mode: str, timeout: int):
    """
    Dify Workflow 실행을 감싸는 함수.
    """
    if not isinstance(dify_api_base, str):
        raise TypeError(f"dify_api_base must be str, got {type(dify_api_base)}")

    endpoint = urljoin(dify_api_base.rstrip("/") + "/", "workflows/run")
    headers = {"Authorization": f"Bearer {dify_api_key}"}

    payload = {
        "inputs": inputs,
        "response_mode": response_mode,
        "user": user,
    }

    status, body = _http_post_json(endpoint, headers, payload, timeout=timeout)

    if status != 200:
        return False, status, body, None, None, None

    try:
        parsed = json.loads(body)
    except Exception:
        return False, status, body, None, None, None

    data = parsed.get("data") or {}
    if not isinstance(data, dict):
        data = {}

    run_status = data.get("status") or parsed.get("status")
    run_id = parsed.get("workflow_run_id") or data.get("workflow_run_id") or data.get("id")

    if run_status and str(run_status).lower() not in ("succeeded", "success", "completed"):
        return False, status, body, None, run_status, run_id

    outputs = data.get("outputs")
    if outputs is None:
        outputs = (parsed.get("data") or {}).get("outputs") if isinstance(parsed.get("data"), dict) else None

    return True, status, body, outputs, run_status, run_id


def main() -> int:
    """
    CLI 엔트리포인트.
    """
    ap = argparse.ArgumentParser()
    ap.add_argument("--dify-api-base", required=True, help="ex) http://api:5001/v1")
    ap.add_argument("--dify-api-key", required=True, help="Dify API Key")
    ap.add_argument("--input", required=True, help="sonar_issues.json")
    ap.add_argument("--output", default="llm_analysis.jsonl", help="output jsonl")
    ap.add_argument("--user", default="jenkins", help="Dify user field")
    ap.add_argument("--response-mode", default="blocking", choices=["blocking", "streaming"], help="Dify response_mode")
    ap.add_argument("--timeout", type=int, default=180, help="HTTP timeout seconds")
    ap.add_argument("--max-issues", type=int, default=0, help="0=all, else limit")
    ap.add_argument("--print-first-errors", type=int, default=0, help="print first N errors body")
    args = ap.parse_args()

    try:
        with open(args.input, "r", encoding="utf-8") as f:
            src = json.load(f)
    except Exception as e:
        print(f"[ERROR] failed to read {args.input}: {e}", file=sys.stderr)
        return 2

    issues = src.get("issues") or []
    if not isinstance(issues, list):
        print("[ERROR] input sonar_issues.json format invalid: issues is not list", file=sys.stderr)
        return 2

    limit = args.max_issues if args.max_issues and args.max_issues > 0 else len(issues)

    analyzed = 0
    failed = 0
    printed = 0

    out_fp = open(args.output, "w", encoding="utf-8")

    try:
        for rec in issues[:limit]:
            sonar_issue_key = rec.get("sonar_issue_key") or ""
            sonar_project_key = rec.get("sonar_project_key") or ""
            sonar_issue_url = rec.get("sonar_issue_url") or ""

            # [수정] Severity 및 Message 추출
            # - GitLab 제목 생성을 위해 SonarQube의 원본 메시지와 심각도를 추출하여 보존한다.
            issue_item = rec.get("issue_search_item") or {}
            severity = issue_item.get("severity") or ""
            sonar_message = issue_item.get("message") or ""

            sonar_issue_json = _safe_json_dumps({
                "sonar_project_key": sonar_project_key,
                "sonar_issue_key": sonar_issue_key,
                "sonar_issue_url": sonar_issue_url,
                "issue_search_item": issue_item,
                "issue_detail": rec.get("issue_detail") or {},
                "component": rec.get("component") or "",
                "sonar_rule_key": rec.get("sonar_rule_key") or "",
            })
            sonar_rule_json = _safe_json_dumps(rec.get("rule_detail") or {})
            code_snippet = rec.get("code_snippet") or ""
            kb_query = build_kb_query(rec)

            inputs = {
                "sonar_issue_json": sonar_issue_json,
                "sonar_rule_json": sonar_rule_json,
                "code_snippet": code_snippet,
                "sonar_issue_url": sonar_issue_url,
                "sonar_issue_key": sonar_issue_key,
                "sonar_project_key": sonar_project_key,
                "kb_query": kb_query,
            }

            try:
                ok, http_status, body, outputs, run_status, run_id = dify_run_workflow(
                    dify_api_base=args.dify_api_base,
                    dify_api_key=args.dify_api_key,
                    inputs=inputs,
                    user=args.user,
                    response_mode=args.response_mode,
                    timeout=args.timeout,
                )
            except Exception as e:
                failed += 1
                print(f"[DIFY][FAIL][unexpected] issue={sonar_issue_key} err={e}", file=sys.stderr)
                continue

            if not ok:
                failed += 1
                print(f"[DIFY][FAIL][http={http_status}] issue={sonar_issue_key} status={run_status} run_id={run_id}", file=sys.stderr)
                if args.print_first_errors and printed < args.print_first_errors:
                    print(f"[DIFY][FAIL][body] {body}", file=sys.stderr)
                    printed += 1
                continue

            if not isinstance(outputs, dict):
                failed += 1
                print(f"[DIFY][FAIL][outputs] issue={sonar_issue_key} outputs is not dict status={run_status} run_id={run_id}", file=sys.stderr)
                if args.print_first_errors and printed < args.print_first_errors:
                    print(f"[DIFY][FAIL][body] {body}", file=sys.stderr)
                    printed += 1
                continue

            # 스키마 매핑 오류 감지
            if isinstance(outputs.get("title"), dict) and outputs.get("title", {}).get("type") == "string":
                failed += 1
                print(f"[DIFY][FAIL][outputs] issue={sonar_issue_key} outputs mapped to schema object", file=sys.stderr)
                if args.print_first_errors and printed < args.print_first_errors:
                    print(f"[DIFY][FAIL][outputs] {json.dumps(outputs, ensure_ascii=False)}", file=sys.stderr)
                    printed += 1
                continue

            # 필수 필드 체크
            need_keys = ["title", "description_markdown", "labels"]
            if any(k not in outputs for k in need_keys):
                failed += 1
                print(f"[DIFY][FAIL][outputs] issue={sonar_issue_key} missing keys in outputs status={run_status} run_id={run_id}", file=sys.stderr)
                if args.print_first_errors and printed < args.print_first_errors:
                    print(f"[DIFY][FAIL][outputs] {json.dumps(outputs, ensure_ascii=False)}", file=sys.stderr)
                    printed += 1
                continue

            row = {
                "sonar_issue_key": sonar_issue_key,
                "sonar_project_key": sonar_project_key,
                "sonar_issue_url": sonar_issue_url,
                "severity": severity,          # [추가] 심각도
                "sonar_message": sonar_message, # [추가] Sonar 원본 메시지 (제목용)
                "kb_query": kb_query,
                "code_snippet": code_snippet,
                "outputs": outputs,
                "generated_at": int(time.time()),
            }
            # 한 줄 JSON으로 파일에 쌓는다.
            out_fp.write(json.dumps(row, ensure_ascii=False) + "\n")
            analyzed += 1

    finally:
        out_fp.close()

    print(f"[OK] analyzed: {analyzed}, failed: {failed}, output={args.output}")
    return 2 if failed > 0 else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (HTTPError, URLError) as e:
        print(f"[ERROR] network/http error: {e}", file=sys.stderr)
        sys.exit(2)
    except Exception as e:
        print(f"[ERROR] unexpected: {e}", file=sys.stderr)
        sys.exit(2)