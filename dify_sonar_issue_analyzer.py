#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# ============================================================================
# dify_sonar_issue_analyzer.py
# 목적: 
#   - SonarQube 이슈를 Dify Workflow(LLM)로 전송하여 원인 분석
#   - 각 이슈마다 코드 스니펫, 룰 정보 수집
#   - LLM 분석 결과를 JSONL 파일로 저장
# 
# 주요 특징:
#   - /api/rules/show로 룰 상세 정보 조회
#   - /api/sources/lines로 코드 스니펫 추출
#   - Dify Workflow /v1/workflows/run 호출
#   - 실패가 1건이라도 있으면 exit code 2로 파이프라인 실패 처리
# ============================================================================
import argparse
import json
import sys
import time
from typing import Dict, Any, List, Optional, Tuple
import urllib.parse
import requests

def eprint(*args):
    """
    표준 에러 출력 (stderr)
    """
    print(*args, file=sys.stderr)

def safe_json_dumps(obj) -> str:
    """
    객체를 안전하게 JSON 문자열로 변환한다.
    """
    try:
        return json.dumps(obj, ensure_ascii=False)
    except Exception:
        return str(obj)

def read_json(path: str) -> Dict[str, Any]:
    """
    JSON 파일을 읽어 딕셔너리로 반환한다.
    """
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def write_jsonl(path: str, rows: List[Dict[str, Any]]) -> None:
    """
    JSONL 파일로 저장한다. (한 줄에 하나의 JSON 객체)
    """
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

def sonar_auth_headers(token: str) -> Dict[str, str]:
    """
    SonarQube API 인증 헤더를 생성한다.
    """
    if not token:
        return {}
    return {"Authorization": f"Bearer {token}"}

def sonar_get(session: requests.Session, base: str, path: str, params: Dict[str, Any], token: str) -> Dict[str, Any]:
    """
    SonarQube API GET 요청을 수행한다.
    """
    url = base.rstrip("/") + path
    r = session.get(url, params=params, headers=sonar_auth_headers(token), timeout=60)
    
    # HTTP 오류 처리
    if r.status_code >= 400:
        eprint(f"[SONAR] [ERROR] HTTP {r.status_code}: {r.text[:2000]}")
        r.raise_for_status()
    
    return r.json()

def build_issue_url(sonar_public_url: str, project_key: str, issue_key: str) -> str:
    """
    SonarQube UI에서 이슈를 바로 열 수 있는 URL을 조립한다.
    """
    if not sonar_public_url:
        return ""
    
    base = sonar_public_url.rstrip("/")
    return f"{base}/project/issues?id={urllib.parse.quote(project_key)}&issues={urllib.parse.quote(issue_key)}&open={urllib.parse.quote(issue_key)}"

def fetch_rule_json(session: requests.Session, sonar_host_url: str, sonar_token: str, rule_key: str) -> Dict[str, Any]:
    """
    SonarQube API로 룰 상세 정보를 조회한다.
    """
    if not rule_key:
        return {}
    
    return sonar_get(session, sonar_host_url, "/api/rules/show", {"key": rule_key}, sonar_token)

def fetch_code_snippet(session: requests.Session, sonar_host_url: str, sonar_token: str, component: str, line: Optional[int], context: int = 5) -> str:
    """
    SonarQube API로 코드 라인 범위를 가져온다.
    """
    if not component or not line:
        return ""
    
    # 라인 범위 계산 (이슈 라인 기준 ±context)
    start = max(1, int(line) - context)
    end = int(line) + context
    
    # /api/sources/lines 엔드포인트로 코드 조회
    try:
        data = sonar_get(session, sonar_host_url, "/api/sources/lines", 
                         {"key": component, "from": start, "to": end}, sonar_token)
    except Exception:
        return ""
    
    # 응답 구조는 SonarQube 버전에 따라 다를 수 있음
    lines = data.get("sources") or data.get("lines") or []
    out_lines = []
    
    # 각 라인을 순회하며 포맷팅
    for item in lines:
        ln = item.get("line")
        code = item.get("code", "")
        # 이슈 라인은 >> 표시, 나머지는 공백 3개
        prefix = ">> " if ln == int(line) else "   "
        out_lines.append(f"{prefix}{ln}: {code}")
    
    return "\n".join(out_lines)

