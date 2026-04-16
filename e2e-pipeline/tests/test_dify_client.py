"""dify_client.py 유닛테스트.

Dify Chatflow API 통신 계층을 검증한다.
requests.request 를 mock 하여 네트워크 없이 테스트한다.
"""

import json
from unittest.mock import patch, MagicMock

import pytest
import requests

from zero_touch_qa.dify_client import DifyClient, DifyConnectionError
from zero_touch_qa.config import Config


@pytest.fixture
def client(sample_config):
    """테스트용 DifyClient 인스턴스."""
    return DifyClient(sample_config)


def _make_response(status_code=200, json_data=None):
    """requests.Response 를 모사하는 MagicMock 을 생성한다."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    if status_code >= 400:
        resp.raise_for_status.side_effect = requests.HTTPError(
            f"{status_code} Error"
        )
    else:
        resp.raise_for_status.return_value = None
    return resp


# ---------------------------------------------------------------------------
# upload_file
# ---------------------------------------------------------------------------

@patch("zero_touch_qa.dify_client.requests.request")
def test_upload_file_success(mock_request, client, tmp_path):
    """정상 업로드 시 file_id 를 반환한다."""
    mock_request.return_value = _make_response(200, {"id": "file-123"})
    f = tmp_path / "doc.pdf"
    f.write_bytes(b"%PDF-1.4 fake content")

    result = client.upload_file(str(f))
    assert result == "file-123"


@patch("zero_touch_qa.dify_client.requests.request")
def test_upload_file_http_error(mock_request, client, tmp_path):
    """HTTP 에러 시 DifyConnectionError 가 발생한다."""
    mock_request.return_value = _make_response(400)
    f = tmp_path / "doc.pdf"
    f.write_bytes(b"%PDF")

    with pytest.raises(DifyConnectionError):
        client.upload_file(str(f))


# ---------------------------------------------------------------------------
# generate_scenario
# ---------------------------------------------------------------------------

@patch("zero_touch_qa.dify_client.requests.request")
def test_generate_scenario_success(mock_request, client):
    """정상 응답에서 시나리오 배열을 반환한다."""
    steps = [{"step": 1, "action": "navigate", "value": "https://x.com"}]
    mock_request.return_value = _make_response(200, {
        "answer": json.dumps(steps, ensure_ascii=False)
    })
    result = client.generate_scenario("chat", "테스트", "https://x.com")

    assert isinstance(result, list)
    assert result[0]["step"] == 1


@patch("zero_touch_qa.dify_client.requests.request")
def test_generate_scenario_with_think_block(mock_request, client):
    """<think> 블록이 포함된 응답도 정상 파싱한다."""
    steps = [{"step": 1, "action": "click", "target": "text=OK"}]
    answer = f"<think>reasoning here</think>{json.dumps(steps)}"
    mock_request.return_value = _make_response(200, {"answer": answer})

    result = client.generate_scenario("chat", "테스트", "https://x.com")
    assert isinstance(result, list)
    assert result[0]["action"] == "click"


@patch("zero_touch_qa.dify_client.requests.request")
def test_generate_scenario_parse_failure(mock_request, client):
    """JSON 파싱 불가 시 DifyConnectionError 가 발생한다."""
    mock_request.return_value = _make_response(200, {"answer": "no json here"})

    with pytest.raises(DifyConnectionError, match="시나리오 파싱 실패"):
        client.generate_scenario("chat", "테스트", "https://x.com")


@patch("zero_touch_qa.dify_client.requests.request")
def test_generate_scenario_with_file_id(mock_request, client):
    """file_id 가 payload 의 files 에 포함된다."""
    steps = [{"step": 1, "action": "navigate", "value": "https://x.com"}]
    mock_request.return_value = _make_response(200, {
        "answer": json.dumps(steps)
    })
    client.generate_scenario("doc", "테스트", "https://x.com", file_id="f-1")

    call_kwargs = mock_request.call_args
    payload = call_kwargs.kwargs.get("json")
    assert "files" in payload
    assert payload["files"][0]["upload_file_id"] == "f-1"


# ---------------------------------------------------------------------------
# request_healing
# ---------------------------------------------------------------------------

@patch("zero_touch_qa.dify_client.requests.request")
def test_request_healing_success(mock_request, client):
    """치유 응답에서 dict 를 반환한다."""
    heal_data = {"target": "text=새타겟"}
    mock_request.return_value = _make_response(200, {
        "answer": json.dumps(heal_data)
    })
    result = client.request_healing("에러", "<html></html>", {"action": "click"})

    assert isinstance(result, dict)
    assert result["target"] == "text=새타겟"


@patch("zero_touch_qa.dify_client.requests.request")
def test_request_healing_returns_none(mock_request, client):
    """파싱 불가 시 None 을 반환한다."""
    mock_request.return_value = _make_response(200, {"answer": "unable to heal"})
    result = client.request_healing("에러", "<html></html>", {"action": "click"})

    assert result is None


# ---------------------------------------------------------------------------
# _call — 타임아웃
# ---------------------------------------------------------------------------

@patch("zero_touch_qa.dify_client.time.sleep")
@patch("zero_touch_qa.dify_client.requests.request")
def test_call_timeout(mock_request, mock_sleep, client):
    """API 타임아웃 시 재시도 후에도 실패하면 DifyConnectionError 가 발생한다."""
    mock_request.side_effect = requests.Timeout("Connection timed out")

    with pytest.raises(DifyConnectionError):
        client.generate_scenario("chat", "테스트", "https://x.com")
