import json
import os
import re
import logging

log = logging.getLogger(__name__)


def convert_playwright_to_dsl(file_path: str, output_dir: str) -> list[dict]:
    """
    Playwright codegen이 생성한 Python 스크립트를 파싱하여
    9대 DSL scenario.json으로 변환한다.

    사용법:
      playwright codegen https://target-app.com --output recorded.py
      python3 -m zero_touch_qa --mode convert --file recorded.py
    """
    if not file_path or not os.path.exists(file_path):
        raise FileNotFoundError(f"파일을 찾을 수 없습니다: {file_path}")

    with open(file_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    scenario = []
    step_num = 0

    skip_keywords = (
        "import ", "from ", "def ", "with ", "browser", "context",
        "try:", "finally:", "if __name__", "print(", "# ---",
        '"""', "'''",
    )

    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if any(line.startswith(k) for k in skip_keywords):
            continue
        if not (line.startswith("page.") or line.startswith("expect(")):
            continue

        step = _parse_playwright_line(line)
        if step:
            step_num += 1
            step["step"] = step_num
            step.setdefault("fallback_targets", [])
            scenario.append(step)

    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, "scenario.json")
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(scenario, f, indent=2, ensure_ascii=False)

    log.info("[Convert] %s -> %s (%d스텝 변환)", file_path, output_path, step_num)
    return scenario


def _parse_playwright_line(line: str) -> dict | None:
    """단일 Playwright 코드 라인을 DSL 스텝으로 변환한다."""

    # navigate
    m = re.search(r'page\.goto\(["\'](.+?)["\']\)', line)
    if m:
        return {
            "action": "navigate", "target": "", "value": m.group(1),
            "description": f"{m.group(1)}로 이동",
        }

    # wait
    m = re.search(r'page\.wait_for_timeout\((\d+)\)', line)
    if m:
        return {
            "action": "wait", "target": "", "value": m.group(1),
            "description": f"{m.group(1)}ms 대기",
        }

    # wait_for_load_state / wait_for_url 은 navigate에 부수적
    if "wait_for_load_state" in line or "wait_for_url" in line:
        return None

    target = _extract_target(line)

    # fill
    m = re.search(r'\.fill\(["\'](.+?)["\']\)', line)
    if m:
        return {
            "action": "fill", "target": target, "value": m.group(1),
            "description": f"'{m.group(1)}' 입력",
        }

    # press
    m = re.search(r'\.press\(["\'](.+?)["\']\)', line)
    if m:
        return {
            "action": "press", "target": target, "value": m.group(1),
            "description": f"{m.group(1)} 키 입력",
        }

    # select_option
    m = re.search(r'\.select_option\((?:label=)?["\'](.+?)["\']\)', line)
    if m:
        return {
            "action": "select", "target": target, "value": m.group(1),
            "description": f"'{m.group(1)}' 선택",
        }

    # check / uncheck
    if ".uncheck()" in line:
        return {
            "action": "check", "target": target, "value": "off",
            "description": "체크 해제",
        }
    if ".check()" in line:
        return {
            "action": "check", "target": target, "value": "on",
            "description": "체크",
        }

    # hover
    if ".hover()" in line:
        return {
            "action": "hover", "target": target, "value": "",
            "description": "마우스 호버",
        }

    # click (다른 액션 매칭 후 최후에 체크)
    if ".click(" in line or line.endswith(".click()"):
        return {
            "action": "click", "target": target, "value": "",
            "description": "클릭",
        }

    # expect → verify
    m = re.search(r'expect\((.+?)\)\.to_have_text\(["\'](.+?)["\']\)', line)
    if m:
        verify_target = _extract_target(m.group(1))
        return {
            "action": "verify", "target": verify_target, "value": m.group(2),
            "description": f"텍스트 '{m.group(2)}' 확인",
        }

    if re.search(r'expect\((.+?)\)\.to_be_visible', line):
        m2 = re.search(r'expect\((.+?)\)', line)
        verify_target = _extract_target(m2.group(1)) if m2 else ""
        return {
            "action": "verify", "target": verify_target, "value": "",
            "description": "요소 표시 확인",
        }

    return None


def _extract_target(line: str) -> str:
    """Playwright 로케이터 코드에서 DSL target 문자열을 추출한다."""

    # get_by_role("button", name="로그인")
    m = re.search(r'get_by_role\(["\'](.+?)["\'],\s*name=["\'](.+?)["\']\)', line)
    if m:
        return f"role={m.group(1)}, name={m.group(2)}"

    # get_by_role("heading") (name 없음)
    m = re.search(r'get_by_role\(["\'](.+?)["\']\)', line)
    if m and "name=" not in line.split("get_by_role")[1].split(")")[0]:
        return f"role={m.group(1)}"

    # get_by_label
    m = re.search(r'get_by_label\(["\'](.+?)["\']\)', line)
    if m:
        return f"label={m.group(1)}"

    # get_by_text
    m = re.search(r'get_by_text\(["\'](.+?)["\']\)', line)
    if m:
        return f"text={m.group(1)}"

    # get_by_placeholder
    m = re.search(r'get_by_placeholder\(["\'](.+?)["\']\)', line)
    if m:
        return f"placeholder={m.group(1)}"

    # get_by_test_id
    m = re.search(r'get_by_test_id\(["\'](.+?)["\']\)', line)
    if m:
        return f"testid={m.group(1)}"

    # page.locator("css-selector")
    m = re.search(r'page\.locator\(["\'](.+?)["\']\)', line)
    if m:
        return m.group(1)

    return ""
