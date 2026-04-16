import json
import os
import re
import time
import logging

from .executor import StepResult

log = logging.getLogger(__name__)


def generate_regression_test(
    scenario: list[dict],
    results: list[StepResult],
    output_dir: str,
) -> str | None:
    """모든 스텝이 성공(PASS/HEALED)한 경우, LLM 없이 독립 실행 가능한 Playwright 스크립트를 생성한다.

    Args:
        scenario: DSL 스텝 dict 리스트.
        results: 각 스텝의 실행 결과 StepResult 리스트.
        output_dir: ``regression_test.py`` 를 저장할 디렉터리.

    Returns:
        생성된 스크립트 파일 경로. FAIL 스텝이 있으면 ``None``.
    """
    if any(r.status == "FAIL" for r in results):
        log.info("[Regression] 실패 스텝 존재 — 생성 건너뜀")
        return None

    lines = [
        '"""',
        "Auto-generated regression test from Zero-Touch QA scenario.",
        "LLM 없이 독립 실행 가능한 Playwright 스크립트.",
        f"Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        '"""',
        "from playwright.sync_api import sync_playwright",
        "",
        "",
        "def test_regression():",
        '    with sync_playwright() as p:',
        "        browser = p.chromium.launch(headless=True)",
        '        page = browser.new_page(viewport={"width": 1440, "height": 900})',
        "        try:",
    ]

    for step in scenario:
        action = step["action"].lower()
        target = step.get("target", "")
        value = step.get("value", "")
        desc = step.get("description", "")

        if desc:
            lines.append(f"            # {desc}")

        locator_code = _target_to_playwright_code(target)

        if action in ("navigate", "maps"):
            url = value or str(target)
            lines.append(f"            page.goto({json.dumps(url)})")
            lines.append('            page.wait_for_load_state("domcontentloaded")')
        elif action == "wait":
            ms = int(value or 1000)
            lines.append(f"            page.wait_for_timeout({ms})")
        elif action == "click":
            lines.append(f"            {locator_code}.click(timeout=5000)")
        elif action == "fill":
            lines.append(
                f"            {locator_code}.fill({json.dumps(str(value))})"
            )
        elif action == "press":
            lines.append(
                f"            {locator_code}.press({json.dumps(str(value))})"
            )
        elif action == "select":
            lines.append(
                f"            {locator_code}.select_option("
                f"label={json.dumps(str(value))})"
            )
        elif action == "check":
            if str(value).lower() == "off":
                lines.append(f"            {locator_code}.uncheck()")
            else:
                lines.append(f"            {locator_code}.check()")
        elif action == "hover":
            lines.append(f"            {locator_code}.hover()")
        elif action == "verify":
            if not value:
                lines.append(f"            assert {locator_code}.is_visible()")
            else:
                lines.append(f"            _el = {locator_code}")
                lines.append(
                    "            _text = _el.inner_text() or _el.input_value()"
                )
                lines.append(
                    f"            assert {json.dumps(str(value))} in _text"
                )
        lines.append("")

    lines.extend([
        "        finally:",
        "            browser.close()",
        "",
        "",
        'if __name__ == "__main__":',
        "    test_regression()",
        '    print("Regression test passed.")',
        "",
    ])

    output_path = os.path.join(output_dir, "regression_test.py")
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    log.info("[Regression] 독립 테스트 생성 완료: %s", output_path)
    return output_path


def _target_to_playwright_code(target) -> str:
    """DSL target 을 독립 실행 가능한 Playwright 코드 스니펫으로 변환한다.

    Args:
        target: DSL target 문자열, dict, 또는 빈 값.

    Returns:
        ``page.get_by_role(...)`` 등의 Playwright 코드 문자열.
        빈 target 이면 ``'page.locator("body")'``.

    Example:
        >>> _target_to_playwright_code("role=button, name=확인")
        'page.get_by_role("button", name="확인").first'
    """
    if not target:
        return 'page.locator("body")'

    if isinstance(target, dict):
        if target.get("role"):
            role = json.dumps(target["role"])
            name = json.dumps(target.get("name", ""))
            return f"page.get_by_role({role}, name={name}).first"
        if target.get("label"):
            return f"page.get_by_label({json.dumps(target['label'])}).first"
        if target.get("text"):
            return f"page.get_by_text({json.dumps(target['text'])}).first"
        if target.get("placeholder"):
            return f"page.get_by_placeholder({json.dumps(target['placeholder'])}).first"
        if target.get("testid"):
            return f"page.get_by_test_id({json.dumps(target['testid'])}).first"
        target = target.get("selector", str(target))

    t = str(target).strip()

    # role=button, name=로그인
    m = re.match(r"role=(.+?),\s*name=(.+)", t)
    if m:
        role = json.dumps(m.group(1).strip())
        name = json.dumps(m.group(2).strip())
        return f"page.get_by_role({role}, name={name}).first"

    prefix_map = {
        "text=": "page.get_by_text",
        "label=": "page.get_by_label",
        "placeholder=": "page.get_by_placeholder",
        "testid=": "page.get_by_test_id",
    }
    for prefix, method in prefix_map.items():
        if t.startswith(prefix):
            val = json.dumps(t.replace(prefix, "", 1).strip())
            return f"{method}({val}).first"

    return f"page.locator({json.dumps(t)}).first"
