#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# 목적: readme.md Stage 4(Quality-Issue-Workflow)에서 LLM 분석 결과(llm_analysis.jsonl)를 GitLab 이슈로 생성한다.
# 원칙:
# - llm_analysis.jsonl을 읽어 GitLab Issue를 생성한다.
# - Dedup(중복 방지): 기본은 sonar_issue_key 검색으로 이미 등록된 이슈가 있으면 skip한다.
# - Sonar 링크 클릭 문제 해결: description_markdown 내 URL을 등록 직전에 치환한다.
# - [수정] 이슈 제목에 [Severity] 태그를 붙여 중요도를 즉시 파악할 수 있게 한다.
# - Jenkins 파이프라인(Job #5)에서 LLM 분석 결과를 실제 GitLab 이슈로 옮기는 단계에 사용된다.
# 기대결과: gitlab_issues_created.json 요약과 함께 실제 GitLab 이슈가 생성되어, 개발자가 바로 처리할 수 있는 티켓이 확보된다.

import argparse
import json
import sys
import time
import re
from urllib.parse import urlencode, urljoin, quote
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError


def _http_get_json(url: str, headers: dict, timeout: int = 60) -> dict:
    """
    GET 요청을 보내 JSON으로 파싱해 반환한다.
    - GitLab issues 검색 등에서 재사용한다.
    - 실패 시 예외를 그대로 던져 상위에서 처리한다.
    """
    req = Request(url, headers=headers, method="GET")
    with urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8", errors="replace")
        return json.loads(body)


def _http_post_form(url: str, headers: dict, form: dict, timeout: int = 60):
    """
    x-www-form-urlencoded POST 요청을 보낸다.
    - GitLab Issue 생성 API가 폼 인코딩을 기대하므로 이 형식을 사용한다.
    - 상태 코드와 응답 본문을 호출자에게 돌려준다.
    """
    data = urlencode(form, doseq=True).encode("utf-8")
    h = dict(headers or {})
    h["Content-Type"] = "application/x-www-form-urlencoded"
    h["Accept"] = "application/json"
    req = Request(url, headers=h, method="POST", data=data)
    with urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8", errors="replace")
        return resp.status, body


def _project_path_to_api_segment(project: str) -> str:
    """
    GitLab 프로젝트 경로를 API 경로에 쓸 수 있게 URL 인코딩한다.
    """
    return quote(project, safe="")


def _replace_sonar_url(text: str, sonar_host_url: str, sonar_public_url: str) -> str:
    """
    SonarQube 내부 주소가 이슈 본문에 그대로 들어가면 브라우저에서 클릭이 안 되므로
    외부에서 접근 가능한 주소로 치환한다.
    또한, 도메인이 누락된 상대 경로(/project/issues...)도 외부 주소를 붙여 보정한다.
    """
    if not text:
        return text

    # 1. 사용할 공개 주소(Public URL) 확정 (기본값 localhost:9000)
    target_base = (sonar_public_url or "http://localhost:9000").rstrip("/")

    # 2. 내부 주소(Internal URL)를 공개 주소로 치환
    if sonar_host_url:
        internal_base = sonar_host_url.rstrip("/")
        text = text.replace(internal_base, target_base)
    
    # 하드코딩된 내부 주소 치환 (Docker Service Name)
    text = text.replace("http://sonarqube:9000", target_base)
    text = text.replace("https://sonarqube:9000", target_base)

    # 3. 도메인이 누락된 상대 경로(/project/issues...) 보정
    # http: 또는 https: 로 시작하지 않는 "/project/issues" 문자열을 찾아 도메인을 붙임
    pattern = r"(?<!http:)(?<!https:)(?<![a-zA-Z0-9])(/project/issues\?)"
    text = re.sub(pattern, f"{target_base}\\1", text)

    return text


def _find_existing_by_sonar_key(gitlab_host_url: str, headers: dict, project: str, sonar_issue_key: str) -> bool:
    """
    간단한 중복 체크. sonar_issue_key 문자열이 이미 등록된 이슈 제목/본문 등에 있으면 True.
    """
    if not sonar_issue_key:
        return False

    proj_seg = _project_path_to_api_segment(project)
    base = gitlab_host_url.rstrip("/") + "/"
    params = {"search": sonar_issue_key}
    url = urljoin(base, f"api/v4/projects/{proj_seg}/issues") + "?" + urlencode(params)
    try:
        arr = _http_get_json(url, headers=headers, timeout=60)
        if isinstance(arr, list) and len(arr) > 0:
            return True
    except Exception:
        return False
    return False


