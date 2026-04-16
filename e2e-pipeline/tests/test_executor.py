"""executor.py 유닛테스트.

QAExecutor 의 정적 메서드(_normalize_step, _perform_action, _screenshot)를
개별 검증한다. sync_playwright 는 mock 처리한다.
"""

import os
from unittest.mock import MagicMock, patch

import pytest

from zero_touch_qa.executor import QAExecutor, StepResult


# ---------------------------------------------------------------------------
# _normalize_step — LLM 출력 보정
# ---------------------------------------------------------------------------

def test_normalize_press_target_to_value():
    """press 에서 target=Enter, value='' 이면 target 과 value 를 swap 한다."""
    step = {"action": "press", "target": "Enter", "value": ""}
    QAExecutor._normalize_step(step)

    assert step["value"] == "Enter"
    assert step["target"] == ""


def test_normalize_press_no_swap():
    """press 에서 value 가 이미 있으면 변경하지 않는다."""
    step = {"action": "press", "target": "label=검색", "value": "Enter"}
    QAExecutor._normalize_step(step)

    assert step["value"] == "Enter"
    assert step["target"] == "label=검색"


def test_normalize_navigate_to_value():
    """navigate 에서 target=URL, value='' 이면 swap 한다."""
    step = {"action": "navigate", "target": "https://example.com", "value": ""}
    QAExecutor._normalize_step(step)

    assert step["value"] == "https://example.com"
    assert step["target"] == ""


def test_normalize_navigate_no_swap():
    """navigate 에서 value 가 이미 있으면 변경하지 않는다."""
    step = {"action": "navigate", "target": "", "value": "https://example.com"}
    QAExecutor._normalize_step(step)

    assert step["value"] == "https://example.com"


# ---------------------------------------------------------------------------
# _perform_action — 9대 DSL 액션
# ---------------------------------------------------------------------------

def test_perform_click(mock_page, mock_locator):
    """click 액션은 locator.click(timeout=5000) 을 호출한다."""
    step = {"action": "click", "value": ""}
    QAExecutor._perform_action(mock_page, mock_locator, step)
    mock_locator.click.assert_called_once_with(timeout=5000)


def test_perform_fill(mock_page, mock_locator):
    """fill 액션은 locator.fill(value) 를 호출한다."""
    step = {"action": "fill", "value": "테스트 입력"}
    QAExecutor._perform_action(mock_page, mock_locator, step)
    mock_locator.fill.assert_called_once_with("테스트 입력")


def test_perform_press(mock_page, mock_locator):
    """press 액션은 locator.press(value) 를 호출한다."""
    step = {"action": "press", "value": "Enter"}
    QAExecutor._perform_action(mock_page, mock_locator, step)
    mock_locator.press.assert_called_once_with("Enter")


def test_perform_select(mock_page, mock_locator):
    """select 액션은 locator.select_option(label=value) 를 호출한다."""
    step = {"action": "select", "value": "옵션1"}
    QAExecutor._perform_action(mock_page, mock_locator, step)
    mock_locator.select_option.assert_called_once_with(label="옵션1")


def test_perform_check_on(mock_page, mock_locator):
    """check 액션(value!='off')은 locator.check() 를 호출한다."""
    step = {"action": "check", "value": "on"}
    QAExecutor._perform_action(mock_page, mock_locator, step)
    mock_locator.check.assert_called_once()


def test_perform_check_off(mock_page, mock_locator):
    """check 액션(value='off')은 locator.uncheck() 를 호출한다."""
    step = {"action": "check", "value": "off"}
    QAExecutor._perform_action(mock_page, mock_locator, step)
    mock_locator.uncheck.assert_called_once()


def test_perform_hover(mock_page, mock_locator):
    """hover 액션은 locator.hover() 를 호출한다."""
    step = {"action": "hover", "value": ""}
    QAExecutor._perform_action(mock_page, mock_locator, step)
    mock_locator.hover.assert_called_once()


def test_perform_verify_visible(mock_page, mock_locator):
    """verify 액션(value 없음)은 locator.is_visible() 로 가시성을 확인한다."""
    mock_locator.is_visible.return_value = True
    step = {"action": "verify", "value": "", "target": "text=OK"}
    QAExecutor._perform_action(mock_page, mock_locator, step)
    mock_locator.is_visible.assert_called_once()


def test_perform_verify_text_pass(mock_page, mock_locator):
    """verify 액션(value 있음)은 inner_text 에 기대 텍스트가 포함되면 통과한다."""
    mock_locator.inner_text.return_value = "환영합니다 사용자님"
    mock_locator.input_value.return_value = ""
    step = {"action": "verify", "value": "환영합니다", "target": "text=환영"}
    QAExecutor._perform_action(mock_page, mock_locator, step)  # 예외 없으면 통과


def test_perform_verify_text_fail(mock_page, mock_locator):
    """verify 액션에서 기대 텍스트가 없으면 AssertionError 가 발생한다."""
    mock_locator.inner_text.return_value = "다른 텍스트"
    mock_locator.input_value.return_value = ""
    step = {"action": "verify", "value": "환영합니다", "target": "text=환영"}

    with pytest.raises(AssertionError, match="텍스트 불일치"):
        QAExecutor._perform_action(mock_page, mock_locator, step)


def test_perform_unknown_action(mock_page, mock_locator):
    """미지원 액션은 ValueError 를 발생시킨다."""
    step = {"action": "unknown_action", "value": ""}
    with pytest.raises(ValueError, match="미지원 DSL 액션"):
        QAExecutor._perform_action(mock_page, mock_locator, step)


# ---------------------------------------------------------------------------
# _screenshot / _safe_screenshot
# ---------------------------------------------------------------------------

def test_screenshot_returns_path(mock_page, tmp_path):
    """스크린샷 경로가 올바른 형식으로 반환된다."""
    path = QAExecutor._screenshot(mock_page, str(tmp_path), 1, "pass")
    assert path.endswith("step_1_pass.png")
    mock_page.screenshot.assert_called_once()


def test_safe_screenshot_swallows_exception(mock_page, tmp_path):
    """page.screenshot 이 예외를 발생시켜도 무시한다."""
    mock_page.screenshot.side_effect = Exception("browser closed")
    QAExecutor._safe_screenshot(mock_page, str(tmp_path / "err.png"))
    # 예외 없이 정상 종료되면 통과
