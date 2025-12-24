#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
gitlab_issue_creator.py

목적.
- llm_analysis.jsonl을 읽어 GitLab Issue를 생성한다.
- Dedup(중복 방지): 기본은 sonar_issue_key 검색으로 이미 등록된 이슈가 있으면 skip한다.
- Sonar 링크 클릭 문제 해결: description_markdown 내 URL을 등록 직전에 치환한다.

입력(JSONL 각 줄).
{
  "sonar_issue_key": "...",
  "sonar_project_key": "...",
  "sonar_issue_url": "...",
  "outputs": {
    "title": "...",
    "description_markdown": "...",
    "labels": [...]
  }
}

출력.
- gitlab_issues_created.json
"""

import argparse
import json
import sys
import time
from urllib.parse import urlencode, urljoin, quote
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError


def _http_get_json(url: str, headers: dict, timeout: int = 60) -> dict:
    req = Request(url, headers=headers, method="GET")
    with urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8", errors="replace")
        return json.loads(body)


def _http_post_form(url: str, headers: dict, form: dict, timeout: int = 60):
    data = urlencode(form, doseq=True).encode("utf-8")
    h = dict(headers or {})
    h["Content-Type"] = "application/x-www-form-urlencoded"
    h["Accept"] = "application/json"
    req = Request(url, headers=h, method="POST", data=data)
    with urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8", errors="replace")
        return resp.status, body


def _project_path_to_api_segment(project: str) -> str:
    # "root/dscore-ttc-sample" -> "root%2Fdscore-ttc-sample"
    return quote(project, safe="")


def _replace_sonar_url(text: str, sonar_host_url: str, sonar_public_url: str) -> str:
    if not text:
        return text

    # (A) 명시적으로 받은 host/public로 치환
    if sonar_host_url and sonar_public_url:
        a = sonar_host_url.rstrip("/")
        b = sonar_public_url.rstrip("/")
        text = text.replace(a, b)

    # (B) PoC 기본 치환(네가 말한 “문자열 치환 1줄”)
    # - 이슈 본문에 내부 주소가 들어가도 브라우저에서 클릭 가능하게 만든다.
    text = text.replace("http://sonarqube:9000", "http://localhost:9000")
    text = text.replace("https://sonarqube:9000", "http://localhost:9000")
    return text


def _find_existing_by_sonar_key(gitlab_host_url: str, headers: dict, project: str, sonar_issue_key: str) -> bool:
    # 아주 단순하게: issues 검색으로 sonar_issue_key가 이미 존재하면 skip
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
        desc = _replace_sonar_url(desc, args.sonar_host_url, args.sonar_public_url)

        # (2) Dedup: sonar_issue_key로 검색해서 있으면 skip
        if _find_existing_by_sonar_key(gitlab_host_url, headers, project, sonar_issue_key):
            skipped.append({"sonar_issue_key": sonar_issue_key, "title": title, "reason": "dedup(search)"})
            continue

        # (3) GitLab Issue 생성
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
