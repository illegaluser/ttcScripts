"""locator_resolver.py 유닛테스트.

7단계 시맨틱 로케이터 탐색 엔진의 각 분기를 검증한다.
Playwright Page 는 conftest.py 의 mock_page fixture 로 대체한다.
"""

from unittest.mock import MagicMock

import pytest

from zero_touch_qa.locator_resolver import LocatorResolver


# ---------------------------------------------------------------------------
# resolve — 기본 분기
# ---------------------------------------------------------------------------

def test_resolve_none_target(mock_page):
    """target 이 None 이면 None 을 반환한다."""
    resolver = LocatorResolver(mock_page)
    assert resolver.resolve(None) is None


def test_resolve_empty_string(mock_page):
    """target 이 빈 문자열이면 None 을 반환한다."""
    resolver = LocatorResolver(mock_page)
    assert resolver.resolve("") is None


# ---------------------------------------------------------------------------
# resolve — role 기반
# ---------------------------------------------------------------------------

def test_resolve_role_with_name(mock_page):
    """'role=button, name=로그인' -> get_by_role('button', name='로그인') 호출."""
    resolver = LocatorResolver(mock_page)
    result = resolver.resolve("role=button, name=로그인")

    assert result is not None
    mock_page.get_by_role.assert_called_with("button", name="로그인")


def test_resolve_role_only(mock_page):
    """'role=heading' -> get_by_role('heading') 호출."""
    resolver = LocatorResolver(mock_page)
    result = resolver.resolve("role=heading")

    assert result is not None
    mock_page.get_by_role.assert_called_with("heading")


# ---------------------------------------------------------------------------
# resolve — 시맨틱 접두사
# ---------------------------------------------------------------------------

def test_resolve_text_prefix(mock_page):
    """'text=로그인' -> get_by_text('로그인') 호출."""
    resolver = LocatorResolver(mock_page)
    result = resolver.resolve("text=로그인")

    assert result is not None
    mock_page.get_by_text.assert_called_with("로그인")


def test_resolve_label_prefix(mock_page):
    """'label=이메일' -> get_by_label('이메일') 호출."""
    resolver = LocatorResolver(mock_page)
    result = resolver.resolve("label=이메일")

    assert result is not None
    mock_page.get_by_label.assert_called_with("이메일")


def test_resolve_placeholder_prefix(mock_page):
    """'placeholder=검색어' -> get_by_placeholder('검색어') 호출."""
    resolver = LocatorResolver(mock_page)
    result = resolver.resolve("placeholder=검색어")

    assert result is not None
    mock_page.get_by_placeholder.assert_called_with("검색어")


def test_resolve_testid_prefix(mock_page):
    """'testid=submit-btn' -> get_by_test_id('submit-btn') 호출."""
    resolver = LocatorResolver(mock_page)
    result = resolver.resolve("testid=submit-btn")

    assert result is not None
    mock_page.get_by_test_id.assert_called_with("submit-btn")


# ---------------------------------------------------------------------------
# resolve — role 존재 검증
# ---------------------------------------------------------------------------

def test_resolve_role_no_match(mock_page):
    """role 로케이터의 count 가 0 이면 None 을 반환한다 (30초 타임아웃 방지)."""
    no_match = MagicMock()
    no_match.count.return_value = 0
    no_match.first = no_match
    mock_page.get_by_role.return_value = no_match
    mock_page.locator.return_value = no_match

    resolver = LocatorResolver(mock_page)
    assert resolver.resolve("role=searchbox") is None


def test_resolve_semantic_no_match(mock_page):
    """시맨틱 로케이터의 count 가 0 이면 None 을 반환한다."""
    no_match = MagicMock()
    no_match.count.return_value = 0
    no_match.first = no_match
    mock_page.get_by_text.return_value = no_match
    mock_page.locator.return_value = no_match

    resolver = LocatorResolver(mock_page)
    assert resolver.resolve("text=존재하지않는텍스트") is None


# ---------------------------------------------------------------------------
# resolve — CSS/XPath
# ---------------------------------------------------------------------------

def test_resolve_css_selector(mock_page):
    """'#login-btn' -> page.locator('#login-btn') 호출."""
    resolver = LocatorResolver(mock_page)
    result = resolver.resolve("#login-btn")

    assert result is not None
    mock_page.locator.assert_called_with("#login-btn")


def test_resolve_css_no_match(mock_page):
    """locator.count() == 0 이면 None 을 반환한다."""
    no_match = MagicMock()
    no_match.count.return_value = 0
    mock_page.locator.return_value = no_match

    resolver = LocatorResolver(mock_page)
    assert resolver.resolve("#nonexistent") is None


# ---------------------------------------------------------------------------
# resolve — dict 타겟
# ---------------------------------------------------------------------------

def test_resolve_dict_target_role(mock_page):
    """dict 형태 {"role": "button", "name": "확인"} 을 해석한다."""
    resolver = LocatorResolver(mock_page)
    result = resolver.resolve({"role": "button", "name": "확인"})

    assert result is not None
    mock_page.get_by_role.assert_called_with("button", name="확인")


def test_resolve_dict_fallback_selector(mock_page):
    """dict 에 알려진 키가 없으면 selector 키로 폴백한다."""
    resolver = LocatorResolver(mock_page)
    result = resolver.resolve({"selector": "#custom"})

    assert result is not None
    mock_page.locator.assert_called_with("#custom")
