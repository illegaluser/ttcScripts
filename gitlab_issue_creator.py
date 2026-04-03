#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# ==================================================================================
# 파일명: gitlab_issue_creator.py
# 버전: 1.1
#
# [시스템 개요]
# 이 스크립트는 품질 분석 파이프라인(Phase 3)의 **3단계(이슈 관리 시스템 자동 등록)**를 담당합니다.
# dify_sonar_issue_analyzer.py가 생성한 LLM 분석 결과(JSONL)를 읽어,
# 각 이슈를 GitLab 프로젝트의 이슈 트래커에 자동으로 생성합니다.
#
# [파이프라인 내 위치]
# dify_sonar_issue_analyzer.py (AI 분석)
#       ↓ llm_analysis.jsonl
# >>> gitlab_issue_creator.py (이슈 등록) <<<
#       ↓ gitlab_issues_created.json (등록 결과 요약)
#
# [핵심 기능]
# 1. JSONL 파일에서 분석 결과를 한 줄씩 읽어 GitLab 이슈로 변환합니다.
# 2. 이슈 제목은 "[심각도] 이슈메시지" 포맷으로 통일합니다.
# 3. SonarQube 내부 URL을 외부 접근 가능한 URL로 치환합니다.
# 4. 동일 SonarQube 이슈 키로 이미 등록된 이슈가 있으면 중복 생성을 방지합니다.
# 5. 생성/건너뜀/실패 결과를 JSON 파일로 저장하여 파이프라인 추적을 지원합니다.
#
# [실행 예시]
# python3 gitlab_issue_creator.py \
#   --gitlab-host-url http://gitlab:8929 \
#   --gitlab-token glpat-xxxxx \
#   --gitlab-project mygroup/myproject \
#   --input llm_analysis.jsonl \
#   --sonar-public-url http://localhost:9000
# ==================================================================================

import argparse
import json
import sys
import time
import re
from urllib.parse import urlencode, urljoin, quote
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError


def _http_post_form(url: str, headers: dict, form: dict, timeout: int = 60):
    """
    URL-encoded 폼 데이터를 POST로 전송합니다.

    GitLab Issues API는 JSON 대신 application/x-www-form-urlencoded을
    사용하는 것이 안정적이므로, 폼 인코딩 방식으로 전송합니다.

    Args:
        url: GitLab API 엔드포인트
        headers: PRIVATE-TOKEN이 포함된 인증 헤더
        form: 전송할 폼 데이터 (title, description, labels 등)
        timeout: 요청 타임아웃 (초)

    Returns:
        tuple: (HTTP 상태 코드, 응답 본문 문자열)
    """
    data = urlencode(form, doseq=True).encode("utf-8")
    h = dict(headers or {})
    h["Content-Type"] = "application/x-www-form-urlencoded"
    req = Request(url, headers=h, method="POST", data=data)
    with urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8", errors="replace")
        return resp.status, body


def _http_get_json(url: str, headers: dict, timeout: int = 60) -> dict:
    """
    HTTP GET 요청을 보내고 JSON 응답을 파싱하여 반환합니다.

    GitLab Issues 검색 API 호출에 사용됩니다 (중복 이슈 확인 등).
    """
    req = Request(url, headers=headers, method="GET")
    with urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def _replace_sonar_url(text: str, sonar_host_url: str, sonar_public_url: str) -> str:
    """
    LLM이 생성한 마크다운 설명 내의 SonarQube URL을 외부 접근 가능한 URL로 치환합니다.

    Jenkins 컨테이너 안에서는 SonarQube를 'http://sonarqube:9000'(Docker 내부 호스트명)으로
    접근하지만, GitLab 이슈를 읽는 사용자는 'http://localhost:9000' 등 외부 URL을 사용합니다.
    이 함수는 세 가지 케이스를 모두 처리합니다:
    1. sonar_host_url 파라미터로 전달된 내부 URL 치환
    2. 하드코딩된 'http://sonarqube:9000' 치환
    3. 호스트명 없이 상대경로로 시작하는 '/project/issues?' 패턴에 호스트 추가

    Args:
        text: LLM이 생성한 마크다운 텍스트 (이슈 설명)
        sonar_host_url: Jenkins에서 사용하는 SonarQube 내부 URL
        sonar_public_url: 사용자가 접근 가능한 SonarQube 외부 URL

    Returns:
        URL이 치환된 텍스트
    """
    if not text: return text
    target_base = (sonar_public_url or "http://localhost:9000").rstrip("/")
    if sonar_host_url:
        text = text.replace(sonar_host_url.rstrip("/"), target_base)
    text = text.replace("http://sonarqube:9000", target_base)
    # 상대경로 형태의 SonarQube 링크에 호스트를 붙여줍니다.
    # lookbehind로 이미 http: 또는 https:가 앞에 있는 경우는 제외합니다.
    pattern = r"(?<!http:)(?<!https:)(?<![a-zA-Z0-9])(/project/issues\?)"
    text = re.sub(pattern, f"{target_base}\\1", text)
    return text


