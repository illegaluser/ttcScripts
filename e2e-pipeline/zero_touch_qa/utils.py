import io
import json
import re
import base64


def extract_json_safely(text: str):
    """
    LLM 응답에서 마크다운 코드펜스, C-style 주석을 제거한 후
    순수 JSON 배열 또는 객체만 추출하여 파싱한다.
    """
    text = re.sub(r"//.*?\n|/\*.*?\*/", "", text, flags=re.S)
    match = re.search(r"\[\s*\{.*\}\s*\]|\{\s*\".*\}\s*", text, re.DOTALL)
    if not match:
        return None
    return json.loads(match.group(0))


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
