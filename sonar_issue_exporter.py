#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
sonar_issue_exporter.py

목적.
- SonarQube 특정 프로젝트에서 지정 severities/statuses 이슈를 수집한다.
- 이슈 1건당 가능한 한 많은 원본 정보를 함께 저장한다.
- code_snippet을 가능한 범위에서 채운다(없으면 빈 문자열).

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
    req = Request(url, headers=headers, method="GET")
    with urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8", errors="replace")
        return json.loads(body)


def _safe_get_json(url: str, headers: dict, timeout: int = 60) -> dict:
    try:
        return _http_get_json(url, headers=headers, timeout=timeout)
    except Exception as e:
        return {"_error": str(e), "_url": url}


def _http_get_text(url: str, headers: dict, timeout: int = 60) -> str:
    req = Request(url, headers=headers, method="GET")
    with urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _safe_get_text(url: str, headers: dict, timeout: int = 60) -> str:
    try:
        return _http_get_text(url, headers=headers, timeout=timeout)
    except Exception:
        return ""


def _build_basic_auth(token: str) -> str:
    raw = f"{token}:".encode("utf-8")
    b64 = base64.b64encode(raw).decode("ascii")
    return f"Basic {b64}"


def _api_url(sonar_host_url: str, api_path: str, params: dict) -> str:
    base = sonar_host_url.rstrip("/") + "/"
    qs = urlencode(params, doseq=True)
    return urljoin(base, api_path.lstrip("/")) + ("?" + qs if qs else "")


def _ui_issue_url(sonar_ui_base: str, project_key: str, issue_key: str) -> str:
    base = sonar_ui_base.rstrip("/") + "/"
    return f"{base}project/issues?id={project_key}&issues={issue_key}&open={issue_key}"


def _extract_snippet_from_issue_snippets(snippet_json: dict) -> str:
    # Sonar 버전별로 구조가 다를 수 있으니 최대한 보수적으로 파싱한다.
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
    # sources/raw는 key(=파일 컴포넌트 키) + from/to로 라인을 가져올 수 있다.
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