def dify_run_workflow(session: requests.Session, dify_api_base: str, dify_api_key: str, inputs: Dict[str, Any], user: str, response_mode: str, timeout: int) -> Tuple[bool, Dict[str, Any], str]:
    """
    Dify Workflow를 실행한다.
    """
    url = dify_api_base.rstrip("/") + "/workflows/run"
    headers = {
        "Authorization": f"Bearer {dify_api_key}",
        "Content-Type": "application/json",
    }
    
    # Workflow 실행 페이로드
    payload = {
        "inputs": inputs,          # Workflow 입력 변수
        "response_mode": response_mode,  # blocking: 전체 응답 대기
        "user": user,              # 사용자 식별자
    }
    
    # API 호출
    r = session.post(url, headers=headers, data=json.dumps(payload), timeout=timeout)
    
    # HTTP 오류 처리
    if r.status_code >= 400:
        try:
            return False, {}, f"HTTP {r.status_code}: {r.text[:2000]}"
        except Exception:
            return False, {}, f"Invalid JSON response: {r.text[:2000]}"
    
    data = r.json()
    return True, data, ""

def pick_outputs(dify_response: Dict[str, Any]) -> Dict[str, Any]:
    """
    Dify 응답에서 outputs를 추출한다.
    """
    outputs = {}
    
    # blocking 모드 기준으로 outputs는 data.outputs 또는 outputs에 있다
    if "data" in dify_response and isinstance(dify_response["data"], dict):
        outputs = dify_response["data"].get("outputs") or {}
    
    if not outputs:
        outputs = dify_response.get("outputs") or {}
    
    if not isinstance(outputs, dict):
        return {}
    
    return outputs

