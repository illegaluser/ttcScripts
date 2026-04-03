#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# ==================================================================================
# 파일명: dify_sonar_issue_analyzer.py
# 버전: 1.2
#
# [시스템 개요]
# 이 스크립트는 품질 분석 파이프라인(Phase 3)의 **2단계(AI 기반 자동 진단)**를 담당합니다.
# sonar_issue_exporter.py가 추출한 정적 분석 이슈 JSON 파일을 입력으로 받아,
# 각 이슈를 Dify AI 워크플로우(Workflow)에 전송하고, LLM이 생성한
# 원인 분석/위험 평가/수정 방안을 JSONL 형식으로 저장합니다.
#
# [파이프라인 내 위치]
# sonar_issue_exporter.py (이슈 수집)
#       ↓ sonar_issues.json
# >>> dify_sonar_issue_analyzer.py (AI 분석) <<<
#       ↓ llm_analysis.jsonl
# gitlab_issue_creator.py (이슈 등록)
#
# [핵심 동작 흐름]
# 1. sonar_issues.json에서 이슈 목록을 읽어들입니다.
# 2. 각 이슈에 대해 코드 스니펫, 룰 설명, 메타데이터를 조합하여 Dify Workflow 입력을 구성합니다.
# 3. Dify /v1/workflows/run API를 blocking 모드로 호출하여 LLM 분석 결과를 받습니다.
# 4. 실패 시 최대 3회 재시도하며, 성공한 결과를 JSONL 파일에 한 줄씩 기록합니다.
#
# [실행 예시]
# python3 dify_sonar_issue_analyzer.py \
#   --dify-api-base http://api:5001 \
#   --dify-api-key app-xxxxxxxx \
#   --input sonar_issues.json \
#   --output llm_analysis.jsonl
# ==================================================================================

import argparse
import json
import sys
import time
import uuid
import re
from urllib.request import Request, urlopen
from urllib.error import HTTPError


def truncate_text(text, max_chars=1000):
    """
    텍스트를 지정된 최대 문자 수로 잘라냅니다.

    Dify 워크플로우에 전송하는 룰 설명(description)이 너무 길면
    코드 스니펫이 컨텍스트 윈도우에서 밀려나 LLM이 코드를 참조하지 못하게 됩니다.
    이를 방지하기 위해 룰 설명에만 길이 제한을 적용합니다.

    참고: 이전에 존재하던 HTML 정제 함수는 데이터 손실을 유발하여 삭제되었습니다.

    Args:
        text: 원본 텍스트
        max_chars: 최대 허용 문자 수 (기본 1000자)

    Returns:
        잘린 텍스트 (초과 시 "... (Rule Truncated)" 접미사 추가)
    """
    if not text: return ""
    if len(text) <= max_chars: return text
    return text[:max_chars] + "... (Rule Truncated)"

