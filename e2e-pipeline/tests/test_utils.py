"""utils.py 유닛테스트.

extract_json_safely 의 다양한 LLM 응답 패턴 처리와
compress_image_to_b64 의 이미지 변환을 검증한다.
"""

import base64
import json

import pytest

from zero_touch_qa.utils import extract_json_safely, compress_image_to_b64


# ---------------------------------------------------------------------------
# extract_json_safely
# ---------------------------------------------------------------------------

class TestExtractJsonSafely:
    """LLM 응답에서 JSON 을 안전하게 추출하는 함수 테스트."""

    def test_extract_valid_json_array(self):
        """정상 JSON 배열을 올바르게 파싱한다."""
        text = '[{"step": 1, "action": "click"}]'
        result = extract_json_safely(text)
        assert isinstance(result, list)
        assert result[0]["step"] == 1

    def test_extract_valid_json_object(self):
        """정상 JSON 객체를 올바르게 파싱한다."""
        text = '{"target": "text=로그인"}'
        result = extract_json_safely(text)
        assert isinstance(result, dict)
        assert result["target"] == "text=로그인"

    def test_extract_with_markdown_fence(self):
        """마크다운 코드펜스로 감싼 JSON 을 추출한다."""
        text = '```json\n[{"step": 1}]\n```'
        result = extract_json_safely(text)
        assert isinstance(result, list)
        assert result[0]["step"] == 1

    def test_extract_with_think_block(self):
        """<think>...</think> 블록을 제거하고 JSON 을 추출한다."""
        text = '<think>모델이 생각하는 내용...</think>[{"step": 1}]'
        result = extract_json_safely(text)
        assert isinstance(result, list)
        assert result[0]["step"] == 1

    def test_extract_with_unclosed_think(self):
        """닫힘 태그 없는 <think> 는 이후 내용 전체를 제거한다."""
        text = '<think>토큰 한계로 잘린 사고 과정...'
        result = extract_json_safely(text)
        assert result is None

    def test_extract_trailing_comma(self):
        """trailing comma 가 있는 비표준 JSON 을 복구하여 파싱한다."""
        text = '[{"step": 1, "action": "click",}]'
        result = extract_json_safely(text)
        assert isinstance(result, list)
        assert result[0]["action"] == "click"

    def test_extract_single_quotes(self):
        """작은따옴표로 감싼 JSON 을 큰따옴표로 변환하여 파싱한다."""
        text = "[{'step': 1}]"
        result = extract_json_safely(text)
        assert isinstance(result, list)
        assert result[0]["step"] == 1

    def test_extract_no_json_returns_none(self):
        """JSON 이 없는 일반 텍스트에서 None 을 반환한다."""
        assert extract_json_safely("hello world") is None

    def test_extract_empty_string(self):
        """빈 문자열에서 None 을 반환한다."""
        assert extract_json_safely("") is None

    def test_extract_url_not_corrupted(self):
        """URL 의 // 가 C-style 주석으로 오인되지 않는다 (회귀 방지).

        이전 버그: ``//.*?\\n`` 정규식이 https:// 이후를 삭제했음.
        """
        text = '[{"value": "https://www.google.com", "step": 1}]'
        result = extract_json_safely(text)
        assert isinstance(result, list)
        assert result[0]["value"] == "https://www.google.com"

    def test_extract_think_block_with_json_after(self):
        """<think> 블록 뒤의 JSON 배열이 올바르게 추출된다."""
        text = (
            '<think>\nLet me analyze the requirements.\n'
            'I need to create steps.\n</think>\n'
            '[{"step": 1, "action": "navigate", "value": "https://example.com"}]'
        )
        result = extract_json_safely(text)
        assert isinstance(result, list)
        assert result[0]["action"] == "navigate"
        assert result[0]["value"] == "https://example.com"


# ---------------------------------------------------------------------------
# compress_image_to_b64
# ---------------------------------------------------------------------------

class TestCompressImageToB64:
    """이미지 압축 후 base64 변환 함수 테스트."""

    def test_compress_returns_base64(self, tmp_path):
        """유효한 base64 문자열을 반환한다."""
        PIL = pytest.importorskip("PIL")
        from PIL import Image

        img = Image.new("RGB", (200, 200), color="red")
        img_path = str(tmp_path / "test.png")
        img.save(img_path)

        result = compress_image_to_b64(img_path)

        assert isinstance(result, str)
        decoded = base64.b64decode(result)
        assert len(decoded) > 0

    def test_compress_file_not_found(self):
        """존재하지 않는 파일 경로에서 FileNotFoundError 가 발생한다."""
        with pytest.raises(FileNotFoundError):
            compress_image_to_b64("/nonexistent/path/image.png")