def main() -> int:
    """
    CLI 엔트리포인트.
    - 입력: llm_analysis.jsonl (LLM 분석 결과)
    - 출력: gitlab_issues_created.json (생성/건너뜀/실패 요약)
    """
    ap = argparse.ArgumentParser()
    ap.add_argument("--gitlab-host-url", required=True, help="ex) http://gitlab:8929")
    ap.add_argument("--gitlab-token", required=True, help="GitLab PAT")
    ap.add_argument("--gitlab-project", required=True, help="ex) root/dscore-ttc-sample")
    ap.add_argument("--input", default="llm_analysis.jsonl", help="input jsonl")
    ap.add_argument("--output", default="gitlab_issues_created.json", help="output json")
    ap.add_argument("--sonar-host-url", default="", help="optional (URL replace용)")
    ap.add_argument("--sonar-public-url", default="", help="optional (URL replace용)")
    ap.add_argument("--timeout", type=int, default=60, help="HTTP timeout")
    args = ap.parse_args()

    gitlab_host_url = args.gitlab_host_url.rstrip("/")
    project = args.gitlab_project

    headers = {
        "PRIVATE-TOKEN": args.gitlab_token,
        "Accept": "application/json",
    }

    created = []
    skipped = []
    failed = []

    rows = []
    try:
        with open(args.input, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                rows.append(json.loads(line))
    except Exception as e:
        print(f"[ERROR] Failed to read input file {args.input}: {e}", file=sys.stderr)
        return 2

    for row in rows:
        sonar_issue_key = row.get("sonar_issue_key") or ""
        outputs = row.get("outputs") or {}

        title = outputs.get("title") or ""
        desc = outputs.get("description_markdown") or ""
        labels = outputs.get("labels") or []

        # [추가] 심각도(Severity) 정보가 있으면 제목 앞에 붙이기
        # 예: [CRITICAL] 기존 타이틀
        severity = row.get("severity") or ""
        if severity and title:
            title = f"[{severity}] {title}"

        if not title or not desc:
            failed.append({"sonar_issue_key": sonar_issue_key, "reason": "missing title/description"})
            continue

        # URL 치환: 등록 직전에 외부에서 클릭 가능한 주소로 교체
        desc = _replace_sonar_url(desc, args.sonar_host_url, args.sonar_public_url)

        # Dedup: sonar_issue_key로 검색해서 있으면 skip
        if _find_existing_by_sonar_key(gitlab_host_url, headers, project, sonar_issue_key):
            skipped.append({"sonar_issue_key": sonar_issue_key, "title": title, "reason": "dedup(search)"})
            continue

        # GitLab Issue 생성
        proj_seg = _project_path_to_api_segment(project)
        endpoint = f"{gitlab_host_url}/api/v4/projects/{proj_seg}/issues"

        label_str = ""
        if isinstance(labels, list):
            label_str = ",".join([str(x).strip() for x in labels if str(x).strip()])
        else:
            label_str = str(labels).strip()

        form = {
            "title": title,
            "description": desc,
        }
        if label_str:
            form["labels"] = label_str

        try:
            status, body = _http_post_form(endpoint, headers=headers, form=form, timeout=args.timeout)
            if status not in (200, 201):
                failed.append({"sonar_issue_key": sonar_issue_key, "title": title, "http": status, "body": body})
                continue
            created.append({"sonar_issue_key": sonar_issue_key, "title": title, "response": json.loads(body)})
        except Exception as e:
            failed.append({"sonar_issue_key": sonar_issue_key, "title": title, "err": str(e)})

    out = {
        "generated_at": int(time.time()),
        "created": created,
        "skipped": skipped,
        "failed": failed,
        "counts": {"created": len(created), "skipped": len(skipped), "failed": len(failed)},
    }

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    print(f"[OK] created={len(created)} skipped={len(skipped)} failed={len(failed)} output={args.output}")

    if failed:
        return 2
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (HTTPError, URLError) as e:
        print(f"[ERROR] network/http error: {e}", file=sys.stderr)
        sys.exit(2)
    except Exception as e:
        print(f"[ERROR] unexpected: {e}", file=sys.stderr)
        sys.exit(2)