def send_dify_request(url, api_key, payload):
    """
    Dify Workflow API에 HTTP POST 요청을 전송합니다.

    Jenkins 컨테이너 내부에서 Dify API 컨테이너로 직접 통신하며,
    타임아웃은 5분(300초)으로 설정합니다.
    LLM 추론은 오래 걸릴 수 있으므로 넉넉한 타임아웃이 필요합니다.

    Args:
        url: Dify Workflow 실행 엔드포인트 (예: http://api:5001/v1/workflows/run)
        api_key: Dify 앱 API 키 (Bearer 토큰)
        payload: 워크플로우 입력 데이터 (dict)

    Returns:
        tuple: (HTTP 상태 코드, 응답 본문 문자열)
               네트워크 오류 시 상태 코드 0과 에러 메시지를 반환합니다.
    """
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
    """
    메인 실행 함수: CLI 인자를 파싱하고, SonarQube 이슈를 순회하며 Dify 워크플로우로 분석을 요청합니다.

    [전체 처리 흐름]
    1. CLI 인자 파싱 (Dify 접속 정보, 입출력 파일 경로 등)
    2. sonar_issues.json 파일에서 이슈 목록 로드
    3. 각 이슈에 대해:
       a. 코드 스니펫, 룰 정보, 메타데이터를 추출하여 Dify 입력 포맷으로 가공
       b. Dify Workflow API 호출 (blocking 모드, 최대 3회 재시도)
       c. 성공 시 분석 결과를 JSONL 파일에 기록
    4. 결과 파일 닫기 (llm_analysis.jsonl)
    """
    # ---------------------------------------------------------------
    # [1단계] CLI 인자 파싱
    # ---------------------------------------------------------------
    parser = argparse.ArgumentParser()
    parser.add_argument("--dify-api-base", required=True)   # Dify API 베이스 URL
    parser.add_argument("--dify-api-key", required=True)     # Dify 앱 API 키
    parser.add_argument("--input", required=True)            # sonar_issues.json 경로
    parser.add_argument("--output", default="llm_analysis.jsonl")  # 분석 결과 출력 파일
    parser.add_argument("--max-issues", type=int, default=0) # 분석할 최대 이슈 수 (0=전체)
    parser.add_argument("--user", default="")                # Dify 사용자 식별자
    parser.add_argument("--response-mode", default="")       # 응답 모드 (미사용, 하위 호환)
    parser.add_argument("--timeout", type=int, default=0)    # 타임아웃 (미사용, 하위 호환)
    parser.add_argument("--print-first-errors", type=int, default=0)  # 에러 출력 수 제한
    args, _ = parser.parse_known_args()

    # ---------------------------------------------------------------
    # [2단계] 입력 파일(sonar_issues.json) 로드
    # sonar_issue_exporter.py가 생성한 이슈 목록을 읽어들입니다.
    # ---------------------------------------------------------------
    try:
        with open(args.input, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print(f"[ERROR] Cannot read input file: {e}", file=sys.stderr)
        sys.exit(1)

    # 이슈 목록 추출 및 개수 제한 적용
    issues = data.get("issues", [])
    if args.max_issues > 0: issues = issues[:args.max_issues]

    # 결과를 기록할 JSONL 파일 열기
    out_fp = open(args.output, "w", encoding="utf-8")

    # Dify API 엔드포인트 구성
    # 사용자가 /v1 접미사를 빠뜨려도 자동으로 보정합니다.
    base_url = args.dify_api_base.rstrip("/")
    if not base_url.endswith("/v1"):
        base_url += "/v1"
    target_api_url = f"{base_url}/workflows/run"

    print(f"[INFO] Analyzing {len(issues)} issues...", file=sys.stderr)

    # ---------------------------------------------------------------
    # [3단계] 각 이슈를 순회하며 Dify 워크플로우에 분석 요청
    # ---------------------------------------------------------------
    for item in issues:
        # --- 3-a. 이슈 메타데이터 추출 ---
        key = item.get("sonar_issue_key")           # SonarQube 이슈 고유 키
        rule = item.get("sonar_rule_key", "")       # 위반 규칙 ID (예: java:S1192)
        project = item.get("sonar_project_key", "") # SonarQube 프로젝트 키

        # issue_search_item: SonarQube /api/issues/search 원본 응답 항목
        issue_item = item.get("issue_search_item", {})
        msg = issue_item.get("message", "")          # 이슈 설명 메시지
        severity = issue_item.get("severity", "")    # 심각도 (BLOCKER, CRITICAL 등)
        component = item.get("component", "")        # 파일 경로 (프로젝트키:src/...)
        line = issue_item.get("line") or issue_item.get("textRange", {}).get("startLine", 0)

        # --- 3-b. 코드 스니펫 추출 ---
        # 여러 키 이름을 시도하여 코드를 확보합니다.
        # sonar_issue_exporter.py는 code_snippet 키에 저장하지만,
        # 다른 소스에서 온 데이터도 호환 지원합니다.
        raw_code = item.get("code_snippet", "")
        if not raw_code:
            raw_code = item.get("source", "") or item.get("code", "")

        # HTML 정제 등의 가공 없이 원본 코드를 그대로 사용합니다.
        # 이전 버전에서 HTML 태그 정제가 코드 내용을 훼손한 사례가 있었기 때문입니다.
        final_code = raw_code if raw_code else "(NO CODE CONTENT)"

        # --- 3-c. 룰 정보 가공 ---
        # 룰 설명은 길이만 제한하되 내용은 그대로 유지합니다.
        rule_detail = item.get("rule_detail", {})
        raw_desc = rule_detail.get("description", "")
        safe_desc = truncate_text(raw_desc, max_chars=800)

        # Dify 워크플로우의 Jinja2 템플릿에서 중괄호({})를 변수 구분자로 사용하므로,
        # 설명 텍스트 내의 중괄호를 소괄호로 치환하여 파싱 에러를 방지합니다.
        safe_rule_json = json.dumps({
            "key": rule_detail.get("key"),
            "name": rule_detail.get("name"),
            "description": safe_desc.replace("{", "(").replace("}", ")")
        }, ensure_ascii=False)

        # 이슈 메타데이터를 JSON 문자열로 직렬화하여 Dify 입력에 포함합니다.
        safe_issue_json = json.dumps({
            "key": key, "rule": rule, "message": msg, "severity": severity,
            "project": project, "component": component, "line": line
        }, ensure_ascii=False)

        # 각 이슈 요청마다 고유한 사용자 ID를 생성합니다.
        # Dify가 세션을 분리하여 이전 대화의 영향을 받지 않도록 합니다.
        session_user = f"jenkins-{uuid.uuid4()}"

        print(f"\n[DEBUG] >>> Sending Issue {key}")

        # --- 3-d. Dify 워크플로우 입력 데이터 구성 ---
        # kb_query: Dify Knowledge Base 검색용 쿼리 (룰 ID + 이슈 메시지)
        # 이를 통해 LLM이 지식 베이스에서 관련 정보를 RAG로 검색할 수 있습니다.
        inputs = {
            "sonar_issue_key": key,
            "sonar_project_key": project,
            "code_snippet": final_code,
            "sonar_issue_url": item.get("sonar_issue_url", ""),
            "kb_query": f"{rule} {msg}",
            "sonar_issue_json": safe_issue_json,
            "sonar_rule_json": safe_rule_json
        }

        # 디버깅용: 실제로 전송되는 코드 내용을 확인합니다.
        print(f"   [DATA CHECK] Code Length: {len(final_code)}")
        print(f"   [DATA CHECK] Preview: {final_code[:100].replace(chr(10), ' ')}...")

        # Dify Workflow 실행 페이로드
        # response_mode="blocking": 워크플로우 완료까지 대기 후 결과 반환
        payload = {
            "inputs": inputs,
            "response_mode": "blocking",
            "user": session_user
        }

        # --- 3-e. API 호출 및 재시도 로직 ---
        # 최대 3회 시도하며, 실패 시 2초 대기 후 재시도합니다.
        # LLM 추론 과부하나 일시적 네트워크 문제에 대한 내결함성을 확보합니다.
        success = False
        for i in range(3):
            status, body = send_dify_request(target_api_url, args.dify_api_key, payload)

            if status == 200:
                try:
                    res = json.loads(body)
                    # Dify 워크플로우 내부 실행이 성공했는지 확인합니다.
                    if res.get("data", {}).get("status") == "succeeded":
                        # 분석 결과를 JSONL 형식으로 기록합니다.
                        # outputs에는 LLM이 생성한 title, description_markdown, labels 등이 포함됩니다.
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
                        # HTTP 200이지만 워크플로우 내부에서 실패한 경우
                        print(f"   -> Dify Internal Fail: {res}", file=sys.stderr)
                except: pass

            print(f"   -> Retry {i+1}/3 due to Status {status} | Error: {body}")
            time.sleep(2)

        if not success:
            print(f"[FAIL] Failed to analyze {key}", file=sys.stderr)

    # ---------------------------------------------------------------
    # [4단계] 결과 파일 닫기
    # ---------------------------------------------------------------
    out_fp.close()

if __name__ == "__main__":
    main()