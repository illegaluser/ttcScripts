"""report.py 유닛테스트.

run_log.jsonl, scenario.json, HTML 리포트 생성 로직을 검증한다.
"""

import json

import pytest

from zero_touch_qa.report import (
    save_run_log,
    save_scenario,
    build_html_report,
    _build_table_rows,
)


# ---------------------------------------------------------------------------
# save_run_log
# ---------------------------------------------------------------------------

def test_save_run_log_creates_jsonl(tmp_path, make_step_result):
    """run_log.jsonl 파일이 생성되고 라인 수가 결과 수와 동일하다."""
    results = [make_step_result(step_id=i) for i in range(1, 4)]
    save_run_log(results, str(tmp_path))

    lines = (tmp_path / "run_log.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 3


def test_save_run_log_content_format(tmp_path, make_step_result):
    """각 라인이 유효한 JSON 이고 필수 키(step, action, status)를 포함한다."""
    results = [make_step_result(step_id=1, action="click", status="PASS")]
    save_run_log(results, str(tmp_path))

    for line in (tmp_path / "run_log.jsonl").read_text(encoding="utf-8").strip().splitlines():
        entry = json.loads(line)
        assert "step" in entry
        assert "action" in entry
        assert "status" in entry


# ---------------------------------------------------------------------------
# save_scenario
# ---------------------------------------------------------------------------

def test_save_scenario_default_name(tmp_path):
    """기본 호출 시 scenario.json 파일명으로 저장된다."""
    scenario = [{"step": 1, "action": "navigate"}]
    path = save_scenario(scenario, str(tmp_path))

    assert path.endswith("scenario.json")
    assert (tmp_path / "scenario.json").exists()


def test_save_scenario_with_suffix(tmp_path):
    """suffix='.healed' 이면 scenario.healed.json 으로 저장된다."""
    scenario = [{"step": 1, "action": "click"}]
    path = save_scenario(scenario, str(tmp_path), suffix=".healed")

    assert path.endswith("scenario.healed.json")
    assert (tmp_path / "scenario.healed.json").exists()


# ---------------------------------------------------------------------------
# build_html_report
# ---------------------------------------------------------------------------

def test_build_html_creates_file(tmp_path, make_step_result):
    """index.html 파일이 생성된다."""
    build_html_report([make_step_result()], str(tmp_path))
    assert (tmp_path / "index.html").exists()


def test_build_html_pass_rate(tmp_path, make_step_result):
    """3 PASS + 1 FAIL -> 성공률 75.0%."""
    results = [
        make_step_result(step_id=1, status="PASS"),
        make_step_result(step_id=2, status="PASS"),
        make_step_result(step_id=3, status="PASS"),
        make_step_result(step_id=4, status="FAIL"),
    ]
    build_html_report(results, str(tmp_path))
    html = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert "75.0%" in html


def test_build_html_empty_results(tmp_path):
    """빈 결과 리스트이면 성공률 0 을 표시한다."""
    build_html_report([], str(tmp_path))
    html = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert "0" in html


# ---------------------------------------------------------------------------
# _build_table_rows
# ---------------------------------------------------------------------------

def test_build_table_rows_badge_classes(make_step_result):
    """PASS->ok, HEALED->warn, FAIL->fail 뱃지 클래스가 적용된다."""
    results = [
        make_step_result(step_id=1, status="PASS"),
        make_step_result(step_id=2, status="HEALED", heal_stage="local"),
        make_step_result(step_id=3, status="FAIL"),
    ]
    html = _build_table_rows(results)
    assert "badge ok" in html
    assert "badge warn" in html
    assert "badge fail" in html


def test_build_table_rows_heal_info(make_step_result):
    """heal_stage='fallback' 이면 '(fallback)' 텍스트가 표시된다."""
    results = [make_step_result(status="HEALED", heal_stage="fallback")]
    html = _build_table_rows(results)
    assert "(fallback)" in html
