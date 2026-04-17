import json
import os
import time
import logging
from html import escape as html_escape

from .executor import StepResult

log = logging.getLogger(__name__)


def save_run_log(results: list[StepResult], output_dir: str) -> str:
    """실행 결과를 JSONL(한 줄에 한 스텝) 형식으로 저장하고 파일 경로를 반환한다.

    Args:
        results: StepResult 리스트.
        output_dir: 저장 디렉터리.

    Returns:
        생성된 ``run_log.jsonl`` 의 절대 경로.
    """
    path = os.path.join(output_dir, "run_log.jsonl")
    with open(path, "w", encoding="utf-8") as f:
        for r in results:
            entry = {
                "step": r.step_id,
                "action": r.action,
                "target": r.target,
                "value": r.value,
                "description": r.description,
                "status": r.status,
                "heal_stage": r.heal_stage,
                "ts": r.timestamp,
            }
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    log.info("[Log] run_log.jsonl 생성 완료: %s", path)
    return path


def save_scenario(
    scenario: list[dict], output_dir: str, suffix: str = ""
) -> str:
    """DSL 시나리오를 JSON 파일로 저장한다.

    Args:
        scenario: DSL 스텝 dict 리스트.
        output_dir: 저장 디렉터리.
        suffix: 파일명 접미사. 예: ``".healed"`` → ``scenario.healed.json``.

    Returns:
        생성된 JSON 파일의 절대 경로.
    """
    filename = f"scenario{suffix}.json"
    path = os.path.join(output_dir, filename)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(scenario, f, indent=2, ensure_ascii=False)
    log.info("[Scenario] %s 저장 완료", filename)
    return path


