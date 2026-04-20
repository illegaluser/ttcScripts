import io
import json
import re
import base64


def extract_json_safely(text: str):
    """LLM 응답 텍스트에서 JSON 배열 또는 객체를 안전하게 추출한다.

    복구 전략 (순차 시도):
      1) <think>/마크다운 제거 후 regex 매칭 + json.loads
      2) trailing comma 제거 후 재시도
      3) 작은따옴표 → 큰따옴표 변환
      4) JSONDecoder.raw_decode 로 개별 ``{...}`` object 선형 스캔
         (배열 구조가 망가져도 step dict 는 유효한 경우)
      5) markdown 분석 노트 파싱 — 작은 LLM (gemma4:e4b 등) 이 JSON 대신
         ``**Step 01. ...** / Action: / Target: / Value: / Description:`` 형식으로
         답할 때의 실측 대응. **원본에서** 직접 추출한다 (LLM 이 ``<think>``
         블록을 닫지 않고 그 안에 이미 step 분석을 써 두는 경우, ``<think>``
         제거로 응답 전체가 사라지면 1-4차는 빈 입력으로 실패하지만 5차가
         원본에서 구조를 건짐).

    Args:
        text: LLM 이 반환한 원시 응답 문자열.

    Returns:
        파싱된 JSON 객체(dict 또는 list). 추출 실패 시 ``None``.
    """
    original = text  # 5차 markdown 파서는 원본을 본다

    # 1-4차용 cleanup
    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    cleaned = re.sub(r"<think>.*", "", cleaned, flags=re.S)
    cleaned = re.sub(r"```(?:json)?\s*", "", cleaned)

    match = re.search(r"\[\s*\{.*\}\s*\]|\{\s*\".*\}\s*", cleaned, re.DOTALL)
    if match:
        raw = match.group(0)

        # 1차: 그대로 파싱
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass

        # 2차: trailing comma 제거
        cleaned_raw = re.sub(r",\s*([}\]])", r"\1", raw)
        try:
            return json.loads(cleaned_raw)
        except json.JSONDecodeError:
            pass

        # 3차: 작은따옴표 → 큰따옴표
        try:
            return json.loads(cleaned_raw.replace("'", '"'))
        except json.JSONDecodeError:
            pass

    # 4차: 개별 object 선형 스캔 (배열 구조가 깨져도 개별 dict 는 유효할 수 있음)
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

    # 5차: markdown 분석 노트 파싱 — **원본** (think 제거 안 한 것) 에서
    return _parse_markdown_steps(original)


def _parse_markdown_steps(text: str):
    """Markdown 스타일 "Step N" + "Action:/Target:/Value:/Description:" 패턴을
    JSON 배열 형태의 step dict 리스트로 변환한다. 매칭되는 step 이 없으면 None.
    """
    # 각 "Step N" 블록을 분리. "**Step 01.", "Step 02.", "1." 등 다양한 패턴 허용.
    step_pattern = re.compile(
        r"Step\s*(\d{1,3})[\.\s].*?(?=Step\s*\d{1,3}[\.\s]|\Z)",
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
