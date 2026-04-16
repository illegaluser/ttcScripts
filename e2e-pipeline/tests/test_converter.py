"""converter.py 유닛테스트.

Playwright codegen 출력을 DSL 시나리오로 변환하는 로직을 검증한다.
"""

import json

import pytest

from zero_touch_qa.converter import (
    convert_playwright_to_dsl,
    _parse_playwright_line,
    _extract_target,
)


# ---------------------------------------------------------------------------
# convert_playwright_to_dsl
# ---------------------------------------------------------------------------

def test_convert_full_script(tmp_path):
    """Playwright 스크립트를 변환하면 step 번호가 순차적인 리스트를 반환한다."""
    script = tmp_path / "recorded.py"
    script.write_text(
        'page.goto("https://example.com")\n'
        'page.get_by_role("button", name="로그인").click()\n'
        'page.get_by_label("이메일").fill("test@test.com")\n',
        encoding="utf-8",
    )
    result = convert_playwright_to_dsl(str(script), str(tmp_path / "out"))

    assert isinstance(result, list)
    assert len(result) == 3
    assert [s["step"] for s in result] == [1, 2, 3]


def test_convert_file_not_found(tmp_path):
    """존재하지 않는 파일 경로에서 FileNotFoundError 가 발생한다."""
    with pytest.raises(FileNotFoundError):
        convert_playwright_to_dsl(str(tmp_path / "no.py"), str(tmp_path / "out"))


def test_convert_empty_file(tmp_path):
    """빈 파일을 변환하면 빈 리스트를 반환한다."""
    script = tmp_path / "empty.py"
    script.write_text("", encoding="utf-8")
    assert convert_playwright_to_dsl(str(script), str(tmp_path / "out")) == []


def test_convert_skips_imports(tmp_path):
    """import, from, def, with 등의 비실행 라인은 무시된다."""
    script = tmp_path / "imports.py"
    script.write_text(
        "import os\nfrom playwright.sync_api import sync_playwright\n"
        "def run():\n    with sync_playwright() as p:\n"
        "        browser = p.chromium.launch()\n",
        encoding="utf-8",
    )
    assert convert_playwright_to_dsl(str(script), str(tmp_path / "out")) == []


def test_convert_creates_output_file(tmp_path):
    """변환 후 output_dir 에 scenario.json 파일이 생성된다."""
    script = tmp_path / "rec.py"
    script.write_text('page.goto("https://example.com")\n', encoding="utf-8")
    out_dir = tmp_path / "artifacts"
    convert_playwright_to_dsl(str(script), str(out_dir))

    output_file = out_dir / "scenario.json"
    assert output_file.exists()
    data = json.loads(output_file.read_text(encoding="utf-8"))
    assert len(data) == 1


# ---------------------------------------------------------------------------
# _parse_playwright_line
# ---------------------------------------------------------------------------

def test_parse_goto():
    """page.goto 라인이 navigate 스텝으로 변환된다."""
    result = _parse_playwright_line('page.goto("https://example.com")')
    assert result["action"] == "navigate"
    assert result["value"] == "https://example.com"


def test_parse_click():
    """click 라인이 click 스텝으로 변환된다."""
    result = _parse_playwright_line(
        'page.get_by_role("button", name="로그인").click()'
    )
    assert result["action"] == "click"


def test_parse_fill():
    """fill 라인이 fill 스텝으로 변환된다."""
    result = _parse_playwright_line(
        'page.get_by_label("이메일").fill("test@test.com")'
    )
    assert result["action"] == "fill"
    assert result["value"] == "test@test.com"


def test_parse_press():
    """press 라인이 press 스텝으로 변환된다."""
    result = _parse_playwright_line('page.get_by_label("검색").press("Enter")')
    assert result["action"] == "press"
    assert result["value"] == "Enter"


def test_parse_select_option():
    """select_option 라인이 select 스텝으로 변환된다."""
    result = _parse_playwright_line('page.locator("select").select_option("옵션1")')
    assert result["action"] == "select"
    assert result["value"] == "옵션1"


def test_parse_check():
    """check 라인이 check 스텝(value='on')으로 변환된다."""
    result = _parse_playwright_line('page.get_by_label("약관 동의").check()')
    assert result["action"] == "check"
    assert result["value"] == "on"


def test_parse_uncheck():
    """uncheck 라인이 check 스텝(value='off')으로 변환된다."""
    result = _parse_playwright_line('page.get_by_label("광고 수신").uncheck()')
    assert result["action"] == "check"
    assert result["value"] == "off"


def test_parse_hover():
    """hover 라인이 hover 스텝으로 변환된다."""
    result = _parse_playwright_line(
        'page.get_by_role("link", name="메뉴").hover()'
    )
    assert result["action"] == "hover"


def test_parse_unknown_returns_none():
    """인식할 수 없는 라인은 None 을 반환한다."""
    assert _parse_playwright_line('print("hello")') is None


# ---------------------------------------------------------------------------
# _extract_target
# ---------------------------------------------------------------------------

def test_extract_role_with_name():
    """get_by_role 에서 role 과 name 을 파싱한다."""
    result = _extract_target('page.get_by_role("button", name="로그인").click()')
    assert result == "role=button, name=로그인"


def test_extract_get_by_label():
    """get_by_label 에서 label 을 파싱한다."""
    result = _extract_target('page.get_by_label("이메일").fill("test")')
    assert result == "label=이메일"


def test_extract_get_by_text():
    """get_by_text 에서 text 를 파싱한다."""
    result = _extract_target('page.get_by_text("환영합니다").click()')
    assert result == "text=환영합니다"


def test_extract_locator_css():
    """page.locator CSS 셀렉터를 파싱한다."""
    result = _extract_target('page.locator("#main > .btn").click()')
    assert result == "#main > .btn"
