#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
sonar_issue_exporter.py

목적.
- SonarQube 특정 프로젝트에서 지정 severities/statuses 이슈를 수집한다.
- 이슈 1건당 가능한 한 많은 원본 정보를 함께 저장한다.
- code_snippet을 가능한 범위에서 채운다(없으면 빈 문자열).
- Jenkins 파이프라인(Job #5: DSCORE-Quality-Issue-Workflow) 1단계에서 사용하며,
  생성된 sonar_issues.json이 이후 LLM(Dify Workflow) 입력으로 전달되어
  정적 분석 결과를 해석하고 해결 방안을 제시할 때 사전 컨텍스트로 활용된다.
 - readme.md Stage 2 “Export SonarQube Issues” 스크립트이며,
   Stage 3(LLM 분석)·Stage 4(GitLab 이슈 생성)으로 이어지는 출발점이다.
 - API 호출 전용 URL(--sonar-host-url)과 UI 링크용 URL(--sonar-public-url)을
   명확히 분리해 내부/외부 주소 혼동으로 인한 실패를 막는다.

중요 원칙.
- API 호출은 --sonar-host-url 만 사용한다. (절대 /sonarqube 같은 prefix를 임의로 붙이지 않는다)
- --sonar-public-url 은 UI 링크(sonar_issue_url) 생성에만 사용한다. API 호출에 절대 섞지 않는다.

출력.
- sonar_issues.json
"""

import argparse
import base64
import json
import sys
import time
from urllib.parse import urlencode, urljoin
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError


def _http_get_json(url: str, headers: dict, timeout: int = 60) -> dict:
    """
    HTTP GET 후 JSON 디코딩. 실패 시 예외를 그대로 던져 상위에서 처리한다.
    - urlopen은 context manager로 열어 응답 본문을 읽고 UTF-8로 디코드한다.
    - Sonar API 호출에 공통 사용된다.
    - readme.md의 Jenkins 스크립트에서 호출하는 모든 Sonar API가 이 헬퍼를 거친다.
    """
    req = Request(url, headers=headers, method="GET")
    with urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8", errors="replace")
        return json.loads(body)


def _safe_get_json(url: str, headers: dict, timeout: int = 60) -> dict:
    """
    JSON 요청의 안전 래퍼. 오류를 삼키고 에러 메시지와 URL을 포함한 dict를 반환한다.
    - 일부 보강 호출(rule/show, issue_snippets 등) 실패가 전체 흐름을 막지 않도록 한다.
    - Sonar API 버전 차이나 일시적 네트워크 오류가 있어도 수집을 이어가기 위함이다.
    """
    try:
        return _http_get_json(url, headers=headers, timeout=timeout)
    except Exception as e:
        return {"_error": str(e), "_url": url}


def _http_get_text(url: str, headers: dict, timeout: int = 60) -> str:
    """
    HTTP GET 후 텍스트 그대로 반환. JSON 파싱이 필요 없는 raw API(sources/raw)용.
    - 코드 스니펫 fallback에 사용한다.
    """
    req = Request(url, headers=headers, method="GET")
    with urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _safe_get_text(url: str, headers: dict, timeout: int = 60) -> str:
    """
    텍스트 요청의 안전 래퍼. 실패 시 빈 문자열을 반환해 스니펫 생성이 중단되지 않게 한다.
    """
    try:
        return _http_get_text(url, headers=headers, timeout=timeout)
    except Exception:
        return ""


def _build_basic_auth(token: str) -> str:
    """
    SonarQube 토큰을 Basic Auth 헤더 문자열로 만든다. (username=token, password 공백)
    - Jenkins Credential에 저장된 token을 가져와 바로 넣는 형태(readme.md Stage 2와 동일).
    """
    raw = f"{token}:".encode("utf-8")
    b64 = base64.b64encode(raw).decode("ascii")
    return f"Basic {b64}"


def _api_url(sonar_host_url: str, api_path: str, params: dict) -> str:
    """
    API 호출용 URL을 생성한다.
    - sonar_host_url은 API 베이스 (컨테이너 내부 접근용)
    - api_path는 /api/issues/search 같은 상대 경로
    - params는 쿼리스트링으로 직렬화한다.
    - 예: _api_url("http://sonarqube:9000", "/api/issues/search", {"p":1})
    - readme.md의 Jenkins 예시처럼 /sonarqube prefix를 임의로 붙이지 않도록 주의한다.
    """
    base = sonar_host_url.rstrip("/") + "/"
    qs = urlencode(params, doseq=True)
    return urljoin(base, api_path.lstrip("/")) + ("?" + qs if qs else "")


def _ui_issue_url(sonar_ui_base: str, project_key: str, issue_key: str) -> str:
    """
    브라우저에서 바로 열 수 있는 이슈 UI 링크를 만든다.
    - API URL과 분리된 공개 URL(프록시/도메인 고려)을 사용한다.
    - Jenkins 로그에서 클릭하면 SonarQube 화면으로 이동하도록 구성한다.
    - readme.md의 “공개 URL” 설정을 따라 내부/외부 주소를 분리해둔다.
    """
    base = sonar_ui_base.rstrip("/") + "/"
    return f"{base}project/issues?id={project_key}&issues={issue_key}&open={issue_key}"


def _extract_snippet_from_issue_snippets(snippet_json: dict) -> str:
    """
    /api/sources/issue_snippets 응답에서 코드 블록을 추출한다.
    - SonarQube 버전에 따라 구조가 다를 수 있으므로 존재 여부를 보수적으로 검사한다.
    - line/text가 있으면 "  42 | code" 형식으로 가공한다.
    - 실패 시 빈 문자열을 반환해 raw fallback으로 넘어가게 한다.
    - 이 단계가 실패해도 프로그램은 계속 진행한다(스니펫 없이도 LLM 분석은 가능).
    - readme.md Stage 2에서 “가능한 코드 스니펫 확보”를 위해 우선 시도하는 방법이다.
    """
    if not isinstance(snippet_json, dict):
        return ""
    sources = snippet_json.get("sources")
    if not isinstance(sources, list) or not sources:
        return ""
    first = sources[0] or {}
    # 후보 키: code / lines
    blocks = first.get("code") or first.get("lines") or []
    if not isinstance(blocks, list) or not blocks:
        return ""
    lines = []
    for b in blocks:
        if isinstance(b, dict) and "line" in b and "text" in b:
            try:
                ln = int(b["line"])
                tx = str(b["text"])
                lines.append(f"{ln:>4} | {tx}")
            except Exception:
                pass
        elif isinstance(b, str):
            lines.append(b)
    return "\n".join(lines).strip()


def _try_get_snippet_via_raw(
    sonar_host_url: str,
    headers: dict,
    component_key: str,
    start_line: int,
    end_line: int
) -> str:
    """
    /api/sources/raw로 특정 라인 범위의 코드를 가져온다. (issue_snippets 실패 시 사용)
    - component_key: 파일 단위 컴포넌트 키
    - start_line/end_line: 검색된 textRange를 사용한다.
    - 성공 시 라인 번호를 붙여 반환한다.
    - SonarQube가 제공하는 "원시 코드" 엔드포인트라 가장 마지막 안전망 역할을 한다.
    - readme.md에서 언급된 “fallback으로 raw 사용”을 구현한 부분이다.
    """
    if not component_key or start_line <= 0 or end_line <= 0:
        return ""
    params = {"key": component_key, "from": str(start_line), "to": str(end_line)}
    url = _api_url(sonar_host_url, "/api/sources/raw", params)
    text = _safe_get_text(url, headers=headers, timeout=60)
    if not text:
        return ""
    # 라인 번호를 붙여준다.
    out = []
    cur = start_line
    for line in text.splitlines():
        out.append(f"{cur:>4} | {line}")
        cur += 1
    return "\n".join(out).strip()


def main() -> int:
    """
    CLI 엔트리포인트.
    - 입력 인자: Sonar API URL/공개 URL/토큰/프로젝트 키/필터(Severity, Status, Type)/페이지 사이즈/출력 경로
    - 동작 흐름:
      (A) issues/search를 페이징 호출해 필터 조건에 맞는 모든 이슈를 수집한다.
          * additionalFields=_all을 먼저 시도하고, 미지원 시 fallback하여 재시도한다.
      (B) 각 이슈에 대해 rule/show, issues/show, issue_snippets를 호출해 세부 정보를 보강한다.
          * 스니펫이 없으면 sources/raw로 textRange 구간을 재시도해 최대한 코드 문맥을 확보한다.
      (C) UI 링크를 생성하고 수집한 모든 데이터를 JSON으로 직렬화해 파일로 저장한다.
    - 반환: 성공 시 0, 네트워크/예상치 못한 오류 시 2
    초보자용 요약:
    1) SonarQube에서 "이슈 목록"을 다 가져온다.
    2) 각 이슈에 대해 "룰 설명"과 "코드 조각"을 추가로 받아온다.
    3) 보기 편하게 JSON 파일 하나로 묶어 저장한다.
    - readme.md Stage 2 Jenkins 단계와 동일한 인자 구성을 유지한다.
    - 출력 sonar_issues.json은 Stage 3(LLM 분석)의 입력이므로, 최대한 많은 메타데이터를 담는다.
    """
    ap = argparse.ArgumentParser()
    ap.add_argument("--sonar-host-url", required=True, help="컨테이너 기준 SonarQube URL (API 호출용)")
    ap.add_argument("--sonar-public-url", default="", help="브라우저 클릭용 SonarQube URL (UI 링크 생성용, optional)")
    ap.add_argument("--sonar-token", required=True, help="SonarQube user token")
    ap.add_argument("--project-key", required=True, help="SonarQube project key")
    ap.add_argument("--severities", default="BLOCKER,CRITICAL", help="comma list")
    ap.add_argument("--statuses", default="OPEN,REOPENED,CONFIRMED", help="comma list")
    ap.add_argument("--types", default="", help="comma list (optional)")
    ap.add_argument("--ps", type=int, default=200, help="page size")
    ap.add_argument("--output", default="sonar_issues.json", help="output file")
    args = ap.parse_args()

    sonar_host_url = args.sonar_host_url.rstrip("/")
    sonar_ui_base = (args.sonar_public_url or args.sonar_host_url).rstrip("/")

    severities = [x.strip() for x in (args.severities or "").split(",") if x.strip()]
    statuses = [x.strip() for x in (args.statuses or "").split(",") if x.strip()]
    types = [x.strip() for x in (args.types or "").split(",") if x.strip()]

    headers = {
        "Authorization": _build_basic_auth(args.sonar_token),
        "Accept": "application/json",
    }

    # (A) issues/search 전량 수집 (페이징)
    # - additionalFields=_all을 지원하는 버전이면 더 많은 필드를 한 번에 수집한다.
    # - HTTPError(예: 파라미터 미지원) 발생 시 추가 필드를 빼고 재시도한다.
    # - readme.md 예시처럼 ps(페이지 크기)를 적절히 설정해 호출 횟수를 줄인다.
    issues = []
    p = 1
    ps = args.ps

    use_additional_fields = True
    while True:
        params = {
            "componentKeys": args.project_key,
            "resolved": "false",
            "p": str(p),
            "ps": str(ps),
        }
        if severities:
            params["severities"] = ",".join(severities)
        if statuses:
            params["statuses"] = ",".join(statuses)
        if types:
            params["types"] = ",".join(types)
        if use_additional_fields:
            params["additionalFields"] = "_all"

        url = _api_url(sonar_host_url, "/api/issues/search", params)

        try:
            page = _http_get_json(url, headers=headers, timeout=60)
        except HTTPError as e:
            # additionalFields 미지원 등을 대비한 fallback
            if use_additional_fields:
                use_additional_fields = False
                continue
            raise e

        page_issues = page.get("issues") or []
        if isinstance(page_issues, list):
            issues.extend(page_issues)

        paging = page.get("paging") or {}
        total = int(paging.get("total", len(issues)))
        page_index = int(paging.get("pageIndex", p))
        page_size = int(paging.get("pageSize", ps))

        if page_index * page_size >= total:
            break
        p += 1

    # (B) rule/show, issues/show, snippet 보강
    # - rule/show 결과는 rules_cache에 저장해 중복 호출을 줄인다.
    # - issue_snippets → sources/raw 순으로 코드 스니펫을 최대한 확보한다.
    # - readme.md 기준 LLM 입력에 rule detail과 snippet을 모두 넣어 품질을 높인다.
    rules_cache = {}
    enriched = []

    for it in issues:
        issue_key = it.get("key") or ""
        rule_key = it.get("rule") or ""
        component_key = it.get("component") or ""

        # Rule detail
        if rule_key and rule_key not in rules_cache:
            rule_url = _api_url(sonar_host_url, "/api/rules/show", {"key": rule_key})
            rules_cache[rule_key] = _safe_get_json(rule_url, headers=headers, timeout=60)
        rule_json = rules_cache.get(rule_key, {}) or {}

        # Issue detail
        detail_url = _api_url(sonar_host_url, "/api/issues/show", {"issueKey": issue_key})
        issue_detail = _safe_get_json(detail_url, headers=headers, timeout=60)

        # Snippet: 우선 issue_snippets 시도 -> 실패 시 sources/raw fallback
        snippet = ""
        snippet_json = {}
        snippet_url = _api_url(sonar_host_url, "/api/sources/issue_snippets", {"issueKey": issue_key})
        snippet_json = _safe_get_json(snippet_url, headers=headers, timeout=60)
        snippet = _extract_snippet_from_issue_snippets(snippet_json)

        # Fallback: issues/search item의 textRange 이용해서 sources/raw
        if not snippet:
            tr = it.get("textRange") or {}
            try:
                start_line = int(tr.get("startLine", 0))
                end_line = int(tr.get("endLine", 0))
            except Exception:
                start_line, end_line = 0, 0
            if component_key and start_line > 0 and end_line >= start_line:
                snippet = _try_get_snippet_via_raw(
                    sonar_host_url=sonar_host_url,
                    headers=headers,
                    component_key=component_key,
                    start_line=start_line,
                    end_line=end_line,
                )

        sonar_url = _ui_issue_url(sonar_ui_base, args.project_key, issue_key)

        record = {
            "sonar_project_key": args.project_key,
            "sonar_issue_key": issue_key,
            "sonar_issue_url": sonar_url,
            "issue_search_item": it,
            "issue_detail": issue_detail,
            "sonar_rule_key": rule_key,
            "rule_detail": rule_json,
            "component": component_key,
            "code_snippet": snippet or "",
        }
        enriched.append(record)

    out = {
        "generated_at": int(time.time()),
        "sonar_host_url": sonar_host_url,
        "sonar_ui_base": sonar_ui_base,
        "project_key": args.project_key,
        "severities": severities,
        "statuses": statuses,
        "types": types,
        "count": len(enriched),
        "issues": enriched,
    }

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    print(f"[OK] exported: {args.output} (count={len(enriched)})")
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
