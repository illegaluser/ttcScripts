import io
import json
import re
import base64


def extract_json_safely(text: str):
    """LLM 응답 텍스트에서 JSON 배열 또는 객체를 안전하게 추출한다.

    복구 전략 (순차 시도):
      1) <think>/마크다운 제거 후 regex 매칭 + json.loads
      2) trailing comma 제거
      3) 작은따옴표 → 큰따옴표 변환
      4) JSONDecoder.raw_decode 로 개별 ``{...}`` object 선형 스캔
      5) markdown 분석 노트 파싱 — 작은 LLM (gemma4:e4b 등) 이 JSON 대신
         ``**Step 01. ...** / Action: / Target: / Value: / Description:`` 형식으로
         답할 때의 실측 대응. **원본에서** 직접 추출 (``<think>`` 가 닫히지
         않고 그 안에 step 분석이 포함된 케이스까지 커버).
    """
    original = text
    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    cleaned = re.sub(r"<think>.*", "", cleaned, flags=re.S)
    cleaned = re.sub(r"```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"//.*?\n|/\*.*?\*/", "", cleaned, flags=re.S)

    match = re.search(r"\[\s*\{.*\}\s*\]|\{\s*\".*\}\s*", cleaned, re.DOTALL)
    if match:
        raw = match.group(0)
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass
        cleaned_raw = re.sub(r",\s*([}\]])", r"\1", raw)
        try:
            return json.loads(cleaned_raw)
        except json.JSONDecodeError:
            pass
        try:
            return json.loads(cleaned_raw.replace("'", '"'))
        except json.JSONDecodeError:
            pass

    decoder = json.JSONDecoder()
    objs = []
    i = 0
    while i < len(cleaned):
        brace_pos = cleaned.find("{", i)
        if brace_pos == -1:
            break
        try:
            obj, end = decoder.raw_decode(cleaned[brace_pos:])
        except json.JSONDecodeError:
            i = brace_pos + 1
            continue
        if isinstance(obj, dict) and (
            "action" in obj or "op" in obj or "step" in obj
        ):
            objs.append(obj)
        i = brace_pos + end
    if objs:
        return objs

    return _parse_markdown_steps(original)


def _parse_markdown_steps(text: str):
    """Markdown 스타일 step 분석 노트를 DSL step dict 리스트로 변환. 없으면 None."""
    step_pattern = re.compile(
        r"(?:\*\*)?Step\s*(\d{1,3})[\.\s].*?(?=(?:\*\*)?Step\s*\d{1,3}[\.\s]|\Z)",
        re.S | re.I,
    )
    field_pattern = re.compile(
        r"(?:\*\s*|-\s*)?(Action|Target|Value|Description)\s*:\s*"
        r'(?:"([^"\n]*)"|([^\n]*))',
        re.I,
    )
    steps = []
    for m in step_pattern.finditer(text):
        block = m.group(0)
        step_num = int(m.group(1))
        fields = {}
        for f in field_pattern.finditer(block):
            key = f.group(1).lower()
            val = (f.group(2) or f.group(3) or "").strip().rstrip(".")
            if key not in fields:
                fields[key] = val
        if "action" in fields and fields["action"]:
            steps.append(
                {
                    "step": step_num,
                    "action": fields["action"].lower(),
                    "target": fields.get("target", ""),
                    "value": fields.get("value", ""),
                    "description": fields.get("description", ""),
                    "fallback_targets": [],
                }
            )
    return steps if steps else None


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
