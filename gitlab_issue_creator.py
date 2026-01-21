#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# 목적: readme.md Stage 4(Quality-Issue-Workflow)에서 LLM 분석 결과(llm_analysis.jsonl)를 GitLab 이슈로 생성한다.
# 원칙:
# - llm_analysis.jsonl을 읽어 GitLab Issue를 생성한다.
# - Dedup(중복 방지): 기본은 sonar_issue_key 검색으로 이미 등록된 이슈가 있으면 skip한다.
# - Sonar 링크 클릭 문제 해결: description_markdown 내 URL을 등록 직전에 치환한다.
# - Jenkins 파이프라인(Job #5)에서 LLM 분석 결과를 실제 GitLab 이슈로 옮기는 단계에 사용된다.
# - 앞 단계(dify_sonar_issue_analyzer.py)가 만든 분석 결과를 토대로 실제 작업 티켓을 생성해, 개발자가 바로 확인하고 처리할 수 있게 한다.
# - 내부 주소 치환과 중복 검사로, 클릭 안 되는 링크나 이슈 중복 등록 같은 실수를 줄인다.
# - readme.md의 “Quality-Issue-Workflow” Stage 4에 해당하며,
#   Stage 2(sonar_issue_exporter) → Stage 3(LLM 분석) 결과를 최종적으로 GitLab에 반영하는 역할이다.
# - PAT(개인 액세스 토큰)으로 인증하며, GitLab 컨테이너 내부 주소(http://gitlab:8929)를 사용한다.
# 기대결과: gitlab_issues_created.json 요약과 함께 실제 GitLab 이슈가 생성되어, 개발자가 바로 처리할 수 있는 티켓이 확보된다.


import argparse
import json
import sys
import time
import re  # [추가] 정규표현식 사용을 위해 import
from urllib.parse import urlencode, urljoin, quote
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError


def _http_get_json(url: str, headers: dict, timeout: int = 60) -> dict:
    """
    GET 요청을 보내 JSON으로 파싱해 반환한다.
    - GitLab issues 검색 등에서 재사용한다.
    - 실패 시 예외를 그대로 던져 상위에서 처리한다.
    - UTF-8로 디코드하며, 문제가 생기면 예외가 발생해 호출자가 상황을 알 수 있다.
    - 검색 용도라 응답을 한 번에 읽어도 부담이 적다.
    - readme.md에 나온 Jenkins 파이프라인에서는 컨테이너 내부 주소로 호출한다.
      여기서는 단순히 HTTP 레이어만 담당한다.
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
    - labels 전달 시 리스트가 들어오면 doseq=True로 여러 값이 퍼질 수 있으니,
      호출부에서 콤마 문자열로 변환해 주는 것이 안전하다.
    - readme.md Stage 4 예제와 동일하게, POST로 title/description/labels를 보낸다.
    - 상태 코드가 200/201이 아니면 호출부에서 실패로 처리한다.
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
    - 예: "root/dscore-ttc-sample" -> "root%2Fdscore-ttc-sample"
    - 슬래시(/)가 포함된 그룹/프로젝트는 인코딩하지 않으면 404가 난다.
    - readme.md의 샘플 프로젝트 경로와 동일한 구조를 가정하며, safe=""로 완전 인코딩한다.
    """
    return quote(project, safe="")


def _replace_sonar_url(text: str, sonar_host_url: str, sonar_public_url: str) -> str:
    """
    SonarQube 내부 주소가 이슈 본문에 그대로 들어가면 브라우저에서 클릭이 안 되므로
    외부에서 접근 가능한 주소로 치환한다.
    
    [수정 사항]
    - 내부 주소(http://sonarqube:9000)를 외부 주소로 치환
    - 도메인이 누락된 상대 경로(/project/issues...)도 외부 주소로 자동 보정
    """
    if not text:
        return text

    # 1. 사용할 공개 주소(Public URL) 확정 (기본값 localhost:9000)
    target_base = (sonar_public_url or "http://localhost:9000").rstrip("/")

    # 2. 내부 주소(Internal URL)를 공개 주소로 치환
    # (A) 인자로 받은 호스트 치환
    if sonar_host_url:
        internal_base = sonar_host_url.rstrip("/")
        text = text.replace(internal_base, target_base)
    
    # (B) 하드코딩된 내부 주소 치환 (Docker Service Name)
    text = text.replace("http://sonarqube:9000", target_base)
    text = text.replace("https://sonarqube:9000", target_base)

    # 3. [FIX] 도메인이 누락된 상대 경로(/project/issues...) 보정
    # LLM이 도메인을 생략하고 경로만 출력하는 경우(예: "링크: /project/issues?id=...")를 처리
    # 정규식 설명: http: 또는 https: 로 시작하지 않는 "/project/issues" 문자열을 찾아 도메인을 붙임
    # (?<!...)는 부정형 후방탐색(Negative Lookbehind)
    pattern = r"(?<!http:)(?<!https:)(?<![a-zA-Z0-9])(/project/issues\?)"
    text = re.sub(pattern, f"{target_base}\\1", text)

    return text