def _find_existing_by_sonar_key(gitlab_host_url: str, headers: dict, project: str, key: str) -> bool:
    """
    GitLab에서 동일한 SonarQube 이슈 키로 이미 등록된 이슈가 있는지 검색합니다.

    중복 이슈 생성을 방지하기 위한 핵심 함수입니다.
    파이프라인을 반복 실행해도 같은 이슈가 여러 번 등록되지 않습니다.

    Args:
        gitlab_host_url: GitLab 호스트 URL
        headers: PRIVATE-TOKEN 인증 헤더
        project: GitLab 프로젝트 경로 (예: "mygroup/myproject")
        key: SonarQube 이슈 고유 키

    Returns:
        bool: 기존 이슈가 존재하면 True, 없으면 False
    """
    if not key: return False
    url = f"{gitlab_host_url.rstrip('/')}/api/v4/projects/{quote(project, safe='')}/issues?search={key}"
    try:
        arr = _http_get_json(url, headers)
        return isinstance(arr, list) and len(arr) > 0
    except Exception:
        return False

def main() -> int:
    """
    메인 실행 함수: LLM 분석 결과를 읽어 GitLab 이슈를 자동 생성합니다.

    [전체 처리 흐름]
    1. CLI 인자 파싱 (GitLab 접속 정보, SonarQube URL 매핑 등)
    2. llm_analysis.jsonl에서 분석 결과를 한 줄씩 로드
    3. 각 분석 결과에 대해:
       a. 이슈 제목 구성: "[심각도] SonarQube메시지" 포맷 (메시지가 없으면 LLM 제목 사용)
       b. 설명 내 SonarQube URL을 외부 접근 가능 URL로 치환
       c. GitLab에서 동일 이슈 키로 중복 검색 → 이미 있으면 건너뜀
       d. GitLab Issues API로 이슈 생성 (LLM이 제안한 labels 포함)
    4. 생성/건너뜀/실패 결과를 JSON 파일로 저장

    Returns:
        int: 실패한 이슈가 있으면 2, 없으면 0 (Jenkins 빌드 상태에 반영)
    """
    # ---------------------------------------------------------------
    # [1단계] CLI 인자 파싱
    # ---------------------------------------------------------------
    ap = argparse.ArgumentParser()
    ap.add_argument("--gitlab-host-url", required=True)   # GitLab 호스트 URL
    ap.add_argument("--gitlab-token", required=True)       # GitLab Personal Access Token
    ap.add_argument("--gitlab-project", required=True)     # 대상 프로젝트 경로
    ap.add_argument("--input", default="llm_analysis.jsonl")     # 입력 파일 (LLM 분석 결과)
    ap.add_argument("--output", default="gitlab_issues_created.json")  # 결과 요약 파일
    ap.add_argument("--sonar-host-url", default="")        # SonarQube 내부 URL (치환 원본)
    ap.add_argument("--sonar-public-url", default="")      # SonarQube 외부 URL (치환 대상)
    ap.add_argument("--timeout", type=int, default=60)     # API 요청 타임아웃 (초)
    args = ap.parse_args()

    # GitLab API 인증 헤더
    headers = {"PRIVATE-TOKEN": args.gitlab_token}

    # 처리 결과를 세 가지 카테고리로 분류합니다.
    created, skipped, failed = [], [], []
    rows = []

    # ---------------------------------------------------------------
    # [2단계] JSONL 입력 파일 로드
    # 각 줄이 하나의 JSON 객체이므로 줄 단위로 파싱합니다.
    # ---------------------------------------------------------------
    try:
        with open(args.input, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip(): rows.append(json.loads(line))
    except Exception as e:
        print(f"[ERROR] Read failed: {e}", file=sys.stderr)
        return 2

    # ---------------------------------------------------------------
    # [3단계] 각 분석 결과를 순회하며 GitLab 이슈 생성
    # ---------------------------------------------------------------
    for row in rows:
        sonar_key = row.get("sonar_issue_key")
        outputs = row.get("outputs") or {}  # Dify 워크플로우가 생성한 LLM 출력

        # --- 3-a. 이슈 제목 결정 ---
        # SonarQube 원본 메시지를 최우선으로 사용합니다.
        # SonarQube 메시지가 없을 경우에만 LLM이 생성한 제목을 사용합니다.
        # 이유: 원본 메시지가 가장 정확하고, 개발자에게 익숙한 표현이기 때문입니다.
        msg = row.get("sonar_message") or ""
        llm_title = outputs.get("title") or ""
        main_title = msg if msg else llm_title

        # 심각도 태그를 제목 앞에 붙여 시각적으로 우선순위를 구분합니다.
        severity = row.get("severity") or ""
        final_title = f"[{severity}] {main_title}" if severity else main_title

        # --- 3-b. 이슈 설명(description) 가공 ---
        # LLM이 마크다운 형식으로 생성한 상세 설명을 가져옵니다.
        desc = outputs.get("description_markdown") or ""
        # 내부 SonarQube URL을 외부 접근 가능 URL로 치환합니다.
        desc = _replace_sonar_url(desc, args.sonar_host_url, args.sonar_public_url)

        # 제목이나 설명이 비어있으면 유효한 이슈를 생성할 수 없으므로 실패 처리합니다.
        if not final_title or not desc:
            failed.append({"key": sonar_key, "reason": "Empty title/desc"})
            continue

        # --- 3-c. 중복 이슈 검사 ---
        # SonarQube 이슈 키로 GitLab에서 기존 이슈를 검색합니다.
        # 파이프라인 재실행 시 동일 이슈가 중복 생성되는 것을 방지합니다.
        if _find_existing_by_sonar_key(args.gitlab_host_url, headers, args.gitlab_project, sonar_key):
            skipped.append({"key": sonar_key, "title": final_title, "reason": "Dedup"})
            continue

        # --- 3-d. GitLab 이슈 생성 ---
        form = {"title": final_title, "description": desc}
        # LLM이 제안한 라벨이 있으면 이슈에 태깅합니다 (예: bug, security, code-smell).
        labels = outputs.get("labels")
        if labels:
            form["labels"] = ",".join(labels) if isinstance(labels, list) else str(labels)

        url = f"{args.gitlab_host_url.rstrip('/')}/api/v4/projects/{quote(args.gitlab_project, safe='')}/issues"
        try:
            status, body = _http_post_form(url, headers, form, args.timeout)
            if status in (200, 201):
                created.append({"key": sonar_key, "title": final_title})
            else:
                failed.append({"key": sonar_key, "status": status, "body": body})
        except Exception as e:
            failed.append({"key": sonar_key, "err": str(e)})

    # ---------------------------------------------------------------
    # [4단계] 결과 요약 파일 저장
    # Jenkins 콘솔과 아티팩트에서 처리 현황을 확인할 수 있습니다.
    # ---------------------------------------------------------------
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump({"created": created, "skipped": skipped, "failed": failed}, f, ensure_ascii=False, indent=2)

    print(f"[OK] created={len(created)} skipped={len(skipped)} failed={len(failed)} output={args.output}")
    # 실패 건이 있으면 종료 코드 2를 반환하여 Jenkins 빌드를 UNSTABLE/FAILURE로 표시합니다.
    return 2 if failed else 0

if __name__ == "__main__":
    sys.exit(main())