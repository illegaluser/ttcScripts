#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# gitlab_issue_creator.py
# 목적: 분석 결과를 GitLab 이슈로 생성. 제목 포맷을 [Severity] Message 로 통일.

import argparse
import json
import sys
import time
import re
from urllib.parse import urlencode, urljoin, quote
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

def _http_post_form(url: str, headers: dict, form: dict, timeout: int = 60):
    data = urlencode(form, doseq=True).encode("utf-8")
    h = dict(headers or {})
    h["Content-Type"] = "application/x-www-form-urlencoded"
    req = Request(url, headers=h, method="POST", data=data)
    with urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8", errors="replace")
        return resp.status, body

def _http_get_json(url: str, headers: dict, timeout: int = 60) -> dict:
    req = Request(url, headers=headers, method="GET")
    with urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))

def _replace_sonar_url(text: str, sonar_host_url: str, sonar_public_url: str) -> str:
    if not text: return text
    target_base = (sonar_public_url or "http://localhost:9000").rstrip("/")
    if sonar_host_url:
        text = text.replace(sonar_host_url.rstrip("/"), target_base)
    text = text.replace("http://sonarqube:9000", target_base)
    pattern = r"(?<!http:)(?<!https:)(?<![a-zA-Z0-9])(/project/issues\?)"
    text = re.sub(pattern, f"{target_base}\\1", text)
    return text

def _find_existing_by_sonar_key(gitlab_host_url: str, headers: dict, project: str, key: str) -> bool:
    if not key: return False
    url = f"{gitlab_host_url.rstrip('/')}/api/v4/projects/{quote(project, safe='')}/issues?search={key}"
    try:
        arr = _http_get_json(url, headers)
        return isinstance(arr, list) and len(arr) > 0
    except Exception:
        return False

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gitlab-host-url", required=True)
    ap.add_argument("--gitlab-token", required=True)
    ap.add_argument("--gitlab-project", required=True)
    ap.add_argument("--input", default="llm_analysis.jsonl")
    ap.add_argument("--output", default="gitlab_issues_created.json")
    ap.add_argument("--sonar-host-url", default="")
    ap.add_argument("--sonar-public-url", default="")
    ap.add_argument("--timeout", type=int, default=60)
    args = ap.parse_args()

    headers = {"PRIVATE-TOKEN": args.gitlab_token}
    
    created, skipped, failed = [], [], []
    rows = []
    
    try:
        with open(args.input, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip(): rows.append(json.loads(line))
    except Exception as e:
        print(f"[ERROR] Read failed: {e}", file=sys.stderr)
        return 2

    for row in rows:
        sonar_key = row.get("sonar_issue_key")
        outputs = row.get("outputs") or {}
        
        # [핵심] 제목 결정 로직
        # Sonar Message가 있으면 최우선 사용, 없으면 LLM 제목 사용
        msg = row.get("sonar_message") or ""
        llm_title = outputs.get("title") or ""
        main_title = msg if msg else llm_title
        
        severity = row.get("severity") or ""
        final_title = f"[{severity}] {main_title}" if severity else main_title
        
        desc = outputs.get("description_markdown") or ""
        desc = _replace_sonar_url(desc, args.sonar_host_url, args.sonar_public_url)
        
        if not final_title or not desc:
            failed.append({"key": sonar_key, "reason": "Empty title/desc"})
            continue

        if _find_existing_by_sonar_key(args.gitlab_host_url, headers, args.gitlab_project, sonar_key):
            skipped.append({"key": sonar_key, "title": final_title, "reason": "Dedup"})
            continue

        # 이슈 생성
        form = {"title": final_title, "description": desc}
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

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump({"created": created, "skipped": skipped, "failed": failed}, f, ensure_ascii=False, indent=2)

    print(f"[OK] created={len(created)} skipped={len(skipped)} failed={len(failed)} output={args.output}")
    return 2 if failed else 0

if __name__ == "__main__":
    sys.exit(main())