def _find_existing_by_sonar_key(gitlab_host_url: str, headers: dict, project: str, sonar_issue_key: str) -> bool:
    """
    간단한 중복 체크. sonar_issue_key 문자열이 이미 등록된 이슈 제목/본문 등에 있으면 True.
    - GitLab Issues 검색 API를 사용한다.
    - 오류가 나면 False를 반환해 흐름을 막지 않는다.
    - 검색이 느슨해도 괜찮다: 같은 키를 가진 이슈가 발견되면 새로 만들지 않는 것이 목적이다.
    - readme.md 파이프라인에서 “중복 방지 메커니즘”의 일부로 작동한다.
    - 실패해도 전체 흐름을 막지 않고 False를 반환해 보수적으로 진행한다.
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
    - 흐름(초보자용):
      1) JSONL을 읽어 LLM이 제안한 제목/본문/라벨을 가져온다.
      2) Sonar 내부 URL을 외부에서 클릭 가능한 주소로 바꾼다.
      3) sonar_issue_key로 이미 등록된 이슈가 있으면 건너뛴다.
      4) GitLab Issue API로 새 이슈를 생성하고 결과를 기록한다.
    - 라벨 리스트를 콤마 문자열로 만들어 GitLab 호환성을 높인다.
    - 실패/생성/스킵 결과를 모두 JSON으로 남겨 사후 확인이 쉽도록 한다.
    - readme.md Stage 4(Jenkins Pipeline)에서 호출되는 CLI와 동일한 인자 구성을 유지해
      문서/스크립트 불일치를 방지한다.
    - 실패가 하나라도 있으면 종료코드 2로 반환해 파이프라인이 실패로 표시되도록 한다.
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

    # 입력 읽기
    rows = []
    with open(args.input, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    # rows에는 dify_sonar_issue_analyzer.py의 한 줄 JSONL이 그대로 들어간다.
    # (sonar_issue_key, title/description/labels 등을 포함)

    for row in rows:
        sonar_issue_key = row.get("sonar_issue_key") or ""
        outputs = row.get("outputs") or {}

        title = outputs.get("title") or ""
        desc = outputs.get("description_markdown") or ""
        labels = outputs.get("labels") or []

        if not title or not desc:
            failed.append({"sonar_issue_key": sonar_issue_key, "reason": "missing title/description"})
            continue

        # (1) URL 치환: 등록 직전에만 한다.
        # - 내부 주소로 남으면 브라우저에서 열리지 않을 수 있어 외부용 주소로 교체한다.
        # - readme.md 주소 체계(내부: sonarqube:9000 / 외부: localhost:9000)를 따른다.
        # - [FIX] LLM이 도메인을 생략한 경우도 여기서 강제로 붙여준다.
        desc = _replace_sonar_url(desc, args.sonar_host_url, args.sonar_public_url)

        # (2) Dedup: sonar_issue_key로 검색해서 있으면 skip
        # - 같은 Sonar 이슈가 두 번 등록되지 않게 방지한다.
        # - LLM이 동일 이슈를 여러 번 분석해도 중복 이슈 생성은 막는다.
        if _find_existing_by_sonar_key(gitlab_host_url, headers, project, sonar_issue_key):
            skipped.append({"sonar_issue_key": sonar_issue_key, "title": title, "reason": "dedup(search)"})
            continue

        # (3) GitLab Issue 생성
        # - labels는 콤마로 이어붙인 문자열로 전달하는 것이 호환성이 가장 높다.
        # - form 인코딩으로 전송하며, 상태 코드가 200/201이 아니면 실패로 기록한다.
        # - readme.md의 Jenkins 샘플 스크립트와 동일한 엔드포인트/방식으로 호출한다.
        proj_seg = _project_path_to_api_segment(project)
        endpoint = f"{gitlab_host_url}/api/v4/projects/{proj_seg}/issues"

        label_str = ""
        if isinstance(labels, list):
            # GitLab은 labels를 comma-separated string으로 받는 게 가장 안전하다.
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