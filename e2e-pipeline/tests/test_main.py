"""__main__.py 유닛테스트.

CLI 엔트리포인트의 시나리오 준비와 에러 리포트 생성을 검증한다.
모든 외부 의존성(DifyClient, QAExecutor 등)은 mock 처리한다.
"""

import json
from argparse import Namespace
from unittest.mock import patch, MagicMock

import pytest

from zero_touch_qa.__main__ import _prepare_scenario, _generate_error_report
from zero_touch_qa.config import Config
from zero_touch_qa.dify_client import DifyConnectionError


@pytest.fixture
def config(tmp_path):
    """테스트용 Config."""
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
# _prepare_scenario
# ---------------------------------------------------------------------------

def test_prepare_scenario_execute_mode(tmp_path, config):
    """execute 모드에서 scenario.json 파일을 로드한다."""
    scenario_data = [{"step": 1, "action": "navigate", "value": "https://x.com"}]
    scenario_file = tmp_path / "scenario.json"
    scenario_file.write_text(json.dumps(scenario_data), encoding="utf-8")

    args = Namespace(mode="execute", scenario=str(scenario_file), file=None)
    result = _prepare_scenario(args, config, "", "")

    assert isinstance(result, list)
    assert result[0]["step"] == 1


def test_prepare_scenario_execute_no_file(config):
    """execute 모드에서 --scenario 없으면 FileNotFoundError 가 발생한다."""
    args = Namespace(mode="execute", scenario=None, file=None)
    with pytest.raises(FileNotFoundError):
        _prepare_scenario(args, config, "", "")


@patch("zero_touch_qa.__main__.convert_playwright_to_dsl")
def test_prepare_scenario_convert_mode(mock_convert, config):
    """convert 모드에서 convert_playwright_to_dsl 을 호출한다."""
    mock_convert.return_value = [{"step": 1, "action": "click"}]
    args = Namespace(mode="convert", file="/path/to/rec.py", scenario=None)

    result = _prepare_scenario(args, config, "", "")

    mock_convert.assert_called_once_with("/path/to/rec.py", config.artifacts_dir)
    assert len(result) == 1


@patch("zero_touch_qa.__main__.DifyClient")
def test_prepare_scenario_chat_mode(MockDifyClient, config):
    """chat 모드에서 DifyClient.generate_scenario 를 호출한다."""
    mock_instance = MagicMock()
    mock_instance.generate_scenario.return_value = [
        {"step": 1, "action": "navigate", "value": "https://x.com"}
    ]
    MockDifyClient.return_value = mock_instance

    args = Namespace(mode="chat", file=None, scenario=None)
    result = _prepare_scenario(args, config, "https://x.com", "검색 테스트")

    mock_instance.generate_scenario.assert_called_once()
    assert len(result) == 1


# ---------------------------------------------------------------------------
# _generate_error_report
# ---------------------------------------------------------------------------

def test_generate_error_report_creates_html(tmp_path):
    """Dify 연결 실패 시 에러 리포트 HTML 이 생성된다."""
    artifacts_dir = str(tmp_path / "artifacts")
    _generate_error_report(artifacts_dir, "Dify 연결 실패: timeout")

    html_path = tmp_path / "artifacts" / "index.html"
    assert html_path.exists()
    content = html_path.read_text(encoding="utf-8")
    assert "Dify 연결 실패" in content
    assert "timeout" in content