def main():
    """
    SonarQube 이슈를 Dify Workflow로 분석하고 결과를 JSONL로 저장한다.
    """
    ap = argparse.ArgumentParser()
    ap.add_argument("--sonar-host-url", required=True)
    ap.add_argument("--sonar-public-url", required=False, default="")
    ap.add_argument("--sonar-token", required=False, default="")
    ap.add_argument("--dify-api-base", required=True)
    ap.add_argument("--dify-api-key", required=True)
    ap.add_argument("--input", required=True, help="Input JSON file (from sonar_issue_exporter.py)")
    ap.add_argument("--output", required=True, help="Output JSONL file")
    ap.add_argument("--user", required=True, help="Dify user identifier")
    ap.add_argument("--response-mode", required=False, default="blocking")
    ap.add_argument("--timeout", required=False, type=int, default=180)
    ap.add_argument("--max-issues", required=False, type=int, default=0, 
                    help="Max issues to analyze (0 = all)")
    ap.add_argument("--print-first-errors", required=False, type=int, default=5,
                    help="Print first N errors to console")
    args = ap.parse_args()
    
    # 입력 JSON 읽기
    src = read_json(args.input)
    issues = src.get("issues") or []
    project_key = src.get("project_key") or ""
    
    # max-issues 제한 적용
    if args.max_issues and args.max_issues > 0:
        issues = issues[:args.max_issues]
    
    session = requests.Session()
    analyzed = 0  # 분석 성공 카운트
    failed = 0    # 분석 실패 카운트
    rows = []     # JSONL 출력 행
    first_errors = 0  # 콘솔 출력한 오류 개수
    
    # 각 이슈를 순회하며 분석
    for it in issues:
        # 이슈 정보 추출
        issue_key = it.get("key") or ""
        rule_key = it.get("rule") or ""
        component = it.get("component") or ""
        
        # -------------------------------------------------------------------------
        # [FIX] 라인 번호 추출 로직 보강
        # SonarQube 버전에 따라 'line' 필드가 없으면 'textRange' -> 'startLine'을 확인한다.
        # -------------------------------------------------------------------------
        line = it.get("line")
        if not line and "textRange" in it:
            line = it["textRange"].get("startLine")
        
        # SonarQube UI 링크 생성
        sonar_issue_url = build_issue_url(args.sonar_public_url, project_key, issue_key)
        
        # 룰 정보 조회
        try:
            rule_json = fetch_rule_json(session, args.sonar_host_url, args.sonar_token, rule_key)
        except Exception as ex:
            rule_json = {}
            eprint(f"[SONAR] [WARN] rule fetch failed: issue={issue_key} rule={rule_key} err={ex}")
        
        # 코드 스니펫 조회
        # line 정보가 없으면 빈 문자열 반환 (LLM 환각 방지를 위해 필수)
        snippet = ""
        if line:
            try:
                snippet = fetch_code_snippet(session, args.sonar_host_url, args.sonar_token, component, line)
            except Exception as ex:
                eprint(f"[SONAR] [WARN] snippet fetch failed: issue={issue_key} component={component} line={line} err={ex}")
        else:
            # 라인 정보가 없는 경우(파일 전체 이슈 등) 로그를 남김
            eprint(f"[SONAR] [INFO] No line number for issue={issue_key}, skipping snippet fetch.")
        
        # Dify Workflow 입력 변수 구성 (7개 고정)
        # 중요: 변수명이 Dify Start 노드와 정확히 일치해야 함
        inputs = {
            "sonar_issue_json": safe_json_dumps(it),           # 이슈 전체 JSON
            "sonar_rule_json": safe_json_dumps(rule_json),     # 룰 상세 JSON
            "code_snippet": snippet,                           # 코드 스니펫 (비어있으면 안됨)
            "sonar_issue_url": sonar_issue_url,                # SonarQube UI 링크
            "sonar_issue_key": issue_key,                      # 이슈 키
            "sonar_project_key": project_key,                  # 프로젝트 키
            "kb_query": f"{rule_key} {it.get('message','')}".strip(),  # Knowledge Base 검색 키워드
        }
        
        # Dify Workflow 실행
        ok, resp, err = dify_run_workflow(
            session=session,
            dify_api_base=args.dify_api_base,
            dify_api_key=args.dify_api_key,
            inputs=inputs,
            user=args.user,
            response_mode=args.response_mode,
            timeout=args.timeout
        )
        
        # 실패 처리
        if not ok:
            failed += 1
            if first_errors < args.print_first_errors:
                first_errors += 1
                eprint(f"[DIFY] [FAIL] issue={issue_key} {err}")
            continue
        
        # 응답에서 outputs 추출
        outputs = pick_outputs(resp)
        title = outputs.get("title") or ""
        description_markdown = outputs.get("description_markdown") or ""
        labels = outputs.get("labels") or ""
        
        # 출력 타입 검증 (최소 안전장치)
        if not isinstance(title, str) or not isinstance(description_markdown, str) or not isinstance(labels, str):
            failed += 1
            if first_errors < args.print_first_errors:
                first_errors += 1
                eprint(f"[DIFY] [FAIL] issue={issue_key} outputs type invalid: {type(outputs)}")
            continue
        
        # JSONL 행 추가
        rows.append({
            "sonar_issue_key": issue_key,
            "sonar_project_key": project_key,
            "sonar_issue_url": sonar_issue_url,
            "kb_query": inputs["kb_query"], # 디버깅용: 쿼리 확인
            "code_snippet": snippet,        # 디버깅용: 스니펫이 잘 들어갔는지 확인
            "outputs": outputs,
        })
        
        analyzed += 1
        
        # 중간 저장 (실패 시에도 이미 분석한 결과 보존)
        write_jsonl(args.output, rows)
    
    # 최종 결과 출력
    print(f"[OK] analyzed: {analyzed}, failed: {failed}, output={args.output}")
    
    # 실패가 1개라도 있으면 exit code 2로 파이프라인 실패 처리
    if failed > 0:
        sys.exit(2)

if __name__ == "__main__":
    main()