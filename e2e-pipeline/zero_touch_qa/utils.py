import io
import json
import re
import base64


def extract_json_safely(text: str):
    """LLM 응답 텍스트에서 JSON 배열 또는 객체를 안전하게 추출한다.

    추론 모델(Qwen, DeepSeek 등)의 ``<think>`` 블록, 마크다운 코드펜스를
    제거한 뒤 정규식으로 JSON 구조를 탐색한다. 1차 파싱 실패 시
    trailing comma 제거, 작은따옴표 변환을 순차 시도한다.

    Args:
        text: LLM 이 반환한 원시 응답 문자열. 마크다운, HTML 태그,
              ``<think>`` 블록, 비표준 JSON 등이 혼재할 수 있다.

    Returns:
        파싱된 JSON 객체(dict 또는 list). 추출 실패 시 ``None``.

    Note:
        복구 전략 순서: 1) 원본 파싱 → 2) trailing comma 제거 → 3) 작은따옴표→큰따옴표.

    Example:
        >>> extract_json_safely('<think>reasoning</think>[{"step": 1}]')
        [{'step': 1}]
        >>> extract_json_safely('no json here')
        None
    """
    # 추론 모델(Qwen, DeepSeek 등)의 <think>...</think> 블록 제거
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    # 닫힘 태그 없는 <think> — 토큰 한계로 잘린 경우 나머지 전부 제거
    text = re.sub(r"<think>.*", "", text, flags=re.S)
    # 마크다운 코드펜스 제거
    text = re.sub(r"```(?:json)?\s*", "", text)

    match = re.search(r"\[\s*\{.*\}\s*\]|\{\s*\".*\}\s*", text, re.DOTALL)
    if not match:
        return None

    raw = match.group(0)

    # 1차 시도: 그대로 파싱
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # 2차 시도: trailing comma 제거 후 파싱
    #   }, ] 또는 ", } 같은 패턴 — LLM 이 자주 생성하는 비표준 JSON
    cleaned = re.sub(r",\s*([}\]])", r"\1", raw)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # 3차 시도: 작은따옴표 → 큰따옴표 변환
    cleaned2 = cleaned.replace("'", '"')
    try:
        return json.loads(cleaned2)
    except json.JSONDecodeError:
        return None


def compress_image_to_b64(
    file_path: str, max_size: int = 1024, quality: int = 60
) -> str:
    """이미지를 리사이즈 후 JPEG 압축하여 base64 문자열로 반환한다.

    Args:
        file_path: 원본 이미지 파일의 절대 또는 상대 경로.
        max_size: 가로/세로 중 긴 변의 최대 픽셀 수. 초과 시 비율 유지 축소.
        quality: JPEG 압축 품질 (1~95). 낮을수록 파일 크기 감소.

    Returns:
        압축된 이미지의 base64 인코딩 문자열 (``data:`` 접두사 미포함).

    Raises:
        FileNotFoundError: 파일이 존재하지 않을 때.
    """
    from PIL import Image

    with Image.open(file_path) as img:
        img = img.convert("RGB")
        img.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)
        buffer = io.BytesIO()
        img.save(buffer, format="JPEG", quality=quality)
        return base64.b64encode(buffer.getvalue()).decode("utf-8")
