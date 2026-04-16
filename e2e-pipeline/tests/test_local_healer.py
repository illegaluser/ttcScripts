"""local_healer.py 유닛테스트.

LLM 없이 DOM 유사도 매칭으로 요소를 찾는 로직을 검증한다.
difflib.SequenceMatcher 는 실제로 동작시키고 Playwright 만 mock 한다.
"""

from unittest.mock import MagicMock

import pytest

from zero_touch_qa.local_healer import LocalHealer


def _make_mock_element(inner_text="", placeholder=None, value=None, aria_label=None):
    """inner_text 와 속성값이 설정된 mock DOM 요소를 생성한다."""
    el = MagicMock()
    el.inner_text.return_value = inner_text
    el.get_attribute.side_effect = lambda attr: {
        "placeholder": placeholder,
        "value": value,
        "aria-label": aria_label,
    }.get(attr)
    return el


# ---------------------------------------------------------------------------
# try_heal
# ---------------------------------------------------------------------------

def test_try_heal_finds_similar(mock_page):
    """target 과 유사한 텍스트 요소가 있으면 해당 Locator 를 반환한다."""
    el_match = _make_mock_element(inner_text="로그인")
    el_other = _make_mock_element(inner_text="완전히 다른 텍스트")

    locator_mock = MagicMock()
    locator_mock.all.return_value = [el_other, el_match]
    mock_page.locator.return_value = locator_mock

    healer = LocalHealer(mock_page, threshold=0.5)
    step = {"action": "click", "target": "로그인 버튼"}
    result = healer.try_heal(step)

    assert result is el_match


def test_try_heal_below_threshold(mock_page):
    """유사도가 threshold 미만이면 None 을 반환한다."""
    el = _make_mock_element(inner_text="완전히 무관한 텍스트")
    locator_mock = MagicMock()
    locator_mock.all.return_value = [el]
    mock_page.locator.return_value = locator_mock

    healer = LocalHealer(mock_page, threshold=0.99)
    step = {"action": "click", "target": "로그인"}
    assert healer.try_heal(step) is None


def test_try_heal_empty_target(mock_page):
    """target 이 1글자 이하이면 검색하지 않고 None 을 반환한다."""
    healer = LocalHealer(mock_page, threshold=0.5)
    step = {"action": "click", "target": ""}
    assert healer.try_heal(step) is None


def test_try_heal_fill_selector(mock_page):
    """fill 액션 시 SELECTOR_MAP['fill'] 선택자를 사용한다."""
    locator_mock = MagicMock()
    locator_mock.all.return_value = []
    mock_page.locator.return_value = locator_mock

    healer = LocalHealer(mock_page, threshold=0.5)
    step = {"action": "fill", "target": "이메일 입력"}
    healer.try_heal(step)

    called_selector = mock_page.locator.call_args[0][0]
    assert "input" in called_selector
    assert "textarea" in called_selector


def test_try_heal_click_selector(mock_page):
    """click 액션 시 DEFAULT_SELECTOR 를 사용한다."""
    locator_mock = MagicMock()
    locator_mock.all.return_value = []
    mock_page.locator.return_value = locator_mock

    healer = LocalHealer(mock_page, threshold=0.5)
    step = {"action": "click", "target": "로그인 버튼"}
    healer.try_heal(step)

    called_selector = mock_page.locator.call_args[0][0]
    assert "button" in called_selector


# ---------------------------------------------------------------------------
# _clean_target
# ---------------------------------------------------------------------------

def test_clean_target_text_prefix():
    """'text=로그인' -> '로그인' 으로 접두사를 제거한다."""
    assert LocalHealer._clean_target("text=로그인") == "로그인"


def test_clean_target_role_name():
    """'role=button, name=확인' 에서 role 접두사를 제거한다.

    _clean_target 은 두 단계로 접두사를 제거한다:
    1. ``^role=`` 접두사 제거 → ``button, name=확인``
    2. ``role=.+?, name=`` 패턴 제거 → ``확인``
    """
    result = LocalHealer._clean_target("role=button, name=확인")
    assert "확인" in result


# ---------------------------------------------------------------------------
# _extract_text
# ---------------------------------------------------------------------------

def test_extract_text_exception():
    """요소에서 텍스트 추출 시 예외가 발생하면 빈 문자열을 반환한다."""
    el = MagicMock()
    el.inner_text.side_effect = Exception("detached")
    assert LocalHealer._extract_text(el) == ""
