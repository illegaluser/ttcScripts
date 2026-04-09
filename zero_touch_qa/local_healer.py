import re
import difflib
import logging

from playwright.sync_api import Page, Locator

log = logging.getLogger(__name__)


class LocalHealer:
    """
    LLM 호출 없이(비용 0) 현재 페이지의 DOM을 스캔하여
    실패한 타겟과 가장 유사한 요소를 찾아 반환한다.

    액션별 검색 대상:
      - fill/press:     input, textarea, [role='textbox'], [role='searchbox'], [contenteditable]
      - select:         select, [role='listbox'], [role='combobox'], option, [role='option']
      - hover:          button, a, [role='menuitem'], [role='tab'], nav a, [aria-haspopup]
      - click/check 등: button, a, [role='button'], [role='link'], [role='menuitem'], [role='tab']
    """

    SELECTOR_MAP = {
        "fill": (
            "input, textarea, [role='textbox'], [role='searchbox'], "
            "[contenteditable='true']"
        ),
        "press": (
            "input, textarea, [role='textbox'], [role='searchbox'], "
            "[contenteditable='true']"
        ),
        "select": (
            "select, [role='listbox'], [role='combobox'], "
            "option, [role='option']"
        ),
        "hover": (
            "button, a, [role='button'], [role='link'], "
            "[role='menuitem'], [role='tab'], [role='menu'], "
            "nav a, [aria-haspopup], [role='tooltip']"
        ),
    }

    DEFAULT_SELECTOR = (
        "button, a, [role='button'], [role='link'], "
        "[role='menuitem'], [role='tab']"
    )

    def __init__(self, page: Page, threshold: float = 0.8):
        self.page = page
        self.threshold = threshold

    def try_heal(self, step: dict) -> Locator | None:
        """step의 target과 유사한 요소를 DOM에서 검색한다."""
        action = step["action"].lower()
        target = step.get("target", "")

        selector = self.SELECTOR_MAP.get(action, self.DEFAULT_SELECTOR)
        clean_target = self._clean_target(target)
        if len(clean_target) <= 1:
            return None

        best_match = None
        highest_ratio = 0.0

        for el in self.page.locator(selector).all():
            text = self._extract_text(el)
            if not text:
                continue
            ratio = difflib.SequenceMatcher(None, clean_target, text).ratio()
            if ratio > self.threshold and ratio > highest_ratio:
                highest_ratio = ratio
                best_match = el

        if best_match:
            log.info("  [로컬복구 성공] 유사도 %.0f%% 매칭", highest_ratio * 100)
        return best_match

    @staticmethod
    def _clean_target(target) -> str:
        """시맨틱 접두사를 제거하여 순수 텍스트를 추출한다."""
        s = re.sub(r"^(text|role|label|placeholder|testid)=", "", str(target))
        s = re.sub(r"role=.+?,\s*name=", "", s)
        return s.strip()

    @staticmethod
    def _extract_text(el) -> str:
        try:
            return (
                el.inner_text()
                or el.get_attribute("placeholder")
                or el.get_attribute("value")
                or el.get_attribute("aria-label")
                or ""
            ).strip()
        except Exception:
            return ""
