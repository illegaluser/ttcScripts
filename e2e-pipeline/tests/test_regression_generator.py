"""regression_generator.py 유닛테스트.

성공한 시나리오를 독립 실행 Playwright 스크립트로 변환하는 로직을 검증한다.
"""

import pytest

from zero_touch_qa.regression_generator import (
    generate_regression_test,
    _target_to_playwright_code,
)


# ---------------------------------------------------------------------------
# generate_regression_test
# ---------------------------------------------------------------------------

def test_generate_all_pass(tmp_path, make_step_result, sample_scenario):
    """모든 스텝이 PASS 이면 regression_test.py 를 생성하고 경로를 반환한다."""
    results = [make_step_result(step_id=i, status="PASS") for i in range(1, 4)]
    path = generate_regression_test(sample_scenario, results, str(tmp_path))

    assert path is not None
    assert path.endswith("regression_test.py")
    assert (tmp_path / "regression_test.py").exists()


def test_generate_has_fail_returns_none(tmp_path, make_step_result, sample_scenario):
    """FAIL 스텝이 포함되면 None 을 반환한다."""
    results = [
        make_step_result(step_id=1, status="PASS"),
        make_step_result(step_id=2, status="FAIL"),
        make_step_result(step_id=3, status="PASS"),
    ]
    assert generate_regression_test(sample_scenario, results, str(tmp_path)) is None


def test_generated_script_contains_navigate(tmp_path, make_step_result, sample_scenario):
    """생성된 스크립트에 page.goto 코드가 포함된다."""
    results = [make_step_result(step_id=i, status="PASS") for i in range(1, 4)]
    generate_regression_test(sample_scenario, results, str(tmp_path))
    content = (tmp_path / "regression_test.py").read_text(encoding="utf-8")
    assert "page.goto(" in content


def test_generated_script_contains_click(tmp_path, make_step_result, sample_scenario):
    """생성된 스크립트에 .click 코드가 포함된다."""
    results = [make_step_result(step_id=i, status="PASS") for i in range(1, 4)]
    generate_regression_test(sample_scenario, results, str(tmp_path))
    content = (tmp_path / "regression_test.py").read_text(encoding="utf-8")
    assert ".click(" in content


def test_generated_script_contains_fill(tmp_path, make_step_result, sample_scenario):
    """생성된 스크립트에 .fill 코드가 포함된다."""
    results = [make_step_result(step_id=i, status="PASS") for i in range(1, 4)]
    generate_regression_test(sample_scenario, results, str(tmp_path))
    content = (tmp_path / "regression_test.py").read_text(encoding="utf-8")
    assert ".fill(" in content


# ---------------------------------------------------------------------------
# _target_to_playwright_code
# ---------------------------------------------------------------------------

def test_target_to_playwright_role():
    """'role=button, name=로그인' -> page.get_by_role 코드를 생성한다."""
    code = _target_to_playwright_code("role=button, name=로그인")
    assert "page.get_by_role(" in code
    assert "button" in code
    # json.dumps 는 한국어를 유니코드 이스케이프할 수 있으므로 원문 또는 이스케이프 형태 모두 허용
    assert "로그인" in code or "\\ub85c" in code


def test_target_to_playwright_text():
    """'text=확인' -> page.get_by_text 코드를 생성한다."""
    code = _target_to_playwright_code("text=확인")
    assert "page.get_by_text(" in code
    assert "확인" in code or "\\ud655" in code


def test_target_to_playwright_empty():
    """빈 target -> page.locator('body') 코드를 생성한다."""
    assert _target_to_playwright_code("") == 'page.locator("body")'
