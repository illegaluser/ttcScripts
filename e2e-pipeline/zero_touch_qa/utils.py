import io
import json
import re
import base64


def _repair_json(text: str) -> str:
    """
    LLM이 자주 만드는 JSON 오류를 휴리스틱으로 수정한다.
    - 후행 쉼표 제거  ({"a":1,} / [1,2,])
    - 작은따옴표 → 큰따옴표 (키/값 모두)
    - 줄바꿈이 포함된 문자열 내 리터럴 개행을 \\n 으로 교체
    - 제어문자 제거
    """
    # 후행 쉼표 제거
    text = re.sub(r",\s*([}\]])", r"\1", text)
    # 작은따옴표 키/값 → 큰따옴표 (간단한 경우만)
    text = re.sub(r"(?<![\\])'", '"', text)
    # 리터럴 탭을 JSON 이스케이프로
    text = text.replace("\t", "\\t")
    return text


def extract_json_safely(text: str):
    """
    LLM 응답에서 JSON 배열 또는 객체를 추출·파싱한다.

    처리 순서:
    0. <think>...</think> 블록 제거 (reasoning 모델 체인오브소트)
       - 닫힘 태그 없는 경우: </think> 이후 또는 <think> 이전 텍스트를 우선 시도
    1. 마크다운 코드펜스(```json ... ```) 제거
    2. C-style 주석 제거
    3. 균형 탐색으로 모든 JSON 후보를 수집 후 파싱 — 마지막 후보 우선
       (reasoning 텍스트 내 [비-JSON] 브라켓보다 마지막 위치의 JSON이 정답일 확률 높음)
    4. 파싱 실패 시 휴리스틱 복구 후 재시도
    """
    # ── Step 0: <think> 블록 제거 ──────────────────────────────────────────
    # [변경] 원본에는 없음. 추가 사유:
    # qwen3:8b 같은 추론(reasoning) 모델은 실제 답변 전에 <think>...</think> 형태의
    # 내부 사고 과정(Chain-of-Thought)을 출력한다.
    # 해당 블록 안에는 JSON 처럼 보이는 괄호·따옴표가 다수 포함되어 있어,
    # 이후 균형 탐색이 사고 블록의 단편을 '시나리오 JSON'으로 오인한다.
    # 따라서 JSON 추출 전 반드시 제거해야 한다.

    # 0-a. 닫힌 <think>...</think> 블록 제거
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)

    # 0-b. 여전히 <think>가 있으면 (닫힘 태그 없음):
    #      </think> 이후 텍스트를 우선 사용, 없으면 <think> 이전 텍스트 사용
    if "<think>" in text:
        if "</think>" in text:
            text = text.split("</think>", 1)[-1]
        else:
            before = text.split("<think>", 1)[0].strip()
            after  = text.split("<think>", 1)[1].strip()
            # JSON이 </think> 없이 <think> 블록 안에 있는 경우를 위해 after도 보관
            text = after if after else before

    # ── Step 1: 마크다운 코드펜스 제거 ────────────────────────────────────
    # LLM 이 ```json ... ``` 형태로 응답을 감싸는 경우 JSON 파싱 전 제거
    text = re.sub(r"```[a-zA-Z]*\n?", "", text)
    text = text.replace("```", "")

    # ── Step 2: C-style 주석 제거 ─────────────────────────────────────────
    # [변경] 원본: re.sub(r"//[^\n]*\n|/\*.*?\*/", ...) → URL 의 // 도 제거하는 버그
    # 수정: (?m)^\s*// 로 줄 시작에 있는 // 만 제거하여 https:// 등의 URL 을 보호한다.
    # 실제 사례: target_url = "https://www.google.com" 이 "/www.google.com" 으로
    #            잘리면서 navigate 스텝이 FAIL 되는 문제가 발생했다.
    text = re.sub(r"(?m)^\s*//[^\n]*\n", "", text)
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)

    # ── Step 3: 균형 탐색으로 모든 JSON 후보 수집 ─────────────────────────
    # [변경] 원본: re.search(r"\[.*\]|\{.*\}", text, re.S) 탐욕(greedy) 매칭 1회
    #        → <think> 블록 내부의 [ 부터 시나리오 끝 ] 까지 통째로 잡아 파싱 실패
    # 수정: 모든 '[' / '{' 로 시작하는 균형 잡힌 서브스트링을 수집하고,
    #       길이 내림차순으로 정렬하여 가장 긴(= 가장 많은 스텝을 담은) 후보부터 시도한다.
    def _all_balanced(s: str, open_ch: str, close_ch: str) -> list[str]:
        """open_ch 로 시작하는 모든 균형 잡힌 서브스트링을 반환한다."""
        results = []
        idx = 0
        while True:
            start = s.find(open_ch, idx)
            if start == -1:
                break
            depth, in_str, escape = 0, False, False
            end = None
            for i, ch in enumerate(s[start:], start):
                if escape:
                    escape = False
                    continue
                if ch == "\\" and in_str:
                    escape = True
                    continue
                if ch == '"' and not escape:
                    in_str = not in_str
                    continue
                if in_str:
                    continue
                if ch == open_ch:
                    depth += 1
                elif ch == close_ch:
                    depth -= 1
                    if depth == 0:
                        end = i
                        break
            if end is not None:
                results.append(s[start : end + 1])
            idx = start + 1
        return results

    # 배열 우선, 없으면 객체 — 가장 긴 후보(복잡한 JSON일수록 길다)부터 시도
    candidates = _all_balanced(text, "[", "]") or _all_balanced(text, "{", "}")
    if not candidates:
        return None

    # 길이 내림차순 정렬 — 긴 후보가 실제 시나리오일 확률이 높음
    for candidate in sorted(candidates, key=len, reverse=True):
        for blob in (candidate, _repair_json(candidate)):
            try:
                return json.loads(blob)
            except json.JSONDecodeError:
                continue

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
