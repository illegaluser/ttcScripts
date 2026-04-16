import io
import json
import re
import base64


def extract_json_safely(text: str):
    """
    LLM 응답에서 마크다운 코드펜스, C-style 주석을 제거한 후
    순수 JSON 배열 또는 객체만 추출하여 파싱한다.
    LLM 이 trailing comma 등 비표준 JSON 을 생성하는 경우도 복구 시도한다.
    """
    # 마크다운 코드펜스 제거
    text = re.sub(r"```(?:json)?\s*", "", text)
    # C-style 주석 제거
    text = re.sub(r"//.*?\n|/\*.*?\*/", "", text, flags=re.S)

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
    """이미지를 리사이즈 후 JPEG 압축하여 base64 문자열로 반환한다."""
    from PIL import Image

    with Image.open(file_path) as img:
        img = img.convert("RGB")
        img.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)
        buffer = io.BytesIO()
        img.save(buffer, format="JPEG", quality=quality)
        return base64.b64encode(buffer.getvalue()).decode("utf-8")
