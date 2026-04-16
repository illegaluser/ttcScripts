"""zero_touch_qa 유닛테스트 공통 fixture."""

import pytest
from unittest.mock import MagicMock

from zero_touch_qa.config import Config
from zero_touch_qa.executor import StepResult


# ---------------------------------------------------------------------------
# Config fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_config(tmp_path):
    """테스트용 Config 인스턴스. artifacts_dir 은 tmp_path 를 사용한다."""
    return Config(
        dify_base_url="http://test/v1",
        dify_api_key="test-key",
        artifacts_dir=str(tmp_path / "artifacts"),
        viewport=(1440, 900),
        slow_mo=0,
        heal_threshold=0.8,
        dom_snapshot_limit=10000,
    )


# ---------------------------------------------------------------------------
# Playwright Mock fixtures
# ---------------------------------------------------------------------------

def _make_chainable_locator(**overrides):
    """체이닝 가능한 Locator MagicMock 을 생성한다.

    Playwright 의 ``page.get_by_role(...).first.click()`` 같은
    체이닝 패턴을 지원하기 위해 ``.first`` 가 자기 자신을 반환한다.

    Args:
        **overrides: inner_text, is_visible, count, all 등의 반환값을 덮어쓸 수 있다.
    """
    loc = MagicMock()
    loc.first = loc
    loc.count.return_value = overrides.get("count", 1)
    loc.inner_text.return_value = overrides.get("inner_text", "mock text")
    loc.input_value.return_value = overrides.get("input_value", "")
    loc.is_visible.return_value = overrides.get("is_visible", True)
    loc.get_attribute.return_value = overrides.get("get_attribute", None)
    loc.all.return_value = overrides.get("all", [])
    loc.click.return_value = None
    loc.fill.return_value = None
    loc.press.return_value = None
    loc.select_option.return_value = None
    loc.check.return_value = None
    loc.uncheck.return_value = None
    loc.hover.return_value = None
    return loc


@pytest.fixture
def mock_locator():
    """단독 Playwright Locator MagicMock."""
    return _make_chainable_locator()


@pytest.fixture
def mock_page():
    """Playwright Page 를 모사하는 MagicMock.

    get_by_role, get_by_text, get_by_label, get_by_placeholder,
    get_by_test_id, locator 메서드가 체이닝 가능한 Locator 를 반환한다.
    """
    page = MagicMock()
    locator = _make_chainable_locator()

    page.get_by_role.return_value = locator
    page.get_by_text.return_value = locator
    page.get_by_label.return_value = locator
    page.get_by_placeholder.return_value = locator
    page.get_by_test_id.return_value = locator
    page.locator.return_value = locator

    page.goto.return_value = None
    page.wait_for_load_state.return_value = None
    page.wait_for_timeout.return_value = None
    page.screenshot.return_value = None
    page.content.return_value = "<html><body>mock</body></html>"
    page.keyboard = MagicMock()
    return page


# ---------------------------------------------------------------------------
# StepResult factory
# ---------------------------------------------------------------------------

@pytest.fixture
def make_step_result():
    """StepResult 를 간편하게 생성하는 팩토리 함수.

    Example:
        result = make_step_result(status="FAIL", action="click")
    """
    def _factory(status="PASS", action="click", **kwargs):
        defaults = dict(
            step_id=1,
            action=action,
            target="text=로그인",
            value="",
            description="클릭 테스트",
            status=status,
            heal_stage="none",
            timestamp=1700000000.0,
            screenshot_path=None,
        )
        defaults.update(kwargs)
        return StepResult(**defaults)
    return _factory


# ---------------------------------------------------------------------------
# Sample scenario
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_scenario():
    """테스트용 DSL 시나리오 3스텝 (navigate, click, fill)."""
    return [
        {
            "step": 1, "action": "navigate", "target": "",
            "value": "http://example.com", "description": "이동",
            "fallback_targets": [],
        },
        {
            "step": 2, "action": "click", "target": "role=button, name=로그인",
            "value": "", "description": "로그인 클릭",
            "fallback_targets": ["text=Login"],
        },
        {
            "step": 3, "action": "fill", "target": "label=이메일",
            "value": "test@test.com", "description": "이메일 입력",
            "fallback_targets": [],
        },
    ]