def build_html_report(
    results: list[StepResult], output_dir: str, version: str = "4.0"
) -> str:
    """Jenkins HTML Publisher 플러그인용 시각적 리포트(index.html)를 생성한다.

    Args:
        results: StepResult 리스트.
        output_dir: 저장 디렉터리.
        version: 리포트에 표시할 버전 문자열.

    Returns:
        생성된 ``index.html`` 의 절대 경로.
    """
    total = len(results)
    passed = sum(1 for r in results if r.status == "PASS")
    healed = sum(1 for r in results if r.status == "HEALED")
    failed = sum(1 for r in results if r.status == "FAIL")
    pass_rate = round((passed + healed) / total * 100, 1) if total else 0

    rows = _build_table_rows(results)

    # 최종 상태 스크린샷 섹션 — 새 탭 전환 등으로 마지막 step_N_*.png 가
    # click 직전 페이지만 보여주는 경우 실제 도달 페이지를 별도 표시.
    final_state_path = os.path.join(output_dir, "final_state.png")
    if os.path.exists(final_state_path):
        final_section = (
            '<h2 style="margin-top:24px;">최종 도달 페이지</h2>'
            '<div class="final-state">'
            '<img src="final_state.png" alt="최종 상태"/>'
            '<p class="caption">모든 스텝 종료 후 활성 페이지의 상태입니다. '
            'click 이 새 탭을 연 경우 전환 후 페이지가 표시됩니다.</p>'
            '</div>'
        )
    else:
        final_section = ""

    html = _HTML_TEMPLATE.format(
        timestamp=time.strftime("%Y-%m-%d %H:%M:%S"),
        version=version,
        total=total,
        passed=passed,
        healed=healed,
        failed=failed,
        pass_rate=pass_rate,
        rows=rows,
        final_section=final_section,
    )

    path = os.path.join(output_dir, "index.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    log.info("[Report] HTML 리포트 생성 완료: %s", path)
    return path


def _build_table_rows(results: list[StepResult]) -> str:
    """StepResult 리스트를 HTML 테이블 ``<tr>`` 행 문자열로 변환한다."""
    rows = []
    for r in results:
        badge_class = {"PASS": "ok", "HEALED": "warn", "FAIL": "fail"}.get(
            r.status, "skip"
        )
        heal_info = f" ({r.heal_stage})" if r.heal_stage != "none" else ""

        if r.status == "PASS":
            screenshot = f"step_{r.step_id}_pass.png"
        elif r.status == "HEALED":
            screenshot = f"step_{r.step_id}_healed.png"
        else:
            screenshot = "error_final.png"

        rows.append(
            "<tr>"
            f"<td>{html_escape(str(r.step_id))}</td>"
            f"<td>{html_escape(r.action)}</td>"
            f"<td title=\"{html_escape(r.target)}\">"
            f"{html_escape(r.target[:60])}</td>"
            f"<td>{html_escape(r.description[:60])}</td>"
            f"<td><span class='badge {badge_class}'>"
            f"{html_escape(r.status)}{html_escape(heal_info)}</span></td>"
            f"<td><a href='{html_escape(screenshot)}' target='_blank'>"
            f"<img src='{html_escape(screenshot)}' class='thumb' alt='step {html_escape(str(r.step_id))}'/>"
            f"</a></td>"
            "</tr>"
        )
    return "\n      ".join(rows)


# ── HTML 템플릿 ──
_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <title>Zero-Touch QA 실행 리포트</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
           margin: 24px; color: #0f172a; background: #f1f5f9; line-height: 1.45; }}
    h1 {{ margin: 0 0 16px; }}
    .meta {{ background: #fff; border: 1px solid #dbe2ea; border-radius: 12px;
             padding: 14px; margin-bottom: 16px; }}
    .meta p {{ margin: 4px 0; }}
    .cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
              gap: 10px; margin-bottom: 16px; }}
    .card {{ background: #fff; border: 1px solid #dbe2ea; border-radius: 12px; padding: 12px; }}
    .card .label {{ font-size: 12px; color: #475569; }}
    .card .value {{ font-size: 22px; font-weight: 700; margin-top: 4px; }}
    table {{ width: 100%; border-collapse: collapse; background: #fff;
             border: 2px solid #334155; margin-top: 8px; }}
    th, td {{ border: 1px solid #64748b; padding: 8px; vertical-align: top;
              text-align: left; font-size: 13px; }}
    th {{ background: #eef2ff; }}
    .badge {{ display: inline-block; border-radius: 999px; padding: 2px 8px;
              font-size: 12px; font-weight: 700; }}
    .ok {{ background: #dcfce7; color: #166534; }}
    .warn {{ background: #fef3c7; color: #92400e; }}
    .fail {{ background: #fee2e2; color: #991b1b; }}
    .skip {{ background: #e2e8f0; color: #334155; }}
    a {{ color: #2563eb; text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    .footer {{ margin-top: 16px; font-size: 12px; color: #64748b; }}
    .thumb {{ max-width: 160px; max-height: 100px; border: 1px solid #cbd5e1;
              border-radius: 4px; display: block; }}
    .thumb:hover {{ border-color: #2563eb; box-shadow: 0 0 0 2px #bfdbfe; }}
    h2 {{ margin: 24px 0 8px; font-size: 18px; color: #0f172a; }}
    .final-state {{ background: #fff; border: 1px solid #dbe2ea; border-radius: 12px;
                    padding: 12px; }}
    .final-state img {{ max-width: 100%; border: 1px solid #cbd5e1; border-radius: 4px; }}
    .final-state .caption {{ margin-top: 8px; font-size: 13px; color: #475569; }}
  </style>
</head>
<body>
  <h1>Zero-Touch QA 실행 리포트</h1>
  <div class="meta">
    <p>실행 시각: <strong>{timestamp}</strong></p>
    <p>버전: <strong>v{version}</strong></p>
  </div>
  <div class="cards">
    <div class="card">
      <div class="label">전체 스텝</div><div class="value">{total}</div>
    </div>
    <div class="card">
      <div class="label">성공 (PASS)</div><div class="value">{passed}</div>
    </div>
    <div class="card">
      <div class="label">자가치유 (HEALED)</div><div class="value">{healed}</div>
    </div>
    <div class="card">
      <div class="label">실패 (FAIL)</div><div class="value">{failed}</div>
    </div>
    <div class="card">
      <div class="label">성공률</div><div class="value">{pass_rate}%</div>
    </div>
  </div>
  <table>
    <thead>
      <tr>
        <th>Step</th><th>Action</th><th>Target</th>
        <th>Description</th><th>Status</th><th>Evidence</th>
      </tr>
    </thead>
    <tbody>
      {rows}
    </tbody>
  </table>
  {final_section}
  <div class="footer">
    Generated by DSCORE Zero-Touch QA v{version}
  </div>
</body>
</html>"""
